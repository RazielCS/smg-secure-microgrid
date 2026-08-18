"""
Sensor module for SMG control node.

Reads 5 analog channels at 3 Hz:
  - Bus current:  ACS712-05B on GPIO34 (ADC1 CH6), 185 mV/A, Vref=2.5V (5V supply)
  - Gen current:  ACS712-05B on GPIO35 (ADC1 CH7), 185 mV/A, Vref=2.5V (5V supply)
  - Bus voltage:  Resistive divider (30K/7.5K, ratio 5:1) on GPIO36 (ADC1 CH0)
  - Gen voltage:  Resistive divider (30K/7.5K, ratio 5:1) on GPIO39 (ADC1 CH3)
  - Node current: ACS712-05B on GPIO32 (ADC1 CH4), 185 mV/A, Vref=2.5V (5V supply)
                  In series with 12V buck converter input — measures node self-consumption

Processing per channel:
  - 16-sample oversampling with median filter (removes outliers)
  - ADC-to-physical conversion with configurable calibration
  - Power computation: P = V x I

Returns SensorReading(V_bus, I_bus, P_bus, V_gen, I_gen, P_gen, I_node, P_node, timestamp_ms)
"""

import time
import machine

ADC_ATTEN = 3   # machine.ADC.ATTN_11DB (0-3.6V). Literal avoids ADC hw init at import time,
                # which corrupts lwip routing tables on ESP32 when WiFi is active.
ADC_MAX = 4095
ADC_VREF = 3.3

NUM_SAMPLES = 16
SAMPLE_DELAY_MS = 2


_ADC_SAT_HIGH = 3.2   # V — ADC near 3.3V rail; reading unreliable
_ADC_SAT_LOW  = 0.1   # V — ADC near floor; reading unreliable

# Physical bounds for the 12V SMG. Values outside these indicate sensor
# failure, miswiring, or miscalibration rather than operating conditions.
_VBUS_MAX = 20.0    # V  — absolute hardware limit for 12V bus
_VBUS_MIN = 0.0     # V  — bus voltage cannot be negative
_VGEN_MAX = 20.0    # V
_VGEN_MIN = 0.0     # V
_IBUS_MAX = 10.0    # A  — ACS712-05B rated ±5A with margin
_IBUS_MIN = -10.0   # A  — bidirectional; negative = reverse flow
_IGEN_MAX = 10.0    # A
_IGEN_MIN = -10.0   # A
_INODE_MAX = 3.0    # A  — ESP32 + sensors + SSR control; >3A indicates fault
_INODE_MIN = 0.0    # A  — node supply is unidirectional (buck converter input)


class SensorReading:
    """Immutable sensor reading snapshot."""
    __slots__ = ('V_bus', 'I_bus', 'P_bus', 'V_gen', 'I_gen', 'P_gen',
                 'I_node', 'P_node', 'timestamp_ms', 'saturated')

    def __init__(self, V_bus, I_bus, P_bus, V_gen, I_gen, P_gen,
                 I_node, P_node, timestamp_ms, saturated=False):
        self.V_bus = V_bus
        self.I_bus = I_bus
        self.P_bus = P_bus
        self.V_gen = V_gen
        self.I_gen = I_gen
        self.P_gen = P_gen
        self.I_node = I_node
        self.P_node = P_node
        self.timestamp_ms = timestamp_ms
        self.saturated = saturated

    def to_dict(self):
        return {
            'V_bus': round(self.V_bus, 3),
            'I_bus': round(self.I_bus, 3),
            'P_bus': round(self.P_bus, 2),
            'V_gen': round(self.V_gen, 3),
            'I_gen': round(self.I_gen, 3),
            'P_gen': round(self.P_gen, 2),
            'I_node': round(self.I_node, 3),
            'P_node': round(self.P_node, 2),
            'ts_ms': self.timestamp_ms,
            'sat': self.saturated,
        }

    def to_json_bytes(self):
        import ujson
        return ujson.dumps(self.to_dict()).encode()

    def __repr__(self):
        return ("SensorReading(V_bus={:.2f}V I_bus={:.2f}A P_bus={:.1f}W "
                "V_gen={:.2f}V I_gen={:.2f}A P_gen={:.1f}W "
                "I_node={:.3f}A P_node={:.2f}W)").format(
            self.V_bus, self.I_bus, self.P_bus,
            self.V_gen, self.I_gen, self.P_gen,
            self.I_node, self.P_node)


def _median_filter(samples):
    """Compute median of a list of values. Handles even-length lists."""
    s = sorted(samples)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2.0


def _read_adc_median(adc, num_samples=NUM_SAMPLES):
    """Read ADC with oversampling and median filtering."""
    samples = []
    for _ in range(num_samples):
        raw = adc.read()
        samples.append(raw)
        if num_samples > 1:
            time.sleep_ms(SAMPLE_DELAY_MS)
    return _median_filter(samples)


