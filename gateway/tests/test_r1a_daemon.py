"""R1a — TDLib daemon response shaping (synthetic TDLib dicts only, no tdjson)."""

import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support

DAEMON_PATH = support.REPO / "gateway" / "tdlib-daemon" / "telegram-tdlib-daemon.py"


def load_daemon(name="town_test_tdlib_daemon"):
    # Force tdjson absent even on an interpreter that has it installed.
    with mock.patch.dict(sys.modules, {"tdjson": None}):
        return support.load_module(DAEMON_PATH, name)


daemon = load_daemon()

CHAT_ID = -100500
ALICE, BOB, CAROL = 1001, 1002, 1003
NAMES = {ALICE: "Alice Example", BOB: "Bob Example", CAROL: "Carol Example"}


def user_name(user_id):
    return NAMES.get(user_id, str(user_id))


def no_fetch(chat_id, message_id):
    raise AssertionError(f"unexpected getMessage({chat_id}, {message_id})")


def no_name(user_id):
    raise AssertionError(f"unexpected getUser({user_id})")


def u16(text):
    return len(text.encode("utf-16-le")) // 2


def mention_entity(text, span):
    start = text.index(span)
    return {
        "@type": "textEntity",
        "offset": u16(text[:start]),
        "length": u16(span),
        "type": {"@type": "textEntityTypeMention"},
    }


def mention_name_entity(text, span, user_id):
    entity = mention_entity(text, span)
    entity["type"] = {"@type": "textEntityTypeMentionName", "user_id": user_id}
    return entity


def formatted(text, entities=()):
    return {"@type": "formattedText", "text": text, "entities": list(entities)}


def user_sender(user_id):
    return {"@type": "messageSenderUser", "user_id": user_id}


def text_message(message_id, text, sender=ALICE, entities=(), **extra):
    message = {
        "@type": "message",
        "id": message_id,
        "chat_id": CHAT_ID,
        "date": 1700000000 + message_id,
        "is_outgoing": False,
        "sender_id": user_sender(sender),
        "content": {"@type": "messageText", "text": formatted(text, entities)},
    }
    message.update(extra)
    return message


def reply_to(message_id, chat_id=CHAT_ID):
    return {"@type": "messageReplyToMessage", "chat_id": chat_id, "message_id": message_id}


def chat(kind_type, title="Weekend Crew"):
    return {"@type": "chat", "id": CHAT_ID, "title": title, "type": kind_type}


def tdlib_user(user_id, first, last="", bot=False, usernames=None):
    user = {
        "@type": "user",
        "id": user_id,
        "first_name": first,
        "last_name": last,
        "type": {"@type": "userTypeBot" if bot else "userTypeRegular"},
    }
    if usernames is not None:
        user["usernames"] = {
            "@type": "usernames",
            "active_usernames": usernames,
            "disabled_usernames": [],
            "editable_username": usernames[0] if usernames else "",
        }
    return user


