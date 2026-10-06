#include <string.h>
#include "esp_log.h"
#include "esp_attr.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "mbedtls/sha256.h"

#include "puflib.h"
#include "rot_identity.h"

static const char *TAG = "rot_identity";

#define ROT_NVS_NAMESPACE "rot_identity"
#define ROT_NVS_KEY_ID_B   "id_b"
#define ROT_NVS_KEY_SHS_B  "shs_b_ref"

void rot_identity_get_id_a(uint8_t id_a[ROT_ID_LEN]) {
    // Placeholder fixed ID -- see TODO in rot_identity.h. "SMG_NODE_PUF01" padded to 16 bytes.
    static const char placeholder[] = "SMG_NODE_PUF01";
    memset(id_a, 0, ROT_ID_LEN);
    memcpy(id_a, placeholder, sizeof(placeholder) - 1);
}

bool rot_identity_run_enrollment_step(void) {
    puflib_init();
    // BUG FIX (found after real-hardware testing, see paper Limitations): enroll_puf()'s own
    // state machine treats PUFLIB_STATE.state == NONE as "never enrolled, start provisioning"
    // -- but puflib_init() ALSO returns to state == NONE after finishing a PUF_RESPONSE_RESET
    // recovery (get_puf_response_reset()'s wake path, handled by puf_response_reset_calculate()
    // just above in this same puflib_init() call). The library itself cannot tell these two
    // cases apart from PUFLIB_STATE alone. Calling enroll_puf() unconditionally therefore
    // re-triggers a full ~20-measurement re-provisioning cycle every time a reconstruction
    // retry wakes from a PUF_RESPONSE_RESET deep sleep -- observed on real hardware as an
    // unbounded reset loop that never reaches the code after enrollment. We already have an
    // independent, NVS-backed signal for "actually enrolled" (rot_identity_is_enrolled(), which
    // checks for PUF_MASK/ECC_DATA blobs rather than PUFLIB_STATE), so use that to decide
    // whether enroll_puf() should run at all. During real, in-progress provisioning the blobs
    // do not exist yet, so this still calls through normally in that case.
    if (!rot_identity_is_enrolled()) {
        enroll_puf();
    }
    // PUFLIB_STATE lives in RTC_DATA_ATTR inside esp32_puflib (puf_measurement.c); it is not
    // exposed via puflib.h, so completion is inferred the same way the library's own README
    // example does: PUF_STATE stays RESPONSE_CLEAN until a response has actually been fetched,
    // but the authoritative "provisioning finished" signal is that enroll_puf() returned
    // without the device having reset itself internally (get_pufsleep_bit_frequency calls
    // esp_deep_sleep_start() and never returns until the last iteration). Reaching this line at
    // all therefore means either provisioning just completed or was already done previously.
    return rot_identity_is_enrolled();
}

