import contextlib
import http.server
import importlib.util
import io
import json
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

    def test_pending_request_is_queried_instead_of_new_action(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(network,'CONFIG',Path(directory)/'device.json'):
            (Path(directory)/'last-action.json').write_text(json.dumps({'url':'http://esp','state':'pending','request_id':'old','boot_id':'boot-one'}))
            with patch.object(network,'wait_action',return_value=0) as wait, patch.object(network,'request') as request, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(network.run_action('http://esp','reset',3,1500),0)
                wait.assert_called_once_with('http://esp','old','boot-one')
                request.assert_not_called()

    def test_network_wrapper_does_not_require_serial_or_esptool(self):
        source=(Path(__file__).resolve().parents[1]/'tools/run.sh').read_text()
        branch=source.split('reset|loader|maskrom|status|result)')[1].split(';;')[0]
        self.assertIn('network_script',branch)
        self.assertNotIn('esptool',branch)
        self.assertNotIn('pyserial',branch)
