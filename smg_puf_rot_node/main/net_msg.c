#include <string.h>
#include <stdlib.h>
#include <lwip/sockets.h>
#include "esp_log.h"

#include "net_msg.h"

static const char *TAG = "net_msg";

#define NET_MSG_MAX_LEN (16 * 1024)

static bool send_all(int sock, const uint8_t *buf, size_t len) {
    size_t sent = 0;
    while (sent < len) {
        int n = send(sock, buf + sent, len - sent, 0);
        if (n <= 0) {
            return false;
        }
        sent += (size_t)n;
    }
    return true;
}

static bool recv_all(int sock, uint8_t *buf, size_t len) {
    size_t got = 0;
    while (got < len) {
        int n = recv(sock, buf + got, len - got, 0);
        if (n <= 0) {
            return false;
        }
        got += (size_t)n;
    }
    return true;
}

bool net_msg_send(int sock, const cJSON *obj) {
    char *body = cJSON_PrintUnformatted(obj);
    if (body == NULL) {
        return false;
    }
    size_t body_len = strlen(body);
    uint8_t hdr[4] = {
        (uint8_t)((body_len >> 24) & 0xFF),
        (uint8_t)((body_len >> 16) & 0xFF),
        (uint8_t)((body_len >> 8) & 0xFF),
        (uint8_t)(body_len & 0xFF),
    };
    bool ok = send_all(sock, hdr, sizeof(hdr)) && send_all(sock, (const uint8_t *)body, body_len);
    free(body);
    return ok;
}

cJSON *net_msg_recv(int sock) {
    uint8_t hdr[4];
    if (!recv_all(sock, hdr, sizeof(hdr))) {
        return NULL;
    }
    uint32_t body_len = ((uint32_t)hdr[0] << 24) | ((uint32_t)hdr[1] << 16) |
                         ((uint32_t)hdr[2] << 8) | (uint32_t)hdr[3];
    if (body_len == 0 || body_len > NET_MSG_MAX_LEN) {
        ESP_LOGE(TAG, "recv: invalid length %u", (unsigned)body_len);
        return NULL;
    }
    char *body = malloc(body_len + 1);
    if (body == NULL) {
        return NULL;
    }
    if (!recv_all(sock, (uint8_t *)body, body_len)) {
        free(body);
        return NULL;
    }
    body[body_len] = '\0';
    cJSON *obj = cJSON_Parse(body);
    free(body);
    if (obj == NULL) {
        ESP_LOGE(TAG, "recv: JSON parse error");
    }
    return obj;
}
