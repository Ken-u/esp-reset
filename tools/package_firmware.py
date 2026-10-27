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
    for name in ('flash.py', 'device.py', 'esp_reset.py', 'network.py'):
        shutil.copy2(Path(__file__).with_name(name), scripts / name)
    shutil.copy2(Path(__file__).with_name('run.sh'), destination / 'run.sh')
    for name in ('setup', 'flash', 'monitor', 'wifi', 'status', 'reset', 'loader', 'maskrom', 'result'):
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
        '日常控制通过 Wi-Fi + HTTP，无需 USB 接电脑。ESP 与目标板按接线图连接。\n'
        '后台密码固定 esp32reset；AP 关闭；网页支持三路独立复位及 Loader/Maskrom 组合。\n\n'
        '网络控制（--url 可省略 http://；也可设置 ESP_RESET_URL）：\n'
        'sh status.sh --url http://192.168.1.50\n'
        'sh reset.sh --url http://192.168.1.50 --channel 3\n'
        'sh loader.sh --url http://192.168.1.50\n'
        'sh maskrom.sh --url http://192.168.1.50 --hold-ms 1500\n'
        'sh result.sh               只查询上一次操作，不发新按键请求\n'
        'sh result.sh --clear-unknown  清除最近操作的未知状态标记，不按键\n'
        '回执仅供查询，不锁定后续操作；每次手动运行都是新请求，超时不自动重发。\n'
        '网络脚本只需 uv/Python，不安装串口或烧录依赖。\n\n'
        '首次刷机/可选 USB 配网：连接 ESP 原生 USB/OTG 口，退出其他串口程序。\n'
        'sh setup.sh    uv准备环境 → 刷写 → USB配网 → 显示后台地址 → 看串口\n'
        'sh flash.sh    只刷写，默认115200，支持 --no-stub / --baud\n'
        'sh wifi.sh     USB配网，隐藏输入Wi-Fi密码，联网后保存地址到device.json\n'
        'sh monitor.sh  USB串口监视并自动重连\n'
        'sh run.sh usb-status  USB查询；USB入口可用 --port 指定端口\n'
        '首次联网后可以断开电脑USB；ESP保留Wi-Fi配置，使用自己的供电。\n'
        '刷写和USB脚本用uv准备Python3.12/esptool5.6.0/pyserial3.5，无需编译环境。\n\n'
        'S3通道1/2/3=GPIO4/5/6；C3=GPIO3/4/5，禁用20/21。\n'
        '组合模式：同一目标板，通道1接RESET、2接RECOVERY、3接专用MASKROM。\n'
        '先按模式键100ms，再RESET200ms，松RESET后再保持模式键1500ms，最后松模式键。\n'
        '三路接不同板RESET时，只使用单路reset；组合操作的实际模式需目标板验证。\n'
        '每路通过独立MOSFET/PhotoMOS模拟按键，不可直接跨接未知信号或eMMC时钟。\n'
        '固件/OTA应用在firmware/，内部工具在tools/，接线图在docs/。\n'
        '诊断：网页显示操作编号、GPIO拉高次数/时间及ESP启动编号；可区分回执丢失和重复执行。\n')
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
