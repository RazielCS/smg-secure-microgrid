"""
RoT Identity Provisioning Script for SMG Control Nodes.

Generates unique cryptographic identity for each node and writes
it to the ESP32 NVS partition via serial connection.

Usage:
    python provision_node.py --port COM3 --node-id SMG_NODE_01 --role secondary
    python provision_node.py --port COM3 --node-id SMG_PRIMARY --role primary

Requires:
    pyserial: pip install pyserial
"""

import argparse
import struct
import time
import sys
import os

try:
    import serial
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)


def generate_uuid4():
    """
    Generate a UUID-4 compliant identifier (16 bytes).

    Format: xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx
    where y is one of 8, 9, A, or B.
    """
    uuid_bytes = bytearray(os.urandom(16))
    uuid_bytes[6] = (uuid_bytes[6] & 0x0F) | 0x40
    uuid_bytes[8] = (uuid_bytes[8] & 0x3F) | 0x80
    return bytes(uuid_bytes)


def generate_identity(node_label, role="secondary"):
    """
    Generate complete identity parameters for a node.

    Returns:
        dict with node_id, node_uuid, node_key, node_salt, is_primary
    """
    node_id = node_label.encode('utf-8')[:16]
    node_id = node_id + b'\x00' * (16 - len(node_id))

    node_uuid = generate_uuid4()
    node_key = os.urandom(32)
    node_salt = os.urandom(32)
    is_primary = role.lower() == "primary"

    return {
        "node_id": node_id,
        "node_uuid": node_uuid,
        "node_key": node_key,
        "node_salt": node_salt,
        "is_primary": is_primary,
    }


def send_repl_command(ser, cmd, timeout=2.0):
    """Send a command via raw REPL and return the output."""
    ser.write(b'\x05')
    time.sleep(0.1)
    ser.write(b'\x01')
    time.sleep(0.1)
    ser.write(cmd.encode('utf-8'))
    ser.write(b'\x04')
    time.sleep(0.5)

    output = b''
    start = time.time()
    while time.time() - start < timeout:
        if ser.in_waiting > 0:
            output += ser.read(ser.in_waiting)
        else:
            time.sleep(0.05)

    ser.write(b'\x02')
    time.sleep(0.1)
    ser.read_all()

    return output.decode('utf-8', errors='replace')


def write_nvs_via_nvs_module(ser, namespace, key, value_hex):
    """Write a value to NVS using the nvs module (if available)."""
    cmd = (
        "import nvs\n"
        "nvs.set_str('{}', '{}', bytes.fromhex('{}'))\n"
        "nvs.commit()\n"
        "print('OK')\n"
    ).format(namespace, key, value_hex)

    result = send_repl_command(ser, cmd)
    return 'OK' in result


def write_nvs_via_esp32(ser, namespace, key, value_hex):
    """Write a value to NVS using esp32.NVS (alternative interface)."""
    cmd = (
        "from esp32 import NVS\n"
        "n = NVS('{}')\n"
        "n.set_blob('{}', bytes.fromhex('{}'))\n"
        "n.commit()\n"
        "print('OK')\n"
    ).format(namespace, key, value_hex)

    result = send_repl_command(ser, cmd)
    return 'OK' in result


