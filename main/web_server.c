#include "web_server.h"
#include "board_reset.h"
#include "actions.h"
#include "network.h"
#include "settings.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "cJSON.h"
#include "esp_app_desc.h"
#include "esp_http_server.h"
#include "esp_ota_ops.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

extern const unsigned char index_start[] asm("_binary_index_html_start");
extern const unsigned char index_end[] asm("_binary_index_html_end");

static esp_err_t json_reply(httpd_req_t *req, const char *status, cJSON *value)
{
    char *body = cJSON_PrintUnformatted(value);
    cJSON_Delete(value);
    if (!body) return ESP_ERR_NO_MEM;
    httpd_resp_set_status(req, status);
    httpd_resp_set_type(req, "application/json; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    esp_err_t err = httpd_resp_sendstr(req, body);
    free(body);
    return err;
}

static esp_err_t error_reply(httpd_req_t *req, const char *status, const char *message)
{
    cJSON *value = cJSON_CreateObject();
    if (!value) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(value, "ok", false);
    cJSON_AddStringToObject(value, "error", message);
    /* An unread request body must not be interpreted as a second request. */
    httpd_resp_set_hdr(req, "Connection", "close");
    json_reply(req, status, value);
    return ESP_FAIL;
}

static bool authorize(httpd_req_t *req)
{
    char token[25];
    size_t length = httpd_req_get_hdr_value_len(req, "X-Reset-Token");
    if (length == strlen("esp32reset") && httpd_req_get_hdr_value_str(req, "X-Reset-Token", token, sizeof token) == ESP_OK &&
        settings_authorize(token)) return true;
    error_reply(req, "401 Unauthorized", "Management password required (X-Reset-Token)");
    return false;
}

static cJSON *read_json(httpd_req_t *req)
{
    char content_type[80];
    if (httpd_req_get_hdr_value_str(req, "Content-Type", content_type, sizeof content_type) != ESP_OK ||
        (strcmp(content_type, "application/json") != 0 &&
         strncmp(content_type, "application/json;", 17) != 0)) {
        error_reply(req, "415 Unsupported Media Type", "Use application/json");
        return NULL;
    }
    if (!req->content_len || req->content_len > 768) {
        error_reply(req, "413 Content Too Large", "JSON body must be 1..768 bytes");
        return NULL;
    }
    char body[769];
    size_t offset = 0;
    while (offset < req->content_len) {
        int got = httpd_req_recv(req, body + offset, req->content_len - offset);
        if (got <= 0) {
            error_reply(req, "408 Request Timeout", "Incomplete JSON body");
            return NULL;
        }
        offset += got;
    }
    body[offset] = 0;
    cJSON *value = cJSON_ParseWithLengthOpts(body, offset + 1, NULL, true);
    if (!cJSON_IsObject(value)) {
        cJSON_Delete(value);
        error_reply(req, "400 Bad Request", "Expected a JSON object");
        return NULL;
    }
    return value;
}

static esp_err_t root_get(httpd_req_t *req)
{
    httpd_resp_set_type(req, "text/html; charset=utf-8");
    httpd_resp_set_hdr(req, "Cache-Control", "no-store");
    httpd_resp_set_hdr(req, "X-Content-Type-Options", "nosniff");
    httpd_resp_set_hdr(req, "X-Frame-Options", "DENY");
    return httpd_resp_send(req, (const char *)index_start, index_end - index_start - 1);
}

static esp_err_t status_get(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    reset_settings_t settings = settings_get();
    reset_network_status_t net = network_status();
    cJSON *out = cJSON_CreateObject();
    if (!out) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "boot_id", actions_boot_id());
    cJSON_AddStringToObject(out, "target", CONFIG_IDF_TARGET);
    cJSON_AddStringToObject(out, "version", esp_app_get_description()->version);
    cJSON_AddNumberToObject(out, "pulse_ms", BOARD_RESET_PULSE_MS);
    cJSON_AddNumberToObject(out, "channel_count", BOARD_RESET_CHANNEL_COUNT);
    cJSON *channels = cJSON_AddArrayToObject(out, "channels");
    if (!channels) { cJSON_Delete(out); return ESP_ERR_NO_MEM; }
    uint32_t total = 0;
    for (int channel = 1; channel <= BOARD_RESET_CHANNEL_COUNT; ++channel) {
        board_reset_status_t reset = board_reset_status(channel);
        cJSON *item = cJSON_CreateObject();
        if (!item) { cJSON_Delete(out); return ESP_ERR_NO_MEM; }
        cJSON_AddNumberToObject(item, "channel", channel);
        cJSON_AddNumberToObject(item, "gpio", board_reset_gpio(channel));
        cJSON_AddNumberToObject(item, "cooldown_ms", reset.cooldown_ms);
        cJSON_AddNumberToObject(item, "reset_count", reset.count);
        cJSON_AddNumberToObject(item, "pulse_count", reset.pulse_count);
        cJSON_AddStringToObject(item, "last_request_id", reset.last_request_id);
        cJSON_AddNumberToObject(item, "pressed_us", reset.pressed_us);
        cJSON_AddNumberToObject(item, "released_us", reset.released_us);
        cJSON_AddBoolToObject(item, "enabled", reset.enabled);
        cJSON_AddBoolToObject(item, "active", reset.active);
        cJSON_AddItemToArray(channels, item);
        total += reset.count;
    }
    cJSON_AddNumberToObject(out, "reset_count", total);
    cJSON_AddBoolToObject(out, "connected", net.connected);
    cJSON_AddBoolToObject(out, "ap_enabled", net.ap_enabled);
    cJSON_AddStringToObject(out, "ssid", settings.ssid);
    cJSON_AddStringToObject(out, "ip", net.ip);
    cJSON_AddStringToObject(out, "ap_ssid", net.ap_ssid);
    cJSON_AddStringToObject(out, "hostname", net.hostname);
    cJSON_AddNumberToObject(out, "uptime_s", esp_timer_get_time() / 1000000LL);
    return json_reply(req, "200 OK", out);
}

