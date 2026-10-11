"""S1 — Where & when: operator profile, location, local time, Up next.

Every fixture is synthetic (Alice Example, home in Boston, example.com). No
test reads or writes live ``~/.hermes`` state, calls Google, or reaches an
LLM: the profile lives in a temp Hermes home, cron output is an in-memory
fake, the LLM is a stub that captures its input, and ``now`` is pinned.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Optional
from unittest import mock
from zoneinfo import ZoneInfo

import support

EASTERN = ZoneInfo("America/New_York")
# Sun 2026-10-11 10:42 AM ET.
NOW_DT = datetime(2026, 10, 11, 10, 42, tzinfo=EASTERN)
NOW = NOW_DT.timestamp()
HOUR = 3600.0
ACCOUNT = "alice@example.com"
PROFILE = {
    "name": "Alice Example",
    "preferred_name": "Alice",
    "home_timezone": "America/New_York",
    "home_city": "Boston",
}
CALENDAR_JOB = "job-calendar"


def where_module() -> ModuleType:
    return support.load_module(support.DASHBOARD / "where.py", "town_test_where")


def profile_module() -> ModuleType:
    return support.load_module(support.DASHBOARD / "operator_profile.py", "town_test_operator_profile")


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


def all_day(summary: str, start: str, end: str, **extra: Any) -> dict[str, Any]:
    event = {
        "summary": summary,
        "start_iso": datetime.fromisoformat(start).replace(tzinfo=EASTERN).isoformat(),
        "end_iso": datetime.fromisoformat(end).replace(tzinfo=EASTERN).isoformat(),
        "all_day": True,
        "account": ACCOUNT,
        "event_type": "default",
    }
    event.update(extra)
    return event


SELF = {"email": ACCOUNT, "name": "Alice", "self": True, "response": "accepted"}
BOB = {"email": "bob@example.com", "name": "Bob", "response": "accepted"}


# --- Flight detection --------------------------------------------------------


class FlightDetectionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.where = where_module()

    def test_flight_titles_positive(self) -> None:
        for title in (
            "SQ 22 SIN→EWR",
            "Flight to Lisbon",
            "UA123",
            "Flight from Paris to Rome",
            "JFK - LIS",
            "Example Airlines 418 — LIS to BOS",
            "B6 615",
            "My flight from Rome",
            "TP 202: flight to Lisbon",
        ):
            self.assertTrue(self.where.is_flight_title(title), title)

    def test_flight_titles_negative(self) -> None:
        for title in (
            "Sync 22",
            "Q3 planning",
            "Q3 2026 planning",
            "Book flight to Lisbon",
            "CS 101",
            "WK 3 review",
            "API-SDK sync",
            "Lunch",
            "Lunch before flight to Lisbon",
            "OKR > KPI review",
        ):
            self.assertFalse(self.where.is_flight_title(title), title)

    def test_origin_and_destination_from_iata_pair(self) -> None:
        info = self.where.flight_info(row("SQ 22 SIN→EWR", -10, -2))
        self.assertEqual(info["code"], "SQ 22")
        self.assertEqual(info["origin"]["iata"], "SIN")
        self.assertEqual(info["origin"]["city"], "Singapore")
        self.assertEqual(info["destination"]["iata"], "EWR")
        self.assertEqual(info["destination"]["metro"], "New York")
        self.assertEqual(info["destination"]["timezone"], "America/New_York")

    def test_destination_from_phrase_and_origin_from_location(self) -> None:
        info = self.where.flight_info(
            row("Flight to Lisbon (TP 202)", 2, 9, location="Boston Logan Airport (BOS), Terminal E")
        )
        self.assertEqual(info["code"], "TP 202")
        self.assertEqual(info["destination"], {"city": "Lisbon", "timezone": "Europe/Lisbon"})
        self.assertEqual(info["origin"]["iata"], "BOS")

    def test_google_auto_added_flight_uses_event_type_cues(self) -> None:
        auto = row("TP 218", -9, -2, event_type="fromGmail", location="Lisbon Airport (LIS)")
        self.assertIsNotNone(self.where.flight_info(auto))
        self.assertEqual(self.where.flight_info(auto)["origin"]["city"], "Lisbon")
        arrival_zone = row("Flight to Example Town", -9, -2, event_tz="Europe/Lisbon",
                           end_tz="America/New_York", event_type="fromGmail")
        self.assertEqual(
            self.where.flight_info(arrival_zone)["destination"]["timezone"], "America/New_York"
        )
        dinner = row("Dinner at Example Bistro", 8, 10, event_type="fromGmail")
        self.assertIsNone(self.where.flight_info(dinner))

    def test_lodging_is_travel_but_meetings_are_not(self) -> None:
        hotel = all_day("Hotel Example", "2026-10-08", "2026-10-11",
                        location="1 Example Rua, Lisbon, Portugal")
        self.assertTrue(self.where.is_travel_row(hotel))
        self.assertFalse(self.where.is_travel_row(row("Design review", -3, -2)))
        self.assertFalse(self.where.is_travel_row(row("Check-in call", -3, -2.5)))


# --- Calendar scanner: recent_travel -----------------------------------------


def google_event(event_id: str, summary: str, start: datetime, end: datetime, **extra: Any) -> dict[str, Any]:
    event = {
        "id": event_id,
        "iCalUID": f"{event_id}@example.com",
        "status": "confirmed",
        "summary": summary,
        "start": {"dateTime": start.isoformat(), "timeZone": "America/New_York"},
        "end": {"dateTime": end.isoformat(), "timeZone": "America/New_York"},
    }
    event.update(extra)
    return event


class ScannerRecentTravelTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._home = tempfile.TemporaryDirectory()
        with mock.patch.dict(os.environ, {"HERMES_HOME": cls._home.name}):
            cls.scanner = support.load_script("town-calendar-scan.py")

    @classmethod
    def tearDownClass(cls) -> None:
        cls._home.cleanup()

    def setUp(self) -> None:
        patcher = mock.patch.object(self.scanner, "_user_timezone", return_value=EASTERN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_recent_travel_keeps_flights_and_stays_ended_within_36h(self) -> None:
        landed = google_event("f1", "TP 217 LIS→BOS", NOW_DT - timedelta(hours=13),
                              NOW_DT - timedelta(hours=6))
        landed["end"]["timeZone"] = "America/New_York"
        landed["start"]["timeZone"] = "Europe/Lisbon"
        old = google_event("f0", "TP 202 BOS→LIS", NOW_DT - timedelta(hours=60),
                           NOW_DT - timedelta(hours=50))
        meeting = google_event("m1", "Design review", NOW_DT - timedelta(hours=3),
                               NOW_DT - timedelta(hours=2))
        hotel = {
            "id": "h1",
            "status": "confirmed",
            "summary": "Hotel Example",
            "location": "1 Example Rua, Lisbon, Portugal",
            "start": {"date": "2026-10-08"},
            "end": {"date": "2026-10-11"},
        }
        upcoming = google_event("u1", "Planning", NOW_DT + timedelta(hours=2),
                                NOW_DT + timedelta(hours=3))
        recent = [(ACCOUNT, "Personal", event) for event in (landed, old, meeting, hotel)]
        payload = self.scanner._build_payload(
            [(ACCOUNT, "Personal", upcoming)], NOW_DT, (ACCOUNT,), recent
        )
        self.assertIsNotNone(payload)
        summaries = [entry["summary"] for entry in payload["recent_travel"]]
        self.assertEqual(summaries, ["Hotel Example", "TP 217 LIS→BOS"])
        flight = payload["recent_travel"][1]
        self.assertEqual(flight["event_tz"], "Europe/Lisbon")
        self.assertEqual(flight["end_tz"], "America/New_York")
        for field in ("start_iso", "end_iso", "all_day", "account", "calendar", "event_type"):
            self.assertIn(field, flight)
        self.assertEqual([entry["summary"] for entry in payload["events"]], ["Planning"])

    def test_recent_travel_alone_still_produces_a_payload(self) -> None:
        landed = google_event("f1", "UA 900 LIS→BOS", NOW_DT - timedelta(hours=10),
                              NOW_DT - timedelta(hours=2))
        payload = self.scanner._build_payload([], NOW_DT, (ACCOUNT,), [(ACCOUNT, "P", landed)])
        self.assertIsNotNone(payload)
        self.assertEqual(payload["events"], [])
        self.assertEqual(len(payload["recent_travel"]), 1)
        self.assertIsNone(self.scanner._build_payload([], NOW_DT, (ACCOUNT,), []))


# --- infer_where -------------------------------------------------------------


class InferWhereTest(unittest.TestCase):
    def setUp(self) -> None:
        self.where = where_module()

    def infer(self, events=(), recent=(), profile: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        return self.where.infer_where(list(events), list(recent), profile or PROFILE, NOW)

    def test_no_evidence_is_home(self) -> None:
        result = self.infer([row("Design review", 2, 3, attendees=[SELF, BOB])])
        self.assertEqual(result["status"], "home")
        self.assertEqual(result["city"], "Boston")
        self.assertEqual(result["timezone"], "America/New_York")
        self.assertEqual(result["label"], "Home in Boston")
        self.assertEqual(result["evidence"], [])

    def test_landed_six_hours_ago_from_abroad_is_just_returned(self) -> None:
        flight = row("TP 217 LIS→BOS", -13, -6, event_tz="Europe/Lisbon")
        result = self.infer(recent=[flight])
        self.assertEqual(result["status"], "just_returned")
        self.assertEqual(result["city"], "Boston")
        self.assertEqual(result["label"], "Back in Boston after Lisbon")
        self.assertIn("From Lisbon", result["evidence"])
        self.assertTrue(any("landed 6h ago" in line for line in result["evidence"]))
        self.assertEqual(result["since"], flight["end_iso"])
        self.assertLessEqual(len(result["evidence"]), 3)

    def test_connecting_legs_report_the_trip_origin(self) -> None:
        first = row("TP 1350 LIS→ZRH", -16, -13)
        second = row("LX 52 ZRH→BOS", -12, -4)
        result = self.infer(recent=[first, second])
        self.assertEqual(result["label"], "Back in Boston after Lisbon")

    def test_without_home_city_the_home_zone_decides(self) -> None:
        west = {"home_timezone": "America/Los_Angeles"}
        result = self.infer([], [row("UA 900 LIS→OAK", -13, -6)], west)
        self.assertEqual(result["status"], "just_returned")
        # Satellite airports read as their metro.
        self.assertEqual(result["label"], "Back in San Francisco after Lisbon")
        result = self.infer([], [row("UA 901 OAK→LIS", -13, -6)], west)
        self.assertEqual((result["status"], result["city"]), ("away", "Lisbon"))

    def test_landed_elsewhere_is_away_with_offset(self) -> None:
        result = self.infer(recent=[row("TP 202 BOS→LIS", -9, -3)])
        self.assertEqual(result["status"], "away")
        self.assertEqual(result["city"], "Lisbon")
        self.assertEqual(result["timezone"], "Europe/Lisbon")
        self.assertEqual(result["label"], "In Lisbon · +5h from home")

    def test_flight_later_today_is_traveling_today(self) -> None:
        result = self.infer([row("Flight to Lisbon (TP 202)", 8, 15)])
        self.assertEqual(result["status"], "traveling_today")
        self.assertEqual(result["label"], "Flying to Lisbon today")
        self.assertEqual(result["timezone"], "Europe/Lisbon")
        self.assertIn("until", result)

    def test_active_three_night_hotel_in_another_city_is_away(self) -> None:
        hotel = all_day("Hotel Example", "2026-10-10", "2026-10-13",
                        location="1 Example Rua, Lisbon, Portugal")
        result = self.infer([hotel])
        self.assertEqual(result["status"], "away")
        self.assertEqual(result["city"], "Lisbon")
        self.assertEqual(result["timezone"], "Europe/Lisbon")
        self.assertEqual(result["label"], "In Lisbon · +5h from home")
        self.assertEqual(result["source"], "lodging")
        self.assertIn("until", result)

    def test_hotel_in_the_home_city_is_not_away(self) -> None:
        hotel = all_day("Hotel Example", "2026-10-10", "2026-10-13",
                        location="9 Example St, Boston, MA 02110")
        self.assertEqual(self.infer([hotel])["status"], "home")

    def test_working_location_office_label(self) -> None:
        office = all_day("Office", "2026-10-11", "2026-10-12", event_type="workingLocation",
                         working_location={"type": "office", "label": "HQ"})
        result = self.infer([office])
        self.assertEqual(result["status"], "home")
        self.assertEqual(result["label"], "At the office · HQ")
        self.assertEqual(result["source"], "working_location")
        self.assertEqual(result["city"], "Boston")

    def test_flight_beats_working_location(self) -> None:
        office = all_day("Home", "2026-10-11", "2026-10-12", event_type="workingLocation",
                         working_location={"type": "home"})
        flight = row("TP 217 LIS→BOS", -13, -6)
        self.assertEqual(self.infer([office], [flight])["status"], "just_returned")
        self.assertEqual(self.infer([office])["label"], "Working from home")

    def test_timezone_of_events_needs_two_agreeing_events(self) -> None:
        one = [row("Coffee", 1, 2, event_tz="Europe/Lisbon")]
        self.assertEqual(self.infer(one)["status"], "home")
        two = one + [row("Walk", 3, 4, event_tz="Europe/Lisbon")]
        result = self.infer(two)
        self.assertEqual(result["status"], "away")
        self.assertEqual(result["timezone"], "Europe/Lisbon")
        self.assertEqual(result["city"], "")
        self.assertEqual(result["label"], "On Lisbon time · +5h from home")
        # Invitations carry the organizer's zone, not the operator's.
        invites = [row("Sync", 1, 2, event_tz="Europe/Lisbon", organizer="bob@example.com"),
                   row("Review", 3, 4, event_tz="Europe/Lisbon", organizer="bob@example.com")]
        self.assertEqual(self.infer(invites)["status"], "home")

    def test_never_claims_a_city_without_evidence(self) -> None:
        nameless = {"home_timezone": "America/New_York"}
        result = self.infer([], [], nameless)
        self.assertEqual((result["status"], result["city"], result["label"]), ("home", "", "Home"))
        stay = all_day("Hotel Example", "2026-10-10", "2026-10-13")
        result = self.infer([stay])
        self.assertEqual(result["status"], "away")
        self.assertEqual(result["city"], "")
        self.assertEqual(result["label"], "Away · Hotel Example")
        # A landed flight that names no destination proves nothing about place.
        result = self.infer([], [row("UA123", -8, -3)])
        self.assertEqual(result["status"], "home")
        self.assertEqual(result["city"], "Boston")
        for status_result in (self.infer([], [], nameless), self.infer([stay])):
            self.assertNotIn("Lisbon", status_result["label"])


# --- Meaningful events -------------------------------------------------------


class MeaningfulTest(unittest.TestCase):
    def setUp(self) -> None:
        self.where = where_module()

    def test_meaningful_rule(self) -> None:
        reminder = row("Vitamins", 1, 1.25)
        block = row("Deep work", 2, 2.5)
        call = row("Quick call", 3, 3.25, attendees=[SELF, BOB], attendee_count=2)
        self.assertFalse(self.where.is_meaningful(reminder))
        self.assertTrue(self.where.is_meaningful(block))
        self.assertTrue(self.where.is_meaningful(call))
        self.assertTrue(self.where.is_meaningful(row("Standup", 1, 1.25, conference="Example Meet")))
        self.assertTrue(self.where.is_meaningful(row("Tea", 1, 1.25, location="Example Cafe")))
        self.assertFalse(self.where.is_meaningful(row("Focus", 1, 3, event_type="focusTime")))
        self.assertFalse(self.where.is_meaningful(all_day("Offsite", "2026-10-11", "2026-10-12")))
        declined = row("Big sync", 1, 2, attendees=[dict(SELF, response="declined"), BOB])
        self.assertFalse(self.where.is_meaningful(declined))
        self.assertTrue(self.where.is_meaningful(row("Flight", 4, 9, event_type="fromGmail")))

    def test_meaningful_counts_today_and_tomorrow(self) -> None:
        events = [
            row("Vitamins", 1, 1.25),
            row("Deep work", 2, 2.5),
            row("Deep work", 2, 2.5),  # duplicate copy
            row("Quick call", 3, 3.25, attendees=[SELF, BOB]),
            row("Ended", -2, -1, attendees=[SELF, BOB]),
        ] + [row(f"Meeting {index}", 24 + index, 24.5 + index, attendees=[SELF, BOB])
             for index in range(5)]
        counts = self.where.meaningful_counts(events, NOW, EASTERN)
        self.assertEqual(counts, {"today": 2, "tomorrow": 5})

    def test_next_meaningful_event_skips_reminders(self) -> None:
        events = [row("Vitamins", 0.5, 0.75), row("Design review", 2, 3, attendees=[SELF, BOB])]
        self.assertEqual(self.where.next_meaningful_event(events, NOW)["summary"], "Design review")


# --- Operator profile extension ---------------------------------------------


class OperatorProfileTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.module = profile_module()

    def write(self, data: dict[str, Any]) -> None:
        path = self.home / "town-feed" / "operator.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_home_city_and_work_domains(self) -> None:
        self.write({
            **PROFILE,
            "work_domains": {"Example-Capital.com": "Example Capital", "bad domain": "x"},
        })
        profile = self.module.load_operator_profile(self.home)
        self.assertEqual(profile["home_city"], "Boston")
        self.assertEqual(profile["work_domains"], {"example-capital.com": "Example Capital"})
        self.write({**PROFILE, "work_domains": ["example-capital.com"]})
        profile = self.module.load_operator_profile(self.home)
        self.assertEqual(profile["work_domains"], {"example-capital.com": "Example Capital"})

    def test_invalid_home_timezone_falls_back(self) -> None:
        self.write({**PROFILE, "home_timezone": "Mars/Olympus"})
        (self.home / "config.yaml").write_text("timezone: Europe/Lisbon\n", encoding="utf-8")
        self.assertEqual(self.module.load_operator_profile(self.home)["home_timezone"], "Europe/Lisbon")

    def test_user_md_home_city(self) -> None:
        memory = self.home / "memories" / "USER.md"
        memory.parent.mkdir(parents=True)
        memory.write_text("**Name:** Alice Example\n**Home city:** Boston\n", encoding="utf-8")
        self.assertEqual(self.module.load_operator_profile(self.home)["home_city"], "Boston")

    def test_operator_context_block(self) -> None:
        context = self.module.operator_context(PROFILE, NOW, None)
        self.assertEqual(context["name"], "Alice Example")
        self.assertEqual(context["preferred_name"], "Alice")
        self.assertEqual(context["home_city"], "Boston")
        self.assertEqual(context["home_timezone"], "America/New_York")
        self.assertEqual(context["local_now"], "2026-10-11T10:42:00-04:00")
        self.assertEqual(context["local_now_label"], "Sun Oct 11, 10:42 AM ET")
        self.assertEqual(context["where"]["status"], "home")
        self.assertNotIn("home_now_label", context)
        away = {"status": "away", "city": "Singapore", "timezone": "Asia/Singapore",
                "label": "In Singapore · +12h from home", "evidence": []}
        context = self.module.operator_context(PROFILE, NOW, away)
        self.assertEqual(context["local_now_label"], "Sun Oct 11, 10:42 PM SGT")
        self.assertEqual(context["home_now_label"], "Sun Oct 11, 10:42 AM ET")
        self.assertEqual(context["where"], away)


# --- Plugin: LLM inputs, day brief, /feed ------------------------------------


class FakeLLM:
    """Captures every structured call; ranks every key and briefs the day."""

    def __init__(self, opener: str = "Back in Boston after Lisbon — two meetings left today.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.opener = opener

    def complete_structured(self, **kwargs: Any) -> Any:
        data = json.loads(kwargs["input"][0]["text"])
        self.calls.append({"schema": kwargs["schema_name"], "input": data,
                           "instructions": kwargs["instructions"], "json_schema": kwargs["json_schema"]})
        if kwargs["schema_name"] == "town_day_brief":
            return SimpleNamespace(parsed={
                "opener": self.opener,
                "day_summary": "Light day — 2 meetings; tomorrow has 5.",
                "callouts": [],
            })
        rows = data["ranking_candidates"] if isinstance(data, dict) else data
        return SimpleNamespace(parsed={
            "items": [{"key": entry["key"], "headline": entry["headline"],
                       "summary": entry["summary"], "score": 70} for entry in rows],
            "suggestions": [],
        })

    def call(self, schema: str) -> dict[str, Any]:
        return next(call for call in self.calls if call["schema"] == schema)


class PluginCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.state_dir = self.tmp / "state"
        self.state_dir.mkdir()
        self.api = support.load_plugin_api(self.state_dir)
        self.home = self.state_dir / "hermes-home"
        (self.home / "town-feed").mkdir(parents=True)
        (self.home / "town-feed" / "operator.json").write_text(json.dumps(PROFILE), encoding="utf-8")
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

    def calendar_doc(self, payload: dict[str, Any], minutes_ago: float = 5) -> None:
        timestamp = NOW - minutes_ago * 60
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

    def write_state(self, state: dict[str, Any]) -> None:
        (self.state_dir / "feed-state.json").write_text(json.dumps(state), encoding="utf-8")

    def schedule(self) -> list[dict[str, Any]]:
        return [
            row("Vitamins", 0.5, 0.75),
            row("Design review", 2, 3, attendees=[SELF, BOB], attendee_count=2,
                location="Room 4"),
            row("Deep work", 4, 5),
        ] + [row(f"Meeting {index}", 24 + index, 24.5 + index, attendees=[SELF, BOB])
             for index in range(5)]

    def scan_payload(self, recent: Optional[list[dict[str, Any]]] = None) -> dict[str, Any]:
        events = self.schedule()
        candidate = {
            "key": "cal-review",
            "content_hash": "c" * 64,
            "source": f"calendar:{ACCOUNT}:review",
            "headline": "Design review — in 2h",
            "summary": "Room 4 · 2 attendees",
            "created_at": events[1]["start_iso"],
        }
        payload = {
            "candidates": [candidate],
            "next_event": {"summary": "Vitamins", "start_iso": events[0]["start_iso"],
                           "account": ACCOUNT},
            "events": events,
        }
        if recent is not None:
            payload["recent_travel"] = recent
        return payload


class RankingInputTest(PluginCase):
    def enriched_call(self, operator: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        events = self.api._normalize_schedule_events(self.schedule())
        candidate = {
            "key": "cal-review",
            "content_hash": "c" * 64,
            "source": f"calendar:{ACCOUNT}:review",
            "headline": "Design review — in 2h",
            "summary": "Room 4 · 2 attendees",
            "created_at_ts": NOW + 2 * HOUR,
            "score": 60,
            "town_label": None,
        }
        llm = FakeLLM()
        self.api._llm_rank(
            [candidate], llm, schedule_events=events, now=NOW,
            has_enriched_contract=True, operator=operator,
        )
        return llm.call("town_feed_ranking")

    def test_ranking_input_carries_operator_and_local_times(self) -> None:
        call = self.enriched_call()
        data = call["input"]
        self.assertEqual(data["operator"]["local_now_label"], "Sun Oct 11, 10:42 AM ET")
        self.assertEqual(data["operator"]["home_city"], "Boston")
        self.assertEqual(data["operator"]["where"]["status"], "home")
        ranking_row = data["ranking_candidates"][0]
        self.assertEqual(ranking_row["created_local"], "Sun Oct 11, 12:42 PM ET")
        event = next(signal for signal in data["suggestion_signals"]["events_next_48h"]
                     if signal["summary"] == "Design review")
        self.assertEqual(event["start_local"], "Sun Oct 11, 12:42 PM ET")
        self.assertNotIn("start_home", event)
        self.assertIn("never describe home_city as a destination", call["instructions"])
        self.assertIn("use those for every time you write", call["instructions"])

    def test_away_operator_gets_home_time_beside_local(self) -> None:
        where = {"status": "away", "city": "Singapore", "timezone": "Asia/Singapore",
                 "label": "In Singapore · +12h from home", "evidence": []}
        operator = profile_module().operator_context(PROFILE, NOW, where)
        data = self.enriched_call(operator)["input"]
        ranking_row = data["ranking_candidates"][0]
        self.assertEqual(ranking_row["created_local"], "Mon Oct 12, 12:42 AM SGT")
        self.assertEqual(ranking_row["created_home"], "Sun Oct 11, 12:42 PM ET")

    def test_legacy_contract_keeps_a_list_and_moves_operator_to_instructions(self) -> None:
        candidate = {"key": "k1", "content_hash": "d" * 64, "source": "session:s1",
                     "headline": "Deploy finished", "summary": "All green.",
                     "created_at_ts": NOW - HOUR, "score": 50}
        llm = FakeLLM()
        self.api._llm_rank([candidate], llm, now=NOW)
        call = llm.call("town_feed_ranking")
        self.assertIsInstance(call["input"], list)
        self.assertEqual(call["input"][0]["created_local"], "Sun Oct 11, 9:42 AM ET")
        self.assertIn('"local_now_label": "Sun Oct 11, 10:42 AM ET"', call["instructions"])
        self.assertIn("never describe home_city as a destination", call["instructions"])


class CuratorDayBriefTest(PluginCase):
    def test_curator_passes_where_and_meaningful_counts_and_persists_opener(self) -> None:
        flight = row("TP 217 LIS→BOS", -13, -6)
        self.calendar_doc(self.scan_payload(recent=[flight]))
        llm = FakeLLM(opener="x" * 200)
        self.api.run_curator(llm=llm)

        # No labelled mail here, so ranking uses the legacy list contract and
        # the operator block rides in the instructions.
        ranking = llm.call("town_feed_ranking")
        self.assertIsInstance(ranking["input"], list)
        operator, _end = json.JSONDecoder().raw_decode(
            ranking["instructions"].split("Operator (JSON): ", 1)[1]
        )
        self.assertEqual(operator["where"]["status"], "just_returned")
        self.assertEqual(operator["where"]["label"], "Back in Boston after Lisbon")

        brief_call = llm.call("town_day_brief")
        brief_input = brief_call["input"]
        self.assertEqual(brief_input["meaningful_today"], 2)
        self.assertEqual(brief_input["meaningful_tomorrow"], 5)
        self.assertEqual(brief_input["operator"]["where"]["status"], "just_returned")
        # The landing instant reaches the model as local time, never bare UTC.
        self.assertEqual(brief_input["operator"]["where"]["since_local"], "Sun Oct 11, 4:42 AM ET")
        self.assertEqual(operator["where"]["since_local"], "Sun Oct 11, 4:42 AM ET")
        self.assertEqual(brief_input["next_event"]["summary"], "Design review")
        minor = [event for event in brief_input["events"] if event.get("minor")]
        self.assertEqual([event["summary"] for event in minor], ["Vitamins"])
        self.assertIn("opener", brief_call["json_schema"]["properties"])
        self.assertIn("day_summary counts ONLY meaningful events", brief_call["instructions"])

        state = self.state()
        self.assertLessEqual(len(state["day_brief"]["opener"]), 140)
        self.assertTrue(state["day_brief"]["opener"].startswith("xxx"))
        self.assertEqual(state["schedule"]["recent_travel"][0]["summary"], "TP 217 LIS→BOS")

        feed = self.api.get_feed()
        self.assertEqual(feed["day_brief"]["opener"], state["day_brief"]["opener"])
        self.assertEqual(feed["where"]["status"], "just_returned")

    def test_old_brief_without_opener_loads(self) -> None:
        old = {"day_summary": "A light Sunday.", "callouts": [], "trip": None,
               "generated_at": iso(NOW - 60), "local_date": "2026-10-11", "input_hash": "abc"}
        normalized = self.api._normalize_day_brief(old)
        self.assertEqual(normalized["day_summary"], "A light Sunday.")
        self.assertNotIn("opener", normalized)
        self.write_state({"version": 6, "items": [], "day_brief": old})
        feed = self.api.get_feed()
        self.assertEqual(feed["day_brief"]["day_summary"], "A light Sunday.")
        self.assertNotIn("opener", feed["day_brief"])

    def test_hash_changes_when_where_status_changes(self) -> None:
        events = self.api._normalize_schedule_events(self.schedule())
        home = {"status": "home", "city": "Boston"}
        back = {"status": "just_returned", "city": "Boston"}
        first = self.api._day_brief_input_hash(events, None, where=home, local_date="2026-10-11")
        self.assertNotEqual(
            first, self.api._day_brief_input_hash(events, None, where=back, local_date="2026-10-11")
        )
        self.assertNotEqual(
            first, self.api._day_brief_input_hash(events, None, where=home, local_date="2026-10-12")
        )
        self.assertEqual(
            first, self.api._day_brief_input_hash(events, None, where=dict(home), local_date="2026-10-11")
        )

    def test_unchanged_inputs_do_not_regenerate_until_where_moves(self) -> None:
        self.calendar_doc(self.scan_payload(recent=[]))
        llm = FakeLLM()
        self.api.run_curator(llm=llm)
        self.api.run_curator(llm=llm)
        self.assertEqual(sum(call["schema"] == "town_day_brief" for call in llm.calls), 1)
        self.calendar_doc(self.scan_payload(recent=[row("TP 217 LIS→BOS", -13, -6)]), minutes_ago=1)
        self.api.run_curator(llm=llm)
        self.assertEqual(sum(call["schema"] == "town_day_brief" for call in llm.calls), 2)


class FeedTest(PluginCase):
    def test_feed_includes_schedule_context_operator_and_where(self) -> None:
        self.calendar_doc(self.scan_payload(recent=[]))
        self.api.run_curator(llm=None)
        feed = self.api.get_feed()
        for key in ("items", "curated_at", "suggestions", "suggestions_generated_at",
                    "next_event", "schedule", "day_brief"):
            self.assertIn(key, feed)
        self.assertEqual(feed["operator"], {"name": "Alice Example", "preferred_name": "Alice",
                                            "home_city": "Boston",
                                            "home_timezone": "America/New_York"})
        self.assertEqual(feed["where"]["status"], "home")
        self.assertEqual(feed["where"]["label"], "Home in Boston")
        context = feed["schedule_context"]
        self.assertEqual(context["local_tz"], "America/New_York")
        self.assertEqual(context["date"], "2026-10-11")
        self.assertIn("conflicts", context)
        self.assertEqual(feed["next_event"]["summary"], "Vitamins")  # scanner contract kept

    def test_old_v6_state_loads_and_feed_stays_compatible(self) -> None:
        events = self.schedule()
        self.write_state({
            "version": 6,
            "items": [],
            "decisions": {},
            "source_state": {"memory_hashes": {}, "cron_consumed": {CALENDAR_JOB: 123}},
            "schedule": {"events": events, "generated_at": iso(NOW - 600),
                         "context": {"date": "2026-10-11"}},
            "day_brief": None,
            "next_event": None,
        })
        with self.api._process_lock():
            state = self.api._read_state_unlocked()
        self.assertEqual(state["schedule"]["recent_travel"], [])
        self.assertEqual(state["source_state"]["cron_consumed"], {CALENDAR_JOB: 123})
        feed = self.api.get_feed()
        self.assertEqual(len(feed["schedule"]), len(events))
        self.assertIsNone(feed["day_brief"])
        self.assertEqual(feed["where"]["status"], "home")
        self.assertIsNotNone(feed["schedule_context"])

    def test_stale_schedule_serves_no_context(self) -> None:
        self.write_state({"version": 6, "items": [],
                          "schedule": {"events": self.schedule(),
                                       "generated_at": iso(NOW - 4 * HOUR)}})
        feed = self.api.get_feed()
        self.assertEqual(feed["schedule"], [])
        self.assertIsNone(feed["schedule_context"])


class LocalTimezoneTest(PluginCase):
    def test_local_timezone_is_the_operator_home_zone(self) -> None:
        zone = self.api._local_timezone()
        self.assertEqual(getattr(zone, "key", None), "America/New_York")
        # DST-aware: the same zone object labels both sides of the change.
        summer = datetime(2026, 7, 1, 12, tzinfo=timezone.utc).astimezone(zone)
        winter = datetime(2026, 12, 1, 12, tzinfo=timezone.utc).astimezone(zone)
        self.assertNotEqual(summer.utcoffset(), winter.utcoffset())


if __name__ == "__main__":
    unittest.main()
