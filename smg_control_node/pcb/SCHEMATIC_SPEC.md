# SMG Control Node PCB — Carrier Board Design (Commercial Modules)

## 1. Design Philosophy

This PCB is a **carrier board** that mounts commercial modules via female headers and screw terminals. No bare SMD components except the soft-latch circuit and passives.

### Modules Mounted on Carrier:
| Module | Mounting | Pins Used |
|--------|----------|-----------|
| ESP32 NodeMCU | 2× 15-pin female headers | All GPIOs accessible |
| LM2596 Buck | Screw terminals (IN/OUT) or 4-pin header | IN+, IN-, OUT+, OUT- |
| ACS712 #1 (Bus) | 3-pin female header + screw terminal | VCC, GND, OUT |
| ACS712 #2 (Gen) | 3-pin female header + screw terminal | VCC, GND, OUT |
| ACS712 #3 (Node) | 3-pin female header + screw terminal | VCC, GND, OUT — in series with LM2596 12V IN |
| Voltage Sensor #1 (Bus) | 3-pin female header + screw terminal | VCC, GND, S |
| Voltage Sensor #2 (Gen) | 3-pin female header + screw terminal | VCC, GND, S |

### Discrete Components on Carrier:
| Component | Purpose |
|-----------|---------|
| BC547A (×3) | SSR driver NPN transistors (Q1/Q2/Q4) |
| Resistors 220Ω (×6) | BC547 base limiters (R4/R12/R14) + LED current limiters (R1/R2/R3) |
| Resistors 10kΩ (×4) | BC547 base pull-downs (R5/R11/R13) + SW1 pull-down (R10) |
| Pushbutton SW1 | Init Control — start/stop SMG function (GPIO18 InitPulse) |
| Red LED (LED1) | Status indicator — Red (GPIO23 via R1=220Ω) |
| Green LED (LED2) | Status indicator — Green (GPIO21 via R2=220Ω) |
| Yellow LED (LED3) | Status indicator — Yellow (GPIO22 via R3=220Ω) |
| SSR terminals | 3× 2-pin screw terminals (J2/J3/J4) |
| Vbus input | 2-pin screw terminal (J1) |

## 2. Module Pinouts

### 2.1 ESP32 NodeMCU (DevKit V1)

```
  ┌──────────────────────────────────────────────────────────────┐
  │  USB                                                         │
  │  ─┐                                                         │
  │  │ │  EN   G23  G22  G21  G19  G18  G5   G17  G16  G4   G0  │  ← Top row
  │  │ │                                                         │
  │  │ │  3V3  G36  G39  G34  G35  G32  G33  G25  G26  G27  G14 │  ← Bottom row
  │  └─┘                                                         │
  │      BOOT                                                    │
  ──────────────────────────────────────────────────────────────┘

  Pin mapping (NodeMCU silkscreen → ESP32 GPIO):
    D0  = GPIO16   D1  = GPIO5    D2  = GPIO18   D3  = GPIO19
    D4  = GPIO21   D5  = GPIO22   D6  = GPIO23   D7  = GPIO4
    D8  = GPIO0    D9  = GPIO2    D10 = GPIO15   D11 = GPIO13
    D12 = GPIO12   D13 = GPIO14   D14 = GPIO27   D15 = GPIO26
    D16 = GPIO25   D17 = GPIO33   D18 = GPIO32   D19 = GPIO35
    D20 = GPIO34   D21 = GPIO39   D22 = GPIO36   (D23 is an alias for D14/GPIO27 — not a separate pin)
    
  Power pins:
    3V3  = 3.3V output (max 500mA)
    5V   = 5V input (from USB or VIN)
    VIN  = 5V input (alternative to 5V pin)
    GND  = Ground (multiple pins)
    EN   = Enable (active HIGH, pull HIGH to run)
```

