"""
SecureNode Protocol v3 — SMG Adapted Implementation (P1-P6)
=============================================================
Platform: MicroPython on ESP32 (NodeMCU)

Protocol phases:
  P1  Secure Provisioning (identity from NVS)
  P2  Root of Trust (HKDF-SHA256 key derivation)
  P3  Session Establishment (PSK mutual authentication, 5-message handshake)
  P4  Data Exchange (hashchain with TLV-encoded registers)
  P5  Logout (session archive)
  P6  Session Verification (chain integrity audit)

Crypto:
  Hash:   SHA-256 (uhashlib, ESP32 HW accelerated), BLAKE3 (optional C module)
  Cipher: AES-256-GCM (aesgcm C module, ESP32 HW), AES-256-ECB (ucryptolib fallback)
  KDF:    HKDF-SHA256 (RFC 5869, ~50-100us vs PBKDF2 ~2-5s)

Changes from protocol_template:
  - PBKDF2 replaced with HKDF (from hkdf.py)
  - aesgcm C module preferred for GCM operations
  - SMG-specific metrics hooks added
  - MicroPython-optimized (no CPython fallback needed)
"""

import struct
import time
import uhashlib
import uos

try:
    import ubinascii
except ImportError:
    import binascii as ubinascii

try:
    import ujson as json_mod
except ImportError:
    import json as json_mod

try:
    import aesgcm as _esp_aesgcm
    _ESP_GCM = True
except ImportError:
    _ESP_GCM = False

try:
    from ucryptolib import aes as _ucrypto_aes
    _UCRYPTO = True
except ImportError:
    _UCRYPTO = False


def _gcm_mul128(X, Y):
    # GF(2^128) multiply: reduction poly x^128+x^7+x^2+x+1 → R = E1||00..0
    R = 0xe1000000000000000000000000000000
    Z = 0
    V = X
    for i in range(128):
        if (Y >> (127 - i)) & 1:
            Z ^= V
        if V & 1:
            V = (V >> 1) ^ R
        else:
            V >>= 1
    return Z


def _ghash(H, data):
    Y = 0
    for i in range(0, len(data), 16):
        blk = data[i:i + 16]
        if len(blk) < 16:
            blk = blk + b'\x00' * (16 - len(blk))
        Y ^= int.from_bytes(blk, 'big')
        Y = _gcm_mul128(Y, H)
    # Convert 128-bit int to 16 bytes without int.to_bytes (MicroPython compat)
    out = bytearray(16)
    v = Y
    for j in range(15, -1, -1):
        out[j] = v & 0xff
        v >>= 8
    return bytes(out)


def _xor16(a, b):
    out = bytearray(len(a))
    for i in range(len(a)):
        out[i] = a[i] ^ b[i]
    return bytes(out)


def _gcm_pure_encrypt(key, nonce, plaintext, aad=b''):
    ecb = _ucrypto_aes(key, 1)
    H = int.from_bytes(ecb.encrypt(b'\x00' * 16), 'big')
    J0 = nonce + b'\x00\x00\x00\x01'
    EJ0 = ecb.encrypt(J0)
    ctr = int.from_bytes(J0, 'big') + 1
    ct = bytearray()
    for i in range(0, len(plaintext), 16):
        blk = plaintext[i:i + 16]
        ks = ecb.encrypt(ctr.to_bytes(16, 'big'))
        ct += _xor16(blk, ks[:len(blk)])
        ctr += 1
    ct = bytes(ct)
    r = len(aad) % 16
    p_aad = aad + (b'\x00' * (16 - r) if r else b'')
    r = len(ct) % 16
    p_ct = ct + (b'\x00' * (16 - r) if r else b'')
    gh = p_aad + p_ct + (len(aad) * 8).to_bytes(8, 'big') + (len(ct) * 8).to_bytes(8, 'big')
    S = _ghash(H, gh)
    return ct, _xor16(S, EJ0)


def _gcm_pure_decrypt(key, nonce, ciphertext, tag, aad=b''):
    ecb = _ucrypto_aes(key, 1)
    H = int.from_bytes(ecb.encrypt(b'\x00' * 16), 'big')
    J0 = nonce + b'\x00\x00\x00\x01'
    EJ0 = ecb.encrypt(J0)
    r = len(aad) % 16
    p_aad = aad + (b'\x00' * (16 - r) if r else b'')
    r = len(ciphertext) % 16
    p_ct = ciphertext + (b'\x00' * (16 - r) if r else b'')
    gh = p_aad + p_ct + (len(aad) * 8).to_bytes(8, 'big') + (len(ciphertext) * 8).to_bytes(8, 'big')
    S = _ghash(H, gh)
    if _xor16(S, EJ0) != tag:
        raise RuntimeError("GCM auth failed")
    ctr = int.from_bytes(J0, 'big') + 1
    pt = bytearray()
    for i in range(0, len(ciphertext), 16):
        blk = ciphertext[i:i + 16]
        ks = ecb.encrypt(ctr.to_bytes(16, 'big'))
        pt += _xor16(blk, ks[:len(blk)])
        ctr += 1
    return bytes(pt)


