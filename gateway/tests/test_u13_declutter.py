"""U13 — declutter: the kanban connect helper, home-time callouts, and the
suggestion post-filter.

Every fixture is synthetic (Alice Example, home in Boston, example.com). No
test reads or writes live ``~/.hermes`` state or reaches an LLM: hermes-agent
modules are ``sys.modules`` stand-ins, cron output is an in-memory fake, the
LLM is a stub that captures its input, and ``now`` is pinned.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Iterator, Optional
from unittest import mock
from zoneinfo import ZoneInfo

import support

EASTERN = ZoneInfo("America/New_York")
# Sun 2026-10-11 10:42 AM ET.
NOW = datetime(2026, 10, 11, 10, 42, tzinfo=EASTERN).timestamp()
HOUR = 3600.0
ACCOUNT = "alice@example.com"
PROFILE = {
    "name": "Alice Example",
    "preferred_name": "Alice",
    "home_timezone": "America/New_York",
    "home_city": "Boston",
}
CALENDAR_JOB = "job-calendar"
SELF = {"email": ACCOUNT, "name": "Alice", "self": True, "response": "accepted"}
BOB = {"email": "bob@example.com", "name": "Bob Example", "response": "accepted"}


def iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def row(summary: str, start_hours: float, end_hours: float, **extra: Any) -> dict[str, Any]:
    """A scanner ``events`` row relative to NOW."""
    event = {
        "summary": summary,
        "start_iso": iso(NOW + start_hours * HOUR),
        "end_iso": iso(NOW + end_hours * HOUR),
        "all_day": False,
        "account": ACCOUNT,
        "event_type": "default",
    }
    event.update(extra)
    return event


def suggestion(sid: str, label: str, kind: str = "followup") -> dict[str, str]:
    return {"id": sid, "label": label, "prompt": f"{label}.", "kind": kind}


class ApiCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.api = support.load_plugin_api(self.state_dir)


# --- K: kanban connect --------------------------------------------------------


class FakeConnection:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def execute(self, query: str, *_: Any) -> Any:
        self.queries.append(query)
        return SimpleNamespace(fetchone=lambda: {"latest": 7})


def closing_factory(conn: Any, calls: list[str], label: str) -> Any:
    @contextlib.contextmanager
    def connect_closing() -> Iterator[Any]:
        calls.append(label)
        yield conn

    return connect_closing


class KanbanConnectTest(ApiCase):
    def stub_hermes_cli(self, *, with_connect_module: bool) -> tuple[list[str], FakeConnection]:
        """Stand-ins for ``hermes_cli`` (+ ``kanban_db``, + ``kanban_db_connect``)."""
        calls: list[str] = []
        conn = FakeConnection()
        package = ModuleType("hermes_cli")
        kanban_db = ModuleType("hermes_cli.kanban_db")
        kanban_db.connect_closing = closing_factory(conn, calls, "kanban_db")  # type: ignore[attr-defined]
        kanban_db.list_tasks = lambda conn, include_archived=False: []  # type: ignore[attr-defined]
        package.kanban_db = kanban_db  # type: ignore[attr-defined]
        patcher = mock.patch.dict(sys.modules)
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop("hermes_cli.kanban_db_connect", None)
        sys.modules["hermes_cli"] = package
        sys.modules["hermes_cli.kanban_db"] = kanban_db
        if with_connect_module:
            connect = ModuleType("hermes_cli.kanban_db_connect")
            connect.connect_closing = closing_factory(conn, calls, "kanban_db_connect")  # type: ignore[attr-defined]
            package.kanban_db_connect = connect  # type: ignore[attr-defined]
            sys.modules["hermes_cli.kanban_db_connect"] = connect
        return calls, conn

    def test_prefers_kanban_db_connect(self) -> None:
        calls, conn = self.stub_hermes_cli(with_connect_module=True)
        with self.api._kanban_connect() as opened:
            self.assertIs(opened, conn)
        self.assertEqual(calls, ["kanban_db_connect"])

    def test_falls_back_to_kanban_db(self) -> None:
        calls, conn = self.stub_hermes_cli(with_connect_module=False)
        with self.api._kanban_connect() as opened:
            self.assertIs(opened, conn)
        self.assertEqual(calls, ["kanban_db"])

    def test_kanban_route_uses_the_helper(self) -> None:
        calls, conn = self.stub_hermes_cli(with_connect_module=True)
        # v0.21.x: the old alias is gone; the route must not reach for it.
        del sys.modules["hermes_cli.kanban_db"].connect_closing
        summary = self.api.get_kanban_summary()
        self.assertEqual(calls, ["kanban_db_connect"])
        self.assertEqual(summary["latest_event_id"], 7)
        self.assertEqual([column["tasks"] for column in summary["columns"]],
                         [[] for _ in self.api.BOARD_COLUMNS])

    def test_no_route_calls_connect_closing_directly(self) -> None:
        source = inspect.getsource(self.api)
        self.assertNotIn("kanban_db.connect_closing()", source)
        for handler in (self.api._create_kanban_task, self.api.create_composer_task,
                        self.api.complete_kanban_task, self.api.get_kanban_summary):
            self.assertIn("_kanban_connect()", inspect.getsource(handler))


# --- C: day brief timezone callouts ------------------------------------------


class BriefLLM:
    """Returns a brief with a timezone and a conflict callout; captures input."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.suggestions: list[dict[str, str]] = []

    def complete_structured(self, **kwargs: Any) -> Any:
        data = json.loads(kwargs["input"][0]["text"])
        self.calls.append({"schema": kwargs["schema_name"], "input": data,
                           "instructions": kwargs["instructions"]})
        if kwargs["schema_name"] == "town_day_brief":
            return SimpleNamespace(parsed={
                "opener": "Two meetings left today.",
                "day_summary": "Light day — 2 meetings.",
                "callouts": [
                    {"kind": "timezone", "text": "Gala is 9:00 PM ET, which is 6:00 PM Pacific."},
                    {"kind": "conflict", "text": "Design review overlaps Lunch at 12:30 PM."},
                ],
            })
        rows = data["ranking_candidates"] if isinstance(data, dict) else data
        return SimpleNamespace(parsed={
            "items": [{"key": entry["key"], "headline": entry["headline"],
                       "summary": entry["summary"], "score": 70} for entry in rows],
            "suggestions": self.suggestions,
        })


