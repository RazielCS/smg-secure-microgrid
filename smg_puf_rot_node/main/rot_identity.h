// Device/server identity handling for the PUF-based Root of Trust
// (paper: "Root of Trust Establishment", Tables rotpsim / rotpsim_b).
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>
#include "mbedtls/bignum.h"

#define ROT_ID_LEN     16   // ID_A / ID_B length in bytes
#define ROT_DIGEST_LEN 32   // SHA-256 digest length (V_B, server_proof, session key)
#define ROT_PUBKEY_LEN 65   // uncompressed SECP256R1 point: 0x04 || X(32) || Y(32)

// ---- Device identity (ID_A) ----
// TODO(paper Limitations / provisioning gap): ID_A is currently a compile-time placeholder.
// A real deployment needs a per-device provisioning step writing a unique ID_A to NVS,
// analogous to how V_B/ID_B below are meant to be pinned once via a trusted provisioning
// channel. Not implemented in this pass.
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

// Derives this boot's device identity keypair (sk_A, pk_A) on SECP256R1 deterministically
// from the reconstructed PUF response, via HKDF-SHA256 expansion (RFC 5869) followed by
// rejection sampling to a uniform scalar in [1, n-1] (n = curve order). sk_A is returned as
// an initialized mbedtls_mpi that the caller must mbedtls_mpi_free() when done; it is used
// only locally to produce an ECDSA signature (rot_session.c) and is never serialized,
// transmitted, or stored -- it exists only transiently in RAM, exactly like the PUF
// response it is derived from (Section "Root of Trust Establishment"). pk_A is the
// corresponding 65-byte uncompressed public point (0x04 || X || Y); being public, it is
// safe to print, export over serial, and register with the server. This keypair is what
// the device proves possession of during P2 (asymmetric device authentication); server
// compromise discloses only pk_A, which is insufficient to forge a signature.
bool rot_identity_derive_keypair(const uint8_t *puf_response, size_t puf_len,
                                  mbedtls_mpi *out_sk_a, uint8_t out_pk_a[ROT_PUBKEY_LEN]);

// ---- Server reference, pinned once at provisioning (Table rotpsim Phase 1: "pin (store):
// {ID_B, V_B} as server reference"). The server is NOT PUF-based (see paper Root of Trust
// Establishment): V_B is a conventional, per-node value known only to the server's record
// for this specific node and to this pinned copy -- a distinct V_B is provisioned for each
// enrolled node (never shared fleet-wide), so recovering one node's V_B lets an attacker
// impersonate the server only to that single node, not to the fleet. Stored in this
// project's own "rot_identity" NVS namespace (kept separate from esp32_puflib's "storage"
// namespace).
bool rot_identity_load_server_ref(uint8_t id_b[ROT_ID_LEN], uint8_t shs_b_ref[ROT_DIGEST_LEN]);
bool rot_identity_save_server_ref(const uint8_t id_b[ROT_ID_LEN], const uint8_t shs_b_ref[ROT_DIGEST_LEN]);
