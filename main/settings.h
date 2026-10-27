#pragma once
#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    char ssid[33];
    char password[65];
    char token[25];
} reset_settings_t;

esp_err_t settings_init(void);
reset_settings_t settings_get(void);
esp_err_t settings_set_wifi(const char *ssid, const char *password);
esp_err_t settings_clear_wifi(void);
bool settings_authorize(const char *token);
