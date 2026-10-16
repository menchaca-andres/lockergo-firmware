import os
import tempfile
import unittest

from lib.outbox import Outbox


class OutboxTests(unittest.TestCase):
    def test_entries_survive_restart_and_acknowledge_by_batch(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "pending.json")
            outbox = Outbox(path, 3)
            outbox.append({"component": "door-1", "value": True}, True)
            outbox.append({"component": "distance-1", "value": 14})

            reopened = Outbox(path, 3)
            self.assertEqual(len(reopened.batch(3)), 2)
            self.assertTrue(reopened.has_urgent())

            reopened.acknowledge(1)
            after_acknowledgement = Outbox(path, 3)
            self.assertEqual(after_acknowledgement.batch(3), [
                {"reading": {"component": "distance-1", "value": 14}, "urgent": False}
            ])

    def test_full_queue_discards_the_oldest_item(self):
        with tempfile.TemporaryDirectory() as directory:
            outbox = Outbox(os.path.join(directory, "pending.json"), 2)
            outbox.append({"value": 1})
            outbox.append({"value": 2})
            outbox.append({"value": 3})

            self.assertEqual(
                [entry["reading"]["value"] for entry in outbox.batch(2)],
                [2, 3]
            )


if __name__ == "__main__":
    unittest.main()
