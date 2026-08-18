"""
HKDF-SHA256 test suite — RFC 5869 test vectors + correctness properties.

Verifies that hkdf.py produces values that match the published standard,
satisfies determinism, and produces unique outputs for unique inputs.
"""

import pytest
from hkdf import hkdf, hkdf_extract, hkdf_expand, hmac_sha256


# ── RFC 5869 Appendix A — Test Case 1 ────────────────────────────────────────
# SHA-256, explicit salt and info, L=42

_TC1_IKM  = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
_TC1_SALT = bytes.fromhex("000102030405060708090a0b0c")
_TC1_INFO = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
_TC1_PRK  = bytes.fromhex(
    "077709362c2e32df0ddc3f0dc47bba63"
    "90b6c73bb50f9c3122ec844ad7c2b3e5")
_TC1_OKM  = bytes.fromhex(
    "3cb25f25faacd57a90434f64d0362f2a"
    "2d2d0a90cf1a5a4c5db02d56ecc4c5bf"
    "34007208d5b887185865")

# ── RFC 5869 Appendix A — Test Case 3 ────────────────────────────────────────
# SHA-256, no salt (zero-filled), no info (empty), L=42

_TC3_IKM = bytes.fromhex("0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b0b")
_TC3_PRK  = bytes.fromhex(
    "19ef24a32c717b167f33a91d6f648bdf"
    "96596776afdb6377ac434c1c293ccb04")
_TC3_OKM  = bytes.fromhex(
    "8da4e775a563c18f715f802a063c5a31"
    "b8a11f5c5ee1879ec3454e5f3c738d2d"
    "9d201395faa4b61a96c8")


def test_rfc5869_tc1_extract():
    """hkdf_extract must produce the PRK from RFC 5869 Test Case 1."""
    prk = hkdf_extract(_TC1_SALT, _TC1_IKM)
    assert prk == _TC1_PRK, "PRK mismatch — HMAC-SHA256 implementation error"


def test_rfc5869_tc1_full():
    """Full HKDF (extract + expand) must produce the OKM from RFC 5869 TC1."""
    okm = hkdf(_TC1_SALT, _TC1_IKM, _TC1_INFO, length=42)
    assert okm == _TC1_OKM


def test_rfc5869_tc3_no_salt_info():
    """HKDF with no salt and no info (TC3) — zero-salt fallback per RFC 5869 §2.2."""
    prk = hkdf_extract(b'', _TC3_IKM)
    assert prk == _TC3_PRK

    okm = hkdf(b'', _TC3_IKM, b'', length=42)
    assert okm == _TC3_OKM


def test_determinism():
    """Same inputs must always produce the same output (no random state)."""
    salt = b'\x01' * 32
    ikm  = b'\x02' * 32
    info = b'smg-test'

    out1 = hkdf(salt, ikm, info, length=32)
    out2 = hkdf(salt, ikm, info, length=32)
    assert out1 == out2


def test_key_uniqueness():
    """Different IKMs must produce different output — uniqueness under distinct PSKs."""
    salt = b'\xAB' * 32
    info = b''

    out_a = hkdf(salt, b'\x00' * 32, info, length=32)
    out_b = hkdf(salt, b'\xFF' * 32, info, length=32)
    assert out_a != out_b


def test_salt_uniqueness():
    """Different salts must produce different output for the same IKM."""
    ikm = b'\x42' * 32
    info = b''

    out1 = hkdf(b'\x00' * 32, ikm, info, length=32)
    out2 = hkdf(b'\x01' * 32, ikm, info, length=32)
    assert out1 != out2


def test_output_length():
    """Output length must exactly match the requested length."""
    for length in (16, 32, 48, 64):
        out = hkdf(b'\x00' * 32, b'\x01' * 32, b'', length=length)
        assert len(out) == length, f"expected {length} bytes, got {len(out)}"


def test_output_is_bytes():
    """Output must be bytes, not str."""
    out = hkdf(b'\x00' * 16, b'\x01' * 16, b'', length=32)
    assert isinstance(out, bytes)
