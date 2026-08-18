# SMG Control Node PCB — Assembly & Wiring Guide (Carrier Board)

## 1. PCB Overview

This is a **carrier board** (100mm × 80mm) that mounts commercial modules via female headers and screw terminals. The node is powered directly from the DC voltage bus (no hardware power switch).

```
┌──────────────────────────────────────────────────────────────────────┐
│  100mm × 80mm — 2-layer PCB — CNC milled (no solder mask)           │
│                                                                      │
│  J1(Vbus)   SW1          LM2596_CONN                                 │
│  ┌──┐      ┌─┐          ┌──────────────────┐                        │
│  │  │      │ │          │IN+ IN- OUT+ OUT- │                        │
│  └──┘      └─┘          └──────────────────┘                        │
│  (12V in) (InitPulse)                                                │
│                                                                      │
│           ┌────────────────────────────────────────────┐            │
│           │         ESP32 NodeMCU (mounted)            │            │
│           │  ┌──────────────────────────────────────┐  │            │
│           │  │  USB  [EN G23...G0] (top header)     │  │            │
│           │  │       [3V3 G36...G14] (bot header)   │  │            │
│           │  └──────────────────────────────────────┘  │            │
│           │  [2×15-pin female headers soldered to PCB] │            │
│           └────────────────────────────────────────────┘            │
│                                                                      │
│  ACS712#Bus  ACS712#Gen  ACS712#Node  Vdiv#Bus  Vdiv#Gen           │
│  ┌────┐     ┌────┐      ┌────┐       ┌────┐    ┌────┐              │
│  │V G O│    │V G O│     │V G O│      │V G S│   │V G S│              │
│  └────┘     └────┘      └────┘       └────┘    └────┘              │
│  [IP+IP-]   [IP+IP-]    [IP+IP-]    [V+ V-]   [V+ V-]              │
│                                                                      │
│  Q1(BC547)  Q2(BC547)  Q4(BC547)   LED1(R) LED2(G) LED3(Y)         │
│  J2(SSRBus) J3(SSRSol) J4(SSRGen)                                   │
└──────────────────────────────────────────────────────────────────────┘
```

## 2. Module Pinouts & Connections

### 2.1 ESP32 NodeMCU DevKit V1

```
  GPIO  → NodeMCU silk → SMG Function
  ──────────────────────────────────────────────────
  GPIO25  → D25         → SSR Bus PWM output (BusCtrl)
  GPIO26  → D26         → SSR Solar PWM output (SolarCtrl)
  GPIO27  → D27         → SSR Gen PWM output (GenCtrl)
  GPIO32  → D32         → ACS712 node self-consumption (ADC1 CH4)
  GPIO34  → D34         → ACS712 bus current (ADC1 CH6)
  GPIO35  → D35         → ACS712 gen/solar current (ADC1 CH7)
  GPIO36  → VP          → Voltage divider bus (ADC1 CH0)
  GPIO39  → VN          → Voltage divider gen/solar (ADC1 CH3)
  GPIO23  → D23         → Red LED (LED1, via R1=220Ω)
  GPIO22  → D22         → Yellow LED (LED3, via R3=220Ω)
  GPIO21  → D21         → Green LED (LED2, via R2=220Ω)
  GPIO18  → D18         → SW1 InitPulse (active HIGH, R10=10kΩ pull-down)
  Vin     → Vin         → 5V from LM2596 output
  GND     → GND         → Common ground
  3V3     → 3V3         → Voltage sensor VCC (divider modules only)
```

**Mounting:** Plug NodeMCU into the two 15-pin female headers on the carrier PCB. USB port should face the board edge for access.

### 2.2 LM2596 Buck Module

```
  LM2596 Terminal → Carrier Connection
  ────────────────────────────────────
  IN+             → Vbus 12V (from J1 screw terminal, direct from bus)
  IN-             → GND
  OUT+            → 5V → NodeMCU Vin + ACS712 VCC (all 3 modules)
  OUT-            → GND
  POT             → Adjust to 5.0V BEFORE assembly
```

**Connection:** Wire LM2596 to the 4-pin screw terminal block (LM2596_CONN).

### 2.3 ACS712 Modules (×3)

All 3 ACS712 modules powered at **5V** (from LM2596 OUT+), giving Vref = VCC/2 = **2.5V**.

