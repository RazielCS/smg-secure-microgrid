"""
Configuration manager for SMG control node firmware.

Loads credentials, calibration, and settings from /config.json on ESP32 flash.
Manages identity parameters (node_id, node_uuid, node_key, node_salt) in NVS.
If config or identity is missing, enters UART provisioning mode for first-boot setup.

NVS namespace: "smg_identity"
Config path: "/config.json"
"""

import sys
import json

try:
    from ubinascii import unhexlify, hexlify
except ImportError:
    from binascii import unhexlify, hexlify

try:
    import os
except ImportError:
    pass

# --- Hardware Pin Constants (NodeMCU ESP32) ---
PIN_ADC_BUS_VOLTAGE = 36   # VP  - ADC1 CH0
PIN_ADC_GEN_VOLTAGE = 39   # VN  - ADC1 CH3
PIN_ADC_BUS_CURRENT = 34   # ADC1 CH6
PIN_ADC_GEN_CURRENT = 35   # ADC1 CH7
PIN_ADC_NODE_CURRENT = 32  # ADC1 CH4 — ACS712 node self-consumption (12V buck input line)
PIN_PWM_BUS   = 25          # LEDC CH0 - SSR Bus (D25 / GPIO25)
PIN_PWM_SOLAR = 26          # LEDC CH1 - SSR Solar (D26 / GPIO26)
PIN_PWM_GEN   = 27          # LEDC CH2 - SSR Generator (D27 / GPIO27)
PIN_PWM_SSRS  = (PIN_PWM_BUS, PIN_PWM_SOLAR, PIN_PWM_GEN)  # Bus, Solar, Gen
PIN_LED_RED    = 23         # Red LED    (D23 / GPIO23) — R1=220Ω
PIN_LED_YELLOW = 22         # Yellow LED (D22 / GPIO22) — R3=220Ω
PIN_LED_GREEN  = 21         # Green LED  (D21 / GPIO21) — R2=220Ω
PIN_BUTTON     = 18         # SW1 InitPulse (D18 / GPIO18), active HIGH, R10=10kΩ pull-down

# --- NVS Configuration ---
NVS_NAMESPACE = "smg_identity"
# All keys stored under the NVS namespace (used for clear_identity).
NVS_KEYS = ("node_id", "node_uuid", "node_key", "node_salt", "server_id", "is_primary",
            "wifi_ssid", "wifi_pass")
# Cryptographic identity keys only — WiFi credentials excluded so get_identity()
# does not mix credentials into the identity dict passed to SecurityManager.
_IDENTITY_KEYS = ("node_id", "node_uuid", "node_key", "node_salt", "server_id", "is_primary")
# Binary keys whose NVS values are raw bytes (not UTF-8 strings).
_BINARY_NVS_KEYS = ("node_id", "node_uuid", "node_key", "node_salt", "server_id", "is_primary")

# --- Config Path ---
_CONFIG_PATH = "/config.json"

# --- Required config fields (wifi_ssid/wifi_pass may come from NVS) ---
_REQUIRED_FIELDS = [
    "node_role", "wifi_ssid", "wifi_pass", "server_ip", "server_port",
    "read_interval_s", "send_interval_s"
]
_NVS_CREDENTIAL_FIELDS = ("wifi_ssid", "wifi_pass")


def get_wifi_credentials():
    """
    Load WiFi credentials from NVS.

    Returns:
        dict with keys wifi_ssid and wifi_pass (str), or None if not provisioned.
    """
    ssid_raw = _nvs_get(NVS_NAMESPACE, "wifi_ssid", binary=True)
    pass_raw = _nvs_get(NVS_NAMESPACE, "wifi_pass", binary=True)
    if ssid_raw and pass_raw:
        ssid = ssid_raw.decode('utf-8') if isinstance(ssid_raw, bytes) else ssid_raw
        passw = pass_raw.decode('utf-8') if isinstance(pass_raw, bytes) else pass_raw
        return {"wifi_ssid": ssid, "wifi_pass": passw}
    return None


def _validate_config(config):
    """Check for missing required fields. Returns list of missing field names."""
    missing = []
    for field in _REQUIRED_FIELDS:
        if field not in config:
            missing.append(field)

    cal = config.get("calibration", {})
    for sensor in ("acs712_bus", "acs712_gen", "vdiv_bus", "vdiv_gen"):
        if sensor not in cal:
            missing.append("calibration.{}".format(sensor))

    ems = config.get("ems", {})
    for ef in ("v_bus_nominal", "p_demand_default", "pwm_min", "pwm_max", "pwm_freq"):
        if ef not in ems:
            missing.append("ems.{}".format(ef))

    return missing


