"""R1b — Telegram attribution: no fake or already-handled action items.

Synthetic daemon-shaped payloads only (with and without the R1a fields); no
network, no LLM, no live state, no wall clock (``NOW`` and the timezone are
injected).
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import support

common = support.load_module(
    support.SCRIPTS / "town_telegram_common.py", "town_test_telegram_common"
)
scan = support.load_script("town-telegram-scan.py")

_DIGEST_HOME = tempfile.mkdtemp(prefix="town-r1b-")


def load_digest():
    """Import the digest with hermes_bootstrap/agent stubbed (no side effects,
    no LLM) and a fake bot token (no live .env read)."""
    aux = types.ModuleType("agent.auxiliary_client")
    aux.call_llm = mock.Mock(side_effect=AssertionError("unexpected LLM call"))
    agent = types.ModuleType("agent")
    agent.auxiliary_client = aux
    stubs = {
        "hermes_bootstrap": types.ModuleType("hermes_bootstrap"),
        "agent": agent,
        "agent.auxiliary_client": aux,
    }
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        env = {"TELEGRAM_BOT_TOKEN": "999000:TEST", "HERMES_HOME": _DIGEST_HOME}
        with mock.patch.dict(os.environ, env):
            return support.load_script("telegram-action-items-v2.py")
    finally:
        for name, prior in saved.items():
            if prior is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = prior


digest = load_digest()


def tearDownModule():
    import shutil

    shutil.rmtree(_DIGEST_HOME, ignore_errors=True)


TZ = timezone(timedelta(hours=-4), "EDT")
NOW = datetime(2026, 10, 11, 18, 0, tzinfo=timezone.utc)  # Sun 14:00 EDT
BASE = int(datetime(2026, 10, 11, 14, 0, 5, tzinfo=timezone.utc).timestamp())  # 10:00:05 EDT

ALICE, BOB, CAROL, DAVE = 1001, 2001, 2002, 2003
NAMES = {
    ALICE: "Alice Example",
    BOB: "Bob Example",
    CAROL: "Carol Example",
    DAVE: "Dave Example",
}
ME = {
    "user_id": ALICE,
    "first_name": "Alice",
    "last_name": "Example",
    "username": "alice_example",
    "usernames": ["alice_example"],
}
ME_OLD = {"user_id": ALICE, "first_name": "Alice", "last_name": "Example"}

DM_BOB = {"id": BOB, "title": "Bob Example", "type": "private", "user_id": BOB, "is_bot": False}
GROUP_ID = -1001500000001
GROUP = {"id": GROUP_ID, "title": "Weekend Crew", "type": "supergroup", "member_count": 6}


def msg(message_id, offset, text="", sender=BOB, *, new=True, **extra):
    """A /messages entry; ``new=False`` yields the pre-R1a shape."""
    message = {
        "id": message_id,
        "date": BASE + offset,
        "text": text,
        "sender_user_id": sender,
        "sender_name": NAMES[sender],
    }
    if new:
        message.update(
            {
                "is_outgoing": sender == ALICE,
                "content_type": "text",
                "mention_user_ids": [],
                "mention_usernames": [],
            }
        )
    message.update(extra)
    return message


def newest_first(messages):
    return sorted(messages, key=lambda m: (m["date"], m["id"]), reverse=True)


def operator(me=ME, fallback="Alice Example"):
    return common.resolve_operator(me, fallback)


def analyze(chat, messages, me=ME):
    return common.analyze_chat(chat["id"], chat, newest_first(messages), operator(me))


def run_scanner(chats, messages, state_dir, me=ME, owner="Alice Example", state=None):
    """One scanner run against fake daemon helpers; returns (code, candidates, watermarks)."""
    state_dir = Path(state_dir)
    if state is not None:
        (state_dir / "telegram-state.json").write_text(json.dumps(state))
    with mock.patch.multiple(
        scan,
        STATE_PATH=state_dir / "telegram-state.json",
        LOCK_PATH=state_dir / ".telegram-state.lock",
        OWNER_NAME=owner,
        get_bot_token=lambda: "999000:TEST",
        get_me=lambda: me,
        get_chats=lambda bot_user_id: {chat["id"]: chat for chat in chats},
        get_messages=lambda chat_id, cutoff_ts: newest_first(messages[chat_id]),
    ):
        code, candidates = scan._run(NOW)
        saved = json.loads((state_dir / "telegram-state.json").read_text())
    return code, candidates, saved["watermarks"]


def digest_prompt(chats, messages, me=ME):
    op = digest.tg.resolve_operator(me, "Alice Example")
    cutoff_ts = int((NOW - timedelta(days=1)).timestamp())
    threads, _collected = digest.collect_threads(
        {chat["id"]: chat for chat in chats},
        lambda chat_id: newest_first(messages[chat_id]),
        op,
        cutoff_ts,
    )
    return digest.build_prompt(digest.render_sections(threads, tz=TZ), op, NOW, tz=TZ)


def bob_asks_carol_replies():
    return [
        msg(501, 0, "Has anyone booked the cabin yet?", BOB),
        msg(502, 60, "Not yet, resend your message with the dates", CAROL, reply_to_message_id=501,
            reply_to_sender_user_id=BOB, reply_to_sender_name=NAMES[BOB]),
        msg(503, 120, "ok will do", BOB, reply_to_message_id=502,
            reply_to_sender_user_id=CAROL, reply_to_sender_name=NAMES[CAROL]),
    ]


def same_minute_dm():
    return [
        msg(301, 0, "Are we still on for dinner at 7?", BOB),
        msg(302, 16, "Yes", ALICE),
    ]


class DigestHeaderTest(unittest.TestCase):
    def test_dm_header_marks_awaiting_reply_only_when_other_person_spoke_last(self):
        waiting = analyze(DM_BOB, [msg(401, 0, "Can you intro me to Carol?", BOB)])
        answered = analyze(DM_BOB, same_minute_dm())
        self.assertIn("[awaiting your reply]", digest.chat_header(waiting))
        self.assertNotIn("[awaiting your reply]", digest.chat_header(answered))
        self.assertIn("[operator replied after last ask]", digest.chat_header(answered))


class GroupAddressingTest(unittest.TestCase):
    def test_group_bob_asks_carol_replies_operator_silent_not_eligible(self):
        thread = analyze(GROUP, bob_asks_carol_replies())
        self.assertFalse(thread["eligible"])
        self.assertFalse(thread["addressed"])
        with tempfile.TemporaryDirectory() as tmp:
            code, candidates, watermarks = run_scanner(
                [GROUP], {GROUP_ID: bob_asks_carol_replies()}, tmp
            )
        self.assertEqual(code, 0)
        self.assertEqual(candidates, [])
        self.assertEqual(watermarks, {str(GROUP_ID): "503"})  # ineligible still advances
        prompt = digest_prompt(
            [GROUP, DM_BOB], {GROUP_ID: bob_asks_carol_replies(), BOB: same_minute_dm()}
        )
        self.assertNotIn("Weekend Crew", prompt)
        self.assertNotIn("booked the cabin", prompt)
        self.assertIn("=== Bob Example [DM with Bob Example]", prompt)

    def test_group_reply_to_operator_eligible_with_markers(self):
        messages = [
            msg(601, 0, "I can drive on Saturday", ALICE),
            msg(602, 30, "Great, can you pick me up at 9?", BOB, reply_to_message_id=601,
                reply_to_sender_user_id=ALICE, reply_to_sender_name=NAMES[ALICE]),
            msg(603, 60, "@alice_example bring the cooler too", CAROL,
                mention_usernames=["alice_example"]),
        ]
        thread = analyze(GROUP, messages)
        self.assertTrue(thread["eligible"])
        self.assertTrue(thread["addressed"])
        lines = [common.render_message(view, tz=TZ) for view in thread["messages"]]
        self.assertEqual(lines[0], "[2026-10-11 10:00] [YOU] I can drive on Saturday")
        self.assertIn("Bob Example ↩ you: Great, can you pick me up at 9?", lines[1])
        self.assertIn("Carol Example @you: @alice_example bring the cooler too", lines[2])
        with tempfile.TemporaryDirectory() as tmp:
            _code, candidates, _ = run_scanner([GROUP], {GROUP_ID: messages}, tmp)
        self.assertEqual(len(candidates), 1)
        candidate = candidates[0]
        self.assertEqual(candidate["chat_type"], "group")
        self.assertTrue(candidate["addressed_to_user"])
        self.assertFalse(candidate["operator_replied_after_ask"])
        self.assertFalse(candidate["user_spoke_last"])
        self.assertTrue(
            candidate["body"].startswith("[chat: Weekend Crew (group); you are Alice Example]")
        )
        self.assertIn("Bob Example ↩ you:", candidate["body"])
        self.assertIn("Carol Example @you:", candidate["body"])
        self.assertIn("[YOU] I can drive on Saturday", candidate["body"])

    def test_group_mention_by_user_id_is_addressed(self):
        messages = [msg(611, 0, "can you check the booking?", CAROL, mention_user_ids=[ALICE])]
        thread = analyze(GROUP, messages)
        self.assertTrue(thread["eligible"])
        self.assertTrue(thread["messages"][0]["mentions_you"])

    def test_group_first_name_whole_word(self):
        named = [msg(701, 0, "Maybe Alice knows the address?", BOB)]
        thread = analyze(GROUP, named)
        self.assertTrue(thread["eligible"])
        self.assertIn("Bob Example @you:", common.render_message(thread["messages"][0], tz=TZ))
        self.assertTrue(analyze(GROUP, [msg(702, 0, "what about alice?", BOB)])["eligible"])
        not_named = [msg(703, 0, "Ask Alicex about the address", BOB)]
        self.assertFalse(analyze(GROUP, not_named)["eligible"])
        handle_only = [msg(704, 0, "ping @alice_examples please", BOB)]
        self.assertFalse(analyze(GROUP, handle_only)["eligible"])

    def test_group_open_commitment_eligible_but_not_addressed(self):
        messages = [
            msg(801, 0, "Who has the photos from Saturday?", BOB),
            msg(802, 60, "I'll send them tonight", ALICE),
            msg(803, 120, "Cool, also what time is brunch", CAROL),
        ]
        thread = analyze(GROUP, messages)
        self.assertTrue(thread["eligible"])
        self.assertFalse(thread["addressed"])
        with tempfile.TemporaryDirectory() as tmp:
            _code, candidates, _ = run_scanner([GROUP], {GROUP_ID: messages}, tmp)
        self.assertFalse(candidates[0]["addressed_to_user"])
        # "let me know" is a request, not a commitment.
        request = [msg(811, 0, "Let me know when you land", ALICE), msg(812, 60, "Sure", BOB)]
        self.assertFalse(analyze(GROUP, request)["eligible"])
        # A reply to the promise acknowledges it (and addresses the operator).
        acked = messages[:2] + [
            msg(813, 90, "thanks!", BOB, reply_to_message_id=802, reply_to_sender_user_id=ALICE)
        ]
        acked_thread = analyze(GROUP, acked)
        self.assertFalse(acked_thread["open_commitment"])
        self.assertTrue(acked_thread["addressed"])


class DirectMessageTest(unittest.TestCase):
    def test_dm_same_minute_reply_order_and_resolution(self):
        raw = newest_first(same_minute_dm())
        self.assertEqual([m["id"] for m in raw], [302, 301])  # daemon order
        thread = analyze(DM_BOB, same_minute_dm())
        self.assertTrue(thread["eligible"])
        self.assertTrue(thread["operator_replied_after_ask"])
        lines = [common.render_message(view, tz=TZ) for view in thread["messages"]]
        self.assertEqual(
            lines,
            [
                "[2026-10-11 10:00] Bob Example: Are we still on for dinner at 7?",
                "[2026-10-11 10:00] [YOU] Yes",
            ],
        )
        prompt = digest_prompt([DM_BOB], {BOB: same_minute_dm()})
        self.assertIn(
            "=== Bob Example [DM with Bob Example] [operator replied after last ask] ===\n"
            "[2026-10-11 10:00] Bob Example: Are we still on for dinner at 7?\n"
            "[2026-10-11 10:00] [YOU] Yes",
            prompt,
        )
        with tempfile.TemporaryDirectory() as tmp:
            _code, candidates, _ = run_scanner([DM_BOB], {BOB: same_minute_dm()}, tmp)
        candidate = candidates[0]
        self.assertEqual(candidate["chat_type"], "dm")
        self.assertTrue(candidate["addressed_to_user"])
        self.assertTrue(candidate["operator_replied_after_ask"])
        self.assertTrue(candidate["user_spoke_last"])
        self.assertEqual(candidate["headline"], "Bob Example — You: Yes")
        self.assertTrue(candidate["body"].endswith("[YOU] Yes"))

    def test_dm_new_ask_after_operator_reply_is_open_again(self):
        messages = same_minute_dm() + [msg(303, 600, "Can you bring wine?", BOB)]
        thread = analyze(DM_BOB, messages)
        self.assertFalse(thread["operator_replied_after_ask"])
        self.assertFalse(thread["user_spoke_last"])

    def test_photo_reply_with_caption(self):
        messages = [
            msg(401, 0, "Did the package arrive?", BOB),
            msg(402, 30, "", ALICE, content_type="photo", caption="Here it is", reply_to_message_id=401,
                reply_to_sender_user_id=BOB, reply_to_sender_name=NAMES[BOB]),
            msg(403, 40, "", BOB, content_type="voice"),
            msg(404, 50, "", BOB, content_type="other"),
            msg(405, 60, "", BOB, new=False),  # old daemon media: nothing to show
        ]
        thread = analyze(DM_BOB, messages)
        lines = [common.render_message(view, with_time=False) for view in thread["messages"]]
        self.assertEqual(
            lines,
            [
                "Bob Example: Did the package arrive?",
                "[YOU] ↩ Bob Example: [photo] Here it is",
                "Bob Example: [voice]",
            ],
        )

    def test_short_replies_are_kept(self):
        for text in ("ok", "PRL", "11:35", "Yes"):
            thread = analyze(DM_BOB, [msg(901, 0, "When?", BOB), msg(902, 10, text, ALICE)])
            self.assertEqual(thread["messages"][-1]["content"], text)


class ExclusionTest(unittest.TestCase):
    def test_channel_and_bot_dm_excluded(self):
        channel = {"id": -1001500000009, "title": "Market Recap", "type": "channel"}
        channel_posts = [
            {"id": 11, "date": BASE, "text": "Alice, today's recap: rates up", "sender_chat_id": channel["id"],
             "is_outgoing": False, "content_type": "text", "mention_user_ids": [], "mention_usernames": []}
        ]
        untyped = {"id": -1001500000010, "title": "Alerts"}  # supergroup-range id, no type
        untyped_posts = [dict(channel_posts[0], id=12, sender_chat_id=untyped["id"])]
        bot = {"id": 5005, "title": "Helper Bot", "type": "private", "user_id": 5005, "is_bot": True}
        bot_messages = [{"id": 13, "date": BASE, "text": "Alice, your report is ready",
                         "sender_user_id": 5005, "sender_name": "Helper Bot", "is_outgoing": False}]
        self.assertIsNone(analyze(channel, channel_posts))
        self.assertIsNone(analyze(untyped, untyped_posts))
        self.assertIsNone(analyze(bot, bot_messages))
        # A channel post inside a group is dropped; it does not address the operator.
        mixed = [dict(channel_posts[0], id=14, sender_chat_id=GROUP_ID), msg(15, 30, "morning all", BOB)]
        mixed_thread = analyze(GROUP, mixed)
        self.assertEqual([view["id"] for view in mixed_thread["messages"]], [15])
        self.assertFalse(mixed_thread["eligible"])

        chats = [channel, untyped, bot]
        messages = {channel["id"]: channel_posts, untyped["id"]: untyped_posts, bot["id"]: bot_messages}
        with tempfile.TemporaryDirectory() as tmp:
            code, candidates, _ = run_scanner(chats, messages, tmp)
        self.assertEqual((code, candidates), (0, []))
        prompt = digest_prompt(chats + [DM_BOB], {**messages, BOB: same_minute_dm()})
        for title in ("Market Recap", "Alerts", "Helper Bot"):
            self.assertNotIn(title, prompt)


class OldDaemonTest(unittest.TestCase):
    def test_old_daemon_payload_still_works(self):
        dm = {"id": BOB, "title": "Bob Example"}  # positive id -> DM
        group = {"id": -1001500000002, "title": "Book Club"}  # supergroup range -> group
        basic = {"id": -4001, "title": "Neighbors"}  # other negative -> basic group
        self.assertEqual(common.chat_kind(dm["id"], dm), "dm")
        self.assertEqual(common.chat_kind(group["id"], group), "group")
        self.assertEqual(common.chat_kind(basic["id"], basic), "group")
        messages = {
            BOB: [msg(21, 0, "Can you review the doc?", BOB, new=False),
                  msg(22, 30, "Done", ALICE, new=False)],
            group["id"]: [msg(31, 0, "Alice picked the next book", CAROL, new=False)],
            basic["id"]: [msg(41, 0, "Trash day moved to Tuesday", DAVE, new=False)],
        }
        op = operator(ME_OLD)
        self.assertEqual((op.user_id, op.name, op.username), (ALICE, "Alice Example", ""))
        with tempfile.TemporaryDirectory() as tmp:
            code, candidates, watermarks = run_scanner([dm, group, basic], messages, tmp, me=ME_OLD)
        self.assertEqual(code, 0)
        by_chat = {candidate["chat_name"]: candidate for candidate in candidates}
        self.assertEqual(set(by_chat), {"Bob Example", "Book Club"})
        self.assertTrue(by_chat["Bob Example"]["user_spoke_last"])
        self.assertEqual(by_chat["Bob Example"]["chat_type"], "dm")
        self.assertTrue(by_chat["Bob Example"]["operator_replied_after_ask"])
        self.assertEqual(by_chat["Book Club"]["chat_type"], "group")
        self.assertTrue(by_chat["Book Club"]["addressed_to_user"])
        self.assertEqual(watermarks[str(basic["id"])], "41")
        prompt = digest_prompt([dm, group, basic], messages, me=ME_OLD)
        self.assertIn("Telegram user id 1001", prompt)
        self.assertIn("no @username", prompt)
        self.assertIn("[YOU] Done", prompt)
        self.assertNotIn("Neighbors", prompt)

    def test_me_unavailable_uses_fallback_identity(self):
        op = common.resolve_operator(None, "Alice Example")
        self.assertEqual((op.user_id, op.first_name, op.name), (None, "Alice", "Alice Example"))
        self.assertEqual(common.resolve_operator({"error": "Could not get user"}, "Alice Example").name,
                         "Alice Example")
        messages = {GROUP_ID: [msg(51, 0, "Alice, is the boat booked?", BOB), msg(52, 60, "Yes", ALICE)]}
        with tempfile.TemporaryDirectory() as tmp:
            _code, candidates, _ = run_scanner([GROUP], messages, tmp, me=None)
        candidate = candidates[0]
        self.assertTrue(candidate["user_spoke_last"])  # via is_outgoing
        self.assertTrue(candidate["operator_replied_after_ask"])
        self.assertIn("you are Alice Example]", candidate["body"])
        self.assertEqual(digest.tg.resolve_operator(None, "").name, "the operator")


class DigestTest(unittest.TestCase):
    def test_digest_groups_same_title_chats_separately(self):
        crew_a = {"id": -1001500000003, "title": "Weekend Crew", "type": "supergroup"}
        crew_b = {"id": -1001500000004, "title": "Weekend Crew", "type": "supergroup"}
        messages = {
            crew_a["id"]: [msg(61, 0, "Alice, can you book the court?", BOB)],
            crew_b["id"]: [msg(71, 100, "Alice, did you get the tickets?", CAROL)],
        }
        prompt = digest_prompt([crew_a, crew_b], messages)
        sections = prompt.split("=== Weekend Crew [group] ===")
        self.assertEqual(len(sections), 3)
        self.assertIn("did you get the tickets?", sections[1])  # most recent first
        self.assertNotIn("book the court", sections[1])
        self.assertIn("book the court", sections[2])

    def test_digest_prompt_contents(self):
        prompt = digest_prompt([DM_BOB], {BOB: same_minute_dm()})
        self.assertIn("Current local time: Sunday 2026-10-11 14:00 EDT, UTC-04:00", prompt)
        self.assertIn("@alice_example", prompt)
        self.assertIn("Telegram user id 1001", prompt)
        self.assertIn("Alice Example", prompt)
        for legend in (
            "Marker legend:",
            "[YOU] — a message Alice sent",
            "↩ you — a reply to Alice's message",
            "@you — the message mentions Alice",
        ):
            self.assertIn(legend, prompt)
        for rule in (
            "is never Alice's item",
            "NOT an action item unless",
            "never presented as current",
            "Quote the ask briefly",
            "=== <chat title> [DM with <name>] ===",
            "=== <chat title> [group] ===",
        ):
            self.assertIn(rule, prompt)
        # The delivery contract is unchanged.
        self.assertIn("🔵 *Chat Name* — _Sender_:", prompt)
        self.assertIn("If nothing qualifies, output exactly: ✅ No action items.", prompt)

    def _run_main(self, chats, messages, llm_reply):
        calls = []

        def call_llm(**kwargs):
            calls.append(kwargs)
            choice = types.SimpleNamespace(message=types.SimpleNamespace(content=llm_reply))
            return types.SimpleNamespace(choices=[choice])

        class FrozenDatetime(datetime):
            @classmethod
            def now(cls, tz=None):
                return NOW.astimezone(tz) if tz else NOW.astimezone().replace(tzinfo=None)

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.multiple(
                digest,
                datetime=FrozenDatetime,
                ARTIFACT_DIR=Path(tmp),
                _chat_cache=None,
                _muted_chat_ids=None,
                get_me=lambda: ME,
                get_chats=lambda: {chat["id"]: chat for chat in chats},
                get_messages=lambda chat_id, limit=100, cutoff_ts=0: newest_first(messages[chat_id]),
                send_telegram=mock.Mock(side_effect=AssertionError("send in --no-send")),
                add_to_reminders=mock.Mock(side_effect=AssertionError("reminders in --no-send")),
            ), mock.patch.object(digest.auxiliary_client, "call_llm", call_llm), mock.patch.object(
                sys, "argv", ["telegram-action-items-v2.py", "--no-send"]
            ), contextlib.redirect_stderr(io.StringIO()):
                digest.main()
            artifact = (Path(tmp) / "latest.md").read_text()
        return calls, artifact

    def test_main_no_send_feeds_only_eligible_chats(self):
        reply = "🔵 *Bob Example* — _Bob Example_: \"bring wine?\" — reply"
        messages = {GROUP_ID: bob_asks_carol_replies(), BOB: same_minute_dm()}
        calls, artifact = self._run_main([GROUP, DM_BOB], messages, reply)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["timeout"], 300)
        prompt = calls[0]["messages"][0]["content"]
        self.assertIn("[YOU] Yes", prompt)
        self.assertNotIn("Weekend Crew", prompt)
        self.assertIn("Current local time: Sunday 2026-10-11", prompt)
        self.assertIn("Telegram send skipped (--no-send test run).", artifact)
        self.assertIn(reply, artifact)

    def test_main_without_eligible_chats_skips_llm(self):
        calls, artifact = self._run_main([GROUP], {GROUP_ID: bob_asks_carol_replies()}, "unused")
        self.assertEqual(calls, [])
        self.assertIn("✅ No action items.", artifact)


class ScannerWatermarkTest(unittest.TestCase):
    def test_scanner_watermark_cap_keeps_unreported_chats(self):
        chats, messages = [], {}
        for index in range(20):
            chat_id = 3001 + index
            NAMES[chat_id] = f"Friend {index:02d}"
            chats.append({"id": chat_id, "title": f"Friend {index:02d}", "type": "private",
                          "user_id": chat_id, "is_bot": False})
            messages[chat_id] = [msg(100 + index, index * 60, f"Question {index}?", chat_id)]
        previous = {str(3001 + index): "1" for index in range(20)}
        with tempfile.TemporaryDirectory() as tmp:
            state = {"version": 1, "watermarks": previous}
            code, candidates, watermarks = run_scanner(chats, messages, tmp, state=state)
            self.assertEqual(code, 0)
            self.assertEqual(len(candidates), scan.MAX_CANDIDATES)
            reported = {candidate["source"].split(":", 1)[1] for candidate in candidates}
            self.assertEqual(reported, {str(3001 + index) for index in range(5, 20)})  # newest 15
            for index in range(20):
                key = str(3001 + index)
                expected = "1" if index < 5 else str(100 + index)
                self.assertEqual(watermarks[key], expected, key)
            # Next run reports the five that were cut, and only those.
            _code, again, watermarks = run_scanner(chats, messages, tmp)
            self.assertEqual(
                {candidate["source"].split(":", 1)[1] for candidate in again},
                {str(3001 + index) for index in range(5)},
            )
            self.assertEqual(watermarks["3001"], "100")


class CuratorPayloadTest(unittest.TestCase):
    def test_llm_rank_payload_includes_attribution_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            _code, scanned, _ = run_scanner([DM_BOB], {BOB: same_minute_dm()}, tmp)
            api = support.load_plugin_api(Path(tmp))
            candidates = api._town_scan_candidates(json.dumps({"candidates": scanned}))
            for candidate in candidates:
                candidate["score"] = 50
            captured = {}

            class FakeLLM:
                def complete_structured(self, **kwargs):
                    captured.update(kwargs)
                    return types.SimpleNamespace(parsed={"items": []})

            api._llm_rank(candidates, FakeLLM())
        payload = json.loads(captured["input"][0]["text"])
        row = payload[0]
        self.assertEqual(row["chat_type"], "dm")
        self.assertIs(row["addressed_to_user"], True)
        self.assertIs(row["operator_replied_after_ask"], True)
        self.assertIs(row["user_spoke_last"], True)
        self.assertIn("addressed_to_user", api._RANKING_RULES)
        self.assertIn("operator_replied_after_ask", api._RANKING_RULES)
        self.assertIn("addressed_to_user", captured["instructions"])


if __name__ == "__main__":
    unittest.main()
