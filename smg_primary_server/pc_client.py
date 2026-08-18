"""
SMG Control Node — PC Client Simulator
=======================================
Runs the SecureNode v3 protocol (P2-P5) from a standard Python environment
to test the primary server without requiring the ESP32 hardware.

Usage:
    python pc_client.py --server 192.168.4.1 --port 5000 --registry node_registry.json
"""

import argparse
import binascii
import hashlib
import hmac as _hmac
import json
import os
import socket
import struct
import sys
import time
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

# ---------------------------------------------------------------------------
# Crypto helpers — mirror secure_node.py + crypto_utils.py
# ---------------------------------------------------------------------------

DIGEST_SIZE = 32
ID_SIZE = 16
AES_KEY_SIZE = 32
GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16
_BLOCK_SIZE = 64
_IPAD = 0x36
_OPAD = 0x5C


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
    prk = hkdf_extract(salt, ikm)
    return hkdf_expand(prk, info, length)


def _fit_key(key: bytes, length: int = AES_KEY_SIZE) -> bytes:
    if len(key) >= length:
        return h_sha256(key)[:length]
    return key + b'\x00' * (length - len(key))


def encrypt_gcm(plaintext: bytes, key: bytes) -> bytes:
    k = _fit_key(key)
    nonce = os.urandom(GCM_NONCE_SIZE)
    aesgcm = AESGCM(k)
    ct_and_tag = aesgcm.encrypt(nonce, plaintext, None)
    ct = ct_and_tag[:-GCM_TAG_SIZE]
    tag = ct_and_tag[-GCM_TAG_SIZE:]
    return nonce + ct + tag


def decrypt_gcm(ciphertext: bytes, key: bytes) -> bytes:
    k = _fit_key(key)
    nonce = ciphertext[:GCM_NONCE_SIZE]
    ct = ciphertext[GCM_NONCE_SIZE:-GCM_TAG_SIZE]
    tag = ciphertext[-GCM_TAG_SIZE:]
    aesgcm = AESGCM(k)
    return aesgcm.decrypt(nonce, ct + tag, None)


def concat(*args: bytes) -> bytes:
    return b''.join(args)


# ---------------------------------------------------------------------------
# Protocol helpers — mirror secure_node.py
# ---------------------------------------------------------------------------

BYTES_FIELDS = {
    "A_GEN_bl", "B_GEN_bl", "ID_A", "ID_B", "ShS_A", "ShS_B",
    "INIT_cif", "SES_cif", "SES_id", "MSG_cif", "RES_cif",
    "A_END_cif", "B_END_cif", "VERIF_cif", "RESP_cif",
    "CONF_cif",
}


def pack_payload(payload):
    serialized = {}
    for k, v in payload.items():
        serialized[k] = binascii.hexlify(v).decode() if isinstance(v, bytes) else v
    body = json.dumps(serialized).encode()
    return len(body).to_bytes(4, "big") + body


def unpack_payload(raw):
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


def send_msg(sock, payload):
    sock.sendall(pack_payload(payload))


def recv_msg(sock):
    hdr = b""
    while len(hdr) < 4:
        chunk = sock.recv(4 - len(hdr))
        if not chunk:
            raise ConnectionError("Connection closed")
        hdr += chunk
    msg_len = int.from_bytes(hdr, "big")
    if msg_len == 0 or msg_len > 16384:
        raise ValueError("recv_msg: frame size {} out of range".format(msg_len))
    body = b""
    while len(body) < msg_len:
        chunk = sock.recv(msg_len - len(body))
        if not chunk:
            raise ConnectionError("Connection closed")
        body += chunk
    return unpack_payload(body)


# ---------------------------------------------------------------------------
# SecureNode client — mirrors secure_node.py initiator flow
# ---------------------------------------------------------------------------

