import json
import os


class Outbox:
    def __init__(self, path, limit):
        self.path = path
        self.limit = limit
        self.entries = self._load()

    def append(self, reading, urgent=False):
        if len(self.entries) >= self.limit:
            self.entries.pop(0)
            print("Offline queue full; dropping the oldest reading")
        self.entries.append({"reading": reading, "urgent": urgent})
        self._save()

    def batch(self, size):
        return self.entries[:size]

    def acknowledge(self, count):
        if count < 0 or count > len(self.entries):
            raise ValueError("Invalid outbox acknowledgement count")
        del self.entries[:count]
        self._save()

    def has_urgent(self):
        return any(entry["urgent"] for entry in self.entries)

    def _load(self):
        try:
            with open(self.path, "r") as file:
                entries = json.load(file)
        except OSError as error:
            if error.args and error.args[0] == 2:
                return []
            raise
        if not isinstance(entries, list):
            raise ValueError("The persistent outbox must contain a JSON array")
        return entries[-self.limit:]

    def _save(self):
        temporary_path = self.path + ".tmp"
        with open(temporary_path, "w") as file:
            json.dump(self.entries, file)
        try:
            os.remove(self.path)
        except OSError:
            pass
        os.rename(temporary_path, self.path)
