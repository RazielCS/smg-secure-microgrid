"""
SMG Primary Server — Security-Aware EMS Coordinator (Tertiary Control).

Implements a 3-input Mamdani fuzzy inference system on the Raspberry Pi 4B:

  Inputs:
    v_bus_error  — mean(V_bus across nodes) − V_nominal (12 V)
    p_demand     — sum(P_bus across all active nodes) [W]
    node_trust   — per-node trust score [0.0 – 1.0], maintained by NodeSession

  Output:
    setpoint_W   — demand setpoint for the node [W]

The trust input is the security-aware dimension: a node whose authentication
session has encountered integrity failures or whose sensor readings deviate
from the cross-node median receives a reduced trust score and a proportionally
reduced setpoint. A node whose trust drops below 0.3 (UNTRUSTED) receives
setpoint = 0 W; the NodeSession simultaneously drops the TCP connection,
forcing a full P2/P3 re-authentication to restore service.

Rule table: 27 rules (3 trust × 3 v_bus_error × 3 p_demand).

Note: the security-aware EMS rules are an implementation-specific enhancement
of the case study. They are not a contribution of the methodology itself and
are planned as the primary subject of a future extended article.
"""

import logging
import threading
import time

log = logging.getLogger(__name__)

V_NOMINAL = 12.0      # V — nominal DC bus voltage
_MIN_SP   = 0.0       # W — minimum setpoint (node inactive or isolated)
_MAX_SP   = 100.0     # W — maximum setpoint
_DEFAULT  = 50.0      # W — sent before first aggregate data is available


# ---------------------------------------------------------------------------
# Membership functions
# ---------------------------------------------------------------------------

def _trimf(x: float, a: float, b: float, c: float) -> float:
    """Triangular MF. Peak at b, zero at a and c."""
    if x <= a or x >= c:
        return 0.0
    if x <= b:
        return (x - a) / (b - a)
    return (c - x) / (c - b)


def _trapf(x: float, a: float, b: float, c: float, d: float) -> float:
    """Trapezoidal MF. Flat top [b, c], zero outside [a, d]."""
    if x <= a or x >= d:
        return 0.0
    if b <= x <= c:
        return 1.0
    if x < b:
        return (x - a) / (b - a)
    return (d - x) / (d - c)


# ---------------------------------------------------------------------------
# Input membership functions
# ---------------------------------------------------------------------------

def _vbe_low(x):
    """V_bus error LOW: bus significantly below nominal (< −0.2 V)."""
    return _trapf(x, -3.0, -3.0, -0.8, -0.2)

def _vbe_med(x):
    """V_bus error MED: bus near nominal (±0.8 V)."""
    return _trimf(x, -0.8, 0.0, 0.8)

def _vbe_high(x):
    """V_bus error HIGH: bus significantly above nominal (> +0.2 V)."""
    return _trapf(x, 0.2, 0.8, 3.0, 3.0)


def _pd_low(x):
    """Total P_demand LOW: 0 – 40 W."""
    return _trapf(x, -10.0, 0.0, 20.0, 40.0)

def _pd_med(x):
    """Total P_demand MED: 20 – 80 W."""
    return _trimf(x, 20.0, 50.0, 80.0)

def _pd_high(x):
    """Total P_demand HIGH: > 60 W."""
    return _trapf(x, 60.0, 80.0, 150.0, 150.0)


def _tr_untrusted(x):
    """Trust UNTRUSTED: score < 0.30."""
    return _trapf(x, -0.1, 0.0, 0.20, 0.30)

def _tr_degraded(x):
    """Trust DEGRADED: score 0.30 – 0.70."""
    return _trimf(x, 0.20, 0.50, 0.75)

def _tr_trusted(x):
    """Trust TRUSTED: score > 0.70."""
    return _trapf(x, 0.65, 0.80, 1.0, 1.1)


# ---------------------------------------------------------------------------
# Output membership functions (setpoint per node, 0 – 100 W)
# ---------------------------------------------------------------------------

_OUT_MFS = {
    'ZERO': (-5.0,   0.0,  10.0),
    'LOW':  ( 5.0,  15.0,  30.0),
    'MED':  (25.0,  40.0,  60.0),
    'HIGH': (50.0,  70.0,  90.0),
    'MAX':  (80.0,  95.0, 105.0),
}

def _out_mf(y: float, name: str) -> float:
    return _trimf(y, *_OUT_MFS[name])


