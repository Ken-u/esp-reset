/* Extracted from Ken-u/esphook's hardware-reset feature. No display dependency. */
#include "board_reset.h"
#include <string.h>
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static SemaphoreHandle_t s_lock;
static int64_t s_next_allowed_us;
static bool s_enabled = true;
static uint32_t s_count;
static char s_recent[8][BOARD_RESET_REQUEST_MAX + 1];
static unsigned s_recent_next;

esp_err_t board_reset_init(void)
{
    if (s_lock) return ESP_OK;
    if (!GPIO_IS_VALID_OUTPUT_GPIO(BOARD_RESET_GPIO) ||
        BOARD_RESET_GPIO == CONFIG_RESET_RECOVERY_GPIO) return ESP_ERR_INVALID_ARG;
#if CONFIG_IDF_TARGET_ESP32S3
    /* N16R8 memory pins, USB, console UART and strapping pins are not allowed. */
    if ((BOARD_RESET_GPIO >= 26 && BOARD_RESET_GPIO <= 37) ||
        BOARD_RESET_GPIO == 19 || BOARD_RESET_GPIO == 20 ||
        BOARD_RESET_GPIO == 43 || BOARD_RESET_GPIO == 44 ||
        BOARD_RESET_GPIO == 0 || BOARD_RESET_GPIO == 3 ||
        BOARD_RESET_GPIO == 45 || BOARD_RESET_GPIO == 46) return ESP_ERR_INVALID_ARG;
#elif CONFIG_IDF_TARGET_ESP32C3
    if ((BOARD_RESET_GPIO >= 11 && BOARD_RESET_GPIO <= 19) ||
        BOARD_RESET_GPIO == 2 || BOARD_RESET_GPIO == 8 ||
        BOARD_RESET_GPIO == 9 || BOARD_RESET_GPIO == 21) return ESP_ERR_INVALID_ARG;
#endif
    esp_err_t err = gpio_set_level(BOARD_RESET_GPIO, 0);
    if (err != ESP_OK) return err;
    const gpio_config_t config = {
        .pin_bit_mask = 1ULL << BOARD_RESET_GPIO,
        .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE,
        .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    err = gpio_config(&config);
    if (err != ESP_OK) return err;
    s_lock = xSemaphoreCreateMutex();
    return s_lock ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t board_reset_press(const char *id)
{
    if (!id || !id[0] || strlen(id) > BOARD_RESET_REQUEST_MAX) return ESP_ERR_INVALID_ARG;
    if (!s_lock || xSemaphoreTake(s_lock, 0) != pdTRUE) return ESP_ERR_INVALID_STATE;
    /* A repeated successful request is acknowledged without another pulse.
       Cache is intentionally limited to the last eight successes in this boot. */
    for (unsigned i = 0; i < 8; ++i) {
        if (strcmp(s_recent[i], id) == 0) {
            xSemaphoreGive(s_lock);
            return ESP_OK;
        }
    }
    if (!s_enabled || esp_timer_get_time() < s_next_allowed_us) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_STATE;
    }
    esp_err_t err = gpio_set_level(BOARD_RESET_GPIO, 1);
    if (err == ESP_OK) vTaskDelay(pdMS_TO_TICKS(BOARD_RESET_PULSE_MS) + 1);
    esp_err_t release_err = gpio_set_level(BOARD_RESET_GPIO, 0);
    s_next_allowed_us = esp_timer_get_time() + BOARD_RESET_COOLDOWN_MS * 1000LL;
    if (release_err != ESP_OK) err = release_err;
    if (err == ESP_OK) {
        strcpy(s_recent[s_recent_next++ % 8], id);
        ++s_count;
    }
    xSemaphoreGive(s_lock);
    return err;
}

board_reset_status_t board_reset_status(void)
{
    board_reset_status_t out = {0};
    if (!s_lock || xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) return out;
    int64_t remaining = s_next_allowed_us - esp_timer_get_time();
    out.cooldown_ms = remaining > 0 ? (uint32_t)((remaining + 999) / 1000) : 0;
    out.count = s_count;
    out.enabled = s_enabled;
    xSemaphoreGive(s_lock);
    return out;
}

esp_err_t board_reset_begin_maintenance(void)
{
    if (!s_lock || xSemaphoreTake(s_lock, 0) != pdTRUE) return ESP_ERR_INVALID_STATE;
    esp_err_t err = s_enabled ? ESP_OK : ESP_ERR_INVALID_STATE;
    if (err == ESP_OK) s_enabled = false;
    xSemaphoreGive(s_lock);
    return err;
}

void board_reset_end_maintenance(void)
{
    if (s_lock && xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
        s_enabled = true;
        xSemaphoreGive(s_lock);
    }
}
