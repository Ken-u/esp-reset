import contextlib
import hashlib
import http.server
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'tools'/(name+'.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cli, packager, flasher = (load(name) for name in ('esp_reset', 'package_firmware', 'flash'))


class ToolTests(unittest.TestCase):
    def test_firmware_bundle_preserves_flash_offsets_and_hashes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            build, output = root/'build', root/'bundle'
            files = {'0x0': 'bootloader/bootloader.bin', '0x8000': 'partition_table/partition-table.bin',
                     '0xf000': 'ota_data_initial.bin', '0x20000': 'esp-reset.bin'}
            for name in files.values():
                path = build/name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b'app-image-'+name.encode())
            config = {'flash_files': files, 'extra_esptool_args': {'chip': 'esp32s3'},
                      'flash_settings': {'flash_mode': 'dio', 'flash_freq': '40m', 'flash_size': '16MB'}}
            (build/'flasher_args.json').write_text(json.dumps(config))
            packager.package(build, output)
            command = flasher.flash_command(output/'firmware', '/dev/test', 460800)
            self.assertIn('esp32s3', command)
            self.assertIn('16MB', command)
            for offset, name in files.items():
                self.assertEqual(command[command.index(offset)+1], str((output/'firmware'/name).resolve()))
            for line in (output/'SHA256SUMS').read_text().splitlines():
                digest, name = line.split('  ', 1)
                self.assertEqual(digest, hashlib.sha256((output/name).read_bytes()).hexdigest())

    def test_invalid_bundle_cannot_escape_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'flasher_args.json').write_text(json.dumps({
                'flash_files': {'0x0': '../outside.bin'}, 'extra_esptool_args': {'chip': 'esp32s3'},
                'flash_settings': {'flash_mode': 'dio', 'flash_freq': '40m', 'flash_size': '16MB'}}))
            with self.assertRaises(ValueError):
                flasher.flash_command(root, '/dev/test', 460800)

    def test_cli_requires_an_explicit_valid_channel(self):
        from unittest.mock import patch
        for flags in ([], ['--channel', '0'], ['--channel', '4']):
            with self.subTest(flags=flags), contextlib.redirect_stderr(io.StringIO()):
                with patch.object(cli, 'request') as request, self.assertRaises(SystemExit) as error:
                    cli.main(['--url', 'http://127.0.0.1', '--token', 'test', 'reset', *flags])
                self.assertEqual(error.exception.code, 2)
                request.assert_not_called()

    def test_cli_authentication_reset_body_and_no_retry(self):
        calls = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                data = self.rfile.read(int(self.headers['Content-Length']))
                calls.append((self.path, self.headers['X-Reset-Token'], json.loads(data)))
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.end_headers()
                self.wfile.write(b'{"ok":false,"error":"cooling down"}')

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with contextlib.redirect_stderr(io.StringIO()):
                result = cli.main(['--url', f'http://127.0.0.1:{server.server_port}',
                                   '--token', 'example-token', 'reset', '--channel', '3'])
            self.assertEqual(result, 1)
            self.assertEqual(len(calls), 1)
            path, token, body = calls[0]
            self.assertEqual(path, '/api/reset')
            self.assertEqual(token, 'example-token')
            self.assertEqual(len(body['request_id']), 32)
            self.assertEqual(body['channel'], 3)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
