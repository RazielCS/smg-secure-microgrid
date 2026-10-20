"""
SMG Primary Server — Authentication Service.

Implements the SecureNode v3 responder role (P2–P5) for each
connected secondary node. One NodeSession instance is created
per TCP connection.

Protocol flow (responder side):
  P2: recv rot_hello  → compute ShS_B + B_GEN_bl → send rot_ack
  P3: recv ses_init   → compute SES_cif → send ses_reply
      recv ses_conf   → verify conf_tag  → send ses_conf_ack
  P4: recv dx_msg     → decrypt + verify chain → send dx_res
      (optionally send setpoint after P4 response)
  P5: recv logout_req → verify A_END_bl → send logout_res
"""

import os
import struct
import time
import binascii
import json
import logging

from crypto_utils import (
    h_sha256, hkdf, encrypt_gcm, decrypt_gcm, concat,
    DIGEST_SIZE, ID_SIZE, GCM_NONCE_SIZE, GCM_TAG_SIZE, AES_KEY_SIZE, _fit_key,
)
from protocol import send_msg, recv_msg

log = logging.getLogger(__name__)

_TRUST_DROP_THRESHOLD = 0.30  # Below this score, connection is dropped to force re-auth


# ---------------------------------------------------------------------------
# TLV helpers — mirror of secure_node.py
# ---------------------------------------------------------------------------

def _make_reg_reg(SMG_reg, TS_b, M_num, ID_A, ID_B, SES_id):
    parts = [SMG_reg, TS_b, struct.pack(">I", M_num), ID_A, ID_B, SES_id]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def _parse_tlv(data, n_fields):
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


def _pack_tlv_pair(a, b):
    return struct.pack(">H", len(a)) + a + struct.pack(">H", len(b)) + b


def _make_end_reg(TS_b, SES_id, ID_A, ID_B):
    parts = [TS_b, SES_id, ID_A, ID_B]
    return b"".join(struct.pack(">H", len(p)) + p for p in parts)


def _ts_bytes():
    return struct.pack(">Q", int(time.time()))


# ---------------------------------------------------------------------------
# NodeSession — one per connected secondary node
# ---------------------------------------------------------------------------

