# SMG Secure IoT Ecosystem — Companion Implementation

Companion code repository for the case study described in:

> R. Campos-Sanchez, L. Hernandez-Martinez, C. Feregrino-Uribe, A. Penuelas-Angulo, D. Cruz-Aguilar.
> *Closing the Security Activation Gap: A Security-by-Design Methodology for IoT Ecosystems and Its
> Empirical Validation in a DC Smart Microgrid.* Submitted to Computers & Security (Elsevier).

The paper validates a security-by-design IoT development methodology through a confirmatory case
study: an isolated DC smart microgrid (SMG) comprising two ESP32-based secondary control nodes and
a Raspberry Pi 4B primary coordination agent. This repository contains the software that implements
that case study's Root of Trust at Technology Readiness Level 3 (laboratory proof of concept).

Node identity is derived from a Physically Unclonable Function (PUF): each node's cryptographic
key pair is deterministically reconstructed from its ESP32's RTC FAST SRAM power-up state at every
boot, rather than generated once and stored. Device authentication is asymmetric (ECDSA); server
authentication is a per-node, nonce-bound hash-reconstruction proof. The protocol is an
**implementation-specific Stage 4 technology choice** for the SMG case study, not itself a
contribution of the methodology paper above — see the paper's Introduction (Scope note) and its
Root of Trust Establishment / STRIDE-to-Protocol sections.

## Repository layout

| Directory | Role | Hardware target |
|---|---|---|
| [`smg_puf_rot_node/`](smg_puf_rot_node/) | Secondary-node firmware: PUF-based identity, Root of Trust, secure-channel client | ESP32 (C / ESP-IDF) |
| [`smg_primary_server/`](smg_primary_server/) | Primary-agent software: PUF challenge-response server, node registry, EMS coordination | Raspberry Pi 4B (CPython) |

Each directory has its own `README.md` with setup and provisioning instructions.

## Reproducibility scope

This repository contains **source code only** — the exact software deployed for the paper's bench
trials. It intentionally does **not** include:

- Provisioned node secrets (`puf_node_registry.json`, `puf_server_identity.json`, NVS images) — see
  the `.example.json` templates and the Root of Trust provisioning steps in
  `smg_primary_server/README.md` and `smg_puf_rot_node/README.md`; these are generated fresh per
  deployment and must never be committed.
- Raw measurement data, logs, or the derived figures/tables reported in the paper. These are
  published in the companion artifact repository:
  <https://github.com/RazielCS/smg-secure-microgrid-paper-artifacts> — manuscript source, raw
  bench-trial measurements (CSVs, serial logs), expert-validation instruments, and the errata log.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

## Citation

If you use this code, please cite the paper above (citation details will be updated on acceptance).
