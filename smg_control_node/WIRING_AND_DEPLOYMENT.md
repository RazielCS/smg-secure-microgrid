# SMG Control Node — Wiring Diagram & Deployment Guide

## 1. System Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        DC SMART MICROGRID (SMG)                          │
│                                                                          │
│  ┌────────────────────────┐         ┌─────────────────────┐             │
│  │   PRIMARY AGENT        │         │   SECONDARY NODE #1 │             │
│  │   Raspberry Pi 4B      │◄─WiFi──►│   (ESP32 NodeMCU)   │             │
│  │   AP: SMG_Primary      │         │   STA Client        │             │
│  │   IP: 192.168.4.1      │         │   IP: 192.168.4.x   │             │
│  │   smg_primary_server/  │         └──────────┬──────────┘             │
│  └──────────┬─────────────┘                    │ VIN (5V)               │
│             │ (load only)          ┌────────────┴────────┐              │
│  ┌──────────┴──────────┐           │  DC-DC Buck #1      │              │
│  │  DC/AC Inverter     │           │  12V → 5V / 3A      │              │
│  │  MSW 1500W 12V→120VAC│          └────────────┬────────┘              │
│  └──────────┬──────────┘                        │ 12V                   │
│             │                                    │                       │
│  ┌──────────┴──────────┐           ┌─────────────┴───────┐             │
│  │  AC/DC Adapter      │           │   SECONDARY NODE #2 │             │
│  │  120VAC → 5VDC/3A  │           │   (ESP32 NodeMCU)   │             │
│  │  USB-C → RPi4       │           │   VIN ← Buck 12V→5V │             │
│  └─────────────────────┘           └─────────────────────┘             │
│                                                                          │
│                                    ┌─────────────────────┐             │
│                                    │   SECONDARY NODE #3 │             │
│                                    │   (ESP32 NodeMCU)   │             │
│                                    │   VIN ← Buck 12V→5V │             │
│                                    └─────────────────────┘             │
│                                                                          │
│  ════════════════════════════════════════════════════════════           │
│                         DC ENERGY BUS (12V)                             │
│  ════════════════════════════════════════════════════════════           │
│       ▲ (load)          ▲              ▲              ▲                │
│  ┌────┴────┐       ┌────┴────┐    ┌────┴────┐    ┌────┴────┐          │
│  │Inverter │       │ SSR #1  │    │ SSR #2  │    │ SSR #3  │          │
│  │(RPi4 ld)│       └────┬────┘    └────┬────┘    └────┬────┘          │
│  └─────────┘            │              │              │                │
│                    ┌────┴────┐    ┌────┴────┐    ┌────┴────┐          │
│                    │Solar #1 │    │Solar #2 │    │Solar #3 │          │
│                    │+ Batt #1│    │+ Batt #2│    │+ Batt #3│          │
│                    └─────────┘    └─────────┘    └─────────┘          │
└──────────────────────────────────────────────────────────────────────────┘
```

### 1.1 Primary Agent — Raspberry Pi 4B

The RPi4 is the primary agent: it has no generation, storage, or SSR of its own.
It is connected to the DC bus as a **load** through:

```
DC bus (12 V) → Modified Sine Wave DC-AC Inverter (1500 W) → 120 V AC → USB-C AC/DC adapter → RPi4 (5 V)
```

The DC/AC inverter is required (instead of a direct DC-DC buck converter) to
provide a clean, regulated supply: direct buck converters at the available power
level introduced supply ripple that caused SD card write errors during continuous
data logging.

**RPi4 network configuration:**
- Interface: wlan0 configured as WiFi AP (hostapd)
- SSID: `SMG_Primary` | WPA2-PSK
- IP: `192.168.4.1` | Subnet: `192.168.4.0/24`
- DHCP server (dnsmasq): assigns `192.168.4.10–30` to secondary nodes
- TCP server: `smg_primary_server/server.py` listens on port 5000

### 1.2 Secondary Node Power Supply

Each ESP32 NodeMCU is powered **directly from the DC voltage bus** (Vbus) through an
on-board LM2596 buck converter. The node is always on while the bus has energy — there
is no hardware power switch.

```
DC Bus (12 V) ──[J1 screw terminal]──→ LM2596 IN+   LM2596 OUT+ ──→ ESP32 Vin (5V)
              └──────────────────────── LM2596 IN−   LM2596 OUT− ──→ GND
