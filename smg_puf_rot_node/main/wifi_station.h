// Minimal WiFi station connect (standard ESP-IDF esp_wifi boilerplate).
// TODO: SSID/password are compile-time constants in wifi_station.c, not provisioned via
// NVS. Moving them into NVS alongside the other enrollment data is a candidate ITER~2
// hardening item (see the paper's Limitations).
#pragma once

#include <stdbool.h>

// Blocks until connected (or a bounded number of retries fail). Returns true on success.
bool wifi_station_connect(void);
