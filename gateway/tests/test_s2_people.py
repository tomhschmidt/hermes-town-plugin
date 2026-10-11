"""S2 — who people are: people directory + meeting prep (GROUNDING pack).

Every person, address, and message here is synthetic (Alice/Bob/Carol,
example.com, "Example Capital"). No test reads live ``~/.hermes`` state,
calls Google, or reaches an LLM: People and Gmail clients are in-memory fakes
that count their calls, the LLM is a stub that records its inputs, and every
cache lives in a temporary HERMES_HOME.
"""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import stat
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
import test_r2_runtime as r2
import test_r4_curation as r4

people = support.load_module(support.DASHBOARD / "people.py", "town_test_s2_people")

DAY = 24 * 60 * 60
NOW = r4.NOW
ALICE = "alice@example-capital.com"  # the operator's work mailbox (custom domain)
ALICE_HOME = "alice.example@gmail.com"  # the operator's personal mailbox
ASSISTANT = "assistant@example.net"
PROFILE = {
    "self_addresses": [ALICE, ALICE_HOME],
    "assistant_senders": [ASSISTANT],
    "work_domains": {"example-fund.com": "Example Fund"},
}
CONTACTS_SCOPE = "https://www.googleapis.com/auth/contacts.readonly"


class _Call:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        return copy.deepcopy(self._result)


class _Failing:
    def __init__(self, exc: BaseException) -> None:
        self._exc = exc

    def execute(self) -> Any:
        raise self._exc


class FakePeople:
    """``people().connections().list(...)`` over fixed pages, counting calls."""

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self.pages = pages
        self.calls: list[dict[str, Any]] = []

    def people(self) -> "FakePeople":
        return self

    def connections(self) -> "FakePeople":
        return self

    def list(self, **request: Any) -> _Call:
        self.calls.append(dict(request))
        index = int(request.get("pageToken") or 0)
        page = dict(self.pages[index])
        if index + 1 < len(self.pages):
            page["nextPageToken"] = str(index + 1)
        return _Call(page)


def sent_message(message_id: str, sent_ts: float, to: str, cc: str = "") -> dict[str, Any]:
    headers = [{"name": "To", "value": to}]
    if cc:
        headers.append({"name": "Cc", "value": cc})
    headers.append({"name": "Date", "value": "Sat, 10 Oct 2026 09:00:00 -0400"})
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(int(sent_ts * 1000)),
        "payload": {"headers": headers},
    }


class FakeSentGmail:
    """The Gmail slice the sent-history refresh uses, counting calls."""

    def __init__(self, messages: list[dict[str, Any]], *, failing_ids: tuple[str, ...] = ()) -> None:
        self.store = {message["id"]: message for message in messages}
        self.failing_ids = set(failing_ids)
        self.list_calls: list[dict[str, Any]] = []
        self.get_calls: list[dict[str, Any]] = []

    def users(self) -> "FakeSentGmail":
        return self

    def messages(self) -> "FakeSentGmail":
        return self

    def list(self, **request: Any) -> _Call:
        self.list_calls.append(dict(request))
        ids = [{"id": message_id} for message_id in self.store]
        return _Call({"messages": ids[: request.get("maxResults", 100)]})

    def get(self, **request: Any) -> Any:
        self.get_calls.append(dict(request))
        if request["id"] in self.failing_ids:
            return _Failing(RuntimeError("HttpError 500 for message"))
        return _Call(self.store[request["id"]])

    @property
    def calls(self) -> int:
        return len(self.list_calls) + len(self.get_calls)


def connection(
    name: Optional[str], emails: list[str], org: Optional[str] = None, title: Optional[str] = None
) -> dict[str, Any]:
    person: dict[str, Any] = {"resourceName": f"people/c{abs(hash(name)) % 10_000}", "etag": "x"}
    if name:
        person["names"] = [{"displayName": name, "metadata": {"primary": True}}]
    if emails:
        person["emailAddresses"] = [{"value": value} for value in emails]
    if org or title:
        person["organizations"] = [{"name": org, "title": title, "metadata": {"primary": True}}]
    return person


def observed(name: Optional[str], last_ts: float, count: int = 1) -> dict[str, Any]:
    return {"name": name, "last_ts": last_ts, "count": count}


# --- Directory precedence ------------------------------------------------------


