// Minimal WiFi station connect (standard ESP-IDF esp_wifi boilerplate).
// TODO(paper OWASP I1 argument / provisioning gap, see rot_identity.h): SSID/password are
// compile-time placeholders (wifi_station.c) mirroring the MicroPython firmware's *old*
// insecure default -- that firmware later moved WiFi credentials into NVS specifically to
// avoid this. Not yet replicated here; flagged in the task report.
#pragma once

#include <stdbool.h>

// Blocks until connected (or a bounded number of retries fail). Returns true on success.
bool wifi_station_connect(void);
