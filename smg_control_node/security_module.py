"""
Security module for SMG control node.

Wraps SecureNode protocol (P1-P6) for the SMG secondary node role.
Handles identity loading from NVS, protocol handshake, data exchange,
logout, and timing metrics collection for the methodology case study.

Usage:
    from security_module import SecurityManager
    sec = SecurityManager(config, identity)
    sec.handshake(sock)
    sec.send_data(sock, sensor_json)
    sec.logout(sock)
"""

import time

try:
    import ubinascii
except ImportError:
    import binascii as ubinascii

from secure_node import SecureNode, send_msg, recv_msg, ts_int


def _to_bytes(v):
    """
    Ensure a protocol field value is bytes.

    recv_msg/unpack_payload auto-decodes any key listed in BYTES_FIELDS from
    hex string → bytes. This helper handles the case where the field arrives
    already as bytes (normal path) or as a hex string (e.g., when testing
    against a server that does not use pack_payload).
    """
    return v if isinstance(v, bytes) else ubinascii.unhexlify(v)


class SecurityManager:
    """Manages SecureNode protocol for SMG secondary node."""

    def __init__(self, config, identity):
        self.config = config
        self.identity = identity
        self.node = None
        self._session_established = False
        self.metrics = {
            'p2_duration_us': 0,
            'p3_duration_us': 0,
            'p4_total_us': 0,
            'p4_count': 0,
            'p5_duration_us': 0,
            'handshake_total_us': 0,
            'chain_length': 0,
            'errors': [],
        }
        # Welford online variance for P4 per-message latency
        self._p4_mean_f = 0.0
        self._p4_M2 = 0.0
        self._pending_setpoint = None

    def _init_node(self):
        """Initialize SecureNode with identity from NVS."""
        node_id = self.identity.get("node_id", b"")
        node_key = self.identity.get("node_key", b"")

        if not node_id or not node_key:
            raise RuntimeError("Identity not provisioned. Run provisioning first.")

        self.node = SecureNode(
            port=self.config.get("server_port", 5000),
            hash_func='sha256',
            cipher_mode='gcm',
            device_id=node_id,
            key=node_key,
        )
        self.node.p1_provision(device_id=node_id, key=node_key)

    def precompute_p2(self):
        """Instantiate SecureNode and compute P2 values before creating TCP socket.

        This must be called BEFORE comm.connect_tcp() so that the heavy
        crypto operations (HKDF, SHA-256, AES-GCM key derivation) do not
        corrupt an existing lwIP socket.
        """
        if self.node is None:
            self._init_node()

        salt = self.identity.get("node_salt")
        if salt is None:
            raise RuntimeError("No node_salt in identity")

        print("[security] Pre-computing P2 values...")
        self._shs_a, self._gen_a = self.node.p2_initiator_compute(salt)
        print("[security] P2 pre-compute OK")

    def handshake(self, sock):
        """
        Execute P2 (Root of Trust) and P3 (Session Establishment) with primary node.

        If P2 values were pre-computed via precompute_p2(), they are reused.
        Otherwise, P2 is computed here (not recommended — may corrupt socket).

        Parameters:
            sock: Connected TCP socket to primary node.

        Returns:
            True if handshake succeeded, False otherwise.
        """
        if self.node is None:
            self._init_node()

        handshake_start = time.ticks_us()

        try:
            p2_start = time.ticks_us()

            if hasattr(self, '_shs_a') and hasattr(self, '_gen_a'):
                shs_a = self._shs_a
                gen_a = self._gen_a
            else:
                salt = self.identity.get("node_salt")
                if salt is None:
                    raise RuntimeError("No node_salt in identity")
                shs_a, gen_a = self.node.p2_initiator_compute(salt)

            rot_msg = {
                "type": "rot_hello",
                "ShS_A": ubinascii.hexlify(shs_a).decode(),
                "A_GEN_bl": ubinascii.hexlify(gen_a).decode(),
                "ID_A": ubinascii.hexlify(self.node.ID).decode(),
            }
            print("[security] Sending rot_hello...")
            send_msg(sock, rot_msg)
            print("[security] rot_hello sent, waiting for rot_ack...")

            rot_resp = recv_msg(sock)
            if rot_resp.get("type") == "error":
                raise RuntimeError("P2 error: {}".format(rot_resp.get("error")))

            shs_b    = _to_bytes(rot_resp["ShS_B"])
            b_gen_bl = _to_bytes(rot_resp["B_GEN_bl"])
            id_b     = _to_bytes(rot_resp["ID_B"])
            self.node._peer_id = id_b

            self.node.p2_finalize(shs_a, shs_b, gen_a, b_gen_bl)

            p2_end = time.ticks_us()
            self.metrics['p2_duration_us'] = time.ticks_diff(p2_end, p2_start)

            p3_start = time.ticks_us()

            ses_msg = self.node.p3_initiator_prepare(self.node._peer_id)
            send_msg(sock, ses_msg)

            ses_resp = recv_msg(sock)
            if ses_resp.get("type") == "error":
                raise RuntimeError("P3 error: {}".format(ses_resp.get("error")))

            # ses_resp["SES_cif"] is already bytes (unpack_payload converts BYTES_FIELDS).
            # p3_initiator_finalize decrypts, verifies RDN1 echo, finalizes session state,
            # and builds the confirmation using a random AES-GCM nonce (via encrypt_gcm).
            conf_msg = self.node.p3_initiator_finalize(ses_resp)
            send_msg(sock, conf_msg)

            conf_ack = recv_msg(sock)

            p3_end = time.ticks_us()
            self.metrics['p3_duration_us'] = time.ticks_diff(p3_end, p3_start)

            handshake_end = time.ticks_us()
            self.metrics['handshake_total_us'] = time.ticks_diff(handshake_end, handshake_start)

            self._session_established = True
            print("[security] Handshake OK: P2={}us P3={}us".format(
                self.metrics['p2_duration_us'], self.metrics['p3_duration_us']))
            return True

        except Exception as e:
            self.metrics['errors'].append("handshake: {}".format(str(e)))
            print("[security] Handshake FAILED: {}".format(e))
            return False

    def send_data(self, sock, sensor_json):
        """
        Send sensor data via P4 (Data Exchange).

        Parameters:
            sock: Connected TCP socket.
            sensor_json: JSON bytes with sensor readings.

        Returns:
            True if send succeeded, False otherwise.
        """
        if not self._session_established or self.node is None:
            self.metrics['errors'].append("send_data: no session")
            return False

        try:
            t0 = time.ticks_us()

            dx_msg = self.node.p4_send(sensor_json)
            send_msg(sock, dx_msg)

            # Drain stale messages (e.g. setpoint from previous cycle)
            # until the expected dx_res response is received.
            while True:
                dx_resp = recv_msg(sock)
                rtype = dx_resp.get("type")
                if rtype == "error":
                    raise RuntimeError("P4 error: {}".format(dx_resp.get("error")))
                elif rtype == "setpoint":
                    self._pending_setpoint = dx_resp.get("data")
                    continue
                elif rtype == "dx_res":
                    break
                else:
                    raise RuntimeError("Unexpected message type: {}".format(rtype))

            res_cif = _to_bytes(dx_resp["RES_cif"])
            m_num = dx_resp.get("M_num", self.node._M_num)
            res_dict = {"RES_cif": res_cif, "M_num": m_num}
            self.node.p4_finalize(res_dict)

            t1 = time.ticks_us()
            duration = time.ticks_diff(t1, t0)

            self.metrics['p4_total_us'] += duration
            self.metrics['p4_count'] += 1
            n = self.metrics['p4_count']
            delta = duration - self._p4_mean_f
            self._p4_mean_f += delta / n
            self._p4_M2 += delta * (duration - self._p4_mean_f)
            # _M_num counts messages sent; self.node.chain is empty on the
            # initiator (rolling PRV_bl only — full chain lives on the server).
            self.metrics['chain_length'] = self.node._M_num

            return True

        except Exception as e:
            self.metrics['errors'].append("send_data: {}".format(str(e)))
            print("[security] Send FAILED: {}".format(e))
            return False

    def receive_setpoint(self, sock):
        """
        Poll for a demand setpoint from the primary node.

        Non-blocking: returns None if no setpoint available.

        Parameters:
            sock: Connected TCP socket.

        Returns:
            dict with setpoint data or None.
        """
        if not self._session_established or self.node is None:
            return None

        # Return cached setpoint from send_data's drain loop first
        if self._pending_setpoint is not None:
            sp = self._pending_setpoint
            self._pending_setpoint = None
            return sp

        try:
            sock.settimeout(0)
            try:
                msg = recv_msg(sock)
            except Exception:
                return None
            finally:
                sock.settimeout(self.config.get('socket_timeout', 5))

            if msg and msg.get("type") == "setpoint":
                return msg.get("data")
            return None

        except Exception:
            return None

    def logout(self, sock):
        """
        Execute P5 (Logout) to cleanly terminate the session.

        Parameters:
            sock: Connected TCP socket.
        """
        if not self._session_established or self.node is None:
            return

        try:
            t0 = time.ticks_us()

            logout_msg = self.node.p5_logout_initiate()
            send_msg(sock, logout_msg)

            logout_resp = recv_msg(sock)
            if logout_resp.get("type") == "logout_res":
                b_end_cif = _to_bytes(logout_resp["B_END_cif"])
                self.node.p5_logout_finalize({"B_END_cif": b_end_cif})

            t1 = time.ticks_us()
            self.metrics['p5_duration_us'] = time.ticks_diff(t1, t0)

            self._session_established = False
            print("[security] Logout OK: {}us".format(self.metrics['p5_duration_us']))

        except Exception as e:
            self.metrics['errors'].append("logout: {}".format(str(e)))
            print("[security] Logout FAILED: {}".format(e))

    def get_metrics(self):
        """Return collected security metrics for case study reporting."""
        import math
        metrics = dict(self.metrics)
        n = self.metrics['p4_count']
        metrics['p4_avg_us'] = round(self.metrics['p4_total_us'] / n, 1) if n > 0 else 0.0
        metrics['p4_std_us'] = (
            round(math.sqrt(self._p4_M2 / (n - 1)), 1) if n > 1 else 0.0
        )

        if self.node:
            # chain_length = messages sent (initiator never stores chain entries;
            # server holds the authoritative audit log).
            metrics['chain_length'] = self.node._M_num
            metrics['chain_integrity'] = self.node.verify_chain()
            metrics['session_active'] = self.node._session_active

            timings = self.node.get_timings_summary()
            metrics['phase_timings'] = timings

            import gc
            gc.collect()
            metrics['gc_mem_free'] = gc.mem_free()

        return metrics

    def print_metrics(self):
        """Print formatted metrics summary."""
        m = self.get_metrics()
        print("\n" + "=" * 50)
        print("  SECURITY METRICS SUMMARY")
        print("=" * 50)
        print("  P2 (Root of Trust) : {} us".format(m['p2_duration_us']))
        print("  P3 (Session)       : {} us".format(m['p3_duration_us']))
        print("  Handshake total    : {} us".format(m['handshake_total_us']))
        print("  P4 transactions    : {} (avg {} ±{} us)".format(
            m['p4_count'], m['p4_avg_us'], m['p4_std_us']))
        print("  P5 (Logout)        : {} us".format(m['p5_duration_us']))
        print("  Chain length       : {}".format(m['chain_length']))
        print("  Chain integrity    : {}".format(
            'OK' if m.get('chain_integrity') else 'N/A'))
        print("  Free heap          : {} bytes".format(m.get('gc_mem_free', 0)))
        if m['errors']:
            print("  Errors             : {}".format(len(m['errors'])))
            for err in m['errors'][-3:]:
                print("    - {}".format(err))
        print("=" * 50)
