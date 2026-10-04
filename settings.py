"""Réglages locaux : KEY=value ou chaîne JSON entre guillemets doubles.

Le fichier est relu à chaque appel, sans exécution de shell ni interpolation.
"""
import json
from pathlib import Path
import re

ENV_PATH = Path(__file__).resolve().parent / ".env"
FIELDS = {
    "TIMEZONE": "timezone", "WHATSAPP_GROUP": "group", "SEND_TIME": "time",
    "MESSAGE_HEADER": "message_header", "MESSAGE_TEMPLATE": "message_template",
    "MAX_MESSAGES_PER_RUN": "max_messages_per_run",
    "SCHEDULER_INTERVAL_SECONDS": "scheduler_interval_seconds",
    "ONE_OFF_INTERVAL_SECONDS": "one_off_interval_seconds",
    "TEST_MESSAGE": "test_message", "ONE_OFF_MESSAGE": "one_off_message",
}


def load_settings(path=ENV_PATH):
    values = {}
    for number, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in FIELDS or key in values:
            raise ValueError(f".env ligne {number}: clé inconnue, dupliquée ou syntaxe invalide.")
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except ValueError as exc:
                raise ValueError(f".env ligne {number}: guillemets ou échappement invalides.") from exc
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f".env ligne {number}: valeur vide ou invalide.")
        values[key] = value
    missing = FIELDS.keys() - values.keys()
    if missing:
        raise ValueError(".env: variables manquantes: " + ", ".join(sorted(missing)))
    result = {field: values[key] for key, field in FIELDS.items()}
    for field, maximum in (("max_messages_per_run", 20), ("scheduler_interval_seconds", 86400), ("one_off_interval_seconds", 86400)):
        value = result[field]
        if not re.fullmatch(r"[0-9]+", value) or not 1 <= int(value) <= maximum:
            raise ValueError(f".env: {field} doit être un entier entre 1 et {maximum}.")
        result[field] = int(value)
    # Validation des valeurs communes identique à celle des configurations historiques.
    from birthday_bot import validate_group, validate_time, render_message
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    try:
        ZoneInfo(result["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError(".env: fuseau horaire invalide.") from exc
    validate_group(result["group"])
    validate_time(result["time"])
    render_message({"name": "Test"}, result)
    return result
