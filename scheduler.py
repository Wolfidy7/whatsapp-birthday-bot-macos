"""Générer, installer ou retirer le LaunchAgent utilisateur macOS."""
import argparse
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import tempfile
import time
import uuid

from birthday_bot import BotError, load_config, load_json
from settings import ENV_PATH

ROOT = Path(__file__).resolve().parent
LABEL = "com.whatsapp-birthday-bot.scheduler"


def check_background_permissions(python):
    """Vérifier le Python exact sous launchd, sans accéder aux conversations."""
    if sys.platform != "darwin":
        raise BotError("La vérification nécessite macOS.")
    (ROOT / "logs").mkdir(exist_ok=True)
    label = LABEL + ".check-" + uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="permission-check-", dir=ROOT / "logs") as directory:
        folder = Path(directory)
        output = folder / "result.json"
        plist = folder / "probe.plist"
        plist.write_bytes(plistlib.dumps({
            "Label": label, "RunAtLoad": True, "LimitLoadToSessionType": "Aqua",
            "ProgramArguments": [str(python), str(ROOT / "background_check.py"), "--output", str(output)],
            "StandardErrorPath": str(folder / "error.log"), "Umask": 0o077,
        }))
        result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)], capture_output=True, text=True)
        if result.returncode:
            raise BotError(f"Impossible de vérifier launchd: {result.stderr.strip()}")
        try:
            deadline = time.monotonic() + 10
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(0.1)
            if not output.exists():
                raise BotError("La vérification de permission en arrière-plan n'a pas répondu.")
            report = load_json(output)
            if report.get("trusted") is not True:
                executable = report.get("python", str(python))
                raise BotError(f"Accessibilité refusée au Python de launchd. Autorise {executable} dans Réglages système → Confidentialité et sécurité → Accessibilité, puis réessaie.")
        finally:
            subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{label}"], capture_output=True)


def agent_config(root, python, *, interval, send=False):
    arguments = [str(python), str(root / "main.py")]
    if send:
        arguments.append("--send")
    return {
        "Label": LABEL, "ProgramArguments": arguments,
        "WorkingDirectory": str(root), "StartInterval": interval,
        "RunAtLoad": True, "LimitLoadToSessionType": "Aqua",
        "StandardOutPath": str(root / "logs" / "scheduler.log"),
        "StandardErrorPath": str(root / "logs" / "scheduler-error.log"),
        "Umask": 0o077,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Planification selon .env, simulation par défaut")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--install", action="store_true", help="Installer et charger le LaunchAgent")
    mode.add_argument("--uninstall", action="store_true", help="Arrêter et retirer le LaunchAgent")
    mode.add_argument("--check-permissions", action="store_true", help="Vérifier les permissions du Python réellement lancé par launchd")
    parser.add_argument("--send", action="store_true", help="Activer les envois réels du LaunchAgent")
    parser.add_argument("--output", type=Path, default=ROOT / f"{LABEL}.plist")
    args = parser.parse_args(argv)
    target = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
    service = f"gui/{os.getuid()}/{LABEL}"
    domain = f"gui/{os.getuid()}"
    try:
        if args.uninstall:
            if sys.platform != "darwin":
                raise BotError("L'installation nécessite macOS.")
            subprocess.run(["launchctl", "bootout", service], capture_output=True)
            target.unlink(missing_ok=True)
            print("Planification retirée.")
            return 0
        config = load_config(ROOT / "birthdays.json", env_path=ENV_PATH)
        # Ne pas résoudre le symlink .venv/bin/python: il porte l'environnement Python.
        python = Path(sys.executable).absolute()
        if args.check_permissions:
            check_background_permissions(python)
            print("OK: le Python de launchd possède l'autorisation Accessibilité.")
            return 0
        (ROOT / "logs").mkdir(exist_ok=True)
        data = plistlib.dumps(agent_config(ROOT, python, interval=config["scheduler_interval_seconds"], send=args.send))
        if args.install:
            if sys.platform != "darwin":
                raise BotError("L'installation nécessite macOS.")
            if args.send:
                check_background_permissions(python)
                from whatsapp_ax import WhatsApp
                WhatsApp()  # Au minimum, vérifier les permissions avant activation.
            target.parent.mkdir(parents=True, exist_ok=True)
            # Refuser de remplacer silencieusement une installation existante.
            if target.exists():
                raise BotError("Un agent existe déjà. Retire-le avec --uninstall avant de le remplacer.")
            target.write_bytes(data)
            target.chmod(0o600)
            result = subprocess.run(["launchctl", "bootstrap", domain, str(target)], capture_output=True, text=True)
            if result.returncode:
                target.unlink(missing_ok=True)
                raise BotError(f"launchctl: {result.stderr.strip()}")
            print(f"Installé: {target} ({'ENVOIS RÉELS' if args.send else 'simulation'}).")
        else:
            args.output.write_bytes(data)
            print(f"Agent prêt: {args.output} ({'ENVOIS RÉELS' if args.send else 'simulation'}).")
        return 0
    except (BotError, OSError, ImportError, RuntimeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
