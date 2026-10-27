import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('device', Path(__file__).resolve().parents[1]/'tools/device.py')
device = importlib.util.module_from_spec(spec)
spec.loader.exec_module(device)


class DeviceTests(unittest.TestCase):
    def test_protocol_ignores_logs_and_partial_lines(self):
        class Connection:
            def __init__(self):
                self.chunks = []
                self.writes = []
            def write(self, data):
                self.writes.append(data)
                req = json.loads(data)
                reply = (device.PREFIX + json.dumps({'id': req['id'], 'ok': True, 'ip': '192.168.1.2'}) + '\n').encode()
                self.chunks = [b'boot log\n', b'@ESP_RESET@{"id":"old","ok":true}\n', reply[:17], reply[17:]]
            def read_until(self, _):
                return self.chunks.pop(0)
        usb = device.USB()
        usb.connection = Connection()
        reply = usb.request('reset', channel=2)
        self.assertEqual(reply['ip'], '192.168.1.2')
        self.assertEqual(len(usb.connection.writes), 1)
        self.assertEqual(json.loads(usb.connection.writes[0])['channel'], 2)

    def test_timeout_never_replays_reset(self):
        usb = device.USB()
        usb.connection = SimpleNamespace(write=lambda data: writes.append(data))
        writes = []
        with patch.object(device.time, 'monotonic', side_effect=[0, 20]):
            with self.assertRaises(TimeoutError):
                usb.request('maskrom', hold_ms=1500)
        self.assertEqual(len(writes), 1)

    def test_reenumeration_keeps_same_device(self):
        usb = device.USB('/dev/old')
        usb.identity = (0x303A, 0x1001, 'same-unit', 'usb-1')
        same = SimpleNamespace(device='/dev/new', vid=0x303A, pid=0x1001,
                               serial_number='same-unit', location='usb-1')
        other = SimpleNamespace(device='/dev/old', vid=0x303A, pid=0x1001,
                                serial_number='different-unit', location='usb-2')
        with patch.object(device.list_ports, 'comports', return_value=[other, same]):
            self.assertIs(usb.candidates(), same)
        with patch.object(device.list_ports, 'comports', return_value=[other]):
            self.assertIsNone(usb.candidates())

    def test_multiple_controllers_require_selection(self):
        ports = [SimpleNamespace(device=path, vid=0x303A) for path in ('/dev/one', '/dev/two')]
        with patch.object(device.list_ports, 'comports', return_value=ports):
            with self.assertRaises(ValueError):
                device.USB().candidates()


if __name__ == '__main__':
    unittest.main()
