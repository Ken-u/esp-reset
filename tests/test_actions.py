"""Run the real action registry/worker; retransmitting a job must not pulse again."""
import unittest
import test_native as native

HARNESS = r'''
#include <assert.h>
#include <setjmp.h>
#include <stdio.h>
#include <string.h>
#include "actions.h"
#include "freertos/semphr.h"
static void (*worker)(void *);
static jmp_buf stop;
static int calls, last_channel;
static int64_t now;
static char last_id[65];
int64_t esp_timer_get_time(void){return ++now;}
void esp_fill_random(void *out,size_t n){memset(out,0x5a,n);}
int xTaskCreate(void (*fn)(void *),const char *name,unsigned stack,void *arg,unsigned priority,void *handle){
    (void)name;(void)stack;(void)arg;(void)priority;(void)handle;worker=fn;return 1;
}
void vTaskDelay(unsigned ticks){(void)ticks;longjmp(stop,1);}
int board_reset_gpio(int ch){return ch+3;}
board_reset_status_t board_reset_status(int ch){(void)ch;board_reset_status_t out={0};return out;}
esp_err_t board_reset_press(int ch,const char *id){calls++;last_channel=ch;strcpy(last_id,id);return ESP_OK;}
esp_err_t board_reset_boot(int ch,const char *id,unsigned hold){assert(hold==1500);return board_reset_press(ch,id);}
static void run_worker(void){if(setjmp(stop)==0)worker(NULL);}
int main(void){
    assert(actions_start()==ESP_OK);
    reset_action_t a;
    const char *boot=actions_boot_id();
    assert(strlen(boot)==24);
    assert(actions_submit("old-boot","one","reset",3,0,&a)==ESP_ERR_INVALID_STATE);
    assert(calls==0);
    assert(actions_submit(boot,"one","reset",3,0,&a)==ESP_OK);
    assert(strcmp(a.state,"queued")==0);
    assert(actions_submit(boot,"one","reset",3,0,&a)==ESP_OK);
    assert(actions_submit(boot,"one","reset",2,0,&a)==ESP_ERR_INVALID_ARG);
    assert(actions_submit(boot,"two","reset",2,0,&a)==ESP_ERR_INVALID_STATE);
    run_worker();assert(calls==1 && last_channel==3 && strcmp(last_id,"one")==0);
    assert(actions_get("one",&a) && strcmp(a.state,"released")==0 && a.finished_us>a.started_us);
    assert(actions_submit(boot,"one","reset",3,0,&a)==ESP_OK);
    run_worker();assert(calls==1); /* Same id after a lost HTTP response: no second pulse. */
    assert(actions_submit(boot,"two","loader",0,1500,&a)==ESP_OK);
    run_worker();assert(calls==2 && last_channel==2);
    assert(actions_submit(boot,"three","maskrom",0,1500,&a)==ESP_OK);
    run_worker();assert(calls==3 && last_channel==3);
    assert(!actions_get("missing",&a));
    puts("Real action worker: lost-reply deduplication, boot identity, conflict rejection and combinations passed");
}
'''


class ActionTests(unittest.TestCase):
    def test_registry_and_worker_never_replay_completed_id(self):
        extra = {'esp_log.h': '#pragma once\n#define ESP_LOGI(...) ((void)0)\n'}
        for name, contents in extra.items():
            native.HEADERS[name] = contents
        original = native.HEADERS['freertos/task.h']
        native.HEADERS['freertos/task.h'] += '\nint xTaskCreate(void (*)(void *),const char *,unsigned,void *,unsigned,void *);\n'
        native.HEADERS['freertos/FreeRTOS.h'] += '\n#define pdPASS 1\n'
        try:
            native.NativeTests().compile_and_run('actions.c', HARNESS)
        finally:
            native.HEADERS['freertos/task.h'] = original