class NodeSession:
    """
    Manages SecureNode P2–P5 responder state for one secondary node.

    Parameters:
        conn:       Connected TCP socket.
        addr:       Client (node) address tuple.
        registry:   Node registry dict {node_id_hex: {node_key, node_salt, label}}.
        server_id:  Server's 16-byte identifier.
        server_key: Server's 32-byte master key.
        data_store: DataStore instance for persisting sensor readings.
        ems:        EMSCoordinator instance for demand setpoints.
    """

    def __init__(self, conn, addr, registry, server_id, server_key,
                 data_store, ems):
        self.conn = conn
        self.addr = addr
        self.registry = registry
        self.server_id = server_id
        self.server_key = server_key
        self.data_store = data_store
        self.ems = ems

        # Protocol state
        self._ShS_AB = None
        self._PRV_bl = None
        self._SES_id = None
        self._SES_key = None
        self._session_active = False
        self._M_num_expected = 0
        self._chain = []

        # Per-connection peer identity
        self._peer_id = None
        self._node_label = "unknown"

        # P3 pending state
        self._p3_pending = {}

        # Trust score — initialized to 0 (not yet authenticated).
        # Set to 0.8 after successful P3 completion.
        # Reduced by security events; drops below 0.3 → connection dropped.
        # Recovery requires a full P5/P2 re-authentication.
        self._trust_score: float = 0.0
        self._consecutive_anomalies: int = 0
        self._V_BUS_ANOMALY_THRESHOLD = 0.8   # V: deviation from cross-node median
        self._V_BUS_ANOMALY_WINDOW    = 5     # consecutive anomalous readings before penalty

        # Metrics
        self.metrics = {
            'p2_us': 0, 'p3_us': 0, 'p4_count': 0, 'p4_total_us': 0,
            'p5_us': 0, 'errors': [], 'trust_events': [],
        }

    # -----------------------------------------------------------------------
    # Main session runner
    # -----------------------------------------------------------------------

    def run(self):
        """Execute the full protocol session until logout or error."""
        log.info("[%s] New connection", self.addr)
        try:
            msg = recv_msg(self.conn)
            if msg.get("type") != "rot_hello":
                self._send_error("Expected rot_hello, got: " + msg.get("type", "?"))
                return

            ok = self._run_p2(msg)
            if not ok:
                return

            msg = recv_msg(self.conn)
            if msg.get("type") != "ses_init":
                self._send_error("Expected ses_init, got: " + msg.get("type", "?"))
                return

            ok = self._run_p3(msg)
            if not ok:
                return

            self._run_data_loop()

        except ConnectionError as e:
            log.info("[%s] Connection closed: %s", self.addr, e)
        except Exception as e:
            log.exception("[%s] Session error: %s", self.addr, e)
            self.metrics['errors'].append(str(e))
        finally:
            log.info("[%s] Session ended (%d P4 messages, node=%s)",
                     self.addr, self.metrics['p4_count'], self._node_label)

    # -----------------------------------------------------------------------
    # P2 — Root of Trust
    # -----------------------------------------------------------------------

    def _run_p2(self, msg):
        t0 = time.perf_counter_ns()
        try:
            ID_A = msg["ID_A"]          # bytes, 16
            ShS_A = msg["ShS_A"]        # bytes, 32
            A_GEN_bl = msg["A_GEN_bl"]  # bytes, 32

            node_id_hex = binascii.hexlify(ID_A).decode()
            entry = self.registry.get(node_id_hex)
            if entry is None:
                self._send_error("Unknown node ID: " + node_id_hex)
                log.warning("[%s] Unknown node: %s", self.addr, node_id_hex)
                return False

            self._peer_id = ID_A
            self._node_label = entry.get("label", node_id_hex[:8])
            node_salt = binascii.unhexlify(entry["node_salt"])

            # Server computes its HKDF contribution using the node's salt
            ShS_B = hkdf(node_salt, self.server_key, length=DIGEST_SIZE)
            B_GEN_bl = h_sha256(concat(node_salt, self.server_id))

            # Shared secret and genesis block
            self._ShS_AB = h_sha256(concat(ShS_A, ShS_B))
            GEN_bl = h_sha256(concat(A_GEN_bl, B_GEN_bl))
            self._PRV_bl = GEN_bl

            send_msg(self.conn, {
                "type": "rot_ack",
                "ShS_B": ShS_B,
                "B_GEN_bl": B_GEN_bl,
                "ID_B": self.server_id,
            })

            self.metrics['p2_us'] = (time.perf_counter_ns() - t0) // 1000
            log.info("[%s] P2 OK node=%s (%d us)",
                     self.addr, self._node_label, self.metrics['p2_us'])
            return True

        except Exception as e:
            self.metrics['errors'].append("P2: " + str(e))
            log.error("[%s] P2 failed: %s", self.addr, e)
            self._send_error("P2 failed: " + str(e))
            return False

    # -----------------------------------------------------------------------
    # P3 — Session Establishment
    # -----------------------------------------------------------------------

    def _run_p3(self, msg):
        t0 = time.perf_counter_ns()
        try:
            # --- Process ses_init ---
            INIT_cif = msg["INIT_cif"]
            plain = decrypt_gcm(INIT_cif, self._ShS_AB)

            PREINIT_bl_rx = plain[0:DIGEST_SIZE]
            RDN1 = plain[DIGEST_SIZE:DIGEST_SIZE + ID_SIZE]
            # TS = plain[DIGEST_SIZE + ID_SIZE:] — not validated in this implementation

            # Verify PREINIT_bl
            INIT_reg = concat(RDN1, plain[DIGEST_SIZE + ID_SIZE:],
                               self._peer_id, self.server_id, self._ShS_AB)
            A_INIT_bl = h_sha256(INIT_reg)
            PREINIT_bl = h_sha256(concat(self._PRV_bl, A_INIT_bl))
            if PREINIT_bl != PREINIT_bl_rx:
                raise RuntimeError("PREINIT_bl verification failed")

            # Generate session parameters
            SES_id = os.urandom(ID_SIZE)
            RDN2 = os.urandom(ID_SIZE)
            SES_key = h_sha256(concat(RDN2, self._ShS_AB))

            # Encrypt ses_reply
            SES_cif = encrypt_gcm(
                concat(SES_id, RDN2, RDN1, PREINIT_bl_rx), self._ShS_AB)

            # Store pending P3 state
            self._p3_pending = {
                "SES_id": SES_id, "RDN2": RDN2, "SES_key": SES_key,
                "PREINIT_bl": PREINIT_bl, "INIT_reg": INIT_reg,
                "RDN1_rx": RDN1,
            }

            send_msg(self.conn, {"type": "ses_reply", "SES_cif": SES_cif})

            # --- Process ses_conf ---
            conf_msg = recv_msg(self.conn)
            if conf_msg.get("type") == "error":
                raise RuntimeError("Client P3 error: " + conf_msg.get("error", "?"))
            if conf_msg.get("type") != "ses_conf":
                raise RuntimeError("Expected ses_conf, got: " + conf_msg.get("type", "?"))

            CONF_cif = conf_msg["CONF_cif"]
            conf_tag_rx = decrypt_gcm(CONF_cif, SES_key)

            conf_expected = h_sha256(
                concat(SES_key, SES_id, RDN1, RDN2))
            if conf_tag_rx != conf_expected:
                raise RuntimeError("conf_tag mismatch")

            # Finalize session state
            INIT_bl = h_sha256(concat(self._PRV_bl, SES_id, PREINIT_bl))
            self._PRV_bl = INIT_bl
            self._SES_id = SES_id
            self._SES_key = SES_key
            self._session_active = True
            self._M_num_expected = 0

            send_msg(self.conn, {"type": "ses_conf_ack", "SES_id": SES_id})

            # Trust score starts at 0.8 after successful mutual authentication.
            self._trust_score = 0.8
            self._consecutive_anomalies = 0

            self.metrics['p3_us'] = (time.perf_counter_ns() - t0) // 1000
            log.info("[%s] P3 OK (%d us)", self.addr, self.metrics['p3_us'])
            return True

        except Exception as e:
            self.metrics['errors'].append("P3: " + str(e))
            log.error("[%s] P3 failed: %s", self.addr, e)
            self._send_error("P3 failed: " + str(e))
            return False

    # -----------------------------------------------------------------------
    # P4 — Data Exchange loop
    # -----------------------------------------------------------------------

    def _run_data_loop(self):
        """Receive sensor data packets until logout or error."""
        log.info("[%s] P4 data loop started", self.addr)
        while self._session_active:
            try:
                msg = recv_msg(self.conn, timeout=30.0)
                msg_type = msg.get("type")

                if msg_type == "dx_msg":
                    self._handle_p4(msg)

                elif msg_type == "logout_req":
                    self._handle_p5(msg)
                    break

                else:
                    log.warning("[%s] Unexpected message type: %s", self.addr, msg_type)

            except TimeoutError:
                log.info("[%s] P4 timeout — closing session", self.addr)
                break

    def _handle_p4(self, msg):
        t0 = time.perf_counter_ns()
        try:
            MSG_cif = msg["MSG_cif"]
            MSG_reg_bytes = decrypt_gcm(MSG_cif, self._SES_key)

            fields = _parse_tlv(MSG_reg_bytes, 6)
            if fields is None:
                raise RuntimeError("MSG_reg malformed")

            SMG_reg_rx, TS_b, m_b, ID_A, PREG_bl_rx, A_REG_bl = fields
            M_num = struct.unpack(">I", m_b)[0]
            TS_int = struct.unpack(">Q", TS_b)[0]

            # Verify hash chain
            REG_reg = _make_reg_reg(
                SMG_reg_rx, TS_b, M_num, ID_A, self.server_id, self._SES_id)
            PRE_REG_bl = h_sha256(REG_reg)
            PREG_bl = h_sha256(concat(self._PRV_bl, PRE_REG_bl))

            if PREG_bl != PREG_bl_rx:
                raise RuntimeError("PREG_bl mismatch at M#{}".format(M_num))

            # Chain asymmetry: ESP32 computes A_REG_bl = H(PREG_bl || node_KEY) using
            # its long-term PSK; server computes B_REG_bl = H(PREG_bl || SES_key) using
            # the session key. Each side contributes a different secret to REG_bl.
            # The node's PSK binds entries to device identity (auditability); the server's
            # session key provides per-session uniqueness (forward secrecy for server side).
            # Final REG_bl = H(A_REG_bl || B_REG_bl) is computed identically on both sides.
            B_REG_bl = h_sha256(concat(PREG_bl, self._SES_key))  # server contribution
            REG_bl = h_sha256(concat(A_REG_bl, B_REG_bl))

            # Advance chain
            prev_prv = self._PRV_bl
            self._PRV_bl = REG_bl
            self._M_num_expected = M_num + 1

            self._chain.append({
                'index': len(self._chain) + 1,
                'M_num': M_num,
                'TS': TS_int,
                'REG_bl': REG_bl.hex(),
                'node': self._node_label,
            })

            # Parse sensor data — cross-node anomaly check before recording.
            try:
                header_len = struct.unpack(">I", SMG_reg_rx[:4])[0]
                raw_json = SMG_reg_rx[4:4 + header_len]
                sensor_data = json.loads(raw_json.decode())
                sensor_data['node'] = self._node_label
                sensor_data['M_num'] = M_num

                # Anomaly detection: compare V_bus against cross-node median.
                # Requires ≥ 2 other nodes to have data (avoids false positives
                # when only one node is connected).
                median_v = self.data_store.get_median_v_bus(
                    exclude_node=self._node_label)
                if median_v is not None:
                    v_reported = sensor_data.get('V_bus', median_v)
                    if abs(v_reported - median_v) > self._V_BUS_ANOMALY_THRESHOLD:
                        self._update_trust('sensor_anomaly')
                    else:
                        self._update_trust('sensor_ok')

                # Embed current trust score in the reading so the EMS coordinator
                # can read it directly from DataStore without additional coupling.
                sensor_data['trust_score'] = self._trust_score

                self.data_store.record(self._node_label, sensor_data)
            except Exception as parse_err:
                log.warning("[%s] Sensor parse error: %s", self.addr, parse_err)

            # Send P4 response before checking isolation (response must be sent
            # even if we are about to drop — otherwise the node stalls).
            RES_cif = encrypt_gcm(B_REG_bl, self._SES_key)
            send_msg(self.conn, {"type": "dx_res", "RES_cif": RES_cif, "M_num": M_num})

            # Optionally send demand setpoint after P4 response.
            setpoint = self.ems.get_setpoint(self._node_label)
            if setpoint is not None:
                send_msg(self.conn, {
                    "type": "setpoint",
                    "data": {"p_demand": setpoint},
                })

            # Drop connection if trust fell below threshold this cycle.
            if self._trust_score < _TRUST_DROP_THRESHOLD:
                self._drop_connection("trust threshold crossed after P4")

            dur = (time.perf_counter_ns() - t0) // 1000
            self.metrics['p4_count'] += 1
            self.metrics['p4_total_us'] += dur

        except Exception as e:
            self._update_trust('security_error')
            self.metrics['errors'].append("P4: " + str(e))
            log.error("[%s] P4 error: %s", self.addr, e)
            self._send_error("P4 failed: " + str(e))
            if self._trust_score < _TRUST_DROP_THRESHOLD:
                self._drop_connection("trust threshold crossed after P4 security error")
            else:
                self._session_active = False

    # -----------------------------------------------------------------------
    # P5 — Logout
    # -----------------------------------------------------------------------

    def _handle_p5(self, msg):
        t0 = time.perf_counter_ns()
        try:
            A_END_cif = msg["A_END_cif"]
            plain = decrypt_gcm(A_END_cif, self._SES_key)

            fields = _parse_tlv(plain, 2)
            if fields is None:
                raise RuntimeError("P5: logout payload malformed")

            END_reg_rx, A_END_bl_rx = fields
            end_fields = _parse_tlv(END_reg_rx, 4)
            if end_fields is None:
                raise RuntimeError("P5: END_reg malformed")

            ID_A_rx = end_fields[2]
            expected_a_end = h_sha256(
                concat(END_reg_rx, self._SES_id, ID_A_rx))
            if expected_a_end != A_END_bl_rx:
                raise RuntimeError("P5: A_END_bl verification failed")

            PRE_END_bl = h_sha256(concat(self._PRV_bl, A_END_bl_rx))
            B_END_bl = h_sha256(concat(PRE_END_bl, self._SES_key))
            B_END_cif = encrypt_gcm(B_END_bl, self._SES_key)

            send_msg(self.conn, {"type": "logout_res", "B_END_cif": B_END_cif})

            self._session_active = False
            self.metrics['p5_us'] = (time.perf_counter_ns() - t0) // 1000
            log.info("[%s] P5 logout OK (%d us)", self.addr, self.metrics['p5_us'])

        except Exception as e:
            self.metrics['errors'].append("P5: " + str(e))
            log.error("[%s] P5 error: %s", self.addr, e)
            self._session_active = False

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # Trust management
    # -----------------------------------------------------------------------

    def _update_trust(self, event: str) -> None:
        """
        Adjust trust score based on a security event.

        Events:
          'security_error'  — P4 chain or crypto failure   → −0.30
          'sensor_anomaly'  — V_bus deviates from median    → −0.40 after
                              _V_BUS_ANOMALY_WINDOW consecutive anomalies
          'sensor_ok'       — reading within normal range   → reset anomaly counter
        """
        if event == 'security_error':
            self._trust_score = max(0.0, self._trust_score - 0.30)
            self.metrics['trust_events'].append(
                {'event': event, 'score': self._trust_score,
                 'M_num': self.metrics['p4_count']})
            log.warning("[%s] Trust event: %s → score=%.2f",
                        self.addr, event, self._trust_score)

        elif event == 'sensor_anomaly':
            self._consecutive_anomalies += 1
            if self._consecutive_anomalies >= self._V_BUS_ANOMALY_WINDOW:
                self._trust_score = max(0.0, self._trust_score - 0.40)
                self._consecutive_anomalies = 0
                self.metrics['trust_events'].append(
                    {'event': event, 'score': self._trust_score,
                     'M_num': self.metrics['p4_count']})
                log.warning("[%s] Trust event: %s (×%d) → score=%.2f",
                            self.addr, event, self._V_BUS_ANOMALY_WINDOW,
                            self._trust_score)

        elif event == 'sensor_ok':
            self._consecutive_anomalies = 0

    def get_trust_score(self) -> float:
        return self._trust_score

    def _drop_connection(self, reason: str) -> None:
        """
        Close the TCP connection to force the node to re-authenticate.
        Trust score is NOT reset here — it resets to 0.8 only after a
        successful P2/P3 re-authentication in a new session.
        """
        log.warning("[%s] Dropping connection (node=%s): %s",
                    self.addr, self._node_label, reason)
        self._session_active = False
        try:
            self.conn.close()
        except Exception:
            pass

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _send_error(self, message):
        try:
            send_msg(self.conn, {"type": "error", "error": message})
        except Exception:
            pass

    def get_metrics(self):
        avg_p4 = (self.metrics['p4_total_us'] // self.metrics['p4_count']
                  if self.metrics['p4_count'] > 0 else 0)
        return {
            **self.metrics,
            'p4_avg_us': avg_p4,
            'node': self._node_label,
            'chain_length': len(self._chain),
            'trust_score': self._trust_score,
            'trust_event_count': len(self.metrics['trust_events']),
        }
