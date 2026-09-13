import subprocess
import unittest
from typix_launcher.settings import AutostartController, disable_command, enable_command, query_command


class FakeRunner:
    def __init__(self, results):
        self.results = results
        self.commands = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        key = tuple(command[2:])
        if key == ("enable", "typix-launcher.service"):
            self.results[("is-enabled", "typix-launcher.service")] = subprocess.CompletedProcess(
                [], 0, b"enabled\n", b""
            )
        return self.results[key]


class AutostartTests(unittest.TestCase):
    def test_commands_are_user_scoped(self):
        self.assertEqual(query_command(), ["systemctl", "--user", "is-enabled", "typix-launcher.service"])
        self.assertEqual(enable_command(), ["systemctl", "--user", "enable", "typix-launcher.service"])
        self.assertEqual(disable_command(), ["systemctl", "--user", "disable", "typix-launcher.service"])

    def test_state_and_toggle(self):
        runner = FakeRunner(
            {
                ("is-enabled", "typix-launcher.service"): subprocess.CompletedProcess([], 1, b"disabled\n", b"disabled\n"),
                ("enable", "typix-launcher.service"): subprocess.CompletedProcess([], 0, b"Created symlink\n", b""),
            }
        )
        controller = AutostartController(runner)
        self.assertFalse(controller.state().enabled)
        state = controller.set_enabled(True)
        self.assertTrue(state.enabled)
        self.assertIn(["systemctl", "--user", "enable", "typix-launcher.service"], runner.commands)

    def test_unavailable_systemctl(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError("systemctl")

        controller = AutostartController(missing)
        state = controller.state()
        self.assertFalse(state.available)
        self.assertFalse(state.enabled)


if __name__ == "__main__":
    unittest.main()
