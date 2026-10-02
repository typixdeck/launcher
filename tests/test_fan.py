"""Offline sysfs fixtures: no host hardware, services, or authorization."""
from __future__ import annotations

from dataclasses import replace
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from typix_launcher import fan, fan_helper, fan_service


class SysFixture:
    def __init__(self, root: Path, number: int = 23, hwmon_index: int = 4):
        self.root = root / 'sys'
        self.node = self.root / 'firmware/devicetree/base/soc/i2c@7e205000'
        self.node.mkdir(parents=True)
        (self.node / 'compatible').write_bytes(b'brcm,bcm2835-i2c\0')
        child = self.node / 'fan@2f'
        child.mkdir()
        (child / 'compatible').write_bytes(b'microchip,emc2301\0')
        self.adapter = self.root / f'devices/platform/soc/fe205000.i2c/i2c-{number}'
        self.adapter.mkdir(parents=True)
        (self.adapter / 'of_node').symlink_to(self.node)
        self.client = self.adapter / f'{number}-002f'
        self.client.mkdir()
        driver = self.root / 'bus/i2c/drivers/emc2305'
        driver.mkdir(parents=True)
        (self.client / 'driver').symlink_to(driver)
        (self.client / 'of_node').symlink_to(child)
        self.hwmon = self.client / f'hwmon/hwmon{hwmon_index}'
        self.hwmon.mkdir(parents=True)
        (self.hwmon / 'device').symlink_to(self.client)
        for name, value in {'name': 'emc2305', 'pwm1': '255', 'fan1_input': '2300', 'fan1_fault': '0'}.items():
            (self.hwmon / name).write_text(value + '\n')
        directory = self.root / 'class/hwmon'
        directory.mkdir(parents=True)
        (directory / f'hwmon{hwmon_index}').symlink_to(self.hwmon)
        self.thermal = self.root / 'class/thermal'
        self.zone = self.thermal / 'thermal_zone6'
        self.zone.mkdir(parents=True)
        (self.zone / 'type').write_text('cpu-thermal\n')
        (self.zone / 'temp').write_text('50000\n')

    def bind_kernel(self):
        cooler = self.thermal / 'cooling_device7'
        cooler.mkdir()
        (cooler / 'type').write_text('emc2305_fan\n')
        (self.zone / 'cdev0').symlink_to(cooler)


class FanDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.fixture = SysFixture(Path(self.directory.name))

    def tearDown(self):
        self.directory.cleanup()

    def test_runtime_indices_and_real_readings(self):
        device = fan.discover(self.fixture.root)
        self.assertEqual(device.client.name, '23-002f')
        self.assertEqual(fan.readings(device, self.fixture.root), (100, 2300, 50.0))

    def test_renumbered_indices_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            fixture = SysFixture(Path(directory), 8, 71)
            self.assertEqual(fan.discover(fixture.root).client.name, '8-002f')

    def test_wrong_controller_and_missing_of_rejected(self):
        (self.fixture.adapter / 'of_node').unlink()
        with self.assertRaises(OSError):
            fan.discover(self.fixture.root)
        (self.fixture.adapter / 'of_node').symlink_to(self.fixture.node.parent)
        with self.assertRaises(fan.FanError):
            fan.discover(self.fixture.root)

    def test_wrong_compatible_and_multiple_channels_rejected(self):
        (self.fixture.node / 'fan@2f/compatible').write_bytes(b'microchip,emc2305\0')
        with self.assertRaises(fan.FanError):
            fan.discover(self.fixture.root)
        (self.fixture.node / 'fan@2f/compatible').write_bytes(b'microchip,emc2301\0')
        (self.fixture.hwmon / 'pwm2').write_text('255')
        with self.assertRaises(fan.FanError):
            fan.discover(self.fixture.root)

    def test_missing_dt_client_rejected(self):
        (self.fixture.client / 'of_node').unlink()
        with self.assertRaises(OSError):
            fan.discover(self.fixture.root)

    def test_bound_thermal_refuses_competing_writer(self):
        self.fixture.bind_kernel()
        with self.assertRaisesRegex(fan.FanError, '内核温控'):
            fan.discover(self.fixture.root)
        self.assertEqual(fan.discover(self.fixture.root, allow_thermal=True).client, self.fixture.client.resolve())

    def test_kernel_owned_fan_keeps_read_only_rpm_and_disables_apply(self):
        self.fixture.bind_kernel()
        discover, readings = fan.discover, fan.readings
        runner = unittest.mock.Mock()
        with patch.object(fan, 'discover', side_effect=lambda **kwargs: discover(self.fixture.root, **kwargs)), \
             patch.object(fan, 'readings', side_effect=lambda device: readings(device, self.fixture.root)):
            controller = fan.FanController(runner)
            current = controller.state()
            self.assertFalse(current.available)
            self.assertEqual((current.percent, current.rpm, current.temperature), (100, 2300, 50.0))
            self.assertIn('内核温控', current.message)
            controller.apply('manual', 50)
        runner.assert_not_called()

    def test_unbound_cooling_device_allowed(self):
        cooler = self.fixture.thermal / 'cooling_device7'
        cooler.mkdir()
        (cooler / 'type').write_text('emc2305_fan')
        self.assertEqual(fan.discover(self.fixture.root).client, self.fixture.client.resolve())

    def test_duplicate_controller_rejected(self):
        (self.fixture.root / 'class/hwmon/hwmon92').symlink_to(self.fixture.hwmon)
        with self.assertRaisesRegex(fan.FanError, '多个'):
            fan.discover(self.fixture.root)

    def test_missing_feedback_is_none_not_zero(self):
        device = fan.discover(self.fixture.root)
        (self.fixture.hwmon / 'fan1_input').unlink()
        self.assertEqual(fan.readings(device, self.fixture.root)[1], None)
        (self.fixture.hwmon / 'fan1_input').write_text('0')
        self.assertEqual(fan.readings(device, self.fixture.root)[1], 0)

    def test_invalid_and_ambiguous_temperature_is_unavailable(self):
        device = fan.discover(self.fixture.root)
        (self.fixture.zone / 'temp').write_text('180000')
        self.assertIsNone(fan.readings(device, self.fixture.root)[2])
        (self.fixture.zone / 'temp').write_text('50000')
        another = self.fixture.thermal / 'thermal_zone9'
        another.mkdir()
        (another / 'type').write_text('cpu-thermal')
        (another / 'temp').write_text('51000')
        self.assertIsNone(fan.readings(device, self.fixture.root)[2])

    def test_failsafe_revalidates_previous_target_despite_ambiguity(self):
        device = fan.discover(self.fixture.root)
        (self.fixture.root / 'class/hwmon/hwmon92').symlink_to(self.fixture.hwmon)
        with patch.object(fan_service, 'write_percent') as writer:
            fan_service.failsafe(self.fixture.root, device)
        writer.assert_called_once_with(device, 100, self.fixture.root)
        (self.fixture.node / 'fan@2f/compatible').write_bytes(b'microchip,emc2305\0')
        with patch.object(fan_service, 'write_percent') as writer:
            fan_service.failsafe(self.fixture.root, device)
        writer.assert_not_called()

    def test_pwm_readback_bounds_and_no_symlink_writes(self):
        device = fan.discover(self.fixture.root)
        owner = SimpleNamespace(st_mode=stat.S_IFREG | 0o644, st_uid=0)
        for request, actual in ((30, 20), (100, 90)):
            with patch.object(fan_service.os, 'fstat', return_value=owner), \
                 patch.object(fan_service, 'readings', return_value=(actual, 2000, 50)):
                with self.assertRaises(fan.FanError):
                    fan_service.write_percent(device, request, self.fixture.root)
        with patch.object(fan_service.os, 'fstat', return_value=owner), \
             patch.object(fan_service, 'readings', return_value=(40, 2000, 50)):
            self.assertEqual(fan_service.write_percent(device, 45, self.fixture.root), 40)
        destination = self.fixture.hwmon / 'innocent-file'
        destination.write_text('untouched')
        (self.fixture.hwmon / 'pwm1').unlink()
        (self.fixture.hwmon / 'pwm1').symlink_to(destination)
        with self.assertRaises(fan.FanError):
            fan_service.write_percent(device, 100, self.fixture.root)
        self.assertEqual(destination.read_text(), 'untouched')