**Pins used by SMG firmware:**
| NodeMCU Pin | GPIO | Function |
|-------------|------|----------|
| D27 | GPIO27 | SSR Generator PWM output (GenCtrl) |
| D26 | GPIO26 | SSR Solar PWM output (SolarCtrl) |
| D25 | GPIO25 | SSR Bus PWM output (BusCtrl) |
| D32 | GPIO32 | ACS712 node self-consumption current (ADC1 CH4) |
| D34 | GPIO34 | ACS712 bus current (ADC1 CH6) |
| D35 | GPIO35 | ACS712 gen/solar current (ADC1 CH7) |
| VP / GPIO36 | GPIO36 | Voltage divider bus voltage (ADC1 CH0) |
| VN / GPIO39 | GPIO39 | Voltage divider gen/solar voltage (ADC1 CH3) |
| D23 | GPIO23 | Red LED (LED1, via R1=220Ω) |
| D22 | GPIO22 | Yellow LED (LED3, via R3=220Ω) |
| D21 | GPIO21 | Green LED (LED2, via R2=220Ω) |
| D18 | GPIO18 | SW1 InitPulse input (active HIGH, R10=10kΩ pull-down) |
| Vin | 5V | 5V from LM2596 buck output |
| 3V3 | 3V3 | Voltage sensor VCC supply |
| GND | GND | Common ground |

> **Note on GPIO39 label in schematic:** The EasyEDA component symbol shows "GPIO29" for this pin, which does not exist on the ESP32. The correct designation is GPIO39 (VN pin = ADC1_CH3).

### 2.2 LM2596 Buck Module

```
  ┌─────────────────────────────────────────┐
  │  [220µF]  [IC]  [330µH]  [220µF]  [POT]│
  │                                         │
  │  IN+   IN-          OUT+   OUT-         │
  │  [===] [===]        [===]  [===]        │
  └─────────────────────────────────────────┘

  Connections:
    IN+  → Vbus 12V (from J1 — direct from DC bus)
    IN-  → GND
    OUT+ → 5V → ESP32 NodeMCU Vin + ACS712 VCC (×3)
    OUT- → GND
    POT  → Adjust to 5.0V output (set before assembly)
```

### 2.3 ACS712 Module

```
  ┌────────────────────────────────┐
  │  [ACS712 IC]                   │
  │                                │
  │  VCC  GND  OUT    [IP+ IP-]   │
  │  [===][===][===]  [========]  │
  └────────────────────────────────┘

  Connections:
    VCC  → LM2596 5V output (Vref = VCC/2 = 2.5V)
    GND  → GND
    OUT  → ESP32 ADC pin (GPIO34, GPIO35, or GPIO32)
    IP+  → Current source
    IP-  → Current sink (in series with measured path)
```

### 2.4 Voltage Sensor Module

```
  ┌────────────────────────────────┐
  │  [Resistors]                   │
  │                                │
  │  VCC  GND  S      [V+ V-]     │
  │  [===][===][===]  [========]  │
  └────────────────────────────────┘

  Connections:
    VCC  → ESP32 3V3
    GND  → GND
    S    → ESP32 ADC pin (GPIO36 or GPIO39)
    V+   → Voltage to measure (DC bus or solar)
    V-   → GND
```

## 3. Carrier PCB Layout

```
  ┌──────────────────────────────────────────────────────────────────────┐
  │  100mm × 80mm — 2-layer PCB — CNC milled                            │
  │                                                                      │
  │  J1(Vbus)  SW1                                                       │
  │  ┌──┐     ┌─┐         LM2596 Module                                 │
  │  │  │     │ │         ┌──────────────────┐                          │
  │  └──┘     └─┘         │  IN+  IN-  OUT+OUT-│                        │
  │  (12V)  (InitPulse)   └──────────────────┘                          │
  │                                                                      │
  │  Q1(BC547A) Q2(BC547A) Q4(BC547A)   ← SSR drivers                                             │
  │                        │                                             │
  │           ┌────────────────────────────────────────────┐            │
  │           │         ESP32 NodeMCU (mounted vertically) │            │
  │           │  ┌──────────────────────────────────────┐  │            │
  │           │  │  USB  [EN G23...G0]                  │  │            │
  │           │  │       [3V3 G36...G14]                │  │            │
  │           │  └──────────────────────────────────────┘  │            │
  │           │  [2×15-pin female headers]                 │            │
  │           └────────────────────────────────────────────┘            │
  │                                                                      │
  │  ACS712#1    ACS712#2    Vdiv#1      Vdiv#2                        │
  │  ┌────┐     ┌────┐     ┌────┐      ┌────┐                          │
  │  │V G O│     │V G O│     │V G S│      │V G S│                      │
  │  └────┘     └────┘     └────┘      └────┘                          │
  │  [IP+IP-]   [IP+IP-]   [V+ V-]    [V+ V-]                          │
  │                                                                      │
  │  J2(SSRBus) J3(SSRSol) J4(SSRGen)   LED1(R) LED2(G) LED3(Y)        │
  │  ┌────┐   ┌────┐    ┌────┐         ┌─┐                              │
  │  │    │   │    │    │    │         │ │                              │
  │  └────┘   └────┘    └────┘         └─┘                              │
  └──────────────────────────────────────────────────────────────────────┘
```

