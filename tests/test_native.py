"""Exercise the real C modules with deterministic GPIO, clock and NVS shims."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
HEADERS = {
    'sdkconfig.h': '#pragma once\n',
    'esp_err.h': '''#pragma once
typedef int esp_err_t;
#define ESP_OK 0
#define ESP_FAIL -1
#define ESP_ERR_NO_MEM 1
#define ESP_ERR_INVALID_STATE 2
#define ESP_ERR_INVALID_ARG 3
#define ESP_ERR_INVALID_SIZE 4
#define ESP_ERR_NVS_NOT_FOUND 5
''',
    'driver/gpio.h': '''#pragma once
#include <stdint.h>
#include "esp_err.h"
typedef struct { uint64_t pin_bit_mask; int mode, pull_up_en, pull_down_en, intr_type; } gpio_config_t;
#define GPIO_MODE_OUTPUT 1
#define GPIO_PULLUP_DISABLE 0
#define GPIO_PULLDOWN_ENABLE 1
#define GPIO_INTR_DISABLE 0
#define GPIO_IS_VALID_OUTPUT_GPIO(pin) ((pin) >= 0 && (pin) < 49)
esp_err_t gpio_set_level(int pin, int level);
esp_err_t gpio_config(const gpio_config_t *config);
''',
    'esp_timer.h': '#pragma once\n#include <stdint.h>\nint64_t esp_timer_get_time(void);\n',
    'esp_random.h': '#pragma once\n#include <stddef.h>\nvoid esp_fill_random(void *out, size_t n);\n',
    'freertos/FreeRTOS.h': '''#pragma once
#include <stddef.h>
#define pdTRUE 1
#define portMAX_DELAY -1
#define pdMS_TO_TICKS(ms) ((ms) / 10)
''',
    'freertos/semphr.h': '''#pragma once
typedef void *SemaphoreHandle_t;
SemaphoreHandle_t xSemaphoreCreateMutex(void);
int xSemaphoreTake(SemaphoreHandle_t lock, int wait);
int xSemaphoreGive(SemaphoreHandle_t lock);
''',
    'freertos/task.h': '#pragma once\nvoid vTaskDelay(unsigned ticks);\n',
    'nvs.h': '''#pragma once
#include <stddef.h>
#include "esp_err.h"
typedef int nvs_handle_t;
#define NVS_READWRITE 1
esp_err_t nvs_open(const char *, int, nvs_handle_t *);
esp_err_t nvs_get_blob(nvs_handle_t, const char *, void *, size_t *);
esp_err_t nvs_get_str(nvs_handle_t, const char *, char *, size_t *);
esp_err_t nvs_set_blob(nvs_handle_t, const char *, const void *, size_t);
esp_err_t nvs_set_str(nvs_handle_t, const char *, const char *);
esp_err_t nvs_commit(nvs_handle_t);
void nvs_close(nvs_handle_t);
''',
}
LOCK = '''
static int locked;
SemaphoreHandle_t xSemaphoreCreateMutex(void) { return &locked; }
int xSemaphoreTake(SemaphoreHandle_t lock, int wait) {
    (void)lock; (void)wait; if (locked) return 0; locked = 1; return 1;
}
int xSemaphoreGive(SemaphoreHandle_t lock) { (void)lock; locked = 0; return 1; }
'''
RESET_HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include "board_reset.h"
#include "driver/gpio.h"
#include "freertos/semphr.h"
static int configured, level = -1, highs, lows, fail_high, fail_low;
static int64_t now;
esp_err_t gpio_set_level(int pin, int value) {
    assert(pin == BOARD_RESET_GPIO);
    if (value) { assert(configured); highs++; if (fail_high) return ESP_FAIL; }
    else { lows++; if (fail_low) return ESP_FAIL; }
    level = value; return ESP_OK;
}
esp_err_t gpio_config(const gpio_config_t *c) {
    assert(level == 0);
    assert(c->pin_bit_mask == (1ULL << BOARD_RESET_GPIO));
    assert(c->mode == GPIO_MODE_OUTPUT && c->pull_up_en == 0 && c->pull_down_en == 1);
    configured = 1; return ESP_OK;
}
int64_t esp_timer_get_time(void) { return now; }
void vTaskDelay(unsigned ticks) {
    assert(level == 1 && ticks * 10 >= 200 && ticks * 10 <= 220);
    assert(board_reset_press("concurrent") == ESP_ERR_INVALID_STATE);
    assert(board_reset_begin_maintenance() == ESP_ERR_INVALID_STATE);
    now += ticks * 10000;
}
int main(void) {
#ifdef EXPECT_INVALID_GPIO
    assert(board_reset_init() == ESP_ERR_INVALID_ARG);
    assert(highs == 0 && lows == 0);
    return 0;
#endif
    assert(board_reset_press("first") == ESP_ERR_INVALID_STATE);
    assert(board_reset_init() == ESP_OK && level == 0 && highs == 0);
    assert(board_reset_press(NULL) == ESP_ERR_INVALID_ARG);
    assert(board_reset_press("") == ESP_ERR_INVALID_ARG);
    assert(board_reset_press("first") == ESP_OK && highs == 1 && level == 0);
    assert(board_reset_press("first") == ESP_OK && highs == 1); /* no second pulse */
    assert(board_reset_press("second") == ESP_ERR_INVALID_STATE);
    now += 2999999;
    assert(board_reset_press("second") == ESP_ERR_INVALID_STATE);
    now++;
    assert(board_reset_press("second") == ESP_OK && highs == 2);
    now += 3000000;
    assert(board_reset_begin_maintenance() == ESP_OK);
    assert(board_reset_press("maintenance") == ESP_ERR_INVALID_STATE);
    board_reset_end_maintenance();
    assert(board_reset_press("third") == ESP_OK && highs == 3);
    board_reset_status_t status = board_reset_status();
    assert(status.count == 3 && status.cooldown_ms == 3000 && status.enabled);
    now += 3000000;
    fail_high = 1;
    int before = lows;
    assert(board_reset_press("error-high") == ESP_FAIL && lows == before + 1 && level == 0);
    fail_high = 0;
    now += 3000000;
    fail_low = 1;
    assert(board_reset_press("error-low") == ESP_FAIL);
    assert(board_reset_status().count == 3);
    assert(!locked);
    puts("Reset startup, pulse, cooldown, deduplication and maintenance passed");
}
'''
SETTINGS_HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "settings.h"
#include "nvs.h"
#include "freertos/semphr.h"
static char persisted_token[25], blob[98], pending_blob[98];
static int have_blob, commit_fails;
esp_err_t nvs_open(const char *name, int mode, nvs_handle_t *out) {
    assert(strcmp(name,"esp_reset")==0); (void)mode; *out=1; return ESP_OK;
}
esp_err_t nvs_get_blob(nvs_handle_t h,const char *k,void *out,size_t *n) {
    (void)h; (void)k; if(!have_blob) return ESP_ERR_NVS_NOT_FOUND;
    assert(*n==sizeof blob); memcpy(out,blob,sizeof blob); return ESP_OK;
}
esp_err_t nvs_get_str(nvs_handle_t h,const char *k,char *out,size_t *n) {
    (void)h; (void)k; (void)n; if(!persisted_token[0])return ESP_ERR_NVS_NOT_FOUND;
    strcpy(out,persisted_token);return ESP_OK;
}
esp_err_t nvs_set_blob(nvs_handle_t h,const char *k,const void *in,size_t n) {
    (void)h; (void)k; assert(n==sizeof blob);memcpy(pending_blob,in,n);return ESP_OK;
}
esp_err_t nvs_set_str(nvs_handle_t h,const char *k,const char *in) {
    (void)h; (void)k;strcpy(persisted_token,in);return ESP_OK;
}
esp_err_t nvs_commit(nvs_handle_t h) {
    (void)h;if(commit_fails)return ESP_FAIL;
    if(pending_blob[0] || have_blob){memcpy(blob,pending_blob,sizeof blob);have_blob=1;}
    return ESP_OK;
}
void nvs_close(nvs_handle_t h){(void)h;}
void esp_fill_random(void *out,size_t n){memset(out,0x9a,n);}
int main(void){
    assert(settings_init()==ESP_OK);
    reset_settings_t s=settings_get();
    assert(!s.ssid[0] && strlen(s.token)==24 && settings_authorize(s.token));
    assert(!settings_authorize(NULL) && !settings_authorize("wrong"));
    assert(!settings_authorize("000000000000000000000000"));
    assert(settings_set_wifi("wifi","short")==ESP_ERR_INVALID_ARG);
    assert(settings_set_wifi("","12345678")==ESP_ERR_INVALID_ARG);
    assert(settings_set_wifi("123456789012345678901234567890123","12345678")==ESP_ERR_INVALID_ARG);
    assert(settings_set_wifi("wifi","12345678")==ESP_OK);
    assert(strcmp(settings_get().ssid,"wifi")==0);
    commit_fails=1;
    assert(settings_set_wifi("other","12345678")==ESP_FAIL);
    assert(strcmp(settings_get().ssid,"wifi")==0);
    commit_fails=0;
    char hex[65];memset(hex,'a',64);hex[64]=0;
    assert(settings_set_wifi("hex",hex)==ESP_OK);
    hex[10]='z';assert(settings_set_wifi("bad",hex)==ESP_ERR_INVALID_ARG);
    assert(settings_init()==ESP_OK && strcmp(settings_get().ssid,"hex")==0);
    assert(settings_clear_wifi()==ESP_OK);
    assert(!settings_get().ssid[0] && settings_authorize(s.token));
    assert(settings_set_wifi("open","")==ESP_OK);
    puts("Wi-Fi validation, token, persistence and failed-write handling passed");
}
'''


class NativeTests(unittest.TestCase):
    def compile_and_run(self, module, harness, definitions=()):
        with tempfile.TemporaryDirectory() as directory:
            build = Path(directory)
            for name, contents in HEADERS.items():
                path = build / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(contents)
            (build / 'harness.c').write_text(harness.replace('int main(void)', LOCK + '\nint main(void)'))
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c11', '-Wall', '-Wextra', '-Werror',
                            '-I'+str(build), '-I'+str(ROOT/'main'), *definitions,
                            str(ROOT/'main'/module), str(build/'harness.c'), '-o', str(build/'test')], check=True)
            subprocess.run([str(build/'test')], check=True)

    def test_s3_gpio_controller(self):
        self.compile_and_run('board_reset.c', RESET_HARNESS,
                             ['-DCONFIG_RESET_OUTPUT_GPIO=4', '-DCONFIG_RESET_RECOVERY_GPIO=0', '-DCONFIG_IDF_TARGET_ESP32S3=1'])

    def test_c3_gpio_controller(self):
        self.compile_and_run('board_reset.c', RESET_HARNESS,
                             ['-DCONFIG_RESET_OUTPUT_GPIO=20', '-DCONFIG_RESET_RECOVERY_GPIO=9', '-DCONFIG_IDF_TARGET_ESP32C3=1'])

    def test_s3_rejects_memory_and_usb_pins(self):
        for pin in (19, 20, 35, 36, 37, 43, 45):
            with self.subTest(pin=pin):
                self.compile_and_run('board_reset.c', RESET_HARNESS,
                    [f'-DCONFIG_RESET_OUTPUT_GPIO={pin}', '-DCONFIG_RESET_RECOVERY_GPIO=0',
                     '-DCONFIG_IDF_TARGET_ESP32S3=1', '-DEXPECT_INVALID_GPIO=1'])

    def test_wifi_settings(self):
        self.compile_and_run('settings.c', SETTINGS_HARNESS)


if __name__ == '__main__':
    unittest.main()
