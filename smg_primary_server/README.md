# SMG Primary Server

Server implementing the Root of Trust challenge-response protocol for the DC Smart Microgrid case
study. Runs on a Raspberry Pi (or any Linux host) configured as a WiFi access point.

## Architecture

```
smg_primary_server/
├── puf_server.py                  # Entry point: PUF challenge-response TCP server
├── rot_challenge_service.py       # Per-session protocol handler (P1-P3)
├── ems_coordinator.py             # Global demand setpoint computation
├── data_store.py                  # Thread-safe sensor reading buffer
├── config.json                    # Server configuration
├── puf_server_identity.json       # Server identity (id_b) -- git-ignored
├── puf_node_registry.json         # Registered nodes (pk_a, shs_b per node) -- git-ignored
├── tools/
│   └── register_puf_node.py       # Register a node's public key and generate its V_B
└── requirements.txt                # Python dependencies
```

## Requirements

- Python 3.11+
- `cryptography` library: `pip install cryptography`
- Linux host acting as WiFi AP (e.g., Raspberry Pi with hostapd)

## Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure WiFi access point

On Raspberry Pi (or similar), using NetworkManager's hotspot mode or hostapd/dnsmasq:
```bash
# /etc/hostapd/hostapd.conf (or equivalent NetworkManager hotspot config):
interface=wlan0
ssid=SMG_Primary
hw_mode=g
channel=1
wpa=2
wpa_passphrase=CHANGE_ME_WIFI_PASSWORD
wpa_key_mgmt=WPA-PSK
```

### 3. Establish the server identity

Run once to generate `puf_server_identity.json` (the fleet-wide, non-secret `id_b`):
```bash
python ../smg_puf_rot_node/tools/provision_server_ref.py
```

### 4. Enroll and register each node

Flash and enroll the node's firmware (see `smg_puf_rot_node/README.md`). The device prints its
`id_a` and public key `pk_a` once, over USB serial, during its one-time provisioning export. Then:
```bash
python tools/register_puf_node.py --id-a <ID_A_HEX> --pk-a <PK_A_HEX> --label SMG_NODE_01
```
This generates a fresh per-node server verifier (`shs_b` / $V_B$) and adds the node to
`puf_node_registry.json` (copy `puf_node_registry.example.json` to start one if it doesn't exist
yet; both registry and identity files are git-ignored and must never be committed). The command
also prints the follow-up `provision_server_ref.py` invocation needed to pin that node's `shs_b`
into its own NVS image before first boot.

### 5. Start the server

```bash
python puf_server.py --port 5001
```

## Protocol notes

The server implements the Root of Trust challenge-response protocol (phases P1-P3): P1 enrollment
is out-of-band (steps 3-4 above); P2 is a nonce-bound mutual-authentication handshake, asymmetric
(ECDSA) on the device side and symmetric (per-node verifier $V_B$) on the server side; P3 is
AES-256-GCM encrypted data exchange. See the paper's Root of Trust Establishment and
STRIDE-to-Protocol Mapping sections for the full message sequence and design rationale. This
protocol is an implementation-specific Stage 4 component of the SMG case study, not itself a
contribution of the IoT methodology.

`ems_coordinator.py` and `data_store.py` provide global EMS coordination and sensor-data buffering
for node types that implement the sensor/EMS layer; the current `smg_puf_rot_node` firmware does
not yet implement that layer (identity/Root-of-Trust/secure-communication only), so these modules
are not currently exercised end-to-end — see the paper's Limitations.

## Security notes

- WiFi password is a known trade-off for a laboratory case study; use WPA3 or 802.1X in production.
- `puf_node_registry.json` and `puf_server_identity.json` are git-ignored and must never be
  committed; protect them with filesystem permissions (`chmod 600`).
- Only public material (`id_a`, `pk_a`, `id_b`) is ever printed or transmitted for a device; its
  private key is derived fresh from the PUF response every boot and never leaves the device.