def _check_placeholder(config):
    """Return True if config still contains placeholder values."""
    return ("REPLACE" in config.get("wifi_ssid", "") or
            "REPLACE" in config.get("wifi_pass", ""))


def _nvs_available():
    """Check if NVS module is available."""
    try:
        import nvs
        return True
    except ImportError:
        pass
    try:
        from esp32 import NVS
        return True
    except ImportError:
        pass
    return False


def _nvs_get(namespace, key, default=None, binary=False):
    """
    Read a value from NVS.

    Parameters:
        binary: If True, return bytes. When the nvs module stores bytes as a
                latin-1 string (via _nvs_set), re-encode the string to recover
                the original bytes. The esp32.NVS path returns bytes natively.
    """
    try:
        import nvs
        val = nvs.get_str(namespace, key)
        if val is not None and binary:
            # _nvs_set stored bytes as value.decode('latin-1'); reverse that here.
            return val.encode('latin-1') if isinstance(val, str) else val
        return val
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from esp32 import NVS
        n = NVS(namespace)
        buf = bytearray(64)
        length = n.get_blob(key, buf)
        if length and length > 0:
            return bytes(buf[:length])
    except ImportError:
        pass
    except Exception:
        pass

    return default


def _nvs_set(namespace, key, value):
    """Write a value to NVS. Tries multiple NVS module interfaces."""
    try:
        import nvs
        if isinstance(value, bytes):
            nvs.set_str(namespace, key, value.decode('latin-1'))
        else:
            nvs.set_str(namespace, key, str(value))
        nvs.commit()
        return True
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from esp32 import NVS
        n = NVS(namespace)
        if isinstance(value, bytes):
            n.set_blob(key, value)
        else:
            n.set_str(key, str(value))
        n.commit()
        return True
    except ImportError:
        pass
    except Exception:
        pass

    return False


def _nvs_delete(namespace, key):
    """Delete a key from NVS."""
    try:
        import nvs
        nvs.erase_key(namespace, key)
        nvs.commit()
        return True
    except ImportError:
        pass
    except Exception:
        pass

    try:
        from esp32 import NVS
        n = NVS(namespace)
        n.erase_key(key)
        n.commit()
        return True
    except ImportError:
        pass
    except Exception:
        pass

    return False


def get_identity():
    """
    Load cryptographic identity parameters from NVS.

    Returns only the identity keys (node_id, node_uuid, node_key, node_salt,
    server_id, is_primary). WiFi credentials are intentionally excluded — use
    get_wifi_credentials() separately to avoid mixing credentials into the
    identity dict passed to SecurityManager.

    Returns:
        dict with keys: node_id, node_uuid, node_key, node_salt, server_id, is_primary
        or None if identity not provisioned.
    """
    identity = {}
    for key in _IDENTITY_KEYS:
        is_bin = key in _BINARY_NVS_KEYS
        val = _nvs_get(NVS_NAMESPACE, key, binary=is_bin)
        if val is not None:
            identity[key] = val

    if "node_id" not in identity or "node_key" not in identity:
        return None

    return identity


def set_identity(node_id, node_uuid, node_key, node_salt, server_id=b'', is_primary=False):
    """
    Store identity parameters in NVS.

    Parameters:
        node_id:     16-byte node identifier (e.g., b'SMG_NODE_01\x00\x00\x00\x00\x00')
        node_uuid:   16-byte UUID-4
        node_key:    32-byte cryptographic PSK (SecureNode uses full bytes, no truncation)
        node_salt:   32-byte salt for HKDF
        server_id:   16-byte primary node ID (empty for primary node itself)
        is_primary:  bool, True if this node is the primary/server
    """
    _nvs_set(NVS_NAMESPACE, "node_id", node_id)
    _nvs_set(NVS_NAMESPACE, "node_uuid", node_uuid)
    _nvs_set(NVS_NAMESPACE, "node_key", node_key)
    _nvs_set(NVS_NAMESPACE, "node_salt", node_salt)
    _nvs_set(NVS_NAMESPACE, "server_id", server_id)
    _nvs_set(NVS_NAMESPACE, "is_primary", b'\x01' if is_primary else b'\x00')
    print("[nvs] Identity stored: node_id={}".format(node_id))


def clear_identity():
    """Remove all identity parameters from NVS."""
    for key in NVS_KEYS:
        _nvs_delete(NVS_NAMESPACE, key)
    print("[nvs] Identity cleared")


def _prompt(label, validate_fn=None, default=None):
    """Prompt for a value via UART. Retry on invalid input, up to 3 times."""
    suffix = " [{}]".format(default) if default is not None else ""
    for attempt in range(3):
        print("  {}{}: ".format(label, suffix), end="")
        try:
            line = sys.stdin.readline().strip()
        except Exception:
            line = ""
        if not line and default is not None:
            return str(default)
        if not line:
            print("    (required, cannot be empty)")
            continue
        if validate_fn is not None:
            err = validate_fn(line)
            if err:
                print("    Invalid: {}".format(err))
                continue
        return line
    return None


