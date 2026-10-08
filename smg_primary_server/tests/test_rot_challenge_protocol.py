"""
Host-only tests for rot_challenge_service.py -- simulates the C firmware's
rot_session.c handshake logic in Python (FakeDevice) talking to the real
RotSession server implementation over a loopback TCP socket pair. No ESP32
hardware involved; this verifies protocol-level interoperability (field
names, hex framing, proof formulas, ECDSA signing convention, derived
Key_ab) against the actual server code, not just the server's internal
consistency.
"""
import hashlib
import secrets
import socket
import threading

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec, utils

from rot_challenge_service import (
    RotSession, send_json, recv_json, encrypt_data_message, decrypt_data_message,
    ROT_ID_LEN, ROT_DIGEST_LEN, ROT_PUBKEY_LEN, CHALLENGE_LEN,
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
    with itself. sk_a here plays the role of the PUF-derived private key
    rot_identity_derive_keypair() produces on the real device each boot."""

    def __init__(self, sock, id_a: bytes, sk_a: ec.EllipticCurvePrivateKey, id_b_expected: bytes):
        self.sock = sock
        self.id_a = id_a
        self.sk_a = sk_a
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

        # Device does NOT know shs_b (V_B) a priori -- it can only verify server_proof if
        # it already has a pinned V_B reference. The test passes it in via with_server_ref()
        # to mirror rot_identity_load_server_ref().
        expected_server_proof = hashlib.sha256(self._shs_b_ref + self.challenge_a).digest()
        if not secrets.compare_digest(server_proof, expected_server_proof):
            send_json(self.sock, {"type": "error", "error": "server auth failed"})
            raise AssertionError("server_proof did not verify")

        # device_proof = ECDSA_sign(sk_A, SHA256(challenge_b || challenge_a)), matching
        # rot_session.c's mbedtls_ecdsa_write_signature(..., MBEDTLS_MD_SHA256, msg_hash, ...)
        # -- msg_hash is signed AS the digest (Prehashed), not re-hashed by the signing call.
        msg_hash = hashlib.sha256(challenge_b + self.challenge_a).digest()
        signature = self.sk_a.sign(msg_hash, ec.ECDSA(utils.Prehashed(hashes.SHA256())))
        send_json(self.sock, {"type": "response", "device_proof": signature.hex()})

        ack = recv_json(self.sock)
        if ack.get("type") != "session_ack":
            raise AssertionError(f"handshake rejected: {ack}")

        self.key_ab = hashlib.sha256(self._shs_b_ref + self.challenge_a + challenge_b).digest()
        return self.key_ab

    def with_server_ref(self, shs_b_ref: bytes):
        self._shs_b_ref = shs_b_ref
        return self


def _pubkey_uncompressed_bytes(private_key: ec.EllipticCurvePrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        encoding=serialization.Encoding.X962,
        format=serialization.PublicFormat.UncompressedPoint,
    )


@pytest.fixture
def identities():
    id_a = secrets.token_bytes(ROT_ID_LEN)
    sk_a = ec.generate_private_key(ec.SECP256R1())
    pk_a_bytes = _pubkey_uncompressed_bytes(sk_a)
    assert len(pk_a_bytes) == ROT_PUBKEY_LEN
    id_b = secrets.token_bytes(ROT_ID_LEN)
    shs_b = secrets.token_bytes(ROT_DIGEST_LEN)
    registry = {id_a.hex(): {"pk_a": pk_a_bytes.hex(), "shs_b": shs_b.hex(), "label": "TEST_NODE"}}
    return dict(id_a=id_a, sk_a=sk_a, id_b=id_b, shs_b=shs_b, registry=registry)


def run_server_in_thread(server_sock, addr, id_b, registry, results):
    session = RotSession(server_sock, addr, id_b, registry)
    results["ok"] = session.run()
    results["session"] = session


def test_successful_handshake_derives_matching_key(identities):
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"],
        identities["registry"], results))
    t.start()

    device = FakeDevice(device_sock, identities["id_a"], identities["sk_a"],
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
        server_sock, ("127.0.0.1", 0), identities["id_b"],
        identities["registry"], results))
    t.start()

    stranger_id_a = secrets.token_bytes(ROT_ID_LEN)
    device = FakeDevice(device_sock, stranger_id_a, identities["sk_a"],
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


def test_wrong_signing_key_is_rejected(identities):
    """Simulates an impostor device that knows a valid id_a but signs with the wrong
    private key (e.g. a cloned ID without the physical chip's PUF-derived sk_A)."""
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"],
        identities["registry"], results))
    t.start()

    wrong_sk_a = ec.generate_private_key(ec.SECP256R1())  # does NOT match the registered pk_a
    device = FakeDevice(device_sock, identities["id_a"], wrong_sk_a,
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    try:
        device.handshake()
        raised = False
    except AssertionError:
        raised = True
    device_sock.close()
    t.join(timeout=5)

    assert raised, "handshake should have been rejected for a wrong signing key"
    assert results["ok"] is False


def test_stolen_registry_cannot_forge_device_proof(identities):
    """Directly exercises the C1 fix: even with full knowledge of everything the server
    stores for this node (pk_a, shs_b) -- i.e. a complete registry compromise -- an
    attacker without sk_a cannot produce a device_proof that verifies."""
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"],
        identities["registry"], results))
    t.start()

    attacker_sk_a = ec.generate_private_key(ec.SECP256R1())
    # Attacker knows id_a, pk_a and shs_b (everything in the "stolen" registry + NVS), but
    # not sk_a -- it never left the real device and is not derivable from pk_a.
    device = FakeDevice(device_sock, identities["id_a"], attacker_sk_a,
                         identities["id_b"]).with_server_ref(identities["shs_b"])
    try:
        device.handshake()
        raised = False
    except AssertionError:
        raised = True
    device_sock.close()
    t.join(timeout=5)

    assert raised, "a stolen server record must not be sufficient to impersonate the device"
    assert results["ok"] is False
    assert results["session"].authenticated is False


def test_data_phase_round_trip_after_handshake(identities):
    device_sock, server_sock = make_loopback_pair()
    results = {}
    t = threading.Thread(target=run_server_in_thread, args=(
        server_sock, ("127.0.0.1", 0), identities["id_b"],
        identities["registry"], results))
    t.start()

    device = FakeDevice(device_sock, identities["id_a"], identities["sk_a"],
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