## 4. Power Supply and Init Control

The node is powered **directly from the DC voltage bus** (Vbus). There is no hardware power switch — the node is always on while the bus has energy. The LM2596 buck converter steps Vbus (12V) down to 5V for the ESP32 and sensor modules.

```
  Vbus (12V DC bus) ──[J1 screw terminal]──→ LM2596 IN+
                                              LM2596 IN− → GND
                                              LM2596 OUT+ → 5V → ESP32 Vin
                                              LM2596 OUT− → GND

  ACS712 modules powered at 5V (from LM2596 OUT+) → Vref = VCC/2 = 2.5V
  Voltage sensor modules powered at 3V3 (from ESP32 3V3 output)
```

**Init Control Circuit (SW1 — InitPulse):**

```
  VCC ──[SW1 NO pushbutton]──→ InitPulse net ──→ GPIO18 (D18)
                                     │
                               R10 (10kΩ)
                                     │
                                    GND
```

- Default (button released): GPIO18 = LOW (pulled down by R10)
- Button pressed: GPIO18 = HIGH (VCC through SW1)
- Firmware detects rising edge → starts/stops SMG operation (SSRs + telemetry)

## 5. Complete Netlist (Module-Based)

```
(Net "VBUS_12V"
  (Pin "J1" "1")              ; Vbus screw terminal — direct 12V from DC bus
  (Pin "U3_LM2596" "Vin")    ; LM2596 buck input
  (Pin "J2_SSR_BUS" "1")     ; SSR Bus +IN (direct 12V control supply)
  (Pin "J3_SSR_SOLAR" "1")   ; SSR Solar +IN
  (Pin "J4_SSR_GEN" "1")     ; SSR Gen +IN
)

(Net "GND"
  (Pin "J1" "2")              ; Vbus GND
  (Pin "U3_LM2596" "GND_in") ; LM2596 input GND
  (Pin "U3_LM2596" "GND_out"); LM2596 output GND
  (Pin "U1_NODEMCU" "GND")   ; NodeMCU GND (multiple pins)
  (Pin "P1_ACS712_BUS" "GND"); ACS712 Bus GND
  (Pin "P2_ACS712_GEN" "GND"); ACS712 Gen/Solar GND
  (Pin "P3_ACS712_NODE" "GND"); ACS712 Node GND
  (Pin "U2_VDIV_GEN" "GND")  ; Voltage div Gen GND
  (Pin "U4_VDIV_BUS" "GND")  ; Voltage div Bus GND
  (Pin "Q1_BC547_BUS" "E")   ; BC547 Bus emitter
  (Pin "Q2_BC547_SOLAR" "E") ; BC547 Solar emitter
  (Pin "Q4_BC547_GEN" "E")   ; BC547 Gen emitter
  (Pin "R10_10k" "2")        ; SW1 pull-down low side
  (Pin "LED1_R" "C")         ; Red LED cathode
  (Pin "LED2_G" "C")         ; Green LED cathode
  (Pin "LED3_Y" "C")         ; Yellow LED cathode
  (Pin "J2_SSR_BUS" "2")     ; SSR Bus −IN return (also BC547 collector)
  (Pin "J3_SSR_SOLAR" "2")   ; SSR Solar −IN return
  (Pin "J4_SSR_GEN" "2")     ; SSR Gen −IN return
)

(Net "5V_BUS"
  (Pin "U3_LM2596" "Vout")   ; LM2596 output (+5V regulated)
  (Pin "U1_NODEMCU" "Vin")   ; NodeMCU 5V input
  (Pin "P1_ACS712_BUS" "5V") ; ACS712 Bus VCC (5V → Vref=2.5V)
  (Pin "P2_ACS712_GEN" "5V") ; ACS712 Gen VCC
  (Pin "P3_ACS712_NODE" "5V"); ACS712 Node VCC
)

(Net "3V3_SENSE"
  (Pin "U1_NODEMCU" "3V3")   ; NodeMCU 3V3 output
  (Pin "U2_VDIV_GEN" "VCC")  ; Voltage div Gen VCC
  (Pin "U4_VDIV_BUS" "VCC")  ; Voltage div Bus VCC
)

(Net "BusCurr"
  (Pin "P1_ACS712_BUS" "VO") ; ACS712 Bus analog output
  (Pin "U1_NODEMCU" "D34")   ; GPIO34 ADC1 CH6
)

(Net "GenCurr"
  (Pin "P2_ACS712_GEN" "VO") ; ACS712 Gen analog output
  (Pin "U1_NODEMCU" "D35")   ; GPIO35 ADC1 CH7
)

(Net "SelfCurr"
  (Pin "P3_ACS712_NODE" "VO"); ACS712 Node analog output
  (Pin "U1_NODEMCU" "D32")   ; GPIO32 ADC1 CH4
)

(Net "BusVolt"
  (Pin "U4_VDIV_BUS" "S")    ; Voltage div Bus signal output
  (Pin "U1_NODEMCU" "GPIO36"); GPIO36 (VP) ADC1 CH0
)

(Net "GenVolt"
  (Pin "U2_VDIV_GEN" "S")    ; Voltage div Gen signal output
  (Pin "U1_NODEMCU" "GPIO39"); GPIO39 (VN) ADC1 CH3
  ; NOTE: component symbol in EasyEDA incorrectly shows "GPIO29" for this pin
)

(Net "InitPulse"
  (Pin "SW1" "2")             ; SW1 pushbutton output side
  (Pin "R10_10k" "1")        ; pull-down resistor (other end → GND)
  (Pin "U1_NODEMCU" "D18")   ; GPIO18 — firmware reads button press
)

(Net "SW1_VCC"
  (Pin "SW1" "1")             ; SW1 input side — connected to VCC
)

(Net "BusCtrl"
  (Pin "U1_NODEMCU" "D25")   ; GPIO25 PWM output
  (Pin "R4_220" "1")         ; BC547 Bus base resistor (input)
)

(Net "BusCtrl_BASE"
  (Pin "R4_220" "2")         ; BC547 Bus base resistor (output)
  (Pin "Q1_BC547_BUS" "B")   ; BC547 Bus base
  (Pin "R5_10k" "1")         ; base pull-down (other end → GND)
)

(Net "SSR_BUS_CTRL"
  (Pin "Q1_BC547_BUS" "C")   ; BC547 Bus collector → SSR Bus −IN
  (Pin "J2_SSR_BUS" "2")     ; SSR Bus −IN terminal
)

(Net "SolarCtrl"
  (Pin "U1_NODEMCU" "D26")   ; GPIO26 PWM output
  (Pin "R12_220" "1")        ; BC547 Solar base resistor (input)
)

(Net "SolarCtrl_BASE"
  (Pin "R12_220" "2")        ; BC547 Solar base resistor (output)
  (Pin "Q2_BC547_SOLAR" "B") ; BC547 Solar base
  (Pin "R11_10k" "1")        ; base pull-down (other end → GND)
)

(Net "SSR_SOLAR_CTRL"
  (Pin "Q2_BC547_SOLAR" "C") ; BC547 Solar collector → SSR Solar −IN
  (Pin "J3_SSR_SOLAR" "2")   ; SSR Solar −IN terminal
)

(Net "GenCtrl"
  (Pin "U1_NODEMCU" "D27")   ; GPIO27 PWM output
  (Pin "R14_220" "1")        ; BC547 Gen base resistor (input)
)

(Net "GenCtrl_BASE"
  (Pin "R14_220" "2")        ; BC547 Gen base resistor (output)
  (Pin "Q4_BC547_GEN" "B")   ; BC547 Gen base
  (Pin "R13_10k" "1")        ; base pull-down (other end → GND)
)

(Net "SSR_GEN_CTRL"
  (Pin "Q4_BC547_GEN" "C")   ; BC547 Gen collector → SSR Gen −IN
  (Pin "J4_SSR_GEN" "2")     ; SSR Gen −IN terminal
)

(Net "LedR"
  (Pin "U1_NODEMCU" "D23")   ; GPIO23 PWM/digital output
  (Pin "R1_220" "1")         ; Red LED series resistor
)

(Net "LED1_ANODE"
  (Pin "R1_220" "2")
  (Pin "LED1_R" "A")         ; Red LED anode
)

(Net "LedY"
  (Pin "U1_NODEMCU" "D22")   ; GPIO22 PWM/digital output
  (Pin "R3_220" "1")         ; Yellow LED series resistor
)

(Net "LED3_ANODE"
  (Pin "R3_220" "2")
  (Pin "LED3_Y" "A")         ; Yellow LED anode
)

(Net "LedG"
  (Pin "U1_NODEMCU" "D21")   ; GPIO21 PWM/digital output
  (Pin "R2_220" "1")         ; Green LED series resistor
)

(Net "LED2_ANODE"
  (Pin "R2_220" "2")
  (Pin "LED2_G" "A")         ; Green LED anode
)
## 6. Connector Specifications

### 6.1 Module Headers (Female, mounted on carrier PCB)

| Header | Pins | Pitch | Module |
|--------|------|-------|--------|
| NODEMCU_TOP | 15 | 2.54mm | ESP32 NodeMCU top row |
| NODEMCU_BOT | 15 | 2.54mm | ESP32 NodeMCU bottom row |
| ACS712_BUS | 3 | 2.54mm | ACS712 Bus current module (P1) |
| ACS712_GEN | 3 | 2.54mm | ACS712 Gen/Solar current module (P2) |
| ACS712_NODE | 3 | 2.54mm | ACS712 Node self-consumption module (P3) |
| VDIV_BUS | 3 | 2.54mm | Voltage sensor Bus (U4) |
| VDIV_GEN | 3 | 2.54mm | Voltage sensor Gen/Solar (U2) |

### 6.2 Screw Terminals (mounted on carrier PCB)

| Terminal | Pins | Pitch | Purpose |
|----------|------|-------|---------|
| J1_VBUS | 2 | 5.08mm | Vbus 12V input (direct from DC bus) |
| J2_SSR_BUS | 2 | 5.08mm | SSR Bus control output |
| J3_SSR_SOLAR | 2 | 5.08mm | SSR Solar control output |
| J4_SSR_GEN | 2 | 5.08mm | SSR Generator control output |
| ACS712_BUS_IP | 2 | 5.08mm | ACS712 Bus current path (in-series) |
| ACS712_GEN_IP | 2 | 5.08mm | ACS712 Gen current path (in-series) |
| ACS712_NODE_IP | 2 | 5.08mm | ACS712 Node current path (12V LM2596 input line) |
| VDIV_BUS_IN | 2 | 5.08mm | Bus voltage input |
| VDIV_GEN_IN | 2 | 5.08mm | Gen voltage input |

### 6.3 LM2596 Connection

4-pin screw terminal block: LM2596 IN+, IN−, OUT+, OUT−.
Set buck output to 5.0 V before assembling (verify with multimeter).

## 7. Bill of Materials (Carrier PCB Only)

### 7.1 Discrete Components (soldered to carrier)

| Qty | Component | Value | Package | Designator | Purpose |
|-----|-----------|-------|---------|------------|---------|
| 3 | NPN Transistor | BC547A | TO-92 | Q1, Q2, Q4 | SSR driver (Bus, Solar, Gen) |
| 1 | Pushbutton | 6mm NO | THT | SW1 | InitPulse — start/stop SMG |
| 3 | Resistor | 220Ω 1/4W | THT | R1, R2, R3 | LED series current limiters |
| 3 | Resistor | 220Ω 1/4W | THT | R4, R12, R14 | BC547 base current limiters |
| 4 | Resistor | 10kΩ 1/4W | THT | R5, R10, R11, R13 | BC547 base pull-downs + SW1 pull-down |
| 1 | LED | Red 5mm | THT | LED1 | Status: error / shutdown |
| 1 | LED | Green 5mm | THT | LED2 | Status: online / running |
| 1 | LED | Yellow 5mm | THT | LED3 | Status: init / connecting |

### 7.2 Connectors (soldered to carrier)

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

### 7.3 External Modules (plug into carrier)

| Qty | Module | Purpose |
|-----|--------|---------|
| 1 | ESP32 NodeMCU DevKit V1 | Main controller |
| 1 | LM2596 Buck Module | 12V→5V converter (powers ESP32 and ACS712s) |
| 3 | ACS712-05B Module | Current sensors (Bus, Gen/Solar, Node self-consumption) |
| 2 | Voltage Sensor Module (0-25V) | Voltage sensors (Bus, Gen/Solar) |
| 3 | DC-DC SSR (IBFIA) | Energy flow control (Bus, Solar, Gen) |
| 3 | MCB (Schneider 2-pole) | Overcurrent protection per SSR channel |

## 8. PCB Specifications

- **Layers:** 2 (Top copper + Bottom copper)
- **Board size:** 100mm × 80mm
- **Board thickness:** 1.6mm
- **Copper weight:** 1oz (35µm)
- **Min trace width:** 0.3mm (12mil)
- **Min trace spacing:** 0.3mm (12mil)
- **Min drill size:** 0.8mm (for header pins)
- **Solder mask:** None (CNC milled)
- **Surface finish:** Bare copper

### 8.1 Power Trace Widths
- 12V Vbus trace: 1.5mm (handles up to 5A)
- 5V trace: 1.0mm (handles up to 3A)
- 3V3 trace: 0.5mm (handles up to 500mA)
- Signal traces: 0.3mm
- Ground plane: Bottom layer, solid pour

## 9. Assembly Instructions

### 9.1 Solder Discrete Components First
1. Resistors (R1-R5, R10-R14)
2. BC547A transistors Q1, Q2, Q4 (CBE pinout — confirm with datasheet)
3. Pushbutton SW1
4. LEDs: LED1 (Red, GPIO23), LED2 (Green, GPIO21), LED3 (Yellow, GPIO22)
   - Longer lead = anode; match to correct GPIO pad

### 9.2 Solder Connectors
1. Female headers (NodeMCU ×2, ACS712 ×3, Voltage sensors ×2)
2. Screw terminals (J1-J4, ACS712_IP ×3, VDIV_IN ×2, LM2596_CONN)

### 9.3 Mount Modules
1. **LM2596:** Adjust potentiometer to 5.0V output BEFORE mounting
2. **ESP32 NodeMCU:** Plug into female headers (USB port facing edge)
3. **ACS712 modules:** Plug into 3-pin headers, wire IP+/IP- to screw terminals
4. **Voltage sensor modules:** Plug into 3-pin headers, wire V+/V- to screw terminals

### 9.4 External Wiring
- Vbus 12V: J1 → LM2596 IN+ and SSR +IN terminals
- SSR control: J2 (Bus), J3 (Solar), J4 (Gen) → SSR −IN terminals
- SSR load: Source → MCB → SSR A1; SSR A2 → DC Bus
- Current sensors: In series with current path
- Voltage sensors: V+ to measurement point, V- to GND

## 10. Testing Procedure

### 10.1 Pre-Power Checks
1. Verify no shorts between 12V and GND
2. Verify no shorts between 5V and GND
3. Verify all header pins soldered correctly
4. Verify LM2596 output set to 5.0V

### 10.2 Power-On Test
1. Connect 12V to J1 (correct polarity)
2. NodeMCU boots automatically (no button needed for power)
3. Verify 5V at NodeMCU Vin pin and ACS712 VCC pins
4. Serial output shows: "Ready. Press SW1 to start SMG operation..."
5. Press SW1 → SMG starts (LEDs: Yellow blink → Yellow solid → Green)

### 10.3 Module Tests
1. Check ACS712 output at rest (~2.5V for 0A — VCC/2 at 5V supply)
2. Check voltage divider output (V_measured / 5)
3. Check PWM output on GPIO25/26/27
4. Check LED operation: Red=GPIO23, Yellow=GPIO22, Green=GPIO21