static esp_err_t reset_post(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    cJSON *body = read_json(req);
    if (!body) return ESP_FAIL;
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(body, "request_id");
    const cJSON *channel_json = cJSON_GetObjectItemCaseSensitive(body, "channel");
    if (!cJSON_IsNumber(channel_json) || channel_json->valuedouble < 1 ||
        channel_json->valuedouble > BOARD_RESET_CHANNEL_COUNT ||
        channel_json->valuedouble != channel_json->valueint) {
        cJSON_Delete(body);
        return error_reply(req, "400 Bad Request", "channel must be an integer from 1 to 3");
    }
    int channel = channel_json->valueint;
    if (!cJSON_IsString(id) || !id->valuestring[0] || strlen(id->valuestring) > BOARD_RESET_REQUEST_MAX) {
        cJSON_Delete(body);
        return error_reply(req, "400 Bad Request", "request_id must be 1..64 bytes");
    }
    esp_err_t err = board_reset_press(channel, id->valuestring);
    if (err != ESP_OK) {
        cJSON_Delete(body);
        return error_reply(req, err == ESP_ERR_INVALID_STATE ? "409 Conflict" : "500 Internal Server Error",
                           err == ESP_ERR_INVALID_STATE ? "Reset is busy, cooling down, or disabled during maintenance" : "GPIO operation failed");
    }
    cJSON *out = cJSON_CreateObject();
    if (!out) { cJSON_Delete(body); return ESP_ERR_NO_MEM; }
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "request_id", id->valuestring);
    cJSON_AddStringToObject(out, "state", "released");
    cJSON_AddNumberToObject(out, "channel", channel);
    cJSON_AddNumberToObject(out, "gpio", board_reset_gpio(channel));
    cJSON_AddNumberToObject(out, "pulse_ms", BOARD_RESET_PULSE_MS);
    cJSON_Delete(body);
    return json_reply(req, "200 OK", out);
}

static cJSON *action_json(const reset_action_t *a)
{
    cJSON *out = cJSON_CreateObject();
    if (!out) return NULL;
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "boot_id", actions_boot_id());
    cJSON_AddStringToObject(out, "request_id", a->id);
    cJSON_AddStringToObject(out, "mode", a->mode);
    cJSON_AddNumberToObject(out, "channel", a->channel);
    cJSON_AddStringToObject(out, "state", a->state);
    cJSON_AddStringToObject(out, "result", esp_err_to_name(a->result));
    cJSON_AddNumberToObject(out, "started_us", a->started_us);
    cJSON_AddNumberToObject(out, "finished_us", a->finished_us);
    cJSON *pins = cJSON_AddArrayToObject(out, "pins");
    for (int ch = 1; ch <= BOARD_RESET_CHANNEL_COUNT; ++ch) {
        if (ch != a->channel && !(strcmp(a->mode, "reset") != 0 && ch == 1)) continue;
        board_reset_status_t trace = a->pin_traces[ch - 1];
        cJSON *pin = cJSON_CreateObject();
        if (!pin || !pins) { cJSON_Delete(pin); cJSON_Delete(out); return NULL; }
        cJSON_AddNumberToObject(pin, "channel", ch);
        cJSON_AddNumberToObject(pin, "gpio", board_reset_gpio(ch));
        cJSON_AddNumberToObject(pin, "pulse_count", trace.pulse_count);
        cJSON_AddStringToObject(pin, "last_request_id", trace.last_request_id);
        cJSON_AddNumberToObject(pin, "pressed_us", trace.pressed_us);
        cJSON_AddNumberToObject(pin, "released_us", trace.released_us);
        cJSON_AddItemToArray(pins, pin);
    }
    return out;
}

