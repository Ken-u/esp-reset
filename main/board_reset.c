/* Three independent reset switches; no display or host-daemon dependency. */
#include "board_reset.h"
#include <string.h>
#include "driver/gpio.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

static const int s_gpios[BOARD_RESET_CHANNEL_COUNT] = {
    CONFIG_RESET_OUTPUT_GPIO_1, CONFIG_RESET_OUTPUT_GPIO_2, CONFIG_RESET_OUTPUT_GPIO_3,
};
typedef struct {
    int64_t next_allowed_us;
    uint32_t count;
    bool active;
    bool fault;
    char recent[8][BOARD_RESET_REQUEST_MAX + 1];
    unsigned recent_next;
} reset_channel_t;
static reset_channel_t s_channels[BOARD_RESET_CHANNEL_COUNT];
static SemaphoreHandle_t s_lock;
static bool s_enabled = true;

int board_reset_gpio(int channel)
{
    return channel >= 1 && channel <= BOARD_RESET_CHANNEL_COUNT ? s_gpios[channel - 1] : -1;
}

static bool pin_allowed(int pin)
{
    if (!GPIO_IS_VALID_OUTPUT_GPIO(pin) || pin == CONFIG_RESET_RECOVERY_GPIO) return false;
#if CONFIG_IDF_TARGET_ESP32S3
    if ((pin >= 26 && pin <= 37) || pin == 19 || pin == 20 || pin == 43 || pin == 44 ||
        pin == 0 || pin == 3 || pin == 45 || pin == 46) return false;
#elif CONFIG_IDF_TARGET_ESP32C3
    /* GPIO20/21 are excluded on this C3 board, including custom pin choices. */
    if ((pin >= 11 && pin <= 21) || pin == 2 || pin == 8 || pin == 9) return false;
#endif
    return true;
}

esp_err_t board_reset_init(void)
{
    if (s_lock) return ESP_OK;
    uint64_t pins = 0;
    /* Validate every pin and duplicates before touching any output. */
    for (unsigned i = 0; i < BOARD_RESET_CHANNEL_COUNT; ++i) {
        int pin = s_gpios[i];
        if (!pin_allowed(pin) || (pins & (1ULL << pin))) return ESP_ERR_INVALID_ARG;
        pins |= 1ULL << pin;
    }
    for (unsigned i = 0; i < BOARD_RESET_CHANNEL_COUNT; ++i) {
        esp_err_t err = gpio_set_level(s_gpios[i], 0);
        if (err != ESP_OK) return err;
    }
    const gpio_config_t config = {
        .pin_bit_mask = pins, .mode = GPIO_MODE_OUTPUT,
        .pull_up_en = GPIO_PULLUP_DISABLE, .pull_down_en = GPIO_PULLDOWN_ENABLE,
        .intr_type = GPIO_INTR_DISABLE,
    };
    esp_err_t err = gpio_config(&config);
    if (err != ESP_OK) return err;
    s_lock = xSemaphoreCreateMutex();
    return s_lock ? ESP_OK : ESP_ERR_NO_MEM;
}

esp_err_t board_reset_press(int channel, const char *id)
{
    int pin = board_reset_gpio(channel);
    if (pin < 0 || !id || !id[0] || strlen(id) > BOARD_RESET_REQUEST_MAX) return ESP_ERR_INVALID_ARG;
    if (!s_lock || xSemaphoreTake(s_lock, 0) != pdTRUE) return ESP_ERR_INVALID_STATE;
    reset_channel_t *state = &s_channels[channel - 1];
    for (unsigned i = 0; i < 8; ++i) {
        if (strcmp(state->recent[i], id) == 0) {
            xSemaphoreGive(s_lock);
            return ESP_OK; /* Same successful channel + id never pulses twice. */
        }
    }
    if (!s_enabled || state->fault || state->active || esp_timer_get_time() < state->next_allowed_us) {
        xSemaphoreGive(s_lock);
        return ESP_ERR_INVALID_STATE;
    }
    state->active = true;
    xSemaphoreGive(s_lock);

    /* No lock is held across the local pulse: other channels stay independent,
       while maintenance sees active=true and cannot start. */
    esp_err_t err = gpio_set_level(pin, 1);
    if (err == ESP_OK) vTaskDelay(pdMS_TO_TICKS(BOARD_RESET_PULSE_MS) + 1);
    esp_err_t release_err = gpio_set_level(pin, 0);

    xSemaphoreTake(s_lock, portMAX_DELAY);
    state->next_allowed_us = esp_timer_get_time() + BOARD_RESET_COOLDOWN_MS * 1000LL;
    state->active = false;
    if (release_err != ESP_OK) {
        state->fault = true;
        err = release_err;
    }
    if (err == ESP_OK) {
        strcpy(state->recent[state->recent_next++ % 8], id);
        ++state->count;
    }
    xSemaphoreGive(s_lock);
    return err;
}