def _adc_to_voltage(adc_raw, vref=ADC_VREF):
    """Convert ADC raw value to voltage at the pin."""
    return (adc_raw / ADC_MAX) * vref


def _acs712_to_current(voltage, vref=2.5, sensitivity=0.185, offset=0.0):
    """
    Convert ACS712 output voltage to current.

    I = (V_adc - Vref) / sensitivity + offset
    """
    return (voltage - vref) / sensitivity + offset


def _vdiv_to_input(voltage, ratio=5.0, offset=0.0):
    """
    Convert voltage divider output to actual input voltage.

    V_in = V_adc * ratio + offset
    """
    return voltage * ratio + offset


class SensorModule:
    """Manages all SMG sensor readings."""

    def __init__(self, config):
        cal = config.get("calibration", {})

        self.cal_bus_current  = cal.get("acs712_bus", {})
        self.cal_gen_current  = cal.get("acs712_gen", {})
        self.cal_bus_voltage  = cal.get("vdiv_bus", {})
        self.cal_gen_voltage  = cal.get("vdiv_gen", {})
        self.cal_node_current = cal.get("acs712_node", {})

        self.adc_bus_current  = machine.ADC(machine.Pin(34))
        self.adc_gen_current  = machine.ADC(machine.Pin(35))
        self.adc_bus_voltage  = machine.ADC(machine.Pin(36))
        self.adc_gen_voltage  = machine.ADC(machine.Pin(39))
        self.adc_node_current = machine.ADC(machine.Pin(32))  # node self-consumption

        for adc in (self.adc_bus_current, self.adc_gen_current,
                     self.adc_bus_voltage, self.adc_gen_voltage,
                     self.adc_node_current):
            adc.atten(ADC_ATTEN)
            adc.width(machine.ADC.WIDTH_12BIT)

        self._last_reading = None
        self._read_count = 0
        self._total_read_us = 0
        self._last_read_us = 0

    def read(self):
        """
        Read all 4 sensors and compute power values.

        Returns:
            SensorReading namedtuple with calibrated values.
        """
        t0 = time.ticks_us()

        adc_bc = _read_adc_median(self.adc_bus_current)
        adc_gc = _read_adc_median(self.adc_gen_current)
        adc_bv = _read_adc_median(self.adc_bus_voltage)
        adc_gv = _read_adc_median(self.adc_gen_voltage)
        adc_nc = _read_adc_median(self.adc_node_current)

        v_adc_bc = _adc_to_voltage(adc_bc)
        v_adc_gc = _adc_to_voltage(adc_gc)
        v_adc_bv = _adc_to_voltage(adc_bv)
        v_adc_gv = _adc_to_voltage(adc_gv)
        v_adc_nc = _adc_to_voltage(adc_nc)

        i_bus = _acs712_to_current(
            v_adc_bc,
            vref=self.cal_bus_current.get("vref", 2.5),
            sensitivity=self.cal_bus_current.get("sensitivity", 0.185),
            offset=self.cal_bus_current.get("offset", 0.0))

        i_gen = _acs712_to_current(
            v_adc_gc,
            vref=self.cal_gen_current.get("vref", 2.5),
            sensitivity=self.cal_gen_current.get("sensitivity", 0.185),
            offset=self.cal_gen_current.get("offset", 0.0))

        v_bus = _vdiv_to_input(
            v_adc_bv,
            ratio=self.cal_bus_voltage.get("ratio", 5.0),
            offset=self.cal_bus_voltage.get("offset", 0.0))

        v_gen = _vdiv_to_input(
            v_adc_gv,
            ratio=self.cal_gen_voltage.get("ratio", 5.0),
            offset=self.cal_gen_voltage.get("offset", 0.0))

        i_node = _acs712_to_current(
            v_adc_nc,
            vref=self.cal_node_current.get("vref", 2.5),
            sensitivity=self.cal_node_current.get("sensitivity", 0.185),
            offset=self.cal_node_current.get("offset", 0.0))

        p_bus  = v_bus * i_bus
        p_gen  = v_gen * i_gen
        p_node = v_bus * i_node  # node supply drawn from 12V bus via LM2596 buck converter

        adc_saturated = (
            v_adc_bc >= _ADC_SAT_HIGH or v_adc_bc <= _ADC_SAT_LOW or
            v_adc_gc >= _ADC_SAT_HIGH or v_adc_gc <= _ADC_SAT_LOW or
            v_adc_bv >= _ADC_SAT_HIGH or v_adc_bv <= _ADC_SAT_LOW or
            v_adc_gv >= _ADC_SAT_HIGH or v_adc_gv <= _ADC_SAT_LOW or
            v_adc_nc >= _ADC_SAT_HIGH or v_adc_nc <= _ADC_SAT_LOW
        )
        if adc_saturated:
            print("[sensor] WARNING: ADC saturation detected")

        out_of_bounds = (
            v_bus  < _VBUS_MIN  or v_bus  > _VBUS_MAX  or
            v_gen  < _VGEN_MIN  or v_gen  > _VGEN_MAX  or
            i_bus  < _IBUS_MIN  or i_bus  > _IBUS_MAX  or
            i_gen  < _IGEN_MIN  or i_gen  > _IGEN_MAX  or
            i_node < _INODE_MIN or i_node > _INODE_MAX
        )
        if out_of_bounds:
            print("[sensor] WARNING: Physical bounds exceeded "
                  "V_bus={:.2f} I_bus={:.2f} V_gen={:.2f} I_gen={:.2f} I_node={:.3f}".format(
                      v_bus, i_bus, v_gen, i_gen, i_node))

        saturated = adc_saturated or out_of_bounds

        ts = time.ticks_ms()

        reading = SensorReading(v_bus, i_bus, p_bus, v_gen, i_gen, p_gen,
                                i_node, p_node, ts, saturated)
        self._last_reading = reading
        self._read_count += 1

        t1 = time.ticks_us()
        self._last_read_us = time.ticks_diff(t1, t0)
        self._total_read_us += self._last_read_us

        return reading

    def get_last_reading(self):
        """Return the most recent sensor reading, or None if not yet read."""
        return self._last_reading

    def get_read_stats(self):
        """Return reading statistics for metrics reporting."""
        avg_us = (self._total_read_us / self._read_count
                  if self._read_count > 0 else 0.0)
        return {
            'read_count': self._read_count,
            'total_read_us': self._total_read_us,
            'avg_read_us': round(avg_us, 1),
            'last_read_us': self._last_read_us,
        }

    def calibrate_zero_current(self, num_samples=32):
        """
        Measure ACS712 zero-current offset with no load connected.

        Call this during initial calibration with no current flowing
        through the sensors. Updates the offset in calibration config.
        """
        print("[sensor] Calibrating zero-current offset...")
        print("[sensor] Ensure NO current is flowing through ACS712 sensors.")
        print("[sensor] NOTE: node sensor calibrates with normal node load (idle consumption).")

        bus_samples  = []
        gen_samples  = []
        node_samples = []
        for _ in range(num_samples):
            v_bc = _adc_to_voltage(_read_adc_median(self.adc_bus_current, 8))
            v_gc = _adc_to_voltage(_read_adc_median(self.adc_gen_current, 8))
            v_nc = _adc_to_voltage(_read_adc_median(self.adc_node_current, 8))
            bus_samples.append(v_bc)
            gen_samples.append(v_gc)
            node_samples.append(v_nc)
            time.sleep_ms(50)

        bus_offset_v  = sum(bus_samples)  / len(bus_samples)
        gen_offset_v  = sum(gen_samples)  / len(gen_samples)
        node_offset_v = sum(node_samples) / len(node_samples)

        bus_vref  = self.cal_bus_current.get("vref", 2.5)
        gen_vref  = self.cal_gen_current.get("vref", 2.5)
        node_vref = self.cal_node_current.get("vref", 2.5)
        bus_sens  = self.cal_bus_current.get("sensitivity", 0.185)
        gen_sens  = self.cal_gen_current.get("sensitivity", 0.185)
        node_sens = self.cal_node_current.get("sensitivity", 0.185)

        bus_offset_a  = (bus_offset_v  - bus_vref)  / bus_sens
        gen_offset_a  = (gen_offset_v  - gen_vref)  / gen_sens
        node_offset_a = (node_offset_v - node_vref) / node_sens

        self.cal_bus_current["offset"]  = -bus_offset_a
        self.cal_gen_current["offset"]  = -gen_offset_a
        self.cal_node_current["offset"] = -node_offset_a

        print("[sensor] Bus  current offset: {:.4f} V -> {:.4f} A correction".format(
            bus_offset_v, -bus_offset_a))
        print("[sensor] Gen  current offset: {:.4f} V -> {:.4f} A correction".format(
            gen_offset_v, -gen_offset_a))
        print("[sensor] Node current offset: {:.4f} V -> {:.4f} A correction".format(
            node_offset_v, -node_offset_a))

        return {
            "bus_offset_v":  bus_offset_v,
            "bus_offset_a":  -bus_offset_a,
            "gen_offset_v":  gen_offset_v,
            "gen_offset_a":  -gen_offset_a,
            "node_offset_v": node_offset_v,
            "node_offset_a": -node_offset_a,
        }