class DirectoryTest(unittest.TestCase):
    def build(self, **sources: Any) -> dict[str, Any]:
        return people.build_directory(
            sources.get("contacts", []),
            sources.get("sent", {}),
            sources.get("inbound", {}),
            sources.get("known", []),
            sources.get("profile", PROFILE),
        )

    def test_colleague_via_work_domains_takes_the_org(self) -> None:
        directory = self.build(inbound={"bob@example-fund.com": observed("Bob Lee", NOW - DAY)})
        bob = directory["bob@example-fund.com"]
        self.assertEqual(bob["relationship"], "colleague")
        self.assertEqual(bob["org"], "Example Fund")
        self.assertEqual(bob["name"], "Bob Lee")

    def test_colleague_via_the_operators_own_custom_domain(self) -> None:
        directory = self.build(sent={"carol@example-capital.com": observed("Carol", NOW - DAY)})
        carol = directory["carol@example-capital.com"]
        self.assertEqual(carol["relationship"], "colleague")
        self.assertIsNone(carol["org"])  # no work_domains org for this domain
        self.assertEqual(carol["last_sent_ts"], NOW - DAY)

    def test_a_gmail_self_domain_does_not_make_every_gmail_user_a_colleague(self) -> None:
        directory = self.build(
            inbound={"dave.example@gmail.com": observed("Dave", NOW - DAY)},
            profile={"self_addresses": [ALICE_HOME]},
        )
        self.assertEqual(directory["dave.example@gmail.com"]["relationship"], "correspondent")
        self.assertEqual(people.colleague_domains({"self_addresses": [ALICE_HOME]}), {})
        self.assertIsNone(people.resolve(people.Directory(), "erin@gmail.com", {"self_addresses": [ALICE_HOME]}))

    def test_contacts_name_beats_the_message_display_name(self) -> None:
        directory = self.build(
            contacts=[{"email": "Bob@Example.com", "name": "Robert Example", "org": "Example Co", "title": "Partner"}],
            sent={"bob@example.com": observed("bobby", NOW - DAY, 3)},
            inbound={"bob@example.com": observed("Bob E.", NOW - 2 * DAY, 2)},
        )
        bob = directory["bob@example.com"]
        self.assertEqual(bob["name"], "Robert Example")
        self.assertEqual(bob["relationship"], "contact")
        self.assertEqual((bob["org"], bob["title"]), ("Example Co", "Partner"))
        self.assertEqual(bob["interactions"], 5)
        self.assertEqual((bob["last_sent_ts"], bob["last_received_ts"]), (NOW - DAY, NOW - 2 * DAY))

    def test_message_display_name_fills_in_without_a_contact(self) -> None:
        directory = self.build(sent={"erin@example.org": observed("Erin Example", NOW - DAY)})
        self.assertEqual(directory["erin@example.org"]["name"], "Erin Example")

    def test_self_and_assistant_addresses_are_excluded(self) -> None:
        directory = self.build(
            contacts=[{"email": ALICE, "name": "Alice"}],
            sent={ALICE_HOME.upper(): observed("Me", NOW - DAY)},
            inbound={ASSISTANT: observed("Assistant", NOW - DAY)},
            known=[ALICE],
        )
        self.assertEqual(directory, {})

    def test_known_contacts_alone_make_a_correspondent(self) -> None:
        directory = self.build(known=["Frank@Example.org"])
        self.assertEqual(directory["frank@example.org"]["relationship"], "correspondent")

    def test_lookup_is_case_insensitive(self) -> None:
        directory = self.build(inbound={"gina@example.org": observed("Gina", NOW - DAY)})
        self.assertEqual(people.lookup(directory, "Gina <GINA@example.org>")["name"], "Gina")
        self.assertIsNone(people.lookup(directory, "nobody@example.org"))


# --- Source parsing ---------------------------------------------------------------


class SourceParsingTest(unittest.TestCase):
    def test_people_api_pages_parse_and_skip_entries_without_email(self) -> None:
        service = FakePeople(
            [
                {
                    "connections": [
                        connection("Bob Lee", ["bob@example-fund.com", "BOB@example.org"], "Example Fund", "Partner"),
                        connection("No Email Person", []),
                    ],
                    "totalPeople": 4,
                },
                {"connections": [connection("Carol Example", ["carol@example.com"]), connection(None, ["anon@example.com"])]},
            ]
        )
        contacts = people.fetch_contacts(service)
        self.assertEqual(
            contacts,
            [
                {"email": "bob@example-fund.com", "name": "Bob Lee", "org": "Example Fund", "title": "Partner"},
                {"email": "bob@example.org", "name": "Bob Lee", "org": "Example Fund", "title": "Partner"},
                {"email": "carol@example.com", "name": "Carol Example", "org": None, "title": None},
                {"email": "anon@example.com", "name": None, "org": None, "title": None},
            ],
        )
        self.assertEqual(len(service.calls), 2)
        self.assertEqual(service.calls[0]["resourceName"], "people/me")
        self.assertEqual(service.calls[0]["personFields"], "names,emailAddresses,organizations")
        self.assertNotIn("pageToken", service.calls[0])
        self.assertEqual(service.calls[1]["pageToken"], "1")

    def test_sent_metadata_with_quoted_names_and_multiple_recipients(self) -> None:
        messages = [
            sent_message("s1", NOW - 3 * DAY, '"Lee, Bob" <bob@example.com>, carol@example.com', "Dave Example <DAVE@Example.com>"),
            sent_message("s2", NOW - DAY, "'Bob Lee' <bob@example.com>", f"Alice <{ALICE}>"),
        ]
        stamp, recipients = people.parse_sent_message(messages[0])
        self.assertEqual(stamp, NOW - 3 * DAY)
        self.assertEqual(
            recipients,
            [("bob@example.com", "Lee, Bob"), ("carol@example.com", ""), ("dave@example.com", "Dave Example")],
        )
        gmail = FakeSentGmail(messages)
        result = people.fetch_sent_recipients(gmail, exclude=[ALICE])
        self.assertEqual(result["bob@example.com"], {"name": "Bob Lee", "last_ts": NOW - DAY, "count": 2})
        self.assertEqual(result["carol@example.com"], {"name": None, "last_ts": NOW - 3 * DAY, "count": 1})
        self.assertNotIn(ALICE, result)
        self.assertEqual(gmail.list_calls[0]["q"], "in:sent newer_than:180d")
        self.assertEqual(gmail.list_calls[0]["maxResults"], 500)
        self.assertEqual(
            {(call["format"], tuple(call["metadataHeaders"])) for call in gmail.get_calls},
            {("metadata", ("To", "Cc", "Date"))},
        )

    def test_sent_history_reads_at_most_500_messages(self) -> None:
        gmail = FakeSentGmail(
            [sent_message(f"s{index}", NOW - index, "bob@example.com") for index in range(520)]
        )
        result = people.fetch_sent_recipients(gmail)
        self.assertEqual(len(gmail.get_calls), 500)
        self.assertEqual(result["bob@example.com"]["count"], 500)

    def test_one_failed_message_is_skipped_but_all_failing_raises(self) -> None:
        gmail = FakeSentGmail(
            [sent_message("ok", NOW - DAY, "bob@example.com"), sent_message("bad", NOW, "carol@example.com")],
            failing_ids=("bad",),
        )
        self.assertEqual(set(people.fetch_sent_recipients(gmail)), {"bob@example.com"})
        with self.assertRaises(RuntimeError):
            people.fetch_sent_recipients(FakeSentGmail([sent_message("bad", NOW, "c@example.com")], failing_ids=("bad",)))


