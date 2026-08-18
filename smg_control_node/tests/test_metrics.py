"""
Metrics module test suite — Welford stddev correctness, float averages,
per-operation latency recording, send failure tracking, V_bus regulation
quality, node power tracking, heap stability, and methodology table.
"""

import math
import pytest
from metrics import MetricsCollector


# ── Welford standard deviation ────────────────────────────────────────────────

class TestWelfordStddev:
    """
    Reference dataset [2, 4, 4, 4, 5, 5, 7, 9]:
      mean = 5.0
      sum of squared deviations = 32
      sample variance = 32/7 ≈ 4.571
      sample stddev = sqrt(32/7) ≈ 2.138
    """
    _DATASET = [2, 4, 4, 4, 5, 5, 7, 9]
    _EXPECTED_MEAN = 5.0
    _EXPECTED_STD  = math.sqrt(32.0 / 7)  # ≈ 2.138

    def test_welford_static_method(self):
        n, mean, M2 = 0, 0.0, 0.0
        for x in self._DATASET:
            n += 1
            mean, M2 = MetricsCollector._welford_update(n, mean, M2, x)
        std = MetricsCollector._welford_std(n, M2)
        assert abs(mean - self._EXPECTED_MEAN) < 1e-9
        assert abs(std - self._EXPECTED_STD) < 0.001

    def test_welford_std_returns_float(self):
        n, mean, M2 = 0, 0.0, 0.0
        for x in self._DATASET:
            n += 1
            mean, M2 = MetricsCollector._welford_update(n, mean, M2, x)
        std = MetricsCollector._welford_std(n, M2)
        assert isinstance(std, float)

    def test_welford_std_zero_for_single_sample(self):
        n, mean, M2 = 1, 5.0, 0.0
        std = MetricsCollector._welford_std(n, M2)
        assert std == 0.0

    def test_welford_constant_series(self):
        n, mean, M2 = 0, 0.0, 0.0
        for x in [7, 7, 7, 7, 7]:
            n += 1
            mean, M2 = MetricsCollector._welford_update(n, mean, M2, x)
        assert MetricsCollector._welford_std(n, M2) == pytest.approx(0.0, abs=1e-9)


# ── Float averages in get_summary() ──────────────────────────────────────────

