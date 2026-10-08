"""
SMG Primary Server -- PUF challenge-response protocol runner.

Entry point for the Root of Trust protocol (rot_challenge_service.py): PUF-seeded
asymmetric device authentication and per-node symmetric server authentication.

Prerequisites:
    1. Run smg_puf_rot_node/tools/provision_server_ref.py once to create
       puf_server_identity.json (id_b).
    2. Register each enrolled device with tools/register_puf_node.py once its
       pk_a has been captured from hardware (requires a connected ESP32).

Usage:
    python puf_server.py [--host 0.0.0.0] [--port 5001]
                          [--server-identity puf_server_identity.json]
                          [--registry puf_node_registry.json]
"""
import argparse
import logging
import socket
import sys
import threading

from rot_challenge_service import (
    RotSession, load_server_identity, load_node_registry,
    DEFAULT_SERVER_IDENTITY_PATH, DEFAULT_NODE_REGISTRY_PATH,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("puf_server")


def handle_client(conn, addr, id_b, shs_b, registry):
    session = RotSession(conn, addr, id_b, shs_b, registry)
    try:
        session.run()
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=5001,
                     help="Separate default port from server.py's 5000 -- the two "
                          "protocols are not wire-compatible and must not share a port")
    ap.add_argument("--server-identity", default=DEFAULT_SERVER_IDENTITY_PATH)
    ap.add_argument("--registry", default=DEFAULT_NODE_REGISTRY_PATH)
    args = ap.parse_args()

    try:
        id_b, shs_b = load_server_identity(args.server_identity)
    except FileNotFoundError:
        log.error("Server identity not found: %s", args.server_identity)
        log.error("Run smg_puf_rot_node/tools/provision_server_ref.py first.")
        sys.exit(1)

    registry = load_node_registry(args.registry)
    if not registry:
        log.warning("Node registry is empty (%s) -- no device will be able to "
                     "authenticate until entries are added with "
                     "tools/register_puf_node.py.", args.registry)
    else:
        log.info("Loaded %d node(s) from registry", len(registry))

    log.info("Server ID (id_b): %s", id_b.hex())

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.host, args.port))
    srv.listen(10)
    log.info("Listening on %s:%d", args.host, args.port)

    try:
        while True:
            conn, addr = srv.accept()
            log.info("Incoming connection from %s:%d", *addr)
            t = threading.Thread(target=handle_client, args=(conn, addr, id_b, shs_b, registry),
                                  daemon=True)
            t.start()
    except KeyboardInterrupt:
        log.info("Shutting down...")
    finally:
        srv.close()


if __name__ == "__main__":
    main()
