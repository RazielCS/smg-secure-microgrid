"""
Metrics collection module for SMG control node.

Aggregates performance data from all subsystems for the methodology
case study validation. Maps to CSQ1-CSQ4 research questions.

Data collected:
  - Authentication latency (P2+P3)     -> CSQ3
  - P4 transaction latency (avg+std)   -> CSQ3
  - Sensor reading latency             -> CSQ1
  - Fuzzy EMS execution time           -> CSQ1
  - End-to-end loop latency            -> CSQ1
  - V_bus regulation quality (mean/std/tracking error) -> CSQ1
  - Free heap per phase                -> CSQ2
  - Heap drift over time               -> CSQ2
  - Node self-consumption power        -> CSQ4
  - Payload size                       -> CSQ4
  - Send success rate                  -> CSQ4
  - Sampling rate achieved             -> CSQ1
"""

import time
import gc
import math

try:
    import ujson
except ImportError:
    import json as ujson


class MetricsCollector:
    """Collects and reports SMG performance metrics."""

    def __init__(self):
        self.loop_count = 0
        self.loop_total_us = 0
        self.loop_min_us = None
        self.loop_max_us = 0
        self.send_count = 0
        self.send_total_us = 0
        self.send_failures = 0
        self.sensor_read_count = 0
        self.sensor_total_us = 0
        self.ems_inference_count = 0
        self.ems_total_us = 0
        self.start_time = time.ticks_ms()
        self._last_heap = None
        self._heap_snapshots = {}
        self._payload_sizes = []
        # Welford's online algorithm state (mean_f, M2) per metric.
        # Enables stddev without storing all samples — O(1) memory, O(n) time.
        self._loop_mean_f = 0.0
        self._loop_M2 = 0.0
        self._send_mean_f = 0.0
        self._send_M2 = 0.0
        self._sensor_mean_f = 0.0
        self._sensor_M2 = 0.0
        self._ems_mean_f = 0.0
        self._ems_M2 = 0.0

        # V_bus regulation quality — CSQ1
        self._vbus_nominal = 12.0
        self._vbus_count = 0
        self._vbus_mean_f = 0.0
        self._vbus_M2 = 0.0
        self._vbus_min = None
        self._vbus_max = 0.0

        # Node self-consumption power — CSQ4 energy proxy
        self._pnode_count = 0
        self._pnode_mean_f = 0.0
        self._pnode_M2 = 0.0

        # Periodic heap samples for stability/leak detection — bounded, O(1) memory.
        # Populated by maybe_snapshot_heap() every _heap_sample_interval loops.
        self._heap_samples = []           # [(loop_count, free_bytes)]
        self._heap_sample_interval = 100

    @staticmethod
    def _welford_update(n, mean, M2, x):
        """Welford online update; returns (new_mean, new_M2)."""
        delta = x - mean
        mean += delta / n
        M2 += delta * (x - mean)
        return mean, M2

    @staticmethod
    def _welford_std(n, M2):
        """Sample standard deviation (n-1 denominator); returns float."""
        if n < 2:
            return 0.0
        return math.sqrt(M2 / (n - 1))

    def record_loop(self, duration_us):
        """Record one complete loop cycle duration."""
        self.loop_count += 1
        self.loop_total_us += duration_us
        if self.loop_min_us is None or duration_us < self.loop_min_us:
            self.loop_min_us = duration_us
        if duration_us > self.loop_max_us:
            self.loop_max_us = duration_us
        self._loop_mean_f, self._loop_M2 = self._welford_update(
            self.loop_count, self._loop_mean_f, self._loop_M2, duration_us)

    def record_send(self, duration_us, success=True):
        """Record a secure data send operation."""
        self.send_count += 1
        self.send_total_us += duration_us
        if not success:
            self.send_failures += 1
        self._send_mean_f, self._send_M2 = self._welford_update(
            self.send_count, self._send_mean_f, self._send_M2, duration_us)

    def record_sensor_read(self, duration_us):
        """Record a sensor reading operation."""
        self.sensor_read_count += 1
        self.sensor_total_us += duration_us
        self._sensor_mean_f, self._sensor_M2 = self._welford_update(
            self.sensor_read_count, self._sensor_mean_f, self._sensor_M2, duration_us)

    def record_ems_inference(self, duration_us):
        """Record a fuzzy EMS inference operation."""
        self.ems_inference_count += 1
        self.ems_total_us += duration_us
        self._ems_mean_f, self._ems_M2 = self._welford_update(
            self.ems_inference_count, self._ems_mean_f, self._ems_M2, duration_us)

    def set_v_nominal(self, v_nominal):
        """Update nominal bus voltage used for tracking-error calculation."""
        self._vbus_nominal = float(v_nominal)

    def record_v_bus(self, v_bus):
        """Record one bus voltage sample for regulation quality metrics (CSQ1)."""
        self._vbus_count += 1
        self._vbus_mean_f, self._vbus_M2 = self._welford_update(
            self._vbus_count, self._vbus_mean_f, self._vbus_M2, float(v_bus))
        if self._vbus_min is None or v_bus < self._vbus_min:
            self._vbus_min = float(v_bus)
        if v_bus > self._vbus_max:
            self._vbus_max = float(v_bus)

    def record_node_power(self, p_node):
        """Record node self-consumption power sample (CSQ4 energy proxy)."""
        self._pnode_count += 1
        self._pnode_mean_f, self._pnode_M2 = self._welford_update(
            self._pnode_count, self._pnode_mean_f, self._pnode_M2, float(p_node))

    def maybe_snapshot_heap(self):
        """Call once per loop iteration; snapshots heap every _heap_sample_interval loops."""
        if self.loop_count > 0 and self.loop_count % self._heap_sample_interval == 0:
            gc.collect()
            free = gc.mem_free()
            self._heap_samples.append((self.loop_count, free))
            if len(self._heap_samples) > 20:   # keep last 20 samples
                self._heap_samples.pop(0)

    def _heap_drift_per_100_loops(self):
        """Heap change per 100 loops between oldest and newest sample. Negative = leak."""
        if len(self._heap_samples) < 2:
            return 0
        first = self._heap_samples[0]
        last = self._heap_samples[-1]
        span = last[0] - first[0]
        if span <= 0:
            return 0
        return round((last[1] - first[1]) / span * 100, 1)

    def record_payload_size(self, size_bytes):
        """Record the size of a transmitted payload (bounded ring; last 20 samples)."""
        self._payload_sizes.append(size_bytes)
        if len(self._payload_sizes) > 20:
            self._payload_sizes.pop(0)

    def snapshot_heap(self, label):
        """Record free heap at a specific point."""
        gc.collect()
        free = gc.mem_free()
        self._heap_snapshots[label] = free
        if self._last_heap is not None:
            delta = self._last_heap - free
        else:
            delta = 0
        self._last_heap = free
        return free, delta

    def get_sampling_rate(self):
        """Calculate achieved sampling rate in Hz."""
        if self.loop_count < 2:
            return 0.0
        elapsed_s = (time.ticks_ms() - self.start_time) / 1000.0
        if elapsed_s <= 0:
            return 0.0
        return self.loop_count / elapsed_s

    def get_summary(self, security_metrics=None, sensor_stats=None, ems_stats=None, comm_stats=None):
        """
        Build comprehensive metrics summary for case study reporting.

        Parameters:
            security_metrics: Dict from SecurityManager.get_metrics()
            sensor_stats: Dict from SensorModule.get_read_stats()
            ems_stats: Dict from FuzzyEMS.get_stats()
            comm_stats: Dict from CommManager.get_stats()

        Returns:
            Dict with all aggregated metrics.
        """
        gc.collect()
        elapsed_s = (time.ticks_ms() - self.start_time) / 1000.0

        summary = {
            'uptime_s': round(elapsed_s, 1),
            'loop_count': self.loop_count,
            'sampling_rate_hz': round(self.get_sampling_rate(), 2),
            'loop_avg_us': round(self._loop_mean_f, 1) if self.loop_count > 0 else 0.0,
            'loop_std_us': self._welford_std(self.loop_count, self._loop_M2),
            'loop_min_us': self.loop_min_us or 0,
            'loop_max_us': self.loop_max_us,
            'send_count': self.send_count,
            'send_failures': self.send_failures,
            'send_success_rate': round(
                (self.send_count - self.send_failures) / self.send_count * 100, 1
            ) if self.send_count > 0 else 0,
            'send_avg_us': round(self._send_mean_f, 1) if self.send_count > 0 else 0.0,
            'send_std_us': self._welford_std(self.send_count, self._send_M2),
            'sensor_reads': self.sensor_read_count,
            'sensor_avg_us': round(self._sensor_mean_f, 1) if self.sensor_read_count > 0 else 0.0,
            'sensor_std_us': self._welford_std(self.sensor_read_count, self._sensor_M2),
            'ems_inferences': self.ems_inference_count,
            'ems_avg_us': round(self._ems_mean_f, 1) if self.ems_inference_count > 0 else 0.0,
            'ems_std_us': self._welford_std(self.ems_inference_count, self._ems_M2),
            'gc_mem_free': gc.mem_free(),
            'heap_snapshots': dict(self._heap_snapshots),
        }

        if self._payload_sizes:
            summary['payload_avg_bytes'] = round(sum(self._payload_sizes) / len(self._payload_sizes), 1)
            summary['payload_max_bytes'] = max(self._payload_sizes)
            summary['payload_min_bytes'] = min(self._payload_sizes)

        if security_metrics:
            summary['security'] = {
                'p2_us': security_metrics.get('p2_duration_us', 0),
                'p3_us': security_metrics.get('p3_duration_us', 0),
                'handshake_us': security_metrics.get('handshake_total_us', 0),
                'p4_count': security_metrics.get('p4_count', 0),
                'p4_avg_us': security_metrics.get('p4_avg_us', 0),
                'p5_us': security_metrics.get('p5_duration_us', 0),
                'chain_length': security_metrics.get('chain_length', 0),
                'chain_integrity': security_metrics.get('chain_integrity', False),
                'errors': len(security_metrics.get('errors', [])),
            }

        if sensor_stats:
            summary['sensor'] = sensor_stats

        if ems_stats:
            summary['ems'] = ems_stats

        if comm_stats:
            summary['comm'] = comm_stats

        return summary

    def get_methodology_table(self, security_metrics=None):
        """
        Return structured methodology validation data mapped to CSQ1-CSQ4.

        Designed for direct use in Q1 paper tables. Call after a representative
        run (minimum ~300 loop iterations for stable statistics).

        Parameters:
            security_metrics: Dict from SecurityManager.get_metrics()

        Returns:
            Dict with four CSQ sections, each containing measured values.
        """
        gc.collect()
        v_mean = round(self._vbus_mean_f, 3) if self._vbus_count > 0 else None
        v_std  = round(self._welford_std(self._vbus_count, self._vbus_M2), 3) \
                 if self._vbus_count > 0 else None
        v_err  = round(abs(self._vbus_mean_f - self._vbus_nominal), 3) \
                 if self._vbus_count > 0 else None
        pn_mean = round(self._pnode_mean_f, 3) if self._pnode_count > 0 else None
        pn_std  = round(self._welford_std(self._pnode_count, self._pnode_M2), 3) \
                  if self._pnode_count > 0 else None

        table = {
            'CSQ1_realtime_performance': {
                'target_sampling_hz': 3.0,
                'achieved_sampling_hz': round(self.get_sampling_rate(), 2),
                'loop_avg_us': round(self._loop_mean_f, 1) if self.loop_count > 0 else 0.0,
                'loop_std_us': self._welford_std(self.loop_count, self._loop_M2),
                'loop_min_us': self.loop_min_us or 0,
                'loop_max_us': self.loop_max_us,
                'sensor_avg_us': round(self._sensor_mean_f, 1) if self.sensor_read_count > 0 else 0.0,
                'sensor_std_us': self._welford_std(self.sensor_read_count, self._sensor_M2),
                'ems_avg_us': round(self._ems_mean_f, 1) if self.ems_inference_count > 0 else 0.0,
                'ems_std_us': self._welford_std(self.ems_inference_count, self._ems_M2),
                'v_bus_nominal_v': self._vbus_nominal,
                'v_bus_mean_v': v_mean,
                'v_bus_std_v': v_std,
                'v_bus_tracking_error_v': v_err,
                'v_bus_min_v': round(self._vbus_min, 3) if self._vbus_min is not None else None,
                'v_bus_max_v': round(self._vbus_max, 3) if self._vbus_max > 0 else None,
            },
            'CSQ2_resource_constraints': {
                'heap_snapshots': dict(self._heap_snapshots),
                'heap_drift_per_100loops': self._heap_drift_per_100_loops(),
                'heap_sample_count': len(self._heap_samples),
                'gc_mem_free': gc.mem_free(),
            },
            'CSQ3_security_overhead': {},
            'CSQ4_energy_communication': {
                'node_power_mean_w': pn_mean,
                'node_power_std_w': pn_std,
                'send_count': self.send_count,
                'send_failures': self.send_failures,
                'send_success_rate': round(
                    (self.send_count - self.send_failures) / self.send_count * 100, 1
                ) if self.send_count > 0 else 0.0,
                'send_avg_us': round(self._send_mean_f, 1) if self.send_count > 0 else 0.0,
                'send_std_us': self._welford_std(self.send_count, self._send_M2),
                'payload_avg_bytes': round(
                    sum(self._payload_sizes) / len(self._payload_sizes), 1
                ) if self._payload_sizes else None,
            },
        }

        if security_metrics:
            table['CSQ3_security_overhead'] = {
                'p2_us': security_metrics.get('p2_duration_us', 0),
                'p3_us': security_metrics.get('p3_duration_us', 0),
                'handshake_us': security_metrics.get('handshake_total_us', 0),
                'p4_avg_us': security_metrics.get('p4_avg_us', 0),
                'p4_std_us': security_metrics.get('p4_std_us', 0),
                'p5_us': security_metrics.get('p5_duration_us', 0),
                'chain_length': security_metrics.get('chain_length', 0),
            }

        return table

    def print_methodology_table(self, security_metrics=None):
        """Print structured methodology validation table for Q1 paper."""
        t = self.get_methodology_table(security_metrics)
        c1 = t['CSQ1_realtime_performance']
        c2 = t['CSQ2_resource_constraints']
        c3 = t['CSQ3_security_overhead']
        c4 = t['CSQ4_energy_communication']

        print("\n" + "=" * 60)
        print("  SMG METHODOLOGY VALIDATION — CSQ1-CSQ4")
        print("=" * 60)

        print("\n  CSQ1 — Real-Time Performance")
        print("  Sampling rate       : {:.2f} Hz  (target {:.1f} Hz)".format(
            c1['achieved_sampling_hz'], c1['target_sampling_hz']))
        print("  Loop avg/std        : {} / {} us".format(
            c1['loop_avg_us'], c1['loop_std_us']))
        print("  Loop min/max        : {} / {} us".format(
            c1['loop_min_us'], c1['loop_max_us']))
        print("  Sensor avg/std      : {} / {} us".format(
            c1['sensor_avg_us'], c1['sensor_std_us']))
        print("  EMS avg/std         : {} / {} us".format(
            c1['ems_avg_us'], c1['ems_std_us']))
        if c1['v_bus_mean_v'] is not None:
            print("  V_bus mean/std      : {:.3f} / {:.3f} V  (nominal {:.1f} V)".format(
                c1['v_bus_mean_v'], c1['v_bus_std_v'] or 0.0, c1['v_bus_nominal_v']))
            print("  V_bus tracking err  : {:.3f} V  (|mean - nominal|)".format(
                c1['v_bus_tracking_error_v'] or 0.0))

        print("\n  CSQ2 — Resource Constraints")
        for label, free in c2['heap_snapshots'].items():
            print("  Heap ({:<16}) : {} bytes".format(label, free))
        print("  Heap drift          : {} bytes / 100 loops".format(
            c2['heap_drift_per_100loops']))
        print("  Heap free (now)     : {} bytes".format(c2['gc_mem_free']))

        if c3:
            print("\n  CSQ3 — Security Overhead")
            print("  P2+P3 handshake     : {} us  ({:.1f} ms)".format(
                c3['handshake_us'], c3['handshake_us'] / 1000.0))
            print("  P4 avg/std          : {} / {} us".format(
                c3['p4_avg_us'], c3['p4_std_us']))
            print("  P5 logout           : {} us".format(c3['p5_us']))
            print("  Chain length        : {} msgs".format(c3['chain_length']))

        print("\n  CSQ4 — Energy & Communication")
        if c4['node_power_mean_w'] is not None:
            print("  Node power mean/std : {:.3f} / {:.3f} W".format(
                c4['node_power_mean_w'], c4['node_power_std_w'] or 0.0))
        print("  Send success rate   : {}%  ({}/{})".format(
            c4['send_success_rate'], c4['send_count'] - c4['send_failures'],
            c4['send_count']))
        print("  Send avg/std        : {} / {} us".format(
            c4['send_avg_us'], c4['send_std_us']))
        if c4['payload_avg_bytes'] is not None:
            print("  Payload avg bytes   : {}".format(c4['payload_avg_bytes']))

        print("=" * 60)

    def print_summary(self, security_metrics=None, sensor_stats=None, ems_stats=None, comm_stats=None):
        """Print formatted metrics summary to console."""
        s = self.get_summary(security_metrics, sensor_stats, ems_stats, comm_stats)

        print("\n" + "=" * 55)
        print("  SMG NODE METRICS SUMMARY")
        print("=" * 55)
        print("  Uptime              : {} s".format(s['uptime_s']))
        print("  Loop count          : {}".format(s['loop_count']))
        print("  Sampling rate       : {} Hz".format(s['sampling_rate_hz']))
        print("  Loop avg/std/min/max: {} / {} / {} / {} us".format(
            s['loop_avg_us'], s['loop_std_us'], s['loop_min_us'], s['loop_max_us']))
        print("  Sensor reads        : {} (avg {} ±{} us)".format(
            s['sensor_reads'], s['sensor_avg_us'], s['sensor_std_us']))
        print("  EMS inferences      : {} (avg {} ±{} us)".format(
            s['ems_inferences'], s['ems_avg_us'], s['ems_std_us']))
        print("  Secure sends        : {} (avg {} ±{} us, {}% ok)".format(
            s['send_count'], s['send_avg_us'], s['send_std_us'], s['send_success_rate']))
        if 'payload_avg_bytes' in s:
            print("  Payload size        : {} / {} / {} bytes (avg/min/max)".format(
                s['payload_avg_bytes'], s['payload_min_bytes'], s['payload_max_bytes']))
        print("  Free heap           : {} bytes".format(s['gc_mem_free']))

        if 'security' in s:
            sec = s['security']
            print("\n  --- SECURITY ---")
            print("  P2 (RoT)            : {} us".format(sec['p2_us']))
            print("  P3 (Session)        : {} us".format(sec['p3_us']))
            print("  Handshake total     : {} us".format(sec['handshake_us']))
            print("  P4 transactions     : {} (avg {} us)".format(sec['p4_count'], sec['p4_avg_us']))
            print("  P5 (Logout)         : {} us".format(sec['p5_us']))
            print("  Chain length        : {}".format(sec['chain_length']))
            print("  Chain integrity     : {}".format('OK' if sec['chain_integrity'] else 'N/A'))
            print("  Security errors     : {}".format(sec['errors']))

        if 'comm' in s:
            c = s['comm']
            print("\n  --- COMMUNICATION ---")
            print("  WiFi connected      : {}".format(c.get('wifi_connected', False)))
            print("  IP address          : {}".format(c.get('ip', 'N/A')))
            print("  Reconnects          : {}".format(c.get('reconnect_count', 0)))

        print("=" * 55)

    def to_json(self, security_metrics=None, sensor_stats=None, ems_stats=None, comm_stats=None):
        """Return metrics as JSON bytes for logging or transmission."""
        summary = self.get_summary(security_metrics, sensor_stats, ems_stats, comm_stats)
        return ujson.dumps(summary).encode()

    def save_to_file(self, path, security_metrics=None, sensor_stats=None,
                     ems_stats=None, comm_stats=None):
        """
        Persist metrics summary as JSON to ESP32 flash.

        Parameters:
            path: File path on flash (e.g. '/metrics.json').

        Returns:
            True on success, False on failure.
        """
        summary = self.get_summary(security_metrics, sensor_stats, ems_stats, comm_stats)
        try:
            with open(path, 'w') as f:
                f.write(ujson.dumps(summary))
            print("[metrics] Saved to {}".format(path))
            return True
        except Exception as e:
            print("[metrics] save_to_file failed: {}".format(e))
            return False

    def export_csv(self, path, security_metrics=None, sensor_stats=None,
                   ems_stats=None, comm_stats=None):
        """
        Export key metrics as CSV for paper measurement tables.

        Format: metric,value,unit — one row per metric, header on first line.

        Parameters:
            path: File path on flash (e.g. '/metrics.csv').

        Returns:
            True on success, False on failure.
        """
        s = self.get_summary(security_metrics, sensor_stats, ems_stats, comm_stats)
        rows = [
            ("metric", "value", "unit"),
            ("uptime_s",          s['uptime_s'],          "s"),
            ("sampling_rate_hz",  s['sampling_rate_hz'],  "Hz"),
            ("loop_avg_us",       s['loop_avg_us'],       "us"),
            ("loop_std_us",       s['loop_std_us'],       "us"),
            ("loop_min_us",       s['loop_min_us'],       "us"),
            ("loop_max_us",       s['loop_max_us'],       "us"),
            ("sensor_avg_us",     s['sensor_avg_us'],     "us"),
            ("sensor_std_us",     s['sensor_std_us'],     "us"),
            ("ems_avg_us",        s['ems_avg_us'],        "us"),
            ("ems_std_us",        s['ems_std_us'],        "us"),
            ("send_avg_us",       s['send_avg_us'],       "us"),
            ("send_std_us",       s['send_std_us'],       "us"),
            ("send_success_rate", s['send_success_rate'], "%"),
            ("gc_mem_free",       s['gc_mem_free'],       "bytes"),
        ]
        if 'payload_avg_bytes' in s:
            rows.append(("payload_avg_bytes", s['payload_avg_bytes'], "bytes"))

        if 'security' in s:
            sec = s['security']
            rows += [
                ("p2_us",          sec['p2_us'],          "us"),
                ("p3_us",          sec['p3_us'],          "us"),
                ("handshake_us",   sec['handshake_us'],   "us"),
                ("p4_avg_us",      sec['p4_avg_us'],      "us"),
                ("p5_us",          sec['p5_us'],          "us"),
                ("chain_length",   sec['chain_length'],   "msgs"),
                ("security_errors",sec['errors'],         "count"),
            ]

        # V_bus regulation quality
        if self._vbus_count > 0:
            rows += [
                ("v_bus_mean",          round(self._vbus_mean_f, 3), "V"),
                ("v_bus_std",           self._welford_std(self._vbus_count, self._vbus_M2), "V"),
                ("v_bus_tracking_err",  round(abs(self._vbus_mean_f - self._vbus_nominal), 3), "V"),
            ]

        # Node power
        if self._pnode_count > 0:
            rows += [
                ("node_power_mean", round(self._pnode_mean_f, 3), "W"),
                ("node_power_std",  self._welford_std(self._pnode_count, self._pnode_M2), "W"),
            ]

        # Heap stability
        rows.append(("heap_drift_per_100loops", self._heap_drift_per_100_loops(), "bytes"))

        try:
            with open(path, 'w') as f:
                for row in rows:
                    f.write("{},{},{}\n".format(row[0], row[1], row[2]))
            print("[metrics] CSV exported to {}".format(path))
            return True
        except Exception as e:
            print("[metrics] export_csv failed: {}".format(e))
            return False

    def export_methodology_csv(self, path, security_metrics=None):
        """
        Export the CSQ1-CSQ4 methodology table as CSV for paper tables.

        Format: csq,metric,value,unit — ready for import into LaTeX or spreadsheet.

        Parameters:
            path: File path on flash (e.g. '/methodology.csv').

        Returns:
            True on success, False on failure.
        """
        t = self.get_methodology_table(security_metrics)
        rows = [("csq", "metric", "value", "unit")]

        c1 = t['CSQ1_realtime_performance']
        rows += [
            ("CSQ1", "achieved_sampling_hz",  c1['achieved_sampling_hz'],  "Hz"),
            ("CSQ1", "loop_avg_us",            c1['loop_avg_us'],           "us"),
            ("CSQ1", "loop_std_us",            c1['loop_std_us'],           "us"),
            ("CSQ1", "loop_min_us",            c1['loop_min_us'],           "us"),
            ("CSQ1", "loop_max_us",            c1['loop_max_us'],           "us"),
            ("CSQ1", "sensor_avg_us",          c1['sensor_avg_us'],         "us"),
            ("CSQ1", "sensor_std_us",          c1['sensor_std_us'],         "us"),
            ("CSQ1", "ems_avg_us",             c1['ems_avg_us'],            "us"),
            ("CSQ1", "ems_std_us",             c1['ems_std_us'],            "us"),
        ]
        if c1['v_bus_mean_v'] is not None:
            rows += [
                ("CSQ1", "v_bus_mean_v",           c1['v_bus_mean_v'],          "V"),
                ("CSQ1", "v_bus_std_v",            c1['v_bus_std_v'],           "V"),
                ("CSQ1", "v_bus_tracking_error_v", c1['v_bus_tracking_error_v'], "V"),
            ]

        c2 = t['CSQ2_resource_constraints']
        for label, free in c2['heap_snapshots'].items():
            rows.append(("CSQ2", "heap_" + label, free, "bytes"))
        rows += [
            ("CSQ2", "heap_drift_per_100loops",  c2['heap_drift_per_100loops'], "bytes"),
            ("CSQ2", "gc_mem_free",              c2['gc_mem_free'],             "bytes"),
        ]

        c3 = t['CSQ3_security_overhead']
        if c3:
            rows += [
                ("CSQ3", "p2_us",         c3['p2_us'],         "us"),
                ("CSQ3", "p3_us",         c3['p3_us'],         "us"),
                ("CSQ3", "handshake_us",  c3['handshake_us'],  "us"),
                ("CSQ3", "p4_avg_us",     c3['p4_avg_us'],     "us"),
                ("CSQ3", "p4_std_us",     c3['p4_std_us'],     "us"),
                ("CSQ3", "p5_us",         c3['p5_us'],         "us"),
                ("CSQ3", "chain_length",  c3['chain_length'],  "msgs"),
            ]

        c4 = t['CSQ4_energy_communication']
        if c4['node_power_mean_w'] is not None:
            rows += [
                ("CSQ4", "node_power_mean_w", c4['node_power_mean_w'], "W"),
                ("CSQ4", "node_power_std_w",  c4['node_power_std_w'],  "W"),
            ]
        rows += [
            ("CSQ4", "send_success_rate",  c4['send_success_rate'],  "%"),
            ("CSQ4", "send_avg_us",        c4['send_avg_us'],        "us"),
            ("CSQ4", "send_std_us",        c4['send_std_us'],        "us"),
        ]
        if c4['payload_avg_bytes'] is not None:
            rows.append(("CSQ4", "payload_avg_bytes", c4['payload_avg_bytes'], "bytes"))

        try:
            with open(path, 'w') as f:
                for row in rows:
                    f.write("{},{},{},{}\n".format(row[0], row[1], row[2], row[3]))
            print("[metrics] Methodology CSV exported to {}".format(path))
            return True
        except Exception as e:
            print("[metrics] export_methodology_csv failed: {}".format(e))
            return False
