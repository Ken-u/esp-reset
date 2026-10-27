import contextlib
import http.server
import importlib.util
import io
import json
import os
import subprocess
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('network', Path(__file__).resolve().parents[1]/'tools/network.py')
network = importlib.util.module_from_spec(spec)
spec.loader.exec_module(network)


class NetworkTests(unittest.TestCase):
    def test_lost_post_response_is_recovered_without_second_post(self):
        posts = []
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                value = {'ok': True, 'boot_id': 'boot-one'}
                if self.path.startswith('/api/action?'):
                    value.update(state='released', request_id=posts[0]['request_id'])
                self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
            def do_POST(self):
                self.assert_token = self.headers['X-Reset-Token']
                posts.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.close_connection = True # Action happened; response was lost.
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as directory, patch.object(network, 'CONFIG', Path(directory)/'device.json'), contextlib.redirect_stdout(io.StringIO()):
                result=network.main(['maskrom','--url',f'http://127.0.0.1:{server.server_port}'])
                self.assertEqual(result,0)
                self.assertEqual(len(posts),1)
                self.assertEqual(posts[0]['mode'],'maskrom')
                receipt=json.loads((Path(directory)/'last-action.json').read_text())
                self.assertEqual(receipt['state'],'released')
        finally: server.shutdown();server.server_close()

    def test_old_pending_receipt_never_blocks_new_manual_invocations(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(network,'CONFIG',Path(directory)/'device.json'):
            receipt = Path(directory)/'last-action.json'
            receipt.write_text(json.dumps({'url':'http://esp','state':'pending','request_id':'old','boot_id':'old-boot'}))
            posts=[]
            def request(url, path, payload=None):
                if payload is not None:
                    posts.append(payload)
                    return {'ok':True,'boot_id':'new-boot','state':'queued'}
                return {'ok':True,'boot_id':'new-boot'}
            with patch.object(network,'wait_action',side_effect=TimeoutError('lost receipt')) as wait, patch.object(network,'request',side_effect=request), contextlib.redirect_stdout(io.StringIO()):
                for unused in range(2):
                    with self.assertRaises(TimeoutError):
                        network.run_action('http://esp','reset',3,1500)
                self.assertEqual(len(posts),2)
                self.assertNotEqual(posts[0]['request_id'],posts[1]['request_id'])
                self.assertTrue(all(p['boot_id']=='new-boot' for p in posts))
                self.assertEqual(wait.call_count,2)

    def test_wrapper_selects_http_only_when_ip_is_explicit(self):
        launcher=Path(__file__).resolve().parents[1]/'tools/espreset'
        with tempfile.TemporaryDirectory() as directory:
            uv=Path(directory)/'uv'
            uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            uv.chmod(0o755)
            env={**os.environ, 'PATH':directory+os.pathsep+os.environ['PATH'], 'ESP_RESET_URL':'http://ignored-without-explicit-ip'}
            cases=[([],False),(['--port','/dev/test'],False),(['--ip','192.168.3.6'],True),(['--url','http://192.168.3.6'],True),(['--ip=192.168.3.6'],True),(['--url=http://192.168.3.6'],True)]
            for command in ('reset','loader','maskrom','status'):
                for flags, network_mode in cases:
                    with self.subTest(command=command, flags=flags):
                        output=subprocess.check_output(['sh',str(launcher),command,*flags],env=env,text=True)
                        self.assertIn('/network.py' if network_mode else '/device.py',output)
                        if network_mode:
                            self.assertNotIn('esptool',output)
                            self.assertNotIn('pyserial',output)

    def test_options_before_command_also_select_network(self):
        launcher=Path(__file__).resolve().parents[1]/'tools/espreset'
        with tempfile.TemporaryDirectory() as directory:
            uv=Path(directory)/'uv';uv.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n');uv.chmod(0o755)
            env={**os.environ,'PATH':directory+os.pathsep+os.environ['PATH']}
            output=subprocess.check_output(['sh',str(launcher),'--ip','192.168.3.6','loader'],env=env,text=True)
            self.assertIn('/network.py',output)
            self.assertNotIn('esptool',output)

    def test_ip_alias_is_accepted(self):
        with patch.object(network,'run_action',return_value=0) as run, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(network.main(['reset','--ip','192.168.3.6']),0)
            run.assert_called_once_with('http://192.168.3.6','reset',1,1500)