class ChatShapeTest(unittest.TestCase):
    def test_chat_type_maps_every_tdlib_kind(self):
        cases = [
            ({"@type": "chatTypePrivate", "user_id": ALICE}, "private"),
            ({"@type": "chatTypeBasicGroup", "basic_group_id": 7}, "group"),
            ({"@type": "chatTypeSupergroup", "supergroup_id": 8, "is_channel": False}, "supergroup"),
            ({"@type": "chatTypeSupergroup", "supergroup_id": 9, "is_channel": True}, "channel"),
            ({"@type": "chatTypeSecret", "secret_chat_id": 3, "user_id": BOB}, "secret"),
            ({"@type": "chatTypeSomethingNew"}, "unknown"),
            (None, "unknown"),
        ]
        for chat_type, expected in cases:
            with self.subTest(expected=expected):
                entry = daemon.shape_chat(CHAT_ID, chat(chat_type))
                self.assertEqual(entry["type"], expected)
                self.assertEqual(list(entry)[:2], ["id", "title"])
                self.assertEqual((entry["id"], entry["title"]), (CHAT_ID, "Weekend Crew"))

    def test_error_chat_keeps_old_unknown_title(self):
        entry = daemon.shape_chat(CHAT_ID, {"@type": "error", "message": "timeout"})
        self.assertEqual(entry, {"id": CHAT_ID, "title": "Unknown", "type": "unknown"})

    def test_private_chat_adds_user_id_and_is_bot_via_lookup(self):
        users = {ALICE: tdlib_user(ALICE, "Alice"), BOB: tdlib_user(BOB, "Helper", bot=True)}
        alice = daemon.shape_chat(1, chat({"@type": "chatTypePrivate", "user_id": ALICE}), users.get)
        helper = daemon.shape_chat(2, chat({"@type": "chatTypePrivate", "user_id": BOB}), users.get)
        missing = daemon.shape_chat(3, chat({"@type": "chatTypePrivate", "user_id": CAROL}), users.get)
        self.assertEqual((alice["user_id"], alice["is_bot"]), (ALICE, False))
        self.assertEqual((helper["user_id"], helper["is_bot"]), (BOB, True))
        self.assertEqual(missing["user_id"], CAROL)
        self.assertNotIn("is_bot", missing)
        self.assertNotIn("member_count", alice)

    def test_member_count_only_when_known(self):
        groups = {
            ("basicGroup", 7): {"@type": "basicGroup", "id": 7, "member_count": 5},
            ("supergroup", 8): {"@type": "supergroup", "id": 8, "member_count": 0},
            ("supergroup", 9): {"@type": "supergroup", "id": 9, "member_count": 1200},
        }
        lookup = lambda kind, group_id: groups.get((kind, group_id))
        basic = daemon.shape_chat(1, chat({"@type": "chatTypeBasicGroup", "basic_group_id": 7}), None, lookup)
        unknown = daemon.shape_chat(2, chat({"@type": "chatTypeSupergroup", "supergroup_id": 8}), None, lookup)
        channel = daemon.shape_chat(3, chat({"@type": "chatTypeSupergroup", "supergroup_id": 9, "is_channel": True}), None, lookup)
        no_lookup = daemon.shape_chat(4, chat({"@type": "chatTypeBasicGroup", "basic_group_id": 7}))
        self.assertEqual(basic["member_count"], 5)
        self.assertNotIn("member_count", unknown)
        self.assertEqual((channel["type"], channel["member_count"]), ("channel", 1200))
        self.assertNotIn("member_count", no_lookup)


class MediaAndMentionTest(unittest.TestCase):
    def test_photo_caption_with_mentions(self):
        caption = "🎉🎉 snaps from Saturday @Carol_Example — thanks Bob!"
        message = {
            "@type": "message",
            "id": 40,
            "chat_id": CHAT_ID,
            "date": 1700000040,
            "is_outgoing": False,
            "sender_id": user_sender(ALICE),
            "content": {
                "@type": "messagePhoto",
                "photo": {"@type": "photo", "sizes": []},
                "caption": formatted(caption, [
                    mention_entity(caption, "@Carol_Example"),
                    mention_name_entity(caption, "Bob", BOB),
                ]),
            },
        }
        entry = daemon.shape_message(message, user_name)
        self.assertEqual(entry["text"], "")
        self.assertEqual(entry["content_type"], "photo")
        self.assertEqual(entry["caption"], caption)
        self.assertEqual(entry["mention_usernames"], ["carol_example"])
        self.assertEqual(entry["mention_user_ids"], [BOB])

    def test_text_mentions_after_non_bmp_emoji(self):
        text = "👋🏽 @Bob_Example can you and @carol_example sync with @bob_example?"
        message = text_message(41, text, entities=[
            mention_entity(text, "@Bob_Example"),
            mention_entity(text, "@carol_example"),
            mention_entity(text, "@bob_example"),
        ])
        entry = daemon.shape_message(message, user_name)
        self.assertEqual(entry["text"], text)
        self.assertEqual(entry["content_type"], "text")
        self.assertNotIn("caption", entry)
        self.assertEqual(entry["mention_usernames"], ["bob_example", "carol_example"])
        self.assertEqual(entry["mention_user_ids"], [])

    def test_content_types(self):
        cases = {
            "messageVideo": "video",
            "messageVoiceNote": "voice",
            "messageVideoNote": "video_note",
            "messageSticker": "sticker",
            "messageAnimation": "animation",
            "messageDocument": "document",
            "messageAudio": "audio",
            "messagePoll": "poll",
            "messageLocation": "location",
            "messageContact": "contact",
            "messageChatAddMembers": "other",
        }
        for tdlib_type, expected in cases.items():
            with self.subTest(tdlib_type=tdlib_type):
                message = text_message(42, "", content={"@type": tdlib_type})
                entry = daemon.shape_message(message, user_name)
                self.assertEqual(entry["content_type"], expected)
                self.assertEqual(entry["text"], "")
                self.assertNotIn("caption", entry)
                self.assertEqual((entry["mention_user_ids"], entry["mention_usernames"]), ([], []))

    def test_empty_caption_is_omitted(self):
        message = text_message(43, "", content={"@type": "messageVoiceNote", "caption": formatted("")})
        self.assertNotIn("caption", daemon.shape_message(message, user_name))


