"""R4 — Need to Know: consume every scan, one item per source, no junk.

Every fixture here is synthetic (Alice/Bob/Carol, example.com). No test reads
or writes live ``~/.hermes`` state, calls Google, or reaches an LLM: cron jobs,
sessions, and Gmail are in-memory fakes, the LLM is a stub, and the clock is
pinned to ``NOW``.
"""

from __future__ import annotations

import contextlib
import copy
import email.utils
import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Callable, Optional
from unittest import mock

import support

NOW = datetime(2026, 10, 11, 15, 0, tzinfo=timezone.utc).timestamp()
DAY = 24 * 60 * 60
ACCOUNT = "alice@example.com"
ALIAS = "alice.alias@example.com"
ASSISTANT = "assistant@example.com"
TELEGRAM_JOB = "job-telegram"
GMAIL_JOB = "job-gmail"
CALENDAR_JOB = "job-calendar"


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def digest(*parts: str) -> str:
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def telegram_candidate(
    chat_id: int, message_id: str, timestamp: float, text: str, chat_name: str = "Weekend Crew"
) -> dict[str, Any]:
    return {
        "headline": f"{chat_name} — Bob: {text}",
        "summary": f"Bob: {text}",
        "source": f"telegram:{chat_id}",
        "created_at": iso(timestamp),
        "content_hash": digest("telegram", str(chat_id), message_id),
        "chat_name": chat_name,
        "last_sender": "Bob",
        "user_spoke_last": False,
    }


def calendar_candidate(event_id: str, timestamp: float, title: str) -> dict[str, Any]:
    return {
        "headline": f"{title} — today at 3 PM",
        "summary": "Room 4 with Carol and Dave; agenda attached.",
        "source": f"calendar:{ACCOUNT}:{event_id}",
        "created_at": iso(timestamp),
        "content_hash": digest("calendar", event_id),
    }


def gmail_candidate(
    message_id: str,
    thread_id: str,
    timestamp: float,
    subject: str,
    *,
    sender: str = "Bob Example <bob@example.com>",
    label: Optional[str] = None,
) -> dict[str, Any]:
    _name, address = email.utils.parseaddr(sender)
    return {
        "headline": f"Bob Example: {subject}",
        "summary": f"{subject}: please confirm the venue deposit by Friday.",
        "source": f"gmail:{ACCOUNT}:{message_id}",
        "created_at": iso(timestamp),
        "content_hash": digest("gmail", thread_id, subject),
        "account": ACCOUNT,
        "thread_id": thread_id,
        "from": sender,
        "from_address": address.casefold(),
        "town_label": label,
        "town_label_confidence": 0.9 if label else None,
    }


