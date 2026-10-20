#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_random.h"
#include "mbedtls/sha256.h"
#include "mbedtls/ecdsa.h"
#include "mbedtls/ecdh.h"
#include "cJSON.h"

#include "rot_session.h"
#include "net_msg.h"

static const char *TAG = "rot_session";

#define CHALLENGE_LEN 16

// Domain-separation tag for device_proof's signed hash (rot_challenge_service.py must use
// the exact same bytes). Without this, a signature produced for this protocol could in
// principle be replayed as a valid signature in an unrelated protocol that also signs
// SHA-256 digests with the same key -- binding a fixed, protocol-specific tag into the
// hash input rules that out.
static const char DEVICE_PROOF_DOMAIN_TAG[] = "SMG-RoT-device-proof-v1";

static void bin_to_hex(const uint8_t *bin, size_t len, char *out /* len*2+1 */) {
    static const char hexd[] = "0123456789abcdef";
    for (size_t i = 0; i < len; ++i) {
        out[2 * i] = hexd[bin[i] >> 4];
        out[2 * i + 1] = hexd[bin[i] & 0x0F];
    }
    out[2 * len] = '\0';
}

static bool hex_to_bin(const char *hex, uint8_t *out, size_t out_len) {
    if (hex == NULL || strlen(hex) != out_len * 2) {
        return false;
    }
    for (size_t i = 0; i < out_len; ++i) {
        unsigned int byte;
        if (sscanf(hex + 2 * i, "%2x", &byte) != 1) {
            return false;
        }
        out[i] = (uint8_t)byte;
    }
    return true;
}

static int rot_session_rng(void *ctx, unsigned char *out, size_t len) {
    (void)ctx;
    esp_fill_random(out, len);
    return 0;
}