class DayBriefTimezoneTest(ApiCase):
    def brief_for(self, status: str) -> tuple[dict[str, Any], str]:
        llm = BriefLLM()
        where = {"status": status, "city": "Boston", "label": "Home in Boston", "evidence": []}
        brief, error = self.api._generate_day_brief(
            llm,
            schedule_events=[row("Design review", 2, 3, attendees=[SELF, BOB])],
            schedule_context=None,
            next_event=None,
            awaiting_reply=[],
            top_headlines=[],
            known_contacts=None,
            now=NOW,
            operator={"name": "Alice Example", "where": where},
            local_tz=EASTERN,
        )
        self.assertIsNone(error)
        return brief, llm.calls[0]["instructions"]

    def kinds(self, brief: dict[str, Any]) -> list[str]:
        return [callout["kind"] for callout in brief["callouts"]]

    def test_timezone_callout_dropped_at_home_and_just_returned(self) -> None:
        for status in ("home", "just_returned"):
            with self.subTest(status=status):
                brief, _instructions = self.brief_for(status)
                self.assertEqual(self.kinds(brief), ["conflict"])

    def test_timezone_callout_kept_while_away(self) -> None:
        for status in ("away", "traveling_today"):
            with self.subTest(status=status):
                brief, _instructions = self.brief_for(status)
                self.assertEqual(self.kinds(brief), ["timezone", "conflict"])

    def test_prompt_says_no_timezone_callout_at_home(self) -> None:
        _brief, instructions = self.brief_for("home")
        self.assertIn("When operator.where.status is home or just_returned, produce NO timezone",
                      instructions)

    def test_unknown_where_keeps_callouts(self) -> None:
        brief = {"day_summary": "x", "callouts": [{"kind": "timezone", "text": "t"}]}
        self.assertEqual(self.api._drop_home_timezone_callouts(dict(brief), None), brief)


# --- D: suggestion post-filter ------------------------------------------------


class SuggestionFilterTest(ApiCase):
    CONTEXT = {"conflicts": [{"a": "Design review", "b": "Lunch with Bob"}]}

    def filtered(self, suggestions: list[dict[str, str]], events: list[dict[str, Any]],
                 context: Optional[dict[str, Any]] = None) -> list[str]:
        kept = self.api._filter_suggestions(suggestions, events, context, NOW)
        return [entry["id"] for entry in kept]

    def test_prep_for_an_event_in_3h_is_dropped(self) -> None:
        events = [row("Example Gala Dinner", 3, 6, location="Example Hall")]
        prep = suggestion("s1", "Prepare for tonight's Example Gala Dinner")
        self.assertEqual(self.filtered([prep], events), [])

    def test_same_label_for_an_event_in_30h_is_kept(self) -> None:
        events = [row("Example Gala Dinner", 30, 33, location="Example Hall")]
        prep = suggestion("s1", "Prepare for tonight's Example Gala Dinner")
        self.assertEqual(self.filtered([prep], events), ["s1"])

    def test_prep_verbs_and_distinctive_names(self) -> None:
        events = [
            row("Weekly Sync", 1, 1.5, conference="Zoom"),
            row("Gala", 2, 4, location="Example Hall"),
            row("Vitamins", 0.5, 0.75),
        ]
        suggestions = [
            suggestion("join", "Join the weekly sync"),
            suggestion("ready", "Get ready for Weekly Sync"),
            suggestion("short", "Prep for the Gala"),  # one short word: not distinctive
            suggestion("minor", "Prepare vitamins for the week"),  # not meaningful
            suggestion("verb", "Review the Weekly Sync notes"),  # not a prep verb
        ]
        self.assertEqual(self.filtered(suggestions, events), ["short", "minor", "verb"])

    def test_conflict_pair_dropped_but_single_mention_kept(self) -> None:
        both = suggestion("both", "Pick between Design review and Lunch with Bob", "maintenance")
        one = suggestion("one", "Send Bob the agenda for Lunch with Bob")
        self.assertEqual(self.filtered([both, one], [], self.CONTEXT), ["one"])

    def test_unrelated_untouched_and_no_backfill(self) -> None:
        events = [row("Example Gala Dinner", 3, 6, location="Example Hall")]
        suggestions = [
            suggestion("prep", "Prepare for tonight's Example Gala Dinner"),
            suggestion("conflict", "Pick between Design review and Lunch with Bob"),
            suggestion("reply", "Reply to Carol about the invoice"),
            suggestion("digest", "Summarize this week's investor updates", "digest"),
        ]
        kept = self.api._filter_suggestions(suggestions, events, self.CONTEXT, NOW)
        self.assertEqual(kept, suggestions[2:])
        self.assertIs(kept[0], suggestions[2])


