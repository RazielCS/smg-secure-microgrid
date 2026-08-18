"""
CPython-compatible crypto primitives for SMG primary server.

Mirrors the MicroPython crypto stack in smg_control_node:
  - HKDF-SHA256 (RFC 5869) — matches hkdf.py with info=b''
  - AES-256-GCM             — matches aesgcm C module behaviour
  - SHA-256                 — matches uhashlib.sha256

Requires: cryptography >= 41.0  (pip install cryptography)
"""

import hashlib
import hmac as _hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

DIGEST_SIZE = 32
ID_SIZE = 16
AES_KEY_SIZE = 32
GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16

_BLOCK_SIZE = 64
_IPAD = 0x36
_OPAD = 0x5C


# ---------------------------------------------------------------------------
# SHA-256 / HMAC-SHA256 — pure Python, matches hkdf.py exactly
# ---------------------------------------------------------------------------

def h_sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def _xor_pad(key: bytes, pad_byte: int, block_size: int) -> bytes:
    if len(key) > block_size:
        key = hashlib.sha256(key).digest()
    padded = bytearray(block_size)
    for i, b in enumerate(key):
        padded[i] = b ^ pad_byte
    for i in range(len(key), block_size):
        padded[i] = pad_byte
    return bytes(padded)


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    ipad = _xor_pad(key, _IPAD, _BLOCK_SIZE)
    opad = _xor_pad(key, _OPAD, _BLOCK_SIZE)
    inner = hashlib.sha256(ipad + msg).digest()
    return hashlib.sha256(opad + inner).digest()


# ---------------------------------------------------------------------------
# HKDF-SHA256 — matches hkdf.py (salt, ikm, info=b'', length=32)
# ---------------------------------------------------------------------------

def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    if not salt:
        salt = b'\x00' * DIGEST_SIZE
    return hmac_sha256(salt, ikm)


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    okm = b''
    t_prev = b''
    counter = 1
    while len(okm) < length:
        t_prev = hmac_sha256(prk, t_prev + info + bytes([counter]))
        okm += t_prev
        counter += 1
    return okm[:length]


def hkdf(salt: bytes, ikm: bytes, info: bytes = b'', length: int = DIGEST_SIZE) -> bytes:
    """HKDF-SHA256 matching hkdf.py on MicroPython."""
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)


# ---------------------------------------------------------------------------
# AES-256-GCM — matches aesgcm C module + encrypt_gcm/decrypt_gcm in secure_node.py
# Ciphertext format: nonce(12) || ct || tag(16)
# ---------------------------------------------------------------------------

def _fit_key(key: bytes, length: int = AES_KEY_SIZE) -> bytes:
    if len(key) >= length:
        return h_sha256(key)[:length]
    return key + b'\x00' * (length - len(key))


def encrypt_gcm(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt using AES-256-GCM. Returns nonce(12) || ct || tag(16)."""
    k = _fit_key(key)
    nonce = os.urandom(GCM_NONCE_SIZE)
    aesgcm = AESGCM(k)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    ct = ct_and_tag[:-GCM_TAG_SIZE]
    tag = ct_and_tag[-GCM_TAG_SIZE:]
    return nonce + ct + tag


def decrypt_gcm(ciphertext: bytes, key: bytes) -> bytes:
    """Decrypt AES-256-GCM ciphertext (nonce(12) || ct || tag(16))."""
    k = _fit_key(key)
    nonce = ciphertext[:GCM_NONCE_SIZE]
    ct = ciphertext[GCM_NONCE_SIZE:-GCM_TAG_SIZE]
    tag = ciphertext[-GCM_TAG_SIZE:]
    aesgcm = AESGCM(k)
    return aesgcm.decrypt(nonce, ct + tag, None)


def concat(*args: bytes) -> bytes:
    return b''.join(args)
