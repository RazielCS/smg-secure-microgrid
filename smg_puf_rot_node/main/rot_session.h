// Mutual Root of Trust session: asymmetric device authentication, symmetric per-node
// server authentication. See paper "Root of Trust Establishment":
//   1. Device -> Server: {ID_A, challenge_A}
//   2. Server -> Device: {ID_B, challenge_B, server_proof = SHA256(V_B || challenge_A)}
//      V_B is this node's own server verifier (a distinct value per enrolled node, never
//      shared fleet-wide). Device verifies server_proof against its pinned V_B reference.
//   3. Device -> Server: {device_proof = ECDSA_sign(sk_A, SHA256(challenge_B || challenge_A))}
//      (sk_A, pk_A) is a device identity keypair derived fresh each boot from PUF_A
//      (rot_identity_derive_keypair()); sk_A never leaves this function and is never
//      transmitted. Server verifies device_proof against the device's registered pk_A --
//      a public value, so a stolen server record cannot be used to forge a device's proof.
//   4. Server -> Device: {session_ack}
// Both sides then independently derive:
//   Key_ab = SHA256(V_B || challenge_A || challenge_B)
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
