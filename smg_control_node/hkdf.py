"""
HKDF-SHA256 implementation per RFC 5869 for MicroPython.

Provides:
  - hmac_sha256(key, msg) -> 32-byte digest
  - hkdf_extract(salt, ikm) -> PRK (32 bytes)
  - hkdf_expand(prk, info, length) -> OKM (length bytes)
  - hkdf(salt, ikm, info, length) -> full HKDF (extract + expand)

Uses uhashlib.sha256 (hardware-accelerated on ESP32).
Replaces PBKDF2-HMAC-SHA256 from the protocol template, reducing
P2 key derivation from ~2-5s (100k iterations) to ~50-100us.
"""

try:
    import uhashlib
except ImportError:
    import hashlib as uhashlib


_IPAD = 0x36
_OPAD = 0x5C
_BLOCK_SIZE = 64
_DIGEST_SIZE = 32


def _xor_pad(key, pad_byte, block_size):
    """XOR key with repeated pad byte to produce padded key block."""
    if len(key) > block_size:
        key = uhashlib.sha256(key).digest()
    padded = bytearray(block_size)
    for i in range(len(key)):
        padded[i] = key[i] ^ pad_byte
    for i in range(len(key), block_size):
        padded[i] = pad_byte
    return bytes(padded)


def hmac_sha256(key, msg):
    """
    HMAC-SHA256(key, msg) -> 32-byte digest.

    Implements HMAC per RFC 2104:
      HMAC(K, m) = H((K' ^ opad) || H((K' ^ ipad) || m))
    """
    ipad = _xor_pad(key, _IPAD, _BLOCK_SIZE)
    opad = _xor_pad(key, _OPAD, _BLOCK_SIZE)

    inner_hash = uhashlib.sha256(ipad + msg).digest()
    return uhashlib.sha256(opad + inner_hash).digest()


def hkdf_extract(salt, ikm):
    """
    HKDF-Extract(salt, IKM) -> PRK (32 bytes).

    PRK = HMAC-SHA256(salt, IKM)

    If salt is empty or None, uses a zero-filled 32-byte salt per RFC 5869.
    """
    if not salt:
        salt = b'\x00' * _DIGEST_SIZE
    return hmac_sha256(salt, ikm)


def hkdf_expand(prk, info, length):
    """
    HKDF-Expand(PRK, info, L) -> OKM (L bytes).

    T(0) = empty
    T(i) = HMAC-SHA256(PRK, T(i-1) || info || i)
    OKM = first L bytes of T(1) || T(2) || ...

    Maximum output: 255 * 32 = 8160 bytes.
    """
    if length > 255 * _DIGEST_SIZE:
        raise ValueError("Cannot expand to more than 8160 bytes")

    okm = b''
    t_prev = b''
    counter = 1
    while len(okm) < length:
        t_prev = hmac_sha256(prk, t_prev + info + bytes([counter]))
        okm += t_prev
        counter += 1

    return okm[:length]


def hkdf(salt, ikm, info=b'', length=_DIGEST_SIZE):
    """
    Full HKDF: extract then expand.

    This replaces the PBKDF2-HMAC-SHA256 call used in the original
    SecureNode protocol for P2 key derivation.

    Parameters:
        salt:   Salt value (bytes). If empty, uses zero salt.
        ikm:    Input keying material (bytes).
        info:   Context/application-specific info (bytes, default empty).
        length: Desired output length in bytes (default 32).

    Returns:
        Derived key material of specified length (bytes).
    """
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)
