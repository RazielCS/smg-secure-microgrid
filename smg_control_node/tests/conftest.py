"""
MicroPython compatibility shims for host-side testing (CPython + pytest).

Injected into sys.modules at collection time — before any firmware module
is imported — so hkdf.py, ems_module.py, metrics.py, sensor_module.py,
and secure_node.py can be imported and exercised under standard pytest
without physical ESP32 hardware.

AES-256-GCM is provided by the 'cryptography' package (same semantics as
the aesgcm C module used on the ESP32).
"""

import sys
import os
import gc
import hashlib
import binascii
import json
import time
from unittest.mock import MagicMock

# ── Path ─────────────────────────────────────────────────────────────────────
# Allow tests to import modules from the parent directory (smg_control_node/).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── MicroPython standard-library shims ───────────────────────────────────────
# hkdf.py already has `try: import uhashlib / except ImportError: import hashlib`
# but secure_node.py does not, so we inject explicitly.
sys.modules.setdefault('uhashlib', hashlib)
sys.modules.setdefault('uos', os)
sys.modules.setdefault('ubinascii', binascii)
sys.modules.setdefault('ujson', json)

# ── time — augment real module with MicroPython-specific functions ────────────
if not hasattr(time, 'ticks_ms'):
    time.ticks_ms = lambda: int(time.time() * 1000) & 0x3FFFFFFF
if not hasattr(time, 'ticks_us'):
    time.ticks_us = lambda: int(time.time() * 1_000_000) & 0x3FFFFFFF
if not hasattr(time, 'ticks_diff'):
    time.ticks_diff = lambda t1, t0: t1 - t0
if not hasattr(time, 'sleep_ms'):
    time.sleep_ms = lambda ms: time.sleep(ms / 1000.0)

# ── gc — add MicroPython mem_free() ──────────────────────────────────────────
if not hasattr(gc, 'mem_free'):
    gc.mem_free = lambda: 256 * 1024  # 256 KB stub

# ── machine — mock hardware peripherals ──────────────────────────────────────
_machine = MagicMock()
_machine.ADC.ATTN_11DB = 3
_machine.ADC.WIDTH_12BIT = 3
_machine.reset.side_effect = SystemExit("machine.reset() called in test context")
sys.modules['machine'] = _machine

# ── network / socket — not exercised by unit tests ───────────────────────────
sys.modules.setdefault('network', MagicMock())
sys.modules.setdefault('socket', MagicMock())

# ── aesgcm — AES-256-GCM using the 'cryptography' package ───────────────────
# Mirrors the C module API: encrypt() → (ciphertext, tag), decrypt() → plaintext.
class _FakeAesGcm:
    @staticmethod
    def encrypt(key, nonce, data, aad=None):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        ct_tag = AESGCM(key).encrypt(nonce, data, aad)
        return ct_tag[:-16], ct_tag[-16:]  # (ciphertext, tag)

    @staticmethod
    def decrypt(key, nonce, data, tag, aad=None):
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        return AESGCM(key).decrypt(nonce, data + tag, aad)

sys.modules['aesgcm'] = _FakeAesGcm()

# ucryptolib and blake3: not available; the firmware falls back to aesgcm gracefully.
sys.modules.setdefault('ucryptolib', MagicMock(side_effect=ImportError))
sys.modules.setdefault('blake3', MagicMock(side_effect=ImportError))