def _validate_int(s):
    try:
        int(s)
    except ValueError:
        return "must be an integer"
    return None


def _validate_float(s):
    try:
        float(s)
    except ValueError:
        return "must be a number"
    return None


def _validate_hex_key(s, expected_len=32):
    """Validate a hex-encoded key of expected byte length."""
    try:
        raw = unhexlify(s)
    except ValueError:
        return "invalid hex characters"
    if len(raw) != expected_len:
        return "must be {} hex chars ({} bytes), got {}".format(
            expected_len * 2, expected_len, len(raw))
    return None


def _enter_uart_config():
    """
    Interactive UART provisioning for first-boot configuration.

    Prompts for WiFi, server, identity, and calibration parameters.
    Writes config.json to flash and identity to NVS.
    """
    print()
    print("=" * 50)
    print("  SMG CONTROL NODE PROVISIONING")
    print("  No configuration found. Enter device parameters.")
    print("  Type values and press Enter. Ctrl+C to abort.")
    print("=" * 50)
    print()

    try:
        # Node role
        print("[1/6] Node Role")
        role = _prompt("Role (primary/secondary)", default="secondary")
        if role is None:
            return None
        is_primary = role.lower() == "primary"

        # Network
        print("\n[2/6] Network")
        if is_primary:
            wifi_ssid = _prompt("WiFi AP SSID", default="SMG_Primary_AP")
            wifi_pass = _prompt("WiFi AP Password", default="CHANGE_ME_WIFI_PASSWORD")
            server_ip = "0.0.0.0"
            server_port = 5000
        else:
            wifi_ssid = _prompt("Primary Node AP SSID", default="SMG_Primary_AP")
            wifi_pass = _prompt("Primary Node AP Password", default="CHANGE_ME_WIFI_PASSWORD")
            server_ip = _prompt("Primary Node IP", default="192.168.4.1")
            server_port = _prompt("Server Port", _validate_int, default="5000")
            server_port = int(server_port)

        # Identity
        print("\n[3/6] Identity")
        node_label = _prompt("Node label (e.g., NODE_01)", default="NODE_01")
        if node_label is None:
            return None

        print("  (Keys will be auto-generated. For pre-provisioned nodes,")
        print("   use tools/provision_node.py instead.)")
        auto_gen = _prompt("Auto-generate keys? (y/n)", default="y")
        if auto_gen and auto_gen.lower() == "y":
            try:
                node_key = os.urandom(32)
                node_uuid = os.urandom(16)
                node_salt = os.urandom(32)
            except Exception as e:
                raise RuntimeError(
                    "os.urandom unavailable — cannot generate cryptographic keys safely. "
                    "Use a MicroPython build with os.urandom support, or provision via "
                    "tools/provision_node.py from a host machine. Error: {}".format(e)
                )
        else:
            node_key_hex = _prompt("Node Key (64 hex chars)", lambda s: _validate_hex_key(s, 32))
            if node_key_hex is None:
                return None
            node_key = unhexlify(node_key_hex)

            node_uuid_hex = _prompt("Node UUID (32 hex chars)", lambda s: _validate_hex_key(s, 16))
            if node_uuid_hex is None:
                return None
            node_uuid = unhexlify(node_uuid_hex)

            node_salt_hex = _prompt("Node Salt (64 hex chars)", lambda s: _validate_hex_key(s, 32))
            if node_salt_hex is None:
                return None
            node_salt = unhexlify(node_salt_hex)

        # Calibration
        print("\n[4/6] Sensor Calibration")
        print("  ACS712 (current sensors):")
        acs_vref = _prompt("  Vref (V)", _validate_float, default="2.5")
        acs_sens = _prompt("  Sensitivity (V/A)", _validate_float, default="0.185")
        acs_offset = _prompt("  Offset (A)", _validate_float, default="0.0")

        print("  Voltage divider:")
        vdiv_ratio = _prompt("  Ratio (V_in/V_adc)", _validate_float, default="5.0")
        vdiv_offset = _prompt("  Offset (V)", _validate_float, default="0.0")

        # EMS
        print("\n[5/6] EMS Parameters")
        v_nominal = _prompt("  V_bus nominal (V)", _validate_float, default="12.0")
        p_default = _prompt("  P_demand default (W)", _validate_float, default="50.0")
        pwm_min = _prompt("  PWM min", _validate_int, default="0")
        pwm_max = _prompt("  PWM max", _validate_int, default="1023")

        # Timing
        print("\n[6/6] Timing")
        read_interval = _prompt("  Read interval (s)", _validate_float, default="0.333")
        send_interval = _prompt("  Send interval (s)", _validate_float, default="1.0")

        # Build node_id (16 bytes, padded)
        node_id_bytes = node_label.encode('utf-8')[:16]
        node_id_bytes = node_id_bytes + b'\x00' * (16 - len(node_id_bytes))

        # Build config (WiFi credentials go to NVS, not config.json)
        config = {
            "node_role": "primary" if is_primary else "secondary",
            "wifi_ssid": wifi_ssid,   # injected into runtime config
            "wifi_pass": wifi_pass,   # injected into runtime config
            "server_ip": server_ip,
            "server_port": server_port,
            "read_interval_s": float(read_interval),
            "send_interval_s": float(send_interval),
            "calibration": {
                "acs712_bus": {
                    "vref": float(acs_vref),
                    "sensitivity": float(acs_sens),
                    "offset": float(acs_offset)
                },
                "acs712_gen": {
                    "vref": float(acs_vref),
                    "sensitivity": float(acs_sens),
                    "offset": float(acs_offset)
                },
                "vdiv_bus": {
                    "ratio": float(vdiv_ratio),
                    "offset": float(vdiv_offset)
                },
                "vdiv_gen": {
                    "ratio": float(vdiv_ratio),
                    "offset": float(vdiv_offset)
                }
            },
            "ems": {
                "v_bus_nominal": float(v_nominal),
                "p_demand_default": float(p_default),
                "pwm_min": int(pwm_min),
                "pwm_max": int(pwm_max),
                "pwm_freq": 1000
            },
            "thresholds": {
                "v_bus_min": 10.0,
                "v_bus_max": 14.0,
                "i_bus_max": 5.0,
                "p_gen_max": 100.0
            }
        }

        # Write config.json (without WiFi credentials — stored in NVS)
        config_to_save = {k: v for k, v in config.items()
                          if k not in _NVS_CREDENTIAL_FIELDS}
        with open(_CONFIG_PATH, "w") as f:
            json.dump(config_to_save, f)
        print("\n[config] Saved to {}".format(_CONFIG_PATH))

        # Write identity to NVS
        server_id = b'' if is_primary else b'\x00' * 16
        set_identity(node_id_bytes, node_uuid, node_key, node_salt, server_id, is_primary)

        # Write WiFi credentials to NVS (kept separate from config.json)
        _nvs_set(NVS_NAMESPACE, "wifi_ssid",
                 wifi_ssid.encode('utf-8') if isinstance(wifi_ssid, str) else wifi_ssid)
        _nvs_set(NVS_NAMESPACE, "wifi_pass",
                 wifi_pass.encode('utf-8') if isinstance(wifi_pass, str) else wifi_pass)
        print("[config] WiFi credentials written to NVS")

        print("[config] Provisioning complete.")
        return config

    except KeyboardInterrupt:
        print("\n[config] Provisioning cancelled")
        return None


