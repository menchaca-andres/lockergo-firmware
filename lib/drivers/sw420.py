import time
from machine import Pin


class SW420:
    def __init__(self, config):
        self.pin = Pin(int(config.get("signalPin", 15)), Pin.IN)
        self.hold_ms = max(50, int(config.get("holdMs", 500)))
        self._active_until = None

    def read(self):
        now_ms = time.ticks_ms()
        if self.pin.value() == 1:
            self._active_until = time.ticks_add(now_ms, self.hold_ms)
            return True, "OK"

        if self._active_until is not None:
            if time.ticks_diff(self._active_until, now_ms) > 0:
                return True, "OK"
            self._active_until = None

        return False, "OK"
