"""Vérifier un groupe ou envoyer un message de test explicitement demandé."""
import argparse
from datetime import datetime
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from birthday_bot import BotError, execute
from settings import load_settings

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description="Ouvrir et vérifier un groupe, sans écrire par défaut")
    parser.add_argument("--group", help="Remplacer le groupe défini dans .env")
    parser.add_argument("--message", help="Remplacer le message de test défini dans .env")
    parser.add_argument("--send", action="store_true", help="Envoyer réellement le message de test")
    args = parser.parse_args()
    try:
        settings = load_settings()
        args.group = args.group if args.group is not None else settings["group"]
        args.message = args.message if args.message is not None else settings["test_message"]
        from whatsapp_ax import WhatsApp
        client = WhatsApp()
        if not args.send:
            client.open_group(args.group)
            print(f"OK: groupe {args.group!r} ouvert, titre vérifié, champ de message trouvé. Aucun message écrit.")
            return 0
        now = datetime.now(ZoneInfo(settings["timezone"]))
        config = {"timezone": settings["timezone"], "max_messages_per_run": 1, "birthdays": [
            {"id": "manual-test", "name": "Test", "month": now.month, "day": now.day, "time": "00:00", "group": args.group, "message": args.message.replace("{", "{{").replace("}", "}}")}
        ]}
        execute(config, ROOT / "state.json", send=True, sender=client.send, now=now)
        return 0
    except (BotError, ImportError, OSError, ValueError, RuntimeError) as exc:
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
