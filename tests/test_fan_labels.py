import unittest

from typix_launcher.fan import FanState
from typix_launcher.fan_labels import readings_label


class FanLabelTests(unittest.TestCase):
    def test_missing_readings_do_not_claim_zero(self):
        text = readings_label(FanState())
        self.assertEqual(text, "温度 —    转速 —    输出 —")
        self.assertNotIn("0 RPM", text)

    def test_actual_zero_is_visible(self):
        self.assertEqual(readings_label(FanState(percent=0, rpm=0, temperature=0)),
                         "温度 0.0 °C    转速 0 RPM    输出 0%")

    def test_readback_is_separate_from_manual_request(self):
        text = readings_label(FanState(mode="manual", requested_percent=70,
                                       percent=47, rpm=1800, temperature=52.34))
        self.assertEqual(text, "温度 52.3 °C    转速 1800 RPM    输出 47%")
        self.assertNotIn("70%", text)


if __name__ == "__main__":
    unittest.main()
