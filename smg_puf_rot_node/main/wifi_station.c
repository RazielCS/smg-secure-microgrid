#include <string.h>
#include "esp_wifi.h"
#include "esp_event.h"
#include "esp_log.h"
#include "nvs_flash.h"
#include "nvs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"

#include "wifi_station.h"

static const char *TAG = "wifi_station";

#define WIFI_CREDS_NVS_NAMESPACE "wifi_creds"
#define WIFI_CREDS_NVS_KEY_SSID  "ssid"
#define WIFI_CREDS_NVS_KEY_PASS  "pass"
#define WIFI_MAX_RETRY 10

// Loads the WiFi SSID/password provisioned into NVS by tools/provision_server_ref.py
// (same out-of-band pattern as id_b/shs_b_ref, rot_identity.c). Returns false if no
// credentials have been provisioned yet.
static bool wifi_load_credentials(char *ssid_out, size_t ssid_cap,
                                   char *pass_out, size_t pass_cap) {
    nvs_handle_t h;
    if (nvs_open(WIFI_CREDS_NVS_NAMESPACE, NVS_READONLY, &h) != ESP_OK) {
        return false;
    }
    size_t ssid_len = ssid_cap, pass_len = pass_cap;
    esp_err_t e1 = nvs_get_str(h, WIFI_CREDS_NVS_KEY_SSID, ssid_out, &ssid_len);
    esp_err_t e2 = (e1 == ESP_OK) ? nvs_get_str(h, WIFI_CREDS_NVS_KEY_PASS, pass_out, &pass_len) : ESP_FAIL;
    nvs_close(h);
    return e1 == ESP_OK && e2 == ESP_OK;
}

static EventGroupHandle_t s_wifi_event_group;
#define WIFI_CONNECTED_BIT BIT0
#define WIFI_FAIL_BIT      BIT1
static int s_retry_count = 0;

static void event_handler(void *arg, esp_event_base_t event_base,
                           int32_t event_id, void *event_data) {
    if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_START) {
        esp_wifi_connect();
    } else if (event_base == WIFI_EVENT && event_id == WIFI_EVENT_STA_DISCONNECTED) {
        if (s_retry_count < WIFI_MAX_RETRY) {
            esp_wifi_connect();
            s_retry_count++;
            ESP_LOGW(TAG, "retrying WiFi connection (%d/%d)", s_retry_count, WIFI_MAX_RETRY);
        } else {
            xEventGroupSetBits(s_wifi_event_group, WIFI_FAIL_BIT);
        }
    } else if (event_base == IP_EVENT && event_id == IP_EVENT_STA_GOT_IP) {
        s_retry_count = 0;
        xEventGroupSetBits(s_wifi_event_group, WIFI_CONNECTED_BIT);
    }
}

bool wifi_station_connect(void) {
    char ssid[33] = {0};
    char pass[65] = {0};
    if (!wifi_load_credentials(ssid, sizeof(ssid), pass, sizeof(pass))) {
        ESP_LOGE(TAG, "no WiFi credentials provisioned in NVS (namespace \"%s\") -- run "
                       "tools/provision_server_ref.py with --wifi-ssid/--wifi-pass and flash "
                       "the resulting NVS image before connecting", WIFI_CREDS_NVS_NAMESPACE);
        return false;
    }

    s_wifi_event_group = xEventGroupCreate();

    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    esp_netif_create_default_wifi_sta();

    wifi_init_config_t cfg = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&cfg));

    esp_event_handler_instance_t instance_any_id, instance_got_ip;
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        WIFI_EVENT, ESP_EVENT_ANY_ID, &event_handler, NULL, &instance_any_id));
    ESP_ERROR_CHECK(esp_event_handler_instance_register(
        IP_EVENT, IP_EVENT_STA_GOT_IP, &event_handler, NULL, &instance_got_ip));

    wifi_config_t wifi_config = { 0 };
    strncpy((char *)wifi_config.sta.ssid, ssid, sizeof(wifi_config.sta.ssid) - 1);
    strncpy((char *)wifi_config.sta.password, pass, sizeof(wifi_config.sta.password) - 1);
    wifi_config.sta.threshold.authmode = WIFI_AUTH_WPA2_PSK;

    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &wifi_config));
    ESP_ERROR_CHECK(esp_wifi_start());

    ESP_LOGI(TAG, "connecting to SSID:%s ...", ssid);
    EventBits_t bits = xEventGroupWaitBits(s_wifi_event_group,
                                            WIFI_CONNECTED_BIT | WIFI_FAIL_BIT,
                                            pdFALSE, pdFALSE, portMAX_DELAY);

    bool ok = (bits & WIFI_CONNECTED_BIT) != 0;
    if (ok) {
        ESP_LOGI(TAG, "connected");
    } else {
        ESP_LOGE(TAG, "connection FAILED after %d retries", WIFI_MAX_RETRY);
    }
    return ok;
}
