// SMG PUF-based Root of Trust node -- identity/RoT/communication layer only (see the
// paper's "Root of Trust Establishment" section). Sensor reading and fuzzy EMS control
// are not implemented in this firmware; see the paper's Limitations.

#include <string.h>
#include <sys/socket.h>
#include <netdb.h>
#include "esp_log.h"
#include "esp_sleep.h"
#include "nvs_flash.h"

#include "puflib.h"
#include "rot_identity.h"
#include "rot_session.h"
#include "secure_channel.h"
#include "wifi_station.h"

static const char *TAG = "app_main";

static void bin_to_hex(const uint8_t *bin, size_t len, char *out /* len*2+1 */) {
    static const char hexd[] = "0123456789abcdef";
    for (size_t i = 0; i < len; ++i) {
        out[2 * i]     = hexd[bin[i] >> 4];
        out[2 * i + 1] = hexd[bin[i] & 0x0F];
    }
    out[2 * len] = '\0';
}

// Placeholder server address -- see wifi_station.h TODO (WiFi credentials) for the same
// caveat. RPi4 primary agent as WiFi AP gateway, matching the SMG bench setup.
#define SERVER_IP   "10.42.0.1"
#define SERVER_PORT 5001

// Required by esp32_puflib (README "Minimal working example"): hooks the library's own
// deep-sleep wake stub, which its multi-iteration enrollment measurement relies on.
void RTC_IRAM_ATTR esp_wake_deep_sleep(void) {
    esp_default_wake_deep_sleep();
    puflib_wake_up_stub();
}

static int connect_to_server(void) {
    struct sockaddr_in dest = { 0 };
    dest.sin_family = AF_INET;
    dest.sin_port = htons(SERVER_PORT);
    if (inet_pton(AF_INET, SERVER_IP, &dest.sin_addr) != 1) {
        ESP_LOGE(TAG, "invalid SERVER_IP");
        return -1;
    }
    int sock = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (sock < 0) {
        ESP_LOGE(TAG, "socket() failed: errno %d", errno);
        return -1;
    }
    if (connect(sock, (struct sockaddr *)&dest, sizeof(dest)) != 0) {
        ESP_LOGE(TAG, "connect() to %s:%d failed: errno %d", SERVER_IP, SERVER_PORT, errno);
        close(sock);
        return -1;
    }
    ESP_LOGI(TAG, "connected to server %s:%d", SERVER_IP, SERVER_PORT);
    return sock;
}