```
  ACS712 Pin → Carrier Header → SMG Function
  ─────────────────────────────────────────
  VCC         → 3-pin header pin 1 → LM2596 5V output
  GND         → 3-pin header pin 2 → GND
  OUT         → 3-pin header pin 3 → NodeMCU ADC

  ACS712 Bus  (P1): OUT → GPIO34; IP+ from bus supply, IP- to bus
  ACS712 Gen  (P2): OUT → GPIO35; IP+ from gen/solar, IP- toward load
  ACS712 Node (P3): OUT → GPIO32; IP+ from Vbus 12V, IP- to LM2596 IN+
```

### 2.4 Voltage Sensor Modules (×2)

Voltage sensor VCC from ESP32 3V3 output (not 5V).

```
  VDiv Bus  (U4): S → GPIO36; V+ to DC bus (+), V- to GND
  VDiv Gen  (U2): S → GPIO39; V+ to gen/solar source (+), V- to GND
```

### 2.5 BC547 SSR Driver Stage (×3)

One BC547A NPN transistor per SSR channel on the carrier PCB.

```
  Channel    GPIO   Base R   Pull-down  Transistor  Terminal
  ──────────────────────────────────────────────────────────
  Bus        GPIO25  R4(220Ω) R5(10kΩ)  Q1(BC547A)  J2
  Solar      GPIO26  R12(220Ω) R11(10kΩ) Q2(BC547A) J3
  Gen        GPIO27  R14(220Ω) R13(10kΩ) Q4(BC547A) J4

  Circuit (per channel):
    GPIOxx ──[220Ω]──→ BC547 base ←──[10kΩ]──→ GND
                         BC547 emitter → GND
                         BC547 collector → SSR −IN (J terminal pin 2)

  SSR +IN (J terminal pin 1) → Vbus 12V (direct, no series resistor)
```

## 3. Power Supply & Init Control

### 3.1 Power Path

```
  DC Bus (12V) ──[J1]──→ LM2596 IN+
                         LM2596 OUT+ → 5V → NodeMCU Vin
                                           → ACS712 VCC (×3)
```

The node is always on while the bus has energy. There is no hardware power switch.

### 3.2 Init Control (SW1)

```
  VCC ──[SW1]──→ GPIO18 (InitPulse)
                      │
                 R10 (10kΩ)
                      │
                     GND
```

| Step | Action | Firmware Response |
|------|--------|-------------------|
| 1 | Power applied (bus live) | Node boots, waits for SW1 |
| 2 | Press SW1 | GPIO18 = HIGH → start SMG (WiFi, handshake, SSRs) |
| 3 | Node running | Yellow LED → Green LED (online) |
| 4 | Press SW1 again | GPIO18 = HIGH → stop SMG (SSRs off, session close) |
| 5 | Node stopped | All LEDs off, waiting for SW1 again |

## 4. Assembly Order

### 4.1 Recommended Sequence

1. **LM2596 setup** — Adjust output to 5.0V BEFORE soldering to carrier
2. **Discrete components** — Q1, Q2, Q4 (BC547A), R1-R5, R10-R14, SW1, LED1-3
3. **Female headers** — NodeMCU (2×15-pin), ACS712 (3×3-pin), VDiv (2×3-pin)
4. **Screw terminals** — J1-J4, ACS712_IP (×3), VDIV_IN (×2), LM2596_CONN
5. **Mount modules** — NodeMCU, LM2596, ACS712s, VDivs
6. **External wiring** — SSRs, current paths, voltage measurement points

### 4.2 Soldering Tips for CNC-Milled PCBs

- CNC-milled boards have **no solder mask** — work carefully to avoid bridges
- Apply **flux** before soldering each component
- Use **solder wick** to clean up any bridges
- After assembly, **inspect all traces** with a multimeter for shorts

## 5. Wiring Connections

### 5.1 External Connections

| Connector | Signal | Wire | Notes |
|-----------|--------|------|-------|
| J1 pin 1 | Vbus 12V (+) | Red, 16 AWG | DC bus positive |
| J1 pin 2 | GND | Black, 16 AWG | Common ground |
| J2 pin 1 | SSR Bus +IN | Red, 22 AWG | Vbus 12V (SSR control supply) |
| J2 pin 2 | SSR Bus −IN | Black, 22 AWG | BC547 Q1 collector |
| J3 pin 1 | SSR Solar +IN | Red, 22 AWG | Vbus 12V |
| J3 pin 2 | SSR Solar −IN | Black, 22 AWG | BC547 Q2 collector |
| J4 pin 1 | SSR Gen +IN | Red, 22 AWG | Vbus 12V |
| J4 pin 2 | SSR Gen −IN | Black, 22 AWG | BC547 Q4 collector |

### 5.2 ACS712 Current Sensor Wiring

