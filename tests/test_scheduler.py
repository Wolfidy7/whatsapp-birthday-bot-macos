import json
from pathlib import Path
import plistlib
from subprocess import CompletedProcess
import tempfile
import unittest
from unittest.mock import patch

from birthday_bot import BotError
import scheduler


class BackgroundPermissionTests(unittest.TestCase):
    def check(self, trust):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            calls = []
            def command(args, **kwargs):
                calls.append(args)
                if args[1] == "bootstrap":
                    job = plistlib.loads(Path(args[-1]).read_bytes())
                    output = Path(job["ProgramArguments"][-1])
                    output.write_text(json.dumps({"trusted": trust, "python": "/tmp/python"}), encoding="utf-8")
                return CompletedProcess(args, 0, "", "")
            with patch.object(scheduler, "ROOT", root), patch.object(scheduler.sys, "platform", "darwin"), patch.object(scheduler.subprocess, "run", side_effect=command):
                try:
                    scheduler.check_background_permissions(Path("/tmp/python"))
                finally:
                    self.assertEqual([c[1] for c in calls], ["bootstrap", "bootout"])
                    self.assertEqual(list((root / "logs").iterdir()), [])

    def test_background_permission_checked_and_probe_removed(self):
        self.check(True)

    def test_denied_background_permission_blocks_install(self):
        with self.assertRaisesRegex(BotError, "Accessibilité refusée"):
            self.check(False)

    def test_truthy_string_is_not_permission(self):
        with self.assertRaises(BotError):self.check("false")


if __name__ == "__main__":unittest.main()
