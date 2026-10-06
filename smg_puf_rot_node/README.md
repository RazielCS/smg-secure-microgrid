# smg_puf_rot_node

Pure C / ESP-IDF firmware for the SMG's PUF-based Root of Trust redesign (no MicroPython,
no BLAKE3). Implements identity, Root of Trust, and secure-communication only — sensor
reading and fuzzy EMS control are **not** ported here (see the paper's Limitations); for
those, see `smg_control_node/` (the prior PSK+NVS/MicroPython instantiation).

Protocol summary (see the paper, "Root of Trust Establishment" and the Methodology section
for the full description):

- **P1 — Enrollment:** `esp32_puflib`'s `enroll_puf()` reads the ESP32's RTC FAST SRAM
  power-up state across repeated measurements and derives a non-secret stable-bit mask and
  ECC helper data, stored in NVS. These never disclose the PUF response without physical
  access to the specific chip.
- **P2 — Root of Trust (session identification):** a 4-message nonce-bound hash-reconstruction
  challenge (`rot_session.c`). Neither party ever transmits its verifier or the underlying
  PUF response; `Key_ab = SHA-256(ShS_A || ShS_B || challenge_A || challenge_B)`.
- **P3 — Secure channel:** AES-256-GCM (`secure_channel.c`, via `mbedtls`) under `Key_ab`.

Server-side counterpart: `smg_primary_server/rot_challenge_service.py` + `puf_server.py`.

## Dependencies

- ESP-IDF **v5.4.2** (other versions not tested).
- [`esp32_puflib`](https://github.com/staniond/esp32_puflib) (Stanicek/CTU FIT, 2022, MIT
  license) — **not vendored here**; clone it as a sibling of this repository's root:
  ```
  git clone https://github.com/RazielCS/smg-secure-microgrid
  git clone https://github.com/staniond/esp32_puflib smg-secure-microgrid/esp32_puflib
  ```
  `CMakeLists.txt`'s `EXTRA_COMPONENT_DIRS` expects this exact layout.

## Build and flash

```
cd smg-secure-microgrid/smg_puf_rot_node
idf.py set-target esp32
idf.py build
idf.py -p <PORT> flash monitor
```

`sdkconfig.defaults` enables the enlarged NVS partition (`partitions_puf.csv`, 0x50000 bytes
for the PUF helper data + server reference) and hardware-accelerated AES/SHA/GCM.

**Before building**, set a real WiFi passphrase in `main/wifi_station.c`
(`WIFI_PASS "CHANGE_ME_WIFI_PASSWORD"`) — the real bench password is not published, for the
same reason the prior PSK+NVS firmware's default AP password was redacted (see
`ERRATA.md`, item m1).

## Provisioning a node

1. First boot after flashing: the device enrolls (`enroll_puf()`), then — since no server
   reference is pinned yet — prints a one-time **provisioning export** block over USB serial:
   `id_a` and `shs_a_ref` in hex, plus a ready-to-run `register_puf_node.py` command.
2. On the server host, generate the server's own identity once:
   `smg_primary_server/tools/` does not include a server-identity generator in this
   repository; see `smg_primary_server/README.md`/`rot_challenge_service.py` docstring for
   the expected `puf_server_identity.json` shape (`{"id_b": "<32 hex chars>", "shs_b": "<64
   hex chars>"}`) and generate your own (e.g. `os.urandom(16).hex()` / `os.urandom(32).hex()`).
3. Register the node from the printed export: `python register_puf_node.py --id-a <id_a>
   --shs-a-ref <shs_a_ref> --label <node-label>` (writes to `puf_node_registry.json`).
4. Pin the server reference on the device: `tools/provision_server_ref.py` builds an NVS
   partition image (`nvs_partition_gen.py` from the ESP-IDF toolchain) containing `id_b`/
   `shs_b_ref`, flashed at the NVS partition offset. **This flash wipes the whole NVS
   partition, including the PUF enrollment** — the device will re-enroll (and derive a
   *different* `shs_a_ref`) on its next boot, so step 1–3 must be repeated with the new
   value if this happens after enrollment.
5. Subsequent boots: enrollment + server reference both present in NVS → the device
   connects to WiFi, connects to the server, reconstructs its PUF response, and runs the
   P2/P3 handshake automatically.

## Reproducing the bench trial (Table "PUF-based RoT real-hardware bench trial" in the paper)

The raw bench data (30 hard-reset trials, both the superseded 2026-10-02 round and the
round reported in the paper, 2026-10-06) is published in the companion artifact repository:
`case_study_SMG/Implementation/smg_puf_rot_node/bench_results_20261005/` at
https://github.com/RazielCS/smg-secure-microgrid-paper-artifacts — including the per-run
raw serial logs, the parsed `summary.csv`, the bench script itself (`run_bench.py`), and the
server-side log (`server_log_20261005.txt`) captured during the run.

To reproduce against your own hardware:

1. Flash and provision a node as above; confirm one successful manual handshake first.
2. Start the server: `cd smg_primary_server && python puf_server.py` (listens on port 5001;
   loads `puf_server_identity.json` + `puf_node_registry.json` from its own directory).
3. `pip install pyserial` (in addition to `smg_primary_server/requirements.txt` on the
   server host), then from this repository:
   ```
   python bench_results_20261005/run_bench.py --port <ESP32_SERIAL_PORT> --runs 30 --window 35
   ```
   (`run_bench.py` is published alongside the data in the artifact repository, not in this
   source repository, since it is a measurement script rather than firmware/server source;
   copy it next to an empty `logs/` directory before running.) The script hard-resets the
   chip via RTS/DTR toggling before each trial and parses the resulting serial log for the
   PUF reconstruction-attempt and handshake-phase markers; see its own module docstring for
   the full regex/column reference.
