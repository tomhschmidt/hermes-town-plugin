"""R3 — calendar: local time, cross-calendar dedupe, event types (GROUNDING pack).

Every event here is synthetic. No test reads live ``~/.hermes`` state or calls
Google: the scanner loads with ``HERMES_HOME`` in a temp dir, ``now`` is
injected, and ``_user_timezone`` is patched unless it is the subject.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock
from zoneinfo import ZoneInfo

import support

NEW_YORK = ZoneInfo("America/New_York")
LONDON = ZoneInfo("Europe/London")
WORK = "alice@example.com"
PERSONAL = "alice.home@example.com"
# Sun 2026-10-11 10:00 ET.
NOW = datetime(2026, 10, 11, 10, 0, tzinfo=NEW_YORK)


def _load_scanner(home: Path) -> ModuleType:
    with mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}):
        return support.load_script("town-calendar-scan.py")


def _event(
    event_id: str,
    summary: str,
    start: str,
    end: str,
    *,
    all_day: bool = False,
    tz: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    field = "date" if all_day else "dateTime"
    start_info: dict[str, Any] = {field: start}
    end_info: dict[str, Any] = {field: end}
    if tz:
        start_info["timeZone"] = tz
        end_info["timeZone"] = tz
    event = {
        "id": event_id,
        "iCalUID": f"{event_id}@example.com",
        "status": "confirmed",
        "summary": summary,
        "start": start_info,
        "end": end_info,
    }
    event.update(extra)
    return event


def _at(hours: float, base: datetime = NOW) -> str:
    return (base + timedelta(hours=hours)).isoformat()


def _guest(index: int) -> dict[str, Any]:
    return {
        "email": f"guest{index:02d}@example.com",
        "displayName": f"Guest {index:02d}",
        "responseStatus": "accepted",
    }


SELF = {"email": WORK, "displayName": "Alice", "self": True, "responseStatus": "accepted"}
BOB = {"email": "bob@example.com", "displayName": "Bob", "responseStatus": "accepted"}


class ScannerTestCase(unittest.TestCase):
    user_tz: Any = NEW_YORK

    @classmethod
    def setUpClass(cls) -> None:
        cls._home = tempfile.TemporaryDirectory()
        cls.scanner = _load_scanner(Path(cls._home.name))

    @classmethod
    def tearDownClass(cls) -> None:
        cls._home.cleanup()

    def setUp(self) -> None:
        patcher = mock.patch.object(self.scanner, "_user_timezone", return_value=self.user_tz)
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(
        self, *events: Any, now: datetime = NOW, accounts: tuple[str, ...] = (WORK,)
    ) -> dict[str, Any]:
        entries = [
            event if isinstance(event, tuple) else (WORK, "Work", event) for event in events
        ]
        payload = self.scanner._build_payload(entries, now, accounts)
        self.assertIsNotNone(payload)
        return payload

    def label(self, start_iso: str, now: datetime = NOW, all_day: bool = False) -> str:
        start = datetime.fromisoformat(start_iso)
        return self.scanner._relative_start(start, now, all_day, self.user_tz)

    @staticmethod
    def summaries(rows: list[dict[str, Any]]) -> list[str]:
        return [row["summary"] for row in rows]


class UserTimezoneTest(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.home = Path(self._home.name)
        self.scanner = _load_scanner(self.home)
        env = mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("TOWN_TIMEZONE", None)

    def write_profile(self, body: str) -> None:
        path = self.home / "plugins" / "town-feed" / "dashboard" / "operator_profile.py"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def write_config(self, body: str) -> None:
        (self.home / "config.yaml").write_text(body, encoding="utf-8")

    def resolve(self) -> Any:
        with mock.patch.object(self.scanner, "_system_timezone", return_value=ZoneInfo("Asia/Tokyo")):
            return self.scanner._user_timezone()

    def test_town_timezone_env_wins_over_every_other_source(self) -> None:
        self.write_profile(
            "def load_operator_profile(hermes_home=None):\n"
            "    return {'home_timezone': 'Europe/London'}\n"
        )
        self.write_config("timezone: Asia/Singapore\n")
        with mock.patch.dict(os.environ, {"TOWN_TIMEZONE": "America/Chicago"}):
            self.assertEqual(self.resolve(), ZoneInfo("America/Chicago"))

    def test_invalid_env_falls_through_to_operator_profile(self) -> None:
        self.write_profile(
            "def load_operator_profile(hermes_home=None):\n"
            "    return {'home_timezone': 'Europe/London'}\n"
        )
        self.write_config("timezone: Asia/Singapore\n")
        with mock.patch.dict(os.environ, {"TOWN_TIMEZONE": "Mars/Olympus_Mons"}):
            self.assertEqual(self.resolve(), LONDON)

    def test_missing_or_broken_profile_falls_back_to_config_yaml(self) -> None:
        self.write_config(
            "model: example\nagent:\n  timezone: Europe/Paris\n"
            'timezone: "Asia/Singapore"  # left over from a trip\n'
        )
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Singapore"))
        self.write_profile("raise RuntimeError('half-installed')\n")
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Singapore"))
        self.write_profile(
            "def load_operator_profile(hermes_home=None):\n"
            "    return {'home_timezone': 'Not/AZone'}\n"
        )
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Singapore"))

    def test_profile_receives_hermes_home(self) -> None:
        self.write_profile(
            "def load_operator_profile(hermes_home=None):\n"
            "    return {'home_timezone': 'Europe/London' if hermes_home else ''}\n"
        )
        self.assertEqual(self.resolve(), LONDON)

    def test_empty_config_falls_back_to_system_timezone(self) -> None:
        self.write_config("timezone: ''\n")
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Tokyo"))
        self.write_config("timezone: Nowhere/Special\n")
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Tokyo"))
        (self.home / "config.yaml").unlink()
        self.assertEqual(self.resolve(), ZoneInfo("Asia/Tokyo"))

    def test_system_timezone_is_a_named_dst_aware_zone(self) -> None:
        with mock.patch.dict(os.environ, {"TZ": "Europe/London"}):
            self.assertEqual(self.scanner._system_timezone(), LONDON)
        with mock.patch.dict(os.environ, {"TZ": ""}):
            zone = self.scanner._system_timezone()
        self.assertIsNotNone(datetime(2026, 1, 1, tzinfo=zone).utcoffset())


class LocalTimeTest(ScannerTestCase):
    def test_singapore_zoned_event_labels_in_user_local_time(self) -> None:
        start = "2026-10-12T23:00:00+08:00"
        self.assertEqual(self.label(start), "Mon at 11:00 AM")
        payload = self.build(
            _event("weekly", "Weekly Call", start, "2026-10-13T00:00:00+08:00",
                   tz="Asia/Singapore", attendees=[SELF, BOB]),
            _event("lunch", "Lunch", "2026-10-12T02:00:00+08:00", "2026-10-12T03:00:00+08:00",
                   tz="Asia/Singapore", attendees=[SELF, BOB]),
        )
        rows = {row["summary"]: row for row in payload["events"]}
        # start_iso keeps the exact instant; consumers convert.
        self.assertEqual(rows["Weekly Call"]["start_iso"], start)
        self.assertEqual(
            datetime.fromisoformat(rows["Weekly Call"]["start_iso"]),
            datetime(2026, 10, 12, 11, 0, tzinfo=NEW_YORK),
        )
        # Lunch is Sun 2 PM ET, 4 h out: the in-window candidate wording.
        self.assertEqual([c["headline"] for c in payload["candidates"]], ["Lunch — in 4h"])

    def test_all_day_date_anchors_to_local_midnight(self) -> None:
        payload = self.build(
            _event("trip", "Conference", "2026-10-12", "2026-10-13", all_day=True),
        )
        row = payload["events"][0]
        self.assertTrue(row["all_day"])
        self.assertEqual(datetime.fromisoformat(row["start_iso"]),
                         datetime(2026, 10, 12, 0, 0, tzinfo=NEW_YORK))
        self.assertEqual(row["start_iso"], "2026-10-12T00:00:00-04:00")
        self.assertEqual(row["end_iso"], "2026-10-13T00:00:00-04:00")
        self.assertEqual(self.label(row["start_iso"], all_day=True), "tomorrow")

    def test_dst_fall_back_day_labels_correct_local_hour(self) -> None:
        now = datetime(2026, 10, 30, 12, 0, tzinfo=NEW_YORK)  # EDT, UTC-4
        # 14:00Z on Nov 1 is 9 AM EST; a fixed EDT offset would say 10 AM.
        self.assertEqual(self.label("2026-11-01T14:00:00+00:00", now), "Sun at 9:00 AM")
        self.assertEqual(self.label("2026-11-01T05:30:00+00:00", now), "Sun at 1:30 AM")
        self.assertEqual(self.label("2026-11-01T01:30:00-05:00", now), "Sun at 1:30 AM")
        payload = self.build(
            _event("fallback", "Fall back", "2026-11-01", "2026-11-02", all_day=True), now=now
        )
        row = payload["events"][0]
        self.assertEqual(row["start_iso"], "2026-11-01T00:00:00-04:00")
        self.assertEqual(row["end_iso"], "2026-11-02T00:00:00-05:00")


class LondonTimeTest(ScannerTestCase):
    user_tz = LONDON

    def test_london_operator_sees_london_time_and_midnight(self) -> None:
        self.assertEqual(self.label("2026-10-12T23:00:00+08:00"), "Mon at 4:00 PM")
        payload = self.build(_event("day", "Offsite", "2026-10-12", "2026-10-13", all_day=True))
        self.assertEqual(payload["events"][0]["start_iso"], "2026-10-12T00:00:00+01:00")


class DedupeTest(ScannerTestCase):
    def test_same_ical_uid_on_two_calendars_is_one_row_with_true_attendee_count(self) -> None:
        guests = [_guest(index) for index in range(1, 41)]
        work_copy = _event("standup-w", "All Hands", _at(3), _at(4),
                           attendees=[SELF] + guests[:39])
        home_copy = _event("standup-h", "All Hands", _at(3), _at(4),
                           attendees=[dict(SELF, self=False)] + guests
                           + [{"email": PERSONAL, "self": True, "responseStatus": "accepted"}])
        work_copy["iCalUID"] = home_copy["iCalUID"] = "allhands-1@example.com"
        home_copy["summary"] = "All Hands (shared)"  # merged by iCalUID, not by title
        payload = self.build((WORK, "Work", work_copy), (PERSONAL, "Family", home_copy),
                             accounts=(WORK, PERSONAL))
        self.assertEqual(len(payload["events"]), 1)
        row = payload["events"][0]
        self.assertEqual(row["calendars"], ["Work", "Family"])
        self.assertEqual(row["calendar"], "Work")
        self.assertEqual(len(row["attendees"]), self.scanner.MAX_ATTENDEE_ENTRIES)
        # alice + 40 guests (guest40 only on the Family copy) + alice.home.
        self.assertEqual(row["attendee_count"], 42)
        self.assertTrue(row["attendees"][0]["self"])
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertIn("42 attendees", payload["candidates"][0]["summary"])

    def test_forty_one_attendee_event_capped_at_eight_reports_true_total(self) -> None:
        guests = [_guest(index) for index in range(1, 41)]
        first = _event("big-a", "Town Hall", _at(5), _at(6), attendees=[SELF] + guests[:20])
        second = _event("big-b", "Town Hall", _at(5), _at(6), attendees=guests)
        first["iCalUID"] = second["iCalUID"] = "townhall@example.com"
        payload = self.build((WORK, "Work", first), (WORK, "Team", second))
        row = payload["events"][0]
        self.assertEqual(len(payload["events"]), 1)
        self.assertEqual(len(row["attendees"]), 8)
        self.assertEqual(row["attendee_count"], 41)

    def test_attendee_union_merges_responses_and_self(self) -> None:
        first = _event("sync-a", "Sync", _at(2), _at(3), attendees=[SELF, BOB])
        second = _event("sync-b", "Sync", _at(2), _at(3), attendees=[
            dict(SELF, self=False),
            {"email": "carol@example.com", "displayName": "Carol"},
            {"resource": True, "email": "room@example.com"},
        ])
        first["iCalUID"] = second["iCalUID"] = "sync@example.com"
        row = self.build((WORK, "Work", first), (WORK, "Team", second))["events"][0]
        self.assertEqual(
            row["attendees"],
            [
                {"email": WORK, "name": "Alice", "response": "accepted", "self": True},
                {"email": "bob@example.com", "name": "Bob", "response": "accepted"},
                {"email": "carol@example.com", "name": "Carol"},
            ],
        )
        self.assertEqual(row["attendee_count"], 3)

    def test_summary_start_end_fallback_collapses_distinct_uids(self) -> None:
        first = _event("hvac-1", "HVAC visit", _at(5), _at(7))
        second = _event("hvac-2", "  hvac   VISIT ", _at(5), _at(7), location="12 Example St")
        payload = self.build((WORK, "Home", first), (PERSONAL, "Family", second),
                             accounts=(WORK, PERSONAL))
        self.assertEqual(len(payload["events"]), 1)
        row = payload["events"][0]
        self.assertEqual(row["calendars"], ["Home", "Family"])
        self.assertEqual(row["location"], "12 Example St")
        self.assertEqual(len(payload["candidates"]), 1)

    def test_same_calendar_name_copies_do_not_emit_calendars(self) -> None:
        event = _event("shared", "Review", _at(2), _at(3), attendees=[SELF, BOB])
        payload = self.build((WORK, "Work", event), (WORK, "Work", dict(event)))
        self.assertEqual(len(payload["events"]), 1)
        self.assertNotIn("calendars", payload["events"][0])

    def test_recurring_instances_sharing_ical_uid_stay_distinct(self) -> None:
        today = _event("daily_1", "Daily", _at(2), _at(2.5), attendees=[SELF, BOB])
        tomorrow = _event("daily_2", "Daily", _at(26), _at(26.5), attendees=[SELF, BOB])
        today["iCalUID"] = tomorrow["iCalUID"] = "daily@example.com"
        payload = self.build(today, tomorrow)
        self.assertEqual(len(payload["events"]), 2)


class EventTypeTest(ScannerTestCase):
    def test_context_event_types_are_never_candidates_or_next_event(self) -> None:
        home = _event("wl-home", "Home", "2026-10-11", "2026-10-12", all_day=True,
                      eventType="workingLocation",
                      workingLocationProperties={"type": "homeOffice", "homeOffice": {}})
        office = _event("wl-office", "Office", _at(0.5), _at(8), eventType="workingLocation",
                        workingLocationProperties={"type": "officeLocation",
                                                   "officeLocation": {"label": "HQ Floor 3"}})
        birthday = _event("bday", "Bob's birthday", "2026-10-11", "2026-10-12", all_day=True,
                          eventType="birthday")
        focus = _event("focus", "Focus Time", _at(1), _at(3), eventType="focusTime",
                       location="Desk", attendees=[SELF, BOB])
        ooo = _event("ooo", "Out of office", _at(1.5), _at(4), eventType="outOfOffice",
                     attendees=[SELF, BOB])
        standup = _event("standup", "Standup", _at(5), _at(5.5), attendees=[SELF, BOB])
        payload = self.build(home, office, birthday, focus, ooo, standup)

        self.assertEqual(self.summaries(payload["events"]),
                         ["Home", "Bob's birthday", "Office", "Focus Time", "Out of office",
                          "Standup"])
        self.assertEqual([c["headline"] for c in payload["candidates"]], ["Standup — in 5h"])
        self.assertEqual(payload["next_event"]["summary"], "Standup")
        rows = {row["summary"]: row for row in payload["events"]}
        self.assertEqual(rows["Home"]["event_type"], "workingLocation")
        self.assertEqual(rows["Home"]["working_location"], {"type": "home"})
        self.assertEqual(rows["Office"]["working_location"],
                         {"type": "office", "label": "HQ Floor 3"})
        self.assertEqual(rows["Bob's birthday"]["event_type"], "birthday")
        self.assertEqual(rows["Focus Time"]["event_type"], "focusTime")
        self.assertEqual(rows["Out of office"]["event_type"], "outOfOffice")
        self.assertEqual(rows["Standup"]["event_type"], "default")
        self.assertNotIn("working_location", rows["Focus Time"])

    def test_from_gmail_reservation_counts_as_a_commitment(self) -> None:
        flight = _event("flight", "Flight to Lisbon", _at(6), _at(13), eventType="fromGmail",
                        location="Example Airport")
        payload = self.build(flight)
        self.assertEqual(payload["next_event"]["summary"], "Flight to Lisbon")
        self.assertEqual(len(payload["candidates"]), 1)

    def test_self_declined_event_is_absent(self) -> None:
        declined = _event("declined", "Vendor pitch", _at(1), _at(2), attendees=[
            dict(SELF, responseStatus="declined"), BOB])
        bob_declined = _event("kept", "Planning", _at(3), _at(4), attendees=[
            SELF, dict(BOB, responseStatus="declined")])
        payload = self.build(declined, bob_declined)
        self.assertEqual(self.summaries(payload["events"]), ["Planning"])
        self.assertEqual(payload["next_event"]["summary"], "Planning")
        self.assertEqual(len(payload["candidates"]), 1)
        self.assertIsNone(self.scanner._build_payload([(WORK, "Work", declined)], NOW, (WORK,)))


class CandidatePolicyTest(ScannerTestCase):
    def test_solo_gym_is_schedule_only(self) -> None:
        payload = self.build(_event("gym", "Gym", _at(2), _at(3)))
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(self.summaries(payload["events"]), ["Gym"])
        self.assertEqual(payload["next_event"]["summary"], "Gym")

    def test_meeting_with_one_other_attendee_in_three_hours_is_a_candidate(self) -> None:
        payload = self.build(_event("1on1", "1:1 with Bob", _at(3), _at(3.5),
                                    attendees=[SELF, BOB]))
        self.assertEqual(len(payload["candidates"]), 1)
        candidate = payload["candidates"][0]
        self.assertEqual(candidate["headline"], "1:1 with Bob — in 3h")
        self.assertEqual(candidate["source"], f"calendar:{WORK}:1on1")

    def test_meeting_in_thirty_hours_is_not_a_candidate(self) -> None:
        payload = self.build(_event("later", "Board prep", _at(30), _at(31),
                                    attendees=[SELF, BOB]))
        self.assertEqual(payload["candidates"], [])
        self.assertEqual(self.summaries(payload["events"]), ["Board prep"])

    def test_conference_link_or_location_qualifies_a_solo_event(self) -> None:
        payload = self.build(
            _event("dentist", "Dentist", _at(4), _at(5), location="1 Example Ave"),
            _event("webinar", "Webinar", _at(6), _at(7), hangoutLink="https://meet.google.com/x"),
        )
        self.assertEqual([c["headline"] for c in payload["candidates"]],
                         ["Dentist — in 4h", "Webinar — in 6h"])

    def test_only_self_addresses_do_not_count_as_other_attendees(self) -> None:
        blocked = _event("block", "Personal block", _at(2), _at(3), attendees=[
            SELF, {"email": PERSONAL, "responseStatus": "accepted"}])
        payload = self.build(blocked, accounts=(WORK, PERSONAL))
        self.assertEqual(payload["candidates"], [])

    def test_all_day_and_ongoing_events_are_not_candidates(self) -> None:
        payload = self.build(
            _event("allday", "Team offsite", "2026-10-11", "2026-10-12", all_day=True,
                   attendees=[SELF, BOB], location="Example Park"),
            _event("ongoing", "Workshop", _at(-1), _at(1), attendees=[SELF, BOB]),
        )
        self.assertEqual(payload["candidates"], [])
        self.assertIsNone(payload["next_event"])
        self.assertEqual(len(payload["events"]), 2)


class ContractTest(ScannerTestCase):
    def test_output_stays_a_superset_of_the_previous_contract(self) -> None:
        meeting = _event(
            "review", "Design review", _at(2), _at(3), tz="America/New_York",
            attendees=[SELF, BOB], location="Room 4", description="Agenda\nline two",
            organizer={"email": "bob@example.com", "displayName": "Bob"},
            conferenceData={"conferenceSolution": {"name": "Zoom"}},
        )
        payload = self.build(meeting)
        self.assertEqual(set(payload), {"candidates", "next_event", "events"})
        candidate = payload["candidates"][0]
        self.assertEqual(
            set(candidate),
            {"key", "content_hash", "source", "headline", "summary", "created_at",
             "created_at_ts"},
        )
        self.assertEqual(candidate["summary"], "Room 4 · 2 attendees · Zoom · Agenda")
        self.assertEqual(set(payload["next_event"]), {"summary", "start_iso", "account",
                                                      "location"})
        row = payload["events"][0]
        for field in ("summary", "start_iso", "end_iso", "all_day", "account", "calendar",
                      "location", "attendees", "attendee_count", "description_excerpt",
                      "organizer", "conference", "event_tz", "event_type", "ical_uid"):
            self.assertIn(field, row)

        with tempfile.TemporaryDirectory() as tmp:
            api = support.load_plugin_api(Path(tmp))
            output = "# Cron Job: calendar\n---\n" + json.dumps(payload)
            self.assertEqual(len(api._town_scan_candidates(output)), 1)
            self.assertEqual(api._town_scan_next_event(output)["summary"], "Design review")
            normalized = api._town_scan_schedule_events(output)
        self.assertEqual(normalized[0]["event_type"], "default")
        self.assertEqual(normalized[0]["ical_uid"], "review@example.com")

    def test_quiet_scan_returns_none(self) -> None:
        self.assertIsNone(self.scanner._build_payload([], NOW, ()))
        cancelled = _event("gone", "Gone", _at(1), _at(2), status="cancelled")
        past = _event("past", "Past", _at(-3), _at(-2))
        self.assertIsNone(
            self.scanner._build_payload([(WORK, "Work", cancelled), (WORK, "Work", past)], NOW)
        )


class ScheduleContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.api = support.load_plugin_api(Path(self._tmp.name))
        patcher = mock.patch.object(self.api, "_local_timezone", return_value=NEW_YORK)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = NOW.timestamp()

    def row(self, summary: str, start_hours: float, end_hours: float, **extra: Any) -> dict[str, Any]:
        row = {
            "summary": summary,
            "start_iso": _at(start_hours),
            "end_iso": _at(end_hours),
            "all_day": False,
            "account": WORK,
        }
        row.update(extra)
        return row

    def context(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        normalized = self.api._normalize_schedule_events(rows)
        context = self.api._schedule_context(normalized, self.now)
        self.assertIsNotNone(context)
        return context

    def test_two_identical_rows_are_not_a_conflict(self) -> None:
        hvac = self.row("HVAC visit", 2, 4, location="12 Example St")
        context = self.context([hvac, dict(hvac)])
        self.assertEqual(context["conflicts"], [])
        self.assertEqual(context["start_clusters"], [])
        self.assertEqual(context["load"]["events_today"], 1)
        self.assertEqual(context["load"]["booked_minutes_today"], 120)

    def test_same_ical_uid_and_start_is_one_occurrence(self) -> None:
        context = self.context([
            self.row("Weekly Call", 2, 3, ical_uid="weekly@example.com"),
            self.row("Weekly call (Alice)", 2, 3.5, ical_uid="weekly@example.com"),
        ])
        self.assertEqual(context["conflicts"], [])

    def test_real_overlap_still_conflicts(self) -> None:
        context = self.context([self.row("Dentist", 2, 3), self.row("Call", 2.5, 3.5)])
        self.assertEqual(len(context["conflicts"]), 1)
        self.assertEqual(context["conflicts"][0]["overlap_minutes"], 30)

    def test_working_location_and_birthday_rows_are_ignored(self) -> None:
        context = self.context([
            self.row("Office", 0, 9, event_type="workingLocation",
                     working_location={"type": "office", "label": "HQ"}),
            self.row("Standup", 1, 1.5, event_type="default"),
            self.row("Lunch", 1.25, 2, event_type="default"),
            {
                "summary": "Home",
                "start_iso": "2026-10-11T00:00:00-04:00",
                "end_iso": "2026-10-14T00:00:00-04:00",
                "all_day": True,
                "account": WORK,
                "multi_day": True,
                "event_type": "workingLocation",
            },
            {
                "summary": "Bob's birthday",
                "start_iso": "2026-10-11T00:00:00-04:00",
                "end_iso": "2026-10-12T00:00:00-04:00",
                "all_day": True,
                "account": WORK,
                "event_type": "birthday",
            },
        ])
        self.assertEqual([(c["a"], c["b"]) for c in context["conflicts"]], [("Standup", "Lunch")])
        self.assertEqual(context["trips"], [])
        self.assertEqual(context["load"]["events_today"], 2)

    def test_normalization_keeps_r3_fields_and_old_rows_load(self) -> None:
        normalized = self.api._normalize_schedule_events([
            self.row("Office", 0, 9, event_type="workingLocation", ical_uid="wl@example.com",
                     working_location={"type": "office", "label": "HQ"}),
            self.row("Legacy", 1, 2),
            self.row("Odd", 3, 4, working_location={"type": "moon"}),
        ])
        self.assertEqual(normalized[0]["event_type"], "workingLocation")
        self.assertEqual(normalized[0]["working_location"], {"type": "office", "label": "HQ"})
        self.assertEqual(normalized[0]["ical_uid"], "wl@example.com")
        self.assertNotIn("event_type", normalized[1])
        self.assertNotIn("working_location", normalized[2])


if __name__ == "__main__":
    unittest.main()
