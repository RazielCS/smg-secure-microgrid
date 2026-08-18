# SMG Control Node — Firmware Build and Deployment Guide

## Versioned Components

| Component | Version | Notes |
|---|---|---|
| MicroPython | **1.22.1** | ESP32 port; stable release |
| ESP-IDF | **5.0.x** | Bundled with MicroPython 1.22.x toolchain |
| `aesgcm` C module | commit pinned to MicroPython 1.22.1 | AES-256-GCM via ESP32 hardware crypto |
| `uhashlib` | Built-in | SHA-256 hardware-accelerated; no separate build needed |
| `blake3` C module | Optional | Not required for core operation |
| Python (host) | **3.11.x** | For provisioning scripts and test suite |

---

## Option A — Flash Pre-Built Firmware (Recommended for Reproduction)

The pre-built firmware binary includes MicroPython 1.22.1 with the `aesgcm` C module compiled in.

```
firmware/
  smg_esp32_micropython_1.22.1_aesgcm.bin   # Full firmware image
  smg_esp32_micropython_1.22.1_aesgcm.sha256 # SHA-256 checksum
```

### Verify integrity

```bash
# Linux / macOS
sha256sum -c smg_esp32_micropython_1.22.1_aesgcm.sha256

# Windows (PowerShell)
Get-FileHash firmware\smg_esp32_micropython_1.22.1_aesgcm.bin -Algorithm SHA256
# compare output against .sha256 file
```

### Flash to ESP32

```bash
# Install host tools
pip install -r requirements.txt

# Erase flash (recommended before first flash)
esptool.py --chip esp32 --port COM3 erase_flash

# Flash firmware
esptool.py --chip esp32 --port COM3 --baud 460800 write_flash -z 0x1000 \
    firmware/smg_esp32_micropython_1.22.1_aesgcm.bin
```

Replace `COM3` with the correct port (`/dev/ttyUSB0` on Linux, `/dev/cu.usbserial-*` on macOS).

---

## Option B — Build from Source

### 1. Clone MicroPython

```bash
git clone https://github.com/micropython/micropython.git
cd micropython
git checkout v1.22.1
git submodule update --init --recursive
```

### 2. Install ESP-IDF v5

```bash
# Per MicroPython docs: https://docs.micropython.org/en/v1.22.1/develop/gettingstarted.html
cd micropython/ports/esp32
make -f Makefile ESPIDF=
# Follow prompts to install ESP-IDF 5.0.x via idf.py
```

### 3. Add the `aesgcm` C module

The `aesgcm` module provides hardware-accelerated AES-256-GCM via the ESP32 crypto peripheral.

```bash
# Copy module source into the MicroPython usermod directory
cp -r <this_repo>/firmware/aesgcm_module/ micropython/ports/esp32/modules/aesgcm/
```

### 4. Build

```bash
cd micropython/ports/esp32
make BOARD=GENERIC USER_C_MODULES=<abs_path>/firmware/aesgcm_module/micropython.cmake -j4
```

Output: `build-GENERIC/firmware.bin`

### 5. Compute and record firmware hash

```bash
sha256sum build-GENERIC/firmware.bin > smg_esp32_micropython_1.22.1_aesgcm.sha256
cat smg_esp32_micropython_1.22.1_aesgcm.sha256
```

Record this hash in the paper's supplementary material for reproducibility.

---

## Upload Firmware Files

After flashing MicroPython, upload the Python source files:

```bash
# Using mpremote (recommended — handles filesystem sync)
cd smg_control_node/
mpremote connect COM3 cp main.py config_manager.py sensor_module.py \
    ems_module.py security_module.py comm_module.py secure_node.py \
    hkdf.py led_semaphore.py metrics.py :

mpremote connect COM3 cp config.json :

# Verify files are present
mpremote connect COM3 ls
```

### Alternative: ampy

