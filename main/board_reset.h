#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "sdkconfig.h"

#define BOARD_RESET_GPIO CONFIG_RESET_OUTPUT_GPIO
#define BOARD_RESET_PULSE_MS 200
#define BOARD_RESET_COOLDOWN_MS 3000
#define BOARD_RESET_REQUEST_MAX 64

typedef struct {
    uint32_t count;
    uint32_t cooldown_ms;
    bool enabled;
} board_reset_status_t;

esp_err_t board_reset_init(void);
esp_err_t board_reset_press(const char *request_id);
board_reset_status_t board_reset_status(void);
/* Blocks new pulses during OTA/reboot; fails while a pulse is in progress. */
esp_err_t board_reset_begin_maintenance(void);
void board_reset_end_maintenance(void);
