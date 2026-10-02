from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from typix_launcher import fan_maintenance as hooks


class UpgradeFanContinuity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.marker = self.root / 'marker.json'
        for target, value in (('RUNTIME', self.root), ('MARKER', self.marker)):
            mock = patch.object(hooks, target, value)
            mock.start()
            self.addCleanup(mock.stop)
        for target in ('secure_directory', 'os.close'):
            mock = patch('typix_launcher.fan_maintenance.' + target)
            mock.start()
            self.addCleanup(mock.stop)
        mock = patch.object(hooks, 'root_lock', return_value=99)
        mock.start()
        self.addCleanup(mock.stop)

    def test_active_upgrade_checkpoints_then_stops_and_resumes(self):
        events = []
        def command(*argv):
            events.append(argv[0])
            return SimpleNamespace(returncode=0)
        def write(*_args):
            events.append('checkpoint')
            self.marker.write_text('pending')
        with patch.object(hooks, 'systemctl', side_effect=command), \
             patch.object(hooks, 'atomic_root_json', side_effect=write), \
             patch.object(hooks, 'pending_restart', side_effect=[False, True]):
            hooks.maintain('pre-upgrade')
            hooks.maintain('post-configure')
        self.assertEqual(events, ['is-active', 'checkpoint', 'stop', 'start'])
        self.assertFalse(self.marker.exists())

    def test_inactive_service_never_starts_or_enables_on_upgrade(self):
        with patch.object(hooks, 'systemctl', return_value=SimpleNamespace(returncode=3)) as command, \
             patch.object(hooks, 'pending_restart', return_value=False), \
             patch.object(hooks, 'atomic_root_json') as write:
            hooks.maintain('pre-upgrade')
            hooks.maintain('post-configure')
        self.assertEqual([call.args for call in command.call_args_list], [('is-active', '--quiet')])
        write.assert_not_called()

    def test_low_storage_preserves_running_service(self):
        with patch.object(hooks, 'systemctl', return_value=SimpleNamespace(returncode=0)) as command, \
             patch.object(hooks, 'pending_restart', return_value=False), \
             patch.object(hooks, 'atomic_root_json', side_effect=OSError('No space left')):
            with self.assertRaises(OSError):
                hooks.maintain('pre-upgrade')
        self.assertEqual([call.args for call in command.call_args_list], [('is-active', '--quiet')])

    def test_failed_restart_preserves_recovery_record(self):
        self.marker.write_text('pending')
        with patch.object(hooks, 'pending_restart', return_value=True), \
             patch.object(hooks, 'systemctl', return_value=SimpleNamespace(returncode=1)):
            with self.assertRaises(hooks.FanError):
                hooks.maintain('post-configure')
        self.assertTrue(self.marker.exists())

    def test_remove_stops_and_disables_without_starting(self):
        with patch.object(hooks, 'systemctl', return_value=SimpleNamespace(returncode=0)) as command, \
             patch.object(hooks, 'pending_restart', return_value=False):
            hooks.maintain('pre-remove')
        self.assertEqual([call.args for call in command.call_args_list], [('stop',), ('disable',)])

    def test_failed_upgrade_then_remove_reinstall_does_not_restart(self):
        self.marker.write_text('pending')
        with patch.object(hooks, 'systemctl', return_value=SimpleNamespace(returncode=0)) as command, \
             patch.object(hooks, 'pending_restart', side_effect=lambda: self.marker.exists()):
            hooks.maintain('pre-remove')
            hooks.maintain('post-configure')
        self.assertEqual([call.args for call in command.call_args_list], [('stop',), ('disable',)])
        self.assertFalse(self.marker.exists())


if __name__ == '__main__':
    unittest.main()
