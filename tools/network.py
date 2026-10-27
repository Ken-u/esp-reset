#!/usr/bin/env python3
"""Control ESP Reset over HTTP. No serial connection or third-party modules."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
import uuid

PASSWORD = 'esp32reset'
CONFIG = Path(__file__).resolve().parent.parent / 'device.json'


def request(url, path, payload=None):
    headers = {'X-Reset-Token': PASSWORD}
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload).encode()
    req = urllib.request.Request(url.rstrip('/') + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=5) as response:
        value = json.load(response)
    if not value.get('ok'):
        raise ValueError(value.get('error', 'Request failed'))
    return value


def save_terminal(action_id, state):
    path = CONFIG.with_name('last-action.json')
    if path.exists():
        saved = json.loads(path.read_text())
        if saved.get('request_id') == action_id:
            saved['state'] = state
            path.write_text(json.dumps(saved, ensure_ascii=False, indent=2) + '\n')


def run_action(url, mode, channel, hold_ms):
    receipt_file = CONFIG.with_name('last-action.json')
    if receipt_file.exists():
        previous = json.loads(receipt_file.read_text())
        if previous.get('state') == 'pending' and previous.get('url') == url:
            print('已有未确认操作，本次只查询旧操作，不会发送新的按键请求。', flush=True)
            return wait_action(url, previous['request_id'], previous['boot_id'])
    before = request(url, '/api/status')
    boot = before.get('boot_id')
    if not boot:
        raise ValueError('固件不支持操作结果查询，请先升级新固件。未发送按键请求。')
    action_id = uuid.uuid4().hex
    payload = {'request_id': action_id, 'boot_id': boot, 'mode': mode,
               'channel': channel, 'hold_ms': hold_ms}
    print(f'HTTP {url} · {mode} · 操作编号 {action_id}', flush=True)
    # Persist the id before sending. A lost response is resolved by GET only.
    receipt_file.write_text(json.dumps({'url': url, 'state': 'pending', **payload}, ensure_ascii=False, indent=2) + '\n')
    try:
        request(url, '/api/action', payload)
    except urllib.error.HTTPError as exc:
        if exc.code in (400, 401, 409):
            save_terminal(action_id, 'rejected')
            raise ValueError(f'请求被拒绝：HTTP {exc.code} {exc.read(4096).decode("utf-8", "replace")}') from exc
        print(f'提交回执丢失（HTTP {exc.code}），只查询结果，不重发。', flush=True)
    except (OSError, ValueError) as exc:
        print(f'提交回执丢失：{exc}；只查询结果，不重发。', flush=True)
    return wait_action(url, action_id, boot)


def wait_action(url, action_id, boot):
    deadline = time.monotonic() + 25
    last_error = ''
    while time.monotonic() < deadline:
        try:
            value = request(url, '/api/action?id=' + action_id)
            if value.get('boot_id') != boot:
                raise ValueError('ESP 已重启，旧操作结果未知；不会重发。')
            state = value.get('state')
            if state == 'released':
                save_terminal(action_id, 'released')
                print(f'已执行并释放 · {action_id}（目标板启动模式需另行确认）')
                return 0
            if state == 'failed':
                save_terminal(action_id, 'failed')
                raise ValueError(f'执行失败：{value.get("result")} · {action_id}')
        except urllib.error.HTTPError as exc:
            last_error = f'HTTP {exc.code}'
            # A new boot / missing receipt cannot prove the previous pulse failed.
            if exc.code == 404:
                state = request(url, '/api/status')
                if state.get('boot_id') != boot:
                    raise ValueError('ESP 已重启，旧操作结果未知；不会重发。') from exc
        except OSError as exc:
            last_error = str(exc)
        time.sleep(0.3)
    raise TimeoutError(f'结果未知：{last_error}；操作编号 {action_id}。用 sh run.sh result 查询；不要直接重发按键。')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('status', 'reset', 'loader', 'maskrom', 'result'))
    parser.add_argument('--url', default=os.environ.get('ESP_RESET_URL', ''))
    parser.add_argument('--channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--hold-ms', type=int, default=1500)
    parser.add_argument('--clear-unknown', action='store_true', help='result only: acknowledge unknown result and unlock, without pressing any button')
    args = parser.parse_args(argv)
    if args.clear_unknown and args.command != 'result':
        parser.error('--clear-unknown is only valid with result')
    if not 100 <= args.hold_ms <= 10000:
        parser.error('--hold-ms must be 100..10000')
    control_lock = None
    try:
        if args.command != 'status':
            control_lock = CONFIG.with_name('.control.lock').open('w')
            try:
                fcntl.flock(control_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('已有网络控制脚本运行中，请等待其结束。')
        if args.command == 'result':
            saved = json.loads(CONFIG.with_name('last-action.json').read_text())
            if args.clear_unknown:
                save_terminal(saved['request_id'], 'acknowledged_unknown')
                print('已解锁未知结果，未触发任何按键。')
                return 0
            return wait_action(saved['url'], saved['request_id'], saved['boot_id'])
        url = args.url
        if not url and CONFIG.exists():
            url = json.loads(CONFIG.read_text()).get('url', '')
        if not url:
            parser.error('请指定 --url http://ESP的IP，或设置 ESP_RESET_URL；日常控制走网络，不需要 USB。')
        if '://' not in url:
            url = 'http://' + url
        if args.command == 'status':
            print(json.dumps(request(url, '/api/status'), ensure_ascii=False, indent=2))
            return 0
        return run_action(url, args.command, args.channel, args.hold_ms)
    except (OSError, ValueError, KeyError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if control_lock is not None:
            control_lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
