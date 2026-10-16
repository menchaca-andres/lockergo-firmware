import time


class DebouncedDigitalInput:
    def __init__(self, pin, config, active_level=0):
        self.pin = pin
        self.active_level = active_level
        self.samples = max(3, int(config.get("debounceSamples", 3)))
        self.delay_ms = max(1, int(config.get("debounceDelayMs", 10)))
        self.stable_value = self.pin.value() == self.active_level

    def read(self):
        samples = []
        for index in range(self.samples):
            samples.append(self.pin.value() == self.active_level)
            if index + 1 < self.samples:
                time.sleep_ms(self.delay_ms)
        if all(value is samples[0] for value in samples):
            self.stable_value = samples[0]
        return self.stable_value
