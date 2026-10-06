// Device/server identity handling for the PUF-based Root of Trust
// (paper: "Root of Trust Establishment", Tables rotpsim / rotpsim_b).
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define ROT_ID_LEN     16   // ID_A / ID_B length in bytes
#define ROT_DIGEST_LEN 32   // SHA-256 digest length (ShS_*, proofs)

// ---- Device identity (ID_A) ----
// TODO(paper Limitations / provisioning gap): ID_A is currently a compile-time placeholder.
// A real deployment needs a per-device provisioning step writing a unique ID_A to NVS
// (mirroring the MicroPython firmware's node_id), analogous to how ShS_B/ID_B below are
// meant to be pinned once via a trusted provisioning channel. Not implemented in this pass.
void rot_identity_get_id_a(uint8_t id_a[ROT_ID_LEN]);

// ---- PUF-based device identity key material (Phase 1/2) ----
// Runs esp32_puflib's enrollment exactly once (idempotent across the multiple deep-sleep
// resets the library uses internally -- see esp32_puflib README and app_main.c). Must be
// called at the top of app_main() on every boot; only completes (returns true) once the
// full RTC+deep-sleep measurement sequence has finished, which may span several resets.
bool rot_identity_run_enrollment_step(void);

// True once PUF_MASK/ECC_DATA are present in NVS (esp32_puflib's own "storage" namespace).
bool rot_identity_is_enrolled(void);

// Reconstructs this boot's PUF response (Phase 2, Table rotpsim_b: get_puf_response() using
// mask_A/ecc_A). *out_response is malloc'd by esp32_puflib; caller must call
// rot_identity_release_puf_response() when done (wraps clean_puf_response()).
//
// NOTE: per esp32_puflib's own README usage pattern, a single reconstruction attempt that
// falls outside the library's reliability threshold is NOT a terminal failure -- the expected
// recovery is get_puf_response_reset(), which resets the chip and retries from scratch on the
// next boot (app_main() re-entered; enrollment is already done so it falls straight through).
// This function implements that retry internally, so a "this attempt was unreliable" failure
// causes the device to reset instead of returning false. A false return here means the device
// is not enrolled at all (rot_identity_is_enrolled() == false) -- a precondition error, not a
// transient reconstruction failure.
bool rot_identity_get_puf_response(uint8_t **out_response, size_t *out_len);
void rot_identity_release_puf_response(void);

// ShS_A = HKDF(PUF_A) in the paper's notation; here implemented as SHA-256(PUF_A) for
// simplicity (single-application KDF, no separate salt input required by the corrected
// challenge-response design -- see rot_session.h). Held only in RAM by the caller.
void rot_identity_derive_shs_a(const uint8_t *puf_response, size_t puf_len,
                                uint8_t shs_a[ROT_DIGEST_LEN]);

// ---- Server reference, pinned once at provisioning (Table rotpsim Phase 1: "pin (store):
// {ID_B, ShS_B} as server reference"). The server is NOT PUF-based (see paper Root of Trust
// Establishment): ShS_B is a conventional value known only to the server and to this pinned
// copy, not physically reconstructed. Stored in this project's own "rot_identity" NVS
// namespace (kept separate from esp32_puflib's "storage" namespace).
//
// TODO(provisioning gap, same as ID_A above): no host-side provisioning tool exists yet for
// this C firmware (the MicroPython tools/provision_node.py does not apply here). Until one is
// written, rot_identity_save_server_ref() must be called from a temporary bring-up path (e.g.
// a one-off debug build) with values coordinated out-of-band with whoever updates the RPi4
// server counterpart -- flagged in the task report as a pending integration item.
bool rot_identity_load_server_ref(uint8_t id_b[ROT_ID_LEN], uint8_t shs_b_ref[ROT_DIGEST_LEN]);
bool rot_identity_save_server_ref(const uint8_t id_b[ROT_ID_LEN], const uint8_t shs_b_ref[ROT_DIGEST_LEN]);
