/* Native USB only: no UART0 / GPIO20/21 on C3. One bounded JSON command per line. */
#include "usb_console.h"
#include "board_reset.h"
#include "network.h"
#include "settings.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "driver/usb_serial_jtag.h"
#include "driver/usb_serial_jtag_vfs.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static void command(const char *line)
{
    cJSON *in = cJSON_Parse(line);
    if (!cJSON_IsObject(in)) { cJSON_Delete(in); return; }
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(in, "id");
    const cJSON *op = cJSON_GetObjectItemCaseSensitive(in, "op");
    if (!cJSON_IsString(id) || !id->valuestring[0] || strlen(id->valuestring) > 64 ||
        !cJSON_IsString(op)) { cJSON_Delete(in); return; }
    cJSON *out = cJSON_CreateObject();
    if (!out) { cJSON_Delete(in); return; }
    cJSON_AddStringToObject(out, "id", id->valuestring);
    esp_err_t err = ESP_ERR_INVALID_ARG;
    if (strcmp(op->valuestring, "status") == 0) {
        reset_network_status_t net = network_status();
        cJSON_AddBoolToObject(out, "connected", net.connected);
        cJSON_AddStringToObject(out, "ip", net.ip);
        cJSON_AddStringToObject(out, "password", settings_get().token);
        cJSON_AddStringToObject(out, "target", CONFIG_IDF_TARGET);
        err = ESP_OK;
    } else if (strcmp(op->valuestring, "wifi") == 0) {
        const cJSON *ssid = cJSON_GetObjectItemCaseSensitive(in, "ssid");
        const cJSON *pass = cJSON_GetObjectItemCaseSensitive(in, "password");
        if (cJSON_IsString(ssid) && cJSON_IsString(pass)) {
            err = settings_set_wifi(ssid->valuestring, pass->valuestring);
            if (err == ESP_OK) network_schedule_reconfigure();
        }
    } else {
        const cJSON *token = cJSON_GetObjectItemCaseSensitive(in, "token");
        if (cJSON_IsString(token) && settings_authorize(token->valuestring)) {
            if (strcmp(op->valuestring, "reset") == 0) {
                const cJSON *ch = cJSON_GetObjectItemCaseSensitive(in, "channel");
                if (cJSON_IsNumber(ch) && ch->valuedouble >= 1 && ch->valuedouble <= 3 &&
                    ch->valuedouble == ch->valueint)
                    err = board_reset_press(ch->valueint, id->valuestring);
            } else if (strcmp(op->valuestring, "loader") == 0 || strcmp(op->valuestring, "maskrom") == 0) {
                const cJSON *hold = cJSON_GetObjectItemCaseSensitive(in, "hold_ms");
                if (cJSON_IsNumber(hold) && hold->valuedouble >= 100 && hold->valuedouble <= 10000 &&
                    hold->valuedouble == hold->valueint)
                    err = board_reset_boot(strcmp(op->valuestring, "loader") == 0 ? 2 : 3,
                                           id->valuestring, hold->valueint);
            }
        }
    }
    cJSON_AddBoolToObject(out, "ok", err == ESP_OK);
    if (err != ESP_OK) cJSON_AddStringToObject(out, "error", esp_err_to_name(err));
    char *body = cJSON_PrintUnformatted(out);
    /* Prefix separates protocol replies from ordinary boot / Wi-Fi logs. */
    if (body) { printf("\n@ESP_RESET@%s\n", body); fflush(stdout); free(body); }
    cJSON_Delete(out);
    cJSON_Delete(in);
}

static void usb_task(void *arg)
{
    (void)arg;
    char line[769];
    size_t length = 0;
    bool overflow = false;
    while (true) {
        unsigned char byte;
        if (usb_serial_jtag_read_bytes(&byte, 1, pdMS_TO_TICKS(100)) != 1) continue;
        if (byte == '\n') {
            if (!overflow && length) { line[length] = 0; command(line); }
            length = 0; overflow = false;
        } else if (byte != '\r') {
            if (length < sizeof line - 1) line[length++] = byte;
            else overflow = true; /* Discard entire overlong command, never its suffix. */
        }
    }
}

esp_err_t usb_console_start(void)
{
    usb_serial_jtag_driver_config_t config = {
        .rx_buffer_size = 2048, .tx_buffer_size = 4096,
    };
    esp_err_t err = usb_serial_jtag_driver_install(&config);
    if (err != ESP_OK) return err;
    usb_serial_jtag_vfs_use_driver();
    return xTaskCreate(usb_task, "usb_commands", 6144, NULL, 4, NULL) == pdPASS ? ESP_OK : ESP_ERR_NO_MEM;
}
