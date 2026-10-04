"""Tests de l'adaptateur avec arbre AX fictif: aucun événement réel."""
import sys
import unittest
from unittest.mock import MagicMock, patch

from birthday_bot import BotError
if sys.platform == "darwin":
    import whatsapp_ax as ax


def node(role="AXGroup", ident=None, desc=None, value=None, kids=()):
    result = {"AXRole": role, "AXIdentifier": ident, "AXDescription": desc,
              "AXValue": value, "AXChildren": list(kids), "AXEnabled": True}
    for child in kids:child["AXParent"] = result
    return result


@unittest.skipUnless(sys.platform == "darwin", "PyObjC nécessite macOS")
class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.attr = patch("whatsapp_ax.get_attr", side_effect=lambda e, key: e.get(key) if e is not None else None)
        self.attr.start(); self.addCleanup(self.attr.stop)
        self.sleep = patch("whatsapp_ax.time.sleep");self.sleep.start();self.addCleanup(self.sleep.stop)
        self.client = object.__new__(ax.WhatsApp)
        self.composer = node("AXTextArea", "ChatBar_ComposerTextView", value="")
        self.header = node("AXButton", "NavigationBar_HeaderViewButton", "Test")
        self.bubble = node("AXStaticText", "WAMessageBubbleTableViewCell", "Your message, Bonjour 🎂, 08:00, Sent to Test, Pending")
        self.chat = node(kids=[self.header, self.bubble, self.composer])
        self.window = node("AXWindow", kids=[self.chat])
        self.client.app = node("AXApplication", kids=[self.window])
        self.client.app["AXFocusedUIElement"] = self.composer
        self.client.window = lambda: self.window

    def test_header_must_be_exact(self):
        self.assertIs(self.client.conversation("Test"), self.chat)
        with self.assertRaises(BotError):self.client.conversation("Tes")
        with self.assertRaises(BotError):self.client.conversation("test")

    def test_sidebar_homonym_cannot_validate_active_chat(self):
        self.header["AXDescription"] = "Other group"
        sidebar_header = node("AXButton", "NavigationBar_HeaderViewButton", "Test")
        sidebar_list = node(ident="ChatListView_TableView")
        self.window["AXChildren"].append(node(kids=[sidebar_header, sidebar_list]))
        with self.assertRaises(BotError):self.client.conversation("Test")

    def test_ambiguous_composer_refused(self):
        self.chat["AXChildren"].append(node("AXTextArea", "ChatBar_ComposerTextView"))
        with self.assertRaises(BotError):self.client.composer()

    def test_pending_bubble_is_not_confirmed_by_sent_to(self):
        self.assertEqual(self.client.outgoing_count("Test", "Bonjour 🎂"), 0)
        self.bubble["AXDescription"] = "Your message, Bonjour 🎂, 08:00, Sent to Test, Sent"
        self.assertEqual(self.client.outgoing_count("Test", "Bonjour 🎂"), 1)
        self.assertEqual(self.client.outgoing_count("Test", "Bonjour"), 0)
        self.bubble["AXDescription"] = "Message from Someone, Bonjour 🎂, 08:00, Received"
        self.assertEqual(self.client.outgoing_count("Test", "Bonjour 🎂"), 0)

    def test_formatted_multiline_message_matches_rendered_accessibility_text(self):
        message = "*_Bot de Wolf 🤖_ (Ceci est un message automatique)*\n\nJoyeux anniversaire Test 🎉🎂 !"
        rendered = "Bot de Wolf 🤖 (Ceci est un message automatique)\n\nJoyeux anniversaire Test 🎉🎂 !"
        self.bubble["AXDescription"] = f"Your message, {rendered}, 17:34, Sent to Test, Sent"
        self.assertEqual(self.client.outgoing_count("Test", message), 1)
        self.assertEqual(self.client.outgoing_count("Test", message.replace("anniversaire", "anniversaires")), 0)
        self.bubble["AXDescription"] = f"Your message, {rendered}, 17:34, Sent to Test, Pending"
        self.assertEqual(self.client.outgoing_count("Test", message), 0)

    def test_literal_markers_and_internal_underscores_preserved(self):
        self.assertEqual(ax.displayed_message("a_b_c * isolé"), "a_b_c * isolé")
        self.assertEqual(ax.displayed_message("*_Bonjour_*"), "Bonjour")

    def test_draft_is_preserved_without_reserving_or_sending(self):
        self.composer["AXValue"] = "Mon brouillon"
        self.client.open_group = lambda group: self.composer
        with patch.object(self.client, "paste") as paste, patch("whatsapp_ax.press") as press:
            reserve = MagicMock()
            with self.assertRaises(BotError):self.client.send("Test", "Message", before_send=reserve)
            paste.assert_not_called();press.assert_not_called();reserve.assert_not_called()
        self.assertEqual(self.composer["AXValue"], "Mon brouillon")

    def test_changed_recipient_blocks_send_before_reservation(self):
        self.client.open_group = lambda group: self.composer
        def paste(element, message):
            element["AXValue"] = message
            self.header["AXDescription"] = "Other group"
        self.client.paste = paste
        with patch("whatsapp_ax.press") as press:
            reserve = MagicMock()
            with self.assertRaises(BotError):self.client.send("Test", "Message", before_send=reserve)
            press.assert_not_called();reserve.assert_not_called()

    def test_azerty_paste_uses_edit_menu_without_virtual_shortcuts(self):
        board = MagicMock();board.pasteboardItems.return_value = [];board.changeCount.return_value = 1
        actions = []
        self.composer["AXValue"] = "Existing search"
        def edit_action(action, focused):
            actions.append(action)
            if action == "paste":focused["AXValue"] = "Test"
        self.client.edit_action = edit_action
        with patch("whatsapp_ax.NSPasteboard") as clipboard, patch("whatsapp_ax.press"), patch("whatsapp_ax.CGEventPost") as event:
            clipboard.generalPasteboard.return_value = board
            self.client.paste(self.composer, "Test", replace=True)
            event.assert_not_called()
            self.assertEqual(actions, ["select_all", "paste"])
            self.assertEqual(board.clearContents.call_count, 2)

    def test_user_clipboard_change_is_not_overwritten(self):
        board = MagicMock();board.pasteboardItems.return_value = [];board.changeCount.side_effect = [1, 2]
        self.client.edit_action = lambda action, focused: focused.update(AXValue="Test")
        with patch("whatsapp_ax.NSPasteboard") as clipboard, patch("whatsapp_ax.press"):
            clipboard.generalPasteboard.return_value = board
            self.client.paste(self.composer, "Test")
            self.assertEqual(board.clearContents.call_count, 1)

    def test_direction_mark_cleanup_preserves_emoji_and_accents(self):
        self.assertEqual(ax.clean("\u200eÉlodie 👩‍💻"), "Élodie 👩‍💻")


if __name__ == "__main__":unittest.main()
