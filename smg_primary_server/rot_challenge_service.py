"""
SMG Primary Server -- PUF challenge-response Root of Trust responder.

Server-side counterpart of the C/ESP-IDF firmware's rot_session.c
(smg_puf_rot_node/main/rot_session.c) and the paper's "Root of Trust
Establishment" section / STRIDE-to-Protocol tables (3-phase design:
P1 Enrollment, P2 Root of Trust challenge-response, P3 AES-256-GCM data
exchange -- the SecureNode v3 P2-P5 hash-chain protocol in auth_service.py
is retired for new deployments; that module is left untouched here since
CSQ3's bench-trial data in the paper still describes it historically).

Wire format (must match net_msg.c exactly): 4-byte big-endian payload
length, followed by that many bytes of UTF-8 JSON. All binary fields are
lowercase-hex strings in JSON, matching rot_session.c's bin_to_hex/hex_to_bin.

Handshake (mirrors rot_session.c's rot_session_handshake):
  1. Device -> Server: {"type":"hello","id_a":hex16,"challenge_a":hex16}
  2. Server -> Device: {"type":"challenge","id_b":hex16,"challenge_b":hex16,
                         "server_proof":hex32}
       server_proof = SHA256(ShS_B || challenge_a)
       ShS_B is the server's own conventional (non-PUF) secret, pinned into
       every device's NVS at provisioning (see tools/provision_server_ref.py
       in smg_puf_rot_node) and loaded here from puf_server_identity.json.
  3. Device -> Server: {"type":"response","device_proof":hex32}
       device_proof = SHA256(ShS_A || challenge_b), where ShS_A = SHA256(PUF_A)
       was captured once at enrollment and stored server-side as shs_a_ref in
       the node registry (puf_node_registry.json) -- never the raw PUF
       response itself (analogous to storing a password hash, not the
       password; see paper's Root of Trust Establishment / Limitations).
  4. Server -> Device: {"type":"session_ack"} (or {"type":"error",...})

  Key_ab = SHA256(ShS_A || ShS_B || challenge_a || challenge_b)
  (both sides derive this independently; never transmitted)

Data phase (P3, mirrors secure_channel.c's demo helper):
  Device -> Server: {"type":"data","nonce":hex24,"ciphertext":hex,"tag":hex32}
  AES-256-GCM under Key_ab. No hash-chain / audit phase (AEAD already
  authenticates each message) -- see paper Limitations on the resulting
  Repudiation/I8 coverage trade-off.
"""
import hashlib
import json
import logging
import os
import secrets
import socket
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # matches crypto_utils.py

log = logging.getLogger(__name__)

ROT_ID_LEN = 16
ROT_DIGEST_LEN = 32
CHALLENGE_LEN = 16
GCM_NONCE_LEN = 12
GCM_TAG_LEN = 16

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_SERVER_IDENTITY_PATH = os.path.join(HERE, "puf_server_identity.json")
DEFAULT_NODE_REGISTRY_PATH = os.path.join(HERE, "puf_node_registry.json")


class ProtocolError(Exception):
    pass


# ---------------------------------------------------------------------------
# Wire framing -- must match net_msg.c bit-for-bit (independent of protocol.py,
# which serves the retired SecureNode protocol's different field-name set).
# ---------------------------------------------------------------------------

def send_json(sock: socket.socket, obj: dict) -> None:
    body = json.dumps(obj).encode("utf-8")
    sock.sendall(struct.pack(">I", len(body)) + body)


def recv_json(sock: socket.socket, timeout: float = 10.0) -> dict:
    sock.settimeout(timeout)
    hdr = _recv_exact(sock, 4)
    (length,) = struct.unpack(">I", hdr)
    if length == 0 or length > 65536:
        raise ProtocolError(f"invalid message length: {length}")
    body = _recv_exact(sock, length)
    return json.loads(body.decode("utf-8"))