class SecureNodeClient:
    """SecureNode v3 initiator (client) for PC testing."""

    def __init__(self, device_id: bytes, key: bytes):
        self.ID = device_id
        self._key = key
        self._peer_id = None
        self._session_active = False
        self._M_num = 0
        self._chain = []
        self._K_AB = None
        self._K_AB_enc = None
        self._SES_id = None
        self._ShS_AB = None
        self._PRV_bl = None  # Genesis block

    def p1_provision(self, device_id: bytes, key: bytes):
        self.ID = device_id
        self._key = key

    def p2_initiator_compute(self, salt: bytes):
        self._r_A = os.urandom(16)
        shs_a = hkdf(salt=salt, ikm=concat(self._r_A, self.ID), length=32)
        gen_a = h_sha256(concat(self._r_A, b'GEN', self.ID))
        return shs_a, gen_a

    def p2_finalize(self, shs_a: bytes, shs_b: bytes, gen_a: bytes, gen_b: bytes):
        self._ShS_AB = h_sha256(concat(shs_a, shs_b))
        GEN_bl = h_sha256(concat(gen_a, gen_b))
        self._PRV_bl = GEN_bl  # Genesis block for P3
        self._K_AB = hkdf(salt=self._ShS_AB, ikm=concat(self._r_A, self._peer_id), length=32)

    def p3_initiator_prepare(self, peer_id: bytes):
        self._r_A2 = os.urandom(16)  # RDN1
        ts = ts_bytes()
        # Compute INIT_reg and PREINIT_bl to match server expectations
        INIT_reg = concat(self._r_A2, ts, self.ID, peer_id, self._ShS_AB)
        A_INIT_bl = h_sha256(INIT_reg)
        PREINIT_bl = h_sha256(concat(self._PRV_bl, A_INIT_bl))
        # Encrypt: PREINIT_bl(32) || RDN1(16) || TS(8)
        init_cif = encrypt_gcm(
            concat(PREINIT_bl, self._r_A2, ts),
            self._ShS_AB
        )
        return {
            "type": "ses_init",
            "INIT_cif": init_cif,
            "ID_A": self.ID,
        }

    def p3_initiator_finalize(self, ses_resp: dict):
        ses_cif = ses_resp["SES_cif"]
        pt = decrypt_gcm(ses_cif, self._ShS_AB)
        # Server sends: SES_id(16) || RDN2(16) || RDN1_echo(16) || PREINIT_bl(32)
        self._SES_id = pt[:16]
        RDN2 = pt[16:32]
        RDN1_echo = pt[32:48]
        PREINIT_bl = pt[48:80]

        if RDN1_echo != self._r_A2:
            raise RuntimeError("P3: RDN1 mismatch")

        # Compute SES_key
        self._K_AB_enc = h_sha256(concat(RDN2, self._ShS_AB))
        # Compute conf_tag matching server: h_sha256(SES_key || SES_id || RDN1 || RDN2)
        conf_tag = h_sha256(concat(self._K_AB_enc, self._SES_id, self._r_A2, RDN2))
        conf_cif = encrypt_gcm(conf_tag, self._K_AB_enc)

        # Update _PRV_bl to match server's post-P3 state
        # Server: INIT_bl = h_sha256(concat(self._PRV_bl, SES_id, PREINIT_bl))
        self._PRV_bl = h_sha256(concat(self._PRV_bl, self._SES_id, PREINIT_bl))

        return {
            "type": "ses_conf",
            "CONF_cif": conf_cif,
            "ID_A": self.ID,
        }

    def p4_send(self, sensor_json: bytes):
        self._M_num += 1
        ts = ts_bytes()
        # Normalize sensor data
        SMG_reg = smg_normalize(sensor_json)
        # Build REG_reg for hash chain
        REG_reg = make_reg_reg(SMG_reg, ts, self._M_num, self.ID, self._peer_id, self._SES_id)
        PRE_REG_bl = h_sha256(REG_reg)
        PREG_bl = h_sha256(concat(self._PRV_bl, PRE_REG_bl))
        # A_REG_bl uses node's long-term PSK (chain asymmetry)
        A_REG_bl = h_sha256(concat(PREG_bl, self._key))
        # Store for p4_finalize
        self._last_A_REG_bl = A_REG_bl
        # Build MSG_reg TLV
        MSG_reg = make_msg_reg(SMG_reg, ts, self._M_num, self.ID, PREG_bl, A_REG_bl)
        # Encrypt with session key
        msg_cif = encrypt_gcm(MSG_reg, self._K_AB_enc)
        return {
            "type": "dx_msg",
            "MSG_cif": msg_cif,
            "ID_A": self.ID,
            "M_num": self._M_num,
        }

    def p4_finalize(self, res_dict: dict):
        res_cif = res_dict["RES_cif"]
        B_REG_bl = decrypt_gcm(res_cif, self._K_AB_enc)
        # Compute REG_bl = h_sha256(concat(A_REG_bl, B_REG_bl))
        # But we need A_REG_bl from the last p4_send call
        REG_bl = h_sha256(concat(self._last_A_REG_bl, B_REG_bl))
        # Update _PRV_bl for next message
        self._PRV_bl = REG_bl

    def p5_logout_initiate(self):
        end_reg = make_end_reg(ts_bytes(), self._SES_id, self.ID, self._peer_id)
        a_end_cif = encrypt_gcm(end_reg, self._K_AB_enc)
        self._session_active = False
        return {
            "type": "logout_req",
            "A_END_cif": a_end_cif,
            "ID_A": self.ID,
        }

    def p5_logout_finalize(self, res_dict: dict):
        b_end_cif = res_dict["B_END_cif"]
        pt = decrypt_gcm(b_end_cif, self._K_AB_enc)
        # Verify logout response structure
        if len(pt) < 4:
            raise RuntimeError("P5: Invalid logout response")

    def verify_chain(self):
        return True  # Client doesn't maintain chain

    def get_timings_summary(self):
        return {}


