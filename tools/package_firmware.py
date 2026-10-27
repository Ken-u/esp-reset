#!/usr/bin/env python3
"""Prepare a self-contained USB flash bundle and a separate OTA app image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def package(build: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    firmware = destination / 'firmware'
    firmware.mkdir(exist_ok=True)
    config = json.loads((build / 'flasher_args.json').read_text())
    for filename in config['flash_files'].values():
        source = (build / filename).resolve()
        if not source.is_relative_to(build.resolve()) or not source.is_file():
            raise ValueError(f'Invalid firmware path: {filename}')
        output = firmware / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
    shutil.copy2(build / 'flasher_args.json', firmware / 'flasher_args.json')
    scripts = destination / 'tools'
    scripts.mkdir(exist_ok=True)
    for name in ('flash.py', 'device.py', 'esp_reset.py'):
        shutil.copy2(Path(__file__).with_name(name), scripts / name)
    shutil.copy2(Path(__file__).with_name('run.sh'), destination / 'run.sh')
    for name in ('setup', 'flash', 'monitor', 'wifi', 'status', 'reset', 'loader', 'maskrom'):
        wrapper = destination / (name + '.sh')
        wrapper.write_text('#!/bin/sh\nset -eu\nbase_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)\nexec sh "$base_dir/run.sh" ' + name + ' "$@"\n')
        wrapper.chmod(0o755)
    (destination / 'run.sh').chmod(0o755)
    docs = destination / 'docs'
    docs.mkdir(exist_ok=True)
    for name in ('hardware.md', 'reset-wiring.svg'):
        shutil.copy2(Path(__file__).resolve().parents[1] / 'docs' / name, docs / name)
    chip = config['extra_esptool_args']['chip']
    (destination / 'README.txt').write_text(
        f'ESP Reset / {chip} — macOS / Linux\n\n'
        '接 ESP 原生 USB/OTG 口（不是 USB-UART 桥接串口）。退出其他串口程序。\n'
        'sh setup.sh                 安装 uv/Python 依赖 → 刷写 → USB 配网 → 串口监视\n'
        'sh flash.sh                 只刷写，默认 115200；--no-stub 可绕过 stub\n'
        'sh wifi.sh                  USB 配网，隐藏输入 Wi-Fi 密码，打印后台地址\n'
        'sh monitor.sh               串口监视，自动重连，并主动查询密码/地址\n'
        'sh status.sh                查询后台地址和密码\n'
        'sh reset.sh --channel 1     单路复位，通道 1/2/3 可选\n'
        'sh loader.sh                通道 2 按住 RECOVERY → 通道 1 RESET → 松开\n'
        'sh maskrom.sh               通道 3 按住 MASKROM → 通道 1 RESET → 松开\n'
        '所有脚本支持 --port /dev/cu.usbmodemXXX 或 --port /dev/ttyACM0\n'
        '自动选择唯一 ESP 原生 USB，多设备必须指定端口。RESET 后自动重连同一设备。\n'
        '后台密码固定 esp32reset；自动 AP 已关闭；联网失败可随时 USB 重新配网。\n'
        'S3 通道 1/2/3: GPIO4/5/6；C3: GPIO3/4/5（禁用20/21）。\n'
        'loader/maskrom 仅在同一目标板接好 RESET/RECOVERY/MASKROM 三个独立开关后使用。\n'
        '三路各接不同板的 RESET 时，只用 reset.sh --channel N。\n'
        'GPIO 通过独立 MOSFET/PhotoMOS 模拟按键，不能直接跨接未知信号。\n'
        '默认启动键在 RESET 前按住100ms，RESET 200ms，松开 RESET 后再保持1500ms。\n'
        'loader/maskrom 支持 --hold-ms 100..10000；不保证板级启动模式，需目标板验证。\n'
        'Maskrom 必须接板上专用按键/安全控制接口，勿把 eMMC 时钟直接接到 ESP GPIO。\n'
        '固件在 firmware/，内部脚本在 tools/。OTA 仅上传 firmware/esp-reset.bin。\n'
        '刷写不清除 Wi-Fi；旧随机管理密码升级后替换为 esp32reset。\n'
        '依赖固定 esptool 5.6.0、pyserial 3.5；uv 自动准备 Python 3.12，无需编译环境。\n')
    manifest = []
    for path in sorted(destination.rglob('*')):
        if path.is_file() and path.name != 'SHA256SUMS':
            manifest.append(hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + str(path.relative_to(destination)))
    (destination / 'SHA256SUMS').write_text('\n'.join(manifest) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('build', type=Path)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    package(args.build, args.destination)
