import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from typix_launcher.runtime import AppJobs, RuntimePreferences, detected_mode


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_memory_detection_and_unavailable_fallback(self):
        mem = self.root / "meminfo"
        self.assertEqual(detected_mode(mem), "single")
        for data, expected in (("MemTotal: 512000 kB", "single"),
                               ("MemTotal: 1995172 kB", "resident"),
                               ("MemTotal: 7995172 kB", "resident"),
                               ("MemTotal: invalid", "single"), ("broken", "single")):
            mem.write_text(data)
            self.assertEqual(detected_mode(mem), expected)

    def test_preferences_survive_restart_and_explicit_choice_overrides_probe(self):
        path = self.root / "config/runtime.json"
        preference = RuntimePreferences(path)
        self.assertEqual(preference.read(), "auto")
        with patch("typix_launcher.runtime.detected_mode", return_value="resident"):
            self.assertEqual(preference.effective(), "resident")
            preference.save("single")
            self.assertEqual(RuntimePreferences(path).effective(), "single")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        path.write_text("not json")
        self.assertEqual(preference.read(), "auto")
        path.write_text(json.dumps({"mode": "bad"}))
        self.assertEqual(preference.read(), "auto")

    def test_failed_save_retains_prior_preference_and_cleans_temporary(self):
        preference = RuntimePreferences(self.root / "runtime.json")
        preference.save("single")
        with patch("typix_launcher.runtime.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                preference.save("resident")
        self.assertEqual(preference.read(), "single")
        self.assertEqual(list(self.root.iterdir()), [preference.path])
        with self.assertRaises(ValueError):
            preference.save("unknown")

    def test_duplicate_launch_is_suppressed_until_app_finishes(self):
        release, started, finished = threading.Event(), threading.Event(), threading.Event()
        calls = []
        errors = []

        def run(path):
            calls.append(path)
            started.set()
            release.wait(3)
            return 0

        def done(error):
            errors.append(error)
            finished.set()

        jobs = AppJobs(run)
        path = self.root / "app.desktop"
        try:
            self.assertTrue(jobs.start(path, done))
            self.assertTrue(started.wait(1))
            self.assertFalse(jobs.start(path, done))
            self.assertEqual(jobs.count(), 1)
            self.assertTrue(jobs.contains(path))
        finally:
            release.set()
        self.assertTrue(finished.wait(2))
        self.assertEqual(jobs.count(), 0)
        self.assertEqual(calls, [path.resolve()])
        self.assertEqual(errors, [""])

    def test_launch_failure_releases_slot_and_reports_error(self):
        finished = threading.Event()
        errors = []

        def fail(_path):
            raise OSError("missing executable")

        jobs = AppJobs(fail)
        jobs.start(self.root / "bad.desktop", lambda error: (errors.append(error), finished.set()))
        self.assertTrue(finished.wait(2))
        self.assertEqual(jobs.count(), 0)
        self.assertIn("missing executable", errors[0])


if __name__ == "__main__":
    unittest.main()
