"""
Energy Management System (EMS) module for SMG control node.

Fuzzy logic controller with 9 rules (3x3) and 3 membership functions
per input. Controls 3 DC-DC SSRs via LEDC PWM:
  - GPIO25 (D25) → SSR Bus
  - GPIO26 (D26) → SSR Solar
  - GPIO27 (D27) → SSR Generator
All 3 SSR channels receive the same duty cycle simultaneously.

Inputs:
  - v_bus_error: Deviation of bus voltage from nominal (V_bus - V_nominal)
  - p_demand: Power demand setpoint from server (W)

Output:
  - pwm_duty: LEDC duty cycle (0-1023, 10-bit), applied to all 3 SSRs

Rule table (error rows × demand columns):
              demand=LOW  demand=MED  demand=HIGH
  error=LOW     20%         40%         60%
  error=MED     25%         50%         65%
  error=HIGH    30%         55%         80%

Defuzzification: Centroid (weighted average of active rules)

Note: The companion CCE 2023 paper describes a 3-input controller
(measured power, bus voltage, error). This implementation uses
2 inputs (v_bus_error = V_bus - V_nominal, p_demand) to reduce the
rule table from 27 to 9 while preserving key control dynamics.
The measured power is available as a monitoring variable but is
not used as a fuzzy input in this tertiary-control implementation.
"""

import time
import machine


class TriangularMF:
    """Triangular membership function."""
    __slots__ = ('a', 'b', 'c', 'label')

    def __init__(self, a, b, c, label):
        self.a = float(a)
        self.b = float(b)
        self.c = float(c)
        self.label = label

    def evaluate(self, x):
        if x <= self.a or x >= self.c:
            return 0.0
        if x == self.b:
            return 1.0
        if x < self.b:
            return (x - self.a) / (self.b - self.a)
        return (self.c - x) / (self.c - self.b)

    def __repr__(self):
        return "TriMF({}, {}, {}, '{}')".format(self.a, self.b, self.c, self.label)