```
  ACS712 Bus (P1):
    IP+ ───→ Bus source (battery / solar controller output before SSR)
    IP- ───→ SSR load terminal (in series with bus current path)
    VCC/GND/OUT ─── via 3-pin header (5V, GND, GPIO34)

  ACS712 Gen/Solar (P2):
    IP+ ───→ Solar panel / generator output
    IP- ───→ Toward load or charge controller input
    VCC/GND/OUT ─── via 3-pin header (5V, GND, GPIO35)

  ACS712 Node (P3):
    IP+ ───→ Vbus 12V (from J1)
    IP- ───→ LM2596 IN+ (in series with node supply current)
    VCC/GND/OUT ─── via 3-pin header (5V, GND, GPIO32)
```

### 5.3 Voltage Sensor Wiring

```
  Voltage Sensor Bus (U4):
    V+ ───→ DC Bus (+) measurement point
    V- ───→ GND
    VCC/GND/S ─── via 3-pin header (3V3, GND, GPIO36)

  Voltage Sensor Gen (U2):
    V+ ───→ Gen/solar source (+) measurement point
    V- ───→ GND
    VCC/GND/S ─── via 3-pin header (3V3, GND, GPIO39)
```

### 5.4 LM2596 Wiring

```
  LM2596 IN+  ───→ LM2596_CONN pin 1 (from J1 Vbus 12V)
  LM2596 IN-  ───→ LM2596_CONN pin 2 (GND)
  LM2596 OUT+ ───→ LM2596_CONN pin 3 (5V → NodeMCU Vin + ACS712 VCC)
  LM2596 OUT- ───→ LM2596_CONN pin 4 (GND)
```

## 6. Pre-Assembly Checklist

- [ ] LM2596 output adjusted to 5.0V (measure before connecting to carrier)
- [ ] All resistor values verified with multimeter
- [ ] BC547A pinout confirmed (CBE order for TO-92; flat face up: C-B-E left to right)
- [ ] LED polarity correct (longer lead = anode)
- [ ] Female headers oriented correctly (pins facing up)
- [ ] Screw terminals oriented correctly (wire entry facing outward)
- [ ] NodeMCU pins clean and straight
- [ ] ACS712 modules oriented correctly (IP+/IP- direction matches current flow)
- [ ] Voltage sensor modules oriented correctly (V+/V- direction)
- [ ] No solder bridges between traces (inspect with magnifier)

## 7. Post-Assembly Testing

### 7.1 Continuity Tests (Power OFF)

| Test | Expected | Notes |
|------|----------|-------|
| J1(+) to LM2596 IN+ | < 1Ω | Power path intact |
| LM2596 OUT+ to NodeMCU Vin | < 1Ω | 5V rail connected |
| LM2596 OUT+ to ACS712 VCC | < 1Ω | 5V to sensor modules |
| All GND pins connected | < 1Ω | Common ground verified |
| No shorts between 12V and GND | > 10kΩ | Critical safety check |

### 7.2 Power-On Test

1. Verify 5V output on LM2596 OUT+ before connecting NodeMCU
2. Connect 12V to J1 (observe correct polarity)
3. NodeMCU should boot automatically (5V from LM2596)
4. Check serial output for boot messages: node waits for SW1 press
5. Press SW1 — firmware starts SMG operation (LEDs: Yellow blink → Yellow solid → Green)
6. Verify ACS712 output at rest (~2.5V for 0A with 5V supply)
7. Press SW1 again — firmware stops SMG (LEDs off)

### 7.3 Module Tests

1. Check ACS712 output at rest (~2.5V for 0A — VCC/2 at 5V supply)
2. Check voltage divider output (V_measured / 5 ≈ displayed voltage)
3. Check PWM output on GPIO25/26/27 with oscilloscope (1kHz, variable duty)
4. Check LED operation: GPIO23=Red, GPIO22=Yellow, GPIO21=Green

## 8. CNC Milling Instructions

### 8.1 Tool Requirements

