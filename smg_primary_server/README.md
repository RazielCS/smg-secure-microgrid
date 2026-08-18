# SMG Primary Server

TCP server implementing the SecureNode v3 responder role for the DC Smart Microgrid case study. Runs on a Raspberry Pi (or any Linux host) configured as a WiFi access point.

## Architecture

```
smg_primary_server/
├── server.py           # Main TCP server (multi-threaded)
├── auth_service.py     # Per-session SecureNode P2–P5 responder
├── data_store.py       # Thread-safe sensor reading buffer
├── ems_coordinator.py  # Global demand setpoint computation
├── crypto_utils.py     # CPython-compatible HKDF + AES-256-GCM
├── protocol.py         # Message framing (matches secure_node.py)
├── config.json         # Server configuration
├── node_registry.json  # Registered secondary nodes
└── requirements.txt    # Python dependencies
```

## Requirements

- Python 3.11+
- `cryptography` library: `pip install cryptography`
- Linux host acting as WiFi AP (e.g., Raspberry Pi with hostapd)

## Setup

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Configure WiFi Access Point

On Raspberry Pi (or similar):
```bash
# Install hostapd and dnsmasq
sudo apt install hostapd dnsmasq

# /etc/hostapd/hostapd.conf:
interface=wlan0
ssid=SMG_Primary_AP
hw_mode=g
channel=6
wpa=2
wpa_passphrase=CHANGE_ME_WIFI_PASSWORD
wpa_key_mgmt=WPA-PSK

# /etc/dnsmasq.conf:
interface=wlan0
dhcp-range=192.168.4.2,192.168.4.20,255.255.255.0,24h
```

### 3. Generate Server Identity

Run the server once without credentials to auto-generate:
```bash
python server.py
# Output:
# WARNING: No server_id/server_key in config — generating ephemeral identity.
# WARNING: Add to config.json: server_id=<hex>  server_key=<hex>
```

Copy the printed values into `config.json`.

### 4. Provision Secondary Nodes

For each secondary node, run the provisioning script (from smg_control_node/tools/):
```bash
python provision_node.py \
    --port COM3 \
    --node-id SMG_NODE_01 \
    --role secondary \
    --server-id <SERVER_ID_HEX> \
    --wifi-ssid SMG_Primary_AP \
    --wifi-pass CHANGE_ME_WIFI_PASSWORD

# Copy the printed node_id, node_key, node_salt into node_registry.json
```

### 5. Populate Node Registry

Copy `node_registry.example.json` to `node_registry.json` (git-ignored — it holds per-node private authentication secrets and must never be committed) and edit it:
```json
{
  "<node_id_hex_from_provisioning>": {
    "label": "SMG_NODE_01",
    "node_key": "<node_key_hex>",
    "node_salt": "<node_salt_hex>",
    "node_uuid": "<node_uuid_hex>"
  }
}
```

### 6. Start the Server

```bash
python server.py --config config.json --registry node_registry.json
```

## Protocol Notes

The server implements the **responder** role of SecureNode v3:

| Phase | Role    | Messages |
|-------|---------|----------|
| P2    | Respond | rot_hello → rot_ack (HKDF key derivation) |
| P3    | Respond | ses_init → ses_reply → ses_conf → ses_conf_ack |
| P4    | Receive | dx_msg → dx_res [+ setpoint] (hash-chain + AES-GCM) |
| P5    | Respond | logout_req → logout_res |

The SecureNode v3 protocol is submitted for separate peer review at IEEE IoT Journal. This server is an implementation-specific component of the SMG case study, not a contribution of the IoT methodology itself.

## Security Notes

- WiFi password stored in `hostapd.conf` is a known trade-off for the case study. In production, use WPA3 or 802.1X.
- `node_registry.json` contains pre-shared keys — protect it with filesystem permissions (`chmod 600 node_registry.json`).
- The server's `server_key` in `config.json` must be kept secret. Do not commit it to version control.
- All data exchange (P4) uses AES-256-GCM with per-message random nonces and a hash chain for integrity.
