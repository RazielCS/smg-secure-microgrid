# SMG Control Node Firmware — MicroPython

ESP32 MicroPython firmware for DC Smart Microgrid (SMG) control nodes.
Implements the SecureNode v3 protocol (P1-P6) with HKDF key derivation,
fuzzy logic EMS, and secure TCP communication to a primary node.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                    main.py                           │
│  Boot: config → WiFi → RoT handshake → 3Hz loop     │
├──────────┬──────────┬───────────┬───────────────────┤
│ sensor_  │ ems_     │ security_ │ comm_             │
│ module   │ module   │ module    │ module            │
│          │          │           │                   │
│ ACS712   │ Fuzzy    │ SecureNode│ WiFi STA +        │
│ + Vdiv   │ logic    │ P1-P6     │ TCP client        │
│ 3Hz ADC  │ + PWM    │ + HKDF    │ to primary AP     │
├──────────┴──────────┴───────────┴───────────────────┤
│              config_manager.py + hkdf.py             │
│              NVS identity + JSON config              │
└─────────────────────────────────────────────────────┘
```

## Files

| File | Description |
|------|-------------|
| `main.py` | Boot orchestrator + 3Hz sensor/EMS/PWM loop |
| `config_manager.py` | JSON config + NVS identity + UART provisioning |
| `sensor_module.py` | ACS712 current + voltage divider ADC readings |
| `ems_module.py` | Fuzzy logic EMS (9 rules, 3×3) + 3-channel LEDC PWM (GPIO27/26/25) |
| `security_module.py` | SecureNode protocol wrapper (P1-P6) with P4 latency std-dev |
| `comm_module.py` | WiFi STA + TCP client to primary node AP |
| `secure_node.py` | Core SecureNode v3 protocol implementation |
| `hkdf.py` | HKDF-SHA256 (RFC 5869) using uhashlib |
| `led_semaphore.py` | Status LED signaling (Red/Yellow/Green) |
| `metrics.py` | CSQ1-CSQ4 metrics collection, methodology table, CSV export |
| `benchmark.py` | Offline timing characterisation — sensor, EMS, heap stability |
| `config.json` | Configuration template with calibration constants |
| `tools/provision_node.py` | Host script for RoT identity deployment |

## Hardware

**Platform:** NodeMCU ESP32 (dual-core 240 MHz, 520 KB SRAM, 4 MB Flash)

**Sensors:**
- Bus current: ACS712-05B (185 mV/A, 5V supply, Vref=2.5V) → GPIO34 (ADC1 CH6)
- Gen/solar current: ACS712-05B → GPIO35 (ADC1 CH7)
- Node self-consumption: ACS712-05B (in series with LM2596 12V input) → GPIO32 (ADC1 CH4)
- Bus voltage: Resistive divider (30K/7.5K, ratio 5:1) → GPIO36 (ADC1 CH0)
- Gen/solar voltage: Resistive divider → GPIO39 (ADC1 CH3)

**Actuators:**
- SSR Bus: GPIO25 → R4(220Ω) → Q1(BC547A) → J2 (IBFIA DC-DC SSR, 40A)
- SSR Solar: GPIO26 → R12(220Ω) → Q2(BC547A) → J3
- SSR Gen: GPIO27 → R14(220Ω) → Q4(BC547A) → J4
- All 3 SSRs receive the same PWM duty cycle (1 kHz, 10-bit)

**Status & Control:**
- Red LED: GPIO23 via R1(220Ω)
- Green LED: GPIO21 via R2(220Ω)
- Yellow LED: GPIO22 via R3(220Ω)
- Init button (SW1): GPIO18, active HIGH, R10=10kΩ pull-down — press to start/stop SMG

## Build and Deployment

See [BUILD.md](BUILD.md) for:
- Pinned MicroPython version (1.22.1 + ESP-IDF 5.0.x)
- Custom `aesgcm` C module build instructions
- Firmware flashing with esptool
- File upload with mpremote
- How to reproduce the case study measurements

Requires custom MicroPython build with C modules:
- `aesgcm` — ESP32 hardware AES-GCM (from `esp32 micropyhton securenode firmware/aesgcm/`)
- `blake3` — BLAKE3 hash (optional, from `esp32 micropyhton securenode firmware/blake3/`)

## Provisioning

### Via Host Script (Recommended)

```bash
# Primary node
python tools/provision_node.py --port COM3 --node-id SMG_PRIMARY --role primary

# Secondary nodes
python tools/provision_node.py --port COM3 --node-id SMG_NODE_01 --role secondary --server-id <primary_node_id_hex>
python tools/provision_node.py --port COM4 --node-id SMG_NODE_02 --role secondary --server-id <primary_node_id_hex>
python tools/provision_node.py --port COM5 --node-id SMG_NODE_03 --role secondary --server-id <primary_node_id_hex>
```

### Via UART (Interactive)

If `config.json` or NVS identity is missing, the node enters interactive
provisioning mode on boot via serial REPL.

## Automated Tests

The test suite runs on standard CPython — no ESP32 hardware required.

```bash
pip install -r requirements.txt
pytest tests/ -v
```

Tests cover: HKDF RFC 5869 vectors, fuzzy EMS rule correctness, sensor
conversion functions, Welford stddev precision, P4 replay attack rejection,
V_bus regulation quality, node power tracking, heap stability/leak detection,
methodology table structure, and CSV export format.
See [BUILD.md](BUILD.md) for expected output and reproduction instructions.

## Deployment

```bash
# Using mpremote
mpremote connect COM3 cp main.py config_manager.py sensor_module.py \
    ems_module.py security_module.py comm_module.py secure_node.py \
    hkdf.py led_semaphore.py metrics.py config.json :

