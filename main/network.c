#include "network.h"
#include "settings.h"
#include "sdkconfig.h"
#include <stdio.h>
#include <string.h>
#include "driver/gpio.h"
#include "esp_event.h"
#include "esp_log.h"
#include "esp_mac.h"
#include "esp_netif.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "freertos/FreeRTOS.h"
#include "freertos/event_groups.h"
#include "freertos/task.h"

static const char *TAG = "network";
static EventGroupHandle_t s_events;
static esp_netif_t *s_sta;
static reset_network_status_t s_status;
static portMUX_TYPE s_mux = portMUX_INITIALIZER_UNLOCKED;
static int64_t s_reconfigure_due;
#define CONNECTED BIT0

static void on_event(void *arg, esp_event_base_t base, int32_t id, void *data)
{
    (void)arg;
    if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
        const ip_event_got_ip_t *event = data;
        char ip[16];
        snprintf(ip, sizeof ip, IPSTR, IP2STR(&event->ip_info.ip));
        portENTER_CRITICAL(&s_mux);
        snprintf(s_status.ip, sizeof s_status.ip, "%s", ip);
        s_status.connected = true;
        portEXIT_CRITICAL(&s_mux);
        xEventGroupSetBits(s_events, CONNECTED);
        ESP_LOGI(TAG, "Web console: http://%s/", ip);
    } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
        portENTER_CRITICAL(&s_mux);
        s_status.connected = false;
        s_status.ip[0] = 0;
        portEXIT_CRITICAL(&s_mux);
        xEventGroupClearBits(s_events, CONNECTED);
        ESP_LOGW(TAG, "Station disconnected; retrying in background");
    }
}

static void apply_station_config(void)
{
    reset_settings_t settings = settings_get();
    wifi_config_t station = {0};
    memcpy(station.sta.ssid, settings.ssid, strlen(settings.ssid));
    memcpy(station.sta.password, settings.password, strlen(settings.password));
    /* An empty password explicitly configures an open network. */
    station.sta.threshold.authmode = settings.password[0] ? WIFI_AUTH_WPA2_PSK : WIFI_AUTH_OPEN;
    ESP_ERROR_CHECK(esp_wifi_set_config(WIFI_IF_STA, &station));
}

static void network_task(void *arg)
{
    (void)arg;
    int64_t last_attempt = -5000000LL, button_since = 0;
    bool button_handled = false;
    while (true) {
        int64_t now = esp_timer_get_time(), due;
        portENTER_CRITICAL(&s_mux);
        due = s_reconfigure_due;
        if (due && now >= due) s_reconfigure_due = 0;
        portEXIT_CRITICAL(&s_mux);
        if (due && now >= due) {
            esp_wifi_disconnect();
            apply_station_config();
            last_attempt = now - 5000000LL;
        }
        bool connected = (xEventGroupGetBits(s_events) & CONNECTED) != 0;
        reset_settings_t settings = settings_get();
        if (!connected && settings.ssid[0] && now - last_attempt >= 5000000LL) {
            esp_wifi_connect();
            last_attempt = now;
        }
        if (gpio_get_level(CONFIG_RESET_RECOVERY_GPIO) == 0) {
            if (!button_since) button_since = now;
            if (!button_handled && now - button_since >= 5000000LL) {
                if (settings_clear_wifi() == ESP_OK) {
                    ESP_LOGW(TAG, "BOOT held: Wi-Fi cleared; management password preserved");
                    network_schedule_reconfigure();
                }
                button_handled = true;
            }
        } else {
            button_since = 0;
            button_handled = false;
        }
        vTaskDelay(pdMS_TO_TICKS(100));
    }
}

esp_err_t network_start(void)
{
    s_events = xEventGroupCreate();
    if (!s_events) return ESP_ERR_NO_MEM;
    ESP_ERROR_CHECK(esp_netif_init());
    ESP_ERROR_CHECK(esp_event_loop_create_default());
    s_sta = esp_netif_create_default_wifi_sta();
    if (!s_sta) return ESP_ERR_NO_MEM;
    uint8_t mac[6];
    ESP_ERROR_CHECK(esp_read_mac(mac, ESP_MAC_WIFI_STA));
    snprintf(s_status.ap_ssid, sizeof s_status.ap_ssid, "ESP-Reset-%02X%02X%02X", mac[3], mac[4], mac[5]);
    snprintf(s_status.hostname, sizeof s_status.hostname, "esp-reset-%02x%02x%02x", mac[3], mac[4], mac[5]);
    ESP_ERROR_CHECK(esp_netif_set_hostname(s_sta, s_status.hostname));
    wifi_init_config_t init = WIFI_INIT_CONFIG_DEFAULT();
    ESP_ERROR_CHECK(esp_wifi_init(&init));
    ESP_ERROR_CHECK(esp_wifi_set_storage(WIFI_STORAGE_RAM));
    ESP_ERROR_CHECK(esp_event_handler_register(WIFI_EVENT, ESP_EVENT_ANY_ID, on_event, NULL));
    ESP_ERROR_CHECK(esp_event_handler_register(IP_EVENT, IP_EVENT_STA_GOT_IP, on_event, NULL));
    ESP_ERROR_CHECK(esp_wifi_set_mode(WIFI_MODE_STA));
    apply_station_config();
    const gpio_config_t button = {
        .pin_bit_mask = 1ULL << CONFIG_RESET_RECOVERY_GPIO,
        .mode = GPIO_MODE_INPUT, .pull_up_en = GPIO_PULLUP_ENABLE,
        .pull_down_en = GPIO_PULLDOWN_DISABLE, .intr_type = GPIO_INTR_DISABLE,
    };
    ESP_ERROR_CHECK(gpio_config(&button));
    ESP_ERROR_CHECK(esp_wifi_start());
    s_status.ap_enabled = false;
    ESP_LOGI(TAG, "USB provisioning ready; AP disabled");
    return xTaskCreate(network_task, "network", 4096, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}

void network_schedule_reconfigure(void)
{
    portENTER_CRITICAL(&s_mux);
    s_reconfigure_due = esp_timer_get_time() + 750000;
    portEXIT_CRITICAL(&s_mux);
}

reset_network_status_t network_status(void)
{
    portENTER_CRITICAL(&s_mux);
    reset_network_status_t status = s_status;
    portEXIT_CRITICAL(&s_mux);
    return status;
}
