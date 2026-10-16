import unittest

from lib.reporter import Reporter


class ReporterTests(unittest.TestCase):
    def test_boolean_sends_changes_and_configured_heartbeat(self):
        reporter = Reporter("BOOLEAN", {"heartbeatS": 300})

        initial, changed = reporter.observe(True, "OK", 0)
        self.assertFalse(changed)
        self.assertTrue(initial["value"])
        self.assertIsNone(reporter.observe(True, "OK", 299999)[0])

        heartbeat, changed = reporter.observe(True, "OK", 300000)
        self.assertFalse(changed)
        self.assertTrue(heartbeat["value"])

        transition, changed = reporter.observe(False, "OK", 300010)
        self.assertTrue(changed)
        self.assertFalse(transition["value"])

    def test_numeric_uses_median_deadband_and_settling_time(self):
        reporter = Reporter("NUMBER", {
            "deadband": 2,
            "heartbeatS": 60,
            "settleS": 2,
            "maxDistanceCm": 25
        })

        initial = None
        for tick, value in enumerate([10, 180, 10, 10, 10]):
            initial, _changed = reporter.observe(value, "OK", tick * 1000)
        self.assertEqual(initial["value"], 10)

        reported = None
        for tick in range(5, 12):
            current, _changed = reporter.observe(13, "OK", tick * 1000)
            if current is not None:
                reported = current
                break
        self.assertEqual(reported["value"], 13)

    def test_out_of_range_is_quality_not_a_distance_change(self):
        reporter = Reporter("NUMBER", {"maxDistanceCm": 25})
        for tick in range(5):
            reading, _changed = reporter.observe(10, "OK", tick * 1000)
        self.assertEqual(reading["value"], 10)

        reading = None
        for tick in range(5, 10):
            current, _changed = reporter.observe(164, "OUT_OF_RANGE", tick * 1000)
            if current is not None:
                reading = current
                break
        self.assertEqual(reading["quality"], "OUT_OF_RANGE")
        self.assertEqual(reporter.last_value, 10)

    def test_error_is_reported_and_a_good_sample_resets_quality(self):
        reporter = Reporter("NUMBER", {"heartbeatS": 60})
        error, _changed = reporter.observe(None, "ERROR", 0)
        self.assertEqual(error["quality"], "ERROR")

        reading = None
        for tick in range(1, 6):
            reading, _changed = reporter.observe(10, "OK", tick * 1000)
        self.assertEqual(reading["quality"], "OK")


if __name__ == "__main__":
    unittest.main()