| Tool | Size | Purpose |
|------|------|---------|
| Endmill | 0.6mm (1/64") | Trace isolation routing |
| Endmill | 1.5mm (1/16") | Board outline cutting |
| Drill bit | 0.8mm | Header pins (NodeMCU, sensors) |
| Drill bit | 1.0mm | Standard component pins |
| Drill bit | 1.2mm | TO-92, larger pins |
| Drill bit | 1.5mm | Terminal block pins |
| Drill bit | 3.2mm | Mounting holes |

### 8.2 Milling Parameters

| Parameter | Value |
|-----------|-------|
| Spindle speed | 10,000–15,000 RPM |
| Feed rate (traces) | 200–300 mm/min |
| Feed rate (outline) | 100–150 mm/min |
| Depth of cut (traces) | 0.15–0.20 mm |
| Depth of cut (outline) | Full depth (1.6mm + 0.2mm) |
| Z-home | Top of copper clad |

### 8.3 File Usage

```
1. Load SMG_ControlNode_copper_top.gbr → Generate isolation toolpath
2. Load SMG_ControlNode_outline.gbr → Generate outline toolpath
3. Load SMG_ControlNode_drill.xln → Generate drill toolpath
4. (Optional) SMG_ControlNode_copper_bottom.gbr → Ground plane
```

### 8.4 Recommended Software

- **FlatCAM** (free) — Gerber to G-code conversion
- **bCNC** (free) — CNC control and G-code sender
- **Gerbv** (free) — Gerber file viewer

## 9. Bill of Materials (Carrier PCB Assembly)

### 9.1 Discrete Components (soldered to carrier)

| Qty | Component | Value/Part | Package | Designator |
|-----|-----------|------------|---------|------------|
| 3 | NPN Transistor | BC547A | TO-92 | Q1, Q2, Q4 |
| 1 | Pushbutton | 6mm NO | THT | SW1 |
| 3 | Resistor | 220Ω 1/4W | THT | R1, R2, R3 (LED series) |
| 3 | Resistor | 220Ω 1/4W | THT | R4, R12, R14 (BC547 base) |
| 4 | Resistor | 10kΩ 1/4W | THT | R5, R10, R11, R13 (pull-downs) |
| 1 | LED Red 5mm | any | THT | LED1 |
| 1 | LED Green 5mm | any | THT | LED2 |
| 1 | LED Yellow 5mm | any | THT | LED3 |

### 9.2 Connectors (soldered to carrier)

| Qty | Component | Type | Designator |
|-----|-----------|------|------------|
| 2 | Female header | 15-pin 2.54mm | NODEMCU_TOP, NODEMCU_BOT |
| 3 | Female header | 3-pin 2.54mm | ACS712_BUS, ACS712_GEN, ACS712_NODE |
| 2 | Female header | 3-pin 2.54mm | VDIV_BUS, VDIV_GEN |
| 1 | Screw terminal | 2-pin 5.08mm | J1_VBUS |
| 3 | Screw terminal | 2-pin 5.08mm | J2_SSR_BUS, J3_SSR_SOLAR, J4_SSR_GEN |
| 3 | Screw terminal | 2-pin 5.08mm | ACS712_BUS_IP, ACS712_GEN_IP, ACS712_NODE_IP |
| 2 | Screw terminal | 2-pin 5.08mm | VDIV_BUS_IN, VDIV_GEN_IN |
| 1 | Screw terminal | 4-pin 5.08mm | LM2596_CONN |

### 9.3 External Modules (plug into carrier)

| Qty | Module | Purpose |
|-----|--------|---------|
| 1 | ESP32 NodeMCU DevKit V1 | Main controller |
| 1 | LM2596 Buck Module | 12V→5V (powers ESP32 + ACS712s) |
| 3 | ACS712-05B Module | Current sensors (Bus, Gen/Solar, Node) |
| 2 | Voltage Sensor Module (0-25V) | Voltage sensors (Bus, Gen/Solar) |
| 3 | DC-DC SSR (IBFIA, 40A) | Energy flow control (Bus, Solar, Gen) |
| 3 | MCB (Schneider 2-pole) | Overcurrent protection per SSR |

## 10. Troubleshooting

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| NodeMCU doesn't boot | LM2596 not outputting 5V | Check LM2596 adjustment, verify 12V at J1 |
| SW1 press has no effect | R10 pull-down missing or GPIO18 wrong | Check R10(10kΩ) to GND, confirm D18=GPIO18 |
| ACS712 reads ~2.5V always | No current flow | Normal — 0A = VCC/2 = 2.5V at 5V supply |
| ACS712 reads unexpected value | VCC at 3.3V instead of 5V | Check LM2596 OUT+ connected to ACS712 VCC |
| Voltage reads 0V | V+ not connected | Check voltage sensor V+ wiring |
| PWM doesn't drive SSR | BC547 not conducting | Check 220Ω base resistor, verify GPIO25/26/27 |
| LED always red | Sensor/EMS error | Check serial output for error messages |
| Repeated reconnects | Primary node unreachable | Verify primary node running, check WiFi |
