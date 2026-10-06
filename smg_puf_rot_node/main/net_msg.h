// Minimal length-prefixed JSON message framing over a TCP socket.
// New wire format for the PUF/challenge-response protocol -- intentionally NOT the same
// framing as the MicroPython SecureNode protocol's send_msg/recv_msg (that protocol is
// being retired for this scheme, see rot_session.h). A matching RPi4 server-side handler
// (smg_primary_server/) does not exist yet -- see task report.
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