class ReplyResolutionTest(unittest.TestCase):
    def test_target_in_batch_needs_no_lookup(self):
        msgs = [
            text_message(11, "Sounds good", sender=CAROL, reply_to=reply_to(10)),
            text_message(10, "Dinner Friday?", sender=BOB),
        ]
        entries = daemon.shape_messages(msgs, CHAT_ID, user_name, no_fetch)
        self.assertEqual(entries[0]["reply_to_message_id"], 10)
        self.assertEqual(entries[0]["reply_to_sender_user_id"], BOB)
        self.assertEqual(entries[0]["reply_to_sender_name"], "Bob Example")
        self.assertNotIn("reply_to_message_id", entries[1])

    def test_missing_target_costs_exactly_one_lookup(self):
        calls = []

        def fetch(chat_id, message_id):
            calls.append((chat_id, message_id))
            return text_message(message_id, "Earlier note", sender=BOB)

        msgs = [
            text_message(21, "Agreed", sender=ALICE, reply_to=reply_to(5)),
            text_message(20, "Same here", sender=CAROL, reply_to=reply_to(5)),
        ]
        entries = daemon.shape_messages(msgs, CHAT_ID, user_name, fetch)
        self.assertEqual(calls, [(CHAT_ID, 5)])
        for entry in entries:
            self.assertEqual(entry["reply_to_message_id"], 5)
            self.assertEqual(entry["reply_to_sender_user_id"], BOB)
            self.assertEqual(entry["reply_to_sender_name"], "Bob Example")

    def test_lookup_cap_is_honored(self):
        calls = []

        def fetch(chat_id, message_id):
            calls.append(message_id)
            return text_message(message_id, "old", sender=BOB)

        msgs = [text_message(1000 + i, "re", reply_to=reply_to(i + 1)) for i in range(25)]
        entries = daemon.shape_messages(msgs, CHAT_ID, user_name, fetch)
        self.assertEqual(len(calls), daemon.MAX_REPLY_LOOKUPS)
        self.assertEqual(daemon.MAX_REPLY_LOOKUPS, 20)
        resolved = [e for e in entries if "reply_to_sender_user_id" in e]
        self.assertEqual(len(resolved), 20)
        self.assertTrue(all("reply_to_message_id" in e for e in entries))

    def test_unresolvable_targets_keep_only_the_id(self):
        def failing(chat_id, message_id):
            if message_id == 2:
                raise RuntimeError("tdlib error")
            return None

        msgs = [
            text_message(31, "a", reply_to=reply_to(1)),
            text_message(32, "b", reply_to=reply_to(2)),
            text_message(33, "c", reply_to=reply_to(3, chat_id=-999)),
            text_message(34, "d", reply_to={"@type": "messageReplyToStory", "story_id": 1}),
        ]
        entries = daemon.shape_messages(msgs, CHAT_ID, user_name, failing)
        self.assertEqual(entries[0]["reply_to_message_id"], 1)
        self.assertEqual(entries[1]["reply_to_message_id"], 2)
        for entry in entries[:2]:
            self.assertNotIn("reply_to_sender_user_id", entry)
            self.assertNotIn("reply_to_sender_name", entry)
        for entry in entries[2:]:
            self.assertNotIn("reply_to_message_id", entry)


class SenderTest(unittest.TestCase):
    def test_outgoing_flag_passthrough(self):
        out = daemon.shape_message(text_message(50, "On my way", is_outgoing=True), user_name)
        inc = daemon.shape_message(text_message(51, "See you", sender=BOB), user_name)
        self.assertIs(out["is_outgoing"], True)
        self.assertIs(inc["is_outgoing"], False)

    def test_channel_post_reports_sender_chat(self):
        message = text_message(52, "Weekly digest is out", sender_id={"@type": "messageSenderChat", "chat_id": CHAT_ID})
        entry = daemon.shape_message(message, no_name)
        self.assertEqual(entry["sender_chat_id"], CHAT_ID)
        self.assertNotIn("sender_user_id", entry)
        self.assertNotIn("sender_name", entry)


