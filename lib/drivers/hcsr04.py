from machine import Pin, time_pulse_us
import time


class HCSR04:
    def __init__(self, config):
        self.config = config
        self.trigger = Pin(config["triggerPin"], Pin.OUT)
        self.echo = Pin(config["echoPin"], Pin.IN)
        self.timeout_us = config.get("timeoutUs", 30000)
        self.trigger.value(0)

    def read(self):
        self.trigger.value(0)
        time.sleep_us(2)
        self.trigger.value(1)
        time.sleep_us(10)
        self.trigger.value(0)

        duration = time_pulse_us(self.echo, 1, self.timeout_us)
        if duration < 0:
            return None, "ERROR"

        distance_cm = duration * 0.0343 / 2
        min_cm = self._get_limit("minCm", 2)
        max_cm = self._get_limit("maxCm", 25)
        quality = "OK" if min_cm <= distance_cm <= max_cm else "OUT_OF_RANGE"
        return round(distance_cm, 1), quality

    def _get_limit(self, key, default):
        return self.config.get(key, default)
