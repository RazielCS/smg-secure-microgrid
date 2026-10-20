"""
SMG Primary Server — Sensor Data Store.

Stores incoming sensor readings from all secondary nodes.
Provides in-memory ring buffer + optional JSON file export.
Thread-safe via threading.Lock.
"""

import json
import logging
import threading
import time
from collections import deque

log = logging.getLogger(__name__)

_MAX_READINGS_PER_NODE = 10800  # 3 Hz × 3600 s = 1 hour of readings


class DataStore:
    """
    Thread-safe sensor reading store.

    Each node maintains a ring buffer of its most recent readings.
    Supports per-node query and cross-node aggregation for the EMS.
    """

    def __init__(self, max_readings: int = _MAX_READINGS_PER_NODE):
        self._lock = threading.Lock()
        self._max = max_readings
        self._buffers: dict[str, deque] = {}
        self._latest: dict[str, dict] = {}
        self._record_counts: dict[str, int] = {}

    def record(self, node_label: str, reading: dict) -> None:
        """Store a sensor reading from a node."""
        reading.setdefault('server_ts', time.time())
        with self._lock:
            if node_label not in self._buffers:
                self._buffers[node_label] = deque(maxlen=self._max)
                self._record_counts[node_label] = 0
            self._buffers[node_label].append(reading)
            self._latest[node_label] = reading
            self._record_counts[node_label] += 1

    def get_latest(self, node_label: str) -> dict | None:
        """Return the most recent reading from a node, or None."""
        with self._lock:
            return self._latest.get(node_label)

    def get_all_latest(self) -> dict[str, dict]:
        """Return the most recent reading from each node."""
        with self._lock:
            return dict(self._latest)

    def get_recent(self, node_label: str, n: int = 10) -> list[dict]:
        """Return the n most recent readings from a node."""
        with self._lock:
            buf = self._buffers.get(node_label)
            if buf is None:
                return []
            items = list(buf)
            return items[-n:] if len(items) > n else items

    def get_record_count(self, node_label: str) -> int:
        with self._lock:
            return self._record_counts.get(node_label, 0)

    def get_all_nodes(self) -> list[str]:
        with self._lock:
            return list(self._buffers.keys())

    def aggregate_power(self) -> dict:
        """Compute total bus power across all nodes (for EMS)."""
        with self._lock:
            total_p_bus = 0.0
            total_p_gen = 0.0
            node_count = 0
            for reading in self._latest.values():
                total_p_bus += reading.get('P_bus', 0.0)
                total_p_gen += reading.get('P_gen', 0.0)
                node_count += 1
        return {
            'total_P_bus': round(total_p_bus, 2),
            'total_P_gen': round(total_p_gen, 2),
            'node_count': node_count,
            'ts': time.time(),
        }

    def export_json(self, path: str, node_label: str = None) -> None:
        """Export readings to a JSON file for case study analysis."""
        with self._lock:
            if node_label:
                data = {node_label: list(self._buffers.get(node_label, []))}
            else:
                data = {k: list(v) for k, v in self._buffers.items()}
        with open(path, 'w') as f:
            json.dump(data, f, indent=2)
        log.info("Exported %d node(s) to %s", len(data), path)

    def get_median_v_bus(self, exclude_node: str = None) -> float | None:
        """
        Return the median V_bus across all nodes except exclude_node.
        Requires at least 2 other nodes' latest readings to be meaningful.
        Used by auth_service for cross-node sensor anomaly detection.
        """
        with self._lock:
            values = [
                r.get('V_bus')
                for node, r in self._latest.items()
                if node != exclude_node and r.get('V_bus') is not None
            ]
        if len(values) < 2:
            return None
        values.sort()
        n = len(values)
        if n % 2 == 0:
            return (values[n // 2 - 1] + values[n // 2]) / 2.0
        return float(values[n // 2])

    def summary(self) -> dict:
        with self._lock:
            return {
                node: {
                    'count': self._record_counts.get(node, 0),
                    'latest_ts': self._latest[node].get('ts_ms') if node in self._latest else None,
                }
                for node in self._buffers
            }
