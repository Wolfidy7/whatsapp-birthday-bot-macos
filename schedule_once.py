"""Programmer un message ponctuel, avec état partagé et retrait automatique."""
import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import plistlib
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

from birthday_bot import BotError, execute, load_config, load_state, nonempty, state_lock
from settings import load_settings

ROOT = Path(__file__).resolve().parent
LABEL = "com.whatsapp-birthday-bot.one-off"
TARGET = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
SPEC = ROOT / "logs" / "scheduled-message.json"
STATE = ROOT / "state.json"
CANCEL = ROOT / "logs" / "one-off-cancel"


def scheduled_time(config):
    try:
        planned = datetime.fromisoformat(config["send_at"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BotError("Date programmée invalide.") from exc
    if planned.tzinfo is None:
        raise BotError("La date programmée doit contenir un fuseau horaire.")
    return planned


def one_off_status(config, state, now):
    planned = scheduled_time(config)
    local = now.astimezone(ZoneInfo(config["timezone"]))
    person = config["birthdays"][0]
    day = planned.astimezone(ZoneInfo(config["timezone"])).date().isoformat()
    if state["sent"].get(person["id"]) == day:
        return "sent"
    if f"{person['id']}:{day}" in state["pending"]:
        return "pending"
    if now < planned:
        return "waiting"
    if local.date().isoformat() != day:
        return "expired"
    return "due"


def remove_agent():
    TARGET.unlink(missing_ok=True)
    # launchd peut terminer ce processus après la confirmation déjà sauvegardée.
    subprocess.run(["launchctl", "bootout", f"gui/{os.getuid()}/{LABEL}"], capture_output=True)


def run_job(config=None, *, cleanup=True):
    config = config if config is not None else load_config(SPEC)
    state = load_state(STATE)
    now = datetime.now(ZoneInfo(config["timezone"]))
    status = one_off_status(config, state, now)
    if status == "waiting":
        return 0
    if status in ("sent", "expired", "pending"):
        print(f"Tâche ponctuelle terminée: {status}.", flush=True)
        if cleanup:
            remove_agent()
        return 0 if status == "sent" else 1
    from whatsapp_ax import send_message
    try:
        execute(config, STATE, send=True, sender=send_message, now=now, report=lambda text: print(text, flush=True))
    except Exception as exc:
        print(f"Envoi ponctuel arrêté: {exc}. Consulter l'état avant une nouvelle programmation.", file=sys.stderr, flush=True)
        if cleanup:
            remove_agent()
        return 1
    # Le verrou d'état est relâché avant le retrait du service.
    state = load_state(STATE)
    if one_off_status(config, state, now) == "sent":
        print("Message ponctuel confirmé; fin de la tâche.", flush=True)
        if cleanup:
            remove_agent()
    return 0


def install_job(args):
    settings = load_settings()
    args.group = args.group if args.group is not None else settings["group"]
    args.message = args.message if args.message is not None else settings["one_off_message"]
    if args.in_minutes is None or not 0 < args.in_minutes <= 1440:
        raise BotError("Le délai doit être compris entre 0 et 1440 minutes.")
    nonempty(args.group, "group"); nonempty(args.message, "message")
    if TARGET.exists():
        raise BotError("Un envoi ponctuel existe déjà; utilise --cancel avant de le remplacer.")
    if not args.foreground:
        from scheduler import check_background_permissions
        check_background_permissions(Path(sys.executable).absolute())
    from whatsapp_ax import WhatsApp
    client = WhatsApp()
    client.open_group(args.group)
    now = datetime.now(ZoneInfo(settings["timezone"]))
    planned = now + timedelta(minutes=args.in_minutes)
    ident = "one-off-" + planned.strftime("%Y%m%dT%H%M%S")
    config = {"timezone": settings["timezone"], "max_messages_per_run": 1, "send_at": planned.isoformat(), "birthdays": [
        {"id": ident, "name": "Amina", "month": planned.month, "day": planned.day,
         "time": planned.strftime("%H:%M"), "group": args.group,
         "message": args.message.replace("{", "{{").replace("}", "}}")}
    ]}
    SPEC.parent.mkdir(exist_ok=True)
    SPEC.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    SPEC.chmod(0o600)
    load_config(SPEC)
    if args.foreground:
        CANCEL.unlink(missing_ok=True)
        print(f"Envoi réel programmé dans {args.group!r} le {planned:%d/%m/%Y à %H:%M:%S} ({config['timezone']}), depuis ce terminal.", flush=True)
        print(f"Message: {args.message}", flush=True)
        while datetime.now(ZoneInfo(config["timezone"])) < planned:
            if CANCEL.exists():
                print("Envoi ponctuel annulé.", flush=True)
                return 0
            time.sleep(0.5)
        if CANCEL.exists():
            print("Envoi ponctuel annulé.", flush=True)
            return 0
        return run_job(config, cleanup=False)
    agent = {
        "Label": LABEL,
        "ProgramArguments": [str(Path(sys.executable).absolute()), str(ROOT / "schedule_once.py"), "--run"],
        "WorkingDirectory": str(ROOT), "StartInterval": settings["one_off_interval_seconds"], "ThrottleInterval": 1,
        "RunAtLoad": True, "LimitLoadToSessionType": "Aqua", "Umask": 0o077,
        "StandardOutPath": str(ROOT / "logs" / "one-off.log"),
        "StandardErrorPath": str(ROOT / "logs" / "one-off-error.log"),
    }
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_bytes(plistlib.dumps(agent)); TARGET.chmod(0o600)
    result = subprocess.run(["launchctl", "bootstrap", f"gui/{os.getuid()}", str(TARGET)], capture_output=True, text=True)
    if result.returncode:
        TARGET.unlink(missing_ok=True)
        raise BotError(f"launchctl: {result.stderr.strip()}")
    print(f"Envoi réel programmé dans {args.group!r} le {planned:%d/%m/%Y à %H:%M:%S} ({config['timezone']}).")
    print(f"Message: {args.message}")
    print(f"Identifiant: {ident}")
    return 0


def main():
    parser = argparse.ArgumentParser(description="Installer un envoi WhatsApp ponctuel")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--in-minutes", type=float, help="Installer un envoi réel dans N minutes")
    mode.add_argument("--run", action="store_true", help=argparse.SUPPRESS)
    mode.add_argument("--cancel", action="store_true", help="Annuler le message programmé")
    parser.add_argument("--group", help="Remplacer le groupe défini dans .env")
    parser.add_argument("--message", help="Remplacer le message ponctuel défini dans .env")
    parser.add_argument("--foreground", action="store_true", help="Attendre puis envoyer depuis ce terminal, sans launchd")
    args = parser.parse_args()
    try:
        if sys.platform != "darwin":
            raise BotError("La planification nécessite macOS.")
        if args.cancel:
            CANCEL.parent.mkdir(exist_ok=True)
            CANCEL.touch()
            remove_agent(); print("Envoi ponctuel annulé."); return 0
        if args.run:
            return run_job()
        SPEC.parent.mkdir(exist_ok=True)
        with state_lock(SPEC):
            return install_job(args)
    except (BotError, OSError, ImportError, ValueError, RuntimeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr, flush=True)
        return 1


if __name__ == "__main__":raise SystemExit(main())