# --- Refresh + cache -----------------------------------------------------------------


class RefreshTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        self.log: list[str] = []
        self.people_service = FakePeople(
            [{"connections": [connection("Carol Example", ["carol@example.com"], "Example Co")]}]
        )
        self.gmail = FakeSentGmail([sent_message("s1", NOW - 2 * DAY, "Bob Lee <bob@example.org>")])

    def refresh(self, now: float, *, people_service: Any = None, **extra: Any) -> dict[str, int]:
        service = self.people_service if people_service is None else people_service
        return people.refresh_people_cache(
            self.home,
            now,
            accounts=[ALICE],
            gmail_services={ALICE: self.gmail},
            people_service=lambda account: service(account) if callable(service) else service,
            profile=PROFILE,
            log=self.log.append,
            **extra,
        )

    def test_second_run_within_24h_makes_no_people_or_sent_calls(self) -> None:
        self.refresh(NOW)
        self.assertEqual((len(self.people_service.calls), self.gmail.calls), (1, 2))
        self.refresh(NOW + 23 * 3600)
        self.assertEqual((len(self.people_service.calls), self.gmail.calls), (1, 2))
        self.refresh(NOW + 25 * 3600)
        self.assertEqual((len(self.people_service.calls), self.gmail.calls), (2, 4))
        self.assertEqual(self.log, [])

    def test_inbound_senders_merge_incrementally_between_refreshes(self) -> None:
        self.refresh(NOW)
        inbound = [
            {"from": "Gina Example <gina@example.org>", "from_address": "gina@example.org", "created_at_ts": NOW + 60, "direction": "inbound"},
            {"from": "News <news@example.org>", "from_address": "news@example.org", "created_at_ts": NOW + 60, "bulk_sender": True},
        ]
        self.refresh(NOW + 3600, inbound=inbound)
        self.refresh(NOW + 7200, inbound=inbound)  # the same message seen again is not recounted
        directory = people.load_directory(self.home)
        self.assertEqual(directory["gina@example.org"]["relationship"], "correspondent")
        self.assertEqual(directory["gina@example.org"]["interactions"], 1)
        self.assertNotIn("news@example.org", directory)
        self.assertEqual(directory["carol@example.com"]["relationship"], "contact")
        self.assertEqual(directory["bob@example.org"]["last_sent_ts"], NOW - 2 * DAY)
        self.assertEqual((len(self.people_service.calls), self.gmail.calls), (1, 2))

    def test_own_sends_between_refreshes_update_last_sent(self) -> None:
        self.refresh(NOW)
        outbound = [
            {
                "account": ALICE,
                "direction": "outbound",
                "created_at_ts": NOW + 600,
                "recipients": [{"email": "hank@example.org", "name": "Hank Example"}, {"email": ALICE}],
            }
        ]
        self.refresh(NOW + 3600, outbound=outbound)
        hank = people.load_directory(self.home)["hank@example.org"]
        self.assertEqual((hank["relationship"], hank["last_sent_ts"], hank["name"]), ("correspondent", NOW + 600, "Hank Example"))

    def test_missing_scope_is_skipped_without_exception_and_logged_once_a_day(self) -> None:
        self.refresh(NOW, people_service=lambda account: None)
        self.refresh(NOW + 3600, people_service=lambda account: None)
        self.assertEqual(len(self.log), 1)
        self.assertIn("contacts.readonly", self.log[0])
        self.refresh(NOW + DAY + 1, people_service=lambda account: None)
        self.assertEqual(len(self.log), 2)
        directory = people.load_directory(self.home)
        self.assertEqual(directory["bob@example.org"]["relationship"], "correspondent")
        self.assertTrue(directory.complete)

    def test_a_failing_source_logs_one_line_and_retries_after_an_hour(self) -> None:
        def broken(account: str) -> Any:
            raise RuntimeError("People API has not been used in project 123 (owner bob@example.org)")

        stats = self.refresh(NOW, people_service=broken)
        self.assertEqual(stats["failures"], 1)
        self.assertEqual(len(self.log), 1)
        self.assertNotIn("bob@example.org", self.log[0])
        self.refresh(NOW + 600, people_service=broken)
        self.assertEqual(len(self.log), 1)  # backoff: no retry within the hour
        self.refresh(NOW + 3601, people_service=broken)
        self.assertEqual(len(self.log), 2)

    def test_cache_is_written_with_mode_0600_and_capped(self) -> None:
        inbound = [
            {"from": f"Person {index} <p{index}@example.org>", "created_at_ts": NOW - index, "direction": "inbound"}
            for index in range(people.MAX_PEOPLE + 25)
        ]
        self.refresh(NOW, inbound=inbound)
        path = self.home / "town-feed" / "people-cache.json"
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        cache = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(cache["people"]), people.MAX_PEOPLE)
        self.assertLessEqual(len(cache["inbound"]), people.MAX_SOURCE_ENTRIES)
        # Most recent exchanges survive the cap.
        self.assertIn("p0@example.org", cache["people"])
        self.assertNotIn(f"p{people.MAX_PEOPLE + 24}@example.org", cache["people"])
        self.assertEqual(list(self.home.joinpath("town-feed").glob(".people-cache-*")), [])

    def test_unreadable_cache_loads_empty_and_is_rebuilt(self) -> None:
        path = self.home / "town-feed" / "people-cache.json"
        path.parent.mkdir(parents=True)
        path.write_text("{not json", encoding="utf-8")
        with contextlib.redirect_stderr(io.StringIO()) as stderr:
            self.assertEqual(people.load_directory(self.home), {})
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        with contextlib.redirect_stderr(io.StringIO()):
            self.refresh(NOW)
        self.assertIn("carol@example.com", people.load_directory(self.home))


