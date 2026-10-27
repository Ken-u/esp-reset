#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "esp_err.h"
#include "sdkconfig.h"

#define BOARD_RESET_CHANNEL_COUNT 3
#define BOARD_RESET_PULSE_MS 200
#define BOARD_RESET_COOLDOWN_MS 3000
#define BOARD_RESET_REQUEST_MAX 64

typedef struct {
    uint32_t count;
    uint32_t cooldown_ms;
    bool enabled;
    bool active;
} board_reset_status_t;

esp_err_t board_reset_init(void);
/* Channels are numbered 1..3; invalid channels never select a default. */
int board_reset_gpio(int channel);
esp_err_t board_reset_press(int channel, const char *request_id);
board_reset_status_t board_reset_status(int channel);
/* Blocks new pulses during OTA/reboot; fails while a pulse is in progress. */
esp_err_t board_reset_begin_maintenance(void);
void board_reset_end_maintenance(void);

/* Combination: channel 1 RESET plus channel 2 RECOVERY or 3 MASKROM. */
esp_err_t board_reset_boot(int strap_channel, const char *request_id, unsigned hold_ms);