class FanPolicyTests(unittest.TestCase):
    def config(self, mode='auto', percent=50):
        return {'version': 1, 'mode': mode, 'percent': percent}

    def test_curve_and_hysteresis(self):
        policy = fan_service.FanPolicy()
        results = [policy.choose(self.config(), value, 2000)[0] for value in (45, 55, 65, 75, 74, 73, 72.9, 64, 62.9, 52.9)]
        self.assertEqual(results, [30, 45, 65, 100, 100, 100, 65, 65, 45, 30])

    def test_manual_overheat_and_sensor_failures(self):
        policy = fan_service.FanPolicy()
        self.assertEqual(policy.choose(self.config('manual', 40), 55, 2000), (40, ''))
        for temperature, rpm in ((80, 2000), (None, 2000), (50, None), (50, 0)):
            output, message = policy.choose(self.config('manual', 30), temperature, rpm)
            self.assertEqual(output, 100)
            self.assertTrue(message)

    def test_zero_tach_starts_full_and_recovers(self):
        policy = fan_service.FanPolicy()
        for _ in range(3):
            self.assertEqual(policy.choose(self.config(), 40, 0)[0], 100)
        self.assertIn('持续', policy.choose(self.config(), 40, 0)[1])
        self.assertEqual(policy.choose(self.config(), 40, 2000), (30, ''))

    def test_strict_parameters_and_config(self):
        self.assertEqual(fan_helper.arguments(['manual', '30']), self.config('manual', 30))
        for value in (['manual', '0'], ['manual', '101'], ['manual', '30.0'], ['auto', '/etc/passwd'], ['manual', '30', 'other'], ['--config', 'x']):
            with self.assertRaises(fan.FanError):
                fan_helper.arguments(value)
        for value in (self.config('manual', True), self.config([], 30), {**self.config(), 'path': '/sys/x'}):
            with self.assertRaises(fan.FanError):
                fan.validate_config(value)