```

**Why a buck converter is required (not direct VIN):**
The NodeMCU onboard LDO (AMS1117-3.3) at 12 V input dissipates up to ~3.8 W
(WiFi TX peak ~440 mA × 8.7 V drop) — far exceeding the SOT-223 package limit.
The LM2596 reduces this to ~0.4 W (5 V → 3.3 V, same current).

**The LM2596 OUT+ also powers the 3× ACS712 modules at 5 V**, which sets their
zero-current reference voltage to VCC/2 = 2.5 V (not 1.65 V as it would be at 3.3 V).

Set buck output to 5.0 V before connecting. Verify with a multimeter.

## 2. Secondary Node Wiring Diagram

```
┌──────────────────────────────────────────────────────────────────┐
│                    NODEMCU ESP32 (30-pin)                        │
│                                                                  │
│  ┌────────────────────────────────────────────────────────────┐  │
│  │  3V3 ────┬─────────────────────────────────────────────┐  │  │
│  │          │                                             │  │  │
│  │  GND ────┼─────────────────────────────────────────────┼──┤  │
│  │          │                                             │  │  │
│  │  VP(36)──┼──[Voltage Divider]─── V_bus (DC bus +)      │  │  │
│  │          │   R1=30KΩ, R2=7.5KΩ                         │  │  │
│  │          │                                             │  │  │
│  │  VN(39)──┼──[Voltage Divider]─── V_gen (Solar panel +) │  │  │
│  │          │   R1=30KΩ, R2=7.5KΩ                         │  │  │
│  │          │                                             │  │  │
│  │  34 ─────┼──[ACS712-05B Vout]── I_bus (bus current)    │  │  │
│  │          │   VCC → 3V3, GND → GND                      │  │  │
│  │          │                                             │  │  │
│  │  35 ─────┼──[ACS712-05B Vout]── I_gen (gen current)    │  │  │
│  │          │   VCC → 3V3, GND → GND                      │  │  │
│  │          │                                             │  │  │
│  │  32 ─────┼──[ACS712-05B Vout]── I_node (self-consump.) │  │  │
│  │          │   In series with LM2596 12V IN; VCC→5V      │  │  │
│  │          │                                             │  │  │
│  │  27 ─────┼──[220Ω Rb]──BC547(Gen) base; coll.→J4 −IN  │  │  │
│  │  26 ─────┼──[220Ω Rb]──BC547(Sol) base; coll.→J3 −IN  │  │  │
│  │  25 ─────┼──[220Ω Rb]──BC547(Bus) base; coll.→J2 −IN  │  │  │
│  │          │   10kΩ base pull-down each; emitter→GND     │  │  │
│  │          │   SSR +IN → Vbus 12V direct; 1kHz 10-bit PWM│  │  │
│  │          │                                             │  │  │
│  │  23 ─────┼──[R1 220Ω]── Red LED (LED1)                 │  │  │
│  │  22 ─────┼──[R3 220Ω]── Yellow LED (LED3)              │  │  │
│  │  21 ─────┼──[R2 220Ω]── Green LED (LED2)               │  │  │
│  │          │   LED cathodes → GND                        │  │  │
│  │          │                                             │  │  │
│  │  18 ─────┼──[SW1 + R10 10kΩ pull-down]── InitPulse     │  │  │
│  │          │   Press to start SMG; press again to stop   │  │  │
│  │          │                                             │  │  │
│  │  VIN ────┼── LM2596 OUT+ (5V) ←── Vbus 12V via LM2596 │  │  │
│  │  GND ────┼── LM2596 OUT− / common GND                 │  │  │
│  │          │                                             │  │  │
│  │  USB ────┼── Serial programming only (not power)      │  │  │
│  └──────────┼─────────────────────────────────────────────┘  │  │
│             └─────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────┘
```

## 3. Detailed Sensor Connections

### 3.1 ACS712 Current Sensor (×3 per node: Bus, Gen/Solar, Node self-consumption)

```
                    ACS712-05B Module
                    ┌─────────────────┐
  DC Bus (+) ───────┤ IP+          OP ├───→ GPIO34 (bus) or GPIO35 (gen)
                    │                 │
  To SSR/Load ──────┤ IP-            │
                    │                 │
  LM2596 5V ─────────┤ VCC            │
  ESP32 GND ─────────┤ GND            │
                    └─────────────────┘

  Specifications:
    - Range: ±5A (10A peak)
    - Sensitivity: 185 mV/A
    - Vref (0A): VCC/2 = 2.5V (at 5V VCC from LM2596)
    - Bandwidth: 80 kHz
    - Response time: 5 µs

  Wiring notes:
    - IP+ and IP- are in SERIES with the current path
    - OP is the analog output to ESP32 ADC
    - ACS712 VCC must be 5V (from LM2596 OUT+), not 3.3V — affects Vref
    - Use shortest possible wires for IP+/IP- to reduce noise
    - Add 0.1µF decoupling capacitor between VCC and GND near module
