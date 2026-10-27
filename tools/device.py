#!/usr/bin/env python3
"""USB setup and control. Dependencies are provisioned by espreset with uv."""
import argparse
import getpass
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

import serial
from serial.tools import list_ports

PASSWORD = 'esp32reset'
PREFIX = '@ESP_RESET@'


class USB:
    def __init__(self, port=None):
        self.port = port
        self.identity = None
        self.connection = None

    def candidates(self):
        ports = list(list_ports.comports())
        if self.identity:
            vid, pid, number, location = self.identity
            matched = [p for p in ports if p.vid == vid and
                       ((number and p.serial_number == number) or
                        (not number and location and p.location == location))]
            if len(matched) == 1:
                return matched[0]
            # Never silently switch to a different attached controller.
            return None
        if self.port:
            return next((p for p in ports if p.device == self.port), None)
        ports = [p for p in ports if p.vid == 0x303A]
        if len(ports) == 1:
            return ports[0]
        if len(ports) > 1:
            raise ValueError('发现多个 ESP USB 端口，请用 --port 指定：' + ', '.join(p.device for p in ports))
        return None

    def connect(self, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            p = self.candidates()
            if p:
                try:
                    conn = serial.Serial(port=None, baudrate=115200, timeout=0.25, write_timeout=2)
                    conn.dtr = False
                    conn.rts = False
                    conn.port = p.device
                    conn.open()
                    self.connection = conn
                    self.port = p.device
                    self.identity = (p.vid, p.pid, p.serial_number, p.location)
                    print(f'USB: {self.port}')
                    return
                except serial.SerialException:
                    pass
            time.sleep(0.25)
        raise TimeoutError('未找到可用设备。请接原生 USB/OTG 口，退出其他串口程序，按 EN 运行固件；可用 --port 指定端口。')

    def close(self):
        if self.connection:
            self.connection.close()
            self.connection = None

    def request(self, op, timeout=15, **payload):
        if not self.connection:
            self.connect()
        request_id = uuid.uuid4().hex
        data = {'id': request_id, 'op': op, 'token': PASSWORD, **payload}
        # All writes, especially reset/boot operations, are issued exactly once.
        self.connection.write(('\n' + json.dumps(data, ensure_ascii=False) + '\n').encode())
        deadline = time.monotonic() + timeout
        pending = bytearray()
        while time.monotonic() < deadline:
            chunk = self.connection.read_until(b'\n')
            pending.extend(chunk)
            if len(pending) > 8192:
                pending.clear()
            if not pending.endswith(b'\n'):
                continue
            line = pending.decode('utf-8', 'replace').strip()
            pending.clear()
            if PREFIX not in line:
                continue
            try:
                reply = json.loads(line.split(PREFIX, 1)[1])
            except ValueError:
                continue
            if reply.get('id') != request_id:
                continue
            if not reply.get('ok'):
                raise ValueError(reply.get('error', '设备拒绝请求'))
            return reply
        raise TimeoutError(f'{op} 未收到确认。操作可能已执行，脚本不会自动重试。')


    def status_when_ready(self, timeout=60):
        """Only read-only status queries may be retried across USB startup."""
        deadline = time.monotonic() + timeout
        last_error = None
        while time.monotonic() < deadline:
            try:
                if not self.connection:
                    self.connect(timeout=min(3, max(0.1, deadline - time.monotonic())))
                return self.request('status', timeout=3)
            except (serial.SerialException, OSError, TimeoutError) as exc:
                last_error = exc
                self.close()
                time.sleep(0.25)
        raise TimeoutError(f'等待固件响应超时：{last_error}。若在下载模式，请按 EN/RESET。')


def show_status(value):
    print(f'后台密码：{value.get("password", PASSWORD)}')
    if value.get('connected') and value.get('ip'):
        url = f'http://{value["ip"]}'
        print(f'后台网页：{url}/')
        config = Path(__file__).resolve().parent.parent / 'device.json'
        config.write_text(json.dumps({'url': url}) + '\n')
        print(f'网络控制：./espreset reset --url {url} --channel 1')
    else:
        print('Wi-Fi 尚未连接。运行 ./espreset wifi 通过 USB 配网。')


def monitor(usb):
    print('串口监视（RESET 后自动重连，Ctrl-C 退出）')
    while True:
        try:
            if not usb.connection:
                usb.connect(timeout=30)
                show_status(usb.request('status'))
            data = usb.connection.read_until(b'\n')
            if data:
                print(data.decode('utf-8', 'replace'), end='', flush=True)
            if usb.port not in [p.device for p in list_ports.comports()]:
                raise serial.SerialException('USB 重新枚举')
        except (serial.SerialException, OSError, TimeoutError) as exc:
            usb.close()
            print(f'\n等待 USB 重连：{exc}')
            time.sleep(0.5)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', default='setup',
                        choices=['setup', 'flash', 'monitor', 'wifi', 'status', 'reset', 'loader', 'maskrom', 'ports', 'usb-status', 'result'])
    parser.add_argument('--port')
    parser.add_argument('--baud', type=int, default=115200)
    parser.add_argument('--no-stub', action='store_true')
    parser.add_argument('--ssid')
    parser.add_argument('--channel', type=int, choices=(1, 2, 3), default=1)
    parser.add_argument('--hold-ms', type=int, default=1500, help='Boot key hold after RESET release (100..10000 ms)')
    args = parser.parse_args(argv)
    if not 100 <= args.hold_ms <= 10000:
        parser.error('--hold-ms must be 100..10000')
    usb = USB(args.port)
    print('后台密码：' + PASSWORD)
    try:
        if args.command == 'result':
            print('USB 操作通过串口同步返回结果；查询历史网络操作请用 ./espreset result --ip ESP的IP。')
            return 1
        if args.command == 'ports':
            for p in list_ports.comports():
                print(p.device, p.description, p.serial_number or '')
            return 0
        if args.command in ('setup', 'flash'):
            usb.connect()
            port = usb.port
            usb.close()
            command = [sys.executable, str(Path(__file__).with_name('flash.py')), '--port', port, '--baud', str(args.baud)]
            if args.no_stub:
                command.append('--no-stub')
            result = subprocess.run(command, check=False).returncode
            if result:
                return result
            print('刷写完成。如果仍在下载模式，请按 EN/RESET（不要按 BOOT）。')
            show_status(usb.status_when_ready())
            if args.command == 'flash':
                return 0
        if args.command in ('wifi', 'setup'):
            ssid = args.ssid or input('2.4GHz Wi-Fi SSID：').strip()
            password = getpass.getpass('Wi-Fi 密码（开放网络留空）：')
            usb.request('wifi', ssid=ssid, password=password)
            print('已保存，等待联网…')
            deadline = time.monotonic() + 60
            # Allow the asynchronous Wi-Fi reconfiguration to start before polling.
            time.sleep(1.5)
            while time.monotonic() < deadline:
                value = usb.request('status')
                if value.get('connected'):
                    show_status(value)
                    break
                time.sleep(1)
            else:
                raise TimeoutError('60 秒内未联网。请检查 SSID/密码及 2.4GHz 网络，重新运行 ./espreset wifi。')
            if args.command == 'wifi':
                return 0
        if args.command in ('setup', 'monitor'):
            monitor(usb)
        elif args.command in ('status', 'usb-status'):
            show_status(usb.request('status'))
        elif args.command in ('reset', 'loader', 'maskrom'):
            payload = {'channel': args.channel} if args.command == 'reset' else {'hold_ms': args.hold_ms}
            usb.request(args.command, **payload)
            print(f'{args.command} 按键操作已完成并释放。启动模式请用目标板串口或 rkdeveloptool ld 确认。')
        return 0
    except KeyboardInterrupt:
        print('\n已退出。')
        return 0
    except (OSError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        usb.close()


if __name__ == '__main__':
    raise SystemExit(main())
