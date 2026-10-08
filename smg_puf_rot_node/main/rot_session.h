// Mutual Root of Trust session: asymmetric device authentication, symmetric per-node
// server authentication, ephemeral ECDH session key (forward secrecy). See paper
// "Root of Trust Establishment":
//   1. Device -> Server: {ID_A, challenge_A}
//   2. Server -> Device: {ID_B, challenge_B, eph_pk_B,
//                         server_proof = SHA256(V_B || challenge_A || eph_pk_B)}
//      V_B is this node's own server verifier (a distinct value per enrolled node, never
//      shared fleet-wide). eph_pk_B is a fresh P-256 public key generated for this session
//      only. Device verifies server_proof against its pinned V_B reference; this also
//      binds eph_pk_B to a party that knows V_B, so it cannot be substituted in transit.
//   3. Device -> Server: {eph_pk_A,
//                         device_proof = ECDSA_sign(sk_A, SHA256(DOMAIN_TAG || ID_A || ID_B
//                                                                || challenge_B || challenge_A
//                                                                || eph_pk_A || eph_pk_B
//                                                                || server_proof))}
//      (sk_A, pk_A) is a device identity keypair derived fresh each boot from PUF_A
//      (rot_identity_derive_keypair()); sk_A never leaves this function and is never
//      transmitted. eph_pk_A is a fresh P-256 public key generated for this session only.
//      DOMAIN_TAG is a fixed, protocol-specific string (DEVICE_PROOF_DOMAIN_TAG in
//      rot_session.c) that rules out cross-protocol signature reuse. The signed hash
//      covers the full transcript exchanged so far -- both identifiers, both nonces, both
//      ephemeral keys, and the server's own proof -- so the server knows the authenticated
//      device committed to this exact session end to end, not one a relay substituted or
//      spliced from another session. Server verifies device_proof against the device's
//      registered pk_A -- a public value, so a stolen server record cannot be used to
//      forge a device's proof.
//   4. Server -> Device: {session_ack}
// Both sides then independently compute the ECDH shared secret
//   Z = ECDH(eph_sk_A, eph_pk_B) = ECDH(eph_sk_B, eph_pk_A)
// and derive:
//   Key_ab = SHA256(Z || V_B || challenge_A || challenge_B)
// used as the AES-256-GCM key for the data channel (secure_channel.h). Both ephemeral key
// pairs are generated fresh each session and discarded immediately after use (never
// persisted), so recovering V_B from a node's flash after the fact -- e.g. via a read-only
// NVS dump, as demonstrated in c4_negative_test/ -- does not by itself allow decrypting a
// previously captured session: Z is also required, and no party retains it once the
// session ends. No hash-chain, no session archival/audit phase (AEAD already
// authenticates each message).
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
