#include "actions.h"
#include <stdio.h>
#include <string.h>
#include "esp_log.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "freertos/task.h"

#define ACTION_SLOTS 16
static reset_action_t s_actions[ACTION_SLOTS];
static SemaphoreHandle_t s_lock;
static char s_boot_id[25];
static unsigned s_next;

const char *actions_boot_id(void) { return s_boot_id; }

bool actions_get(const char *id, reset_action_t *out)
{
    if (!s_lock || !id || !out) return false;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    bool found = false;
    for (int i = 0; i < ACTION_SLOTS; ++i) {
        if (strcmp(s_actions[i].id, id) == 0 && id[0]) {
            *out = s_actions[i]; found = true; break;
        }
    }
    xSemaphoreGive(s_lock);
    return found;
}

esp_err_t actions_submit(const char *boot, const char *id, const char *mode,
                         int channel, unsigned hold_ms, reset_action_t *out)
{
    if (!s_lock || !boot || strcmp(boot, s_boot_id) != 0) return ESP_ERR_INVALID_STATE;
    if (!id || !id[0] || strlen(id) > BOARD_RESET_REQUEST_MAX || !mode || !out)
        return ESP_ERR_INVALID_ARG;
    bool reset = strcmp(mode, "reset") == 0;
    bool boot_mode = strcmp(mode, "loader") == 0 || strcmp(mode, "maskrom") == 0;
    if ((!reset && !boot_mode) || (reset && (channel < 1 || channel > 3)) ||
        (boot_mode && (hold_ms < 100 || hold_ms > 10000))) return ESP_ERR_INVALID_ARG;
    if (!reset) channel = strcmp(mode, "loader") == 0 ? 2 : 3;
    if (reset) hold_ms = 0;
    xSemaphoreTake(s_lock, portMAX_DELAY);
    for (int i = 0; i < ACTION_SLOTS; ++i) {
        reset_action_t *a = &s_actions[i];
        if (strcmp(a->id, id) == 0) {
            bool same = strcmp(a->mode, mode) == 0 && a->channel == channel && a->hold_ms == hold_ms;
            *out = *a;
            xSemaphoreGive(s_lock);
            return same ? ESP_OK : ESP_ERR_INVALID_ARG;
        }
    }
    /* Only one outstanding action: no unexpected delayed reset from a queue. */
    for (int i = 0; i < ACTION_SLOTS; ++i) {
        if (strcmp(s_actions[i].state, "queued") == 0 || strcmp(s_actions[i].state, "running") == 0) {
            xSemaphoreGive(s_lock);
            return ESP_ERR_INVALID_STATE;
        }
    }
    reset_action_t *a = &s_actions[s_next++ % ACTION_SLOTS];
    memset(a, 0, sizeof *a);
    strcpy(a->id, id); strcpy(a->mode, mode); strcpy(a->state, "queued");
    a->channel = channel; a->hold_ms = hold_ms;
    *out = *a;
    xSemaphoreGive(s_lock);
    return ESP_OK;
}

static void action_task(void *arg)
{
    (void)arg;
    while (true) {
        int slot = -1;
        reset_action_t job = {0};
        xSemaphoreTake(s_lock, portMAX_DELAY);
        for (int i = 0; i < ACTION_SLOTS; ++i) {
            if (strcmp(s_actions[i].state, "queued") == 0) {
                slot = i;
                strcpy(s_actions[i].state, "running");
                s_actions[i].started_us = esp_timer_get_time();
                job = s_actions[i]; break;
            }
        }
        xSemaphoreGive(s_lock);
        if (slot >= 0) {
            /* Log before and after the entire operation; never delay a held reset for logging. */
            ESP_LOGI("action", "id=%s mode=%s channel=%d gpio=%d start=%lld", job.id,
                     job.mode, job.channel, board_reset_gpio(job.channel), (long long)job.started_us);
            esp_err_t err = strcmp(job.mode, "reset") == 0 ?
                board_reset_press(job.channel, job.id) : board_reset_boot(job.channel, job.id, job.hold_ms);
            int64_t finished = esp_timer_get_time();
            for (int ch = 1; ch <= BOARD_RESET_CHANNEL_COUNT; ++ch)
                job.pin_traces[ch - 1] = board_reset_status(ch);
            xSemaphoreTake(s_lock, portMAX_DELAY);
            memcpy(s_actions[slot].pin_traces, job.pin_traces, sizeof job.pin_traces);
            s_actions[slot].result = err;
            s_actions[slot].finished_us = finished;
            strcpy(s_actions[slot].state, err == ESP_OK ? "released" : "failed");
            xSemaphoreGive(s_lock);
            ESP_LOGI("action", "id=%s state=%s result=%s finish=%lld", job.id,
                     err == ESP_OK ? "released" : "failed", esp_err_to_name(err), (long long)finished);
        }
        vTaskDelay(pdMS_TO_TICKS(20));
    }
}

esp_err_t actions_start(void)
{
    unsigned char bytes[12];
    esp_fill_random(bytes, sizeof bytes);
    for (unsigned i = 0; i < sizeof bytes; ++i) snprintf(s_boot_id + i * 2, 3, "%02x", bytes[i]);
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;
    return xTaskCreate(action_task, "reset_actions", 4096, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