class FuzzyEMS:
    """Simplified fuzzy logic EMS for SMG tertiary control."""

    def __init__(self, config):
        ems_cfg = config.get("ems", {})
        thresholds = config.get("thresholds", {})

        self.v_bus_nominal = ems_cfg.get("v_bus_nominal", 12.0)
        self.p_demand_default = ems_cfg.get("p_demand_default", 50.0)
        self.pwm_min = ems_cfg.get("pwm_min", 0)
        self.pwm_max = ems_cfg.get("pwm_max", 1023)
        self.pwm_freq = ems_cfg.get("pwm_freq", 1000)

        self.v_bus_min = thresholds.get("v_bus_min", 10.0)
        self.v_bus_max = thresholds.get("v_bus_max", 14.0)

        error_range = max(
            abs(self.v_bus_min - self.v_bus_nominal),
            abs(self.v_bus_max - self.v_bus_nominal))

        self.error_mfs = {
            'LOW': TriangularMF(
                -error_range, -error_range, 0.0, 'error_LOW'),
            'MED': TriangularMF(
                -error_range * 0.5, 0.0, error_range * 0.5, 'error_MED'),
            'HIGH': TriangularMF(
                0.0, error_range, error_range, 'error_HIGH'),
        }

        self.demand_mfs = {
            'LOW': TriangularMF(0.0, 0.0, 50.0, 'demand_LOW'),
            'MED': TriangularMF(25.0, 50.0, 75.0, 'demand_MED'),
            'HIGH': TriangularMF(50.0, 100.0, 100.0, 'demand_HIGH'),
        }

        self.rules = [
            ('LOW',  'LOW',  0.20),
            ('LOW',  'MED',  0.40),
            ('LOW',  'HIGH', 0.60),
            ('MED',  'LOW',  0.25),
            ('MED',  'MED',  0.50),
            ('MED',  'HIGH', 0.65),
            ('HIGH', 'LOW',  0.30),
            ('HIGH', 'MED',  0.55),
            ('HIGH', 'HIGH', 0.80),
        ]

        self._pwms = []          # list of machine.PWM objects, one per SSR channel
        self._current_duty = 0
        self._last_duty = 0
        self._last_p_demand = self.p_demand_default
        self._inference_count = 0
        self._total_inference_us = 0
        self._last_inference_us = 0

    def init_pwm(self, pins=(25, 26, 27)):
        """Initialize 3 LEDC PWM channels: GPIO25=Bus, GPIO26=Solar, GPIO27=Gen."""
        self._pwms = []
        for pin in pins:
            pwm = machine.PWM(machine.Pin(pin))
            pwm.freq(self.pwm_freq)
            pwm.duty(self.pwm_min)
            self._pwms.append(pwm)
        self._current_duty = self.pwm_min
        print("[ems] PWM initialized: pins={}, freq={}Hz, duty={}".format(
            list(pins), self.pwm_freq, self.pwm_min))

    def _fuzzify_error(self, error):
        return {label: mf.evaluate(error)
                for label, mf in self.error_mfs.items()}

    def _fuzzify_demand(self, demand):
        return {label: mf.evaluate(demand)
                for label, mf in self.demand_mfs.items()}

    def infer(self, v_bus, p_demand=None):
        """
        Run fuzzy inference and return PWM duty cycle.

        Parameters:
            v_bus: Current bus voltage (V).
            p_demand: Power demand setpoint (W). Uses default if None.

        Returns:
            PWM duty cycle (0-1023).
        """
        t0 = time.ticks_us()

        if p_demand is None:
            p_demand = self._last_p_demand
        else:
            self._last_p_demand = p_demand

        error = v_bus - self.v_bus_nominal

        error_fuzzy = self._fuzzify_error(error)
        demand_fuzzy = self._fuzzify_demand(p_demand)

        numerator = 0.0
        denominator = 0.0

        for err_label, dem_label, output in self.rules:
            weight = min(error_fuzzy.get(err_label, 0.0),
                         demand_fuzzy.get(dem_label, 0.0))
            if weight > 0:
                numerator += weight * output
                denominator += weight

        if denominator > 0:
            duty_normalized = numerator / denominator
        else:
            duty_normalized = 0.50

        duty = int(duty_normalized * (self.pwm_max - self.pwm_min) + self.pwm_min)
        duty = max(self.pwm_min, min(self.pwm_max, duty))

        t1 = time.ticks_us()
        self._last_inference_us = time.ticks_diff(t1, t0)
        self._total_inference_us += self._last_inference_us
        self._inference_count += 1

        self._last_duty = self._current_duty
        self._current_duty = duty

        return duty

    def update_pwm(self, duty=None):
        """
        Apply duty cycle to all 3 SSR PWM channels simultaneously.

        Parameters:
            duty: Duty cycle value (0-1023). If None, uses last inferred value.
        """
        if duty is not None:
            self._current_duty = max(self.pwm_min, min(self.pwm_max, int(duty)))

        for pwm in self._pwms:
            pwm.duty(self._current_duty)

    def get_duty_percent(self):
        """Return current duty cycle as percentage (0-100)."""
        if self.pwm_max == self.pwm_min:
            return 0.0
        return ((self._current_duty - self.pwm_min) /
                (self.pwm_max - self.pwm_min)) * 100.0

    def set_demand(self, p_demand):
        """Update the power demand setpoint from server."""
        self._last_p_demand = max(0.0, float(p_demand))

    def get_stats(self):
        """Return inference statistics for metrics reporting."""
        avg_us = (self._total_inference_us / self._inference_count
                  if self._inference_count > 0 else 0.0)
        return {
            'inference_count': self._inference_count,
            'total_inference_us': self._total_inference_us,
            'avg_inference_us': round(avg_us, 1),
            'last_inference_us': self._last_inference_us,
            'current_duty': self._current_duty,
            'duty_percent': round(self.get_duty_percent(), 1),
            'p_demand': self._last_p_demand,
        }

    def stop_pwm(self):
        """Disable all 3 SSR PWM channels (set to minimum duty)."""
        for pwm in self._pwms:
            pwm.duty(self.pwm_min)
        self._current_duty = self.pwm_min
        print("[ems] PWM stopped (duty={})".format(self.pwm_min))
