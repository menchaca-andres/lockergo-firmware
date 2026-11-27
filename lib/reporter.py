class Reporter:
    def __init__(self, value_kind, config):
        self.value_kind = value_kind
        self.config = config
        self.deadband = float(config.get("deadband", 2))
        heartbeat_s = int(config.get(
            "heartbeatS",
            300 if value_kind == "BOOLEAN" else 60
        ))
        settle_s = int(config.get("settleS", 2))
        if self.deadband < 0 or heartbeat_s < 1 or settle_s < 0:
            raise ValueError("Invalid reporter timing or deadband configuration")
        self.heartbeat_ms = heartbeat_s * 1000
        self.settle_ms = settle_s * 1000
        max_distance_cm = config.get("maxDistanceCm")
        self.max_distance_cm = (
            float(max_distance_cm) if max_distance_cm is not None else None
        )
        if self.max_distance_cm is not None and self.max_distance_cm <= 0:
            raise ValueError("maxDistanceCm must be positive")
        self.samples = []
        self.last_value = None
        self.last_quality = None
        self.last_report_ms = None
        self.candidate = None
        self.candidate_since_ms = None
        self.confirm_count = max(1, int(config.get("confirmCount", 2 if value_kind == "STRING" else 1)))
        self._pending_string = None
        self._pending_string_hits = 0

    def observe(self, value, quality, now_ms):
        if quality == "ERROR" or value is None:
            self.candidate = None
            self.candidate_since_ms = None
            self._pending_string = None
            self._pending_string_hits = 0
            self.last_quality = "ERROR"
            self.last_report_ms = now_ms
            return self._reading(None, "ERROR", now_ms), False

        if self.value_kind == "STRING":
            text = str(value)
            if text == self._pending_string:
                self._pending_string_hits += 1
            else:
                self._pending_string = text
                self._pending_string_hits = 1

            changed = self.last_value is not None and text != self.last_value
            confirmed = self._pending_string_hits >= self.confirm_count
            if (
                (self.last_value is None and confirmed) or
                (changed and confirmed) or
                (text == self.last_value and (quality != self.last_quality or self._heartbeat_due(now_ms)))
            ):
                self.last_value = text
                self.last_quality = quality
                self.last_report_ms = now_ms
                return self._reading(text, quality, now_ms), changed
            return None, False

        if self.value_kind == "BOOLEAN":
            changed = self.last_value is not None and value is not self.last_value
            if (
                self.last_value is None or
                changed or
                quality != self.last_quality or
                self._heartbeat_due(now_ms)
            ):
                self.last_value = value
                self.last_quality = quality
                self.last_report_ms = now_ms
                return self._reading(value, quality, now_ms), changed
            return None, False

        self.samples.append(float(value))
        if len(self.samples) > 5:
            self.samples.pop(0)
        if len(self.samples) < 5:
            return None, False

        measured = self._median(self.samples)
        if self.max_distance_cm is not None and measured > self.max_distance_cm:
            if self.last_quality != "OUT_OF_RANGE" or self._heartbeat_due(now_ms):
                self.last_quality = "OUT_OF_RANGE"
                self.last_report_ms = now_ms
                self.candidate = None
                self.candidate_since_ms = None
                return self._reading(measured, "OUT_OF_RANGE", now_ms), False
            return None, False

        if quality != "OK":
            if self.last_quality != quality or self._heartbeat_due(now_ms):
                self.last_quality = quality
                self.last_report_ms = now_ms
                return self._reading(measured, quality, now_ms), False
            return None, False

        if self.last_value is None:
            return self._emit(measured, "OK", now_ms, True)

        if self.last_quality != "OK":
            return self._emit(measured, "OK", now_ms, False)

        if abs(measured - self.last_value) < self.deadband:
            self.candidate = None
            self.candidate_since_ms = None
            if self._heartbeat_due(now_ms):
                return self._emit(measured, "OK", now_ms, False)
            return None, False

        if self.candidate is None or abs(measured - self.candidate) >= self.deadband:
            self.candidate = measured
            self.candidate_since_ms = now_ms
            return None, False

        if (
            self.candidate_since_ms is not None and
            _ticks_diff(now_ms, self.candidate_since_ms) >= self.settle_ms
        ):
            return self._emit(measured, "OK", now_ms, True)
        if self._heartbeat_due(now_ms):
            return self._emit(measured, "OK", now_ms, False)
        return None, False

    def force_reading(self, value, quality, now_ms, purpose):
        if value is None or quality == "ERROR":
            return self._reading(None, "ERROR", now_ms)
        measured = float(value)
        if self.max_distance_cm is not None and measured > self.max_distance_cm:
            quality = "OUT_OF_RANGE"
        return self._reading(measured, quality, now_ms, purpose)

    def _emit(self, value, quality, now_ms, changed):
        self.last_value = value
        self.last_quality = quality
        self.last_report_ms = now_ms
        self.candidate = None
        self.candidate_since_ms = None
        return self._reading(value, quality, now_ms), changed

    def _heartbeat_due(self, now_ms):
        return self.last_report_ms is None or _ticks_diff(now_ms, self.last_report_ms) >= self.heartbeat_ms

    def _reading(self, value, quality, now_ms, purpose="PERIODIC"):
        reading = {
            "value": value,
            "quality": quality,
            "ts": _unix_time()
        }
        if purpose != "PERIODIC":
            reading["purpose"] = purpose
        return reading

    @staticmethod
    def _median(values):
        ordered = sorted(values)
        return ordered[len(ordered) // 2]


def _unix_time():
    import time
    epoch_year = time.gmtime(0)[0]
    offset = 946684800 if epoch_year == 2000 else 0
    return time.time() + offset


def _ticks_diff(now_ms, previous_ms):
    import time
    try:
        return time.ticks_diff(now_ms, previous_ms)
    except AttributeError:
        return now_ms - previous_ms
