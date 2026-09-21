# SMG Secure IoT Ecosystem — Companion Implementation

Companion code repository for the case study described in:

> R. Campos-Sanchez, L. Hernandez-Martinez, C. Feregrino-Uribe, A. Penuelas-Angulo, D. Cruz-Aguilar.
> *Closing the Security Activation Gap: A Security-by-Design Methodology for IoT Ecosystems and Its
> Empirical Validation in a DC Smart Microgrid.* Submitted to Computers & Security (Elsevier).

The paper validates a security-by-design IoT development methodology through a confirmatory case
study: an isolated DC smart microgrid (SMG) comprising two ESP32-based secondary control nodes and
a Raspberry Pi 4B primary coordination agent. This repository contains the two software artifacts
that implement that case study at Technology Readiness Level 3 (laboratory proof of concept).

The SecureNode v3 authentication/data-exchange protocol (P1–P6) implemented here is an
**implementation-specific Stage 4 technology choice** for the SMG case study, described and evaluated
in a companion publication under separate peer review — it is not itself a contribution of the
methodology paper above. See the paper's Introduction (Scope note) and Section on protocol phases
for the distinction.

## Repository layout

| Directory | Role | Hardware target |
|---|---|---|
| [`smg_control_node/`](smg_control_node/) | Secondary-agent firmware: sensor acquisition, local fuzzy EMS, RoT + P1–P5 protocol client | ESP32 (MicroPython) |
| [`smg_primary_server/`](smg_primary_server/) | Primary-agent software: RoT registrar, PSK authentication service, global security-aware fuzzy EMS, P1–P6 protocol server | Raspberry Pi 4B (CPython) |

Each directory has its own `README.md` with setup and provisioning instructions, and
`smg_control_node/BUILD.md` / `WIRING_AND_DEPLOYMENT.md` for firmware build and hardware assembly
(schematics and gerbers under `smg_control_node/pcb/`).

## Reproducibility scope

This repository contains **source code and hardware design files only** — the exact software and
PCB design deployed for the paper's bench trials. It intentionally does **not** include:

- Provisioned node secrets (`node_registry.json`) — see `smg_primary_server/node_registry.example.json`
  and the Root of Trust provisioning steps in `smg_primary_server/README.md`; these are generated
  fresh per deployment and must never be committed.
- Raw measurement data, logs, or the derived figures/tables reported in the paper. These are
  published in the companion artifact repository:
  <https://github.com/RazielCS/smg-secure-microgrid-paper-artifacts> — manuscript source, raw
  bench-trial measurements (CSVs, serial logs, packet captures), the frozen firmware snapshot
  used on the bench, expert-validation instruments, and the errata log.
- Development-only diagnostic/debug scripts used to bring up WiFi, NVS provisioning, and the RPi
  access point during firmware bring-up; only the scripts needed to provision a node
  (`tools/provision_node.py`), run the dual-node bench trial (`tools/dual_bench_test.py`), and the
  SSR-startup workaround for a known dead-button board (`tools/ssr_on_startup.py`) are kept.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

## Citation

If you use this code, please cite the paper above (citation details will be updated on acceptance).