class BackCompatTest(unittest.TestCase):
    OLD_KEYS = ["id", "date", "text", "sender_user_id", "sender_name"]

    def test_plain_text_message_keeps_old_shape(self):
        message = text_message(60, "Lunch at noon?", sender=ALICE)
        entry = daemon.shape_messages([message], CHAT_ID, user_name, no_fetch)[0]
        old = {
            "id": 60,
            "date": 1700000060,
            "text": "Lunch at noon?",
            "sender_user_id": ALICE,
            "sender_name": "Alice Example",
        }
        self.assertEqual(list(entry)[:5], self.OLD_KEYS)
        self.assertEqual({key: entry[key] for key in self.OLD_KEYS}, old)
        self.assertEqual(
            set(entry) - set(self.OLD_KEYS),
            {"is_outgoing", "content_type", "mention_user_ids", "mention_usernames"},
        )

    def test_old_sender_name_fallbacks(self):
        self.assertEqual(daemon.user_display_name(None, ALICE), str(ALICE))
        self.assertEqual(daemon.user_display_name({"@type": "error"}, ALICE), str(ALICE))
        self.assertEqual(daemon.user_display_name(tdlib_user(ALICE, ""), ALICE), str(ALICE))
        self.assertEqual(daemon.user_display_name(tdlib_user(ALICE, "Alice", "Example"), ALICE), "Alice Example")


class MeShapeTest(unittest.TestCase):
    def test_me_adds_username_and_usernames(self):
        me = daemon.shape_me(tdlib_user(ALICE, "Alice", "Example", usernames=["Alice_Example", "alice_alt"]))
        self.assertEqual(me, {
            "user_id": ALICE,
            "first_name": "Alice",
            "last_name": "Example",
            "username": "Alice_Example",
            "usernames": ["Alice_Example", "alice_alt"],
        })

    def test_me_without_usernames(self):
        me = daemon.shape_me(tdlib_user(ALICE, "Alice"))
        self.assertEqual((me["username"], me["usernames"]), ("", []))
        legacy = daemon.shape_me({**tdlib_user(ALICE, "Alice"), "username": "alice_example"})
        self.assertEqual((legacy["username"], legacy["usernames"]), ("alice_example", ["alice_example"]))


class ImportIsolationTest(unittest.TestCase):
    def test_imports_without_tdjson_or_touching_home(self):
        root = logging.getLogger()
        handlers_before = list(root.handlers)
        with tempfile.TemporaryDirectory() as tmp:
            env = {"HOME": tmp, "HERMES_HOME": str(Path(tmp) / ".hermes")}
            with mock.patch.dict(os.environ, env):
                module = load_daemon("town_test_tdlib_daemon_isolated")
                self.assertEqual(module.log_path(), Path(tmp) / ".hermes" / "logs" / "tdlib-daemon.log")
            self.assertIsNone(module.tdjson)
            self.assertEqual(module.SESSION_DIR, Path(tmp) / ".tdlib_daemon")
            self.assertEqual(list(Path(tmp).iterdir()), [])
        self.assertEqual(root.handlers, handlers_before)
        self.assertEqual((module.HTTP_PORT, module.SESSION_DIR.name), (18784, ".tdlib_daemon"))


class FakeClient:
    def __init__(self, chats=None, history=None, users=None, groups=None, stored=None, me=None):
        self.chats = chats or {}
        self.history = history or []
        self.users = users or {}
        self.groups = groups or {}
        self.stored = stored or {}
        self.me = me
        self.calls = []

    def get_chats(self, limit=100):
        return list(self.chats)

    def get_chat(self, chat_id):
        return self.chats[chat_id]

    def get_messages(self, chat_id, from_message_id=0, limit=50, cutoff_ts=0):
        self.calls.append(("history", from_message_id))
        return self.history

    def send(self, data, timeout=30.0):
        kind = data["@type"]
        self.calls.append((kind, timeout))
        missing = {"@type": "error", "code": 404, "message": "Not Found"}
        if kind == "getMe":
            return self.me
        if kind == "getUser":
            return self.users.get(data["user_id"], missing)
        if kind == "getBasicGroup":
            return self.groups.get(("basicGroup", data["basic_group_id"]), missing)
        if kind == "getSupergroup":
            return self.groups.get(("supergroup", data["supergroup_id"]), missing)
        if kind == "getMessage":
            return self.stored.get(data["message_id"], missing)
        raise AssertionError(f"unexpected request {data}")