def _recv_exact(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("connection closed during recv")
        buf += chunk
    return buf


def hex_to_bin(s: str, expected_len: int) -> bytes:
    b = bytes.fromhex(s)
    if len(b) != expected_len:
        raise ProtocolError(f"expected {expected_len} bytes, got {len(b)}")
    return b


# ---------------------------------------------------------------------------
# Identity / registry loading
# ---------------------------------------------------------------------------

def load_server_identity(path: str = DEFAULT_SERVER_IDENTITY_PATH) -> tuple[bytes, bytes]:
    """Returns (id_b, shs_b) as raw bytes. Raises FileNotFoundError if the
    device-side provisioning tool (provision_server_ref.py) has not been run yet."""
    with open(path) as f:
        data = json.load(f)
    return hex_to_bin(data["id_b"], ROT_ID_LEN), hex_to_bin(data["shs_b"], ROT_DIGEST_LEN)


def load_node_registry(path: str = DEFAULT_NODE_REGISTRY_PATH) -> dict:
    """Returns {id_a_hex: {"shs_a_ref": hex64, "label": str}}. Missing file is
    treated as an empty registry (no enrolled devices known yet)."""
    if not os.path.isfile(path):
        return {}
    with open(path) as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# AES-256-GCM data-phase helpers (matches secure_channel.c's wire format)
# ---------------------------------------------------------------------------

def decrypt_data_message(msg: dict, key_ab: bytes) -> bytes:
    """Wire format: nonce/ciphertext/tag as SEPARATE hex fields (matches
    secure_channel.c's out_nonce/out_ciphertext/out_tag, unlike crypto_utils.py's
    combined nonce||ct||tag layout used by the retired SecureNode protocol)."""
    nonce = hex_to_bin(msg["nonce"], GCM_NONCE_LEN)
    ciphertext = bytes.fromhex(msg["ciphertext"])
    tag = hex_to_bin(msg["tag"], GCM_TAG_LEN)
    aesgcm = AESGCM(key_ab)
    return aesgcm.decrypt(nonce, ciphertext + tag, None)


def encrypt_data_message(plaintext: bytes, key_ab: bytes) -> dict:
    nonce = secrets.token_bytes(GCM_NONCE_LEN)
    aesgcm = AESGCM(key_ab)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    ciphertext, tag = ct_and_tag[:-GCM_TAG_LEN], ct_and_tag[-GCM_TAG_LEN:]
    return {"type": "data", "nonce": nonce.hex(), "ciphertext": ciphertext.hex(), "tag": tag.hex()}


# ---------------------------------------------------------------------------
# RotSession -- one per connected device, mirrors rot_session.c step for step
# ---------------------------------------------------------------------------

class RotSession:
    def __init__(self, conn: socket.socket, addr, id_b: bytes, shs_b: bytes,
                 registry: dict):
        self.conn = conn
        self.addr = addr
        self.id_b = id_b
        self.shs_b = shs_b
        self.registry = registry
        self.key_ab: bytes | None = None
        self.node_label = "unknown"
        self.authenticated = False

    def run(self) -> bool:
        """Runs the P2 handshake; on success, enters the P3 demo data loop until
        the peer disconnects. Returns True iff the handshake succeeded."""
        try:
            if not self._handshake():
                return False
            self._data_loop()
            return True
        except (ConnectionError, ProtocolError) as e:
            log.info("[%s] session ended: %s", self.addr, e)
            return self.authenticated
        except OSError as e:
            log.info("[%s] socket error: %s", self.addr, e)
            return self.authenticated

    # -- P2: challenge-response handshake --------------------------------

    def _handshake(self) -> bool:
        hello = recv_json(self.conn)
        if hello.get("type") != "hello":
            self._send_error(f"expected hello, got: {hello.get('type')}")
            return False

        id_a = hex_to_bin(hello["id_a"], ROT_ID_LEN)
        challenge_a = hex_to_bin(hello["challenge_a"], CHALLENGE_LEN)

        entry = self.registry.get(id_a.hex())
        if entry is None:
            log.warning("[%s] unknown device id_a=%s", self.addr, id_a.hex())
            self._send_error("unknown device id_a")
            return False
        shs_a_ref = hex_to_bin(entry["shs_a_ref"], ROT_DIGEST_LEN)
        self.node_label = entry.get("label", id_a.hex()[:8])

        challenge_b = secrets.token_bytes(CHALLENGE_LEN)
        server_proof = hashlib.sha256(self.shs_b + challenge_a).digest()

        send_json(self.conn, {
            "type": "challenge",
            "id_b": self.id_b.hex(),
            "challenge_b": challenge_b.hex(),
            "server_proof": server_proof.hex(),
        })

        resp = recv_json(self.conn)
        if resp.get("type") != "response":
            self._send_error(f"expected response, got: {resp.get('type')}")
            return False
        device_proof = hex_to_bin(resp["device_proof"], ROT_DIGEST_LEN)

        expected_device_proof = hashlib.sha256(shs_a_ref + challenge_b).digest()
        if not secrets.compare_digest(device_proof, expected_device_proof):
            log.warning("[%s] device authentication FAILED (proof mismatch) node=%s",
                        self.addr, self.node_label)
            self._send_error("device authentication failed")
            return False

        self.key_ab = hashlib.sha256(shs_a_ref + self.shs_b + challenge_a + challenge_b).digest()
        self.authenticated = True
        send_json(self.conn, {"type": "session_ack"})
        log.info("[%s] handshake OK, node=%s authenticated", self.addr, self.node_label)
        return True

    # -- P3: AEAD data phase (demo scope, no chain/audit) -----------------

    def _data_loop(self) -> None:
        while True:
            try:
                msg = recv_json(self.conn, timeout=30.0)
            except (ConnectionError, socket.timeout, TimeoutError):
                return
            if msg.get("type") != "data":
                log.warning("[%s] unexpected message type in data phase: %s",
                            self.addr, msg.get("type"))
                continue
            try:
                plaintext = decrypt_data_message(msg, self.key_ab)
            except Exception as e:
                log.warning("[%s] P3 GCM verification failed: %s", self.addr, e)
                self._send_error("GCM verification failed")
                continue
            log.info("[%s] node=%s P3 payload: %r", self.addr, self.node_label, plaintext)

    def _send_error(self, message: str) -> None:
        try:
            send_json(self.conn, {"type": "error", "error": message})
        except Exception:
            pass
