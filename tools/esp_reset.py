#!/usr/bin/env python3
"""CLI for the standalone ESP Reset HTTP API; reset requests are never retried."""
import argparse
import getpass
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid


def request(base: str, token: str, path: str, payload=None, binary: bytes | None = None):
    headers = {'X-Reset-Token': token}
    data = None
    if payload is not None:
        data = json.dumps(payload).encode()
        headers['Content-Type'] = 'application/json'
    if binary is not None:
        data = binary
        headers['Content-Type'] = 'application/octet-stream'
    req = urllib.request.Request(base.rstrip('/') + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=180 if binary is not None else 5) as response:
        value = json.load(response)
    if value.get('ok') is not True:
        raise ValueError(value.get('error', 'Request failed'))
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True, help='e.g. http://192.168.4.1')
    parser.add_argument('--token', default=os.environ.get('ESP_RESET_TOKEN', ''))
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('status')
    reset = commands.add_parser('reset')
    reset.add_argument('--channel', type=int, choices=(1, 2, 3), required=True,
                       help='Explicit reset channel (1, 2 or 3)')
    commands.add_parser('reboot', help='Reboot the ESP controller, not the target board')
    wifi = commands.add_parser('wifi')
    wifi.add_argument('ssid')
    wifi.add_argument('--password', default=None, help='Omit to enter privately; empty means open Wi-Fi')
    ota = commands.add_parser('ota')
    ota.add_argument('binary', type=Path)
    args = parser.parse_args(argv)
    token = args.token or getpass.getpass('Management password: ')
    base = args.url if '://' in args.url else 'http://' + args.url
    try:
        if args.command == 'status':
            value = request(base, token, '/api/status')
        elif args.command == 'reset':
            value = request(base, token, '/api/reset', {'channel': args.channel, 'request_id': uuid.uuid4().hex})
        elif args.command == 'reboot':
            value = request(base, token, '/api/reboot', {})
        elif args.command == 'wifi':
            password = args.password if args.password is not None else getpass.getpass('Wi-Fi password: ')
            value = request(base, token, '/api/wifi', {'ssid': args.ssid, 'password': password})
        else:
            value = request(base, token, '/api/ota', binary=args.binary.read_bytes())
        print(json.dumps(value, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as exc:
        print(f'HTTP {exc.code}: {exc.read(8192).decode("utf-8", "replace")}', file=sys.stderr)
    except (OSError, ValueError, urllib.error.URLError) as exc:
        print(f'Not confirmed: {exc}. Outcome may be unknown; no automatic retry.', file=sys.stderr)
    return 1


if __name__ == '__main__':
    raise SystemExit(main())