static esp_err_t action_get(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    char query[128], id[65];
    if (httpd_req_get_url_query_str(req, query, sizeof query) != ESP_OK ||
        httpd_query_key_value(query, "id", id, sizeof id) != ESP_OK)
        return error_reply(req, "400 Bad Request", "id required");
    reset_action_t action;
    if (!actions_get(id, &action)) return error_reply(req, "404 Not Found", "Action not found in this boot");
    cJSON *out = action_json(&action);
    return out ? json_reply(req, "200 OK", out) : ESP_ERR_NO_MEM;
}

static esp_err_t action_post(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    cJSON *body = read_json(req);
    if (!body) return ESP_FAIL;
    const cJSON *id = cJSON_GetObjectItemCaseSensitive(body, "request_id");
    const cJSON *boot = cJSON_GetObjectItemCaseSensitive(body, "boot_id");
    const cJSON *mode = cJSON_GetObjectItemCaseSensitive(body, "mode");
    const cJSON *ch = cJSON_GetObjectItemCaseSensitive(body, "channel");
    const cJSON *hold = cJSON_GetObjectItemCaseSensitive(body, "hold_ms");
    int channel = 0; unsigned hold_ms = 1500;
    bool valid = cJSON_IsString(id) && cJSON_IsString(boot) && cJSON_IsString(mode);
    if (valid && strcmp(mode->valuestring, "reset") == 0) {
        valid = cJSON_IsNumber(ch) && ch->valuedouble >= 1 && ch->valuedouble <= 3 &&
                ch->valuedouble == ch->valueint;
        if (valid) channel = ch->valueint;
    }
    if (hold) {
        bool good_hold = cJSON_IsNumber(hold) && hold->valuedouble >= 100 && hold->valuedouble <= 10000 &&
                         hold->valuedouble == hold->valueint;
        valid &= good_hold;
        if (good_hold) hold_ms = hold->valueint;
    }
    reset_action_t action;
    esp_err_t err = valid ? actions_submit(boot->valuestring, id->valuestring, mode->valuestring,
                                           channel, hold_ms, &action) : ESP_ERR_INVALID_ARG;
    cJSON_Delete(body);
    if (err != ESP_OK) return error_reply(req, err == ESP_ERR_INVALID_STATE ? "409 Conflict" : "400 Bad Request",
                                         esp_err_to_name(err));
    cJSON *out = action_json(&action);
    return out ? json_reply(req, "202 Accepted", out) : ESP_ERR_NO_MEM;
}

static esp_err_t wifi_post(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    cJSON *body = read_json(req);
    if (!body) return ESP_FAIL;
    cJSON *ssid = cJSON_GetObjectItemCaseSensitive(body, "ssid");
    cJSON *password = cJSON_GetObjectItemCaseSensitive(body, "password");
    esp_err_t err = cJSON_IsString(ssid) && cJSON_IsString(password) ?
                    settings_set_wifi(ssid->valuestring, password->valuestring) : ESP_ERR_INVALID_ARG;
    cJSON_Delete(body);
    if (err != ESP_OK) return error_reply(req, err == ESP_ERR_INVALID_ARG ? "400 Bad Request" : "500 Internal Server Error",
                                         err == ESP_ERR_INVALID_ARG ? "SSID: 1..32 bytes. Password: empty, 8..63 bytes, or 64 hex digits." : "Could not save Wi-Fi settings");
    cJSON *out = cJSON_CreateObject();
    if (!out) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "state", "saved_connecting");
    esp_err_t response = json_reply(req, "200 OK", out);
    network_schedule_reconfigure();
    return response;
}