def load():
    """
    Load and validate device configuration from flash.

    Returns:
        Validated config dict with calibration and EMS parameters.

    Raises:
        SystemExit: If config is invalid and UART provisioning fails.
    """
    config = None

    try:
        with open(_CONFIG_PATH, "r") as f:
            config = json.load(f)
        print("[config] Loaded {}".format(_CONFIG_PATH))
    except OSError:
        print("[config] {} not found".format(_CONFIG_PATH))

    # Overlay WiFi credentials from NVS before checking for placeholders.
    # This allows config.json to use "REPLACE_WITH_NVS" placeholder values —
    # NVS fills them in at runtime so the placeholder check sees the real values
    # and does not trigger interactive provisioning on already-provisioned devices.
    # Provisioning is only triggered if placeholders remain after the overlay
    # (i.e., NVS is empty and the node genuinely needs first-boot setup).
    if config is not None:
        wifi = get_wifi_credentials()
        if wifi:
            config["wifi_ssid"] = wifi["wifi_ssid"]
            config["wifi_pass"] = wifi["wifi_pass"]
            print("[config] WiFi credentials loaded from NVS")
        else:
            print("[config] WiFi credentials not in NVS; using config.json values")

    if config is None or _check_placeholder(config):
        print("[config] Device needs provisioning")
        config = _enter_uart_config()
        if config is None:
            print("[config] FATAL: No configuration available")
            sys.exit(1)

    missing = _validate_config(config)
    if missing:
        print("[config] FATAL: Missing fields: {}".format(", ".join(missing)))
        sys.exit(1)

    print("[config] role={}, server={}:{}".format(
        config["node_role"], config["server_ip"], config["server_port"]))
    return config