try:
    import blake3 as _blake3_mod
    _BLAKE3 = True
except ImportError:
    _BLAKE3 = False

from hkdf import hkdf as _hkdf_derive

IS_MICROPYTHON = True

DIGEST_SIZE = 32
ID_SIZE = 16
AES_KEY_SIZE = 32
GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16


def _urandom(n):
    return uos.urandom(n)


def _ticks_us():
    return time.ticks_us()


def _ticks_diff(t1, t0):
    return time.ticks_diff(t1, t0)


def h_sha256(data):
    return uhashlib.sha256(data).digest()


def h_blake3(data):
    if _BLAKE3:
        try:
            h = _blake3_mod.BLAKE3()
            h.update(data)
            return h.digest()
        except Exception:
            pass
    return h_sha256(data)


HASH_FN = {'sha256': h_sha256, 'blake3': h_blake3}


def _pkcs7_pad(data, bs=16):
    pl = bs - (len(data) % bs)
    return data + bytes([pl] * pl)


def _pkcs7_unpad(data):
    return data[:-data[-1]]


def _fit_key(key, length=AES_KEY_SIZE):
    if len(key) >= length:
        return h_sha256(key)[:length]
    return key + b'\x00' * (length - len(key))


def aes_gcm_encrypt(key, nonce, plaintext, aad=None):
    if _ESP_GCM:
        return _esp_aesgcm.encrypt(key, nonce, plaintext, aad)
    if _UCRYPTO:
        return _gcm_pure_encrypt(key, nonce, plaintext, aad or b'')
    raise RuntimeError("No AES-GCM available (aesgcm C module required)")


def aes_gcm_decrypt(key, nonce, ciphertext, tag, aad=None):
    if _ESP_GCM:
        return _esp_aesgcm.decrypt(key, nonce, ciphertext, tag, aad)
    if _UCRYPTO:
        return _gcm_pure_decrypt(key, nonce, ciphertext, tag, aad or b'')
    raise RuntimeError("No AES-GCM available (aesgcm C module required)")


def aes_ecb_encrypt(key, plaintext):
    k = _fit_key(key)
    if _UCRYPTO:
        return _ucrypto_aes(k, 1).encrypt(_pkcs7_pad(plaintext))
    raise RuntimeError("No AES-ECB available")


def aes_ecb_decrypt(key, ciphertext):
    k = _fit_key(key)
    if _UCRYPTO:
        return _pkcs7_unpad(_ucrypto_aes(k, 1).decrypt(ciphertext))
    raise RuntimeError("No AES-ECB available")


def encrypt_gcm(plaintext, key):
    k = _fit_key(key)
    nonce = _urandom(GCM_NONCE_SIZE)
    ct, tag = aes_gcm_encrypt(k, nonce, plaintext)
    return nonce + ct + tag


def decrypt_gcm(ciphertext, key):
    k = _fit_key(key)
    nonce = ciphertext[:GCM_NONCE_SIZE]
    tag = ciphertext[-GCM_TAG_SIZE:]
    ct = ciphertext[GCM_NONCE_SIZE:-GCM_TAG_SIZE]
    return aes_gcm_decrypt(k, nonce, ct, tag)


def encrypt_ecb(plaintext, key):
    return aes_ecb_encrypt(_fit_key(key), plaintext)


def decrypt_ecb(ciphertext, key):
    return aes_ecb_decrypt(_fit_key(key), ciphertext)


CIPHER_ENC = {'gcm': encrypt_gcm, 'ecb': encrypt_ecb}
CIPHER_DEC = {'gcm': decrypt_gcm, 'ecb': decrypt_ecb}


def concat(*args):
    return b"".join(args)


def ts_bytes():
    # time.time() returns Unix epoch only if NTP has been synced; without NTP
    # it returns seconds since boot. Configure NTP (ntptime.settime()) before
    # P2 if forensic timestamp accuracy is required.
    return struct.pack(">Q", int(time.time()))


def ts_int():
    return int(time.time())


def smg_normalize(raw_data):
    header = struct.pack(">I", len(raw_data))
    payload = header + raw_data
    pad = (64 - len(payload) % 64) % 64
    return payload + b'\x00' * pad