board_reset_status_t board_reset_status(int channel)
{
    board_reset_status_t out = {0};
    if (board_reset_gpio(channel) < 0 || !s_lock || xSemaphoreTake(s_lock, portMAX_DELAY) != pdTRUE) return out;
    const reset_channel_t *state = &s_channels[channel - 1];
    int64_t remaining = state->next_allowed_us - esp_timer_get_time();
    out.cooldown_ms = remaining > 0 ? (uint32_t)((remaining + 999) / 1000) : 0;
    out.count = state->count;
    out.active = state->active;
    out.enabled = s_enabled && !state->fault;
    xSemaphoreGive(s_lock);
    return out;
}

esp_err_t board_reset_begin_maintenance(void)
{
    if (!s_lock || xSemaphoreTake(s_lock, 0) != pdTRUE) return ESP_ERR_INVALID_STATE;
    bool busy = !s_enabled;
    for (unsigned i = 0; i < BOARD_RESET_CHANNEL_COUNT; ++i) busy |= s_channels[i].active;
    if (!busy) s_enabled = false;
    xSemaphoreGive(s_lock);
    return busy ? ESP_ERR_INVALID_STATE : ESP_OK;
}

void board_reset_end_maintenance(void)
{
    if (s_lock && xSemaphoreTake(s_lock, portMAX_DELAY) == pdTRUE) {
        s_enabled = true;
        xSemaphoreGive(s_lock);
    }
}

/* Reserved together so HTTP pulses / OTA cannot interleave with a boot sequence. */
esp_err_t board_reset_boot(int strap, const char *id, unsigned hold_ms)
{
    static char recent[8][BOARD_RESET_REQUEST_MAX + 1];
    static int recent_strap[8];
    static unsigned next;
    if ((strap != 2 && strap != 3) || !id || !id[0] ||
        strlen(id) > BOARD_RESET_REQUEST_MAX || hold_ms < 100 || hold_ms > 10000)
        return ESP_ERR_INVALID_ARG;
    if (!s_lock || xSemaphoreTake(s_lock, 0) != pdTRUE) return ESP_ERR_INVALID_STATE;
    for (unsigned i = 0; i < 8; ++i) {
        if (recent_strap[i] == strap && strcmp(recent[i], id) == 0) {
            xSemaphoreGive(s_lock);
            return ESP_OK;
        }
    }
    bool busy = !s_enabled;
    for (unsigned i = 0; i < BOARD_RESET_CHANNEL_COUNT; ++i)
        busy |= s_channels[i].active || s_channels[i].fault ||
                esp_timer_get_time() < s_channels[i].next_allowed_us;
    if (busy) { xSemaphoreGive(s_lock); return ESP_ERR_INVALID_STATE; }
    s_enabled = false;
    s_channels[0].active = s_channels[strap - 1].active = true;
    xSemaphoreGive(s_lock);
    esp_err_t err = gpio_set_level(s_gpios[strap - 1], 1);
    if (err == ESP_OK) {
        vTaskDelay(pdMS_TO_TICKS(100));
        err = gpio_set_level(s_gpios[0], 1);
        if (err == ESP_OK) vTaskDelay(pdMS_TO_TICKS(BOARD_RESET_PULSE_MS));
    }
    esp_err_t reset_release = gpio_set_level(s_gpios[0], 0);
    if (err == ESP_OK && reset_release == ESP_OK) vTaskDelay(pdMS_TO_TICKS(hold_ms));
    esp_err_t strap_release = gpio_set_level(s_gpios[strap - 1], 0);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    s_channels[0].active = s_channels[strap - 1].active = false;
    s_channels[0].fault |= reset_release != ESP_OK;
    s_channels[strap - 1].fault |= strap_release != ESP_OK;
    if (reset_release != ESP_OK) err = reset_release;
    if (strap_release != ESP_OK) err = strap_release;
    for (unsigned i = 0; i < BOARD_RESET_CHANNEL_COUNT; ++i)
        s_channels[i].next_allowed_us = esp_timer_get_time() + BOARD_RESET_COOLDOWN_MS * 1000LL;
    if (err == ESP_OK) {
        unsigned slot = next++ % 8;
        strcpy(recent[slot], id); recent_strap[slot] = strap;
        ++s_channels[0].count; ++s_channels[strap - 1].count;
    }
    s_enabled = true;
    xSemaphoreGive(s_lock);
    return err;
}
