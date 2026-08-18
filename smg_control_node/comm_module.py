"""
Communication module for SMG control node — MINIMAL BENCH VERSION.

Stripped to absolute minimum to avoid lwIP heap fragmentation and PHY
calibration corruption. Only essential WiFi/TCP init with single active()
cycle and one connect() attempt per boot.

Usage:
    from comm_module import CommManager
    comm = CommManager(config)
    comm.connect_wifi()
    sock = comm.connect_tcp()
"""

import time
import socket
import network
import ujson

try:
    import errno as _errno_mod
    _EAGAIN = _errno_mod.EAGAIN
except ImportError:
    _EAGAIN = 11

_DEFAULT_SOCK_TIMEOUT = 10
_RECV_MSG_MAX = 16384


class CommManager:
    """Minimal WiFi/TCP manager for bench testing."""

    def __init__(self, config, wlan=None):
        import gc
        gc.collect()
        self.config = config
        if wlan is not None:
            self.wlan = wlan
        else:
            self.wlan = network.WLAN(network.STA_IF)
            self.wlan.active(True)
            self.wlan.config(pm=self.wlan.PM_NONE)
            time.sleep(1)
        self._sock = None
        self._connected = False
        self._wifi_connect_count = 0
        self._tcp_connect_count = 0
        self._reconnect_count = 0

    def connect_wifi(self, timeout=30):
        """Connect to WiFi AP. Single active() cycle, single connect()."""
        if self.wlan.isconnected() and self.wlan.ifconfig()[0] != '0.0.0.0':
            ip = self.wlan.ifconfig()[0]
            print("[comm] WiFi already connected: {}".format(ip))
            self._connected = True
            return True

        ssid = self.config.get("wifi_ssid", "")
        password = self.config.get("wifi_pass", "")
        print("[comm] Connecting to WiFi '{}'...".format(ssid))

        self._wifi_connect_count += 1

        try:
            self.wlan.connect(ssid, password)
        except Exception as e:
            print("[comm] connect() error: {}".format(e))
            return False

        start = time.time()
        last_st = None
        no_ap_count = 0
        while True:
            try:
                if self.wlan.isconnected():
                    ip = self.wlan.ifconfig()[0]
                    if ip != '0.0.0.0':
                        print("[comm] WiFi connected: {}".format(ip))
                        self._connected = True
                        return True
            except Exception:
                pass

            try:
                cur_st = self.wlan.status()
                if cur_st != last_st:
                    print("[comm] WiFi status: {}->{}".format(last_st, cur_st))
                    # 202 = STAT_NO_AP_FOUND; accumulate total occurrences.
                    # The stuck pattern alternates 1001<->202, so count total
                    # 202 transitions (not consecutive) — 3+ = PMKSA driver stuck.
                    if cur_st == 202:
                        no_ap_count += 1
                        if no_ap_count >= 3:
                            print("[comm] Persistent NO_AP_FOUND ({}) — fast fail".format(
                                no_ap_count))
                            return False
                    last_st = cur_st
            except Exception:
                pass

            if time.time() - start > timeout:
                break
            time.sleep(0.2)

        try:
            st = self.wlan.status()
            print("[comm] WiFi timed out (status={})".format(st))
        except Exception:
            print("[comm] WiFi timed out")
        return False

    def connect_tcp(self, timeout=10):
        """Establish TCP socket. Single attempt, no retries."""
        server_ip = self.config.get("server_ip", "10.42.0.1")
        server_port = self.config.get("server_port", 5000)

        print("[comm] Connecting TCP to {}:{}".format(server_ip, server_port))

        import gc
        gc.collect()

        self._tcp_connect_count += 1

        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.settimeout(timeout)
            sock.connect((server_ip, server_port))
            sock.settimeout(_DEFAULT_SOCK_TIMEOUT)
            self._sock = sock
            print("[comm] TCP connected (NODELAY=1)")
            return sock
        except Exception as e:
            try:
                print("[comm] TCP failed: {} (wlan={}, status={})".format(
                    e, self.wlan.isconnected(), self.wlan.status()))
            except Exception:
                print("[comm] TCP failed: {}".format(e))
            try:
                sock.close()
            except Exception:
                pass
            return None

    def send_json(self, sock, data):
        """Send JSON with 4-byte big-endian length header."""
        try:
            body = ujson.dumps(data).encode()
            header = len(body).to_bytes(4, 'big')
            sock.sendall(header + body)
            return True
        except Exception as e:
            print("[comm] Send failed: {}".format(e))
            return False

    def recv_json(self, sock, timeout=5):
        """Receive JSON with 4-byte big-endian length header."""
        try:
            sock.settimeout(timeout)
            hdr = b""
            while len(hdr) < 4:
                chunk = sock.recv(4 - len(hdr))
                if not chunk:
                    return None
                hdr += chunk

            msg_len = int.from_bytes(hdr, 'big')
            if msg_len == 0 or msg_len > _RECV_MSG_MAX:
                return None

            body = b""
            while len(body) < msg_len:
                chunk = sock.recv(msg_len - len(body))
                if not chunk:
                    return None
                body += chunk

            return ujson.loads(body.decode())

        except OSError as e:
            if e.args[0] == _EAGAIN:
                return None
            print("[comm] Recv failed: {}".format(e))
            return None
        except Exception as e:
            print("[comm] Recv error: {}".format(e))
            return None

    def reconnect(self):
        """Reconnect WiFi and TCP. If WiFi is still up, skip WiFi disconnect (TCP-only reconnect)."""
        self._reconnect_count += 1
        print("[comm] Reconnecting (attempt {})...".format(self._reconnect_count))

        self.close()

        # If WiFi is still connected, only reconnect TCP — don't disrupt the association.
        if self.wlan.isconnected() and self.wlan.ifconfig()[0] != '0.0.0.0':
            print("[comm] WiFi still up, TCP-only reconnect...")
            time.sleep(1)
            sock = self.connect_tcp()
            if sock is not None:
                print("[comm] TCP-only reconnect OK")
                return sock
            # TCP failed even with WiFi up — fall through to full WiFi reconnect.

        # WiFi is down or TCP-only failed — disconnect and reconnect WiFi.
        try:
            self.wlan.disconnect()
        except Exception:
            pass
        time.sleep(2)

        if self.connect_wifi(timeout=15):
            sock = self.connect_tcp()
            if sock is not None:
                print("[comm] WiFi+TCP reconnect OK")
                return sock

        # Full driver reset as last resort.
        print("[comm] Simple reconnect failed, doing full reset...")
        try:
            self.wlan.active(False)
            time.sleep(2)
            self.wlan.active(True)
            time.sleep(2)
        except Exception:
            pass

        if not self.connect_wifi(timeout=20):
            return None

        return self.connect_tcp()

    def is_connected(self):
        """Check WiFi and TCP status."""
        if not self.wlan.isconnected():
            return False
        if self._sock is None:
            return False
        try:
            self._sock.settimeout(0)
            data = self._sock.recv(1)
            self._sock.settimeout(_DEFAULT_SOCK_TIMEOUT)
            return data != b''
        except OSError as e:
            self._sock.settimeout(_DEFAULT_SOCK_TIMEOUT)
            return e.args[0] == _EAGAIN
        except Exception:
            return False

    def close(self):
        """Close TCP socket."""
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None

    def get_ip(self):
        """Return local IP address."""
        if self.wlan.isconnected():
            return self.wlan.ifconfig()[0]
        return ""

    def get_stats(self):
        """Return connection statistics."""
        return {
            'wifi_connected': self.wlan.isconnected(),
            'ip': self.get_ip(),
            'wifi_connect_count': self._wifi_connect_count,
            'tcp_connect_count': self._tcp_connect_count,
            'reconnect_count': self._reconnect_count,
        }