class TestFloatAverages:
    def _make_collector_with_data(self):
        mc = MetricsCollector()
        # Inject known loop durations: [100, 200, 300] µs → avg = 200.0
        for us in [100, 200, 300]:
            mc.record_loop(us)
        for us in [500, 600]:
            mc.record_send(us, success=True)
        for us in [1000, 2000, 3000]:
            mc.record_sensor_read(us)
        for us in [10, 20]:
            mc.record_ems_inference(us)
        return mc

    def test_loop_avg_is_float(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert isinstance(summary['loop_avg_us'], float)

    def test_loop_avg_correct_value(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert summary['loop_avg_us'] == pytest.approx(200.0, abs=0.1)

    def test_send_avg_is_float(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert isinstance(summary['send_avg_us'], float)
        assert summary['send_avg_us'] == pytest.approx(550.0, abs=0.1)

    def test_sensor_avg_is_float(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert isinstance(summary['sensor_avg_us'], float)
        assert summary['sensor_avg_us'] == pytest.approx(2000.0, abs=0.1)

    def test_ems_avg_is_float(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert isinstance(summary['ems_avg_us'], float)
        assert summary['ems_avg_us'] == pytest.approx(15.0, abs=0.1)

    def test_loop_std_is_float(self):
        mc = self._make_collector_with_data()
        summary = mc.get_summary()
        assert isinstance(summary['loop_std_us'], float)


# ── Per-operation latency (not average-of-averages) ──────────────────────────

class TestPerOperationLatency:
    def test_record_sensor_read_uses_given_value(self):
        """record_sensor_read must feed the exact value into Welford, not an avg."""
        mc = MetricsCollector()
        mc.record_sensor_read(1000)
        mc.record_sensor_read(3000)
        summary = mc.get_summary()
        # avg = (1000+3000)/2 = 2000
        assert summary['sensor_avg_us'] == pytest.approx(2000.0, abs=0.1)
        # std of [1000, 3000] = sqrt((1000000+1000000)/1) = 1414.2
        expected_std = math.sqrt(2_000_000.0)
        assert summary['sensor_std_us'] == pytest.approx(expected_std, rel=0.01)

    def test_ems_inference_welford_correct(self):
        mc = MetricsCollector()
        for us in [8, 10, 12]:
            mc.record_ems_inference(us)
        summary = mc.get_summary()
        assert summary['ems_avg_us'] == pytest.approx(10.0, abs=0.1)


# ── Send failure tracking ─────────────────────────────────────────────────────

class TestSendTracking:
    def test_success_rate_100_percent(self):
        mc = MetricsCollector()
        for _ in range(5):
            mc.record_send(200, success=True)
        assert mc.get_summary()['send_success_rate'] == pytest.approx(100.0)

    def test_success_rate_partial(self):
        mc = MetricsCollector()
        mc.record_send(200, success=True)
        mc.record_send(200, success=False)
        assert mc.get_summary()['send_success_rate'] == pytest.approx(50.0)

    def test_failure_counter(self):
        mc = MetricsCollector()
        mc.record_send(100, success=True)
        mc.record_send(100, success=False)
        mc.record_send(100, success=False)
        summary = mc.get_summary()
        assert summary['send_failures'] == 2
        assert summary['send_count'] == 3


# ── Payload size tracking ─────────────────────────────────────────────────────

class TestPayloadSize:
    def test_payload_avg_is_float(self):
        mc = MetricsCollector()
        mc.record_payload_size(150)
        mc.record_payload_size(160)
        mc.record_payload_size(170)
        summary = mc.get_summary()
        assert 'payload_avg_bytes' in summary
        assert isinstance(summary['payload_avg_bytes'], float)
        assert summary['payload_avg_bytes'] == pytest.approx(160.0, abs=0.1)

    def test_payload_min_max(self):
        mc = MetricsCollector()
        mc.record_payload_size(100)
        mc.record_payload_size(200)
        mc.record_payload_size(150)
        summary = mc.get_summary()
        assert summary['payload_min_bytes'] == 100
        assert summary['payload_max_bytes'] == 200


# ── V_bus regulation quality ──────────────────────────────────────────────────

class TestVBusQuality:
    def test_v_bus_mean(self):
        mc = MetricsCollector()
        for v in [11.8, 12.0, 12.2]:
            mc.record_v_bus(v)
        t = mc.get_methodology_table()
        assert t['CSQ1_realtime_performance']['v_bus_mean_v'] == pytest.approx(12.0, abs=0.001)

    def test_v_bus_std(self):
        mc = MetricsCollector()
        for v in [11.8, 12.0, 12.2]:
            mc.record_v_bus(v)
        t = mc.get_methodology_table()
        # std of [11.8, 12.0, 12.2] = 0.2
        assert t['CSQ1_realtime_performance']['v_bus_std_v'] == pytest.approx(0.2, abs=0.001)

    def test_v_bus_tracking_error_zero_at_nominal(self):
        mc = MetricsCollector()
        mc.set_v_nominal(12.0)
        for _ in range(5):
            mc.record_v_bus(12.0)
        t = mc.get_methodology_table()
        assert t['CSQ1_realtime_performance']['v_bus_tracking_error_v'] == pytest.approx(0.0, abs=1e-6)

    def test_v_bus_tracking_error_nonzero(self):
        mc = MetricsCollector()
        mc.set_v_nominal(12.0)
        for _ in range(4):
            mc.record_v_bus(11.5)
        t = mc.get_methodology_table()
        assert t['CSQ1_realtime_performance']['v_bus_tracking_error_v'] == pytest.approx(0.5, abs=0.001)

    def test_v_bus_none_when_no_samples(self):
        mc = MetricsCollector()
        t = mc.get_methodology_table()
        assert t['CSQ1_realtime_performance']['v_bus_mean_v'] is None

    def test_set_v_nominal_changes_tracking_error(self):
        mc = MetricsCollector()
        mc.set_v_nominal(24.0)
        for _ in range(3):
            mc.record_v_bus(23.0)
        t = mc.get_methodology_table()
        assert t['CSQ1_realtime_performance']['v_bus_tracking_error_v'] == pytest.approx(1.0, abs=0.001)


# ── Node power tracking ───────────────────────────────────────────────────────

class TestNodePower:
    def test_node_power_mean(self):
        mc = MetricsCollector()
        for p in [0.5, 0.6, 0.7]:
            mc.record_node_power(p)
        t = mc.get_methodology_table()
        assert t['CSQ4_energy_communication']['node_power_mean_w'] == pytest.approx(0.6, abs=0.001)

    def test_node_power_std(self):
        mc = MetricsCollector()
        for p in [0.5, 0.6, 0.7]:
            mc.record_node_power(p)
        t = mc.get_methodology_table()
        assert t['CSQ4_energy_communication']['node_power_std_w'] == pytest.approx(0.1, abs=0.001)

    def test_node_power_none_when_no_samples(self):
        mc = MetricsCollector()
        t = mc.get_methodology_table()
        assert t['CSQ4_energy_communication']['node_power_mean_w'] is None


# ── Heap stability ────────────────────────────────────────────────────────────

class TestHeapStability:
    def test_heap_drift_zero_with_no_samples(self):
        mc = MetricsCollector()
        assert mc._heap_drift_per_100_loops() == 0

    def test_heap_drift_zero_with_one_sample(self):
        mc = MetricsCollector()
        mc._heap_samples.append((100, 50000))
        assert mc._heap_drift_per_100_loops() == 0

    def test_heap_drift_positive(self):
        mc = MetricsCollector()
        mc._heap_samples.append((0, 40000))
        mc._heap_samples.append((100, 41000))
        assert mc._heap_drift_per_100_loops() == pytest.approx(1000.0, abs=0.1)

    def test_heap_drift_negative_indicates_leak(self):
        mc = MetricsCollector()
        mc._heap_samples.append((0, 50000))
        mc._heap_samples.append((200, 49000))
        # drift = (49000-50000)/200*100 = -500
        assert mc._heap_drift_per_100_loops() == pytest.approx(-500.0, abs=0.1)

    def test_maybe_snapshot_heap_fires_at_interval(self):
        mc = MetricsCollector()
        mc._heap_sample_interval = 10
        for i in range(10):
            mc.record_loop(100)          # advances loop_count
            mc.maybe_snapshot_heap()
        # loop_count reached 10 exactly once → one sample
        assert len(mc._heap_samples) == 1

    def test_maybe_snapshot_heap_bounded_at_20(self):
        mc = MetricsCollector()
        mc._heap_sample_interval = 1
        for i in range(25):
            mc.record_loop(100)
            mc.maybe_snapshot_heap()
        assert len(mc._heap_samples) <= 20


# ── Methodology table structure ───────────────────────────────────────────────

class TestMethodologyTable:
    def _make_full_collector(self):
        mc = MetricsCollector()
        for _ in range(300):
            mc.record_loop(333000)
        for _ in range(300):
            mc.record_sensor_read(50000)
        for _ in range(300):
            mc.record_ems_inference(200)
        for _ in range(300):
            mc.record_send(5000, success=True)
        for v in [11.9, 12.0, 12.1]:
            mc.record_v_bus(v)
        for p in [0.5, 0.55, 0.6]:
            mc.record_node_power(p)
        return mc

    def test_table_has_four_csq_keys(self):
        mc = self._make_full_collector()
        t = mc.get_methodology_table()
        assert 'CSQ1_realtime_performance' in t
        assert 'CSQ2_resource_constraints' in t
        assert 'CSQ3_security_overhead' in t
        assert 'CSQ4_energy_communication' in t

    def test_csq1_has_required_fields(self):
        mc = self._make_full_collector()
        c1 = mc.get_methodology_table()['CSQ1_realtime_performance']
        for field in ('target_sampling_hz', 'achieved_sampling_hz',
                      'loop_avg_us', 'loop_std_us', 'sensor_avg_us', 'ems_avg_us',
                      'v_bus_nominal_v', 'v_bus_mean_v', 'v_bus_tracking_error_v'):
            assert field in c1, "Missing CSQ1 field: {}".format(field)

    def test_csq2_has_heap_fields(self):
        mc = self._make_full_collector()
        c2 = mc.get_methodology_table()['CSQ2_resource_constraints']
        assert 'heap_snapshots' in c2
        assert 'heap_drift_per_100loops' in c2
        assert 'gc_mem_free' in c2

    def test_csq3_populated_from_security_metrics(self):
        mc = self._make_full_collector()
        sec = {
            'p2_duration_us': 1000,
            'p3_duration_us': 2000,
            'handshake_total_us': 3000,
            'p4_avg_us': 500,
            'p4_std_us': 30,
            'p5_duration_us': 400,
            'chain_length': 10,
        }
        c3 = mc.get_methodology_table(security_metrics=sec)['CSQ3_security_overhead']
        assert c3['p4_avg_us'] == 500
        assert c3['p4_std_us'] == 30
        assert c3['handshake_us'] == 3000

    def test_csq3_empty_without_security_metrics(self):
        mc = self._make_full_collector()
        c3 = mc.get_methodology_table()['CSQ3_security_overhead']
        assert c3 == {}

    def test_csq4_has_communication_fields(self):
        mc = self._make_full_collector()
        c4 = mc.get_methodology_table()['CSQ4_energy_communication']
        assert 'send_success_rate' in c4
        assert 'node_power_mean_w' in c4
        assert 'send_avg_us' in c4

    def test_send_success_rate_100_percent(self):
        mc = MetricsCollector()
        for _ in range(10):
            mc.record_send(1000, success=True)
        c4 = mc.get_methodology_table()['CSQ4_energy_communication']
        assert c4['send_success_rate'] == pytest.approx(100.0)


# ── export_methodology_csv (format check) ────────────────────────────────────

class TestExportMethodologyCsv:
    def test_export_creates_file(self, tmp_path):
        mc = MetricsCollector()
        for _ in range(10):
            mc.record_loop(333000)
            mc.record_sensor_read(50000)
            mc.record_ems_inference(200)
        path = str(tmp_path / "methodology.csv")
        ok = mc.export_methodology_csv(path)
        assert ok is True
        with open(path) as f:
            lines = f.readlines()
        assert lines[0].strip() == "csq,metric,value,unit"
        assert len(lines) > 5

    def test_export_contains_csq1_rows(self, tmp_path):
        mc = MetricsCollector()
        for _ in range(10):
            mc.record_loop(333000)
        path = str(tmp_path / "m.csv")
        mc.export_methodology_csv(path)
        with open(path) as f:
            content = f.read()
        assert "CSQ1" in content
        assert "loop_avg_us" in content

    def test_export_contains_csq3_when_security_provided(self, tmp_path):
        mc = MetricsCollector()
        sec = {
            'p2_duration_us': 500, 'p3_duration_us': 1000,
            'handshake_total_us': 1500, 'p4_avg_us': 400,
            'p4_std_us': 25, 'p5_duration_us': 200, 'chain_length': 5,
        }
        path = str(tmp_path / "m.csv")
        mc.export_methodology_csv(path, security_metrics=sec)
        with open(path) as f:
            content = f.read()
        assert "CSQ3" in content
        assert "p4_std_us" in content
