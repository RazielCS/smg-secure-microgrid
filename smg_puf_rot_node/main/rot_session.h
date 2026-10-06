// Mutual challenge-response Root of Trust session (Phase 2, replaces the retired
// SecureNode 5-message PSK handshake). See paper "Root of Trust Establishment":
//   1. Device -> Server: {ID_A, challenge_A}
//   2. Server -> Device: {ID_B, challenge_B, server_proof = SHA256(ShS_B || challenge_A)}
//      Device verifies server_proof against its pinned ShS_B reference (server authentication).
//   3. Device -> Server: {device_proof = SHA256(ShS_A || challenge_B)}
//      ShS_A = SHA256(PUF_A), PUF_A reconstructed fresh this boot, never persisted.
//      Server verifies device_proof against its stored ShS_A reference (device identification).
//   4. Server -> Device: {session_ack}
// Both sides then independently derive:
//   Key_ab = SHA256(ShS_A || ShS_B || challenge_A || challenge_B)
// used as the AES-256-GCM key for the data channel (secure_channel.h) -- no hash-chain,
// no session archival/audit phase (AEAD already authenticates each message).
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include "rot_identity.h"

typedef struct {
    uint8_t key_ab[ROT_DIGEST_LEN];
} rot_session_t;

// Runs the full handshake over an already-connected TCP socket. `puf_response`/`puf_len` is
// this boot's reconstructed PUF response (caller obtained it via rot_identity_get_puf_response()
// and is still responsible for releasing it afterwards). On success, `out_session->key_ab` is
// populated. Returns false on any verification failure or I/O error.
bool rot_session_handshake(int sock, const uint8_t *puf_response, size_t puf_len,
                            rot_session_t *out_session);
