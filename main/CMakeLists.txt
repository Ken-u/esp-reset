idf_component_register(
    SRCS "main.c" "usb_console.c" "board_reset.c" "settings.c" "network.c" "web_server.c"
    INCLUDE_DIRS "."
    REQUIRES esp_driver_usb_serial_jtag esp_driver_gpio esp_timer esp_wifi esp_event esp_netif nvs_flash
             esp_http_server json app_update esp_app_format esp_hw_support
    EMBED_TXTFILES "web/index.html"
)