# ---------------------------------------------------------------------------
# 27-rule table: (trust_mf, vbe_mf, pd_mf, output_label)
# ---------------------------------------------------------------------------
#
# Logic:
#   UNTRUSTED → always ZERO  (node isolated from EMS)
#   DEGRADED  → conservative setpoints (~50 % of TRUSTED equivalent)
#   TRUSTED   → full energy rules (bus voltage error drives setpoint)
#
# Energy rules (TRUSTED tier):
#   LOW v_bus_error (bus under-voltage) → increase contribution
#   HIGH v_bus_error (bus over-voltage) → reduce contribution
#
_RULES: list[tuple] = [
    # --- UNTRUSTED (rules R01–R09) ---
    (_tr_untrusted, _vbe_low,  _pd_low,  'ZERO'),
    (_tr_untrusted, _vbe_low,  _pd_med,  'ZERO'),
    (_tr_untrusted, _vbe_low,  _pd_high, 'ZERO'),
    (_tr_untrusted, _vbe_med,  _pd_low,  'ZERO'),
    (_tr_untrusted, _vbe_med,  _pd_med,  'ZERO'),
    (_tr_untrusted, _vbe_med,  _pd_high, 'ZERO'),
    (_tr_untrusted, _vbe_high, _pd_low,  'ZERO'),
    (_tr_untrusted, _vbe_high, _pd_med,  'ZERO'),
    (_tr_untrusted, _vbe_high, _pd_high, 'ZERO'),
    # --- DEGRADED (rules R10–R18) ---
    (_tr_degraded,  _vbe_low,  _pd_low,  'LOW'),
    (_tr_degraded,  _vbe_low,  _pd_med,  'MED'),
    (_tr_degraded,  _vbe_low,  _pd_high, 'MED'),
    (_tr_degraded,  _vbe_med,  _pd_low,  'ZERO'),
    (_tr_degraded,  _vbe_med,  _pd_med,  'LOW'),
    (_tr_degraded,  _vbe_med,  _pd_high, 'MED'),
    (_tr_degraded,  _vbe_high, _pd_low,  'ZERO'),
    (_tr_degraded,  _vbe_high, _pd_med,  'ZERO'),
    (_tr_degraded,  _vbe_high, _pd_high, 'LOW'),
    # --- TRUSTED (rules R19–R27) ---
    (_tr_trusted,   _vbe_low,  _pd_low,  'MED'),
    (_tr_trusted,   _vbe_low,  _pd_med,  'HIGH'),
    (_tr_trusted,   _vbe_low,  _pd_high, 'MAX'),
    (_tr_trusted,   _vbe_med,  _pd_low,  'LOW'),
    (_tr_trusted,   _vbe_med,  _pd_med,  'MED'),
    (_tr_trusted,   _vbe_med,  _pd_high, 'HIGH'),
    (_tr_trusted,   _vbe_high, _pd_low,  'ZERO'),
    (_tr_trusted,   _vbe_high, _pd_med,  'LOW'),
    (_tr_trusted,   _vbe_high, _pd_high, 'MED'),
]


def _fuzzy_infer(v_bus_error: float, p_demand: float, trust_score: float) -> float:
    """
    Mamdani fuzzy inference with centroid defuzzification.
    Output universe: [0, 100] W sampled at 0.5 W resolution.
    """
    universe_step = 0.5
    universe = [i * universe_step for i in range(int(_MAX_SP / universe_step) + 1)]
    aggregated = [0.0] * len(universe)

    for tr_fn, vbe_fn, pd_fn, out_label in _RULES:
        strength = min(tr_fn(trust_score), vbe_fn(v_bus_error), pd_fn(p_demand))
        if strength <= 0.0:
            continue
        for i, y in enumerate(universe):
            clipped = min(strength, _out_mf(y, out_label))
            if clipped > aggregated[i]:
                aggregated[i] = clipped

    numerator   = sum(universe[i] * aggregated[i] for i in range(len(universe)))
    denominator = sum(aggregated)
    if denominator == 0.0:
        return 0.0
    return numerator / denominator


# ---------------------------------------------------------------------------
# EMSCoordinator
# ---------------------------------------------------------------------------

class EMSCoordinator:
    """
    Security-aware fuzzy EMS coordinator for the SMG primary server (RPi4).

    On each update cycle (default every 5 s):
      1. Reads the latest sensor data and trust score from DataStore for each node.
      2. Computes v_bus_error (mean V_bus − 12 V) and p_demand (sum P_bus).
      3. Runs the 27-rule fuzzy inference for each node individually, using
         the node's own trust score as the third input.
      4. Stores the resulting setpoint for retrieval after each P4 exchange.
    """

    def __init__(self, config: dict):
        self._lock = threading.Lock()
        self._setpoints: dict[str, float] = {}
        self._update_interval = config.get("ems_update_interval_s", 5.0)
        self._last_update: dict[str, float] = {}

    def update(self, data_store) -> None:
        """Recompute setpoints from latest sensor + trust data."""
        all_latest = data_store.get_all_latest()
        if not all_latest:
            return

        # Global inputs: mean V_bus error and total P_bus
        v_bus_values = [r.get('V_bus', V_NOMINAL) for r in all_latest.values()]
        mean_v_bus   = sum(v_bus_values) / len(v_bus_values)
        v_bus_error  = mean_v_bus - V_NOMINAL
        p_demand     = sum(r.get('P_bus', 0.0) for r in all_latest.values())

        now = time.time()
        with self._lock:
            for node, reading in all_latest.items():
                trust = reading.get('trust_score', 1.0)
                sp = _fuzzy_infer(v_bus_error, p_demand, trust)
                sp = max(_MIN_SP, min(_MAX_SP, round(sp, 1)))
                self._setpoints[node] = sp
                self._last_update[node] = now

        log.debug("EMS update: vbe=%.2f pd=%.1fW | %s",
                  v_bus_error, p_demand,
                  {n: self._setpoints[n] for n in self._setpoints})

    def get_setpoint(self, node_label: str) -> float | None:
        """Return current setpoint for a node, or None before first update."""
        with self._lock:
            if node_label not in self._last_update:
                return None
            return self._setpoints.get(node_label, _DEFAULT)

    def set_manual_setpoint(self, node_label: str, p_demand: float) -> None:
        """Override setpoint for testing."""
        with self._lock:
            p_demand = max(_MIN_SP, min(_MAX_SP, p_demand))
            self._setpoints[node_label] = round(p_demand, 1)
            self._last_update[node_label] = time.time()
        log.info("Manual setpoint: %s = %.1f W", node_label, p_demand)

    def get_all_setpoints(self) -> dict:
        with self._lock:
            return dict(self._setpoints)

    def summary(self) -> dict:
        with self._lock:
            return {'setpoints': dict(self._setpoints)}