void app_main(void) {
    esp_err_t nvs_err = nvs_flash_init();
    if (nvs_err == ESP_ERR_NVS_NO_FREE_PAGES || nvs_err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        ESP_ERROR_CHECK(nvs_flash_erase());
        nvs_err = nvs_flash_init();
    }
    ESP_ERROR_CHECK(nvs_err);

    // --- Phase 1: enrollment (idempotent across the internal deep-sleep resets this may
    // trigger -- see rot_identity_run_enrollment_step() and esp32_puflib's own state
    // machine in ecc.c/puf_measurement.c). While enrollment is still in progress, this
    // function call chain ends in esp_deep_sleep_start() and app_main() is re-entered
    // from scratch on the next boot; the code below this point only runs once enrollment
    // has actually completed.
    bool enrolled = rot_identity_run_enrollment_step();
    if (!enrolled) {
        ESP_LOGW(TAG, "enrollment not yet complete (device will reset and resume automatically)");
        return;
    }
    ESP_LOGI(TAG, "PUF identity ready (enrollment data present in NVS)");

    // --- Provisioning gap (see rot_identity.h TODOs): this pass does not implement a
    // host-side tool to pin the server reference (ID_B/ShS_B). Without it,
    // rot_identity_load_server_ref() below will fail and the handshake cannot proceed --
    // this is expected until that tool (or a manual NVS write for bring-up) exists.
    uint8_t id_b_probe[ROT_ID_LEN], shs_b_probe[ROT_DIGEST_LEN];
    if (!rot_identity_load_server_ref(id_b_probe, shs_b_probe)) {
        // --- One-time provisioning export (trusted physical channel) ---
        // No server reference is pinned yet, so this boot cannot proceed to the network
        // handshake anyway. This is exactly the point at which the device is known-good
        // (enrollment just completed) and still physically attached over USB serial to a
        // controlled workstation. ID_A and the device's PUBLIC key pk_A are printed here,
        // once, for an operator to copy into the server registry via
        // smg_primary_server/tools/register_puf_node.py. pk_A being public, printing and
        // transmitting it discloses nothing an attacker could use to forge a signature --
        // unlike the private key sk_A, which is derived fresh each boot, never leaves
        // rot_session_handshake()'s stack, and is never printed, stored, or transmitted.
        // After this branch returns, the PUF response is released from RAM like any other
        // boot -- there is no serial export path in normal operation (i.e. once a server
        // reference IS pinned, this branch never runs).
        uint8_t id_a[ROT_ID_LEN];
        rot_identity_get_id_a(id_a);

        uint8_t *puf_response = NULL;
        size_t puf_len = 0;
        if (!rot_identity_get_puf_response(&puf_response, &puf_len)) {
            ESP_LOGE(TAG, "no pinned server reference in NVS, and PUF response reconstruction "
                           "failed -- cannot even export for provisioning (see rot_identity.h "
                           "TODOs)");
            return;
        }
        mbedtls_mpi sk_a;
        uint8_t pk_a[ROT_PUBKEY_LEN];
        bool keypair_ok = rot_identity_derive_keypair(puf_response, puf_len, &sk_a, pk_a);
        rot_identity_release_puf_response();
        if (!keypair_ok) {
            ESP_LOGE(TAG, "failed to derive device identity keypair for provisioning export");
            return;
        }
        mbedtls_mpi_free(&sk_a);  // sk_a is never exported; only pk_a (public) is printed below

        char id_a_hex[ROT_ID_LEN * 2 + 1], pk_a_hex[ROT_PUBKEY_LEN * 2 + 1];
        bin_to_hex(id_a, ROT_ID_LEN, id_a_hex);
        bin_to_hex(pk_a, ROT_PUBKEY_LEN, pk_a_hex);

        ESP_LOGW(TAG, "no pinned server reference in NVS -- provisioning step missing, "
                       "cannot proceed to handshake (see rot_identity.h TODOs)");
        printf("\n=== PROVISIONING EXPORT (one-time, trusted physical channel) ===\n");
        printf("id_a=%s\n", id_a_hex);
        printf("pk_a=%s\n", pk_a_hex);
        printf("Run on the RPi4 / server host:\n");
        printf("  python register_puf_node.py --id-a %s --pk-a %s --label SMG_NODE_01\n",
               id_a_hex, pk_a_hex);
        printf("=== END PROVISIONING EXPORT ===\n\n");
        return;
    }

    if (!wifi_station_connect()) {
        ESP_LOGE(TAG, "WiFi connection failed, aborting");
        return;
    }

    int sock = connect_to_server();
    if (sock < 0) {
        return;
    }

    uint8_t *puf_response = NULL;
    size_t puf_len = 0;
    if (!rot_identity_get_puf_response(&puf_response, &puf_len)) {
        ESP_LOGE(TAG, "failed to reconstruct PUF response");
        close(sock);
        return;
    }
    ESP_LOGI(TAG, "PUF response reconstructed (%u bytes)", (unsigned)puf_len);

    rot_session_t session;
    bool ok = rot_session_handshake(sock, puf_response, puf_len, &session);
    rot_identity_release_puf_response();  // keypair already derived; raw PUF_A no longer needed

    if (!ok) {
        ESP_LOGE(TAG, "Root of Trust handshake FAILED");
        close(sock);
        return;
    }
    ESP_LOGI(TAG, "mutual authentication OK, session key derived");

    // Demo data message (AES-256-GCM under Key_ab) -- verifies the encrypted channel end
    // to end; not the sensor/EMS data path (out of scope, see file header).
    const char *demo_payload = "{\"demo\":\"puf_rot_node\",\"status\":\"handshake_ok\"}";
    if (secure_channel_send_demo_message(sock, session.key_ab, demo_payload)) {
        ESP_LOGI(TAG, "demo encrypted message sent");
    } else {
        ESP_LOGE(TAG, "failed to send demo encrypted message");
    }

    close(sock);
}
