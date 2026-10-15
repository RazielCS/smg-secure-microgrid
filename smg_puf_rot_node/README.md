# smg_puf_rot_node

Pure C / ESP-IDF firmware for the SMG's PUF-based Root of Trust. Implements identity, Root of
Trust, and secure-communication only — sensor reading and fuzzy EMS control are **not** ported
here (see the paper's Limitations).

Protocol summary (see the paper, "Root of Trust Establishment" and "STRIDE-to-Protocol Mapping"
for the full description):

- **P1 — Enrollment:** `esp32_puflib`'s `enroll_puf()` reads the ESP32's RTC FAST SRAM
  power-up state across repeated measurements and derives a non-secret stable-bit mask and
  ECC helper data, stored in NVS. The device derives an elliptic-curve (P-256) key pair from
  its PUF response (HKDF, RFC 5869, with rejection sampling) and registers its public key
  `pk_a` with the server once; the private key `sk_a` is never transmitted or stored.
- **P2 — Root of Trust (session identification):** a 4-message nonce-bound mutual
  authentication (`rot_session.c`). Asymmetric on the device side (ECDSA signature over the
  full transcript, verified against `pk_a`) and symmetric on the server side (a per-node
  verifier `V_B`, proven via a nonce-bound hash). Each side also exchanges a fresh,
  session-only ephemeral EC key pair; `Key_ab = SHA-256(Z || V_B || challenge_A ||
  challenge_B)`, where `Z` is the resulting ECDH shared secret, discarded after use, giving
  the session key forward secrecy. Neither `sk_a`, `V_B`, nor `Z` is ever transmitted.
- **P3 — Secure channel:** AES-256-GCM (`secure_channel.c`, via `mbedtls`) under `Key_ab`.

Server-side counterpart: `smg_primary_server/rot_challenge_service.py` + `puf_server.py`.

## Dependencies

- ESP-IDF **v5.4.2** (other versions not tested).
- [`esp32_puflib`](https://github.com/staniond/esp32_puflib) (Stanicek/CTU FIT, 2022, MIT
  license) — vendored under `components/esp32_puflib/` with the fixes this work made to it;
  see that directory's README for the exact diff from upstream. No separate clone or
  `EXTRA_COMPONENT_DIRS` entry needed: ESP-IDF auto-discovers `components/` subdirectories.

## Build and flash

```
cd smg-secure-microgrid/smg_puf_rot_node
idf.py set-target esp32
idf.py build
idf.py -p <PORT> flash monitor
```

`sdkconfig.defaults` enables the enlarged NVS partition (`partitions_puf.csv`, 0x50000 bytes
for the PUF helper data + server reference), HKDF, and hardware-accelerated AES/SHA/ECDSA.

WiFi SSID/password are **not** a firmware-source constant: `wifi_station.c` reads them from
NVS (namespace `wifi_creds`) at runtime, provisioned the same way as the server reference
below (`tools/provision_server_ref.py --wifi-ssid/--wifi-pass`) — the real bench credentials
are not published (see Materials Availability in the paper).

## Provisioning a node

1. First boot after flashing: the device enrolls (`enroll_puf()`), then — since no server
   reference is pinned yet — prints a one-time **provisioning export** block over USB serial:
   `id_a` and the device's public key `pk_a` in hex, plus a ready-to-run `register_puf_node.py`
   command.
2. On the server host, establish the server's own identity once (fleet-wide, non-secret
   `id_b`): `python tools/provision_server_ref.py` (run with no `--id-b` argument to generate
   and persist a fresh one into `smg_primary_server/puf_server_identity.json`).
3. Register the node from the printed export: `python register_puf_node.py --id-a <id_a>
   --pk-a <pk_a> --label <node-label>` (writes to `puf_node_registry.json`, and generates a
   fresh per-node server verifier `V_B` for this node).
4. Pin the server reference (and WiFi credentials) on the device. **Caveat (open issue,
   found in a journal review pass, 2026-10-15):** `provision_server_ref.py --out-bin` calls
   ESP-IDF's `nvs_partition_gen.py`, which always builds a complete replacement NVS image from
   its CSV — there is no merge mode. Flashing that image after step 1 has already run
   overwrites the PUF enrollment (`PUF_MASK`/`ECC_DATA`/etc.) along with everything else,
   forcing a full re-enrollment (and a new `pk_a`) on the next boot. There is currently no
   supported way to flash *only* the server-reference/WiFi namespaces without this risk; this
   work's own bring-up worked around it with a temporary, hand-written firmware patch that
   wrote those two namespaces via the live NVS API instead of reflashing the partition (not
   committed here, since it is a one-off debugging aid, not reusable tooling). Until
   `provision_server_ref.py` gains a real merge mode (read back the device's existing
   `PUF_MASK`/`ECC_DATA` and include them unchanged in the generated image), treat steps 1–4
   as needing to run together on a device you are willing to fully re-enroll, not as
   independently repeatable steps.
5. Subsequent boots: enrollment + server reference both present in NVS → the device
   connects to WiFi, connects to the server, reconstructs its PUF response, and runs the
   P2/P3 handshake automatically.

## Reproducing the bench trial (Table "PUF-based RoT real-hardware bench trial" in the paper)

The raw bench data (30 hard-reset trials against SMG_NODE_01) is published in the companion
artifact repository:
`case study SMG/Implementation/smg_puf_rot_node/bench_results_20261015_fixed/` at
https://github.com/RazielCS/smg-secure-microgrid-paper-artifacts — including the per-run
raw serial logs, the parsed `summary.csv`, the bench script itself (`run_bench.py`), and the
server-side log captured during the run. Earlier rounds in the same directory
(`bench_results_20261015/`, `bench_results_20261013/`, `bench_results_20261005/`) are
superseded and kept for provenance; see that directory's own README for what changed between
them.

To reproduce against your own hardware:

1. Flash and provision a node as above; confirm one successful manual handshake first.
2. Start the server: `cd smg_primary_server && python puf_server.py` (listens on port 5001;
   loads `puf_server_identity.json` + `puf_node_registry.json` from its own directory).
3. `pip install pyserial` (in addition to `smg_primary_server/requirements.txt` on the
   server host), then from this repository:
   ```
   python bench_results_20261015_fixed/run_bench.py --port <ESP32_SERIAL_PORT> --runs 30 --window 45
   ```
   (`run_bench.py` is published alongside the data in the artifact repository, not in this
   source repository, since it is a measurement script rather than firmware/server source;
   copy it next to an empty `logs/` directory before running.) The script hard-resets the
   chip via RTS/DTR toggling before each trial and parses the resulting serial log for the
   PUF reconstruction-attempt and handshake-phase markers; see its own module docstring for
   the full regex/column reference.
