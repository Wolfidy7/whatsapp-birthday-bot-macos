from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from birthday_bot import BotError, due_birthdays, execute, load_config, load_state, render_message, save_state, state_lock
from scheduler import agent_config


def config():
    return {"timezone": "Europe/Paris", "max_messages_per_run": 1, "birthdays": [
        {"id": "demo", "name": "Amina", "month": 10, "day": 4, "time": "08:00", "group": "Test", "message": "Bon anniversaire {name} 🎂"}
    ]}


class BirthdayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.state = self.root / "state.json"
        self.now = datetime(2026, 10, 4, 7, 0, tzinfo=timezone.utc)
        self.empty = {"sent": {}, "pending": {}}

    def validate(self, cfg):
        path = self.root / "birthdays.json"
        path.write_text(json.dumps(cfg), encoding="utf-8")
        return load_config(path)

    def test_timezone_and_send_time(self):
        before = datetime(2026, 10, 4, 5, 59, tzinfo=timezone.utc)
        self.assertEqual(due_birthdays(config(), self.empty, before)[1], [])
        exact = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
        self.assertEqual(len(due_birthdays(config(), self.empty, exact)[1]), 1)

    def test_local_date_can_differ_from_utc(self):
        cfg = config(); cfg["birthdays"][0]["time"] = "00:00"
        now = datetime(2026, 10, 3, 22, 30, tzinfo=timezone.utc)
        today, due, _ = due_birthdays(cfg, self.empty, now)
        self.assertEqual(today, "2026-10-04")
        self.assertEqual(len(due), 1)

    def test_no_late_send_next_day(self):
        tomorrow = datetime(2026, 10, 5, 7, tzinfo=timezone.utc)
        self.assertEqual(due_birthdays(config(), self.empty, tomorrow)[1], [])

    def test_naive_datetime_rejected(self):
        with self.assertRaises(BotError):
            due_birthdays(config(), self.empty, datetime(2026, 10, 4))

    def test_disabled_and_already_sent(self):
        cfg = config(); cfg["birthdays"][0]["enabled"] = False
        self.assertEqual(due_birthdays(cfg, self.empty, self.now)[1], [])
        state = {"sent": {"demo": "2026-10-04"}, "pending": {}}
        self.assertEqual(due_birthdays(config(), state, self.now)[1], [])
        state["sent"]["demo"] = "2025-10-04"
        self.assertEqual(len(due_birthdays(config(), state, self.now)[1]), 1)

    def test_leap_day_only_in_leap_year(self):
        cfg = config(); cfg["birthdays"][0].update(month=2, day=29)
        self.validate(cfg)
        leap = datetime(2028, 2, 29, 9, tzinfo=timezone.utc)
        self.assertEqual(len(due_birthdays(cfg, self.empty, leap)[1]), 1)
        normal = datetime(2027, 2, 28, 9, tzinfo=timezone.utc)
        self.assertEqual(due_birthdays(cfg, self.empty, normal)[1], [])

    def test_invalid_config(self):
        changes = [
            {"day": 31, "month": 4}, {"day": True}, {"month": 0},
            {"time": "8:00"}, {"time": "24:00"}, {"group": ""},
            {"group": " Test"}, {"message": " "}, {"id": "../bad"},
            {"enabled": "false"}, {"message": "{name.__class__}"},
            {"message": "{unknown}"}, {"message": "{name:>20}"},
            {"message": "{name!r}"}, {"message": "{"},
        ]
        for change in changes:
            with self.subTest(change=change):
                cfg = config(); cfg["birthdays"][0].update(change)
                with self.assertRaises(BotError):self.validate(cfg)
        for change in ({"timezone": "bad/zone"}, {"max_messages_per_run": True}, {"max_messages_per_run": 0}):
            cfg = config(); cfg.update(change)
            with self.assertRaises(BotError):self.validate(cfg)

    def test_duplicate_id_rejected(self):
        cfg = config(); cfg["birthdays"].append(deepcopy(cfg["birthdays"][0]))
        with self.assertRaises(BotError):self.validate(cfg)

    def test_name_template_preserves_unicode(self):
        self.assertEqual(render_message(config()["birthdays"][0]), "Bon anniversaire Amina 🎂")

    def test_shared_group_and_time_drive_send_and_reservation(self):
        cfg = config()
        cfg.update(group="Groupe commun", time="09:00")
        person = cfg["birthdays"][0]
        del person["group"]
        del person["time"]
        self.validate(cfg)
        before = datetime(2026, 10, 4, 6, 59, tzinfo=timezone.utc)
        self.assertEqual(due_birthdays(cfg, self.empty, before)[1], [])
        calls = []
        def sender(group, message, *, before_send):
            before_send()
            self.assertEqual(load_state(self.state)["pending"]["demo:2026-10-04"]["group"], "Groupe commun")
            calls.append(group)
        execute(cfg, self.state, send=True, sender=sender, now=self.now, report=lambda _: None)
        self.assertEqual(calls, ["Groupe commun"])

    def test_shared_group_and_time_override_legacy_fields(self):
        cfg = config()
        cfg.update(group="Commun", time="10:00")
        self.validate(cfg)
        self.assertEqual(due_birthdays(cfg, self.empty, self.now)[1], [])
        reports = []
        later = datetime(2026, 10, 4, 8, 0, tzinfo=timezone.utc)
        execute(cfg, self.state, now=later, report=reports.append)
        self.assertIn("→ Commun:", reports[0])

    def test_invalid_shared_group_and_time_refused(self):
        for change in ({"group": ""}, {"group": " Test"}, {"group": 42},
                       {"time": "8:00"}, {"time": "24:00"}, {"time": None}):
            with self.subTest(change=change):
                cfg = config(); cfg.update(change)
                with self.assertRaises(BotError):
                    self.validate(cfg)

    def test_shared_message_used_for_every_person(self):
        cfg = config()
        cfg.update(message_header="*_Bot de Wolf 🤖_*", message_template="Joyeux anniversaire {name} 🎉🎂 Profite bien de ta journée !")
        person = cfg["birthdays"][0]
        del person["message"]
        other = dict(person, id="second", name="Moussa")
        cfg["birthdays"].append(other)
        self.validate(cfg)
        reports = []
        cfg["max_messages_per_run"] = 2
        execute(cfg, self.state, now=self.now, report=reports.append)
        for name, report in zip(("Amina", "Moussa"), reports):
            self.assertIn(f"*_Bot de Wolf 🤖_*\n\nJoyeux anniversaire {name} 🎉🎂 Profite bien de ta journée !", report)
        self.assertFalse(self.state.exists())

    def test_shared_template_is_single_source_of_truth(self):
        cfg = config()
        cfg["message_template"] = "Salut {name}"
        self.assertEqual(render_message(cfg["birthdays"][0], cfg), "Salut Amina")

    def test_invalid_shared_message_refused(self):
        for change in ({"message_header": " "}, {"message_header": 42},
                       {"message_template": ""}, {"message_template": "{name.__class__}"},
                       {"message_template": "{unknown}"}, {"message_template": "{"}):
            with self.subTest(change=change):
                cfg = config(); cfg.update(change)
                with self.assertRaises(BotError):
                    self.validate(cfg)

    def test_dry_run_has_no_sender_or_state_effects(self):
        def forbidden(*args, **kwargs):self.fail("Unexpected send")
        execute(config(), self.state, sender=forbidden, now=self.now, report=lambda _: None)
        self.assertFalse(self.state.exists())

    def test_confirmed_send_is_not_repeated(self):
        calls = []
        def sender(group, message, *, before_send):
            before_send()
            self.assertIn("demo:2026-10-04", load_state(self.state)["pending"])
            calls.append((group, message))
        for _ in range(2):execute(config(), self.state, send=True, sender=sender, now=self.now, report=lambda _: None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(load_state(self.state), {"sent": {"demo": "2026-10-04"}, "pending": {}})
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_uncertain_send_blocks_retry(self):
        def uncertain(group, message, *, before_send):
            before_send(); raise BotError("No confirmation")
        with self.assertRaises(BotError):execute(config(), self.state, send=True, sender=uncertain, now=self.now, report=lambda _: None)
        self.assertEqual(load_state(self.state)["sent"], {})
        def forbidden(*args, **kwargs):self.fail("Duplicate side effect")
        result = execute(config(), self.state, send=True, sender=forbidden, now=self.now, report=lambda _: None)
        self.assertFalse(result)

    def test_failure_before_send_can_be_retried(self):
        def unavailable(*args, **kwargs):raise BotError("No group")
        with self.assertRaises(BotError):execute(config(), self.state, send=True, sender=unavailable, now=self.now, report=lambda _: None)
        self.assertFalse(self.state.exists())

    def test_crash_after_send_before_state_commit_blocks_retry(self):
        def sender(group, message, *, before_send):before_send()
        original = save_state
        count = 0
        def failing_save(path, state):
            nonlocal count
            count += 1
            if count == 2:raise OSError("Disk failure")
            original(path, state)
        with patch("birthday_bot.save_state", side_effect=failing_save):
            with self.assertRaises(OSError):execute(config(), self.state, send=True, sender=sender, now=self.now, report=lambda _: None)
        state = load_state(self.state)
        self.assertIn("demo:2026-10-04", state["pending"])
        self.assertEqual(state["sent"], {})

    def test_atomic_write_preserves_old_file_on_failure(self):
        save_state(self.state, self.empty)
        with patch("birthday_bot.os.replace", side_effect=OSError("No disk")):
            with self.assertRaises(OSError):save_state(self.state, {"sent": {"demo": "2026-10-04"}, "pending": {}})
        self.assertEqual(load_state(self.state), self.empty)
        self.assertEqual(list(self.root.glob(".state.json.*")), [])

    def test_concurrent_run_refused(self):
        with state_lock(self.state):
            with self.assertRaises(BotError):
                with state_lock(self.state):self.fail("Lock not held")

    def test_corrupt_state_never_sends(self):
        self.state.write_text("{broken", encoding="utf-8")
        with self.assertRaises(BotError):execute(config(), self.state, send=True, sender=lambda *a, **k: self.fail("Send"), now=self.now)

    def test_legacy_state_supported(self):
        self.state.write_text('{"sent": {"demo": "2025-10-04"}}', encoding="utf-8")
        self.assertEqual(load_state(self.state)["pending"], {})

    def test_limit_spreads_work_between_runs(self):
        cfg = config(); other = deepcopy(cfg["birthdays"][0]); other["id"] = "second"; cfg["birthdays"].append(other)
        calls = []
        def sender(group, message, *, before_send):before_send(); calls.append(group)
        execute(cfg, self.state, send=True, sender=sender, now=self.now, report=lambda _: None)
        self.assertEqual(len(calls), 1)
        execute(cfg, self.state, send=True, sender=sender, now=self.now, report=lambda _: None)
        self.assertEqual(len(calls), 2)

    def test_agent_is_simulation_by_default_and_keeps_venv_path(self):
        python = self.root / ".venv" / "bin" / "python"
        agent = agent_config(self.root, python, interval=300)
        self.assertEqual(agent["ProgramArguments"][0], str(python))
        self.assertNotIn("--send", agent["ProgramArguments"])
        self.assertEqual(agent["StartInterval"], 300)
        self.assertIn("--send", agent_config(self.root, python, interval=300, send=True)["ProgramArguments"])


if __name__ == "__main__":unittest.main()
