#include <string.h>
#include <stdio.h>
#include "esp_log.h"
#include "esp_random.h"
#include "mbedtls/sha256.h"
#include "mbedtls/ecdsa.h"
#include "cJSON.h"

#include "rot_session.h"
#include "net_msg.h"

static const char *TAG = "rot_session";

#define CHALLENGE_LEN 16

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

static void sha256_2(const uint8_t *a, size_t a_len, const uint8_t *b, size_t b_len,
                      uint8_t out[ROT_DIGEST_LEN]) {
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, a, a_len);
    mbedtls_sha256_update(&ctx, b, b_len);
    mbedtls_sha256_finish(&ctx, out);
    mbedtls_sha256_free(&ctx);
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

    // --- Message 2: challenge_B + server_proof ---
    cJSON *challenge_msg = net_msg_recv(sock);
    if (challenge_msg == NULL) {
        ESP_LOGE(TAG, "handshake: failed to receive challenge message");
        goto done;
    }
    const cJSON *j_id_b = cJSON_GetObjectItemCaseSensitive(challenge_msg, "id_b");
    const cJSON *j_cb = cJSON_GetObjectItemCaseSensitive(challenge_msg, "challenge_b");
    const cJSON *j_proof = cJSON_GetObjectItemCaseSensitive(challenge_msg, "server_proof");

    uint8_t id_b_claimed[ROT_ID_LEN], challenge_b[CHALLENGE_LEN], server_proof[ROT_DIGEST_LEN];
    bool parsed = cJSON_IsString(j_id_b) && hex_to_bin(j_id_b->valuestring, id_b_claimed, ROT_ID_LEN) &&
                  cJSON_IsString(j_cb) && hex_to_bin(j_cb->valuestring, challenge_b, CHALLENGE_LEN) &&
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

    uint8_t expected_server_proof[ROT_DIGEST_LEN];
    sha256_2(shs_b_ref, ROT_DIGEST_LEN, challenge_a, CHALLENGE_LEN, expected_server_proof);
    if (memcmp(expected_server_proof, server_proof, ROT_DIGEST_LEN) != 0) {
        ESP_LOGE(TAG, "handshake: server authentication FAILED (proof mismatch)");
        goto done;
    }
    ESP_LOGI(TAG, "handshake: server authenticated");

    // --- Message 3: device_proof = ECDSA_sign(sk_A, SHA256(challenge_B || challenge_A)) ---
    // Asymmetric device authentication: the server never sees sk_A and cannot forge this
    // signature even if its own stored record (id_a -> pk_A) is fully compromised.
    uint8_t msg_hash[ROT_DIGEST_LEN];
    sha256_2(challenge_b, CHALLENGE_LEN, challenge_a, CHALLENGE_LEN, msg_hash);

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

    cJSON *response_msg = cJSON_CreateObject();
    cJSON_AddStringToObject(response_msg, "type", "response");
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

    // --- Key_ab = SHA256(V_B || challenge_A || challenge_B) ---
    // V_B is this node's distinct, per-node server verifier (not a value shared fleet-wide);
    // both sides already hold it (device: pinned NVS reference; server: per-node registry
    // entry), so no additional exchange is needed to agree on Key_ab.
    mbedtls_sha256_context ctx;
    mbedtls_sha256_init(&ctx);
    mbedtls_sha256_starts(&ctx, 0);
    mbedtls_sha256_update(&ctx, shs_b_ref, ROT_DIGEST_LEN);
    mbedtls_sha256_update(&ctx, challenge_a, CHALLENGE_LEN);
    mbedtls_sha256_update(&ctx, challenge_b, CHALLENGE_LEN);
    mbedtls_sha256_finish(&ctx, out_session->key_ab);
    mbedtls_sha256_free(&ctx);

    result = true;

done:
    mbedtls_mpi_free(&sk_a);
    return result;
}