class CuratorSuggestionTest(ApiCase):
    """The filter runs inside run_curator, after the LLM, before persisting."""

    def setUp(self) -> None:
        super().setUp()
        home = self.state_dir / "hermes-home"
        (home / "town-feed").mkdir(parents=True)
        (home / "town-feed" / "operator.json").write_text(json.dumps(PROFILE), encoding="utf-8")
        self.cron_root = self.tmp / "cron-output"
        sessions = ModuleType("hermes_state")

        class SessionDB:
            def list_sessions_rich(self, **_: Any) -> list[dict[str, Any]]:
                return []

            def close(self) -> None:
                pass

        sessions.SessionDB = SessionDB  # type: ignore[attr-defined]
        cron = ModuleType("cron")
        cron.__path__ = []  # type: ignore[attr-defined]
        jobs = ModuleType("cron.jobs")
        jobs.get_cron_output_dir = lambda: self.cron_root  # type: ignore[attr-defined]
        jobs.list_jobs = lambda include_disabled=False: [  # type: ignore[attr-defined]
            {"id": CALENDAR_JOB, "name": "town-calendar-scan", "last_status": "ok",
             "last_run_at": iso(NOW - 60)}
        ]
        for patcher in (
            mock.patch.dict(sys.modules, {"hermes_state": sessions, "cron": cron, "cron.jobs": jobs}),
            mock.patch("time.time", return_value=NOW),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def calendar_doc(self, events: list[dict[str, Any]]) -> None:
        payload = {
            "candidates": [{
                "key": "cal-review",
                "content_hash": "c" * 64,
                "source": f"calendar:{ACCOUNT}:review",
                "headline": "Design review — in 2h",
                "summary": "Room 4 · 2 attendees",
                "created_at": events[0]["start_iso"],
            }],
            "events": events,
        }
        timestamp = NOW - 300
        stamp = datetime.fromtimestamp(timestamp, tz=timezone.utc)
        path = self.cron_root / CALENDAR_JOB / f"{stamp.strftime('%Y-%m-%d_%H-%M-%S')}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"# Cron Job: town-calendar-scan\n\n**Job ID:** {CALENDAR_JOB}\n---\n\n"
            + json.dumps(payload) + "\n",
            encoding="utf-8",
        )
        os.utime(path, ns=(int(timestamp * 1e9), int(timestamp * 1e9)))

    def state(self) -> dict[str, Any]:
        return json.loads((self.state_dir / "feed-state.json").read_text(encoding="utf-8"))

    def test_curator_persists_filtered_suggestions_and_home_brief(self) -> None:
        self.calendar_doc([
            row("Design review", 2, 3, attendees=[SELF, BOB], location="Room 4"),
            row("Lunch with Bob", 2.5, 3.5, attendees=[SELF, BOB]),
        ])
        llm = BriefLLM()
        llm.suggestions = [
            suggestion("prep", "Prepare for the Design review"),
            suggestion("conflict", "Pick between Design review and Lunch with Bob", "maintenance"),
            suggestion("reply", "Reply to Carol about the invoice"),
        ]
        self.api.run_curator(llm=llm)
        state = self.state()
        self.assertEqual([entry["id"] for entry in state["suggestions"]], ["reply"])
        # Home (no travel evidence): the persisted brief has no timezone callout.
        self.assertEqual([callout["kind"] for callout in state["day_brief"]["callouts"]],
                         ["conflict"])

    def test_filtered_to_empty_replaces_the_previous_set(self) -> None:
        self.calendar_doc([row("Design review", 2, 3, attendees=[SELF, BOB], location="Room 4")])
        llm = BriefLLM()
        llm.suggestions = [suggestion("prep", "Prepare for the Design review")]
        (self.state_dir / "feed-state.json").write_text(json.dumps({
            "version": 6, "items": [],
            "suggestions": [suggestion("old", "Prepare for the Design review")],
        }), encoding="utf-8")
        self.api.run_curator(llm=llm)
        self.assertEqual(self.state()["suggestions"], [])


if __name__ == "__main__":
    unittest.main()
