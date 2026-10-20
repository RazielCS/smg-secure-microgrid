# SMG Secure IoT Ecosystem — Companion Implementation

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23505100.svg)](https://doi.org/10.5281/zenodo.23505100)

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
| [`smg_primary_server/`](smg_primary_server/) | Primary-agent software: PUF challenge-response server, node registry (active); also holds a superseded module set, see Reproducibility scope below | Raspberry Pi 4B (CPython) |

Each directory has its own `README.md` with setup and provisioning instructions.

## Reproducibility scope

This repository contains **source code only**. The code that actually ran during the paper's
bench trials is `smg_puf_rot_node/` (firmware) and, on the server side, `puf_server.py` plus
`rot_challenge_service.py`.

`smg_primary_server/` also contains an earlier module set (`auth_service.py`, `server.py`,
`pc_client.py`, `protocol.py`, `crypto_utils.py`, `ems_coordinator.py`) from a prior,
superseded hash-chain-based protocol design. It is **not** the software deployed for the
reported bench trials; it is kept only because some of its functionality (anomaly-based trust
scoring and EMS coordination, Stage~7.2.1) has not yet been ported to `rot_challenge_service.py`
(see the paper's Limitations), and it is not guaranteed to run against the current
`rot_challenge_service.py` or `smg_puf_rot_node/` firmware.

This repository intentionally does **not** include:

- Provisioned node secrets (`puf_node_registry.json`, `puf_server_identity.json`, NVS images) — see
  the `.example.json` templates and the Root of Trust provisioning steps in
  `smg_primary_server/README.md` and `smg_puf_rot_node/README.md`; these are generated fresh per
  deployment and must never be committed.
- Raw measurement data, logs, or the derived figures/tables reported in the paper. These are
  published in the companion artifact repository:
  <https://github.com/RazielCS/smg-secure-microgrid-paper-artifacts> — manuscript source, raw
  bench-trial measurements (CSVs, serial logs), and expert-validation instruments.

## License

Apache License 2.0 — see [`LICENSE`](LICENSE).

## Citation

If you use this code, please cite the paper above (citation details will be updated on acceptance).