def smg_normalize(raw_data: bytes) -> bytes:
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


def ts_bytes():
    return struct.pack(">Q", int(time.time()))


def ts_int():
    return int(time.time())


# ---------------------------------------------------------------------------
# PC Client Runner
# ---------------------------------------------------------------------------

def run_client(server_ip: str, server_port: int, node_id: bytes, node_key: bytes, node_salt: bytes, num_sends: int = 10, send_interval: float = 0.0):
    """Run the full SecureNode protocol from PC to server.

    Args:
        send_interval: Seconds to wait between P4 sends.
                       0 = no delay (fire as fast as TCP allows).
                       ~1.1 matches the ESP32's bench-mode loop timing.
    """
    metrics = {
        'p2_duration_us': 0,
        'p3_duration_us': 0,
        'p4_total_us': 0,
        'p4_count': 0,
        'p5_duration_us': 0,
        'handshake_total_us': 0,
        'p4_latencies': [],
        'errors': [],
    }

    print("=" * 50)
    print("  SMG Control Node — PC Client Simulator")
    print("  Server: {}:{}".format(server_ip, server_port))
    print("=" * 50)

    # Connect TCP
    print("\n[client] Connecting to {}:{}...".format(server_ip, server_port))
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10)
    try:
        sock.connect((server_ip, server_port))
        print("[client] TCP connected")
    except Exception as e:
        print("[client] TCP connection failed: {}".format(e))
        return None

    # Initialize SecureNode
    node = SecureNodeClient(device_id=node_id, key=node_key)
    node.p1_provision(device_id=node_id, key=node_key)

    # P2: Root of Trust
    print("\n[client] P2: Root of Trust...")
    p2_start = time.perf_counter()
    try:
        shs_a, gen_a = node.p2_initiator_compute(node_salt)
        rot_msg = {
            "type": "rot_hello",
            "ShS_A": binascii.hexlify(shs_a).decode(),
            "A_GEN_bl": binascii.hexlify(gen_a).decode(),
            "ID_A": binascii.hexlify(node.ID).decode(),
        }
        send_msg(sock, rot_msg)

        rot_resp = recv_msg(sock)
        if rot_resp.get("type") == "error":
            raise RuntimeError("P2 error: {}".format(rot_resp.get("error")))

        shs_b = rot_resp["ShS_B"]
        b_gen_bl = rot_resp["B_GEN_bl"]
        id_b = rot_resp["ID_B"]
        node._peer_id = id_b

        node.p2_finalize(shs_a, shs_b, gen_a, b_gen_bl)
        p2_end = time.perf_counter()
        metrics['p2_duration_us'] = int((p2_end - p2_start) * 1_000_000)
        print("[client] P2 OK: {} us".format(metrics['p2_duration_us']))
    except Exception as e:
        metrics['errors'].append("P2: {}".format(str(e)))
        print("[client] P2 FAILED: {}".format(e))
        sock.close()
        return metrics

    # P3: Session Establishment
    print("\n[client] P3: Session Establishment...")
    p3_start = time.perf_counter()
    try:
        ses_msg = node.p3_initiator_prepare(node._peer_id)
        send_msg(sock, ses_msg)

        ses_resp = recv_msg(sock)
        if ses_resp.get("type") == "error":
            raise RuntimeError("P3 error: {}".format(ses_resp.get("error")))

        conf_msg = node.p3_initiator_finalize(ses_resp)
        send_msg(sock, conf_msg)

        conf_ack = recv_msg(sock)
        if conf_ack.get("type") == "error":
            raise RuntimeError("P3 conf error: {}".format(conf_ack.get("error")))

        p3_end = time.perf_counter()
        metrics['p3_duration_us'] = int((p3_end - p3_start) * 1_000_000)
        node._session_active = True
        print("[client] P3 OK: {} us".format(metrics['p3_duration_us']))
    except Exception as e:
        metrics['errors'].append("P3: {}".format(str(e)))
        print("[client] P3 FAILED: {}".format(e))
        sock.close()
        return metrics

    metrics['handshake_total_us'] = metrics['p2_duration_us'] + metrics['p3_duration_us']
    print("\n[client] Handshake total: {} us".format(metrics['handshake_total_us']))

    # P4: Data Exchange
    print("\n[client] P4: Data Exchange ({} messages)...".format(num_sends))

    # Methodologically correct sensor data generator
    # Base statistical profile from NODE_02 golden trial (n=400):
    #   V_bus: N(17.141, 0.193) V,  P_node: N(97.164, 2.64) W
    # Derived: I_node = P_node / V_bus  ≈ 5.67 A
    import random as _random
    _SEED = 42
    _rng = _random.Random(_SEED)

    def _gen_reading(idx, rng):
        v_bus = 17.141 + rng.gauss(0, 0.193) + (idx / 4000.0)
        i_node = 5.670 + rng.gauss(0, 0.08) + (idx / 8000.0)
        v_gen = 16.500 + rng.gauss(0, 0.15) + (idx / 3000.0)
        i_bus = 5.500 + rng.gauss(0, 0.12) + (idx / 6000.0)
        i_gen = 5.200 + rng.gauss(0, 0.10) + (idx / 5000.0)

        p_bus = v_bus * i_bus
        p_gen = v_gen * i_gen
        p_node = v_bus * i_node

        return {
            "V_bus": round(v_bus, 3),
            "I_bus": round(i_bus, 3),
            "P_bus": round(p_bus, 2),
            "V_gen": round(v_gen, 3),
            "I_gen": round(i_gen, 3),
            "P_gen": round(p_gen, 2),
            "I_node": round(i_node, 3),
            "P_node": round(p_node, 2),
            "timestamp_ms": int(time.time() * 1000),
            "saturated": False,
        }

    for i in range(num_sends):
        sensor_data = _gen_reading(i, _rng)
        sensor_json = json.dumps(sensor_data).encode()

        t0 = time.perf_counter()
        try:
            dx_msg = node.p4_send(sensor_json)
            send_msg(sock, dx_msg)

            # Receive response (may need to skip setpoint messages)
            dx_resp = recv_msg(sock)
            while dx_resp.get("type") == "setpoint":
                # Setpoint is optional, skip and get the actual dx_res
                dx_resp = recv_msg(sock)

            if dx_resp.get("type") == "error":
                raise RuntimeError("P4 error: {}".format(dx_resp.get("error")))

            node.p4_finalize(dx_resp)
            t1 = time.perf_counter()
            duration_us = int((t1 - t0) * 1_000_000)
            metrics['p4_total_us'] += duration_us
            metrics['p4_count'] += 1
            metrics['p4_latencies'].append(duration_us)
            print("  P4 #{:02d}: {} us".format(i + 1, duration_us))

            if send_interval > 0 and i < num_sends - 1:
                time.sleep(send_interval)
        except Exception as e:
            metrics['errors'].append("P4 #{}: {}".format(i + 1, str(e)))
            print("  P4 #{} FAILED: {}".format(i + 1, e))
            break

    # P5: Logout
    print("\n[client] P5: Logout...")
    p5_start = time.perf_counter()
    try:
        logout_msg = node.p5_logout_initiate()
        send_msg(sock, logout_msg)

        logout_resp = recv_msg(sock)
        if logout_resp.get("type") == "logout_res":
            node.p5_logout_finalize(logout_resp)

        p5_end = time.perf_counter()
        metrics['p5_duration_us'] = int((p5_end - p5_start) * 1_000_000)
        print("[client] P5 OK: {} us".format(metrics['p5_duration_us']))
    except Exception as e:
        metrics['errors'].append("P5: {}".format(str(e)))
        print("[client] P5 FAILED: {}".format(e))

    sock.close()

    # Print summary
    print("\n" + "=" * 50)
    print("  SECURITY METRICS SUMMARY")
    print("=" * 50)
    print("  P2 (Root of Trust) : {} us".format(metrics['p2_duration_us']))
    print("  P3 (Session)       : {} us".format(metrics['p3_duration_us']))
    print("  Handshake total    : {} us".format(metrics['handshake_total_us']))
    print("  P4 transactions    : {} (avg {} us)".format(
        metrics['p4_count'],
        round(metrics['p4_total_us'] / metrics['p4_count'], 1) if metrics['p4_count'] > 0 else 0
    ))
    print("  P5 (Logout)        : {} us".format(metrics['p5_duration_us']))
    if metrics['errors']:
        print("  Errors             : {}".format(len(metrics['errors'])))
        for err in metrics['errors'][-3:]:
            print("    - {}".format(err))
    print("=" * 50)

    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SMG PC Client Simulator")
    parser.add_argument("--server", default="192.168.4.1", help="Server IP")
    parser.add_argument("--port", type=int, default=5000, help="Server port")
    parser.add_argument("--registry", default="node_registry.json", help="Node registry JSON")
    parser.add_argument("--node", default="SMG_NODE_01", help="Node label to use")
    parser.add_argument("--sends", type=int, default=10, help="Number of P4 messages")
    parser.add_argument("--interval", type=float, default=0.0,
                        help="Send interval in seconds (0 = no delay)")
    args = parser.parse_args()

    # Load registry
    with open(args.registry) as f:
        registry = json.load(f)

    # Find node by label
    node_entry = None
    for node_id_hex, entry in registry.items():
        if entry.get("label") == args.node:
            node_entry = entry
            break

    if node_entry is None:
        print("ERROR: Node '{}' not found in registry".format(args.node))
        sys.exit(1)

    node_id = binascii.unhexlify(node_id_hex)
    node_key = binascii.unhexlify(node_entry["node_key"])
    node_salt = binascii.unhexlify(node_entry["node_salt"])

    metrics = run_client(args.server, args.port, node_id, node_key, node_salt, args.sends, args.interval)

    if metrics:
        # Save results
        output = "pc_client_results.json"
        with open(output, "w") as f:
            json.dump(metrics, f, indent=2)
        print("\nResults saved to {}".format(output))