class FanAuthorizationTests(unittest.TestCase):
    def test_construction_does_not_probe(self):
        with patch.object(fan, 'discover') as probe:
            fan.FanController()
        probe.assert_not_called()

    def test_fixed_helper_and_cancel_preserve_real_state(self):
        current = fan.FanState(True, 'manual', requested_percent=60, percent=59, rpm=2000, temperature=50)
        runner = unittest.mock.Mock(return_value=subprocess.CompletedProcess([], 126, b'', b''))
        controller = fan.FanController(runner)
        with patch.object(controller, 'state', return_value=current):
            result = controller.apply('manual', 40)
        self.assertEqual(runner.call_args.args[0], ['/usr/bin/pkexec', '/usr/libexec/typix-launcher-fan', 'manual', '40'])
        self.assertEqual(result.error, 'cancelled')
        self.assertEqual(result.mode, 'manual')
        self.assertEqual(result.requested_percent, 60)
        self.assertTrue(result.available)

    def test_invalid_or_unavailable_never_runs_helper(self):
        runner = unittest.mock.Mock()
        controller = fan.FanController(runner)
        with patch.object(controller, 'state', return_value=fan.FanState()):
            controller.apply('manual', 0)
            controller.apply('auto')
        runner.assert_not_called()

    def test_systemctl_environment_and_unit_are_fixed(self):
        with patch.object(fan_helper.subprocess, 'run') as runner:
            fan_helper.systemctl('enable', '--now')
        self.assertEqual(runner.call_args.args[0], ['/usr/bin/systemctl', 'enable', '--now', 'typix-launcher-fan.service'])
        self.assertNotIn('PYTHONPATH', runner.call_args.kwargs['env'])

    def test_failure_restores_prior_preference_and_full_speed(self):
        previous = {'version': 1, 'mode': 'manual', 'percent': 70}
        pending = {'version': 1, 'mode': 'manual', 'percent': 40}
        failure = subprocess.CompletedProcess([], 1, b'', b'')
        with patch.object(fan_helper, 'discover', return_value='device'), \
             patch.object(fan_helper, 'secure_directory'), \
             patch.object(fan_helper, 'root_lock', return_value=97), \
             patch.object(fan_helper.os, 'close'), \
             patch.object(fan_helper, 'read_root_json', return_value=previous), \
             patch.object(fan_helper, 'atomic_root_json') as writer, \
             patch.object(fan_helper, 'systemctl', side_effect=[failure, failure, failure,
                         subprocess.CompletedProcess([], 0), subprocess.CompletedProcess([], 0)]) as command, \
             patch.object(fan_helper, 'failsafe') as full:
            with self.assertRaises(fan.FanError):
                fan_helper.apply_config(pending)
        self.assertEqual([item.args[1] for item in writer.call_args_list], [pending, previous])
        self.assertEqual([item.args for item in command.call_args_list][-2:], [('stop',), ('disable',)])
        full.assert_called_once_with(previous='device')

    def test_failed_restore_stops_and_disables_even_former_active_service(self):
        old = {'version': 1, 'mode': 'manual', 'percent': 70}
        with patch.object(fan_helper, 'atomic_root_json', side_effect=OSError('ENOSPC')), \
             patch.object(fan_helper, 'systemctl', return_value=subprocess.CompletedProcess([], 0)) as command, \
             patch.object(fan_helper, 'failsafe') as full, \
             patch.object(fan_helper.syslog, 'syslog') as audit:
            with self.assertRaises(fan_helper.RecoveryError):
                fan_helper.rollback(old, True, True, True, True, 'device')
        self.assertEqual([item.args for item in command.call_args_list], [('stop',), ('disable',)])
        full.assert_called_once_with(previous='device')
        self.assertIn('administrator recovery required', audit.call_args.args[1])

    def test_stop_failure_does_not_skip_disable_or_full_speed(self):
        old = {'version': 1, 'mode': 'manual', 'percent': 70}
        with patch.object(fan_helper, 'atomic_root_json'), \
             patch.object(fan_helper, 'read_root_json', return_value=old), \
             patch.object(fan_helper, 'systemctl', side_effect=[OSError('stop failed'), subprocess.CompletedProcess([], 0)]) as command, \
             patch.object(fan_helper, 'failsafe') as full:
            with self.assertRaises(fan_helper.RecoveryError):
                fan_helper.rollback(old, False, False, True, True, 'device')
        self.assertEqual([item.args for item in command.call_args_list], [('stop',), ('disable',)])
        full.assert_called_once_with(previous='device')

    def test_valid_rollback_preserves_prior_active_and_enabled_service(self):
        old = {'version': 1, 'mode': 'manual', 'percent': 70}
        with patch.object(fan_helper, 'atomic_root_json'), \
             patch.object(fan_helper, 'read_root_json', return_value=old), \
             patch.object(fan_helper, 'systemctl') as command, \
             patch.object(fan_helper, 'failsafe') as full:
            fan_helper.rollback(old, True, True, True, True, 'device')
        command.assert_not_called()
        full.assert_called_once_with(previous='device')

    def test_recovery_error_has_distinct_admin_required_exit(self):
        with patch.object(fan_helper.sys, 'argv', ['helper', 'manual', '40']), \
             patch.object(fan_helper.os, 'geteuid', return_value=0), \
             patch.object(fan_helper, 'apply_config', side_effect=fan_helper.RecoveryError('需要管理员恢复')), \
             patch.object(fan_helper, 'print') as output:
            self.assertEqual(fan_helper.main(), 70)
        self.assertIn('需要管理员恢复', output.call_args.args[0])

    def test_untrusted_directory_and_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'target'
            target.mkdir()
            link = Path(directory) / 'link'
            link.symlink_to(target)
            with self.assertRaises(fan.FanError):
                fan.secure_directory(link)
            target.chmod(0o777)
            with self.assertRaises(fan.FanError):
                fan.secure_directory(target)

    def test_atomic_writer_failure_preserves_previous_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fan.json'
            target.write_text('{"version":1,"mode":"auto","percent":50}\n')
            original = target.read_bytes()
            with patch.object(fan_service, 'secure_directory'), \
                 patch.object(fan_service, 'read_root_json', return_value={}), \
                 patch.object(fan_service.os, 'replace', side_effect=OSError('disk full')):
                with self.assertRaises(OSError):
                    fan_service.atomic_root_json(target, {'version': 1, 'mode': 'manual', 'percent': 40})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob('.fan-*')), [])

    def test_state_reads_actual_pwm_not_requested_or_saved_pwm(self):
        config = {'version': 1, 'mode': 'manual', 'percent': 45}
        runtime = {'version': 1, 'mode': 'manual', 'requested_percent': 45, 'percent': 45, 'rpm': 1,
                   'temperature': 99, 'updated': 1000, 'error': '', 'message': ''}
        with patch.object(fan, 'discover', return_value='device'), \
             patch.object(fan, 'readings', return_value=(40, 2200, 55)), \
             patch.object(fan, 'read_root_json', side_effect=[config, runtime]), \
             patch.object(fan.time, 'monotonic', return_value=1002):
            state = fan.FanController().state()
        self.assertEqual((state.percent, state.rpm, state.temperature), (40, 2200, 55))
        self.assertEqual(state.requested_percent, 45)

    def test_stale_status_does_not_claim_safety_or_active_control(self):
        config = {'version': 1, 'mode': 'manual', 'percent': 30}
        runtime = {'version': 1, 'mode': 'manual', 'requested_percent': 30, 'percent': 30, 'rpm': 1,
                   'temperature': 50, 'updated': 1000, 'error': '', 'message': ''}
        with patch.object(fan, 'discover', return_value='device'), \
             patch.object(fan, 'readings', return_value=(30, 2200, 55)), \
             patch.object(fan, 'read_root_json', side_effect=[config, runtime]), \
             patch.object(fan.time, 'monotonic', return_value=1100):
            state = fan.FanController().state()
        self.assertEqual(state.mode, 'off')
        self.assertNotIn('安全', state.message)

    def test_service_failure_still_recovers_saved_target(self):
        device = SimpleNamespace(client='verified-client')
        with patch.object(fan_service.os, 'geteuid', return_value=0), \
             patch.object(fan_service, 'secure_directory'), \
             patch.object(fan_service, 'root_lock', return_value=97), \
             patch.object(fan_service.os, 'close'), \
             patch.object(fan_service.signal, 'signal'), \
             patch.object(fan_service, 'discover', return_value=device), \
             patch.object(fan_service, 'write_percent') as writer, \
             patch.object(fan_service.time, 'sleep'), \
             patch.object(fan_service, 'read_root_json', return_value={'bad': 'configuration'}), \
             patch.object(fan_service, 'failsafe') as full:
            with self.assertRaises(fan.FanError):
                fan_service.run()
        writer.assert_called_once_with(device, 100)
        full.assert_called_once_with(previous=device)

    def test_short_write_and_enospc_preserve_existing_config(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fan.json'
            target.write_text('{"version":1,"mode":"auto","percent":50}\n')
            original = target.read_bytes()
            real_write = os.write
            calls = 0

            def partial_then_full_disk(descriptor, payload):
                nonlocal calls
                calls += 1
                if calls == 1:
                    return real_write(descriptor, payload[:3])
                raise OSError('ENOSPC')

            with patch.object(fan_service, 'secure_directory'), \
                 patch.object(fan_service, 'read_root_json', return_value={}), \
                 patch.object(fan_service.os, 'write', side_effect=partial_then_full_disk):
                with self.assertRaises(OSError):
                    fan_service.atomic_root_json(target, {'version': 1, 'mode': 'manual', 'percent': 40})
            self.assertEqual(target.read_bytes(), original)
            self.assertEqual(list(Path(directory).glob('.fan-*')), [])

    def test_invalid_root_status_is_rejected(self):
        value = {'version': 1, 'mode': 'manual', 'requested_percent': 50, 'percent': 50, 'rpm': None,
                 'temperature': 50, 'updated': 1000, 'error': '', 'message': ''}
        self.assertEqual(fan.validate_status(value), value)
        for field, invalid in (('mode', []), ('updated', float('nan')), ('rpm', -1), ('message', {}), ('percent', True)):
            with self.assertRaises(fan.FanError):
                fan.validate_status({**value, field: invalid})

    def test_root_file_ownership_and_write_permissions_required(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'fan.json'
            target.write_text('{}')
            for uid, mode in ((1000, stat.S_IFREG | 0o644), (0, stat.S_IFREG | 0o664)):
                item = SimpleNamespace(st_mode=mode, st_uid=uid, st_size=2)
                with patch.object(fan, 'secure_directory'), patch.object(fan.os, 'fstat', return_value=item):
                    with self.assertRaises(fan.FanError):
                        fan.read_root_json(target)
            link = Path(directory) / 'symlink.json'
            link.symlink_to(target)
            with patch.object(fan, 'secure_directory'):
                with self.assertRaises(OSError):
                    fan.read_root_json(link)

    def test_post_rename_failure_still_attempts_rollback(self):
        old = {'version': 1, 'mode': 'manual', 'percent': 70}
        with patch.object(fan_helper, 'discover', return_value='device'), \
             patch.object(fan_helper, 'secure_directory'), \
             patch.object(fan_helper, 'root_lock', return_value=97), \
             patch.object(fan_helper.os, 'close'), \
             patch.object(fan_helper, 'read_root_json', return_value=old), \
             patch.object(fan_helper, 'atomic_root_json', side_effect=[OSError('directory fsync'), None]) as writer, \
             patch.object(fan_helper, 'systemctl', return_value=subprocess.CompletedProcess([], 0, b'', b'')), \
             patch.object(fan_helper, 'failsafe') as full:
            with self.assertRaises(OSError):
                fan_helper.apply_config({'version': 1, 'mode': 'manual', 'percent': 40})
        self.assertEqual(writer.call_args_list[-1].args[1], old)
        full.assert_called_once_with(previous='device')


if __name__ == '__main__':
    unittest.main()
