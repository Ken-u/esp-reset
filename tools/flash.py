#!/usr/bin/env python3
"""Flash a CI firmware bundle using its exact ESP-IDF flash layout."""
import argparse
import json
from pathlib import Path
import subprocess
import sys


def flash_command(bundle: Path, port: str, baud: int) -> list[str]:
    config = json.loads((bundle / 'flasher_args.json').read_text())
    settings = config['flash_settings']
    chip = config['extra_esptool_args']['chip']
    command = [sys.executable, '-m', 'esptool', '--chip', chip, '--port', port,
               '--baud', str(baud), 'write_flash']
    for key in ('flash_mode', 'flash_freq', 'flash_size'):
        command.extend(['--' + key, settings[key]])
    for address, filename in sorted(config['flash_files'].items(), key=lambda pair: int(pair[0], 0)):
        path = (bundle / filename).resolve()
        if not path.is_relative_to(bundle.resolve()) or not path.is_file():
            raise ValueError(f'Invalid or missing firmware file: {filename}')
        command.extend([address, str(path)])
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', required=True)
    parser.add_argument('--baud', type=int, default=460800)
    parser.add_argument('--bundle', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    try:
        return subprocess.run(flash_command(args.bundle, args.port, args.baud), check=False).returncode
    except (ValueError, OSError, KeyError) as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
