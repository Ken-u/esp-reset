#!/usr/bin/env python3
"""Version planning and exact release asset sets (no publishing side effects)."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BOARDS = ('esp32s3-n16r8', 'esp32c3-supermini')


def read_version(root=ROOT):
    value = (root/'version.txt').read_text().strip()
    if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', value):
        raise ValueError('version.txt must contain a single stable version, e.g. 0.5.0')
    return value


def previous_version(root, before):
    if not before or set(before) == {'0'}:
        return None
    # Fail on missing history instead of accidentally treating it as a new version.
    subprocess.run(['git', 'cat-file', '-e', before+'^{commit}'], cwd=root, check=True)
    def show(path):
        result = subprocess.run(['git', 'show', before+':'+path], cwd=root,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return result.stdout.strip() if result.returncode == 0 else None
    version = show('version.txt')
    if version is not None:
        if not re.fullmatch(r'(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)', version):
            raise ValueError('Previous version.txt is invalid')
        return version
    legacy = re.search(r'project\(esp-reset VERSION (\d+\.\d+\.\d+)\)', show('CMakeLists.txt') or '')
    return legacy.group(1) if legacy else None


def plan(root, event_name, ref, event):
    version = read_version(root)
    before = event.get('before') if event_name == 'push' else event.get('pull_request', {}).get('base', {}).get('sha')
    old = previous_version(root, before)
    if old and tuple(map(int, version.split('.'))) < tuple(map(int, old.split('.'))):
        raise ValueError(f'Version cannot decrease: {old} -> {version}')
    publish = event_name == 'push' and ref == 'refs/heads/main' and version != old
    return {'version': version, 'publish': str(publish).lower()}


def pack(bundle, output, board, version):
    if board not in BOARDS:
        raise ValueError('Unknown board')
    if (bundle/'version.txt').read_text().strip() != version:
        raise ValueError('Bundle version does not match release')
    # Never ship environment caches, local credentials, or stale wrapper scripts.
    allowed = {'espreset', 'README.txt', 'SHA256SUMS', 'version.txt', 'firmware', 'tools', 'docs'}
    if {p.name for p in bundle.iterdir()} != allowed:
        raise ValueError('Unexpected bundle entries; package into a clean directory')
    output.mkdir(parents=True, exist_ok=True)
    name = f'esp-reset-{board}-v{version}'
    archive = output/(name+'.zip')
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as stream:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                stream.write(path, str(Path(name)/path.relative_to(bundle)))
    shutil.copy2(bundle/'firmware/esp-reset.bin', output/(name+'-ota.bin'))


def checksums(directory, version):
    expected = {f'esp-reset-{board}-v{version}{suffix}' for board in BOARDS for suffix in ('.zip', '-ota.bin')}
    actual = {p.name for p in directory.iterdir() if p.is_file() and p.name != 'SHA256SUMS'}
    if actual != expected:
        raise ValueError(f'Incomplete/unexpected release assets: expected {sorted(expected)}, got {sorted(actual)}')
    (directory/'SHA256SUMS').write_text(''.join(
        hashlib.sha256((directory/name).read_bytes()).hexdigest()+'  '+name+'\n' for name in sorted(expected)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('plan')
    package = commands.add_parser('pack')
    package.add_argument('bundle', type=Path)
    package.add_argument('output', type=Path)
    package.add_argument('board', choices=BOARDS)
    sums = commands.add_parser('checksums')
    sums.add_argument('directory', type=Path)
    args = parser.parse_args()
    if args.command == 'plan':
        event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
        result = plan(ROOT, os.environ['GITHUB_EVENT_NAME'], os.environ['GITHUB_REF'], event)
        with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
            for key, value in result.items():
                print(f'{key}={value}', file=output)
        print(result)
    elif args.command == 'pack':
        pack(args.bundle, args.output, args.board, read_version())
    else:
        checksums(args.directory, read_version())


if __name__ == '__main__':
    main()
