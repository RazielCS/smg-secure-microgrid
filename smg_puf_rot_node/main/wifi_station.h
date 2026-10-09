// Minimal WiFi station connect (standard ESP-IDF esp_wifi boilerplate).
// SSID/password are read from NVS (namespace "wifi_creds", keys "ssid"/"pass"), written
// out-of-band by tools/provision_server_ref.py's generated NVS image -- the same
// provisioning pattern as the Root of Trust's id_b/shs_b_ref (rot_identity.c). No
// credential value is compiled into the firmware source or binary.
#pragma once

#include <stdbool.h>

// Blocks until connected (or a bounded number of retries fail). Returns true on success.
// Returns false immediately (without attempting to connect) if no WiFi credentials are
// provisioned in NVS yet.
bool wifi_station_connect(void);
