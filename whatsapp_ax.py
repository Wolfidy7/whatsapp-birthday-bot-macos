"""Pilotage par Accessibility; positions des contrôles lues dans l'arbre AX."""
import subprocess
import time
import unicodedata
import re

from Cocoa import NSWorkspace, NSPasteboard, NSPasteboardItem, NSRunningApplication, NSApplicationActivateIgnoringOtherApps
from ApplicationServices import (
    AXIsProcessTrusted, AXUIElementCreateApplication, AXUIElementCopyAttributeValue,
    AXUIElementPerformAction,
    AXUIElementSetMessagingTimeout,
    AXValueGetValue, kAXValueCGPointType, kAXValueCGSizeType,
)
from Quartz import (
    CGEventSetFlags, CGSessionCopyCurrentDictionary,
    CGEventCreateMouseEvent, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
    kCGMouseButtonLeft, CGEventCreate, CGEventGetLocation, CGEventPost,
    kCGHIDEventTap, kCGEventMouseMoved,
    CGEventSetIntegerValueField, kCGMouseEventClickState,
)
from birthday_bot import BotError, nonempty

BUNDLE_ID = "net.whatsapp.WhatsApp"
# Retirer seulement les marques de direction; préserver les accents et les emoji ZWJ.
BIDI_MARKS = dict.fromkeys(map(ord, "\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"))


def clean(value):
    return unicodedata.normalize("NFC", str(value or "").translate(BIDI_MARKS))


def displayed_message(value):
    """AX expose le texte rendu, sans les marqueurs gras/italique/barré."""
    value = clean(value)
    pattern = r"(?<!\w)([*_~])(?=\S)([^\n]+?)(?<=\S)\1(?!\w)"
    while True:
        rendered = re.sub(pattern, lambda match: match.group(2), value)
        if rendered == value:
            return value
        value = rendered


def get_attr(element, attribute):
    if element is None:
        return None
    error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return value if error == 0 else None


def children(element):
    return get_attr(element, "AXChildren") or []


def get_whatsapp_pid():
    for app in NSWorkspace.sharedWorkspace().runningApplications():
        if app.bundleIdentifier() == BUNDLE_ID:
            return app.processIdentifier()
    return None


def launch_whatsapp():
    subprocess.run(["open", "-b", BUNDLE_ID], check=True, timeout=10)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        pid = get_whatsapp_pid()
        if pid:
            return pid
        time.sleep(0.25)
    raise BotError("Impossible de lancer WhatsApp.")


def get_whatsapp_app():
    if not AXIsProcessTrusted():
        raise BotError("Accessibilité refusée. Lance permissions.py depuis le même terminal.")
    app = AXUIElementCreateApplication(launch_whatsapp())
    AXUIElementSetMessagingTimeout(app, 1.0)
    return app


def session_ready():
    session = CGSessionCopyCurrentDictionary()
    if not session or session.get("CGSSessionScreenIsLocked") or not session.get("kCGSSessionOnConsoleKey"):
        raise BotError("La session macOS doit être ouverte et déverrouillée.")


def press(element):
    if get_attr(element, "AXEnabled") is False:
        raise BotError("Le contrôle WhatsApp est désactivé.")
    error = AXUIElementPerformAction(element, "AXPress")
    if error:
        raise BotError(f"WhatsApp refuse l'action Accessibility ({error}).")