```bash
for f in main.py config_manager.py sensor_module.py ems_module.py \
         security_module.py comm_module.py secure_node.py hkdf.py \
         led_semaphore.py metrics.py config.json; do
    ampy --port COM3 put $f
done
```

---

## Node Provisioning

Provision must be completed before the first boot. See `WIRING_AND_DEPLOYMENT.md` for the full procedure.

```bash
# Provision Node 1
python tools/provision_node.py --port COM3 --node-id SMG_NODE_01 --role secondary

# Provision Node 2
python tools/provision_node.py --port COM4 --node-id SMG_NODE_02 --role secondary

# Provision Node 3
python tools/provision_node.py --port COM5 --node-id SMG_NODE_03 --role secondary
```

The provisioning script writes `node_id`, `node_uuid`, `node_key` (32-byte random), `node_salt`, and WiFi credentials to the ESP32 NVS partition over USB serial. **No secrets are stored in config.json or any source file.**

---

## Running the Automated Test Suite

The test suite runs on standard CPython (no ESP32 hardware required). It exercises all pure-logic modules via MicroPython compatibility shims.

```bash
# Install dependencies
pip install -r requirements.txt

# Run all tests
cd smg_control_node/
pytest tests/ -v

# Run a specific module
pytest tests/test_hkdf.py -v
pytest tests/test_ems.py -v
pytest tests/test_security.py -v
```

Expected output:

```
tests/test_hkdf.py::test_rfc5869_tc1_extract       PASSED
tests/test_hkdf.py::test_rfc5869_tc1_full          PASSED
tests/test_hkdf.py::test_rfc5869_tc3_no_salt_info  PASSED
tests/test_hkdf.py::test_determinism               PASSED
tests/test_hkdf.py::test_key_uniqueness            PASSED
tests/test_ems.py::test_nominal_med_med            PASSED
tests/test_ems.py::test_low_low_single_rule        PASSED
tests/test_ems.py::test_high_high_single_rule      PASSED
tests/test_ems.py::test_duty_always_in_range       PASSED
tests/test_ems.py::test_no_fallback_triggered      PASSED
tests/test_ems.py::test_set_demand                 PASSED
tests/test_sensor.py::test_adc_to_voltage          PASSED
tests/test_sensor.py::test_acs712_zero_current     PASSED
tests/test_sensor.py::test_acs712_positive_current PASSED
tests/test_sensor.py::test_vdiv_input_voltage      PASSED
tests/test_sensor.py::test_median_filter           PASSED
tests/test_sensor.py::test_get_read_stats_float    PASSED
tests/test_metrics.py::test_welford_known_stddev   PASSED
tests/test_metrics.py::test_averages_are_float     PASSED
tests/test_metrics.py::test_per_operation_latency  PASSED
tests/test_metrics.py::test_send_failure_tracking  PASSED
tests/test_metrics.py::test_payload_avg_float      PASSED
tests/test_security.py::test_same_key_same_secret  PASSED
tests/test_security.py::test_different_key_different_secret PASSED
tests/test_security.py::test_aes_gcm_roundtrip     PASSED
tests/test_security.py::test_aes_gcm_tamper_detected PASSED
tests/test_security.py::test_p4_replay_rejected    PASSED

25 passed in X.XXs
```

---

## Reproducing the Case Study Measurements

To reproduce the performance measurements from Section 4.2 of the case study:

1. Build and flash firmware to 3 NodeMCU ESP32 boards
2. Provision each node (see above)
3. Start the primary server: `cd smg_primary_server && python server.py`
4. Power all 3 nodes simultaneously
5. Let the system run for 1 hour
6. Stop each node with Ctrl-C; the firmware prints a full metrics summary to serial
7. Retrieve `measurements/` files: `mpremote connect COMx cp :metrics.json .`

The metrics summary includes all values reported in Table 4.2: P2/P3 latency, P4 round-trip, sensor read time, EMS inference time, sampling rate, and heap statistics.