# Or using ampy
ampy --port COM3 put main.py
ampy --port COM3 put config_manager.py
# ... etc
```

## Configuration

Edit `config.json` before deployment:

| Field | Description | Default |
|-------|-------------|---------|
| `node_role` | "primary" or "secondary" | "secondary" |
| `wifi_ssid` | Primary node AP SSID | "SMG_Primary_AP" |
| `wifi_pass` | Primary node AP password | "CHANGE_ME_WIFI_PASSWORD" |
| `server_ip` | Primary node IP | "192.168.4.1" |
| `server_port` | TCP port | 5000 |
| `read_interval_s` | Sensor read interval | 0.333 (3 Hz) |
| `send_interval_s` | Data send interval | 1.0 (1 Hz) |
| `calibration.*` | Sensor calibration constants | See config.json |
| `ems.*` | EMS parameters | See config.json |
| `thresholds.*` | Safety limits | See config.json |

## Protocol

SecureNode v3 phases:

| Phase | Name | Description |
|-------|------|-------------|
| P1 | Secure Provisioning | Identity loaded from NVS |
| P2 | Root of Trust | HKDF key derivation with per-node salt |
| P3 | Session Establishment | SIGMA-I handshake (5 messages) |
| P4 | Data Exchange | Hash-chain encrypted sensor data |
| P5 | Logout | Session teardown and archival |

## Metrics

All metrics are collected live during operation and exported on shutdown.
They map to the four case-study questions (CSQ1–CSQ4) of the Q1 methodology paper.

### CSQ1 — Real-Time Performance

| Metric | Source | Notes |
|--------|--------|-------|
| Sampling rate (achieved, Hz) | `MetricsCollector` | Target: 3 Hz |
| Loop duration avg ± std (µs) | `MetricsCollector` | Welford online, O(1) memory |
| Loop min / max (µs) | `MetricsCollector` | Worst-case jitter |
| Sensor read avg ± std (µs) | `SensorModule` | 16-sample oversampling + median |
| EMS inference avg ± std (µs) | `FuzzyEMS` | 9-rule fuzzy centroid |
| V_bus mean ± std (V) | `MetricsCollector` | Regulation quality |
| V_bus tracking error (V) | `MetricsCollector` | `|mean − 12.0 V|` |

### CSQ2 — Resource Constraints

| Metric | Source | Notes |
|--------|--------|-------|
| Heap at each boot phase (bytes) | `MetricsCollector` | Snapshots: boot, sensors, ems, session |
| Heap drift per 100 loops (bytes) | `MetricsCollector` | Negative = probable leak |
| Free heap at shutdown (bytes) | `gc.mem_free()` | Sampled every 100 loops, bounded 20 entries |

### CSQ3 — Security Overhead

| Metric | Source | Notes |
|--------|--------|-------|
| P2 (RoT) duration (µs) | `SecurityManager` | HKDF key derivation |
| P3 (Session) duration (µs) | `SecurityManager` | SIGMA-I handshake |
| Handshake total (µs) | `SecurityManager` | P2 + P3 combined |
| P4 avg ± std (µs) | `SecurityManager` | Per-message AES-GCM round-trip |
| P5 (Logout) duration (µs) | `SecurityManager` | Session teardown |
| Chain length (messages) | `SecurityManager` | M_num at shutdown |

### CSQ4 — Energy & Communication

| Metric | Source | Notes |
|--------|--------|-------|
| Node self-consumption mean ± std (W) | `MetricsCollector` | P_node = V_bus × I_node |
| Send success rate (%) | `MetricsCollector` | Encrypted P4 transactions |
| Send avg ± std (µs) | `MetricsCollector` | End-to-end including ACK |
| Payload size avg (bytes) | `MetricsCollector` | JSON sensor reading |

### Output files written to ESP32 flash on shutdown

| File | Format | Contents |
|------|--------|----------|
| `/methodology.csv` | `csq,metric,value,unit` | Full CSQ1–CSQ4 table, ready for paper import |
| `/metrics.csv` | `metric,value,unit` | Flat per-metric summary |
| `/metrics.json` | JSON | Complete nested summary including subsystem stats |

### Offline benchmark (`benchmark.py`)

Run before deployment from the MicroPython REPL to characterise
sensor and EMS timing without WiFi or SecureNode:

```python
exec(open('benchmark.py').read())
```

Phases:
1. **Sensor read timing** — 100 reads, full 16-sample oversampling; reports avg ± std / min / max
2. **EMS inference timing** — 100 inferences over a synthetic 10–14 V sweep; exercises all 9 fuzzy rules
3. **Heap stability** — 100 sensor+EMS loops, heap sampled every 10 iterations; reports drift and leak detection

Results are printed to the console and saved to `/benchmark.csv`.

## Wiring & Deployment

See [WIRING_AND_DEPLOYMENT.md](WIRING_AND_DEPLOYMENT.md) for:
- Complete wiring diagrams (system, sensors, SSR, LED)
- Pin assignment table
- Primary node setup
- Step-by-step deployment procedure
- Calibration procedures
- Troubleshooting guide
- Bill of materials
