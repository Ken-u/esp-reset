#pragma once
#include <stdbool.h>
#include <stdint.h>
#include "board_reset.h"
#include "esp_err.h"

typedef struct {
    char id[BOARD_RESET_REQUEST_MAX + 1];
    char mode[8];
    int channel;
    unsigned hold_ms;
    char state[12];
    esp_err_t result;
    int64_t started_us, finished_us;
    board_reset_status_t pin_traces[BOARD_RESET_CHANNEL_COUNT];
} reset_action_t;

esp_err_t actions_start(void);
const char *actions_boot_id(void);
/* A reused id must have identical parameters; accepted actions are never replayed. */
esp_err_t actions_submit(const char *boot_id, const char *id, const char *mode,
                         int channel, unsigned hold_ms, reset_action_t *out);
bool actions_get(const char *id, reset_action_t *out);
