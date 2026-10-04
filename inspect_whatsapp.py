"""Inspecter les sélecteurs, sans afficher le contenu des messages par défaut."""
import argparse
from birthday_bot import BotError


def main():
    parser = argparse.ArgumentParser(description="Inspecter les contrôles Accessibility de WhatsApp")
    parser.add_argument("--include-messages", action="store_true", help="Inclure les textes privés dans la sortie")
    args = parser.parse_args()
    try:
        from whatsapp_ax import WhatsApp, get_attr
        client = WhatsApp()
        print(f"WhatsApp PID: {client.pid}")
        for element in client.walk():
            role = get_attr(element, "AXRole")
            ident = get_attr(element, "AXIdentifier")
            if args.include_messages or ident or role in ("AXTextArea", "AXTextField", "AXHeading"):
                description = get_attr(element, "AXDescription")
                if not args.include_messages and ident in ("WAMessageBubbleTableViewCell", "ChatListSearchView_ChatResult"):
                    description = "[contenu masqué]"
                print(f"role={role!r} identifier={ident!r} description={description!r}")
        return 0
    except (BotError, ImportError, RuntimeError) as exc:
        print(f"Erreur: {exc}")
        return 1


if __name__ == "__main__":raise SystemExit(main())
