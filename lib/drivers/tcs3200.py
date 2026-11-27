import time
from machine import Pin, time_pulse_us


DEFAULT_REFERENCES = {
    "amarillo": (0.35559504, 0.34826496, 0.29614002, 6.974819),
    "naranja": (0.38223708, 0.32102628, 0.29673666, 5.4694756),
    "negro": (0.32315058, 0.34537356, 0.33147588, 4.6481084),
    "azul": (0.29606872, 0.342133336, 0.36179792, 5.2429784),
}


class TCS3200:
    def __init__(self, config):
        self.s2 = Pin(int(config.get("s2Pin", 16)), Pin.OUT)
        self.s3 = Pin(int(config.get("s3Pin", 17)), Pin.OUT)
        self.out = Pin(int(config.get("outPin", 4)), Pin.IN)
        self.samples = max(1, int(config.get("samples", 3)))
        self.pulse_count = max(1, int(config.get("pulseCount", 6)))
        self.references = config.get("references") or DEFAULT_REFERENCES
        others = [v[3] for k, v in self.references.items() if k != "negro"]
        if "negro" in self.references and others:
            self.black_threshold = (self.references["negro"][3] + min(others)) / 2
        else:
            self.black_threshold = None

    def _period(self):
        total = 0
        valid = 0
        for _ in range(self.pulse_count):
            low_us = time_pulse_us(self.out, 0, 50000)
            high_us = time_pulse_us(self.out, 1, 50000)
            if low_us > 0 and high_us > 0:
                total += low_us + high_us
                valid += 1
        if valid == 0:
            return None
        return total // valid

    def _read_rgb_periods(self):
        self.s2.value(0)
        self.s3.value(0)
        time.sleep_ms(15)
        r = self._period()

        self.s2.value(1)
        self.s3.value(1)
        time.sleep_ms(15)
        g = self._period()

        self.s2.value(0)
        self.s3.value(1)
        time.sleep_ms(15)
        b = self._period()
        return r, g, b

    def _measure(self):
        acc = [0.0, 0.0, 0.0]
        brightness = 0.0
        count = 0
        for index in range(self.samples):
            r, g, b = self._read_rgb_periods()
            if r and g and b:
                ir, ig, ib = 1.0 / r, 1.0 / g, 1.0 / b
                total = ir + ig + ib
                acc[0] += ir / total
                acc[1] += ig / total
                acc[2] += ib / total
                brightness += total * 1000.0
                count += 1
            if index + 1 < self.samples:
                time.sleep_ms(20)
        if count == 0:
            return None
        return (acc[0] / count, acc[1] / count, acc[2] / count, brightness / count)

    def read(self):
        measured = self._measure()
        if measured is None:
            return None, "ERROR"
        if self.black_threshold is not None and measured[3] < self.black_threshold:
            return "negro", "OK"

        best_name = None
        best_dist = 9.0
        for name, ref in self.references.items():
            if name == "negro" and self.black_threshold is not None:
                continue
            dist = sum((measured[i] - ref[i]) ** 2 for i in range(3))
            if dist < best_dist:
                best_name = name
                best_dist = dist

        if best_name is None:
            return None, "ERROR"
        return best_name, "OK"