static void reboot_task(void *arg)
{
    (void)arg;
    vTaskDelay(pdMS_TO_TICKS(750));
    esp_restart();
}

static esp_err_t reboot_post(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    cJSON *body = read_json(req);
    if (!body) return ESP_FAIL;
    cJSON_Delete(body);
    if (board_reset_begin_maintenance() != ESP_OK) return error_reply(req, "409 Conflict", "Controller is busy");
    if (xTaskCreate(reboot_task, "reboot", 2048, NULL, 4, NULL) != pdPASS) {
        board_reset_end_maintenance();
        return error_reply(req, "500 Internal Server Error", "Could not schedule reboot");
    }
    cJSON *out = cJSON_CreateObject();
    if (!out) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "state", "controller_rebooting");
    return json_reply(req, "200 OK", out);
}

static esp_err_t ota_post(httpd_req_t *req)
{
    if (!authorize(req)) return ESP_FAIL;
    const esp_partition_t *partition = esp_ota_get_next_update_partition(NULL);
    if (!partition || req->content_len < 256 || req->content_len > partition->size) {
        return error_reply(req, "413 Content Too Large", "Application binary has invalid size");
    }
    if (board_reset_begin_maintenance() != ESP_OK) return error_reply(req, "409 Conflict", "Controller is busy");
    esp_ota_handle_t ota = 0;
    esp_err_t err = esp_ota_begin(partition, req->content_len, &ota);
    if (err != ESP_OK) goto failed;
    size_t remaining = req->content_len;
    char buffer[2048];
    while (remaining) {
        int got = httpd_req_recv(req, buffer, remaining < sizeof buffer ? remaining : sizeof buffer);
        if (got <= 0) { err = ESP_FAIL; break; }
        err = esp_ota_write(ota, buffer, got);
        if (err != ESP_OK) break;
        remaining -= got;
    }
    if (err != ESP_OK) { esp_ota_abort(ota); goto failed; }
    err = esp_ota_end(ota); /* Validates image format, target chip and checksum. */
    if (err != ESP_OK) goto failed;
    esp_app_desc_t description;
    err = esp_ota_get_partition_description(partition, &description);
    if (err != ESP_OK || strcmp(description.project_name, "esp-reset") != 0) {
        err = ESP_ERR_INVALID_ARG;
        goto failed;
    }
    err = esp_ota_set_boot_partition(partition);
    if (err != ESP_OK) goto failed;
    if (xTaskCreate(reboot_task, "ota_reboot", 2048, NULL, 4, NULL) != pdPASS) {
        /* Keep the current app as the next boot target if task creation failed. */
        esp_ota_set_boot_partition(esp_ota_get_running_partition());
        err = ESP_ERR_NO_MEM;
        goto failed;
    }
    cJSON *out = cJSON_CreateObject();
    if (!out) return ESP_ERR_NO_MEM;
    cJSON_AddBoolToObject(out, "ok", true);
    cJSON_AddStringToObject(out, "state", "controller_rebooting");
    return json_reply(req, "200 OK", out);
failed:
    board_reset_end_maintenance();
    return error_reply(req, "400 Bad Request", "OTA rejected or interrupted; current firmware retained");
}

esp_err_t web_server_start(void)
{
    httpd_config_t config = HTTPD_DEFAULT_CONFIG();
    config.stack_size = 8192;
    config.max_uri_handlers = 10;
    config.lru_purge_enable = true;
    config.recv_wait_timeout = 10;
    httpd_handle_t server;
    esp_err_t err = httpd_start(&server, &config);
    if (err != ESP_OK) return err;
    const httpd_uri_t routes[] = {
        {.uri="/", .method=HTTP_GET, .handler=root_get},
        {.uri="/api/status", .method=HTTP_GET, .handler=status_get},
        {.uri="/api/action", .method=HTTP_GET, .handler=action_get},
        {.uri="/api/action", .method=HTTP_POST, .handler=action_post},
        {.uri="/api/reset", .method=HTTP_POST, .handler=reset_post},
        {.uri="/api/wifi", .method=HTTP_POST, .handler=wifi_post},
        {.uri="/api/reboot", .method=HTTP_POST, .handler=reboot_post},
        {.uri="/api/ota", .method=HTTP_POST, .handler=ota_post},
    };
    for (unsigned i = 0; i < sizeof routes / sizeof routes[0]; ++i) {
        err = httpd_register_uri_handler(server, &routes[i]);
        if (err != ESP_OK) { httpd_stop(server); return err; }
    }
    return ESP_OK;
}
