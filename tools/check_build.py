#!/usr/bin/env python3
"""Verify the generated CI board profiles, not just the requested defaults."""
import json
from pathlib import Path
import sys


def check(build: Path, target: str) -> None:
    config = json.loads((build/'config/sdkconfig.json').read_text())
    flash = json.loads((build/'flasher_args.json').read_text())
    expected = (3, 4, 5) if target == 'esp32c3' else (4, 5, 6)
    actual = tuple(config[f'RESET_OUTPUT_GPIO_{i}'] for i in (1, 2, 3))
    assert config['IDF_TARGET'] == target, config['IDF_TARGET']
    assert actual == expected, (actual, expected)
    assert flash['extra_esptool_args']['chip'] == target
    assert flash['flash_settings']['flash_size'] == ('4MB' if target == 'esp32c3' else '16MB')
    if target == 'esp32c3':
        assert config.get('ESP_CONSOLE_USB_SERIAL_JTAG') is True
        assert not config.get('ESP_CONSOLE_UART'), 'C3 application UART must stay off GPIO20/21'
    print(f'Verified {target}: channels {actual}, flash profile and console configuration')


if __name__ == '__main__':
    check(Path(sys.argv[1]), sys.argv[2])
