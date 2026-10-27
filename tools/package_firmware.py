#!/usr/bin/env python3
"""Prepare a self-contained USB flash bundle and a separate OTA app image."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def package(build: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    config = json.loads((build / 'flasher_args.json').read_text())
    for filename in config['flash_files'].values():
        source = (build / filename).resolve()
        if not source.is_relative_to(build.resolve()) or not source.is_file():
            raise ValueError(f'Invalid firmware path: {filename}')
        output = destination / filename
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, output)
    shutil.copy2(build / 'flasher_args.json', destination / 'flasher_args.json')
    shutil.copy2(Path(__file__).with_name('flash.py'), destination / 'flash.py')
    chip = config['extra_esptool_args']['chip']
    (destination / 'README.txt').write_text(
        f'ESP Reset / {chip}\n\nUSB: pip install esptool\n'
        'python flash.py --port YOUR_SERIAL_PORT\n\n'
        'OTA: upload esp-reset.bin in the existing ESP Reset web console.\n'
        'Do not use C3 firmware on S3 or vice versa.\n'
        'After USB flash, open serial monitor at 115200 for AP and management password.\n'
        'Firmware does not erase NVS; hold BOOT 5s at runtime to clear only Wi-Fi.\n')
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
