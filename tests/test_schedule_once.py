from datetime import datetime, timedelta, timezone
import unittest
from birthday_bot import BotError
from schedule_once import one_off_status


class OneOffTests(unittest.TestCase):
    def setUp(self):
        self.planned = datetime(2026, 10, 4, 23, 59, 30, tzinfo=timezone.utc)
        self.config = {"timezone": "Europe/Paris", "send_at": self.planned.isoformat(), "birthdays": [{"id": "one-off-test"}]}
        self.state = {"sent": {}, "pending": {}}

    def test_not_before_exact_second(self):
        self.assertEqual(one_off_status(self.config, self.state, self.planned - timedelta(seconds=1)), "waiting")
        self.assertEqual(one_off_status(self.config, self.state, self.planned), "due")

    def test_expired_next_local_day(self):
        self.assertEqual(one_off_status(self.config, self.state, self.planned + timedelta(days=1)), "expired")

    def test_uses_local_day_across_midnight(self):
        self.state["sent"]["one-off-test"] = "2026-10-05"
        self.assertEqual(one_off_status(self.config, self.state, self.planned), "sent")

    def test_pending_never_retried(self):
        self.state["pending"]["one-off-test:2026-10-05"] = {"status": "pending"}
        self.assertEqual(one_off_status(self.config, self.state, self.planned), "pending")

    def test_naive_planned_time_refused(self):
        self.config["send_at"] = "2026-10-04T08:00:00"
        with self.assertRaises(BotError):one_off_status(self.config, self.state, self.planned)


if __name__ == "__main__":unittest.main()
