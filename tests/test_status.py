import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from typix_launcher import battery, status


class StatusTests(unittest.TestCase):
    def test_real_zero_is_distinct_from_missing_and_mouse(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            self.assertIsNone(status.system_battery(root))
            mouse = root / 'a_mouse'
            mouse.mkdir()
            for k, v in {'type': 'Battery', 'scope': 'Device', 'capacity': '90'}.items():
                (mouse / k).write_text(v)
            self.assertIsNone(status.system_battery(root))
            pack = root / 'battery'
            pack.mkdir()
            for k, v in {'type': 'Battery', 'capacity': '0', 'status': 'Discharging', 'present': '1'}.items():
                (pack / k).write_text(v)
            self.assertEqual(status.system_battery(root).text, '0%')
            (pack / 'capacity').write_text('101')
            self.assertIsNone(status.system_battery(root))
            (pack / 'capacity').write_text('42')
            (pack / 'status').write_text('Charging')
            self.assertIn('charging', status.system_battery(root).icon)
            (pack / 'present').write_text('0')
            self.assertIsNone(status.system_battery(root))

    def test_wifi_off_disconnected_missing_and_signal_boundaries(self):
        self.assertEqual(status.wifi_indicator('ethernet:connected', 'enabled', '*:70').text, '无网卡')
        self.assertEqual(status.wifi_indicator('wifi:unavailable', 'disabled', '').text, '已关闭')
        self.assertEqual(status.wifi_indicator('wifi:disconnected', 'enabled', '*:70').text, '未连接')
        for value, icon in [(0, 'weak'), (24, 'weak'), (25, 'ok'), (50, 'good'), (75, 'excellent'), (100, 'excellent')]:
            result = status.wifi_indicator('wifi:connected', 'enabled', f':99\n*:{value}')
            self.assertEqual(result.text, f'{value}%')
            self.assertIn(icon, result.icon)
        self.assertEqual(status.wifi_indicator('wifi:connected', 'enabled', '*:101').text, '已连接')

    def test_wifi_no_scan_no_ssid_and_error_is_unknown(self):
        commands = []
        def run(command, **kwargs):
            commands.append(command)
            self.assertLessEqual(kwargs['timeout'], 3)
            self.assertEqual(kwargs['env']['LC_ALL'], 'C')
            output = 'wifi:connected' if 'TYPE,STATE' in command else '*:52' if 'IN-USE,SIGNAL' in command else 'enabled'
            return subprocess.CompletedProcess(command, 0, output, '')
        self.assertEqual(status.read_wifi(run).text, '52%')
        self.assertEqual(commands[-1][-2:], ['--rescan', 'no'])
        self.assertNotIn('SSID', ' '.join(' '.join(c) for c in commands))
        self.assertEqual(status.read_wifi(lambda *a, **kw: (_ for _ in ()).throw(FileNotFoundError())), status.WIFI_UNKNOWN)


class BatteryTests(unittest.TestCase):
    def record(self, **changes):
        values = dict.fromkeys(battery.FIELDS, -1)
        values.update(stc_err=259, cw_err=0, cw_mv=4194, cw_soc=100, cw_mode=0)
        values.update(changes)
        return ('TD_BATT v=2 ' + ' '.join(f'{k}={values[k]}' for k in battery.FIELDS)).encode()

    def test_whitelist_rejects_stale_partial_faulty_records(self):
        sample = battery.parse_battery(self.record())
        self.assertEqual(sample, {'percent': 100, 'mv': 4194, 'source': 'CW2015', 'estimated': True})
        for line in [self.record()[:-1], self.record() + b' extra=1', b'raw keyboard data', self.record().replace(b'v=2', b'v=1')]:
            self.assertIsNone(battery.parse_battery(line))
        for changes in [dict(cw_soc=101), dict(cw_soc=-1), dict(cw_mv=1000), dict(cw_mv=99999), dict(cw_err=1), dict(cw_mode=192), dict(cw_mode=-1)]:
            self.assertIsNone(battery.parse_battery(self.record(**changes)))
        self.assertEqual(battery.parse_battery(self.record(cw_soc=0))['percent'], 0)
        self.assertEqual(status.battery_indicator(100, estimated=True).text, '≈100%')

    def test_reader_and_writer_are_mutually_exclusive_and_release_on_failure(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'lock'
            with self.assertRaises(OSError):
                with battery.shared_writer_lock(path):
                    self.fail('missing lock must not be created')
            path.touch(mode=0o644)
            real_stat = os.fstat
            def root_owned(fd):
                fields = list(real_stat(fd))
                fields[4] = 0  # Simulate installer ownership; retain real file type/mode.
                return os.stat_result(fields)
            with patch.object(battery.os, 'fstat', root_owned):
                with path.open('rb') as writer:
                    fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    with self.assertRaises(BlockingIOError):
                        with battery.shared_writer_lock(path):
                            self.fail('cannot query during a write')
                    fcntl.flock(writer, fcntl.LOCK_UN)
                    with self.assertRaises(RuntimeError):
                        with battery.shared_writer_lock(path):
                            with self.assertRaises(BlockingIOError):
                                fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                            raise RuntimeError('disconnect')
                    fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                path.chmod(0o666)
                with self.assertRaises(ValueError):
                    with battery.shared_writer_lock(path):
                        self.fail('world-writable lock rejected')

    def test_unavailable_never_emits_raw_error(self):
        from io import StringIO
        with patch.object(battery, 'query_battery', side_effect=RuntimeError('sensitive raw data')):
            with patch('sys.stdout', new_callable=StringIO) as output:
                battery.main()
            self.assertIsNone(json.loads(output.getvalue()))


if __name__ == '__main__':
    unittest.main()
