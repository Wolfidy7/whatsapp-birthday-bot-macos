"""Vérifier les modèles distribués sans lire de configuration privée."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import unittest

from birthday_bot import execute, load_config, render_message
from scheduler import agent_config
from settings import FIELDS, load_settings

ROOT = Path(__file__).resolve().parents[1]


class ExampleTests(unittest.TestCase):
    def test_examples_form_a_valid_complete_configuration(self):
        settings = load_settings(ROOT / ".env.example")
        self.assertEqual(set(settings), set(FIELDS.values()))
        config = load_config(ROOT / "birthdays.json.example", env_path=ROOT / ".env.example")
        self.assertEqual(config["group"], "Groupe Démo")
        self.assertEqual(config["timezone"], "UTC")
        for person in config["birthdays"]:
            self.assertEqual(set(person), {"id", "name", "month", "day"})
            self.assertEqual(render_message(person, config), f"Assistant anniversaires\n\nBon anniversaire {person['name']} !")
        agent = agent_config(ROOT, Path("/example/.venv/bin/python"), interval=settings["scheduler_interval_seconds"])
        self.assertEqual(agent["StartInterval"], 300)
        self.assertNotIn("--send", agent["ProgramArguments"])

    def test_example_simulation_has_no_send_or_history(self):
        config = load_config(ROOT / "birthdays.json.example", env_path=ROOT / ".env.example")
        person = config["birthdays"][0]
        now = datetime(2030, person["month"], person["day"], 9, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state.json"
            reports = []
            def forbidden(*args, **kwargs):
                self.fail("La simulation ne doit pas envoyer de message")
            execute(config, state, now=now, sender=forbidden, report=reports.append)
            self.assertTrue(any("Camille → Groupe Démo" in report for report in reports))
            self.assertFalse(state.exists())
