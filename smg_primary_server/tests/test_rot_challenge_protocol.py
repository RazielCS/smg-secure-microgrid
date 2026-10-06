"""
Host-only tests for rot_challenge_service.py -- simulates the C firmware's
rot_session.c handshake logic in Python (FakeDevice) talking to the real
RotSession server implementation over a loopback TCP socket pair. No ESP32
hardware involved; this verifies protocol-level interoperability (field
names, hex framing, proof formulas, derived Key_ab) against the actual
server code, not just the server's internal consistency.
"""
import hashlib
import secrets
import socket
import threading

import pytest

from rot_challenge_service import (
    RotSession, send_json, recv_json, encrypt_data_message, decrypt_data_message,
    ROT_ID_LEN, ROT_DIGEST_LEN, CHALLENGE_LEN,
)


def make_loopback_pair():
    """Returns (device_sock, server_sock) connected over localhost."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    device_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    device_sock.connect(("127.0.0.1", port))
    server_sock, _ = listener.accept()
    listener.close()
    return device_sock, server_sock


class FakeDevice:
    """Replicates rot_session.c's client-side handshake math exactly
    (see smg_puf_rot_node/main/rot_session.c), so a passing test proves
    wire/format/formula compatibility with the real firmware, not just
    with itself."""

    def __init__(self, sock, id_a: bytes, shs_a: bytes, id_b_expected: bytes):
        self.sock = sock
        self.id_a = id_a
        self.shs_a = shs_a
        self.id_b_expected = id_b_expected
        self.challenge_a = secrets.token_bytes(CHALLENGE_LEN)
        self.key_ab = None

    def handshake(self):
        send_json(self.sock, {
            "type": "hello",
            "id_a": self.id_a.hex(),
            "challenge_a": self.challenge_a.hex(),
        })

        msg = recv_json(self.sock)
        if msg.get("type") != "challenge":
            raise AssertionError(f"expected challenge, got {msg}")
        id_b = bytes.fromhex(msg["id_b"])
        challenge_b = bytes.fromhex(msg["challenge_b"])
        server_proof = bytes.fromhex(msg["server_proof"])

        assert id_b == self.id_b_expected, "server id_b mismatch"

        # Device does NOT know shs_b -- it can only verify server_proof if it
        # already has a pinned shs_b reference. The test passes it in via the
        # constructor's closure (see test functions below) to mirror
        # rot_identity_load_server_ref().
        expected_server_proof = hashlib.sha256(self._shs_b_ref + self.challenge_a).digest()
        if not secrets.compare_digest(server_proof, expected_server_proof):
            send_json(self.sock, {"type": "error", "error": "server auth failed"})
            raise AssertionError("server_proof did not verify")

        device_proof = hashlib.sha256(self.shs_a + challenge_b).digest()
        send_json(self.sock, {"type": "response", "device_proof": device_proof.hex()})

        ack = recv_json(self.sock)
        if ack.get("type") != "session_ack":
            raise AssertionError(f"handshake rejected: {ack}")

        self.key_ab = hashlib.sha256(
            self.shs_a + self._shs_b_ref + self.challenge_a + challenge_b).digest()
        return self.key_ab

    def with_server_ref(self, shs_b_ref: bytes):
        self._shs_b_ref = shs_b_ref
        return self


@pytest.fixture
def identities():
    id_a = secrets.token_bytes(ROT_ID_LEN)
    puf_a = secrets.token_bytes(48)  # arbitrary-length "PUF response"
    shs_a = hashlib.sha256(puf_a).digest()  # ShS_A = SHA256(PUF_A), per rot_identity.c
    id_b = secrets.token_bytes(ROT_ID_LEN)
    shs_b = secrets.token_bytes(ROT_DIGEST_LEN)
    registry = {id_a.hex(): {"shs_a_ref": shs_a.hex(), "label": "TEST_NODE"}}
    return dict(id_a=id_a, shs_a=shs_a, id_b=id_b, shs_b=shs_b, registry=registry)


def run_server_in_thread(server_sock, addr, id_b, shs_b, registry, results):
    session = RotSession(server_sock, addr, id_b, shs_b, registry)
    results["ok"] = session.run()
    results["session"] = session


def test_successful_handshake_derives_matching_key(identities):
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"], identities["shs_b"],
        identities["registry"], results))
    t.start()

    device = FakeDevice(device_sock, identities["id_a"], identities["shs_a"],
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    device_key = device.handshake()
    device_sock.close()
    t.join(timeout=5)

    assert results["ok"] is True
    assert results["session"].authenticated is True
    assert results["session"].key_ab == device_key
    assert results["session"].node_label == "TEST_NODE"


def test_unknown_device_id_is_rejected(identities):
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"], identities["shs_b"],
        identities["registry"], results))
    t.start()

    stranger_id_a = secrets.token_bytes(ROT_ID_LEN)
    device = FakeDevice(device_sock, stranger_id_a, identities["shs_a"],
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    try:
        device.handshake()
        raised = False
    except AssertionError:
        raised = True
    device_sock.close()
    t.join(timeout=5)

    assert raised, "handshake should have been rejected for an unregistered id_a"
    assert results["ok"] is False
    assert results["session"].authenticated is False


def test_wrong_puf_response_is_rejected(identities):
    """Simulates an impostor device that knows a valid id_a but not the real
    PUF-derived shs_a (e.g. cloned ID without the physical chip)."""
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"], identities["shs_b"],
        identities["registry"], results))
    t.start()

    wrong_shs_a = secrets.token_bytes(ROT_DIGEST_LEN)
    device = FakeDevice(device_sock, identities["id_a"], wrong_shs_a,
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    try:
        device.handshake()
        raised = False
    except AssertionError:
        raised = True
    device_sock.close()
    t.join(timeout=5)

    assert raised, "handshake should have been rejected for a wrong PUF-derived secret"
    assert results["ok"] is False


def test_data_phase_round_trip_after_handshake(identities):
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"], identities["shs_b"],
        identities["registry"], results))
    t.start()

    device = FakeDevice(device_sock, identities["id_a"], identities["shs_a"],
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    key_ab = device.handshake()

    payload = b'{"V_bus": 13.6, "node": "TEST_NODE"}'
    msg = encrypt_data_message(payload, key_ab)
    send_json(device_sock, msg)
    device_sock.close()
    t.join(timeout=5)

    assert results["ok"] is True  # server loop exits cleanly on disconnect


def test_encrypt_decrypt_round_trip_matches_wire_format():
    """Unit-level check of the AES-GCM helper pair, independent of sockets."""
    key = secrets.token_bytes(32)
    plaintext = b"hello SMG"
    msg = encrypt_data_message(plaintext, key)
    assert set(msg.keys()) == {"type", "nonce", "ciphertext", "tag"}
    assert msg["type"] == "data"
    assert len(bytes.fromhex(msg["nonce"])) == 12
    assert len(bytes.fromhex(msg["tag"])) == 16
    recovered = decrypt_data_message(msg, key)
    assert recovered == plaintext


def test_decrypt_rejects_tampered_ciphertext():
    key = secrets.token_bytes(32)
    msg = encrypt_data_message(b"original", key)
    tampered = bytes.fromhex(msg["ciphertext"])
    tampered = bytes([tampered[0] ^ 0xFF]) + tampered[1:]
    msg["ciphertext"] = tampered.hex()
    with pytest.raises(Exception):
        decrypt_data_message(msg, key)
