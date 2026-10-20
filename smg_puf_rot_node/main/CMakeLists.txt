idf_component_register(
    SRCS "app_main.c" "rot_identity.c" "rot_session.c" "secure_channel.c" "net_msg.c" "wifi_station.c"
    INCLUDE_DIRS "."
    REQUIRES "esp32_puflib" "nvs_flash" "esp_wifi" "esp_netif" "esp_event" "lwip" "mbedtls" "json"
)