def wait_for(predicate, description, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise BotError(f"Délai dépassé: {description}.")


class WhatsApp:
    def __init__(self):
        session_ready()
        self.app = get_whatsapp_app()
        self.pid = get_whatsapp_pid()
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        wait_for(lambda: get_attr(self.app, "AXMainWindow"), "fenêtre WhatsApp indisponible")

    def window(self):
        error, window = AXUIElementCopyAttributeValue(self.app, "AXMainWindow", None)
        if error or window is None:
            raise BotError(f"Fenêtre WhatsApp inaccessible ({error}); ouvre WhatsApp et réessaie.")
        if get_attr(window, "AXModal"):
            raise BotError("Ferme la boîte de dialogue WhatsApp avant de continuer.")
        return window

    def walk(self, root=None):
        stack = [(self.window() if root is None else root, 0)]
        deadline = time.monotonic() + 8
        count = 0
        while stack:
            if time.monotonic() > deadline or count >= 2000:
                raise BotError("Arbre Accessibility trop lent ou trop volumineux.")
            element, depth = stack.pop()
            count += 1
            yield element
            if depth < 24:
                stack.extend((child, depth + 1) for child in reversed(children(element)))

    def find(self, predicate, root=None):
        return [element for element in self.walk(root) if predicate(element)]

    def identifier(self, ident):
        matches = self.find(lambda e: get_attr(e, "AXIdentifier") == ident)
        if len(matches) != 1:
            raise BotError(f"Contrôle {ident} absent ou ambigu; utilise inspect_whatsapp.py.")
        return matches[0]

    def click(self, element):
        session_ready()
        if get_attr(element, "AXEnabled") is False:
            raise BotError("Le contrôle WhatsApp est désactivé.")
        position = get_attr(element, "AXPosition")
        size = get_attr(element, "AXSize")
        if position is None or size is None:
            raise BotError("Position Accessibility du contrôle indisponible.")
        ok, origin = AXValueGetValue(position, kAXValueCGPointType, None)
        size_ok, dimensions = AXValueGetValue(size, kAXValueCGSizeType, None)
        if not ok or not size_ok or dimensions.width <= 0 or dimensions.height <= 0:
            raise BotError("Position Accessibility invalide.")
        point = (origin.x + dimensions.width / 2, origin.y + dimensions.height / 2)
        running = NSRunningApplication.runningApplicationWithProcessIdentifier_(self.pid)
        running.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)
        def foreground():
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            return front is not None and front.processIdentifier() == self.pid
        wait_for(foreground, "WhatsApp au premier plan", timeout=2)
        saved_cursor = CGEventGetLocation(CGEventCreate(None))
        button_down = False
        try:
            # Catalyst ignore les clics adressés au PID; le clic système est permis
            # uniquement lorsque WhatsApp occupe réellement le premier plan.
            for event_type in (kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp):
                if event_type != kCGEventLeftMouseUp and not foreground():
                    raise BotError("WhatsApp a perdu le premier plan avant le clic.")
                event = CGEventCreateMouseEvent(None, event_type, point, kCGMouseButtonLeft)
                if event is None:
                    raise BotError("Impossible de créer le clic.")
                CGEventSetFlags(event, 0)
                CGEventSetIntegerValueField(event, kCGMouseEventClickState, 1)
                CGEventPost(kCGHIDEventTap, event)
                if event_type == kCGEventLeftMouseDown:
                    button_down = True
                elif event_type == kCGEventLeftMouseUp:
                    button_down = False
                time.sleep(0.05)
        finally:
            if button_down:
                release = CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, point, kCGMouseButtonLeft)
                if release is not None:
                    CGEventSetFlags(release, 0)
                    CGEventPost(kCGHIDEventTap, release)
            restore = CGEventCreateMouseEvent(None, kCGEventMouseMoved, saved_cursor, kCGMouseButtonLeft)
            if restore is not None:
                CGEventSetFlags(restore, 0)
                CGEventPost(kCGHIDEventTap, restore)

    def edit_action(self, action, focused):
        if get_attr(self.app, "AXFocusedUIElement") != focused:
            raise BotError("Le champ WhatsApp ciblé a perdu le focus.")
        titles = {
            "select_all": ("Select All", "Tout sélectionner", "Sélectionner tout"),
            "paste": ("Paste", "Coller"),
        }[action]
        bar = get_attr(self.app, "AXMenuBar")
        edits = [e for e in children(bar) if clean(get_attr(e, "AXTitle")) in ("Edit", "Édition")]
        if len(edits) != 1:
            raise BotError("Menu Édition WhatsApp absent ou ambigu.")
        # AppKit actualise AXEnabled lorsque le menu s'ouvre.
        press(edits[0])
        try:
            entries = self.find(lambda e: get_attr(e, "AXRole") == "AXMenuItem" and clean(get_attr(e, "AXTitle")) in titles, edits[0])
            if len(entries) != 1:
                raise BotError(f"Action Édition {action} absente ou ambiguë.")
            press(entries[0])
        finally:
            AXUIElementPerformAction(bar, "AXCancel")

    def send_action(self):
        bar = get_attr(self.app, "AXMenuBar")
        chats = [e for e in children(bar) if clean(get_attr(e, "AXTitle")) in ("Chat", "Discussion", "Conversation")]
        if len(chats) != 1:
            raise BotError("Menu Discussion WhatsApp absent ou ambigu.")
        press(chats[0])
        try:
            entries = self.find(lambda e: get_attr(e, "AXRole") == "AXMenuItem" and clean(get_attr(e, "AXTitle")) in ("Send", "Envoyer"), chats[0])
            if len(entries) != 1:
                raise BotError("Action Envoyer WhatsApp absente ou ambiguë.")
            press(entries[0])
        finally:
            AXUIElementPerformAction(bar, "AXCancel")

    def paste(self, element, text, *, replace=False):
        press(element)
        time.sleep(0.15)
        if get_attr(self.app, "AXFocusedUIElement") != element:
            self.click(element)
            wait_for(lambda: get_attr(self.app, "AXFocusedUIElement") == element, "focus du champ ciblé", timeout=2)
        # AXSet peut retourner succès sans effet sur les champs Catalyst.
        # Le collage est vérifié par lecture du champ et conserve toutes les données du presse-papiers.
        board = NSPasteboard.generalPasteboard()
        originals = []
        for item in board.pasteboardItems() or []:
            copied = NSPasteboardItem.alloc().init()
            for kind in item.types():
                data = item.dataForType_(kind)
                if data is not None:
                    copied.setData_forType_(data, kind)
            originals.append(copied)
        board.clearContents()
        board.setString_forType_(text, "public.utf8-plain-text")
        own_count = board.changeCount()
        try:
            if replace and clean(get_attr(element, "AXValue")):
                self.edit_action("select_all", element)
            self.edit_action("paste", element)
            wait_for(lambda: clean(get_attr(element, "AXValue")) == clean(text), "vérification du texte collé", timeout=4)
        finally:
            if board.changeCount() == own_count:
                board.clearContents()
                if originals:
                    board.writeObjects_(originals)

    def composer(self):
        composer = self.identifier("ChatBar_ComposerTextView")
        if get_attr(composer, "AXRole") != "AXTextArea":
            raise BotError("Le contrôle de saisie n'est pas un champ de message.")
        return composer

    def conversation(self, group):
        composer = self.composer()
        node = get_attr(composer, "AXParent")
        # Trouver la branche qui réunit le champ de saisie et le titre exact,
        # sans accepter un résultat homonyme situé dans la liste des discussions.
        for _ in range(18):
            if node is None or get_attr(node, "AXRole") in ("AXWindow", "AXApplication"):
                break
            headings = self.find(lambda e: get_attr(e, "AXIdentifier") == "NavigationBar_HeaderViewButton" and clean(get_attr(e, "AXDescription")) == clean(group), node)
            lists = self.find(lambda e: get_attr(e, "AXIdentifier") in ("ChatListView_TableView", "ChatListSearchView_ChatResult"), node)
            if len(headings) == 1 and not lists:
                return node
            node = get_attr(node, "AXParent")
        raise BotError(f"Le titre du chat actif n'est pas exactement {group!r}; aucun envoi.")

    def open_group(self, group):
        nonempty(group, "group")
        # Toujours rechercher pour détecter les homonymes, même si le chat est déjà visible.
        search = self.identifier("TokenizedSearchBar_TextView")
        self.paste(search, group, replace=True)
        time.sleep(0.8)  # Attendre la recherche différée avant de compter les résultats.
        def results():
            return self.find(lambda e: get_attr(e, "AXIdentifier") == "ChatListSearchView_ChatResult" and clean(get_attr(e, "AXDescription")) == clean(group))
        matches = wait_for(results, f"résultat exact {group!r}")
        time.sleep(0.5)
        matches = results()
        if len(matches) != 1:
            raise BotError(f"{len(matches)} conversations portent le nom {group!r}; sélection refusée.")
        press(matches[0])
        time.sleep(0.5)
        # Les contrôles absents ou inattendus échouent; aucun premier résultat arbitraire.
        self.conversation(group)
        return self.composer()

    def outgoing_count(self, group, message):
        root = self.conversation(group)
        prefixes = ("Your message, ", "Votre message, ")
        forms = {clean(message), displayed_message(message)}
        count = 0
        for element in self.walk(root):
            if get_attr(element, "AXIdentifier") != "WAMessageBubbleTableViewCell":
                continue
            desc = clean(get_attr(element, "AXDescription"))
            if any(desc.startswith(prefix + text + ", ") for prefix in prefixes for text in forms) and re.search(r", (?:Sent|Delivered|Read|Envoyé|Distribué|Lu)\s*$", desc):
                count += 1
        return count

    def send(self, group, message, *, before_send):
        nonempty(message, "message")
        composer = self.open_group(group)
        if clean(get_attr(composer, "AXValue")).strip():
            raise BotError("Un brouillon existe déjà dans ce groupe; il est conservé.")
        before = self.outgoing_count(group, message)
        self.paste(composer, message)
        self.conversation(group)
        if clean(get_attr(composer, "AXValue")) != clean(message):
            raise BotError("Le contenu du message a changé; aucun envoi.")
        send_buttons = self.find(lambda e: get_attr(e, "AXRole") == "AXButton" and clean(get_attr(e, "AXDescription") or get_attr(e, "AXTitle")) in ("Send", "Envoyer"), self.conversation(group))
        if len(send_buttons) > 1:
            raise BotError("Bouton d'envoi ambigu.")
        before_send()  # État durable AVANT toute action pouvant envoyer.
        self.conversation(group)
        if send_buttons:
            press(send_buttons[0])
        else:
            self.send_action()
        wait_for(lambda: self.outgoing_count(group, message) > before, "nouveau message sortant avec statut envoyé", timeout=15)
        if clean(get_attr(composer, "AXValue")).strip():
            raise BotError("Le champ n'est pas vide après l'envoi; résultat à vérifier.")


def send_message(group_name, message, *, before_send):
    WhatsApp().send(group_name, message, before_send=before_send)