```

### 3.2 Voltage Divider (×2)

```
                    Voltage Divider Module
                    ┌─────────────────┐
  DC Bus (+) ~12V ──┤ VCC          S  ├───→ GPIO36 (bus) or GPIO39 (gen)
                    │                 │
                    │              NC │   (not connected)
                    │                 │
  ESP32 GND ────────┤ GND          -  ├───→ ESP32 GND
                    └─────────────────┘

  Internal resistors: R1 = 30KΩ, R2 = 7.5KΩ
  Division ratio: (R1 + R2) / R2 = 5.0
  Max measurable: 25V (with 5V ADC ref) or 16.5V (with 3.3V ADC ref)

  DIY alternative (if module not available):
    V_in ───[30KΩ]───┬───[7.5KΩ]─── GND
                     │
                     └───→ ESP32 ADC pin

  Wiring notes:
    - Connect GND of divider to ESP32 GND (common ground)
    - VCC connects to the point to measure (DC bus + or solar panel +)
    - S (signal) goes to ESP32 ADC pin
    - Add 0.1µF capacitor between S and GND for noise filtering
```

### 3.3 SSR Connection — BC547 Power Interface (3 channels per node)

Each node has **3 DC-DC SSRs** (IBFIA brand, 40A). A BC547 NPN transistor on each
channel drives the SSR −IN from the ESP32 PWM output. The SSR +IN is connected
directly to the 12V Vbus rail.

**BC547 transistor stage (per channel):**



**Full SSR wiring per channel:**



  BC547 specifications:
    - Type: NPN small-signal transistor (TO-92 package)
    - Ic max: 100mA; hFE min: 110
    - Base resistor 220Ω: Ib = (3.3V − 0.7V) / 220Ω ≈ 11.8mA → saturates BC547
    - Base pull-down 10kΩ: ensures Q off when GPIO is LOW or floating

  SSR specifications (IBFIA DC-DC, implemented):
    - Load type: DC-DC
    - Load voltage: 5–60 VDC (covers 12V bus)
    - Control input: 3–32 VDC — driven at 12V directly from Vbus
    - PWM compatible: YES (no zero-crossing circuit)
    - Current rating: 40A per SSR

  MCB specifications (Schneider, one per SSR):
    - Type: 2-pole miniature circuit breaker
    - Purpose: Overcurrent protection per channel
### 3.4 Node Self-Consumption Current Sensor

ACS712 #3 measures the current drawn by the node itself on the 12V input to
the LM2596 buck converter. This allows reporting of actual node power overhead.

```
                    ACS712-05B Module (#3)
                    ┌─────────────────┐
  Vbus 12V ─────────┤ IP+          OP ├───→ GPIO32 (ADC1 CH4)
                    │                 │
  To LM2596 IN+ ───┤ IP-            │
                    │                 │
  LM2596 5V ────────┤ VCC            │
  ESP32 GND ────────┤ GND            │
                    └─────────────────┘

  Placement: In series with the 12V line feeding the LM2596 buck converter.
  Measures: Total node supply current (ESP32 + sensors + SSR control loads).
  VCC: 5V from LM2596 OUT+ (Vref = 2.5V)
  Typical idle: 0.10–0.18A (1.2–2.2W at 12V)
  Peak (WiFi TX): 0.25–0.35A (3–4W at 12V)
```

### 3.4 Status LEDs (Red / Yellow / Green)

3 discrete LEDs — Red (LED1), Green (LED2), Yellow (LED3) — each with a 220Ω series resistor.

```
  ESP32 GPIO23 ───[R1 220Ω]──→ LED1 (Red)    → GND
  ESP32 GPIO22 ───[R3 220Ω]──→ LED3 (Yellow) → GND
  ESP32 GPIO21 ───[R2 220Ω]──→ LED2 (Green)  → GND

  Status codes:
    OFF (all off)       - Node idle / waiting for SW1 press
    YELLOW blink        - Boot / initializing
    YELLOW solid        - WiFi connecting
    YELLOW fast blink   - Secure handshake (RoT)
    GREEN solid         - Online / operational
    GREEN blink         - Data transmission
    RED solid           - Error (sensor/EMS failure)
    RED blink           - Comms failure / reconnecting
```

### 3.5 Init Control Button (SW1)

```
  VCC ──[SW1 NO pushbutton]──→ InitPulse ──→ GPIO18 (D18)
                                    │
                              R10 (10kΩ)
                                    │
                                   GND

  Default (released): GPIO18 = LOW
  Pressed: GPIO18 = HIGH

  Firmware behavior:
    - Press once at startup (after boot): starts SMG operation
      (opens SSRs, begins WiFi connect, secure session, telemetry)
    - Press again while running: stops SMG gracefully
      (closes SSRs, logs out from primary, resets to waiting state)
```

## 4. Pin Assignment Summary

| ESP32 Pin | Function | Signal | Notes |
|-----------|----------|--------|-------|
| GPIO36 (VP) | ADC1 CH0 | Bus voltage | Voltage divider output |
| GPIO39 (VN) | ADC1 CH3 | Gen voltage | Voltage divider output |
| GPIO34 | ADC1 CH6 | Bus current | ACS712 output |
| GPIO35 | ADC1 CH7 | Gen current | ACS712 output |
| GPIO32 | ADC1 CH4 | Node self-consumption current | ACS712 in series with buck 12V IN |
| GPIO25 | LEDC CH0 | SSR Bus PWM (BusCtrl) | 1kHz, 10-bit; Q1 BC547A |
| GPIO26 | LEDC CH1 | SSR Solar PWM (SolarCtrl) | 1kHz, 10-bit; Q2 BC547A |
| GPIO27 | LEDC CH2 | SSR Gen PWM (GenCtrl) | 1kHz, 10-bit; Q4 BC547A |
| GPIO23 | GPIO | Red LED (LED1) | R1=220Ω, active HIGH |
| GPIO22 | GPIO | Yellow LED (LED3) | R3=220Ω, active HIGH |
| GPIO21 | GPIO | Green LED (LED2) | R2=220Ω, active HIGH |
| GPIO18 | GPIO input | SW1 InitPulse | Active HIGH, R10=10kΩ pull-down |
| VIN | Power in | 5V from LM2596 | Vbus 12V → LM2596 → VIN |
| 3V3 | Power out | Voltage sensor VCC | Voltage divider modules only |
| GND | Ground | Common ground | All GNDs tied together |

## 5. Primary Node Setup

The primary node runs a modified firmware that acts as both an ESP32 AP and the SecureNode server.

### 5.1 Primary Node Configuration

```json
{
  "node_role": "primary",
  "wifi_ssid": "SMG_Primary_AP",
  "wifi_pass": "CHANGE_ME_WIFI_PASSWORD",
  "server_ip": "0.0.0.0",
  "server_port": 5000,
  "read_interval_s": 0.333,
  "send_interval_s": 1.0
}
```

### 5.2 Primary Node Identity

```bash
python tools/provision_node.py --port COM3 --node-id SMG_PRIMARY --role primary
```

Save the output `node_id` hex value — it is needed for secondary node provisioning.

## 6. Secondary Node Deployment

### 6.1 Flash MicroPython Firmware

```bash
# Download ESP32 MicroPython with custom C modules
# Use the firmware from:
#   esp32 micropyhton securenode firmware/firmware/micropython.bin

# Flash using esptool.py
esptool.py --chip esp32 --port COM3 erase_flash
esptool.py --chip esp32 --port COM3 \
  --baud 460800 write_flash -z \
  0x1000 bootloader.bin \
  0x8000 partition-table.bin \
  0x10000 micropython.bin
```

### 6.2 Provision Identity and WiFi Credentials

WiFi credentials are stored in NVS at provisioning time — never in config.json.

```bash
# For each secondary node (WiFi credentials go to NVS, not filesystem):
python tools/provision_node.py \
  --port COM3 \
  --node-id SMG_NODE_01 \
  --role secondary \
  --server-id <PRIMARY_NODE_ID_HEX> \
  --wifi-ssid SMG_Primary_AP \
  --wifi-pass CHANGE_ME_WIFI_PASSWORD

python tools/provision_node.py \
  --port COM4 \
  --node-id SMG_NODE_02 \
  --role secondary \
  --server-id <PRIMARY_NODE_ID_HEX> \
  --wifi-ssid SMG_Primary_AP \
  --wifi-pass CHANGE_ME_WIFI_PASSWORD

python tools/provision_node.py \
  --port COM5 \
  --node-id SMG_NODE_03 \
  --role secondary \
  --server-id <PRIMARY_NODE_ID_HEX> \
  --wifi-ssid SMG_Primary_AP \
  --wifi-pass CHANGE_ME_WIFI_PASSWORD

# Copy the printed node_id, node_key, node_salt for each node into
# smg_primary_server/node_registry.json
```

### 6.3 Deploy Firmware Files

```bash
# Using mpremote (recommended)
mpremote --port COM3 cp *.py :
mpremote --port COM3 cp config.json :

# Or using ampy
ampy --port COM3 put main.py
ampy --port COM3 put config_manager.py
ampy --port COM3 put sensor_module.py
ampy --port COM3 put ems_module.py
ampy --port COM3 put security_module.py
ampy --port COM3 put comm_module.py
ampy --port COM3 put secure_node.py
ampy --port COM3 put hkdf.py
ampy --port COM3 put led_semaphore.py
ampy --port COM3 put metrics.py
ampy --port COM3 put config.json
```

### 6.4 Configure Calibration

Edit `config.json` on each node with sensor-specific calibration values:

```bash
# Connect via serial REPL
mpremote --port COM3 repl

# Run zero-current calibration (with no current flowing)
>>> from sensor_module import SensorModule
>>> import config_manager
>>> config = config_manager.load()
>>> sensors = SensorModule(config)
>>> offsets = sensors.calibrate_zero_current()
>>> # Update config.json with offset values
```

### 6.5 Verify Operation

```bash
# Connect via serial monitor
mpremote --port COM3 repl

# Expected boot output:
# ==================================================
#   SMG Control Node Firmware v1.0
#   MicroPython + SecureNode v3 + HKDF
# ==================================================
# [config] Loaded /config.json
# [main] Initializing sensors...
# [main] Initializing EMS...
# [ems] PWM initialized: pin=27, freq=1000Hz, duty=0
# [main] Connecting to primary node...
# [comm] Connecting to WiFi 'SMG_Primary_AP'...
# [comm] WiFi connected: 192.168.4.2
# [main] Establishing secure session...
# [comm] Connecting TCP to 192.168.4.1:5000
# [comm] TCP connected
# [security] Handshake OK: P2=XXXXus P3=XXXXus
# [main] Secure session established. Starting main loop.
```

## 7. Calibration Procedures

### 7.1 ACS712 Zero-Current Offset

1. Disconnect all loads from the DC bus
2. Ensure no current flows through ACS712 sensors
3. Run `calibrate_zero_current()` from serial REPL
4. Record offset values and update `config.json`

### 7.2 Voltage Divider Verification

1. Measure actual DC bus voltage with a multimeter
2. Compare with node's reported V_bus reading
3. Adjust `vdiv_bus.ratio` and `vdiv_bus.offset` in `config.json`
4. Repeat for generator voltage

### 7.3 PWM Duty Cycle Verification

1. Connect a multimeter to SSR control input
2. Set known duty cycle values via REPL:
   ```python
   >>> from ems_module import FuzzyEMS
   >>> ems = FuzzyEMS(config)
   >>> ems.init_pwm()
   >>> ems.update_pwm(512)  # 50% duty
   ```
3. Verify PWM frequency with oscilloscope (should be 1kHz)

## 8. Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| Node doesn't boot | Corrupt firmware | Re-flash MicroPython |
| WiFi won't connect | Credentials not provisioned | Run provision_node.py with --wifi-ssid/--wifi-pass to store in NVS |
| Handshake fails | Identity mismatch | Re-provision node, verify server_id |
| Sensor readings = 0 | ADC not configured | Check pin connections, verify ADC_ATTEN |
| ACS712 reads ~2.5V always | No current flow | Normal — 0A = VCC/2 at 5V supply |
| Voltage reads 0V | Divider not connected | Check VCC and GND connections |
| PWM doesn't drive SSR | Wrong pin, freq, or AC-type SSR | Verify GPIO27; confirm SSR is DC-rated (not AC zero-crossing) |
| LED always red | Sensor/EMS error | Check serial output for error messages |
| Low heap (<20KB) | Memory leak | Reduce chain size, check for object accumulation |
| Repeated reconnects | Primary node unreachable | Verify primary node is running, check WiFi range |

## 9. Metrics Collection for Case Study

After the node has been running for a sufficient period, retrieve metrics:

```python
# Via serial REPL
>>> from metrics import MetricsCollector
>>> # Metrics are collected automatically during operation
>>> # Print summary:
>>> metrics.print_summary(
...     security_metrics=sec.get_metrics(),
...     sensor_stats=sensors.get_read_stats(),
...     ems_stats=ems.get_stats(),
...     comm_stats=comm.get_stats()
... )
```

Expected output format:
```
=======================================================
  SMG NODE METRICS SUMMARY
=======================================================
  Uptime              : 3600.0 s
  Loop count          : 10800
  Sampling rate       : 3.0 Hz
  Loop avg/min/max    : 280000 / 250000 / 350000 us
  Sensor reads        : 10800 (avg 128000 us)
  EMS inferences      : 10800 (avg 10000 us)
  Secure sends        : 3600 (avg 180000 us, 99.8% ok)
  Payload size        : 156 / 148 / 164 bytes (avg/min/max)
  Free heap           : 85000 bytes

  --- SECURITY ---
  P2 (RoT)            : 45000 us
  P3 (Session)        : 120000 us
  Handshake total     : 165000 us
  P4 transactions     : 3600 (avg 180000 us)
  P5 (Logout)         : 35000 us
  Chain length        : 3600
  Chain integrity     : OK
  Security errors     : 0
=======================================================
```

## 10. Bill of Materials

| Component | Quantity | Specification | Approx. Cost |
|-----------|----------|---------------|-------------|
| NodeMCU ESP32 | 4 | Dual-core 240MHz, 520KB SRAM, 4MB Flash, WiFi | $6-8 each |
| DC-DC Buck Converter | 3 | LM2596 or MP2307, 12V→5V, ≥1A, adjustable output | $1-2 each |
| ACS712-05B | 9 | ±5A, 185mV/A, 5V or 3.3V compatible (2 energy + 1 node self-consumption per node) | $2-3 each |
| BC547 NPN transistor | 9 | SSR PWM power interface, 3 per node, TO-92 | $0.05 each |
| 1kΩ resistors | 9 | BC547 base current limiters, 3 per node, 1/4W THT | $0.01 each |
| Voltage sensor module | 6 | 30K/7.5K divider, 0-25V range | $1-2 each |
| SSR DC-DC (IBFIA) | 9 | 40A, 3-32VDC control, 5-60VDC load, PWM compatible (3 per node) | $5-8 each |
| MCB (Schneider) | 9 | 2-pole miniature circuit breaker, one per SSR channel | $3-5 each |
| LED Red 5mm (LED1) | 3 | one per node | $0.05 each |
| LED Green 5mm (LED2) | 3 | one per node | $0.05 each |
| LED Yellow 5mm (LED3) | 3 | one per node | $0.05 each |
| 220Ω resistors | 9 | SSR pull-ups (R4/R5/R6) + LED current limiters (R11/R12/R13), 1/4W | $0.01 each |
| Solar panel | 3 | 18V, 10W minimum | $10-15 each |
| Battery (Epcom AGM VRLA) | 3 | 12V 18Ah sealed AGM, PL-18-12 | $25-35 each |
| Commercial solar controller | 3 | PWM or MPPT, 12V | $10-20 each |
| Jumper wires | 1 set | Male-female, male-male | $3-5 |
| Breadboard or PCB | 3 | For prototyping | $2-5 each |

**Total per node (excluding solar/battery/controller): ~$25-35**