class FakeSyncState:
    def __init__(self):
        self.state = {}
        self.saved = []

    def get_last_sync(self, chat_id):
        return 77

    def set_last_sync(self, chat_id, message_id):
        self.saved.append((chat_id, message_id))


class HandlerTest(unittest.TestCase):
    def setUp(self):
        self.saved_globals = (daemon._client, daemon._sync_state)
        daemon._user_cache.clear()
        daemon._sync_state = FakeSyncState()

    def tearDown(self):
        daemon._client, daemon._sync_state = self.saved_globals
        daemon._user_cache.clear()

    def get(self, path):
        handler = daemon.RequestHandler.__new__(daemon.RequestHandler)
        handler.path = path
        replies = []
        handler._json = lambda data, status=200: replies.append((status, data))
        handler.do_GET()
        self.assertEqual(len(replies), 1)
        return replies[0]

    def test_messages_sync_state_side_effect_preserved(self):
        daemon._client = FakeClient(
            users={BOB: tdlib_user(BOB, "Bob", "Example"), ALICE: tdlib_user(ALICE, "Alice")},
            history=[text_message(91, "ok", reply_to=reply_to(90)), text_message(90, "ping?", sender=BOB)],
        )
        status, body = self.get(f"/messages?chat_id={CHAT_ID}&limit=5&from_message_id=0")
        self.assertEqual(status, 200)
        self.assertEqual(daemon._sync_state.saved, [])
        self.assertEqual(daemon._client.calls[0], ("history", 0))
        self.assertEqual(body["messages"][0]["reply_to_sender_name"], "Bob Example")
        self.assertNotIn(("getMessage", daemon.REPLY_LOOKUP_TIMEOUT), daemon._client.calls)

        status, _ = self.get(f"/messages?chat_id={CHAT_ID}&limit=5")
        self.assertEqual(status, 200)
        self.assertEqual(daemon._client.calls[-1], ("history", 77))
        self.assertEqual(daemon._sync_state.saved, [(CHAT_ID, 91)])

    def test_reply_lookup_goes_through_get_message(self):
        daemon._client = FakeClient(
            users={CAROL: tdlib_user(CAROL, "Carol"), ALICE: tdlib_user(ALICE, "Alice")},
            history=[text_message(95, "yes", reply_to=reply_to(3))],
            stored={3: text_message(3, "who's in?", sender=CAROL)},
        )
        _, body = self.get(f"/messages?chat_id={CHAT_ID}&from_message_id=0")
        entry = body["messages"][0]
        self.assertEqual((entry["reply_to_sender_user_id"], entry["reply_to_sender_name"]), (CAROL, "Carol"))
        self.assertEqual(daemon._client.calls.count(("getMessage", daemon.REPLY_LOOKUP_TIMEOUT)), 1)

    def test_chats_and_muted_chats_are_enriched(self):
        muted = {"@type": "chatNotificationSettings", "mute_for": 3600}
        daemon._client = FakeClient(
            chats={
                1: {**chat({"@type": "chatTypePrivate", "user_id": BOB}, "Bob Example"), "notification_settings": muted},
                2: chat({"@type": "chatTypeBasicGroup", "basic_group_id": 7}),
            },
            users={BOB: tdlib_user(BOB, "Bob", bot=True)},
            groups={("basicGroup", 7): {"@type": "basicGroup", "id": 7, "member_count": 4}},
        )
        _, body = self.get("/chats")
        self.assertEqual(body["chats"], [
            {"id": 1, "title": "Bob Example", "type": "private", "user_id": BOB, "is_bot": True},
            {"id": 2, "title": "Weekend Crew", "type": "group", "member_count": 4},
        ])
        _, body = self.get("/muted-chats")
        self.assertEqual(body["muted_chats"], [
            {"id": 1, "title": "Bob Example", "type": "private", "user_id": BOB, "is_bot": True},
        ])
        self.assertEqual(daemon._client.calls.count(("getUser", 30.0)), 1)

    def test_me_endpoint(self):
        daemon._client = FakeClient(me=tdlib_user(ALICE, "Alice", "Example", usernames=["alice_example"]))
        status, body = self.get("/me")
        self.assertEqual(status, 200)
        self.assertEqual((body["username"], body["usernames"]), ("alice_example", ["alice_example"]))


if __name__ == "__main__":
    unittest.main()