class CronDir:
    """Synthetic cron output docs in the real wrapped format, with pinned mtimes."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.jobs: dict[str, dict[str, Any]] = {}

    def job(self, job_id: str, name: str) -> None:
        self.jobs[job_id] = {
            "id": job_id,
            "name": name,
            "last_status": "ok",
            "last_run_at": iso(NOW - 60),
        }

    def doc(self, job_id: str, minutes_ago: float, payload: Optional[dict[str, Any]]) -> Path:
        timestamp = NOW - minutes_ago * 60
        stamp = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        path = self.root / job_id / f"{stamp.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        name = self.jobs[job_id]["name"]
        text = (
            f"# Cron Job: {name}\n\n**Job ID:** {job_id}\n"
            f"**Run Time:** {stamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            "**Mode:** no_agent (script)\n"
        )
        if payload is None:
            text += "**Status:** silent (empty output)\n"
        else:
            text += "\n---\n\n" + json.dumps(payload, separators=(",", ":")) + "\n"
        path.write_text(text, encoding="utf-8")
        mtime_ns = int(timestamp * 1_000_000_000)
        os.utime(path, ns=(mtime_ns, mtime_ns))
        return path

    def modules(self) -> dict[str, ModuleType]:
        package = ModuleType("cron")
        package.__path__ = []  # type: ignore[attr-defined]
        jobs = ModuleType("cron.jobs")
        jobs.get_cron_output_dir = lambda: self.root  # type: ignore[attr-defined]
        jobs.list_jobs = lambda include_disabled=False: [  # type: ignore[attr-defined]
            dict(job) for job in self.jobs.values()
        ]
        return {"cron": package, "cron.jobs": jobs}


class FakeSessions:
    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.messages: dict[str, list[dict[str, Any]]] = {}

    def module(self) -> ModuleType:
        owner = self

        class SessionDB:
            def list_sessions_rich(self, **_: Any) -> list[dict[str, Any]]:
                return [dict(meta) for meta in owner.sessions]

            def get_messages(self, session_id: str) -> list[dict[str, Any]]:
                return [dict(message) for message in owner.messages.get(session_id, [])]

            def close(self) -> None:
                pass

        module = ModuleType("hermes_state")
        module.SessionDB = SessionDB  # type: ignore[attr-defined]
        return module


class FakeLLM:
    """Records every structured call; ranks the keys ``choose`` picks."""

    def __init__(self, choose: Optional[Callable[[list[dict[str, Any]]], list[str]]] = None) -> None:
        self.choose = choose
        self.calls: list[tuple[str, Any]] = []

    def complete_structured(self, **kwargs: Any) -> Any:
        data = json.loads(kwargs["input"][0]["text"])
        self.calls.append((kwargs["schema_name"], data))
        if kwargs["schema_name"] == "town_day_brief":
            return SimpleNamespace(parsed={"day_summary": "A light Sunday.", "callouts": []})
        rows = data["ranking_candidates"] if isinstance(data, dict) else data
        chosen = self.choose(rows) if self.choose else [row["key"] for row in rows]
        by_key = {row["key"]: row for row in rows}
        items = [
            {
                "key": key,
                "headline": by_key[key]["headline"],
                "summary": by_key[key]["summary"],
                "score": 80 - index,
            }
            for index, key in enumerate(chosen)
        ]
        suggestions = [
            {"id": f"s{index}", "label": f"Reply to Bob {index}", "prompt": "Draft it.", "kind": "followup"}
            for index in range(3)
        ]
        return SimpleNamespace(parsed={"items": items, "suggestions": suggestions})

    def inputs(self, schema_name: str) -> list[Any]:
        return [data for name, data in self.calls if name == schema_name]


class CuratorCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state_dir = self.tmp / "state"
        self.home = self.tmp / "home"
        self.state_dir.mkdir()
        self.home.mkdir()
        self.api = support.load_plugin_api(self.state_dir)
        self.api.get_hermes_home = lambda: self.home
        self.cron = CronDir(self.tmp / "cron-output")
        self.sessions = FakeSessions()
        modules = {**self.cron.modules(), "hermes_state": self.sessions.module()}
        for patcher in (
            mock.patch.dict(sys.modules, modules),
            mock.patch("time.time", return_value=NOW),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    @property
    def state_path(self) -> Path:
        return self.state_dir / "feed-state.json"

    def state(self) -> dict[str, Any]:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    def write_state(self, state: dict[str, Any]) -> None:
        self.state_path.write_text(json.dumps(state), encoding="utf-8")

    def served(self) -> list[dict[str, Any]]:
        return self.api.get_feed()["items"]

    def prior_item(self, candidate: dict[str, Any], score: float = 70.0) -> dict[str, Any]:
        item_id = f"ntk_{candidate['content_hash'][:16]}"
        item = {
            "id": item_id,
            "headline": candidate["headline"],
            "summary": candidate["summary"],
            "source": candidate["source"],
            "score": score,
            "created_at": candidate["created_at"],
            "source_activity_at": candidate["created_at"],
            "dismissed": False,
            "promoted": False,
            "task": None,
            "content_hash": candidate["content_hash"],
        }
        for field in ("chat_name", "last_sender", "account", "thread_id"):
            if candidate.get(field):
                item[field] = candidate[field]
        return item


# --- Operator profile -------------------------------------------------------


class OperatorProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.profile = support.load_module(
            support.DASHBOARD / "operator_profile.py", "town_test_r4_operator_profile"
        )
        patcher = mock.patch.object(self.profile, "_system_timezone", return_value="America/Chicago")
        patcher.start()
        self.addCleanup(patcher.stop)

    def write(self, relative: str, text: str) -> None:
        path = self.home / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_missing_files_yield_defaults(self) -> None:
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile = self.profile.load_operator_profile(self.home)
        self.assertEqual(stderr.getvalue(), "")
        self.assertEqual(profile["name"], "")
        self.assertEqual(profile["preferred_name"], "")
        self.assertEqual(profile["home_timezone"], "America/Chicago")
        self.assertEqual(profile["self_addresses"], [])
        self.assertEqual(profile["assistant_senders"], [])

    def test_malformed_json_logs_one_line_and_falls_back(self) -> None:
        self.write("town-feed/operator.json", "{not json")
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile = self.profile.load_operator_profile(self.home, account_addresses=[ACCOUNT])
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertIn("operator.json", stderr.getvalue())
        self.assertEqual(profile["name"], "")
        self.assertEqual(profile["self_addresses"], [ACCOUNT])

        self.write("town-feed/operator.json", '["not", "an", "object"]')
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            profile = self.profile.load_operator_profile(self.home)
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertEqual(profile["assistant_senders"], [])

    def test_config_yaml_timezone_precedes_system_timezone(self) -> None:
        self.write("config.yaml", "model: x\ntimezone: 'Europe/Lisbon'  # home\n")
        self.assertEqual(self.profile.load_operator_profile(self.home)["home_timezone"], "Europe/Lisbon")
        self.write("config.yaml", "timezone: ''\n")
        self.assertEqual(self.profile.load_operator_profile(self.home)["home_timezone"], "America/Chicago")

    def test_user_md_fallback_fields(self) -> None:
        self.write(
            "memories/USER.md",
            "**Name:** Alice Example\n§\n**What to call them:** Ali\n§\n"
            "**Telegram:** @alice_example\n§\n"
            "**Timezone:** America/New_York (US Eastern)\n§\n"
            "Alice prefers morning meetings and keeps a cat named Pickles.\n",
        )
        profile = self.profile.load_operator_profile(self.home)
        self.assertEqual(profile["name"], "Alice Example")
        self.assertEqual(profile["preferred_name"], "Ali")
        self.assertEqual(profile["home_timezone"], "America/New_York")
        self.assertEqual(profile["telegram_username"], "alice_example")
        self.assertNotIn("Pickles", json.dumps(profile))

    def test_operator_json_wins_and_addresses_are_lowercased(self) -> None:
        self.write("memories/USER.md", "**Name:** Alice Example\n**Timezone:** Europe/London\n")
        self.write(
            "town-feed/operator.json",
            json.dumps(
                {
                    "name": "Alice B. Example",
                    "home_timezone": "Not/AZone",
                    "self_addresses": ["Alice.Alias@Example.COM", "Alice Example <ALICE@example.com>"],
                    "assistant_senders": ["Assistant@Example.com", 42],
                    "telegram_user_id": 1000001,
                }
            ),
        )
        profile = self.profile.load_operator_profile(
            self.home, account_addresses=["Alice@Example.com", "Work@Example.com"]
        )
        self.assertEqual(profile["name"], "Alice B. Example")
        self.assertEqual(profile["preferred_name"], "Alice")
        self.assertEqual(profile["home_timezone"], "Europe/London")
        self.assertEqual(profile["self_addresses"], [ALIAS, ACCOUNT, "work@example.com"])
        self.assertEqual(profile["assistant_senders"], [ASSISTANT])
        self.assertEqual(profile["telegram_user_id"], 1000001)


# --- Consume every scan -----------------------------------------------------


class ConsumeEveryScanTest(CuratorCase):
    def setUp(self) -> None:
        super().setUp()
        self.cron.job(TELEGRAM_JOB, "town-telegram-scan")
        self.consumed_doc = self.cron.doc(
            TELEGRAM_JOB, 40, {"candidates": [telegram_candidate(100, "m0", NOW - 2400, "old news")]}
        )
        self.watermark = self.consumed_doc.stat().st_mtime_ns
        self.new = [
            telegram_candidate(101, "m1", NOW - 1500, "Can you bring the speaker?"),
            telegram_candidate(102, "m2", NOW - 900, "Dinner moved to 8"),
            telegram_candidate(103, "m3", NOW - 300, "Tickets are booked"),
        ]
        self.cron.doc(TELEGRAM_JOB, 30, {"candidates": [self.new[0]]})
        self.cron.doc(TELEGRAM_JOB, 20, {"candidates": [self.new[1]]})
        self.newest_doc = self.cron.doc(TELEGRAM_JOB, 10, {"candidates": [self.new[2]]})
        self.write_state(
            {"version": 6, "items": [], "source_state": {"cron_consumed": {TELEGRAM_JOB: self.watermark}}}
        )

    def ranked_keys(self, llm: FakeLLM) -> set[str]:
        rows = llm.inputs("town_feed_ranking")[-1]
        return {row["key"] for row in rows}

    def test_every_doc_since_the_watermark_reaches_ranking(self) -> None:
        llm = FakeLLM()
        self.api.run_curator(llm=llm)

        expected = {candidate["content_hash"][:16] for candidate in self.new}
        self.assertEqual(self.ranked_keys(llm), expected)
        self.assertEqual(
            self.state()["source_state"]["cron_consumed"][TELEGRAM_JOB],
            self.newest_doc.stat().st_mtime_ns,
        )
        self.assertEqual({item["source"] for item in self.served()}, {"telegram:101", "telegram:102", "telegram:103"})

    def test_second_run_without_new_docs_rereads_nothing(self) -> None:
        self.api.run_curator(llm=FakeLLM())
        parsed: list[str] = []
        original = self.api._town_scan_candidates

        def spy(output: str) -> Any:
            parsed.append(output)
            return original(output)

        with mock.patch.object(self.api, "_town_scan_candidates", spy):
            llm = FakeLLM()
            self.api.run_curator(llm=llm)
            candidates, *_rest, consumed = self.api._scan_cron(
                NOW, self.state()["source_state"]["cron_consumed"]
            )

        self.assertEqual(parsed, [])
        self.assertEqual(candidates, [])
        self.assertEqual(consumed, {})
        # The three items survive as retained items, not as re-read candidates.
        self.assertEqual(self.ranked_keys(llm), {c["content_hash"][:16] for c in self.new})
        self.assertEqual(
            self.state()["source_state"]["cron_consumed"][TELEGRAM_JOB],
            self.newest_doc.stat().st_mtime_ns,
        )

    def test_failed_write_rereads_the_same_docs(self) -> None:
        before = self.state_path.read_text(encoding="utf-8")
        with mock.patch.object(self.api, "_write_state_unlocked", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                self.api.run_curator(llm=FakeLLM())
        self.assertEqual(self.state_path.read_text(encoding="utf-8"), before)

        llm = FakeLLM()
        self.api.run_curator(llm=llm)
        self.assertEqual(self.ranked_keys(llm), {c["content_hash"][:16] for c in self.new})
        self.assertEqual(
            self.state()["source_state"]["cron_consumed"][TELEGRAM_JOB],
            self.newest_doc.stat().st_mtime_ns,
        )

    def test_without_watermark_reads_six_hours_capped_at_twelve_docs(self) -> None:
        self.cron.job("job-backfill", "town-telegram-scan-2")
        self.cron.doc("job-backfill", 7 * 60, {"candidates": [telegram_candidate(200, "x", NOW - 7 * 3600, "stale")]})
        for index in range(14):
            minutes = 5 + index * 10
            self.cron.doc(
                "job-backfill",
                minutes,
                {"candidates": [telegram_candidate(300 + index, "y", NOW - minutes * 60, f"fresh {index}")]},
            )
        candidates, *_rest, consumed = self.api._scan_cron(NOW, {})
        chats = {c["source"] for c in candidates if c["source"].startswith("telegram:3")}
        self.assertEqual(chats, {f"telegram:{300 + index}" for index in range(12)})
        self.assertNotIn("telegram:200", {c["source"] for c in candidates})
        self.assertEqual(consumed["job-backfill"], int((NOW - 5 * 60) * 1_000_000_000))

    def test_snapshot_fields_come_from_the_newest_doc_that_has_them(self) -> None:
        self.cron.job(GMAIL_JOB, "town-gmail-scan")
        older = {"account": ACCOUNT, "thread_id": "t-old", "to": "carol@example.com", "subject": "Venue", "last_sent_ts": NOW - 3 * DAY}
        newer = {"account": ACCOUNT, "thread_id": "t-new", "to": "dave@example.com", "subject": "Quote", "last_sent_ts": NOW - DAY}
        self.cron.doc(GMAIL_JOB, 25, {"candidates": [], "awaiting_reply": [older]})
        self.cron.doc(GMAIL_JOB, 15, {"candidates": [], "awaiting_reply": [newer]})
        self.cron.doc(GMAIL_JOB, 5, None)  # a silent run carries no snapshot
        _c, _e, _n, _s, awaiting, *_rest = self.api._scan_cron(NOW, {})
        self.assertEqual([entry["thread_id"] for entry in awaiting], ["t-new"])


class OldStateTest(CuratorCase):
    def test_v6_state_without_cron_consumed_loads_and_curates(self) -> None:
        retained = telegram_candidate(400, "r1", NOW - 3 * 3600, "See you Saturday")
        old_state = {
            "version": 6,
            "items": [dict(self.prior_item(retained), town_label=None, action_hint="Reply to Bob")],
            "decisions": {"ntk_0123456789abcdef": {"dismissed": True, "updated_at": iso(NOW - 3600), "source_activity_at": iso(NOW - 7200)}},
            "source_state": {"memory_hashes": {}},
            "last_curated_at": iso(NOW - 1800),
            "ranking_mode": "llm",
            "last_curate_errors": [],
            "suggestions": [],
            "suggestions_generated_at": None,
            "label_stats": {},
            "last_labeling_summary": None,
            "next_event": None,
            "schedule": {"events": [], "generated_at": None, "context": None},
            "day_brief": None,
            "open_loop_suppressed": [],
        }
        self.write_state(old_state)
        loaded = self.api._read_state_unlocked()
        self.assertNotIn("cron_consumed", loaded["source_state"])

        self.cron.job(TELEGRAM_JOB, "town-telegram-scan")
        doc = self.cron.doc(TELEGRAM_JOB, 5, {"candidates": [telegram_candidate(401, "n1", NOW - 300, "Running late")]})
        result = self.api.run_curator(llm=None)

        self.assertTrue(result["ok"])
        self.assertEqual({item["source"] for item in self.served()}, {"telegram:400", "telegram:401"})
        state = self.state()
        self.assertEqual(state["source_state"]["cron_consumed"], {TELEGRAM_JOB: doc.stat().st_mtime_ns})
        self.assertTrue(state["decisions"]["ntk_0123456789abcdef"]["dismissed"])


# --- One live item per source ----------------------------------------------


class OneItemPerSourceTest(CuratorCase):
    def setUp(self) -> None:
        super().setUp()
        self.cron.job(TELEGRAM_JOB, "town-telegram-scan")

    def test_newer_telegram_candidate_replaces_the_retained_item(self) -> None:
        older = telegram_candidate(500, "a1", NOW - 3 * DAY, "3 days silent on the 11 a.m. move", "Bob")
        newer = telegram_candidate(500, "a2", NOW - 600, "That works!", "Bob")
        self.write_state({"version": 6, "items": [self.prior_item(older, score=90.0)]})
        self.cron.doc(TELEGRAM_JOB, 5, {"candidates": [newer]})

        self.api.run_curator(llm=None)

        served = [item for item in self.served() if item["source"] == "telegram:500"]
        self.assertEqual(len(served), 1)
        self.assertEqual(served[0]["id"], f"ntk_{newer['content_hash'][:16]}")
        self.assertEqual(len([i for i in self.state()["items"] if i["source"] == "telegram:500"]), 1)

    def test_attribution_fields_survive_retention(self) -> None:
        candidate = dict(
            telegram_candidate(502, "c1", NOW - 600, "Can you send the deck?", "Bob"),
            chat_type="group",
            addressed_to_user=True,
            operator_replied_after_ask=False,
        )
        self.cron.doc(TELEGRAM_JOB, 5, {"candidates": [candidate]})
        self.api.run_curator(llm=None)
        # Second cycle: no new docs, so the item re-enters only via retention.
        self.api.run_curator(llm=None)

        stored = [i for i in self.state()["items"] if i["source"] == "telegram:502"]
        self.assertEqual(len(stored), 1)
        self.assertEqual(stored[0]["chat_type"], "group")
        self.assertIs(stored[0]["addressed_to_user"], True)
        self.assertIs(stored[0]["operator_replied_after_ask"], False)

    def test_new_activity_after_a_dismissal_is_shown_and_the_dismissal_stays(self) -> None:
        older = telegram_candidate(501, "b1", NOW - DAY, "Lunch Tuesday?")
        newer = telegram_candidate(501, "b2", NOW - 600, "Actually, Wednesday works better")
        old_id = f"ntk_{older['content_hash'][:16]}"
        dismissal = {"dismissed": True, "updated_at": iso(NOW - 3600), "source_activity_at": older["created_at"]}
        self.write_state({"version": 6, "items": [], "decisions": {old_id: dismissal}})
        self.cron.doc(TELEGRAM_JOB, 20, {"candidates": [older]})
        self.cron.doc(TELEGRAM_JOB, 5, {"candidates": [newer]})

        self.api.run_curator(llm=None)

        self.assertEqual([item["id"] for item in self.served()], [f"ntk_{newer['content_hash'][:16]}"])
        self.assertTrue(self.state()["decisions"][old_id]["dismissed"])

    def test_one_session_yields_one_item_and_skips_the_interrupt_stub(self) -> None:
        first = {
            "source": "session:sess-1",
            "headline": "Plan the offsite",
            "summary": "Drafted an agenda with three sessions and a dinner.",
            "created_at": iso(NOW - 2 * DAY),
            "content_hash": digest("session", "first"),
        }
        second = dict(first, summary="Booked the venue hold; awaiting deposit details.", created_at=iso(NOW - DAY), content_hash=digest("session", "second"))
        self.write_state({"version": 6, "items": [self.prior_item(first), self.prior_item(second)]})
        self.sessions.sessions = [{"id": "sess-1", "title": "Plan the offsite", "last_active": NOW - 600}]
        self.sessions.messages["sess-1"] = [
            {"role": "user", "content": "Help me plan the team offsite in Lisbon."},
            {"role": "assistant", "content": "The venue confirmed the hold for May 4; the deposit of $500 is due Friday."},
            {"role": "user", "content": "Great, what next?"},
            {"role": "assistant", "content": "Operation interrupted: waiting for model response (9.6s elapsed)."},
        ]

        self.api.run_curator(llm=None)

        sessions = [item for item in self.served() if item["source"] == "session:sess-1"]
        self.assertEqual(len(sessions), 1)
        self.assertIn("deposit of $500", sessions[0]["summary"])
        self.assertNotIn("Operation interrupted", json.dumps(self.state()["items"]))

    def test_a_session_with_only_stub_replies_yields_no_item(self) -> None:
        self.sessions.sessions = [{"id": "sess-2", "title": "Check the router", "last_active": NOW - 600}]
        self.sessions.messages["sess-2"] = [
            {"role": "user", "content": "Why is the router dropping connections every hour?"},
            {"role": "assistant", "content": "Error: provider returned HTTP 529 while streaming the response body."},
            {"role": "assistant", "content": "Request timed out after 120 seconds waiting for the provider."},
        ]
        self.api.run_curator(llm=None)
        self.assertEqual([i for i in self.served() if i["source"].startswith("session:")], [])

    def test_identity_keys(self) -> None:
        key = self.api._identity_key
        self.assertEqual(key({"source": "telegram:-100123"}), "telegram:-100123")
        self.assertEqual(key({"source": "session:sess-1"}), "session:sess-1")
        self.assertEqual(
            key({"source": f"gmail:{ACCOUNT}:m-2", "account": ACCOUNT, "thread_id": "t-9"}),
            f"gmail:{ACCOUNT}:t-9",
        )
        self.assertEqual(key({"source": f"gmail:{ACCOUNT}:m-2"}), f"gmail:{ACCOUNT}:m-2")
        self.assertEqual(key({"source": f"calendar:{ACCOUNT}:evt-1"}), f"calendar:{ACCOUNT}:evt-1")
        self.assertEqual(key({"source": "memory:notes.md", "content_hash": "abc"}), "content:abc")


# --- Junk out ----------------------------------------------------------------


class JunkOutTest(CuratorCase):
    def setUp(self) -> None:
        super().setUp()
        self.cron.job(TELEGRAM_JOB, "town-telegram-scan")
        self.five = [
            telegram_candidate(600 + index, f"c{index}", NOW - (index + 1) * 600, f"Update number {index}")
            for index in range(5)
        ]
        self.cron.doc(TELEGRAM_JOB, 5, {"candidates": self.five})

    def keep(self, *chats: int) -> Callable[[list[dict[str, Any]]], list[str]]:
        sources = {f"telegram:{chat}" for chat in chats}
        return lambda rows: [row["key"] for row in rows if row["source"] in sources]

    def test_llm_declined_candidates_are_not_served(self) -> None:
        llm = FakeLLM(self.keep(601, 603))
        self.api.run_curator(llm=llm)
        self.assertEqual(len(llm.inputs("town_feed_ranking")[0]), 5)
        self.assertEqual({item["source"] for item in self.served()}, {"telegram:601", "telegram:603"})
        self.assertEqual(len(self.state()["items"]), 2)

    def test_heuristic_mode_keeps_all_eligible(self) -> None:
        self.api.run_curator(llm=None)
        self.assertEqual(len(self.served()), 5)

    def test_candidates_beyond_the_llm_cap_keep_heuristic_treatment(self) -> None:
        with mock.patch.object(self.api, "MAX_CANDIDATES_FOR_LLM", 3):
            llm = FakeLLM(lambda rows: [rows[0]["key"]])
            self.api.run_curator(llm=llm)
        sent = llm.inputs("town_feed_ranking")[0]
        self.assertEqual(len(sent), 3)
        served = {item["source"] for item in self.served()}
        self.assertIn(sent[0]["source"], served)
        self.assertTrue(served.isdisjoint({row["source"] for row in sent[1:]}))
        self.assertEqual(len(served), 3)

    def test_at_most_three_calendar_items_are_served(self) -> None:
        items = [
            self.prior_item(calendar_candidate(f"evt-{index}", NOW + 3600, f"Meeting {index}"), score=95.0 - index)
            for index in range(6)
        ] + [
            self.prior_item(candidate, score=50.0 - index) for index, candidate in enumerate(self.five)
        ]
        self.write_state({"version": 6, "items": items})

        served = self.served()

        calendar = [item for item in served if item["source"].startswith("calendar:")]
        self.assertEqual([item["headline"] for item in calendar], [f"Meeting {i} — today at 3 PM" for i in range(3)])
        self.assertEqual(len(served), 8)
        self.assertEqual(len(self.state()["items"]), 11)  # serve-time cap; nothing deleted

    def test_select_served_fills_to_the_feed_cap(self) -> None:
        visible = [{"source": f"calendar:{ACCOUNT}:e{i}"} for i in range(6)] + [
            {"source": f"telegram:{i}"} for i in range(12)
        ]
        served = self.api._select_served(visible)
        self.assertEqual(len(served), self.api.MAX_FEED_ITEMS)
        self.assertEqual(sum(item["source"].startswith("calendar:") for item in served), 3)


class AssistantMailCuratorTest(CuratorCase):
    def test_assistant_sent_mail_from_old_scanner_docs_is_not_curated(self) -> None:
        (self.home / "town-feed").mkdir()
        (self.home / "town-feed" / "operator.json").write_text(
            json.dumps({"assistant_senders": [ASSISTANT]}), encoding="utf-8"
        )
        self.cron.job(GMAIL_JOB, "town-gmail-scan")
        briefing = gmail_candidate("m-1", "t-1", NOW - 600, "Morning Briefing", sender=f"Assistant <{ASSISTANT}>")
        real = gmail_candidate("m-2", "t-2", NOW - 300, "Venue deposit")
        self.cron.doc(GMAIL_JOB, 5, {"candidates": [briefing, real]})
        self.api.run_curator(llm=None)
        self.assertEqual([item["source"] for item in self.served()], [f"gmail:{ACCOUNT}:m-2"])


# --- Signals ------------------------------------------------------------------


class SignalsTest(CuratorCase):
    def test_awaiting_reply_signals_are_recent_junk_free_and_newest_first(self) -> None:
        self.cron.job(GMAIL_JOB, "town-gmail-scan")

        def entry(thread: str, to: str, subject: str, days_ago: float) -> dict[str, Any]:
            return {"account": ACCOUNT, "thread_id": thread, "to": to, "subject": subject, "last_sent_ts": NOW - days_ago * DAY}

        awaiting = [
            entry("t-old", "Carol <carol@example.com>", "Contract redlines", 40),
            entry("t-mid", "Dave <dave@example.com>", "Quote for the deck", 6),
            entry("t-new", "Erin <erin@example.com>", "Dinner Friday?", 1),
            entry("t-unsub", "unsubscribe@list.example.com", "Unsubscribe", 2),
            entry("t-mailto", "leave-123@list.example.com", "unsubscribe", 2),
            entry("t-fwd", "Frank <frank@example.com>", "Fwd: Flight details", 2),
            entry("t-self", ACCOUNT, "Morning Briefing", 0.5),
            entry("t-edge", "Grace <grace@example.com>", "Venue options", 20),
        ]
        self.cron.doc(GMAIL_JOB, 5, {"candidates": [], "awaiting_reply": awaiting})

        _c, _e, _n, _s, signals, *_rest = self.api._scan_cron(NOW, {})

        self.assertEqual([item["thread_id"] for item in signals], ["t-new", "t-mid", "t-edge"])

    def test_open_loop_signals_drop_bulk_and_stale_and_sort_newest_first(self) -> None:
        self.cron.job(GMAIL_JOB, "town-gmail-scan")

        def loop(thread: str, person: str, last_days_ago: float, **extra: Any) -> dict[str, Any]:
            return {
                "account": ACCOUNT,
                "thread_id": thread,
                "person": person,
                "subject": f"Subject {thread}",
                "ask": "Confirm the date",
                "first_seen_ts": NOW - (last_days_ago + 1) * DAY,
                "last_seen_ts": NOW - last_days_ago * DAY,
                **extra,
            }

        loops = [
            loop("l-old", "Carol", 30),
            loop("l-a", "Dave", 5),
            loop("l-b", "Erin", 1),
            loop("l-bulk", "Example Billing", 1, bulk_sender=True),
            loop("l-self", ACCOUNT, 2),
        ]
        self.cron.doc(GMAIL_JOB, 5, {"candidates": [], "open_loops": loops})

        _c, _e, _n, _s, _a, signals, *_rest = self.api._scan_cron(NOW, {})

        self.assertEqual([item["thread_id"] for item in signals], ["l-b", "l-a"])


# --- Dismissed candidates ---------------------------------------------------


class DismissedInputsTest(CuratorCase):
    def test_dismissed_candidate_feeds_neither_suggestions_nor_the_day_brief(self) -> None:
        self.cron.job(GMAIL_JOB, "town-gmail-scan")
        self.cron.job(CALENDAR_JOB, "town-calendar-scan")
        dismissed = gmail_candidate("m-d", "t-d", NOW - 900, "Overdue invoice from Example Co", label="action needed")
        kept = gmail_candidate("m-k", "t-k", NOW - 600, "Venue deposit", label="action needed")
        awaiting = {"account": ACCOUNT, "thread_id": "t-a", "to": "carol@example.com", "subject": "Venue", "last_sent_ts": NOW - DAY}
        self.cron.doc(GMAIL_JOB, 5, {"candidates": [dismissed, kept], "awaiting_reply": [awaiting]})
        event = {
            "summary": "Planning sync",
            "start_iso": iso(NOW + 2 * 3600),
            "end_iso": iso(NOW + 3 * 3600),
            "all_day": False,
            "account": ACCOUNT,
        }
        self.cron.doc(CALENDAR_JOB, 5, {"candidates": [], "events": [event], "next_event": None})
        dismissed_id = f"ntk_{dismissed['content_hash'][:16]}"
        self.write_state(
            {
                "version": 6,
                "items": [],
                "decisions": {dismissed_id: {"dismissed": True, "updated_at": iso(NOW - 60), "source_activity_at": dismissed["created_at"]}},
            }
        )
        llm = FakeLLM()

        self.api.run_curator(llm=llm)

        ranking = llm.inputs("town_feed_ranking")[0]
        ranking_text = json.dumps(ranking)
        self.assertNotIn("Overdue invoice", ranking_text)
        self.assertIn("Venue deposit", json.dumps(ranking["suggestion_signals"]["action_mail"]))
        self.assertEqual(
            [row["key"] for row in ranking["suggestion_fallback_candidates"]],
            [kept["content_hash"][:16]],
        )
        brief = llm.inputs("town_day_brief")[0]
        self.assertNotIn("Overdue invoice", json.dumps(brief["top_need_to_know"]))
        self.assertEqual(len(brief["top_need_to_know"]), 1)
        self.assertNotIn(dismissed_id, {item["id"] for item in self.served()})


# --- Gmail scanner (google_scan) -------------------------------------------------


def gmail_message(
    message_id: str,
    *,
    thread_id: Optional[str] = None,
    sender: str = "Bob Example <bob@example.com>",
    to: str = ACCOUNT,
    subject: str = "Lunch next week",
    internal_date: int = 2_000_000,
    labels: Optional[list[str]] = None,
    list_unsubscribe: str = "",
) -> dict[str, Any]:
    import base64

    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": to},
        {"name": "Subject", "value": subject},
    ]
    if list_unsubscribe:
        headers.append({"name": "List-Unsubscribe", "value": list_unsubscribe})
    body = base64.urlsafe_b64encode(f"{subject}. Let me know what works for you.".encode()).decode()
    return {
        "id": message_id,
        "threadId": thread_id or f"thread-{message_id}",
        "internalDate": str(internal_date),
        "labelIds": list(labels if labels is not None else ["INBOX", "UNREAD"]),
        "payload": {"mimeType": "text/plain", "headers": headers, "body": {"data": body}},
    }


class _Call:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return copy.deepcopy(self._result)


class FakeGmail:
    """The slice of the Gmail v1 client scan_gmail/label_gmail_messages use."""

    def __init__(self, messages: list[dict[str, Any]], label_names: list[str]) -> None:
        self.store = {message["id"]: message for message in messages}
        self.label_list = [
            {"id": f"Label_{index}", "name": name, "type": "user", "color": {"backgroundColor": "#000000"}}
            for index, name in enumerate(label_names)
        ]
        self.modified: list[tuple[str, dict[str, Any]]] = []

    def users(self) -> "FakeGmail":
        return self

    def getProfile(self, userId: str) -> _Call:
        return _Call({"emailAddress": ACCOUNT})

    def messages(self) -> "FakeGmail.Messages":
        return FakeGmail.Messages(self)

    def labels(self) -> "FakeGmail.Labels":
        return FakeGmail.Labels(self)

    def threads(self) -> "FakeGmail.Threads":
        return FakeGmail.Threads(self)

    def label_id(self, name: str) -> str:
        return next(label["id"] for label in self.label_list if label["name"] == name)

    class Messages:
        def __init__(self, gmail: "FakeGmail") -> None:
            self.gmail = gmail

        def list(self, userId: str, q: str = "", maxResults: int = 100, **_: Any) -> _Call:
            if q.startswith("in:inbox"):
                wanted = "INBOX"
            elif q.startswith("in:sent") and "to:" not in q:
                wanted = "SENT"
            else:
                return _Call({"messages": []})
            found = [m for m in self.gmail.store.values() if wanted in m["labelIds"]]
            found.sort(key=lambda message: int(message["internalDate"]), reverse=True)
            return _Call({"messages": [{"id": m["id"]} for m in found][:maxResults]})

        def get(self, userId: str, id: str, format: str = "full") -> _Call:
            return _Call(self.gmail.store[id])

        def modify(self, userId: str, id: str, body: dict[str, Any]) -> _Call:
            self.gmail.modified.append((id, copy.deepcopy(body)))
            return _Call({"id": id})

    class Labels:
        def __init__(self, gmail: "FakeGmail") -> None:
            self.gmail = gmail

        def list(self, userId: str) -> _Call:
            return _Call({"labels": self.gmail.label_list})

        def get(self, userId: str, id: str) -> _Call:
            return _Call(next(label for label in self.gmail.label_list if label["id"] == id))

        def create(self, userId: str, body: dict[str, Any]) -> _Call:
            raise AssertionError("tests pre-create every Town label")

        def patch(self, userId: str, id: str, body: dict[str, Any]) -> _Call:
            return _Call({"id": id})

    class Threads:
        def __init__(self, gmail: "FakeGmail") -> None:
            self.gmail = gmail

        def get(self, userId: str, id: str, format: str = "full") -> _Call:
            return _Call({"messages": [m for m in self.gmail.store.values() if m["threadId"] == id]})


class _Credentials:
    valid = True
    expired = False
    refresh_token = None
    granted_scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    @classmethod
    def from_authorized_user_file(cls, path: str, scopes: Any = None) -> "_Credentials":
        return cls()


PROFILE = {"self_addresses": [ALIAS], "assistant_senders": [ASSISTANT]}


class GmailScannerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.scan = support.load_module(support.DASHBOARD / "google_scan.py", "town_test_r4_google_scan")
        token = self.tmp / "google_token.json"
        token.write_text("{}", encoding="utf-8")
        self.scan._gmail_token_paths = lambda: [token]
        self.scan.get_town_inbox_settings = lambda: (
            True,
            self.scan.MIN_LABEL_CONFIDENCE,
            dict(self.scan.DEFAULT_INBOX_TAXONOMY),
        )
        self.label_calls: list[tuple[str, str, str]] = []

        def record_label(service: Any, account: str, message_id: str, label: str) -> str:
            self.label_calls.append((account, message_id, label))
            return "Label_recorded"

        self.scan._add_gmail_label = record_label

    def gmail(self, messages: list[dict[str, Any]]) -> FakeGmail:
        return FakeGmail(messages, list(self.scan.DEFAULT_INBOX_TAXONOMY))

    def run_scan(self, gmail: FakeGmail) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        google_modules = {
            "google.auth.transport.requests": SimpleNamespace(Request=object),
            "google.oauth2.credentials": SimpleNamespace(Credentials=_Credentials),
            "googleapiclient.discovery": SimpleNamespace(build=lambda *a, **k: gmail),
        }
        labeling: list[dict[str, Any]] = []
        with mock.patch.dict(sys.modules, google_modules):
            candidates, errors = self.scan.scan_gmail(NOW, {}, {}, labeling, profile=PROFILE)
        self.assertEqual(errors, [])
        return candidates, labeling

    def test_assistant_and_self_to_self_mail_are_not_candidates(self) -> None:
        gmail = self.gmail(
            [
                gmail_message("m-assistant", sender=f"Assistant <{ASSISTANT}>", subject="Morning Briefing"),
                gmail_message("m-self", sender=f"Alice <{ALIAS}>", to=f"Alice <{ACCOUNT}>", subject="Notes to self"),
                gmail_message("m-bob", subject="Lunch next week"),
            ]
        )
        candidates, labeling = self.run_scan(gmail)
        self.assertEqual([c["message_id"] for c in candidates], ["m-bob"])
        self.assertEqual({m["message_id"] for m in labeling}, {"m-bob"})
        self.assertFalse(any(key.startswith("_recipients") or key == "_list_unsubscribe" for key in candidates[0]))

    def test_inbound_exhaust_never_gets_a_label(self) -> None:
        gmail = self.gmail(
            [
                gmail_message("m-assistant", sender=f"Assistant <{ASSISTANT}>", subject="Morning Briefing"),
                gmail_message("m-self", sender=f"Alice <{ALIAS}>", to=ACCOUNT, subject="Notes to self"),
            ]
        )
        candidates = [self.scan._gmail_candidate(m, ACCOUNT) for m in gmail.store.values()]
        result = self.scan.label_gmail_messages(candidates, {ACCOUNT: gmail}, {}, llm=None, profile=PROFILE)
        self.assertEqual(self.label_calls, [])
        self.assertEqual(gmail.modified, [])
        reasons = {item["message_id"]: item["reason"] for item in result["summary"]["skipped"]}
        self.assertEqual(reasons, {"m-assistant": "assistant_sender", "m-self": "self_to_self"})

    def label_outbound(self, gmail: FakeGmail, state: dict[str, Any]) -> dict[str, Any]:
        candidates = [
            self.scan._gmail_candidate(message, ACCOUNT)
            for message in gmail.store.values()
            if "SENT" in message["labelIds"]
        ]
        return self.scan.label_gmail_messages(candidates, {ACCOUNT: gmail}, state, llm=None, profile=PROFILE)

    def test_outbound_junk_gets_no_awaiting_label_or_tracked_entry(self) -> None:
        sent = ["SENT"]
        gmail = self.gmail(
            [
                gmail_message("o-unsub", sender=ACCOUNT, to="unsubscribe@list.example.com", subject="Please remove me", labels=sent),
                gmail_message("o-fwd", sender=ACCOUNT, to="Carol <carol@example.com>", subject="Fwd: Flight details", labels=sent),
                gmail_message("o-self", sender=ACCOUNT, to=f"Alice <{ALIAS}>", subject="Morning Briefing", labels=sent),
                gmail_message(
                    "o-mailto",
                    thread_id="thread-news",
                    sender=ACCOUNT,
                    to="leave-77@news.example.com",
                    subject="Remove",
                    labels=sent,
                    internal_date=2_000_500,
                ),
                gmail_message(
                    "i-news",
                    thread_id="thread-news",
                    sender="Example News <news@example.com>",
                    list_unsubscribe="<mailto:leave-77@news.example.com?subject=unsubscribe>, <https://news.example.com/u>",
                ),
                gmail_message("o-real", sender=ACCOUNT, to="Dave <dave@example.com>", subject="Quote for the deck", labels=sent),
            ]
        )
        state: dict[str, Any] = {}
        self.label_outbound(gmail, state)

        self.assertEqual(self.label_calls, [(ACCOUNT, "o-real", self.scan.AWAITING_REPLY_LABEL)])
        self.assertEqual(set(state["awaiting_reply_threads"][ACCOUNT]), {"thread-o-real"})

    def test_existing_awaiting_labels_and_entries_are_never_removed(self) -> None:
        gmail = self.gmail([])
        awaiting_id = gmail.label_id(self.scan.AWAITING_REPLY_LABEL)
        gmail.store["o-fwd"] = gmail_message(
            "o-fwd", sender=ACCOUNT, to="carol@example.com", subject="Fwd: Itinerary", labels=["SENT", awaiting_id]
        )
        tracked = {"message_id": "o-earlier", "labeled_at": iso(NOW - 40 * DAY), "to": "carol@example.com", "subject": "Fwd: Itinerary", "last_sent_ts": NOW - 40 * DAY}
        state: dict[str, Any] = {"awaiting_reply_threads": {ACCOUNT: {"thread-o-fwd": dict(tracked)}}}

        self.label_outbound(gmail, state)

        self.assertEqual(self.label_calls, [])
        self.assertFalse(any("removeLabelIds" in body for _id, body in gmail.modified))
        self.assertEqual(state["awaiting_reply_threads"][ACCOUNT]["thread-o-fwd"], tracked)

    def test_normal_outbound_is_labeled_as_today(self) -> None:
        gmail = self.gmail(
            [gmail_message("o-real", sender=ACCOUNT, to="Dave <dave@example.com>", subject="Quote", labels=["SENT"])]
        )
        state: dict[str, Any] = {}
        result = self.label_outbound(gmail, state)
        self.assertEqual(self.label_calls, [(ACCOUNT, "o-real", self.scan.AWAITING_REPLY_LABEL)])
        self.assertEqual(state["awaiting_reply_threads"][ACCOUNT]["thread-o-real"]["message_id"], "o-real")
        self.assertEqual(result["summary"]["applied"], 1)

    def test_awaiting_payload_filters_at_read_time_newest_first(self) -> None:
        def tracked(to: str, subject: str, days_ago: float) -> dict[str, Any]:
            return {"message_id": "x", "labeled_at": iso(NOW), "to": to, "subject": subject, "last_sent_ts": NOW - days_ago * DAY}

        threads = {
            ACCOUNT: {
                "t-old": tracked("carol@example.com", "Contract", 50),
                "t-a": tracked("dave@example.com", "Quote", 3),
                "t-b": tracked("erin@example.com", "Dinner", 1),
                "t-unsub": tracked("unsubscribe@list.example.com", "Unsubscribe", 1),
                "t-fw": tracked("frank@example.com", "Fw: photos", 1),
                "t-self": tracked(ALIAS, "Morning Briefing", 0.2),
            }
        }
        snapshot = copy.deepcopy(threads)
        entries = self.scan._awaiting_reply_entries(threads, profile=PROFILE, now=NOW)
        self.assertEqual([entry["thread_id"] for entry in entries], ["t-b", "t-a"])
        self.assertEqual(threads, snapshot)  # read-time only

    def test_open_loop_payload_drops_bulk_self_and_stale(self) -> None:
        def loop(days_ago: float, **extra: Any) -> dict[str, Any]:
            return {
                "person": "Bob Example",
                "subject": "Deposit",
                "ask": "Confirm the deposit",
                "deadline_iso": None,
                "first_seen_ts": NOW - (days_ago + 1) * DAY,
                "last_seen_ts": NOW - days_ago * DAY,
                **extra,
            }

        loops = {
            ACCOUNT: {
                "l-a": loop(4, from_address="bob@example.com", bulk_sender=False),
                "l-b": loop(1),
                "l-bulk": loop(1, from_address="billing@example.com", bulk_sender=True),
                "l-noreply": loop(1, person="no-reply@example.com"),
                "l-assistant": loop(1, from_address=ASSISTANT),
                "l-stale": loop(25),
            }
        }
        entries = self.scan._open_loop_entries(loops, profile=PROFILE, now=NOW)
        self.assertEqual([entry["thread_id"] for entry in entries], ["l-b", "l-a"])


if __name__ == "__main__":
    unittest.main()