bool rot_session_handshake(int sock, const uint8_t *puf_response, size_t puf_len,
                            rot_session_t *out_session) {
    uint8_t id_a[ROT_ID_LEN];
    uint8_t id_b_ref[ROT_ID_LEN], shs_b_ref[ROT_DIGEST_LEN];
    rot_identity_get_id_a(id_a);
    if (!rot_identity_load_server_ref(id_b_ref, shs_b_ref)) {
        ESP_LOGE(TAG, "handshake: no pinned server reference (provisioning not done)");
        return false;
    }

    mbedtls_mpi sk_a;
    uint8_t pk_a[ROT_PUBKEY_LEN];
    if (!rot_identity_derive_keypair(puf_response, puf_len, &sk_a, pk_a)) {
        ESP_LOGE(TAG, "handshake: failed to derive device identity keypair from PUF response");
        return false;
    }

    bool result = false;

    // Ephemeral ECDH key pair for this session only -- generated fresh every handshake,
    // never persisted, discarded (mbedtls_ecp_group_free/mbedtls_mpi_free below) once the
    // shared secret Z is computed. This is what gives Key_ab forward secrecy: V_B alone
    // (recoverable from a flash dump) is not enough to recompute Z for a past session.
    mbedtls_ecp_group eph_grp;
    mbedtls_ecp_group_init(&eph_grp);
    mbedtls_mpi eph_sk_a;
    mbedtls_mpi_init(&eph_sk_a);
    mbedtls_ecp_point eph_pk_a_pt;
    mbedtls_ecp_point_init(&eph_pk_a_pt);
    uint8_t eph_pk_a[ROT_PUBKEY_LEN];
    bool eph_ready = false;

    if (mbedtls_ecp_group_load(&eph_grp, MBEDTLS_ECP_DP_SECP256R1) == 0 &&
        mbedtls_ecp_gen_keypair(&eph_grp, &eph_sk_a, &eph_pk_a_pt, rot_session_rng, NULL) == 0) {
        size_t eph_pk_a_len = 0;
        if (mbedtls_ecp_point_write_binary(&eph_grp, &eph_pk_a_pt, MBEDTLS_ECP_PF_UNCOMPRESSED,
                                            &eph_pk_a_len, eph_pk_a, ROT_PUBKEY_LEN) == 0 &&
            eph_pk_a_len == ROT_PUBKEY_LEN) {
            eph_ready = true;
        }
    }
    if (!eph_ready) {
        ESP_LOGE(TAG, "handshake: failed to generate ephemeral ECDH key pair");
        goto done;
    }

    // --- Message 1: hello + challenge_A ---
    uint8_t challenge_a[CHALLENGE_LEN];
    esp_fill_random(challenge_a, sizeof(challenge_a));

    char id_a_hex[ROT_ID_LEN * 2 + 1], ca_hex[CHALLENGE_LEN * 2 + 1];
    bin_to_hex(id_a, ROT_ID_LEN, id_a_hex);
    bin_to_hex(challenge_a, CHALLENGE_LEN, ca_hex);

    cJSON *hello = cJSON_CreateObject();
    cJSON_AddStringToObject(hello, "type", "hello");
    cJSON_AddStringToObject(hello, "id_a", id_a_hex);
    cJSON_AddStringToObject(hello, "challenge_a", ca_hex);
    bool ok = net_msg_send(sock, hello);
    cJSON_Delete(hello);
    if (!ok) {
        ESP_LOGE(TAG, "handshake: failed to send hello");
        goto done;
    }

    // --- Message 2: challenge_B + eph_pk_B + server_proof ---
    cJSON *challenge_msg = net_msg_recv(sock);
    if (challenge_msg == NULL) {
        ESP_LOGE(TAG, "handshake: failed to receive challenge message");
        goto done;
    }
    const cJSON *j_id_b = cJSON_GetObjectItemCaseSensitive(challenge_msg, "id_b");
    const cJSON *j_cb = cJSON_GetObjectItemCaseSensitive(challenge_msg, "challenge_b");
    const cJSON *j_eph_pk_b = cJSON_GetObjectItemCaseSensitive(challenge_msg, "eph_pk_b");
    const cJSON *j_proof = cJSON_GetObjectItemCaseSensitive(challenge_msg, "server_proof");

    uint8_t id_b_claimed[ROT_ID_LEN], challenge_b[CHALLENGE_LEN];
    uint8_t eph_pk_b[ROT_PUBKEY_LEN], server_proof[ROT_DIGEST_LEN];
    bool parsed = cJSON_IsString(j_id_b) && hex_to_bin(j_id_b->valuestring, id_b_claimed, ROT_ID_LEN) &&
                  cJSON_IsString(j_cb) && hex_to_bin(j_cb->valuestring, challenge_b, CHALLENGE_LEN) &&
                  cJSON_IsString(j_eph_pk_b) && hex_to_bin(j_eph_pk_b->valuestring, eph_pk_b, ROT_PUBKEY_LEN) &&
                  cJSON_IsString(j_proof) && hex_to_bin(j_proof->valuestring, server_proof, ROT_DIGEST_LEN);
    cJSON_Delete(challenge_msg);
    if (!parsed) {
        ESP_LOGE(TAG, "handshake: malformed challenge message");
        goto done;
    }
    if (memcmp(id_b_claimed, id_b_ref, ROT_ID_LEN) != 0) {
        ESP_LOGE(TAG, "handshake: unexpected server id_b");
        goto done;
    }

    // expected_server_proof = SHA256(V_B || challenge_A || eph_pk_B) -- binding eph_pk_B
    // into the proof means a relay cannot substitute its own ephemeral key here without
    // the device detecting it (it would not match what a real V_B holder would compute).
    {
        mbedtls_sha256_context ctx;
        mbedtls_sha256_init(&ctx);
        mbedtls_sha256_starts(&ctx, 0);
        mbedtls_sha256_update(&ctx, shs_b_ref, ROT_DIGEST_LEN);
        mbedtls_sha256_update(&ctx, challenge_a, CHALLENGE_LEN);
        mbedtls_sha256_update(&ctx, eph_pk_b, ROT_PUBKEY_LEN);
        uint8_t expected_server_proof[ROT_DIGEST_LEN];
        mbedtls_sha256_finish(&ctx, expected_server_proof);
        mbedtls_sha256_free(&ctx);
        if (memcmp(expected_server_proof, server_proof, ROT_DIGEST_LEN) != 0) {
            ESP_LOGE(TAG, "handshake: server authentication FAILED (proof mismatch)");
            goto done;
        }
    }
    ESP_LOGI(TAG, "handshake: server authenticated");

    // --- Message 3: eph_pk_A + device_proof = ECDSA_sign(sk_A,
    //       SHA256(DOMAIN_TAG || id_A || id_B || challenge_B || challenge_A
    //              || eph_pk_A || eph_pk_B || server_proof)) ---
    // Asymmetric device authentication: the server never sees sk_A and cannot forge this
    // signature even if its own stored record (id_a -> pk_A) is fully compromised. The
    // signed hash covers the full transcript exchanged so far -- both identifiers, both
    // nonces, both ephemeral keys, and the server's own proof -- under a fixed
    // protocol-specific domain tag, so the signature cannot be replayed across a different
    // id_a/id_b pairing, a different session, or an unrelated protocol that also signs
    // SHA-256 digests with this key, and a relay cannot swap any one of these fields in
    // transit undetected.
    uint8_t msg_hash[ROT_DIGEST_LEN];
    {
        mbedtls_sha256_context ctx;
        mbedtls_sha256_init(&ctx);
        mbedtls_sha256_starts(&ctx, 0);
        mbedtls_sha256_update(&ctx, (const unsigned char *)DEVICE_PROOF_DOMAIN_TAG,
                               sizeof(DEVICE_PROOF_DOMAIN_TAG) - 1);
        mbedtls_sha256_update(&ctx, id_a, ROT_ID_LEN);
        mbedtls_sha256_update(&ctx, id_b_claimed, ROT_ID_LEN);
        mbedtls_sha256_update(&ctx, challenge_b, CHALLENGE_LEN);
        mbedtls_sha256_update(&ctx, challenge_a, CHALLENGE_LEN);
        mbedtls_sha256_update(&ctx, eph_pk_a, ROT_PUBKEY_LEN);
        mbedtls_sha256_update(&ctx, eph_pk_b, ROT_PUBKEY_LEN);
        mbedtls_sha256_update(&ctx, server_proof, ROT_DIGEST_LEN);
        mbedtls_sha256_finish(&ctx, msg_hash);
        mbedtls_sha256_free(&ctx);
    }

    mbedtls_ecdsa_context ecdsa;
    mbedtls_ecdsa_init(&ecdsa);
    int rc = mbedtls_ecp_group_load(&ecdsa.MBEDTLS_PRIVATE(grp), MBEDTLS_ECP_DP_SECP256R1);
    if (rc == 0) {
        rc = mbedtls_mpi_copy(&ecdsa.MBEDTLS_PRIVATE(d), &sk_a);
    }
    uint8_t sig[MBEDTLS_ECDSA_MAX_LEN];
    size_t sig_len = 0;
    if (rc == 0) {
        rc = mbedtls_ecdsa_write_signature(&ecdsa, MBEDTLS_MD_SHA256, msg_hash, sizeof(msg_hash),
                                            sig, sizeof(sig), &sig_len, rot_session_rng, NULL);
    }
    mbedtls_ecdsa_free(&ecdsa);
    if (rc != 0) {
        ESP_LOGE(TAG, "handshake: ECDSA signing failed (mbedtls rc=-0x%04x)", (unsigned)-rc);
        goto done;
    }

    char dp_hex[MBEDTLS_ECDSA_MAX_LEN * 2 + 1];
    bin_to_hex(sig, sig_len, dp_hex);
    char eph_pk_a_hex[ROT_PUBKEY_LEN * 2 + 1];
    bin_to_hex(eph_pk_a, ROT_PUBKEY_LEN, eph_pk_a_hex);

    cJSON *response_msg = cJSON_CreateObject();
    cJSON_AddStringToObject(response_msg, "type", "response");
    cJSON_AddStringToObject(response_msg, "eph_pk_a", eph_pk_a_hex);
    cJSON_AddStringToObject(response_msg, "device_proof", dp_hex);
    ok = net_msg_send(sock, response_msg);
    cJSON_Delete(response_msg);
    if (!ok) {
        ESP_LOGE(TAG, "handshake: failed to send device_proof");
        goto done;
    }

    // --- Message 4: session_ack (or error) ---
    cJSON *ack_msg = net_msg_recv(sock);
    if (ack_msg == NULL) {
        ESP_LOGE(TAG, "handshake: failed to receive session_ack");
        goto done;
    }
    const cJSON *j_type = cJSON_GetObjectItemCaseSensitive(ack_msg, "type");
    bool acked = cJSON_IsString(j_type) && strcmp(j_type->valuestring, "session_ack") == 0;
    cJSON_Delete(ack_msg);
    if (!acked) {
        ESP_LOGE(TAG, "handshake: device authentication rejected by server");
        goto done;
    }
    ESP_LOGI(TAG, "handshake: device authenticated, session established");

    // --- Z = ECDH(eph_sk_A, eph_pk_B); Key_ab = SHA256(Z || V_B || challenge_A || challenge_B) ---
    {
        mbedtls_ecp_point peer_pt;
        mbedtls_ecp_point_init(&peer_pt);
        rc = mbedtls_ecp_point_read_binary(&eph_grp, &peer_pt, eph_pk_b, ROT_PUBKEY_LEN);
        mbedtls_mpi z;
        mbedtls_mpi_init(&z);
        if (rc == 0) {
            rc = mbedtls_ecdh_compute_shared(&eph_grp, &z, &peer_pt, &eph_sk_a,
                                              rot_session_rng, NULL);
        }
        uint8_t z_bytes[32];
        if (rc == 0) {
            rc = mbedtls_mpi_write_binary(&z, z_bytes, sizeof(z_bytes));
        }
        mbedtls_mpi_free(&z);
        mbedtls_ecp_point_free(&peer_pt);
        if (rc != 0) {
            ESP_LOGE(TAG, "handshake: ECDH shared-secret computation failed (mbedtls rc=-0x%04x)",
                      (unsigned)-rc);
            goto done;
        }

        mbedtls_sha256_context ctx;
        mbedtls_sha256_init(&ctx);
        mbedtls_sha256_starts(&ctx, 0);
        mbedtls_sha256_update(&ctx, z_bytes, sizeof(z_bytes));
        mbedtls_sha256_update(&ctx, shs_b_ref, ROT_DIGEST_LEN);
        mbedtls_sha256_update(&ctx, challenge_a, CHALLENGE_LEN);
        mbedtls_sha256_update(&ctx, challenge_b, CHALLENGE_LEN);
        mbedtls_sha256_finish(&ctx, out_session->key_ab);
        mbedtls_sha256_free(&ctx);
    }

    result = true;

done:
    mbedtls_mpi_free(&sk_a);
    mbedtls_mpi_free(&eph_sk_a);
    mbedtls_ecp_point_free(&eph_pk_a_pt);
    mbedtls_ecp_group_free(&eph_grp);
    return result;
}
