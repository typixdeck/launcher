import subprocess
import unittest
from unittest.mock import patch

from typix_launcher import fan, fan_helper, fan_service


def config(mode='auto', percent=50):
    return {'version': 2, 'mode': mode, 'percent': percent,
            'curve': [[40, 30], [50, 50], [60, 70], [75, 100]]}


def status(value):
    return {'version': 2, 'mode': value['mode'], 'requested_percent': value['percent'],
            'curve': value['curve'], 'percent': 40, 'rpm': 2200, 'temperature': 45,
            'updated': 1000, 'error': '', 'message': ''}


class CurveTests(unittest.TestCase):
    def test_interpolates_and_clamps_at_endpoint_duties(self):
        points = config()['curve']
        self.assertEqual([fan.curve_percent(points, value) for value in (20, 40, 45, 50, 55, 67.5, 75, 90)],
                         [30, 30, 40, 50, 60, 85, 100, 100])

    def test_requires_safe_monotonic_points(self):
        for value in ([[40, 30], [40, 50], [60, 70], [75, 100]],
                      [[40, 40], [50, 30], [60, 70], [75, 100]],
                      [[40, 30], [50, 50], [60, 70], [81, 100]],
                      [[40, 30], [50, 50], [60, 70], [75, 99]],
                      [[40, 30], [50, 50], [60, 70]],
                      [[40, True], [50, 50], [60, 70], [75, 100]]):
            with self.subTest(value=value), self.assertRaises(fan.FanError):
                fan.validate_config({**config(), 'curve': value})

    def test_helper_parses_only_fixed_bounded_curve_arguments(self):
        value = fan_helper.arguments(['auto', '--curve', '40:30,50:50,60:70,75:100'])
        self.assertEqual(value, config())
        value = fan_helper.arguments(['manual', '65', '--curve', '40:30,50:50,60:70,75:100'])
        self.assertEqual(value, config('manual', 65))
        for argv in (['auto', '--curve', '/tmp/config'], ['auto', '--curve', '40:30,50:50,60:70,75:100,80:100'],
                     ['auto', '--curve', '40:30,50:50,60:70,75:100\n'],
                     ['manual', '25', '--curve', '40:30,50:50,60:70,75:100']):
            with self.subTest(argv=argv), self.assertRaises(fan.FanError):
                fan_helper.arguments(argv)

    def test_continuous_curve_downward_hysteresis_and_live_edit(self):
        policy = fan_service.FanPolicy()
        values = [policy.choose(config(), temp, 2400)[0] for temp in (45, 55, 54, 53, 52, 45)]
        self.assertEqual(values, [40, 60, 60, 60, 58, 44])
        changed = {**config(), 'curve': [[40, 50], [50, 60], [60, 80], [75, 100]]}
        self.assertEqual(policy.choose(changed, 45, 2400), (55, ''))

    def test_manual_and_protection_ignore_curve_duty(self):
        policy = fan_service.FanPolicy()
        self.assertEqual(policy.choose(config('manual', 66), 45, 2400), (66, ''))
        for value in (config(), config('manual', 30)):
            for temperature, rpm in ((80, 2400), (None, 2400), (50, None), (50, 0)):
                duty, message = policy.choose(value, temperature, rpm)
                self.assertEqual(duty, 100)
                self.assertTrue(message)

    def test_manual_switch_back_to_curve_resets_hysteresis(self):
        policy = fan_service.FanPolicy()
        policy.choose(config(), 70, 2400)
        policy.choose(config('manual'), 40, 2400)
        self.assertEqual(policy.choose(config(), 40, 2400), (30, ''))

    def test_old_config_remains_accepted_without_rewriting(self):
        old = {'version': 1, 'mode': 'manual', 'percent': 50}
        self.assertIs(fan.validate_config(old), old)
        self.assertEqual(fan.config_curve(old), fan.DEFAULT_CURVE)
        self.assertNotIn('curve', old)

    def test_status_must_confirm_the_actual_saved_curve(self):
        value = config()
        runtime = status(value)
        self.assertEqual(fan.validate_status(runtime), runtime)
        self.assertTrue(fan.status_matches_config(runtime, value))
        runtime = {**runtime, 'curve': [[40, 30], [50, 45], [60, 70], [75, 100]]}
        self.assertFalse(fan.status_matches_config(runtime, value))
        with patch.object(fan, 'discover'), patch.object(fan, 'readings', return_value=(40, 2200, 45)), \
             patch.object(fan, 'read_root_json', side_effect=[value, runtime]), \
             patch.object(fan.time, 'monotonic', return_value=1001):
            state = fan.FanController().state()
        self.assertEqual(state.mode, 'off')
        self.assertIn('正在应用', state.message)

    def test_ui_state_reads_saved_curve_and_actual_measurements(self):
        value = config('manual', 65)
        with patch.object(fan, 'discover'), patch.object(fan, 'readings', return_value=(60, 4200, 47)), \
             patch.object(fan, 'read_root_json', side_effect=[value, status(value)]), \
             patch.object(fan.time, 'monotonic', return_value=1001):
            state = fan.FanController().state()
        self.assertEqual(state.curve, ((40, 30), (50, 50), (60, 70), (75, 100)))
        self.assertEqual((state.mode, state.requested_percent, state.percent, state.rpm), ('manual', 65, 60, 4200))

    def test_client_only_passes_validated_points_to_fixed_helper(self):
        controller = fan.FanController(runner=lambda *args, **kwargs: subprocess.CompletedProcess(args, 0))
        with patch.object(controller, 'state', return_value=fan.FanState(available=True)), \
             patch.object(controller, 'runner', return_value=subprocess.CompletedProcess([], 0)) as runner:
            controller.apply('manual', 65, curve=config()['curve'])
            self.assertEqual(runner.call_args.args[0], ['/usr/bin/pkexec', fan.HELPER, 'manual', '65',
                                                       '--curve', '40:30,50:50,60:70,75:100'])
            runner.reset_mock()
            controller.apply('auto', curve=[[40, 10], [50, 50], [60, 70], [75, 100]])
            runner.assert_not_called()


if __name__ == '__main__':
    unittest.main()
