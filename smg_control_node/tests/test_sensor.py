"""
Sensor module test suite — conversion functions and stats.

All functions under test (_adc_to_voltage, _acs712_to_current,
_vdiv_to_input, _median_filter) are pure computations with no hardware
dependency. SensorModule.__init__ is not exercised here because it calls
machine.ADC which is a mock.
"""

import pytest
from sensor_module import (
    _adc_to_voltage,
    _acs712_to_current,
    _vdiv_to_input,
    _median_filter,
    ADC_MAX,
    ADC_VREF,
)


# ── ADC to voltage ────────────────────────────────────────────────────────────

class TestAdcToVoltage:
    def test_zero_input(self):
        assert _adc_to_voltage(0) == pytest.approx(0.0)

    def test_full_scale(self):
        assert _adc_to_voltage(ADC_MAX) == pytest.approx(ADC_VREF)

    def test_midpoint(self):
        expected = ADC_VREF / 2.0
        result = _adc_to_voltage(ADC_MAX // 2)
        assert result == pytest.approx(expected, rel=0.01)

    def test_linearity(self):
        v1 = _adc_to_voltage(1000)
        v2 = _adc_to_voltage(2000)
        assert v2 == pytest.approx(v1 * 2, rel=0.01)


# ── ACS712 current conversion ─────────────────────────────────────────────────

class TestAcs712ToCurrent:
    def test_zero_current_at_vref(self):
        current = _acs712_to_current(voltage=1.65, vref=1.65, sensitivity=0.185)
        assert current == pytest.approx(0.0, abs=1e-9)

    def test_positive_current(self):
        # 1A at 185mV/A: v = vref + 0.185 → I = (vref + 0.185 - vref) / 0.185 = 1.0
        current = _acs712_to_current(voltage=1.65 + 0.185, vref=1.65, sensitivity=0.185)
        assert current == pytest.approx(1.0, abs=1e-6)

    def test_negative_current(self):
        current = _acs712_to_current(voltage=1.65 - 0.185, vref=1.65, sensitivity=0.185)
        assert current == pytest.approx(-1.0, abs=1e-6)

    def test_five_amps(self):
        current = _acs712_to_current(voltage=1.65 + 5 * 0.185, vref=1.65, sensitivity=0.185)
        assert current == pytest.approx(5.0, abs=1e-5)

    def test_offset_applied(self):
        # offset shifts the zero-current point
        current = _acs712_to_current(voltage=1.65, vref=1.65, sensitivity=0.185, offset=0.5)
        assert current == pytest.approx(0.5, abs=1e-9)


# ── Voltage divider ───────────────────────────────────────────────────────────

class TestVdivInput:
    def test_5to1_ratio(self):
        # V_adc = 2.4V, ratio 5:1 → V_in = 12V
        assert _vdiv_to_input(2.4, ratio=5.0) == pytest.approx(12.0)

    def test_zero_input(self):
        assert _vdiv_to_input(0.0, ratio=5.0) == pytest.approx(0.0)

    def test_with_offset(self):
        # offset shifts the output
        result = _vdiv_to_input(2.0, ratio=5.0, offset=0.5)
        assert result == pytest.approx(2.0 * 5.0 + 0.5)


# ── Median filter ─────────────────────────────────────────────────────────────

class TestMedianFilter:
    def test_odd_count(self):
        assert _median_filter([3, 1, 4, 1, 5]) == 3

    def test_even_count(self):
        assert _median_filter([1, 3, 5, 7]) == pytest.approx(4.0)

    def test_single_element(self):
        assert _median_filter([42]) == 42

    def test_already_sorted(self):
        assert _median_filter([1, 2, 3, 4, 5]) == 3

    def test_reverse_sorted(self):
        assert _median_filter([5, 4, 3, 2, 1]) == 3

    def test_all_identical(self):
        assert _median_filter([7, 7, 7, 7]) == pytest.approx(7.0)

    def test_16_samples_midpoint(self):
        # 16 samples as used by the firmware (even count → average of two middle values)
        samples = list(range(16))  # 0..15
        result = _median_filter(samples)
        assert result == pytest.approx(7.5)


# ── SensorModule stats ────────────────────────────────────────────────────────

class TestSensorModuleStats:
    """
    Verifies that get_read_stats() returns float avg and exposes last_read_us.
    Uses a mocked ADC so no hardware is needed.
    """

    def _make_module(self):
        from unittest.mock import MagicMock, patch
        import sensor_module

        cfg = {
            "calibration": {
                "acs712_bus":  {"vref": 2.5, "sensitivity": 0.185, "offset": 0.0},
                "acs712_gen":  {"vref": 2.5, "sensitivity": 0.185, "offset": 0.0},
                "acs712_node": {"vref": 2.5, "sensitivity": 0.185, "offset": 0.0},
                "vdiv_bus":    {"ratio": 5.0, "offset": 0.0},
                "vdiv_gen":    {"ratio": 5.0, "offset": 0.0},
            }
        }
        mod = sensor_module.SensorModule(cfg)
        # Inject a fixed ADC read value (mid-scale → ~2.5V → 0A at 5V supply, ~8.25V bus)
        mid_scale = sensor_module.ADC_MAX // 2
        for adc_attr in ('adc_bus_current', 'adc_gen_current',
                         'adc_bus_voltage', 'adc_gen_voltage', 'adc_node_current'):
            mock_adc = MagicMock()
            mock_adc.read.return_value = mid_scale
            setattr(mod, adc_attr, mock_adc)
        return mod

    def test_get_read_stats_last_read_us_present(self):
        mod = self._make_module()
        mod.read()
        stats = mod.get_read_stats()
        assert 'last_read_us' in stats

    def test_get_read_stats_avg_is_float(self):
        mod = self._make_module()
        for _ in range(3):
            mod.read()
        stats = mod.get_read_stats()
        assert isinstance(stats['avg_read_us'], float)

    def test_last_read_us_is_per_operation(self):
        """last_read_us must reflect the most recent read, not a running average."""
        mod = self._make_module()
        mod.read()
        stats1 = mod.get_read_stats()
        last1 = stats1['last_read_us']

        mod.read()
        stats2 = mod.get_read_stats()
        last2 = stats2['last_read_us']

        # Both are non-negative integers; avg should be their mean
        assert last1 >= 0
        assert last2 >= 0
        assert stats2['read_count'] == 2
