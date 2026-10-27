#include <stdio.h>
#include "board_reset.h"
#include "network.h"
#include "settings.h"
#include "web_server.h"
#include "esp_log.h"
#include "esp_ota_ops.h"
#include "nvs_flash.h"

void app_main(void)
{
    /* Keep target reset released before starting any network or storage work. */
    ESP_ERROR_CHECK(board_reset_init());
    esp_err_t err = nvs_flash_init();
    if (err == ESP_ERR_NVS_NO_FREE_PAGES || err == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        /* Do not silently erase Wi-Fi or credentials after a storage error. */
        ESP_LOGE("main", "NVS needs recovery. Back up credentials before erase-flash.");
    }
    ESP_ERROR_CHECK(err);
    ESP_ERROR_CHECK(settings_init());
    reset_settings_t settings = settings_get();
    printf("\nESP RESET / %s\nReset channels 1/2/3: GPIO%d / GPIO%d / GPIO%d (active high, external switches)\n"
           "Management + recovery AP password: %s\n"
           "Hold BOOT for 5 seconds while running to clear Wi-Fi.\n\n",
           CONFIG_IDF_TARGET, board_reset_gpio(1), board_reset_gpio(2), board_reset_gpio(3), settings.token);
    ESP_ERROR_CHECK(network_start());
    ESP_ERROR_CHECK(web_server_start());
    err = esp_ota_mark_app_valid_cancel_rollback();
    if (err != ESP_OK && err != ESP_ERR_OTA_ROLLBACK_INVALID_STATE) {
        ESP_LOGW("main", "OTA validation: %s", esp_err_to_name(err));
    }
}
