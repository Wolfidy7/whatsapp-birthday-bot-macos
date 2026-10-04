import json
from pathlib import Path
import tempfile
import unittest

from birthday_bot import BotError, execute, load_config, load_state
from scheduler import agent_config
from settings import load_settings


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = self.root / ".env"
        self.values = {
            "TIMEZONE": "Europe/Paris", "WHATSAPP_GROUP": "Test",
            "SEND_TIME": "08:00", "MESSAGE_HEADER": "*_Bot 🤖_*",
            "MESSAGE_TEMPLATE": "Bonjour {name} 🎂", "MAX_MESSAGES_PER_RUN": "1",
            "SCHEDULER_INTERVAL_SECONDS": "17", "ONE_OFF_INTERVAL_SECONDS": "3",
            "TEST_MESSAGE": "Test #1", "ONE_OFF_MESSAGE": "Un rappel",
        }
        self.write_env()

    def write_env(self):
        self.env.write_text("# Commentaire\n" + "\n".join(k + "=" + json.dumps(v, ensure_ascii=False) for k, v in self.values.items()) + "\n", encoding="utf-8")

    def test_env_drives_person_only_json_and_scheduler(self):
        from datetime import datetime, timezone
        path = self.root / "birthdays.json"
        path.write_text(json.dumps({"birthdays": [{"id": "one", "name": "Amina", "month": 10, "day": 4}]}))
        cfg = load_config(path, env_path=self.env)
        calls = []
        state = self.root / "state.json"
        def sender(group, message, *, before_send):
            before_send()
            self.assertEqual(load_state(state)["pending"]["one:2026-10-04"]["group"], "Test")
            calls.append((group, message))
        execute(cfg, state, now=datetime(2026, 10, 4, 7, tzinfo=timezone.utc), send=True, sender=sender, report=lambda _: None)
        self.assertEqual(calls, [("Test", "*_Bot 🤖_*\n\nBonjour Amina 🎂")])
        job = agent_config(self.root, Path("/tmp/python"), interval=cfg["scheduler_interval_seconds"])
        self.assertEqual(job["StartInterval"], 17)

    def test_rereads_changes_and_preserves_literal_values(self):
        self.values["MESSAGE_HEADER"] = "Prix $5 # remarque\nSuite"
        self.write_env()
        self.assertEqual(load_settings(self.env)["message_header"], self.values["MESSAGE_HEADER"])
        self.values["WHATSAPP_GROUP"] = "Autre"
        self.write_env()
        self.assertEqual(load_settings(self.env)["group"], "Autre")

    def test_unquoted_integer(self):
        text = self.env.read_text().replace('SCHEDULER_INTERVAL_SECONDS="17"', 'SCHEDULER_INTERVAL_SECONDS=17')
        self.env.write_text(text)
        self.assertEqual(load_settings(self.env)["scheduler_interval_seconds"], 17)

    def test_bad_settings_fail_closed(self):
        for key, value in (("SEND_TIME", "24:00"), ("WHATSAPP_GROUP", " Test"),
                           ("TIMEZONE", "bad/zone"), ("MAX_MESSAGES_PER_RUN", "0"),
                           ("SCHEDULER_INTERVAL_SECONDS", "0"), ("ONE_OFF_INTERVAL_SECONDS", "1.5"),
                           ("MESSAGE_TEMPLATE", "{name.__class__}")):
            with self.subTest(key=key):
                original = self.values[key]
                self.values[key] = value
                self.write_env()
                with self.assertRaises((ValueError, BotError)):
                    load_settings(self.env)
                self.values[key] = original

    def test_missing_duplicate_unknown_or_malformed_refused(self):
        for extra in ('TIMEZONE="UTC"', 'UNKNOWN=1', 'bad line', 'bad="unfinished'):
            with self.subTest(extra=extra):
                self.write_env()
                with self.env.open("a") as stream:
                    stream.write(extra + "\n")
                with self.assertRaises(ValueError):
                    load_settings(self.env)
        del self.values["SEND_TIME"]
        self.write_env()
        with self.assertRaisesRegex(ValueError, "manquantes"):
            load_settings(self.env)

    def test_missing_env_prevents_config_loading(self):
        path = self.root / "birthdays.json"
        path.write_text('{"birthdays": []}')
        with self.assertRaises(BotError):
            load_config(path, env_path=self.root / "absent")