# --- Calendar attendee enrichment ---------------------------------------------------------


class CalendarEnrichmentTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name)
        with mock.patch.dict(os.environ, {"HERMES_HOME": str(self.home)}):
            self.scanner = support.load_script("town-calendar-scan.py")
        for patcher in (
            mock.patch.object(self.scanner, "PEOPLE_PATH", support.DASHBOARD / "people.py"),
            mock.patch.object(self.scanner, "OPERATOR_PROFILE_PATH", support.DASHBOARD / "operator_profile.py"),
            mock.patch.object(self.scanner, "_user_timezone", return_value=ZoneInfo("America/New_York")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.now = datetime(2026, 10, 11, 10, 0, tzinfo=ZoneInfo("America/New_York"))

    def event(self, *attendees: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
        start = self.now + timedelta(hours=3)
        return (
            ALICE,
            "Work",
            {
                "id": "sync-1",
                "iCalUID": "sync-1@example.com",
                "status": "confirmed",
                "summary": "Fund sync",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": (start + timedelta(hours=1)).isoformat()},
                "attendees": list(attendees),
            },
        )

    def write_cache(self) -> None:
        cache = {
            "accounts": {ALICE: {"sent_refreshed_at": NOW, "contacts_refreshed_at": NOW}},
            "people": people.build_directory(
                [{"email": "carol@example.com", "name": "Carol Example", "org": "Example Co"}],
                {"bob@example-fund.com": observed("Bob", NOW - 3 * DAY)},
                {},
                [],
                PROFILE,
            ),
        }
        people.write_cache(self.home, cache)
        operator = self.home / "town-feed" / "operator.json"
        operator.write_text(json.dumps(PROFILE), encoding="utf-8")

    def test_attendees_gain_directory_facts_from_the_cache(self) -> None:
        self.write_cache()
        enrich = self.scanner._people_enricher([ALICE])
        self.assertIsNotNone(enrich)
        payload = self.scanner._build_payload(
            [
                self.event(
                    {"email": ALICE, "self": True, "responseStatus": "accepted"},
                    {"email": "Bob@Example-Fund.com", "displayName": "bob.lee", "responseStatus": "accepted"},
                    {"email": "carol@example.com", "displayName": "C. Example", "responseStatus": "declined"},
                    {"email": "dana@example.org", "displayName": "Dana Example"},
                    {"email": ALICE_HOME, "responseStatus": "accepted"},
                )
            ],
            self.now,
            [ALICE],
            enrich_attendee=enrich,
        )
        attendees = {entry["email"]: entry for entry in payload["events"][0]["attendees"]}
        self.assertEqual(attendees[ALICE], {"email": ALICE, "response": "accepted", "self": True})
        bob = attendees["bob@example-fund.com"]
        self.assertEqual(
            (bob["name"], bob["org"], bob["relationship"], bob["last_contact_ts"]),
            ("Bob", "Example Fund", "colleague", NOW - 3 * DAY),
        )
        carol = attendees["carol@example.com"]
        self.assertEqual((carol["name"], carol["org"], carol["relationship"]), ("Carol Example", "Example Co", "contact"))
        self.assertEqual(carol["response"], "declined")
        self.assertNotIn("last_contact_ts", carol)
        dana = attendees["dana@example.org"]
        self.assertEqual((dana["name"], dana["relationship"]), ("Dana Example", "new"))
        self.assertTrue(attendees[ALICE_HOME]["self"])  # profile self address, not Google's flag

    def test_missing_cache_is_a_no_op(self) -> None:
        self.assertIsNone(self.scanner._people_enricher([ALICE]))
        payload = self.scanner._build_payload(
            [self.event({"email": "bob@example-fund.com", "displayName": "Bob Lee"})],
            self.now,
            [ALICE],
            enrich_attendee=self.scanner._people_enricher([ALICE]),
        )
        self.assertEqual(payload["events"][0]["attendees"], [{"email": "bob@example-fund.com", "name": "Bob Lee"}])

    def test_self_attendees_survive_the_entry_cap(self) -> None:
        guests = [{"email": f"guest{index:02d}@example.org"} for index in range(40)]
        payload = self.scanner._build_payload(
            [self.event(*guests, {"email": ALICE, "responseStatus": "accepted"})], self.now, [ALICE]
        )
        row = payload["events"][0]
        self.assertEqual(len(row["attendees"]), self.scanner.MAX_ATTENDEE_ENTRIES)
        self.assertEqual(row["attendee_count"], 41)
        self.assertEqual(row["attendees"][-1], {"email": ALICE, "response": "accepted", "self": True})


# --- First contact ---------------------------------------------------------------------


def complete_directory() -> Any:
    directory = people.Directory(
        people.build_directory(
            [{"email": "carol@example.com", "name": "Carol Example"}],
            {"erin@example.org": observed("Erin", NOW - 9 * DAY)},
            {"gina@example.org": observed("Gina", NOW - 2 * DAY)},
            [],
            PROFILE,
        )
    )
    directory.complete = True
    return directory


class FirstContactTest(unittest.TestCase):
    def test_only_new_people_are_first_contact(self) -> None:
        index = people.PeopleIndex(complete_directory(), PROFILE, now=NOW)
        verdicts = {
            address: index.is_first_contact({"email": address})
            for address in (
                "bob@example-fund.com",  # colleague (work_domains)
                "zed@example-capital.com",  # colleague (own custom domain), not in directory
                "carol@example.com",  # contact
                "erin@example.org",  # correspondent (sent)
                "gina@example.org",  # correspondent (inbound)
                "dana@example.org",  # unknown -> new
                ALICE,  # the operator
            )
        }
        self.assertEqual(
            verdicts,
            {
                "bob@example-fund.com": False,
                "zed@example-capital.com": False,
                "carol@example.com": False,
                "erin@example.org": False,
                "gina@example.org": False,
                "dana@example.org": True,
                ALICE: False,
            },
        )
        self.assertFalse(index.is_first_contact({"email": "dana@example.org", "self": True}))

    def test_without_sent_history_no_one_is_a_first_contact(self) -> None:
        incomplete = people.Directory({"carol@example.com": people.lookup(complete_directory(), "carol@example.com")})
        index = people.PeopleIndex(incomplete, PROFILE, now=NOW)
        self.assertFalse(index.is_first_contact({"email": "dana@example.org"}))
        self.assertFalse(people.PeopleIndex(people.Directory(), PROFILE, now=NOW).is_first_contact({"email": "dana@example.org"}))

    def test_known_contacts_are_never_first_contact(self) -> None:
        index = people.PeopleIndex(complete_directory(), PROFILE, now=NOW, known=["dana@example.org"])
        self.assertFalse(index.is_first_contact({"email": "dana@example.org"}))

    def test_signal_groups_list_only_new_attendees(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            api = support.load_plugin_api(Path(tmp))
        event = {
            "summary": "Fund sync",
            "start_iso": r4.iso(NOW + 3600),
            "end_iso": r4.iso(NOW + 7200),
            "all_day": False,
            "account": ALICE,
            "attendees": [
                {"email": ALICE, "self": True},
                {"email": "bob@example-fund.com", "name": "Bob"},
                {"email": "carol@example.com", "name": "Carol Example"},
                {"email": "erin@example.org", "name": "Erin"},
                {"email": "dana@example.org", "name": "Dana Example"},
            ],
        }
        index = people.PeopleIndex(complete_directory(), PROFILE, now=NOW)
        groups = api._suggestion_signal_groups([], [], [], [], None, [event], NOW, people=index)
        self.assertEqual(groups["events_next_48h"][0]["first_contact_attendees"], ["dana@example.org"])
        # A colleague missing from known_contacts is no longer a first contact.
        legacy = api._suggestion_signal_groups([], [], [], [], None, [event], NOW, known_contacts={"x@example.org"})
        self.assertNotIn("first_contact_attendees", legacy["events_next_48h"][0])


# --- LLM inputs and prep ------------------------------------------------------------------


class PeopleLLM(r4.FakeLLM):
    """Records inputs and instructions; the day brief returns ``prep(meeting_people)``."""

    def __init__(self, prep: Any = None) -> None:
        super().__init__()
        self.prep = prep
        self.instructions: dict[str, str] = {}
        self.schemas: dict[str, Any] = {}

    def complete_structured(self, **kwargs: Any) -> Any:
        self.instructions[kwargs["schema_name"]] = kwargs["instructions"]
        self.schemas[kwargs["schema_name"]] = kwargs["json_schema"]
        if kwargs["schema_name"] != "town_day_brief":
            return super().complete_structured(**kwargs)
        data = json.loads(kwargs["input"][0]["text"])
        self.calls.append((kwargs["schema_name"], data))
        prep = self.prep(data["meeting_people"]) if self.prep else []
        return SimpleNamespace(parsed={"day_summary": "Two syncs today.", "callouts": [], "prep": prep})


class CuratorPeopleTest(r4.CuratorCase):
    def setUp(self) -> None:
        super().setUp()
        (self.home / "town-feed").mkdir()
        (self.home / "town-feed" / "operator.json").write_text(json.dumps(PROFILE), encoding="utf-8")
        cache = {
            "accounts": {ALICE: {"sent_refreshed_at": NOW - 3600}},
            "people": people.build_directory(
                [{"email": "carol@example.com", "name": "Carol Example", "org": "Example Co", "title": "Partner"}],
                {"bob@example-fund.com": observed("Bob Lee", NOW - 3 * DAY, 4)},
                {"gina@example.org": observed("Gina Example", NOW - 120, 1)},
                [],
                PROFILE,
            ),
        }
        people.write_cache(self.home, cache)
        self.cron.job(r4.GMAIL_JOB, "town-gmail-scan")
        self.cron.job(r4.CALENDAR_JOB, "town-calendar-scan")
        gmail = r4.gmail_candidate("m-1", "t-1", NOW - 120, "Venue deposit", sender="Gina Example <gina@example.org>", label="action needed")
        gmail["account"] = ALICE
        gmail["source"] = f"gmail:{ALICE}:m-1"
        gmail["direction"] = "inbound"
        gmail["recipients"] = [
            {"email": ALICE, "name": "Alice"},
            {"email": "bob@example-fund.com", "name": "bob"},
            {"email": "carol@example.com"},
            {"email": "dana@example.org", "name": "Dana Example"},
        ]
        self.gmail = gmail
        self.events = [
            self.meeting("sync", "Fund sync", 2, [
                {"email": ALICE, "self": True},
                {"email": "bob@example-fund.com", "name": "Bob Lee", "org": "Example Fund", "relationship": "colleague", "last_contact_ts": NOW - 3 * DAY},
                {"email": "dana@example.org", "name": "Dana Example", "relationship": "new"},
                {"email": "carol@example.com", "name": "Carol Example", "response": "declined"},
            ], attendee_count=12, description="Agenda: Q4 allocation memo."),
            self.meeting("intro", "Intro call", 5, [{"email": ALICE, "self": True}, {"email": "erin@example.org", "name": "Erin Example"}]),
            self.meeting("review", "Board review", 8, [{"email": "gina@example.org", "name": "Gina Example"}]),
            self.meeting("solo", "Focus block", 9, [{"email": ALICE, "self": True}]),
            self.meeting("later", "Offsite planning", 30, [{"email": "bob@example-fund.com"}]),
        ]
        calendar_candidate = {
            "headline": "Fund sync — in 2h",
            "summary": "12 attendees · Agenda: Q4 allocation memo.",
            "source": f"calendar:{ALICE}:sync",
            "created_at": r4.iso(NOW + 2 * 3600),
            "content_hash": r4.digest("calendar", "sync"),
        }
        self.cron.doc(r4.GMAIL_JOB, 5, {"candidates": [gmail], "awaiting_reply": [], "known_contacts": []})
        self.cron.doc(r4.CALENDAR_JOB, 5, {"candidates": [calendar_candidate], "events": self.events, "next_event": None})

    @staticmethod
    def meeting(
        event_id: str, summary: str, hours: float, attendees: list[dict[str, Any]], **extra: Any
    ) -> dict[str, Any]:
        event = {
            "summary": summary,
            "start_iso": r4.iso(NOW + hours * 3600),
            "end_iso": r4.iso(NOW + (hours + 1) * 3600),
            "all_day": False,
            "account": ALICE,
            "event_type": "default",
            "ical_uid": f"{event_id}@example.com",
            "attendees": attendees,
        }
        if "attendee_count" in extra:
            event["attendee_count"] = extra["attendee_count"]
        if "description" in extra:
            event["description_excerpt"] = extra["description"]
        return event

    def test_ranking_rows_carry_people_with_human_last_contact(self) -> None:
        llm = PeopleLLM()
        self.api.run_curator(llm=llm)
        ranking = llm.inputs("town_feed_ranking")[0]
        rows = {row["source"]: row for row in ranking["ranking_candidates"]}
        mail = rows[f"gmail:{ALICE}:m-1"]["people"]
        self.assertEqual(
            mail,
            [
                # Gina's only email is this one: still new for this row.
                {"name": "Gina Example", "org": None, "relationship": "new", "last_contact": "no prior email"},
                {"name": "Bob Lee", "org": "Example Fund", "relationship": "colleague", "last_contact": "you emailed 3 days ago"},
                {"name": "Carol Example", "org": "Example Co", "relationship": "contact", "last_contact": None, "title": "Partner"},
                {"name": "Dana Example", "org": None, "relationship": "new", "last_contact": "no prior email"},
            ],
        )
        meeting = rows[f"calendar:{ALICE}:sync"]["people"]
        self.assertEqual([entry["name"] for entry in meeting], ["Bob Lee", "Dana Example", "Carol Example"])
        self.assertEqual(meeting[0]["last_contact"], "you emailed 3 days ago")
        self.assertEqual(meeting[2]["response"], "declined")
        self.assertNotIn(ALICE, json.dumps([row.get("people") for row in rows.values()]))
        self.assertIn(
            "no prior email history with someone only when their relationship is new",
            llm.instructions["town_feed_ranking"],
        )
        self.assertEqual(
            ranking["suggestion_signals"]["events_next_48h"][0]["first_contact_attendees"], ["dana@example.org"]
        )

    def test_day_brief_gets_meeting_people_and_only_new_first_contacts(self) -> None:
        llm = PeopleLLM()
        self.api.run_curator(llm=llm)
        brief = llm.inputs("town_day_brief")[0]
        meetings = brief["meeting_people"]
        self.assertEqual([meeting["summary"] for meeting in meetings], ["Fund sync", "Intro call", "Board review"])
        self.assertEqual(meetings[0]["attendee_count"], 12)
        self.assertEqual(meetings[0]["details"], "Agenda: Q4 allocation memo.")
        self.assertEqual(
            meetings[0]["people"][:2],
            [
                {"email": "bob@example-fund.com", "name": "Bob Lee", "org": "Example Fund", "relationship": "colleague", "last_contact": "you emailed 3 days ago"},
                {"email": "dana@example.org", "name": "Dana Example", "org": None, "relationship": "new", "last_contact": "no prior email"},
            ],
        )
        self.assertEqual(meetings[2]["people"][0]["last_contact"], "emailed you 2 minutes ago")
        self.assertTrue(all(len(meeting["people"]) <= 6 for meeting in meetings))
        self.assertEqual(brief["first_contact_attendees"], ["dana@example.org", "erin@example.org"])
        self.assertIn("prep", llm.schemas["town_day_brief"]["properties"])
        self.assertIn("no prior email history only when relationship is new", llm.instructions["town_day_brief"])

    def test_prep_is_grounded_normalized_persisted_and_served(self) -> None:
        long_line = "Colleague at Example Fund; you emailed 3 days ago about the allocation memo and the Q4 numbers"

        def prep(meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
            first, second, third = (meeting["event_key"] for meeting in meetings[:3])
            return [
                {
                    "event_key": first,
                    "people": [
                        {"email": "bob@example-fund.com", "line": long_line},
                        {"email": "mallory@example.org", "line": "Invented guest"},
                    ],
                    "talking_point": "Open with the Q4 allocation memo.",
                },
                {"event_key": "ev_madeup", "people": [{"email": "bob@example-fund.com", "line": "x"}]},
                {"event_key": third, "people": [{"email": "gina@example.org", "line": "Beyond the next two"}]},
                {"event_key": second, "people": [{"email": "erin@example.org", "line": "First meeting; no prior email"}]},
            ]

        llm = PeopleLLM(prep)
        self.api.run_curator(llm=llm)
        stored = self.state()["day_brief"]["prep"]
        keys = [meeting["event_key"] for meeting in llm.inputs("town_day_brief")[0]["meeting_people"]]
        # Invented key, invented guest, and the third meeting are dropped before the cap.
        self.assertEqual([entry["event_key"] for entry in stored], [keys[0], keys[1]])
        self.assertGreater(len(long_line), 90)
        self.assertEqual(stored[0]["people"], [{"email": "bob@example-fund.com", "line": long_line[:87].rstrip() + "..."}])
        self.assertLessEqual(len(stored[0]["people"][0]["line"]), 90)
        self.assertEqual(stored[0]["talking_point"], "Open with the Q4 allocation memo.")
        self.assertEqual(stored[1]["people"], [{"email": "erin@example.org", "line": "First meeting; no prior email"}])

        feed = self.api.get_feed()
        self.assertEqual(feed["day_brief"]["prep"], stored)
        schedule_keys = {event["summary"]: event["event_key"] for event in feed["schedule"]}
        self.assertEqual(schedule_keys["Fund sync"], keys[0])
        bob = next(a for a in feed["schedule"][0]["attendees"] if a.get("email") == "bob@example-fund.com")
        self.assertEqual((bob["org"], bob["relationship"], bob["last_contact_ts"]), ("Example Fund", "colleague", NOW - 3 * DAY))

    def test_prep_normalizer_caps_and_old_briefs_without_prep_load(self) -> None:
        normalized = self.api._normalize_day_brief(
            {
                "day_summary": "Busy day.",
                "callouts": [],
                "prep": [
                    {"event_key": "ev_a", "people": [{"email": "Bob@Example.com", "line": "  Colleague;\n emailed  today "}, {"email": "not-an-email", "line": "x"}]},
                    {"event_key": "ev_a", "people": [{"email": "c@example.com", "line": "duplicate key"}]},
                    {"event_key": "ev_b", "people": [], "talking_point": "Ask about the memo."},
                    {"event_key": "ev_c", "people": [{"email": "d@example.com", "line": "third"}]},
                    {"event_key": "", "people": [{"email": "e@example.com", "line": "no key"}]},
                ],
            }
        )
        self.assertEqual(
            normalized["prep"],
            [
                {"event_key": "ev_a", "people": [{"email": "bob@example.com", "line": "Colleague; emailed today"}]},
                {"event_key": "ev_b", "people": [], "talking_point": "Ask about the memo."},
            ],
        )
        old = {"day_summary": "A light Sunday.", "callouts": [], "trip": None, "generated_at": r4.iso(NOW - 60), "local_date": datetime.fromtimestamp(NOW).astimezone().date().isoformat()}
        self.assertNotIn("prep", self.api._normalize_day_brief(old))
        self.write_state({"version": 6, "items": [], "decisions": {}, "day_brief": old})
        state = self.api._read_state_unlocked()
        self.assertEqual(state["day_brief"]["day_summary"], "A light Sunday.")
        served = self.api.get_feed()["day_brief"]
        self.assertEqual(served["day_summary"], "A light Sunday.")
        self.assertNotIn("prep", served)

    def test_without_a_people_cache_nothing_is_claimed(self) -> None:
        (self.home / "town-feed" / "people-cache.json").unlink()
        llm = PeopleLLM()
        self.api.run_curator(llm=llm)
        brief = llm.inputs("town_day_brief")[0]
        # Only the scanner's own enrichment (Dana: new) can ground a claim.
        self.assertEqual(brief["first_contact_attendees"], ["dana@example.org"])
        mail = {row["source"]: row for row in llm.inputs("town_feed_ranking")[0]["ranking_candidates"]}
        sender = mail[f"gmail:{ALICE}:m-1"]["people"][0]
        self.assertEqual((sender["name"], sender["relationship"], sender["last_contact"]), ("Gina Example", None, None))


class SenderAsOfTest(unittest.TestCase):
    def test_this_message_is_not_prior_history(self) -> None:
        gina = people._person("gina@example.org", relationship="correspondent", last_received_ts=NOW, interactions=1)
        self.assertEqual(people.sender_as_of(gina, NOW)["relationship"], "new")
        self.assertEqual(people.sender_as_of(gina, NOW, known=True)["relationship"], "correspondent")
        repeat = dict(gina, interactions=3)
        self.assertEqual(people.sender_as_of(repeat, NOW)["relationship"], "correspondent")
        self.assertEqual(people.last_contact_text(people.sender_as_of(repeat, NOW), NOW), "emailed before")
        replied = dict(gina, last_sent_ts=NOW - 2 * DAY)
        self.assertEqual(people.last_contact_text(people.sender_as_of(replied, NOW), NOW), "you emailed 2 days ago")

    def test_last_contact_text(self) -> None:
        text = people.last_contact_text
        self.assertEqual(text(people._person("a@example.org"), NOW), "no prior email")
        self.assertEqual(text(people._person("a@example.org", relationship="correspondent", last_received_ts=NOW - 5 * DAY), NOW), "emailed you 5 days ago")
        self.assertEqual(text(people._person("a@example.org", relationship="contact", last_sent_ts=NOW - 30 * DAY), NOW), "you emailed 4 weeks ago")
        self.assertIsNone(text(people._person("a@example.org", relationship="colleague"), NOW))


# --- Gmail scan contract --------------------------------------------------------------------


def person_message(message_id: str, internal_date: int, subject: str) -> dict[str, Any]:
    body = base64.urlsafe_b64encode(f"{subject}. Could you send the deck before Friday?".encode()).decode()
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "internalDate": str(internal_date),
        "labelIds": ["INBOX", "UNREAD"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Gina Example <gina@example.org>"},
                {"name": "To", "value": f"Alice <{r2.ACCOUNT}>"},
                {"name": "Cc", "value": '"Lee, Bob" <bob@example.org>'},
                {"name": "Subject", "value": subject},
            ],
            "body": {"data": body},
        },
    }


class GmailScanContractTest(unittest.TestCase):
    """The people refresh never changes the scan's exit code or stdout contract."""

    def run_scan(self, *, scopes: list[str], call_llm: Any, refresh: bool = True) -> tuple[int, dict[str, Any], str, Path]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        script = r2._load_sandboxed("town-gmail-scan.py", home)
        token = home / "google_token.json"
        token.write_text(json.dumps({"scopes": scopes, "token": "synthetic"}), encoding="utf-8")
        script.STATE_PATH.parent.mkdir(parents=True)
        script.STATE_PATH.write_text(
            json.dumps(
                {
                    "version": 1,
                    "source_state": {"gmail": {r2.ACCOUNT: 1_000_000}, "gmail_sent": {}},
                    "migrations": {"feed_state_gmail_v1": {"completed_at": "2026-01-01T00:00:00Z"}},
                }
            ),
            encoding="utf-8",
        )
        load_scanner = script._load_installed_google_scan

        def load_repo_scanner() -> Any:
            scanner = load_scanner()
            scanner._gmail_token_paths = lambda: [token]
            scanner.get_town_inbox_settings = lambda: (True, scanner.MIN_LABEL_CONFIDENCE, dict(scanner.DEFAULT_INBOX_TAXONOMY))
            if not refresh:
                del scanner.refresh_people
            return scanner

        scanner_module = support.load_module(support.DASHBOARD / "google_scan.py", "town_test_s2_google_scan")
        gmail = r2.FakeGmail(
            [person_message("msg-1", 1_100_000, "Deck for the fund"), r2._message("msg-2", 1_200_000, "Weekly roundup")],
            scanner_module.LABEL_COLORS,
        )
        google_modules = {
            "google.auth.transport.requests": SimpleNamespace(Request=object),
            "google.oauth2.credentials": SimpleNamespace(Credentials=r2._Credentials),
            "googleapiclient.discovery": SimpleNamespace(build=lambda *a, **k: gmail),
        }
        stderr = io.StringIO()
        with (
            mock.patch.object(script, "PLUGIN_PATH", support.DASHBOARD / "google_scan.py"),
            mock.patch.object(script, "_load_installed_google_scan", load_repo_scanner),
            mock.patch.dict(sys.modules, google_modules),
            mock.patch.dict(os.environ, {"HERMES_HOME": str(home)}),
            contextlib.redirect_stderr(stderr),
        ):
            code, payload = script._run(lambda: call_llm)
        stdout = json.loads(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
        return code, stdout, stderr.getvalue(), home

    def test_refresh_leaves_exit_code_and_stdout_unchanged(self) -> None:
        base_code, base_stdout, _, _ = self.run_scan(scopes=[], call_llm=r2._labeling_call_llm, refresh=False)
        code, stdout, stderr, home = self.run_scan(scopes=[], call_llm=r2._labeling_call_llm)
        self.assertEqual((code, base_code), (0, 0))
        self.assertEqual(stdout, base_stdout)
        self.assertEqual(
            set(stdout), {"candidates", "known_contacts", "awaiting_reply", "open_loops", "labeling", "watermarks_advanced"}
        )
        gina = next(c for c in stdout["candidates"] if c["message_id"] == "msg-1")
        self.assertEqual(
            gina["recipients"], [{"email": r2.ACCOUNT, "name": "Alice"}, {"email": "bob@example.org", "name": "Lee, Bob"}]
        )
        cache_path = home / "town-feed" / "people-cache.json"
        self.assertEqual(stat.S_IMODE(cache_path.stat().st_mode), 0o600)
        directory = people.load_directory(home)
        self.assertEqual(directory["gina@example.org"]["relationship"], "correspondent")
        self.assertNotIn("news@example.com", directory)  # bulk senders are not people
        # Missing contacts scope: one line, no people data on stderr.
        refresh_lines = [line for line in stderr.splitlines() if "people refresh" in line]
        self.assertEqual(len(refresh_lines), 1)
        self.assertIn("contacts.readonly", refresh_lines[0])
        self.assertNotIn("gina@example.org", stderr)

    def test_a_failing_refresh_never_changes_the_exit_code_or_masks_the_scan_error(self) -> None:
        # contacts.readonly is granted, but the fake credentials cannot build a
        # People client, so the contacts refresh fails on top of a classifier failure.
        code, stdout, stderr, _ = self.run_scan(scopes=[CONTACTS_SCOPE], call_llm=r2._failing_call_llm)
        base_code, base_stdout, _, _ = self.run_scan(scopes=[], call_llm=r2._failing_call_llm, refresh=False)
        self.assertEqual((code, base_code), (4, 4))
        self.assertEqual(stdout, base_stdout)
        town_lines = [line for line in stderr.splitlines() if line.startswith("town-gmail-scan:")]
        self.assertTrue(any("people refresh" in line and "contacts refresh failed" in line for line in town_lines))
        self.assertIn(r2.CLASSIFIER_FAILURE, town_lines[-1])

    def test_refresh_wrapper_swallows_unexpected_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = r2._load_sandboxed("town-gmail-scan.py", Path(tmp))

        def explode(*_: Any, **__: Any) -> None:
            raise KeyError("carol@example.com")

        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            script._refresh_people(SimpleNamespace(refresh_people=explode), {r2.ACCOUNT: object()}, [], [], [])
        self.assertEqual(len(stderr.getvalue().splitlines()), 1)
        self.assertNotIn("carol@example.com", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
