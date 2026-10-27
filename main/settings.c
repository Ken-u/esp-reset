#include "settings.h"
#include <string.h>
#include "esp_random.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "nvs.h"

typedef struct { char ssid[33]; char password[65]; } wifi_record_t;
static reset_settings_t s_settings;
static SemaphoreHandle_t s_lock;

esp_err_t settings_init(void)
{
    s_lock = xSemaphoreCreateMutex();
    if (!s_lock) return ESP_ERR_NO_MEM;
    nvs_handle_t nvs;
    esp_err_t err = nvs_open("esp_reset", NVS_READWRITE, &nvs);
    if (err != ESP_OK) return err;
    wifi_record_t wifi = {0};
    size_t size = sizeof wifi;
    err = nvs_get_blob(nvs, "wifi", &wifi, &size);
    if (err != ESP_OK && err != ESP_ERR_NVS_NOT_FOUND) goto done;
    if (err == ESP_OK && (size != sizeof wifi || wifi.ssid[32] || wifi.password[64])) {
        err = ESP_ERR_INVALID_SIZE;
        goto done;
    }
    memcpy(s_settings.ssid, wifi.ssid, sizeof wifi.ssid);
    memcpy(s_settings.password, wifi.password, sizeof wifi.password);
    /* Deliberately fixed by the owner; old random NVS tokens are ignored. */
    strcpy(s_settings.token, "esp32reset");
    err = ESP_OK;
done:
    nvs_close(nvs);
    return err;
}

reset_settings_t settings_get(void)
{
    xSemaphoreTake(s_lock, portMAX_DELAY);
    reset_settings_t result = s_settings;
    xSemaphoreGive(s_lock);
    return result;
}

static esp_err_t save_wifi(const char *ssid, const char *password)
{
    wifi_record_t record = {0};
    strcpy(record.ssid, ssid);
    strcpy(record.password, password);
    xSemaphoreTake(s_lock, portMAX_DELAY);
    nvs_handle_t nvs;
    esp_err_t err = nvs_open("esp_reset", NVS_READWRITE, &nvs);
    if (err == ESP_OK) {
        err = nvs_set_blob(nvs, "wifi", &record, sizeof record);
        if (err == ESP_OK) err = nvs_commit(nvs);
        nvs_close(nvs);
    }
    if (err == ESP_OK) {
        memcpy(s_settings.ssid, record.ssid, sizeof record.ssid);
        memcpy(s_settings.password, record.password, sizeof record.password);
    }
    xSemaphoreGive(s_lock);
    return err;
}

esp_err_t settings_set_wifi(const char *ssid, const char *password)
{
    if (!ssid || !password || !ssid[0] || strlen(ssid) > 32 || strlen(password) > 64) {
        return ESP_ERR_INVALID_ARG;
    }
    size_t n = strlen(password);
    if (n && n < 8) return ESP_ERR_INVALID_ARG;
    if (n == 64) {
        for (size_t i = 0; i < n; ++i) {
            char c = password[i];
            if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
                  (c >= 'A' && c <= 'F'))) return ESP_ERR_INVALID_ARG;
        }
    }
    return save_wifi(ssid, password);
}

esp_err_t settings_clear_wifi(void) { return save_wifi("", ""); }

bool settings_authorize(const char *token)
{
    if (!token || strlen(token) != strlen("esp32reset")) return false;
    reset_settings_t current = settings_get();
    unsigned difference = 0;
    for (unsigned i = 0; i < strlen("esp32reset"); ++i) difference |= (unsigned char)token[i] ^ (unsigned char)current.token[i];
    return difference == 0;
}
