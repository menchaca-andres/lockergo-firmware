import unittest

import lib.drivers.debounce as debounce
from lib.drivers.debounce import DebouncedDigitalInput


class FakePin:
    def __init__(self, values):
        self.values = iter(values)

    def value(self):
        return next(self.values)


class DebouncedDigitalInputTests(unittest.TestCase):
    def test_transition_is_accepted_only_after_three_matching_samples(self):
        had_sleep_ms = hasattr(debounce.time, "sleep_ms")
        previous_sleep_ms = getattr(debounce.time, "sleep_ms", None)
        debounce.time.sleep_ms = lambda _duration: None
        try:
            pin = FakePin([1, 0, 1, 0, 0, 0, 0])
            input_filter = DebouncedDigitalInput(pin, {"debounceSamples": 3}, 0)

            self.assertFalse(input_filter.read())
            self.assertTrue(input_filter.read())
        finally:
            if had_sleep_ms:
                debounce.time.sleep_ms = previous_sleep_ms
            else:
                del debounce.time.sleep_ms


if __name__ == "__main__":
    unittest.main()
