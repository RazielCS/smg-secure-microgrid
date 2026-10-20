"""
SMG SecureNode protocol message framing — CPython version.

Matches the send_msg / recv_msg / pack_payload / unpack_payload
functions in secure_node.py so the server can communicate with
MicroPython secondary nodes without modification.

Wire format: 4-byte big-endian length || JSON body
Binary fields are hex-encoded strings in JSON (BYTES_FIELDS set).
"""

import json
import binascii
import socket
import struct

BYTES_FIELDS = {
    "A_GEN_bl", "B_GEN_bl", "ID_A", "ID_B", "ShS_A", "ShS_B",
    "INIT_cif", "SES_cif", "SES_id", "MSG_cif", "RES_cif",
    "A_END_cif", "B_END_cif", "VERIF_cif", "RESP_cif",
    "CONF_cif",
}


def pack_payload(payload: dict) -> bytes:
    """Serialize dict to length-prefixed JSON, hex-encoding bytes values."""
    serialized = {}
    for k, v in payload.items():
        if isinstance(v, bytes):
            serialized[k] = binascii.hexlify(v).decode()
        else:
            serialized[k] = v
    body = json.dumps(serialized).encode()
    return len(body).to_bytes(4, 'big') + body


def unpack_payload(raw: bytes) -> dict:
    """Deserialize JSON body, converting BYTES_FIELDS from hex back to bytes."""
    d = json.loads(raw.decode())
    result = {}
    for k, v in d.items():
        if k in BYTES_FIELDS and isinstance(v, str):
            try:
                result[k] = binascii.unhexlify(v)
            except Exception:
                result[k] = v
        else:
            result[k] = v
    return result


def send_msg(sock: socket.socket, payload: dict) -> None:
    """Send a protocol message over a connected socket."""
    data = pack_payload(payload)
    sock.sendall(data)


def recv_msg(sock: socket.socket, timeout: float = 10.0) -> dict:
    """Receive a protocol message from a connected socket."""
    sock.settimeout(timeout)
    hdr = b''
    while len(hdr) < 4:
        chunk = sock.recv(4 - len(hdr))
        if not chunk:
            raise ConnectionError("Connection closed during header recv")
        hdr += chunk
    msg_len = int.from_bytes(hdr, 'big')
    if msg_len == 0 or msg_len > 65536:
        raise ValueError("Invalid message length: {}".format(msg_len))
    body = b''
    while len(body) < msg_len:
        chunk = sock.recv(msg_len - len(body))
        if not chunk:
            raise ConnectionError("Connection closed during body recv")
        body += chunk
    return unpack_payload(body)
