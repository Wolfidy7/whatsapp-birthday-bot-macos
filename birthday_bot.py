"""Dates, validation et état durable, sans dépendance à macOS."""
from contextlib import contextmanager
from datetime import date, datetime, time
import fcntl
import json
import os
from pathlib import Path
import re
import string
import tempfile
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class BotError(RuntimeError):
    pass


def load_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BotError(f"Impossible de lire {path}: {exc}") from exc


def nonempty(value, label):
    if not isinstance(value, str) or not value.strip():
        raise BotError(f"{label} doit être un texte non vide.")
    return value


def shared_setting(config, person, key):
    return config[key] if key in config else person.get(key)


def validate_group(value):
    nonempty(value, "group")
    if value != value.strip():
        raise BotError("Le nom du groupe ne doit pas commencer ou finir par un espace.")


def validate_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise BotError("Heure invalide; format attendu HH:MM.")


def render_message(person, config=None):
    config = config or {}
    # Le modèle commun est prioritaire; les anciennes configurations restent lisibles.
    template = nonempty(config.get("message_template", person.get("message")), "message_template ou message")
    header = nonempty(config["message_header"], "message_header") if "message_header" in config else ""
    try:
        for _, field, spec, conversion in string.Formatter().parse(template):
            if field is not None and (field != "name" or spec or conversion):
                raise BotError("Seul le marqueur {name} est accepté dans les messages.")
        body = template.format(name=person["name"])
        return header + "\n\n" + body if header else body
    except (ValueError, KeyError) as exc:
        raise BotError(f"Template invalide: {exc}") from exc


def load_config(path, *, env_path=None):
    config = load_json(path)
    if not isinstance(config, dict) or not isinstance(config.get("birthdays"), list):
        raise BotError("La configuration doit contenir une liste birthdays.")
    if env_path is not None:
        from settings import load_settings
        try:
            config.update(load_settings(env_path))
        except (OSError, ValueError, KeyError) as exc:
            raise BotError(f"Configuration .env invalide: {exc}") from exc
    try:
        ZoneInfo(config.get("timezone", "Europe/Paris"))
    except (TypeError, ValueError, ZoneInfoNotFoundError) as exc:
        raise BotError("Fuseau horaire invalide.") from exc
    limit = config.get("max_messages_per_run", 1)
    if type(limit) is not int or not 1 <= limit <= 20:
        raise BotError("max_messages_per_run doit être un entier entre 1 et 20.")
    if "message_header" in config:
        nonempty(config["message_header"], "message_header")
    if "message_template" in config:
        render_message({"name": "Test"}, config)
    if "group" in config:
        validate_group(config["group"])
    if "time" in config:
        validate_time(config["time"])
    seen = set()
    for person in config["birthdays"]:
        if not isinstance(person, dict):
            raise BotError("Chaque anniversaire doit être un objet.")
        ident = nonempty(person.get("id"), "id")
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", ident) or ident in seen:
            raise BotError("Chaque id doit être unique et utiliser lettres, chiffres, - ou _.")
        seen.add(ident)
        nonempty(person.get("name"), "name")
        validate_group(shared_setting(config, person, "group"))
        if type(person.get("month")) is not int or type(person.get("day")) is not int:
            raise BotError("month et day doivent être des entiers.")
        try:
            date(2000, person["month"], person["day"])
        except ValueError as exc:
            raise BotError(f"Date invalide pour {ident}.") from exc
        validate_time(shared_setting(config, person, "time"))
        if type(person.get("enabled", True)) is not bool:
            raise BotError("enabled doit être true ou false.")
        render_message(person, config)
    return config


def load_state(path):
    if not Path(path).exists():
        return {"sent": {}, "pending": {}}
    state = load_json(path)
    if not isinstance(state, dict):
        raise BotError("État invalide; ne pas le supprimer sans vérifier les envois.")
    state.setdefault("pending", {})
    for key in ("sent", "pending"):
        if not isinstance(state.get(key), dict):
            raise BotError(f"État {key} invalide.")
    for ident, value in state["sent"].items():
        try:
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value or not re.fullmatch(r"[a-zA-Z0-9_-]+", ident):
                raise ValueError("Identifiant ou date non canonique")
        except (TypeError, ValueError) as exc:
            raise BotError(f"Date d'envoi invalide pour {ident}.") from exc
    for key, value in state["pending"].items():
        if not isinstance(value, dict) or value.get("status") != "pending":
            raise BotError(f"État d'envoi incertain invalide: {key}.")
        try:
            ident, day = key.rsplit(":", 1)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", ident) or date.fromisoformat(day).isoformat() != day:
                raise ValueError("Identifiant ou date invalide")
            nonempty(value.get("group"), "groupe en attente")
            started = datetime.fromisoformat(value["started_at"])
            if started.tzinfo is None:
                raise ValueError("Fuseau absent")
        except (KeyError, TypeError, ValueError) as exc:
            raise BotError(f"Réservation invalide: {key}.") from exc
    return state


def save_state(path, state):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@contextmanager
def state_lock(path):
    with open(str(path) + ".lock", "a", encoding="utf-8") as stream:
        os.chmod(stream.name, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BotError("Une autre exécution est déjà en cours.") from exc
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def due_birthdays(config, state, now):
    if now.tzinfo is None:
        raise BotError("L'heure doit inclure un fuseau horaire.")
    local = now.astimezone(ZoneInfo(config.get("timezone", "Europe/Paris")))
    today = local.date().isoformat()
    due, blocked = [], []
    for person in sorted(config["birthdays"], key=lambda p: (shared_setting(config, p, "time"), p["id"])):
        if not person.get("enabled", True):
            continue
        if (person["month"], person["day"]) != (local.month, local.day):
            continue
        if local.time() < time.fromisoformat(shared_setting(config, person, "time")):
            continue
        if state["sent"].get(person["id"]) == today:
            continue
        # Un résultat incertain bloque aussi les années suivantes jusqu'à résolution.
        if any(key.startswith(person["id"] + ":") for key in state["pending"]):
            blocked.append(person)
        else:
            due.append(person)
    return today, due, blocked


def execute(config, state_path, *, send=False, sender=None, now=None, report=print):
    now = now or datetime.now(ZoneInfo(config.get("timezone", "Europe/Paris")))
    with state_lock(state_path):
        state = load_state(state_path)
        today, due, blocked = due_birthdays(config, state, now)
        for person in blocked:
            report(f"À VÉRIFIER: {person['id']} (envoi précédent incertain).")
        if not due:
            report(f"{today}: aucun anniversaire à envoyer.")
        for person in due[:config.get("max_messages_per_run", 1)]:
            message = render_message(person, config)
            group = shared_setting(config, person, "group")
            if not send:
                report(f"SIMULATION: {person['name']} → {group}: {message}")
                continue
            if sender is None:
                raise BotError("Expéditeur absent.")
            key = f"{person['id']}:{today}"

            def reserve():
                state["pending"][key] = {"status": "pending", "group": group, "started_at": now.isoformat()}
                save_state(state_path, state)

            try:
                sender(group, message, before_send=reserve)
            except Exception:
                report(f"ÉCHEC: {person['id']}; consulter l'état avant de réessayer.")
                raise
            if key not in state["pending"]:
                raise BotError("L'expéditeur n'a pas réservé l'envoi.")
            state["sent"][person["id"]] = today
            del state["pending"][key]
            save_state(state_path, state)
            report(f"CONFIRMÉ: {person['id']} → {group} ({today}).")
        if len(due) > config.get("max_messages_per_run", 1):
            report("Limite atteinte; les autres anniversaires attendent la prochaine exécution.")
        return len(blocked) == 0
