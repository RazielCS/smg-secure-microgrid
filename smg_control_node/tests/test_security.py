"""
Security test suite — cryptographic primitive correctness and protocol security.

Tests cover:
  - HKDF key uniqueness and shared-secret derivation
  - AES-256-GCM authenticated encryption (roundtrip and tamper detection)
  - SecureNode P4 replay attack rejection (chain-based replay protection)
  - Hash chain continuity (PRV_bl propagates correctly across P4 exchanges)
  - Wrong-key divergence (different PSK produces different shared secret)

All tests run on CPython using the aesgcm shim from conftest.py.
No sockets or physical hardware required.
"""

import os
import pytest
from hkdf import hkdf
from secure_node import (
    SecureNode,
    aes_gcm_encrypt,
    aes_gcm_decrypt,
    h_sha256,
    concat,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_paired_nodes(key):
    """Return (initiator, responder) SecureNode pair sharing the same PSK."""
    id_a = b'\xAA' * 16
    id_b = b'\xBB' * 16
    a = SecureNode(device_id=id_a, key=key)
    b = SecureNode(device_id=id_b, key=key)
    return a, b, id_a, id_b


def _do_p2(a, b, id_a, id_b):
    """Complete P2 exchange between a and b using a shared salt."""
    salt = os.urandom(32)
    shS_a, gen_a = a.p2_initiator_compute(salt)
    shS_b, gen_b = b.p2_responder_compute(salt)
    a.p2_finalize(shS_a, shS_b, gen_a, gen_b)
    b.p2_finalize(shS_a, shS_b, gen_a, gen_b)


def _do_p3(a, b, id_b):
    """Complete P3 handshake; both nodes get a session key."""
    ses_init = a.p3_initiator_prepare(b.ID)
    ses_reply_msg, ses_id, rdn2, preinit_bl, init_reg = b.p3_responder_process(ses_init)
    a.p3_initiator_finalize(ses_reply_msg)
    b.p3_responder_finalize(ses_id, rdn2, preinit_bl, init_reg)


def _do_full_handshake(key):
    """Run P2+P3; return session-active (a, b) pair."""
    a, b, id_a, id_b = _make_paired_nodes(key)
    _do_p2(a, b, id_a, id_b)
    _do_p3(a, b, id_b)
    assert a._session_active, "P3 did not activate initiator session"
    assert b._session_active, "P3 did not activate responder session"
    return a, b


# ── HKDF shared-secret derivation ────────────────────────────────────────────

class TestHKDFSharedSecret:
    def test_same_key_same_shared_secret(self):
        """Two nodes with the same PSK must derive the same shared secret."""
        key = os.urandom(32)
        salt = os.urandom(32)
        shS_a = hkdf(salt, key, length=32)
        shS_b = hkdf(salt, key, length=32)
        assert shS_a == shS_b

    def test_different_keys_different_shared_secrets(self):
        """Two nodes with different PSKs must derive different shared secrets."""
        salt = os.urandom(32)
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        assert key_a != key_b  # guard against astronomically unlikely collision
        shS_a = hkdf(salt, key_a, length=32)
        shS_b = hkdf(salt, key_b, length=32)
        assert shS_a != shS_b

    def test_shared_secret_length(self):
        key = os.urandom(32)
        salt = os.urandom(32)
        shS = hkdf(salt, key, length=32)
        assert len(shS) == 32


# ── AES-256-GCM ───────────────────────────────────────────────────────────────

class TestAesGcm:
    def test_roundtrip(self):
        """Encrypt then decrypt must recover the original plaintext."""
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b'{"V_bus":12.1,"I_bus":2.3,"P_bus":27.9}'
        ct, tag = aes_gcm_encrypt(key, nonce, plain)
        recovered = aes_gcm_decrypt(key, nonce, ct, tag)
        assert recovered == plain

    def test_ciphertext_differs_from_plaintext(self):
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b'test payload'
        ct, tag = aes_gcm_encrypt(key, nonce, plain)
        assert ct != plain

    def test_tamper_ciphertext_raises(self):
        """Modifying a single byte of ciphertext must raise an authentication error."""
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b'authentic message'
        ct, tag = aes_gcm_encrypt(key, nonce, plain)
        tampered_ct = bytes([ct[0] ^ 0xFF]) + ct[1:]
        with pytest.raises(Exception):
            aes_gcm_decrypt(key, nonce, tampered_ct, tag)

    def test_tamper_tag_raises(self):
        """Modifying the authentication tag must raise an authentication error."""
        key   = os.urandom(32)
        nonce = os.urandom(12)
        plain = b'authentic message'
        ct, tag = aes_gcm_encrypt(key, nonce, plain)
        tampered_tag = bytes([tag[0] ^ 0x01]) + tag[1:]
        with pytest.raises(Exception):
            aes_gcm_decrypt(key, nonce, ct, tampered_tag)

    def test_wrong_key_raises(self):
        """Decryption with a different key must fail authentication."""
        key_enc = os.urandom(32)
        key_dec = os.urandom(32)
        nonce   = os.urandom(12)
        plain   = b'secret data'
        ct, tag = aes_gcm_encrypt(key_enc, nonce, plain)
        with pytest.raises(Exception):
            aes_gcm_decrypt(key_dec, nonce, ct, tag)

    def test_nonce_uniqueness_produces_different_ciphertext(self):
        """Same key + same plaintext with different nonces must differ."""
        key   = os.urandom(32)
        plain = b'same plaintext'
        ct1, _ = aes_gcm_encrypt(key, os.urandom(12), plain)
        ct2, _ = aes_gcm_encrypt(key, os.urandom(12), plain)
        assert ct1 != ct2


# ── P4 replay attack rejection ────────────────────────────────────────────────

class TestP4ReplayRejected:
    def test_replay_first_message_after_chain_advances(self):
        """
        After a P4 exchange advances PRV_bl on the responder, re-sending the
        first message must fail with a PREG_bl mismatch (RuntimeError).

        This verifies the hash-chain replay protection: each message commits
        to the CURRENT PRV_bl, so a replayed message is rejected once the
        chain has moved forward.
        """
        key = os.urandom(32)
        a, b = _do_full_handshake(key)

        # First P4 exchange
        msg1 = a.p4_send(b'{"V_bus":12.1}')
        res1 = b.p4_receive(msg1)
        a.p4_finalize(res1)

        # Second P4 exchange — advances PRV_bl on both sides
        msg2 = a.p4_send(b'{"V_bus":12.2}')
        res2 = b.p4_receive(msg2)
        a.p4_finalize(res2)

        # Replay msg1 against b whose PRV_bl has now advanced twice
        with pytest.raises(RuntimeError, match="PREG_bl mismatch"):
            b.p4_receive(msg1)

    def test_replaying_second_message_also_rejected(self):
        """Replay of any non-current message is rejected."""
        key = os.urandom(32)
        a, b = _do_full_handshake(key)

        msg1 = a.p4_send(b'data1')
        res1 = b.p4_receive(msg1)
        a.p4_finalize(res1)

        msg2 = a.p4_send(b'data2')
        res2 = b.p4_receive(msg2)
        a.p4_finalize(res2)

        msg3 = a.p4_send(b'data3')
        res3 = b.p4_receive(msg3)
        a.p4_finalize(res3)

        with pytest.raises(RuntimeError, match="PREG_bl mismatch"):
            b.p4_receive(msg2)


# ── Hash chain continuity ─────────────────────────────────────────────────────

class TestHashChainContinuity:
    def test_prv_bl_advances_after_p4(self):
        """PRV_bl on the initiator must change after each P4 finalize."""
        key = os.urandom(32)
        a, b = _do_full_handshake(key)

        prv_before = a.PRV_bl
        msg = a.p4_send(b'payload')
        res = b.p4_receive(msg)
        a.p4_finalize(res)
        assert a.PRV_bl != prv_before

    def test_m_num_increments(self):
        key = os.urandom(32)
        a, b = _do_full_handshake(key)

        assert a._M_num == 0
        a.p4_send(b'data1')
        assert a._M_num == 1
        a.p4_send(b'data2')
        assert a._M_num == 2

    def test_chain_verify_passes_after_valid_exchange(self):
        """
        Responder chain must pass verify_chain() after a valid P4 sequence.
        (Responder stores compact chain entries; initiator stores only PRV_bl.)
        """
        key = os.urandom(32)
        a, b = _do_full_handshake(key)

        for payload in [b'data1', b'data2', b'data3']:
            msg = a.p4_send(payload)
            res = b.p4_receive(msg)
            a.p4_finalize(res)

        assert b.verify_chain() is True


# ── Wrong-key divergence (MITM scenario) ─────────────────────────────────────

class TestMitmKeyDivergence:
    def test_session_keys_differ_with_mismatched_psks(self):
        """
        When a and b use different PSKs, they derive different ShS values.
        Their combined ShS_AB will be based on different inputs, making
        any session established under this handshake unverifiable by a
        legitimate server with the correct PSK.
        """
        salt  = os.urandom(32)
        key_a = os.urandom(32)
        key_b = os.urandom(32)
        assert key_a != key_b

        shS_a = hkdf(salt, key_a, length=32)
        shS_b = hkdf(salt, key_b, length=32)

        # Legitimate shared secret: when both use key_a (as registered)
        shS_expected = hkdf(salt, key_a, length=32)

        # shS_b computed with wrong key differs from the expected value
        assert shS_b != shS_expected

        # Combined ShS_AB with wrong key produces a different session foundation
        shS_ab_correct = h_sha256(concat(shS_a, shS_expected))
        shS_ab_wrong   = h_sha256(concat(shS_a, shS_b))
        assert shS_ab_correct != shS_ab_wrong
