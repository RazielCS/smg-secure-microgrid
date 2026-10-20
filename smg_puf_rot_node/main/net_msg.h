// Minimal length-prefixed JSON message framing over a TCP socket, used by the Root of
// Trust challenge-response protocol (see rot_session.h). Server-side counterpart:
// smg_primary_server/rot_challenge_service.py.
//
// Wire format: 4-byte big-endian payload length, followed by that many bytes of UTF-8 JSON.
#pragma once

#include <stdbool.h>
#include "cJSON.h"

// Sends `obj` (not freed by this function -- caller owns it). Returns true on success.
bool net_msg_send(int sock, const cJSON *obj);

// Blocks until a full message is received. Returns a cJSON object the caller must
// cJSON_Delete(), or NULL on error/disconnect.
cJSON *net_msg_recv(int sock);
