"""
SMG Primary Server — Main TCP Server.

Listens for TCP connections from secondary nodes on port 5000.
Each connection is handled in a dedicated thread running the
SecureNode v3 responder protocol (P2–P5).

Usage:
    python server.py [--config config.json] [--registry node_registry.json]

The server also runs a background thread that periodically updates
EMS demand setpoints based on aggregate sensor data.
"""

import argparse
import binascii
import json
import logging
import os
import socket
import sys
import threading
import time

from auth_service import NodeSession
from data_store import DataStore
from ems_coordinator import EMSCoordinator
from crypto_utils import h_sha256

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("smg_server")


def load_config(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def load_registry(path: str) -> dict:
    """
    Load node registry. Returns:
        {node_id_hex: {"node_key": hex, "node_salt": hex, "label": str}}
    """
    with open(path) as f:
        return json.load(f)


def load_or_generate_server_identity(config: dict) -> tuple[bytes, bytes]:
    """Load or generate the server's ID and master key."""
    id_hex = config.get("server_id")
    key_hex = config.get("server_key")

    if id_hex and key_hex:
        return binascii.unhexlify(id_hex), binascii.unhexlify(key_hex)

    # Auto-generate and print (operator must save these to config.json)
    server_id = os.urandom(16)
    server_key = os.urandom(32)
    log.warning("No server_id/server_key in config — generating ephemeral identity.")
    log.warning("Add to config.json: server_id=%s  server_key=%s",
                server_id.hex(), server_key.hex())
    return server_id, server_key


class SMGServer:
    """Primary node TCP server."""

    def __init__(self, config: dict, registry: dict):
        self.host = config.get("server_ip", "0.0.0.0")
        self.port = config.get("server_port", 5000)
        self.registry = registry
        self.server_id, self.server_key = load_or_generate_server_identity(config)
        self.data_store = DataStore()
        self.ems = EMSCoordinator(config)
        self._sessions: list[NodeSession] = []
        self._lock = threading.Lock()
        self._running = False

        log.info("Server ID: %s", self.server_id.hex())
        log.info("Registry: %d nodes", len(self.registry))

    def _handle_client(self, conn: socket.socket, addr: tuple) -> None:
        session = NodeSession(
            conn=conn,
            addr=addr,
            registry=self.registry,
            server_id=self.server_id,
            server_key=self.server_key,
            data_store=self.data_store,
            ems=self.ems,
        )
        with self._lock:
            self._sessions.append(session)
        try:
            session.run()
        finally:
            conn.close()
            with self._lock:
                self._sessions.remove(session)

    def _ems_update_loop(self) -> None:
        interval = 5.0
        while self._running:
            time.sleep(interval)
            try:
                self.ems.update(self.data_store)
            except Exception as e:
                log.error("EMS update error: %s", e)

    def _status_loop(self) -> None:
        while self._running:
            time.sleep(30)
            nodes = self.data_store.get_all_nodes()
            if not nodes:
                continue
            agg = self.data_store.aggregate_power()
            log.info("Status: %d nodes connected | total P_gen=%.1fW P_bus=%.1fW",
                     len(nodes), agg['total_P_gen'], agg['total_P_bus'])
            for node in nodes:
                count = self.data_store.get_record_count(node)
                sp = self.ems.get_setpoint(node)
                log.info("  %s: %d readings | setpoint=%.1fW",
                         node, count, sp if sp else 0.0)

    def run(self) -> None:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind((self.host, self.port))
        srv.listen(10)
        self._running = True
        log.info("Listening on %s:%d", self.host, self.port)

        threading.Thread(target=self._ems_update_loop, daemon=True).start()
        threading.Thread(target=self._status_loop, daemon=True).start()

        try:
            while True:
                conn, addr = srv.accept()
                log.info("Incoming connection from %s:%d", *addr)
                t = threading.Thread(
                    target=self._handle_client, args=(conn, addr), daemon=True)
                t.start()
        except KeyboardInterrupt:
            log.info("Shutting down...")
        finally:
            self._running = False
            srv.close()
            self._print_summary()

    def _print_summary(self) -> None:
        print("\n" + "=" * 60)
        print("  SMG PRIMARY SERVER — SESSION SUMMARY")
        print("=" * 60)
        print("  Data store:", self.data_store.summary())
        print("  EMS state: ", self.ems.summary())
        print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="SMG Primary Server")
    parser.add_argument("--config", default="config.json",
                        help="Server configuration file (default: config.json)")
    parser.add_argument("--registry", default="node_registry.json",
                        help="Node registry file (default: node_registry.json)")
    parser.add_argument("--debug", action="store_true",
                        help="Enable DEBUG logging")
    args = parser.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        config = load_config(args.config)
    except FileNotFoundError:
        log.error("Config file not found: %s", args.config)
        sys.exit(1)

    try:
        registry = load_registry(args.registry)
    except FileNotFoundError:
        log.error("Registry file not found: %s", args.registry)
        sys.exit(1)

    # Filter out metadata keys (those starting with "_") to count actual node entries
    node_count = sum(1 for k in registry if not k.startswith("_"))
    if node_count == 0:
        log.warning("Node registry is empty — no secondary nodes registered.")
        log.warning("Run tools/provision_node.py and add entries to %s", args.registry)
    else:
        log.info("Loaded %d node(s) from registry", node_count)

    server = SMGServer(config, registry)
    server.run()


if __name__ == "__main__":
    main()
