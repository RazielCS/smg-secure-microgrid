#include <string.h>
#include <stdlib.h>
#include "esp_log.h"
#include "esp_random.h"
#include "mbedtls/gcm.h"
#include "cJSON.h"

#include "secure_channel.h"
#include "net_msg.h"

static const char *TAG = "secure_channel";

bool secure_channel_encrypt(const uint8_t key[32], const uint8_t *plaintext, size_t plaintext_len,
                             uint8_t out_nonce[SECURE_CHANNEL_NONCE_LEN],
                             uint8_t *out_ciphertext, uint8_t out_tag[SECURE_CHANNEL_TAG_LEN]) {
    esp_fill_random(out_nonce, SECURE_CHANNEL_NONCE_LEN);

    mbedtls_gcm_context ctx;
    mbedtls_gcm_init(&ctx);
    int rc = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, 256);
    if (rc == 0) {
        rc = mbedtls_gcm_crypt_and_tag(&ctx, MBEDTLS_GCM_ENCRYPT, plaintext_len,
                                        out_nonce, SECURE_CHANNEL_NONCE_LEN,
                                        NULL, 0,
                                        plaintext, out_ciphertext,
                                        SECURE_CHANNEL_TAG_LEN, out_tag);
    }
    mbedtls_gcm_free(&ctx);
    if (rc != 0) {
        ESP_LOGE(TAG, "encrypt: mbedtls error 0x%04x", (unsigned)(-rc));
        return false;
    }
    return true;
}

bool secure_channel_decrypt(const uint8_t key[32], const uint8_t nonce[SECURE_CHANNEL_NONCE_LEN],
                             const uint8_t *ciphertext, size_t ciphertext_len,
                             const uint8_t tag[SECURE_CHANNEL_TAG_LEN],
                             uint8_t *out_plaintext) {
    mbedtls_gcm_context ctx;
    mbedtls_gcm_init(&ctx);
    int rc = mbedtls_gcm_setkey(&ctx, MBEDTLS_CIPHER_ID_AES, key, 256);
    if (rc == 0) {
        rc = mbedtls_gcm_auth_decrypt(&ctx, ciphertext_len,
                                       nonce, SECURE_CHANNEL_NONCE_LEN,
                                       NULL, 0,
                                       tag, SECURE_CHANNEL_TAG_LEN,
                                       ciphertext, out_plaintext);
    }
    mbedtls_gcm_free(&ctx);
    if (rc != 0) {
        ESP_LOGW(TAG, "decrypt: authentication failed (0x%04x)", (unsigned)(-rc));
        return false;
    }
    return true;
}

static void bin_to_hex(const uint8_t *bin, size_t len, char *out) {
    static const char hexd[] = "0123456789abcdef";
    for (size_t i = 0; i < len; ++i) {
        out[2 * i] = hexd[bin[i] >> 4];
        out[2 * i + 1] = hexd[bin[i] & 0x0F];
    }
    out[2 * len] = '\0';
}

bool secure_channel_send_demo_message(int sock, const uint8_t key[32], const char *json_payload) {
    size_t plaintext_len = strlen(json_payload);
    uint8_t nonce[SECURE_CHANNEL_NONCE_LEN];
    uint8_t tag[SECURE_CHANNEL_TAG_LEN];
    uint8_t *ciphertext = malloc(plaintext_len);
    if (ciphertext == NULL) {
        return false;
    }

    bool ok = secure_channel_encrypt(key, (const uint8_t *)json_payload, plaintext_len,
                                      nonce, ciphertext, tag);
    if (!ok) {
        free(ciphertext);
        return false;
    }

    char nonce_hex[SECURE_CHANNEL_NONCE_LEN * 2 + 1];
    char tag_hex[SECURE_CHANNEL_TAG_LEN * 2 + 1];
    char *ct_hex = malloc(plaintext_len * 2 + 1);
    if (ct_hex == NULL) {
        free(ciphertext);
        return false;
    }
    bin_to_hex(nonce, SECURE_CHANNEL_NONCE_LEN, nonce_hex);
    bin_to_hex(tag, SECURE_CHANNEL_TAG_LEN, tag_hex);
    bin_to_hex(ciphertext, plaintext_len, ct_hex);
    free(ciphertext);

    cJSON *msg = cJSON_CreateObject();
    cJSON_AddStringToObject(msg, "type", "data");
    cJSON_AddStringToObject(msg, "nonce", nonce_hex);
    cJSON_AddStringToObject(msg, "ciphertext", ct_hex);
    cJSON_AddStringToObject(msg, "tag", tag_hex);
    ok = net_msg_send(sock, msg);
    cJSON_Delete(msg);
    free(ct_hex);
    return ok;
}