def make_reg_reg(SMG_reg, TS_b, M_num, ID_A, ID_B, SES_id):
    parts = [SMG_reg, TS_b, struct.pack(">I", M_num), ID_A, ID_B, SES_id]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def make_msg_reg(SMG_reg, TS_b, M_num, ID_A, PREG_bl, A_REG_bl):
    parts = [SMG_reg, TS_b, struct.pack(">I", M_num), ID_A, PREG_bl, A_REG_bl]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def make_end_reg(TS_b, SES_id, ID_A, ID_B):
    parts = [TS_b, SES_id, ID_A, ID_B]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def parse_tlv(data, n_fields):
    fields, pos = [], 0
    for _ in range(n_fields):
        if pos + 2 > len(data):
            return None
        ln = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        if pos + ln > len(data):
            return None
        fields.append(data[pos:pos + ln])
        pos += ln
    return tuple(fields)


def pack_tlv_pair(a, b):
    return struct.pack(">H", len(a)) + a + struct.pack(">H", len(b)) + b


def pack_verif_entry(ID_A, PREG_bl, A_REG_bl, REG_bl):
    parts = [ID_A, PREG_bl, A_REG_bl, REG_bl]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def unpack_verif_entry(data):
    return parse_tlv(data, 4)


def pack_list(entries):
    out = struct.pack(">H", len(entries))
    for e in entries:
        out += struct.pack(">H", len(e)) + e
    return out


def unpack_list(data):
    if len(data) < 2:
        return None
    n, pos = struct.unpack(">H", data[:2])[0], 2
    entries = []
    for _ in range(n):
        if pos + 2 > len(data):
            return None
        ln = struct.unpack(">H", data[pos:pos + 2])[0]
        pos += 2
        entries.append(data[pos:pos + ln])
        pos += ln
    return entries


BYTES_FIELDS = {
    "A_GEN_bl", "B_GEN_bl", "ID_A", "ID_B", "ShS_A", "ShS_B",
    "INIT_cif", "SES_cif", "SES_id", "MSG_cif", "RES_cif",
    "A_END_cif", "B_END_cif", "VERIF_cif", "RESP_cif",
    "CONF_cif",
}


def pack_payload(payload):
    serialized = {}
    for k, v in payload.items():
        serialized[k] = ubinascii.hexlify(v).decode() if isinstance(v, bytes) else v
    body = json_mod.dumps(serialized).encode()
    return len(body).to_bytes(4, "big") + body


def unpack_payload(raw):
    d = json_mod.loads(raw.decode())
    result = {}
    for k, v in d.items():
        if k in BYTES_FIELDS and isinstance(v, str):
            try:
                result[k] = ubinascii.unhexlify(v)
            except Exception:
                result[k] = v
        else:
            result[k] = v
    return result


def send_msg(sock, payload):
    sock.sendall(pack_payload(payload))


_RECV_MSG_MAX = 16384  # 16 KB — prevents MemoryError from oversized/malicious frames


def recv_msg(sock):
    hdr = b""
    while len(hdr) < 4:
        chunk = sock.recv(4 - len(hdr))
        if not chunk:
            raise ConnectionError("Connection closed")
        hdr += chunk
    msg_len = int.from_bytes(hdr, "big")
    if msg_len == 0 or msg_len > _RECV_MSG_MAX:
        raise ValueError("recv_msg: frame size {} out of range [1, {}]".format(
            msg_len, _RECV_MSG_MAX))
    body = b""
    while len(body) < msg_len:
        chunk = sock.recv(msg_len - len(body))
        if not chunk:
            raise ConnectionError("Connection closed")
        body += chunk
    return unpack_payload(body)


