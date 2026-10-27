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
static int configured, levels[49], highs[49], lows[49], last_high, fail_high, fail_low;
static int depth, nested_done;
static int64_t now;
static int valid_pin(int pin) {
    return pin == board_reset_gpio(1) || pin == board_reset_gpio(2) || pin == board_reset_gpio(3);
}
esp_err_t gpio_set_level(int pin, int value) {
    assert(valid_pin(pin));
    if (value) { assert(configured); highs[pin]++; last_high=pin; if (fail_high) return ESP_FAIL; }
    else { lows[pin]++; if (fail_low) return ESP_FAIL; }
    levels[pin]=value; return ESP_OK;
}
esp_err_t gpio_config(const gpio_config_t *c) {
    uint64_t mask=0;
    for(int ch=1;ch<=3;ch++) {
        int pin=board_reset_gpio(ch);mask|=1ULL<<pin;
        assert(levels[pin]==0 && lows[pin]==1 && highs[pin]==0);
    }
    assert(c->pin_bit_mask == mask);
    assert(c->mode == GPIO_MODE_OUTPUT && c->pull_up_en == 0 && c->pull_down_en == 1);
    configured=1;return ESP_OK;
}
int64_t esp_timer_get_time(void) { return now; }
void vTaskDelay(unsigned ticks) {
    int pin=last_high, channel=0;
    for(int ch=1;ch<=3;ch++)if(board_reset_gpio(ch)==pin)channel=ch;
    assert(channel && levels[pin]==1 && ticks*10>=200 && ticks*10<=220);
    assert(board_reset_press(channel,"concurrent")==ESP_ERR_INVALID_STATE);
    assert(board_reset_begin_maintenance()==ESP_ERR_INVALID_STATE);
    assert(board_reset_status(channel).active);
    if(channel==1 && depth==0 && !nested_done) {
        nested_done=1;depth++;
        assert(board_reset_press(2,"nested")==ESP_OK);
        assert(levels[pin]==1); /* Channel 2 must not release channel 1. */
        assert(board_reset_status(1).active && !board_reset_status(2).active);
        depth--;
    }
    now+=ticks*10000;
}
int main(void) {
#ifdef EXPECT_INVALID_GPIO
    assert(board_reset_init()==ESP_ERR_INVALID_ARG);
    for(int pin=0;pin<49;pin++)assert(highs[pin]==0 && lows[pin]==0);
    return 0;
#endif
    int p1=board_reset_gpio(1),p2=board_reset_gpio(2),p3=board_reset_gpio(3);
    assert(BOARD_RESET_CHANNEL_COUNT==3);
    assert(board_reset_gpio(0)==-1 && board_reset_gpio(4)==-1);
    assert(board_reset_press(1,"first")==ESP_ERR_INVALID_STATE);
    assert(board_reset_init()==ESP_OK && configured);
    assert(board_reset_press(0,"bad")==ESP_ERR_INVALID_ARG);
    assert(board_reset_press(4,"bad")==ESP_ERR_INVALID_ARG);
    assert(board_reset_press(1,NULL)==ESP_ERR_INVALID_ARG);
    assert(board_reset_press(1,"")==ESP_ERR_INVALID_ARG);
    char long_id[66];for(int i=0;i<65;i++)long_id[i]='a';long_id[65]=0;
    assert(board_reset_press(1,long_id)==ESP_ERR_INVALID_ARG);
    assert(board_reset_press(1,"shared")==ESP_OK);
    assert(highs[p1]==1 && highs[p2]==1 && highs[p3]==0);
    assert(!levels[p1] && !levels[p2] && !levels[p3]);
    assert(board_reset_press(1,"shared")==ESP_OK && highs[p1]==1);
    assert(board_reset_press(1,"second")==ESP_ERR_INVALID_STATE);
    assert(board_reset_press(2,"second")==ESP_ERR_INVALID_STATE);
    assert(board_reset_press(3,"shared")==ESP_OK && highs[p3]==1); /* dedupe is per-channel */
    assert(board_reset_status(1).count==1 && board_reset_status(2).count==1 && board_reset_status(3).count==1);
    now+=3000000;
    assert(board_reset_press(1,"second")==ESP_OK && highs[p1]==2);
    assert(board_reset_status(1).cooldown_ms==3000);
    assert(board_reset_begin_maintenance()==ESP_OK);
    for(int ch=1;ch<=3;ch++)assert(board_reset_press(ch,"maintenance")==ESP_ERR_INVALID_STATE);
    board_reset_end_maintenance();
    assert(board_reset_press(2,"second")==ESP_OK && highs[p2]==2);
    now+=3000000;
    fail_high=1;int previous=lows[p1];
    assert(board_reset_press(1,"high-error")==ESP_FAIL && lows[p1]==previous+1 && levels[p1]==0);
    fail_high=0;
    fail_low=1;
    assert(board_reset_press(3,"low-error")==ESP_FAIL);
    fail_low=0;now+=3000000;
    board_reset_end_maintenance();
    assert(!board_reset_status(3).enabled && board_reset_status(3).count==1);
    assert(board_reset_press(3,"fault-latched")==ESP_ERR_INVALID_STATE);
    assert(board_reset_press(2,"healthy")==ESP_OK && highs[p2]==3);
    assert(board_reset_status(1).count==2 && !board_reset_status(1).active);
    assert(!locked);
    puts("Three channels: startup, isolation, concurrent pulse, cooldown, dedupe, maintenance and fault isolation passed");
}
'''
BOOT_HARNESS = r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "board_reset.h"
#include "driver/gpio.h"
#include "freertos/semphr.h"
static int levels[49], events[64], n, fail_release, phase;
static int64_t now;
esp_err_t gpio_set_level(int pin,int level) {
    if(n < 64) events[n++]=pin*2+level;
    if(!level && pin==fail_release)return ESP_FAIL;
    levels[pin]=level;return ESP_OK;
}
esp_err_t gpio_config(const gpio_config_t *c){(void)c;return ESP_OK;}
int64_t esp_timer_get_time(void){return now;}
void vTaskDelay(unsigned ticks){
    assert(board_reset_begin_maintenance()==ESP_ERR_INVALID_STATE);
    assert(board_reset_press(3,"interleaving")==ESP_ERR_INVALID_STATE);
    assert(board_reset_boot(2,"overlap",1500)==ESP_ERR_INVALID_STATE);
    int p1=board_reset_gpio(1),p2=board_reset_gpio(2),p3=board_reset_gpio(3);
    int strap=levels[p2]?p2:p3;
    assert(levels[strap]==1);
    if(phase%3==0)assert(!levels[p1] && ticks==10);
    if(phase%3==1)assert(levels[p1] && ticks==20);
    if(phase%3==2)assert(!levels[p1] && ticks==150);
    phase++;now+=ticks*10000;
}
int main(void){
    assert(board_reset_init()==ESP_OK);n=0;
    int p1=board_reset_gpio(1),p2=board_reset_gpio(2),p3=board_reset_gpio(3);
    assert(board_reset_boot(1,"bad",1500)==ESP_ERR_INVALID_ARG && n==0);
    assert(board_reset_boot(2,"bad",99)==ESP_ERR_INVALID_ARG && n==0);
    assert(board_reset_boot(2,"loader",1500)==ESP_OK);
    int loader[]={p2*2+1,p1*2+1,p1*2,p2*2};
    assert(n==4 && memcmp(events,loader,sizeof loader)==0);
    assert(!levels[p1] && !levels[p2] && !levels[p3]);
    assert(board_reset_boot(2,"loader",1500)==ESP_OK && n==4);
    assert(board_reset_boot(3,"rom",1500)==ESP_ERR_INVALID_STATE && n==4);
    now+=3000000;n=0;
    assert(board_reset_boot(3,"rom",1500)==ESP_OK);
    int rom[]={p3*2+1,p1*2+1,p1*2,p3*2};
    assert(n==4 && memcmp(events,rom,sizeof rom)==0);
    assert(board_reset_status(1).count==2 && board_reset_status(2).count==1 && board_reset_status(3).count==1);
    now+=3000000;fail_release=p3;n=0;
    assert(board_reset_boot(3,"release-fail",1500)==ESP_FAIL);
    assert(!board_reset_status(3).enabled && !board_reset_status(1).active);
    fail_release=0;now+=3000000;
    assert(board_reset_boot(2,"blocked-fault",1500)==ESP_ERR_INVALID_STATE);
    puts("Boot sequence order, timing, exclusion, deduplication, cooldown and release fault passed");
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
    assert(!s.ssid[0] && strcmp(s.token,"esp32reset")==0 && settings_authorize(s.token));
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

    def reset_definitions(self, target, pins):
        return [f'-DCONFIG_RESET_OUTPUT_GPIO_{i}={pin}' for i, pin in enumerate(pins, 1)] + [
            f'-DCONFIG_RESET_RECOVERY_GPIO={0 if target == "ESP32S3" else 9}',
            f'-DCONFIG_IDF_TARGET_{target}=1']

    def test_s3_gpio_controller(self):
        self.compile_and_run('board_reset.c', RESET_HARNESS, self.reset_definitions('ESP32S3', (4, 5, 6)))

    def test_c3_gpio_controller(self):
        self.compile_and_run('board_reset.c', RESET_HARNESS, self.reset_definitions('ESP32C3', (3, 4, 5)))

    def test_s3_rejects_memory_usb_and_duplicate_pins(self):
        for pin in (19, 20, 35, 36, 37, 43, 45, 5):
            with self.subTest(pin=pin):
                self.compile_and_run('board_reset.c', RESET_HARNESS,
                    self.reset_definitions('ESP32S3', (pin, 5, 6)) + ['-DEXPECT_INVALID_GPIO=1'])

    def test_c3_rejects_20_21_on_every_channel(self):
        for pin in (20, 21):
            for index in range(3):
                pins = [3, 4, 5]
                pins[index] = pin
                with self.subTest(pin=pin, channel=index+1):
                    self.compile_and_run('board_reset.c', RESET_HARNESS,
                        self.reset_definitions('ESP32C3', pins) + ['-DEXPECT_INVALID_GPIO=1'])

    def test_boot_sequences(self):
        for target, pins in [('ESP32S3', (4, 5, 6)), ('ESP32C3', (3, 4, 5))]:
            with self.subTest(target=target):
                self.compile_and_run('board_reset.c', BOOT_HARNESS, self.reset_definitions(target, pins))

    def test_wifi_settings(self):
        self.compile_and_run('settings.c', SETTINGS_HARNESS)


if __name__ == '__main__':
    unittest.main()