def provision_node(port, baudrate, node_label, role, server_id_hex=None,
                   wifi_ssid=None, wifi_pass=None):
    """
    Provision a single node with generated identity and WiFi credentials.

    Parameters:
        port: Serial port (e.g., COM3, /dev/ttyUSB0)
        baudrate: Serial baud rate (default 115200)
        node_label: Human-readable node identifier
        role: 'primary' or 'secondary'
        server_id_hex: Hex string of primary node ID (for secondary nodes)
        wifi_ssid: WiFi AP SSID to store in NVS (optional)
        wifi_pass: WiFi AP password to store in NVS (optional)
    """
    identity = generate_identity(node_label, role)

    print("=" * 60)
    print("  SMG RoT Identity Provisioning")
    print("=" * 60)
    print()
    print("  Node:     {}".format(node_label))
    print("  Role:     {}".format(role))
    print("  node_id:  {}".format(identity["node_id"].hex()))
    print("  node_uuid:{}".format(identity["node_uuid"].hex()))
    print("  node_key: {}".format(identity["node_key"].hex()))
    print("  node_salt:{}".format(identity["node_salt"].hex()))
    print()

    confirm = input("  Proceed with provisioning? (y/n): ")
    if confirm.lower() != 'y':
        print("  Aborted.")
        return

    print("\n  Connecting to {} at {}...".format(port, baudrate))
    try:
        ser = serial.Serial(port, baudrate, timeout=1)
        ser.reset_input_buffer()
        ser.reset_output_buffer()
    except serial.SerialException as e:
        print("  ERROR: Cannot open port: {}".format(e))
        return

    print("  Connected. Testing REPL...")

    test_result = send_repl_command(ser, "print('REPL_OK')", timeout=3.0)
    if 'REPL_OK' not in test_result:
        print("  ERROR: REPL not responding. Ensure MicroPython is running.")
        ser.close()
        return

    print("  REPL OK. Writing identity to NVS...")

    namespace = "smg_identity"
    keys_to_write = {
        "node_id": identity["node_id"].hex(),
        "node_uuid": identity["node_uuid"].hex(),
        "node_key": identity["node_key"].hex(),
        "node_salt": identity["node_salt"].hex(),
        "is_primary": b'\x01'.hex() if identity["is_primary"] else b'\x00'.hex(),
    }

    if server_id_hex and role.lower() == "secondary":
        keys_to_write["server_id"] = server_id_hex

    if wifi_ssid:
        keys_to_write["wifi_ssid"] = wifi_ssid.encode('utf-8').hex()
    if wifi_pass:
        keys_to_write["wifi_pass"] = wifi_pass.encode('utf-8').hex()

    success_count = 0
    total_count = len(keys_to_write)

    for key, value_hex in keys_to_write.items():
        print("    Writing {}...".format(key), end=" ")

        ok = write_nvs_via_nvs_module(ser, namespace, key, value_hex)
        if not ok:
            ok = write_nvs_via_esp32(ser, namespace, key, value_hex)

        if ok:
            print("OK")
            success_count += 1
        else:
            print("FAILED")

    print()
    if success_count == total_count:
        print("  Provisioning SUCCESS: {}/{} keys written.".format(success_count, total_count))
        print()
        print("  Save these values for the server database:")
        print("    node_id:   {}".format(identity["node_id"].hex()))
        print("    node_uuid: {}".format(identity["node_uuid"].hex()))
        print("    node_key:  {}".format(identity["node_key"].hex()))
        print("    node_salt: {}".format(identity["node_salt"].hex()))
        print()
        print("  The node will use these on next boot to establish secure communication.")
    else:
        print("  Provisioning PARTIAL: {}/{} keys written.".format(success_count, total_count))
        print("  Some keys failed. Check NVS module availability.")

    ser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Provision SMG control node identity via serial")
    parser.add_argument("--port", required=True, help="Serial port (e.g., COM3, /dev/ttyUSB0)")
    parser.add_argument("--baud", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--node-id", required=True, help="Node label (e.g., SMG_NODE_01)")
    parser.add_argument("--role", choices=["primary", "secondary"], default="secondary",
                        help="Node role (default: secondary)")
    parser.add_argument("--server-id", default=None,
                        help="Primary node ID in hex (for secondary nodes)")
    parser.add_argument("--wifi-ssid", default=None,
                        help="WiFi AP SSID to store in NVS (recommended; keeps creds off filesystem)")
    parser.add_argument("--wifi-pass", default=None,
                        help="WiFi AP password to store in NVS")

    args = parser.parse_args()

    provision_node(args.port, args.baud, args.node_id, args.role, args.server_id,
                   args.wifi_ssid, args.wifi_pass)


if __name__ == "__main__":
    main()
