"""fetserver/wifilink.py: an ESP32 board's WiFi link history from its heartbeat, checked and named."""
import unittest

from fetserver import wifilink as W


class Link(unittest.TestCase):
    def test_only_well_formed_values_are_kept(self):
        self.assertIsNone(W.clean("drops=3"))
        self.assertIsNone(W.clean({"drops": "3", "bssid": "AA:BB"}))
        self.assertEqual(W.clean({"drops": 3, "reason": 200, "reset": True, "down_ms": -1, "bssid": "aa:bb:cc:dd:ee:ff", "x": 1}),
                         {"drops": 3, "reason": 200, "bssid": "aa:bb:cc:dd:ee:ff"}, "a bool, a negative number and unknown keys go")
        self.assertNotIn("bssid", W.clean({"drops": 0, "bssid": "aa:bb:cc:dd:ee:f"}))

    def test_named(self):
        v = W.view({"reset": 1, "drops": 0, "reason": 0})
        self.assertEqual((v["reset_name"], v["reset_trouble"]), ("power-on", False))
        self.assertNotIn("reason_name", v, "no drop, no reason")
        v = W.view({"reset": 6, "drops": 2, "reason": 3, "last_drop_ms": 10000}, uptime_ms=70000)
        self.assertEqual((v["reset_name"], v["reset_trouble"], v["reason_name"], v["last_drop_ago_s"]),
                         ("the task watchdog", True, "the router ended it (deauthenticated)", 60))
        self.assertEqual(W.view({"reset": 99, "drops": 1, "reason": 77})["reason_name"], "code 77")
        self.assertIsNone(W.view(None))

    def test_the_codes_are_esp_idfs(self):
        """Spot checks against esp_wifi_types_generic.h and esp_system.h (Arduino-ESP32 3.3.12)."""
        self.assertTrue(W.REASONS[200].startswith("beacon timeout") and W.REASONS[15].startswith("4-way handshake"))
        self.assertTrue(W.REASONS[8].startswith("the target left") and W.REASONS[2] == "authentication expired")
        self.assertEqual((W.RESETS[1], W.RESETS[3], W.RESETS[9][:11]), ("power-on", "a software restart", "a brownout "))


if __name__ == "__main__":
    unittest.main()
