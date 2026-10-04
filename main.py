"""Exécution ponctuelle; par défaut, simulation sans ouvrir WhatsApp."""
import argparse
from datetime import datetime
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

from birthday_bot import BotError, execute, load_config, load_state, save_state, state_lock
from settings import ENV_PATH

ROOT = Path(__file__).resolve().parent


def logger():
    (ROOT / "logs").mkdir(exist_ok=True)
    log = logging.getLogger("birthdays")
    log.setLevel(logging.INFO)
    if not log.handlers:
        handler = RotatingFileHandler(ROOT / "logs" / "bot.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
    return log


def main(argv=None):
    parser = argparse.ArgumentParser(description="Anniversaires WhatsApp — simulation par défaut")
    parser.add_argument("--config", type=Path, default=ROOT / "birthdays.json")
    parser.add_argument("--state", type=Path, default=ROOT / "state.json")
    parser.add_argument("--env", type=Path, default=ENV_PATH, help="Fichier des réglages communs")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send", action="store_true", help="Envoyer réellement les anniversaires dus")
    mode.add_argument("--doctor", action="store_true", help="Vérifier macOS, les permissions et la fenêtre WhatsApp")
    mode.add_argument("--history", action="store_true", help="Afficher les envois confirmés et ceux à vérifier")
    mode.add_argument("--resolve", nargs=3, metavar=("ID", "DATE", "RÉSULTAT"), help="Résoudre un envoi incertain: résultat sent ou retry")
    args = parser.parse_args(argv)
    log = logger()
    def report(message):
        print(message)
        # Les simulations montrent les messages à l'écran, sans les archiver.
        if not message.startswith("SIMULATION:"):
            log.info(message)
    try:
        config = load_config(args.config, env_path=args.env)
        if args.history:
            state = load_state(args.state)
            print("Confirmés:", state["sent"] or "aucun")
            print("À vérifier:", state["pending"] or "aucun")
            return 0
        if args.resolve:
            ident, day, outcome = args.resolve
            datetime.strptime(day, "%Y-%m-%d")
            if outcome not in ("sent", "retry"):
                raise BotError("Résultat attendu: sent ou retry.")
            with state_lock(args.state):
                state = load_state(args.state)
                key = f"{ident}:{day}"
                if key not in state["pending"]:
                    raise BotError("Cet envoi n'est pas en attente de vérification.")
                if outcome == "sent":
                    state["sent"][ident] = day
                del state["pending"][key]
                save_state(args.state, state)
            report(f"État résolu pour {key}: {outcome}.")
            return 0
        if args.doctor:
            if sys.platform != "darwin":
                raise BotError("Le pilotage de WhatsApp requiert macOS.")
            from whatsapp_ax import WhatsApp
            client = WhatsApp()
            report(f"OK: accessibilité, session ouverte et fenêtre WhatsApp (PID {client.pid}).")
            report(f"Fuseau: {config.get('timezone', 'Europe/Paris')} ; {len(config['birthdays'])} anniversaire(s) configuré(s).")
            return 0
        sender = None
        if args.send:
            if sys.platform != "darwin":
                raise BotError("L'envoi requiert macOS.")
            from whatsapp_ax import send_message
            sender = send_message
        return 0 if execute(config, args.state, send=args.send, sender=sender, report=report) else 1
    except (BotError, OSError, ValueError, ImportError, RuntimeError) as exc:
        log.error("%s", exc)
        print(f"Erreur: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