class SecureNode:
    """SecureNode protocol P1-P6 for ESP32 MicroPython."""

    def __init__(self, port=5000, hash_func='sha256',
                 cipher_mode='gcm', device_id=None, key=None):
        self.port = port
        self.hash_func = hash_func
        self.cipher_mode = cipher_mode

        if device_id is not None:
            self.ID = device_id if isinstance(device_id, bytes) else device_id.encode()[:ID_SIZE]
        else:
            self.ID = _urandom(ID_SIZE)

        if key is not None:
            # Bytes key is used as-is (no truncation). Recommended size: 32 bytes.
            # String key (legacy/testing only) is encoded and truncated to ID_SIZE.
            if not isinstance(key, bytes):
                print("[SecureNode] WARNING: string key passed — encoded and truncated to {} bytes. "
                      "Pass a bytes key (e.g. os.urandom(32)) for full entropy.".format(ID_SIZE))
            self.KEY = key if isinstance(key, bytes) else key.encode()[:ID_SIZE]
        else:
            self.KEY = _urandom(ID_SIZE)

        self._ShS = self._GEN_bl = self.ShS_AB = self.GEN_bl = self.PRV_bl = None
        self._ses_pending = {}
        self.SES_id = self.SES_key = self.INIT_bl = self.INIT_reg = None
        self._session_active = False
        self._peer_id = None
        self._M_num = 0
        self._dx_pending = {}
        self.chain = []
        self._logout_pending = {}
        self.logout_record = None
        self.session_archives = {}
        self._verif_pending = {}
        self.verif_results = []
        self._timings = {}
        self._memory_snapshots = {}

    def _h(self, data):
        return HASH_FN.get(self.hash_func, h_sha256)(data)

    def _c(self, plaintext, key):
        return CIPHER_ENC.get(self.cipher_mode, encrypt_gcm)(plaintext, key)

    def _d(self, ciphertext, key):
        return CIPHER_DEC.get(self.cipher_mode, decrypt_gcm)(ciphertext, key)

    def _t0(self):
        return _ticks_us()

    def _t1(self):
        return _ticks_us()

    def _td(self, t1, t0):
        return _ticks_diff(t1, t0)

    def _record_timing(self, phase, op, dur_us, extra=None):
        entry = {'phase': phase, 'operation': op, 'duration_us': dur_us, 'timestamp': ts_int()}
        if extra:
            entry.update(extra)
        self._timings.setdefault(phase, []).append(entry)

    def _record_memory(self, phase, label):
        import gc
        gc.collect()
        val = gc.mem_free()
        self._memory_snapshots.setdefault(phase, {})[label] = val

    def p1_provision(self, device_id=None, key=None):
        t0 = self._t0()
        if device_id is not None:
            self.ID = device_id if isinstance(device_id, bytes) else device_id.encode()[:ID_SIZE]
        if key is not None:
            if not isinstance(key, bytes):
                print("[SecureNode] WARNING: string key passed to p1_provision — encoded and "
                      "truncated to {} bytes. Pass bytes (e.g. os.urandom(32)).".format(ID_SIZE))
            self.KEY = key if isinstance(key, bytes) else key.encode()[:ID_SIZE]
        self._record_timing('P1', 'provision', self._td(self._t1(), t0))
        return self.ID, self.KEY

    def p2_initiator_compute(self, salt):
        t0 = self._t0()
        self._ShS = _hkdf_derive(salt, self.KEY, length=DIGEST_SIZE)
        self._GEN_bl = self._h(concat(salt, self.ID))
        self._record_timing('P2', 'initiator_compute', self._td(self._t1(), t0))
        return self._ShS, self._GEN_bl

    def p2_responder_compute(self, salt):
        t0 = self._t0()
        self._ShS = _hkdf_derive(salt, self.KEY, length=DIGEST_SIZE)
        self._GEN_bl = self._h(concat(salt, self.ID))
        self._record_timing('P2', 'responder_compute', self._td(self._t1(), t0))
        return self._ShS, self._GEN_bl

    def p2_finalize(self, ShS_A, ShS_B, A_GEN_bl, B_GEN_bl):
        t0 = self._t0()
        self.ShS_AB = self._h(concat(ShS_A, ShS_B))
        self.GEN_bl = self._h(concat(A_GEN_bl, B_GEN_bl))
        self.PRV_bl = self.GEN_bl
        self._record_timing('P2', 'finalize', self._td(self._t1(), t0))
        self._record_memory('P2', 'after_finalize')
        return self.ShS_AB, self.GEN_bl

    def p3_initiator_prepare(self, peer_id):
        if not self.ShS_AB:
            raise RuntimeError("P2 not completed")
        self._peer_id = peer_id
        t0 = self._t0()
        RDN1 = _urandom(ID_SIZE)
        TS = ts_bytes()
        INIT_reg = concat(RDN1, TS, self.ID, peer_id, self.ShS_AB)
        A_INIT_bl = self._h(INIT_reg)
        PREINIT_bl = self._h(concat(self.PRV_bl, A_INIT_bl))
        INIT_cif = self._c(concat(PREINIT_bl, RDN1, TS), self.ShS_AB)
        self._ses_pending = {"RDN1": RDN1, "TS": TS, "PREINIT_bl": PREINIT_bl,
                             "INIT_reg": INIT_reg, "peer_id": peer_id}
        self._record_timing('P3', 'initiator_prepare', self._td(self._t1(), t0))
        return {"type": "ses_init", "ID_A": self.ID, "INIT_cif": INIT_cif}

    def p3_responder_process(self, msg):
        ID_A = msg["ID_A"]
        t0 = self._t0()
        plain = self._d(msg["INIT_cif"], self.ShS_AB)
        PREINIT_bl_rx, RDN1, TS = plain[0:DIGEST_SIZE], plain[DIGEST_SIZE:DIGEST_SIZE + ID_SIZE], plain[DIGEST_SIZE + ID_SIZE:]
        INIT_reg = concat(RDN1, TS, ID_A, self.ID, self.ShS_AB)
        PREINIT_bl = self._h(concat(self.PRV_bl, self._h(INIT_reg)))
        if PREINIT_bl != PREINIT_bl_rx:
            raise RuntimeError("P3: PREINIT_bl verification failed")
        SES_id = _urandom(ID_SIZE)
        RDN2 = _urandom(ID_SIZE)
        self.SES_id = SES_id
        self.SES_key = self._h(concat(RDN2, self.ShS_AB))
        SES_cif = self._c(concat(SES_id, RDN2, RDN1, PREINIT_bl_rx), self.ShS_AB)
        self._ses_pending.update({"SES_id": SES_id, "RDN2": RDN2,
                                  "PREINIT_bl": PREINIT_bl, "INIT_reg": INIT_reg,
                                  "RDN1_rx": RDN1})
        self._record_timing('P3', 'responder_process', self._td(self._t1(), t0))
        return ({"type": "ses_reply", "SES_cif": SES_cif},
                SES_id, RDN2, PREINIT_bl, INIT_reg)

    def p3_initiator_finalize(self, msg):
        t0 = self._t0()
        plain = self._d(msg["SES_cif"], self.ShS_AB)
        SES_id = plain[0:ID_SIZE]
        RDN2 = plain[ID_SIZE:ID_SIZE * 2]
        RDN1_echo = plain[ID_SIZE * 2:ID_SIZE * 3]  # 16 bytes; excludes PREINIT_bl_rx tail
        sp = self._ses_pending
        if RDN1_echo and RDN1_echo != sp["RDN1"]:
            raise RuntimeError("P3: RDN1 echo mismatch")
        self._p3_finalize(SES_id, RDN2, sp["PREINIT_bl"], sp["INIT_reg"])
        conf_tag = self._h(concat(self.SES_key, SES_id, sp["RDN1"], RDN2))
        CONF_cif = self._c(conf_tag, self.SES_key)
        self._record_timing('P3', 'initiator_finalize', self._td(self._t1(), t0))
        return {"type": "ses_conf", "SES_id": SES_id, "CONF_cif": CONF_cif}

    def p3_responder_confirm(self, msg):
        t0 = self._t0()
        sp = self._ses_pending
        SES_id, RDN2 = sp["SES_id"], sp["RDN2"]
        conf_tag_rx = self._d(msg["CONF_cif"], self.SES_key)
        conf_expected = self._h(concat(self.SES_key, SES_id, sp.get("RDN1_rx", b''), RDN2))
        if conf_tag_rx != conf_expected:
            raise RuntimeError("P3: conf_tag mismatch")
        self._record_timing('P3', 'responder_confirm', self._td(self._t1(), t0))
        return {"type": "ses_conf_ack", "SES_id": SES_id}

    def p3_responder_finalize(self, SES_id, RDN2, PREINIT_bl, INIT_reg):
        t0 = self._t0()
        self.INIT_bl = self._h(concat(self.PRV_bl, SES_id, PREINIT_bl))
        self.INIT_reg = INIT_reg
        self.PRV_bl = self.INIT_bl
        self._session_active = True
        self._record_timing('P3', 'responder_finalize', self._td(self._t1(), t0))
        self._record_memory('P3', 'after_finalize')

    def _p3_finalize(self, SES_id, RDN2, PREINIT_bl, INIT_reg):
        self.SES_id = SES_id
        self.SES_key = self._h(concat(RDN2, self.ShS_AB))
        self.INIT_bl = self._h(concat(self.PRV_bl, SES_id, PREINIT_bl))
        self.INIT_reg = INIT_reg
        self.PRV_bl = self.INIT_bl
        self._session_active = True
        self._record_memory('P3', 'after_finalize')

    def p4_send(self, raw_data):
        if not self.SES_key or not self.PRV_bl:
            raise RuntimeError("P3 not completed")
        self._M_num += 1
        M_num = self._M_num
        TS_b = ts_bytes()
        TS_int = struct.unpack(">Q", TS_b)[0]
        SMG_reg = smg_normalize(raw_data)
        REG_reg = make_reg_reg(SMG_reg, TS_b, M_num, self.ID, self._peer_id, self.SES_id)
        PRE_REG_bl = self._h(REG_reg)
        PREG_bl = self._h(concat(self.PRV_bl, PRE_REG_bl))
        # KEY (long-term PSK) used here, not SES_key: A_REG_bl binds each chain
        # entry to the device identity so the server can audit the full chain
        # offline without holding session keys. Tradeoff: compromising KEY allows
        # forging past entries retroactively (chain lacks forward secrecy). This
        # is an explicit design choice — auditability is prioritized over chain
        # forward secrecy because the PSK itself is the root of trust.
        A_REG_bl = self._h(concat(PREG_bl, self.KEY))
        MSG_reg = make_msg_reg(SMG_reg, TS_b, M_num, self.ID, PREG_bl, A_REG_bl)
        MSG_cif = self._c(MSG_reg, self.SES_key)
        self._dx_pending[M_num] = {"SMG_reg": SMG_reg, "TS_int": TS_int,
                                    "M_num": M_num, "REG_reg": REG_reg,
                                    "A_REG_bl": A_REG_bl, "PRV_bl": self.PRV_bl}
        return {"type": "dx_msg", "SES_id": self.SES_id, "MSG_cif": MSG_cif}

    def p4_receive(self, msg):
        t0 = self._t0()
        MSG_reg_bytes = self._d(msg["MSG_cif"], self.SES_key)
        fields = parse_tlv(MSG_reg_bytes, 6)
        if fields is None:
            raise RuntimeError("P4: MSG_reg malformed")
        SMG_reg_rx, TS_b, m_b, ID_A, PREG_bl_rx, A_REG_bl = fields
        M_num = struct.unpack(">I", m_b)[0]
        REG_reg = make_reg_reg(SMG_reg_rx, TS_b, M_num, ID_A, self.ID, self.SES_id)
        PRE_REG_bl = self._h(REG_reg)
        PREG_bl = self._h(concat(self.PRV_bl, PRE_REG_bl))
        if PREG_bl != PREG_bl_rx:
            raise RuntimeError("P4: PREG_bl mismatch at M#" + str(M_num))
        B_REG_bl = self._h(concat(PREG_bl, self.KEY))
        REG_bl = self._h(concat(A_REG_bl, B_REG_bl))
        # Compact chain entry: only store the hashes needed for continuity/audit.
        # Full SMG_reg and REG_reg records are NOT stored on the node — a constrained
        # ESP32 (~110 KB free heap) can only hold ~128 full entries before MemoryError.
        # The authoritative audit log lives on the server (auth_service.py).
        self.chain.append({'index': len(self.chain) + 1, 'M_num': M_num,
                           'REG_bl': REG_bl, 'PRV_bl': self.PRV_bl})
        self.PRV_bl = REG_bl
        self._record_timing('P4', 'receive', self._td(self._t1(), t0), {'M_num': M_num})
        return {"type": "dx_res", "RES_cif": self._c(B_REG_bl, self.SES_key), "M_num": M_num}

    def p4_finalize(self, msg):
        M_num = msg["M_num"]
        B_REG_bl = self._d(msg["RES_cif"], self.SES_key)
        pending = self._dx_pending.pop(M_num, None)
        if pending is None:
            raise RuntimeError("P4: No pending state for M#" + str(M_num))
        REG_bl = self._h(concat(pending["A_REG_bl"], B_REG_bl))
        # Node stores ONLY the rolling PRV_bl (32 bytes) — not the full chain entry.
        # Each full entry costs ~860 bytes; 110 KB heap → ~128 entries max (~2 min).
        # The server (auth_service.py) holds the authoritative audit chain in CPython.
        # _M_num already tracks the message count; no compact list needed here.
        self.PRV_bl = REG_bl

    def p5_logout_initiate(self):
        if not self._session_active:
            raise RuntimeError("No active session")
        t0 = self._t0()
        TS_b = ts_bytes()
        TS_int = struct.unpack(">Q", TS_b)[0]
        END_reg = make_end_reg(TS_b, self.SES_id, self.ID, self._peer_id)
        A_END_bl = self._h(concat(END_reg, self.SES_id, self.ID))
        plain_end = pack_tlv_pair(END_reg, A_END_bl)
        A_END_cif = self._c(plain_end, self.SES_key)
        self._logout_pending = {"END_reg": END_reg, "A_END_bl": A_END_bl,
                                "TS_int": TS_int, "PRV_bl": self.PRV_bl}
        self._record_timing('P5', 'initiate', self._td(self._t1(), t0))
        return {"type": "logout_req", "SES_id": self.SES_id, "A_END_cif": A_END_cif}

    def p5_logout_respond(self, msg):
        t0 = self._t0()
        plain = self._d(msg["A_END_cif"], self.SES_key)
        fields = parse_tlv(plain, 2)
        if fields is None:
            raise RuntimeError("P5: Logout payload malformed")
        END_reg_rx, A_END_bl_rx = fields
        end_fields = parse_tlv(END_reg_rx, 4)
        if end_fields is None:
            raise RuntimeError("P5: END_reg malformed")
        TS_int = struct.unpack(">Q", end_fields[0])[0]
        ID_A_rx = end_fields[2]
        if self._h(concat(END_reg_rx, self.SES_id, ID_A_rx)) != A_END_bl_rx:
            raise RuntimeError("P5: A_END_bl verification failed")
        PRE_END_bl = self._h(concat(self.PRV_bl, A_END_bl_rx))
        B_END_bl = self._h(concat(PRE_END_bl, self.KEY))
        B_END_cif = self._c(B_END_bl, self.SES_key)
        self._p5_finalize(END_reg_rx, A_END_bl_rx, B_END_bl, TS_int, False, self.PRV_bl)
        self._record_timing('P5', 'respond', self._td(self._t1(), t0))
        return {"type": "logout_res", "B_END_cif": B_END_cif}

    def p5_logout_finalize(self, msg):
        t0 = self._t0()
        B_END_bl = self._d(msg["B_END_cif"], self.SES_key)
        lp = self._logout_pending
        self._p5_finalize(lp["END_reg"], lp["A_END_bl"], B_END_bl,
                          lp["TS_int"], True, lp["PRV_bl"])
        self._record_timing('P5', 'finalize', self._td(self._t1(), t0))

    def _p5_finalize(self, END_reg, A_END_bl, B_END_bl, TS_int, is_initiator, prv):
        END_bl = self._h(concat(A_END_bl, B_END_bl))
        self.logout_record = {'END_reg': END_reg, 'END_bl': END_bl, 'TS': TS_int,
                              'SES_id': self.SES_id,
                              'ID_A': self.ID if is_initiator else self._peer_id,
                              'ID_B': self._peer_id if is_initiator else self.ID,
                              'PRV_bl': prv}
        self._archive_session()
        self.SES_key = self.SES_id = None
        self._session_active = False
        self._logout_pending = {}
        self._record_memory('P5', 'after_finalize')

    def _archive_session(self):
        if not self.SES_id or not self.INIT_bl or not self.logout_record:
            return
        key = ubinascii.hexlify(self.SES_id).decode()
        # 'records' holds compact chain entries (M_num + REG_bl only) if this node
        # acted as responder, or is empty if this node acted as initiator.
        # The server's auth_service.py holds the complete audit log in both cases.
        self.session_archives[key] = {'SES_id': self.SES_id, 'INIT_reg': self.INIT_reg or b"",
                                      'INIT_bl': self.INIT_bl, 'records': list(self.chain),
                                      'msg_count': self._M_num, 'final_PRV_bl': self.PRV_bl,
                                      'logout': self.logout_record, 'peer_id': self._peer_id or b""}

    def find_session(self, ses_id=None, index=-1):
        if not self.session_archives:
            return None
        if ses_id is not None:
            key = ses_id.hex() if isinstance(ses_id, bytes) else ses_id
            return self.session_archives.get(key)
        keys = list(self.session_archives.keys())
        try:
            return self.session_archives[keys[index]]
        except (IndexError, KeyError):
            return None

    def p6_verify_initiate(self, session_archive):
        """
        Initiate P6 chain verification against a session archive.

        IMPORTANT: Requires server-format archives where each record contains
        'REG_reg' (the full register payload). The compact archives stored by
        initiator nodes (ESP32 secondaries) contain only {'index', 'M_num',
        'REG_bl', 'PRV_bl'} and do NOT have 'REG_reg'. Call this method only
        on the server (primary node / RPi4) which stores full records.
        """
        if not session_archive.get('records'):
            raise RuntimeError("Archived session has no records")
        if not self.SES_key:
            raise RuntimeError("Need SES_key to verify")
        # Detect compact (initiator-side) archives and raise a clear error.
        first_rec = session_archive['records'][0] if session_archive['records'] else {}
        if 'REG_reg' not in first_rec:
            raise RuntimeError(
                "P6 requires server-format archives with full REG_reg entries. "
                "Initiator nodes store compact records (REG_bl only). "
                "Run P6 on the server (RPi4 primary node).")
        t0 = self._t0()
        arc = session_archive
        PRV_ix = arc['INIT_bl']
        verif_entries, a_reg_list = [], []
        for rec in arc['records']:
            PRE_REG_bl = self._h(rec['REG_reg'])
            PREG_bl = self._h(concat(PRV_ix, PRE_REG_bl))
            A_REG_bl = self._h(concat(PREG_bl, self.KEY))
            verif_entries.append(pack_verif_entry(self.ID, PREG_bl, A_REG_bl, rec['REG_bl']))
            a_reg_list.append(A_REG_bl)
            PRV_ix = rec['REG_bl']
        VERIF_x_cif = self._c(pack_list(verif_entries), self.SES_key)
        arc_key = ubinascii.hexlify(arc['SES_id']).decode()
        self._verif_pending[arc_key] = {"arc": arc, "a_reg_list": a_reg_list}
        self._record_timing('P6', 'initiate', self._td(self._t1(), t0))
        return {"type": "sverif_req", "SES_id": self.SES_id, "VERIF_cif": VERIF_x_cif}

    def p6_verify_respond(self, msg):
        t0 = self._t0()
        VERIF_x = self._d(msg["VERIF_cif"], self.SES_key)
        entries = unpack_list(VERIF_x)
        if entries is None:
            raise RuntimeError("P6: VERIF_x malformed")
        resp = []
        for entry_data in entries:
            fields = unpack_verif_entry(entry_data)
            if fields is None:
                continue
            ID_A, PREG_bl, A_REG_bl, REG_bl = fields
            B_REG_bl = self._h(concat(PREG_bl, self.KEY))
            match = (self._h(concat(A_REG_bl, B_REG_bl)) == REG_bl)
            resp.append({'B_REG_bl': B_REG_bl, 'match': match})
        RESP_cif = self._c(pack_list([
            pack_tlv_pair(r['B_REG_bl'], b'\x01' if r['match'] else b'\x00')
            for r in resp
        ]), self.SES_key)
        self._record_timing('P6', 'respond', self._td(self._t1(), t0))
        return {"type": "sverif_res", "RESP_cif": RESP_cif}

    def p6_verify_finalize(self, msg):
        t0 = self._t0()
        RESP_bytes = self._d(msg["RESP_cif"], self.SES_key)
        resp_raw = unpack_list(RESP_bytes)
        if resp_raw is None:
            raise RuntimeError("P6: RESP malformed")
        target_key = list(self._verif_pending.keys())[0] if self._verif_pending else None
        if target_key is None:
            raise RuntimeError("P6: No pending verification")
        pending = self._verif_pending.pop(target_key)
        arc, a_reg_list = pending["arc"], pending["a_reg_list"]
        results = []
        for i, raw_field in enumerate(resp_raw):
            if len(raw_field) < 2:
                continue
            ln = struct.unpack(">H", raw_field[:2])[0]
            B_REG_bl = raw_field[2:2 + ln]
            pos2 = 2 + ln
            match_byte = b'\x00'
            if pos2 + 2 <= len(raw_field):
                ln2 = struct.unpack(">H", raw_field[pos2:pos2 + 2])[0]
                match_byte = raw_field[pos2 + 2:pos2 + 2 + ln2]
            A_REG_bl = a_reg_list[i] if i < len(a_reg_list) else b""
            REG_bl_calc = self._h(concat(A_REG_bl, B_REG_bl))
            stored = arc['records'][i]['REG_bl'] if i < len(arc['records']) else b""
            results.append({'index': i + 1, 'REG_bl': REG_bl_calc,
                            'verified': (REG_bl_calc == stored) and match_byte == b'\x01',
                            'A_REG_bl': A_REG_bl, 'B_REG_bl': B_REG_bl})
        self.verif_results = results
        self._record_timing('P6', 'finalize', self._td(self._t1(), t0))
        return results

    def verify_chain(self):
        # Initiator nodes store no chain entries (only rolling PRV_bl is kept).
        # Responder nodes store compact entries {M_num, REG_bl, PRV_bl}.
        # Full audit verification is performed by the server via P6.
        if not self.chain:
            return self.PRV_bl is not None
        for i in range(1, len(self.chain)):
            if self.chain[i]['PRV_bl'] != self.chain[i - 1]['REG_bl']:
                return False
        return True

    def print_chain(self, role=""):
        sep = "=" * 64
        print("\n{}".format(sep))
        print(" HASH CHAIN - {}".format(role or repr(self)))
        print(" Node ID : {}".format(ubinascii.hexlify(self.ID).decode()))
        print(" Msgs    : {}  Local records: {} {}".format(
            self._M_num, len(self.chain), '+ LOGOUT' if self.logout_record else '(active)'))
        print(" Hash    : {}  Cipher: {}".format(self.hash_func, self.cipher_mode))
        if self.PRV_bl:
            print(" PRV_bl  : {}".format(ubinascii.hexlify(self.PRV_bl).decode()[:32]))
        print(sep)
        if not self.chain:
            print(" (initiator: chain audit log held by server)")
        for rec in self.chain:
            print(" #{:02d} M={} REG={}".format(
                rec['index'], rec['M_num'],
                ubinascii.hexlify(rec['REG_bl']).decode()[:16]))
        if self.logout_record:
            lr = self.logout_record
            print(" [LOGOUT] END_bl={}".format(
                ubinascii.hexlify(lr['END_bl']).decode()[:16]))
        print(" Integrity: {}".format('OK' if self.verify_chain() else 'COMPROMISED'))
        print(sep)

    def print_timings(self):
        sep = "-" * 60
        print("\n{}".format(sep))
        print(" TIMING SUMMARY")
        print(sep)
        for phase, entries in self._timings.items():
            total = sum(e['duration_us'] for e in entries)
            avg = total / len(entries) if entries else 0
            print(" {:6s}: {:4d} ops  total={:>10.0f} us  avg={:>10.1f} us".format(
                phase, len(entries), total, avg))
        print(sep)

    def get_timings_summary(self):
        summary = {}
        for phase, entries in self._timings.items():
            total = sum(e['duration_us'] for e in entries)
            avg = total / len(entries) if entries else 0
            summary[phase] = {'count': len(entries), 'total_us': total, 'avg_us': avg}
        return summary

    def __repr__(self):
        return "SecureNode(:{} ID={}... hash={})".format(
            self.port, ubinascii.hexlify(self.ID).decode()[:8], self.hash_func)


def create_node(node_id, key=None, hash_func='sha256', cipher_mode='gcm', port=5000):
    return SecureNode(port=port, hash_func=hash_func,
                      cipher_mode=cipher_mode, device_id=node_id, key=key)
