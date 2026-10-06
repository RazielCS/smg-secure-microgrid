// AES-256-GCM data channel (replaces the retired SecureNode P4 hash-chain: AEAD already
// authenticates each message, so no separate chain/audit mechanism is used -- see paper
// "Root of Trust Establishment"). Keyed by the session's Key_ab (rot_session_t).
#pragma once

#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

#define SECURE_CHANNEL_NONCE_LEN 12
#define SECURE_CHANNEL_TAG_LEN   16

// Encrypts `plaintext` under `key` (32 bytes) with a fresh random 12-byte nonce.
// *out_nonce, *out_ciphertext, *out_tag are caller-provided buffers of the sizes above /
// plaintext_len respectively. Returns false on mbedtls error.
bool secure_channel_encrypt(const uint8_t key[32], const uint8_t *plaintext, size_t plaintext_len,
                             uint8_t out_nonce[SECURE_CHANNEL_NONCE_LEN],
                             uint8_t *out_ciphertext, uint8_t out_tag[SECURE_CHANNEL_TAG_LEN]);

// Decrypts and verifies. Returns false if the GCM tag does not verify (tampering/wrong key).
bool secure_channel_decrypt(const uint8_t key[32], const uint8_t nonce[SECURE_CHANNEL_NONCE_LEN],
                             const uint8_t *ciphertext, size_t ciphertext_len,
                             const uint8_t tag[SECURE_CHANNEL_TAG_LEN],
                             uint8_t *out_plaintext);

// Sends a single JSON-serializable test payload over `sock`, AES-GCM encrypted under `key`,
// as {"type":"data","nonce":hex,"ciphertext":hex,"tag":hex}. Demo/verification helper for
// this task's scope (see rot_identity.h TODOs) -- not the full sensor/EMS data path.
bool secure_channel_send_demo_message(int sock, const uint8_t key[32], const char *json_payload);
