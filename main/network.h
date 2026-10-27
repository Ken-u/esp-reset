#pragma once
#include <stdbool.h>
#include "esp_err.h"

typedef struct {
    bool connected;
    bool ap_enabled;
    char ip[16];
    char ap_ssid[32];
    char hostname[32];
} reset_network_status_t;

esp_err_t network_start(void);
void network_schedule_reconfigure(void);
reset_network_status_t network_status(void);