bool rot_identity_is_enrolled(void) {
    nvs_handle_t h;
    if (nvs_open("storage", NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t len = 0;
    esp_err_t err_mask = nvs_get_blob(h, "PUF_MASK", NULL, &len);
    esp_err_t err_ecc = ESP_FAIL;
    if (err_mask == ESP_OK) {
        len = 0;
        err_ecc = nvs_get_blob(h, "ECC_DATA", NULL, &len);
    }
    nvs_close(h);
    return err_mask == ESP_OK && err_ecc == ESP_OK;
}

// Counts PUF_RESPONSE_RESET retry cycles across the deep-sleep resets get_puf_response_reset()
// causes. RTC_DATA_ATTR survives deep sleep (same mechanism esp32_puflib uses for its own
// PUFLIB_STATE/PUF_STATE), but is cleared on a power-on/hard reset, which is exactly the scope
// we want: a fresh USB power cycle gets a fresh retry budget.
#define ROT_PUF_RESET_RETRY_LIMIT 5
static RTC_DATA_ATTR int s_puf_reset_retry_count = 0;

bool rot_identity_get_puf_response(uint8_t **out_response, size_t *out_len) {
    if (!rot_identity_is_enrolled()) {
        ESP_LOGE(TAG, "get_puf_response: device not enrolled");
        return false;
    }

    // Per esp32_puflib's README usage pattern: if PUF_STATE is already RESPONSE_READY, a
    // response was already reconstructed -- either by a get_puf_response() call earlier in
    // this same boot, or (the path that matters here) by puf_response_reset_calculate() on
    // the wake from a get_puf_response_reset() triggered by a PRIOR failed attempt. Calling
    // get_puf_response() again in that case would discard a perfectly usable response and
    // re-run a fresh RTC measurement for no reason -- just consume what is already there.
    if (PUF_STATE == RESPONSE_READY) {
        s_puf_reset_retry_count = 0;  // got a usable response; reset the budget for next time
        *out_response = PUF_RESPONSE;
        *out_len = PUF_RESPONSE_LEN;
        return true;
    }

    bool ok = get_puf_response();
    if (!ok) {
        s_puf_reset_retry_count += 1;
        if (s_puf_reset_retry_count > ROT_PUF_RESET_RETRY_LIMIT) {
            // Terminal failure: do NOT reset again. See puf_measurement.c's diagnostic printf
            // (hw%/errors%) in the log immediately above this line for why the last attempt
            // failed -- that is printed by get_puf_response() itself before returning here.
            ESP_LOGE(TAG, "get_puf_response: %d consecutive reconstruction failures, giving up "
                           "(retry limit %d) -- see hw%%/errors%% diagnostic above",
                           s_puf_reset_retry_count - 1, ROT_PUF_RESET_RETRY_LIMIT);
            s_puf_reset_retry_count = 0;
            return false;
        }
        // Per esp32_puflib's README: a failed reconstruction attempt is recovered by
        // get_puf_response_reset(), which resets the chip and retries on the next boot --
        // it is _Noreturn, so this call does not return and this function never reaches the
        // lines below on this path.
        ESP_LOGW(TAG, "get_puf_response: response outside reliability thresholds for this "
                       "attempt (%d/%d), resetting to retry",
                       s_puf_reset_retry_count, ROT_PUF_RESET_RETRY_LIMIT);
        get_puf_response_reset();
    }
    s_puf_reset_retry_count = 0;
    *out_response = PUF_RESPONSE;
    *out_len = PUF_RESPONSE_LEN;
    return true;
}

void rot_identity_release_puf_response(void) {
    clean_puf_response();
}

void rot_identity_derive_shs_a(const uint8_t *puf_response, size_t puf_len,
                                uint8_t shs_a[ROT_DIGEST_LEN]) {
    mbedtls_sha256(puf_response, puf_len, shs_a, 0 /* SHA-256, not SHA-224 */);
}

bool rot_identity_load_server_ref(uint8_t id_b[ROT_ID_LEN], uint8_t shs_b_ref[ROT_DIGEST_LEN]) {
    nvs_handle_t h;
    if (nvs_open(ROT_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t len = ROT_ID_LEN;
    esp_err_t e1 = nvs_get_blob(h, ROT_NVS_KEY_ID_B, id_b, &len);
    size_t len2 = ROT_DIGEST_LEN;
    esp_err_t e2 = (e1 == ESP_OK) ? nvs_get_blob(h, ROT_NVS_KEY_SHS_B, shs_b_ref, &len2) : ESP_FAIL;
    nvs_close(h);
    return e1 == ESP_OK && e2 == ESP_OK && len == ROT_ID_LEN && len2 == ROT_DIGEST_LEN;
}

bool rot_identity_save_server_ref(const uint8_t id_b[ROT_ID_LEN], const uint8_t shs_b_ref[ROT_DIGEST_LEN]) {
    nvs_handle_t h;
    if (nvs_open(ROT_NVS_NAMESPACE, NVS_READWRITE, &h) != ESP_OK) {
        return false;
    }
    esp_err_t e1 = nvs_set_blob(h, ROT_NVS_KEY_ID_B, id_b, ROT_ID_LEN);
    esp_err_t e2 = nvs_set_blob(h, ROT_NVS_KEY_SHS_B, shs_b_ref, ROT_DIGEST_LEN);
    esp_err_t e3 = nvs_commit(h);
    nvs_close(h);
    return e1 == ESP_OK && e2 == ESP_OK && e3 == ESP_OK;
}
