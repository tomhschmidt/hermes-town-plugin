#!/usr/bin/env python3
"""Scan selected Google calendars for the next 48 hours.

Exit codes:
  0: completed successfully (including no calendar-scoped tokens or no events)
  2: token input or configuration is invalid
  3: required Google API imports are unavailable
  5: Calendar authentication or API access failed
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
from datetime import date, datetime, time as datetime_time, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_AGENT = HERMES_HOME / "hermes-agent"
if HERMES_AGENT.exists():
    sys.path.insert(0, str(HERMES_AGENT))


def _foreign_python_error(
    version: tuple[int, ...] = tuple(sys.version_info[:2]),
) -> str | None:
    """Name the managed command when this Python cannot load Hermes' packages.

    hermes_bootstrap activates PM's dependency environment into the running
    interpreter, so another Python minor version dies on its first compiled
    import. Cron runs scripts on PM's interpreter; PM's own API names it.
    """
    try:
        from pm.environments import committed_venv, venv_python, venv_python_version

        environment = committed_venv(HERMES_AGENT)
        wanted = venv_python_version(environment) if environment else None
    except Exception as exc:
        return (
            f"Python {version[0]}.{version[1]} cannot load Hermes "
            f"({type(exc).__name__}: {exc}); run it the way cron does: "
            "hermes cron run <job_id>"
        )
    if wanted is None or tuple(version[:2]) == tuple(wanted):
        return None
    return (
        f"Python {version[0]}.{version[1]} cannot load Hermes' managed environment "
        f"(Python {wanted[0]}.{wanted[1]}); run: "
        f"{venv_python(environment)} {Path(__file__).resolve()}"
    )


# Bootstrap BEFORE importing agent dependencies, exactly like
# telegram-action-items-v2.py: PM activates its packages in this process.
# Cron already runs scripts on PM's interpreter, so there is no re-exec here.
_RUNTIME_ERROR = _foreign_python_error()
if _RUNTIME_ERROR is None:
    try:
        import hermes_bootstrap  # noqa: F401
    except Exception as exc:
        _RUNTIME_ERROR = f"cannot bootstrap Hermes: {exc}"


CALENDAR_SCOPES = {
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.readonly",
}
MAX_EVENTS_PER_ACCOUNT = 20
MAX_ATTENDEE_ENTRIES = 8
SCAN_WINDOW = timedelta(hours=48)
NTK_WINDOW = timedelta(hours=24)
# Ended flights and stays stay visible this long so the dashboard can say
# "back in <home city> after <origin>" (S1); travel detection lives in the
# plugin's where.py.
RECENT_TRAVEL_WINDOW = timedelta(hours=36)
MAX_RECENT_TRAVEL = 10
MAX_LOOKBACK_EVENTS = 50
BIRTHDAYS_CALENDAR_ID = "addressbook#contacts@group.v.calendar.google.com"
HOLIDAY_CALENDAR_SUFFIX = "#holiday@group.v.calendar.google.com"
CONFIG_PATH = HERMES_HOME / "config.yaml"
OPERATOR_PROFILE_PATH = HERMES_HOME / "plugins" / "town-feed" / "dashboard" / "operator_profile.py"
_OPERATOR_PROFILE_MODULE_NAME = "hermes_town_calendar_operator_profile"
PEOPLE_PATH = HERMES_HOME / "plugins" / "town-feed" / "dashboard" / "people.py"
_PEOPLE_MODULE_NAME = "hermes_town_calendar_people"
WHERE_PATHS = (
    HERMES_HOME / "plugins" / "town-feed" / "dashboard" / "where.py",
    Path(__file__).resolve().parents[1] / "town-feed" / "dashboard" / "where.py",
)
_WHERE_MODULE_NAME = "hermes_town_calendar_where"

# Google eventType values that are real commitments: NTK candidates and
# next_event come only from these. fromGmail rows are reservations Google
# copied from mail (flights, tables), so they count like ordinary events.
_COMMITMENT_EVENT_TYPES = {"default", "fromGmail"}
_WORKING_LOCATION_TYPES = {
    "homeOffice": "home",
    "officeLocation": "office",
    "customLocation": "custom",
}
_WORKING_LOCATION_LABEL_FIELDS = {"office": "officeLocation", "custom": "customLocation"}

_CONFERENCE_HOSTS = {
    "meet.google.com": "Google Meet",
    "zoom.us": "Zoom",
    "teams.microsoft.com": "Microsoft Teams",
    "streamyard.com": "StreamYard",
    "webex.com": "Webex",
}
_CONFERENCE_RE = re.compile(
    r"\b(" + "|".join(re.escape(host) for host in _CONFERENCE_HOSTS) + r")\b",
    re.IGNORECASE,
)
_LODGING_RE = re.compile(
    r"\b(hotel|inn|resort|ritz|marriott|hyatt|hilton|airbnb|lodging|stay|flight)\b",
    re.IGNORECASE,
)


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def _token_paths() -> list[Path]:
    paths = [HERMES_HOME / "google_token.json"]
    for path in sorted((HERMES_HOME / "google-accounts").glob("*/google_token.json")):
        if path not in paths:
            paths.append(path)
    return paths


def _stored_scopes(token: dict[str, Any]) -> set[str]:
    raw = token.get("scopes", token.get("scope", []))
    if isinstance(raw, str):
        values = raw.split()
    elif isinstance(raw, list):
        values = [value for value in raw if isinstance(value, str)]
    else:
        values = []
    return {value.strip() for value in values if value.strip()}


def _calendar_tokens() -> list[tuple[Path, dict[str, Any]]]:
    calendar_tokens: list[tuple[Path, dict[str, Any]]] = []
    for path in _token_paths():
        if not path.exists():
            continue
        try:
            token = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"could not read {path}: {exc}") from exc
        if not isinstance(token, dict):
            raise RuntimeError(f"invalid token in {path}: expected a JSON object")
        if _stored_scopes(token) & CALENDAR_SCOPES:
            calendar_tokens.append((path, token))
    return calendar_tokens


def _zone(name: Any) -> ZoneInfo | None:
    text = str(name or "").strip()
    if not text:
        return None
    try:
        return ZoneInfo(text)
    except Exception:
        return None


def _operator_profile_timezone() -> str:
    """``home_timezone`` from the installed plugin's operator profile, if any.

    The module is optional (older installs lack it); any failure falls through.
    """
    if not OPERATOR_PROFILE_PATH.is_file():
        return ""
    try:
        spec = importlib.util.spec_from_file_location(
            _OPERATOR_PROFILE_MODULE_NAME, OPERATOR_PROFILE_PATH
        )
        if spec is None or spec.loader is None:
            return ""
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        profile = module.load_operator_profile(HERMES_HOME)
    except Exception:
        return ""
    if not isinstance(profile, dict):
        return ""
    return str(profile.get("home_timezone") or "")


def _where_module() -> Any:
    """The plugin's pure where.py (installed copy, else the repo sibling).

    ``None`` when neither loads: the scan then reports no recent travel.
    """
    cached = sys.modules.get(_WHERE_MODULE_NAME)
    if cached is not None:
        return cached
    for path in WHERE_PATHS:
        if not path.is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(_WHERE_MODULE_NAME, path)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            sys.modules[_WHERE_MODULE_NAME] = module
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(_WHERE_MODULE_NAME, None)
            continue
        return module
    return None


def _config_timezone() -> str:
    """Hermes ``config.yaml`` top-level ``timezone`` (empty string when unset)."""
    try:
        text = CONFIG_PATH.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        match = re.match(r"timezone:(.*)$", line)
        if not match:
            continue
        value = re.sub(r"\s+#.*$", "", match.group(1)).strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1].strip()
        return value
    return ""


def _system_timezone() -> tzinfo:
    """The host's named zone (DST-aware), else its current fixed offset."""
    zone = _zone(os.environ.get("TZ", "").lstrip(":"))
    if zone is not None:
        return zone
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        target = ""
    if "zoneinfo/" in target:
        zone = _zone(target.split("zoneinfo/", 1)[1])
        if zone is not None:
            return zone
    return datetime.now().astimezone().tzinfo or timezone.utc


def _user_timezone() -> tzinfo:
    """Resolve the operator's zone: TOWN_TIMEZONE, operator profile, config, system.

    Invalid names fall through to the next source.
    """
    for source in (
        lambda: os.environ.get("TOWN_TIMEZONE", ""),
        _operator_profile_timezone,
        _config_timezone,
    ):
        zone = _zone(source())
        if zone is not None:
            return zone
    return _system_timezone()


def _parse_event_time(value: Any, user_tz: tzinfo = timezone.utc) -> tuple[datetime, bool] | None:
    """Timed values keep their exact instant; all-day dates anchor to local midnight."""
    if not isinstance(value, dict):
        return None
    date_time = value.get("dateTime")
    if isinstance(date_time, str) and date_time.strip():
        try:
            parsed = datetime.fromisoformat(date_time.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed, False

    date_value = value.get("date")
    if isinstance(date_value, str) and date_value.strip():
        try:
            parsed_date = date.fromisoformat(date_value.strip())
        except ValueError:
            return None
        return datetime.combine(parsed_date, datetime_time.min, tzinfo=user_tz), True
    return None


def _relative_start(start: datetime, now: datetime, all_day: bool, user_tz: tzinfo) -> str:
    local_start = start.astimezone(user_tz)
    if all_day:
        day_delta = (local_start.date() - now.astimezone(user_tz).date()).days
        if day_delta <= 0:
            return "today"
        if day_delta == 1:
            return "tomorrow"
        return local_start.strftime("%a")

    seconds = (start - now).total_seconds()
    if seconds < 0:
        minutes = max(1, round(abs(seconds) / 60))
        return f"started {minutes}m ago"
    if seconds < 60 * 60:
        return f"in {max(1, round(seconds / 60))}m"
    if seconds < 24 * 60 * 60:
        return f"in {max(1, round(seconds / 3600))}h"
    return f"{local_start.strftime('%a')} at {local_start.strftime('%-I:%M %p')}"


def _compact(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _event_details(events: list[dict[str, Any]], row: dict[str, Any]) -> str:
    parts: list[str] = []
    location = _compact(row.get("location"), 120)
    if location:
        parts.append(location)

    count = int(row.get("attendee_count") or 0)
    if count:
        parts.append(f"{count} attendee" if count == 1 else f"{count} attendees")

    conference = row.get("conference")
    if conference:
        parts.append(conference)

    for event in events:
        description = str(event.get("description") or "")
        first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
        if first_line:
            parts.append(_compact(first_line, 240))
            break
    return " · ".join(parts) or "No additional event details."


def _attendees(event: dict[str, Any]) -> list[dict[str, Any]]:
    raw = event.get("attendees")
    if not isinstance(raw, list):
        return []
    return [
        attendee
        for attendee in raw
        if isinstance(attendee, dict) and attendee.get("resource") is not True
    ]


def _attendee_entries(
    *events: dict[str, Any], self_addresses: frozenset[str] | set[str] = frozenset()
) -> tuple[list[dict[str, Any]], int]:
    """Attendee union across copies of one event, keyed by email (else name).

    Returns at most MAX_ATTENDEE_ENTRIES entries and the TRUE union size.
    The operator's own addresses are flagged ``self`` and survive the cap, so
    consumers can count the other attendees exactly.
    """
    merged: dict[str, dict[str, Any]] = {}
    anonymous = 0
    for event in events:
        unnamed = 0
        for attendee in _attendees(event):
            email = str(attendee.get("email") or "").strip().casefold()
            name = _compact(attendee.get("displayName"), 80)
            key = email or (f"name:{name.casefold()}" if name else "")
            if not key:
                unnamed += 1
                continue
            entry = merged.setdefault(key, {})
            if email:
                entry.setdefault("email", email)
            if name:
                entry.setdefault("name", name)
            response = str(attendee.get("responseStatus") or "").strip()
            if response:
                entry.setdefault("response", response)
            if attendee.get("self") is True or (email and email in self_addresses):
                entry["self"] = True
        anonymous = max(anonymous, unnamed)
    entries = [
        {field: entry[field] for field in ("email", "name", "response", "self") if field in entry}
        for entry in merged.values()
    ]
    if len(entries) > MAX_ATTENDEE_ENTRIES:
        own = [index for index, entry in enumerate(entries) if entry.get("self")]
        own = own[:MAX_ATTENDEE_ENTRIES]
        others = [index for index, entry in enumerate(entries) if not entry.get("self")]
        keep = set(own) | set(others[: MAX_ATTENDEE_ENTRIES - len(own)])
        capped = [entry for index, entry in enumerate(entries) if index in keep]
    else:
        capped = entries
    return capped, len(entries) + anonymous


def _has_other_attendee(events: list[dict[str, Any]], self_addresses: set[str]) -> bool:
    for event in events:
        for attendee in _attendees(event):
            email = str(attendee.get("email") or "").strip().casefold()
            if attendee.get("self") is True or (email and email in self_addresses):
                continue
            return True
    return False


def _self_declined(event: dict[str, Any]) -> bool:
    return any(
        attendee.get("self") is True
        and str(attendee.get("responseStatus") or "").strip() == "declined"
        for attendee in _attendees(event)
    )


def _event_type(event: dict[str, Any]) -> str:
    return str(event.get("eventType") or "").strip()[:32] or "default"


def _working_location(event: dict[str, Any]) -> dict[str, str] | None:
    properties = event.get("workingLocationProperties")
    if not isinstance(properties, dict):
        return None
    kind = _WORKING_LOCATION_TYPES.get(str(properties.get("type") or "").strip())
    if kind is None:
        kind = next(
            (value for key, value in _WORKING_LOCATION_TYPES.items() if key in properties),
            None,
        )
    if kind is None:
        return None
    location = {"type": kind}
    details = properties.get(_WORKING_LOCATION_LABEL_FIELDS.get(kind, ""))
    if isinstance(details, dict):
        label = _compact(details.get("label"), 120)
        if label:
            location["label"] = label
    return location


def _conference(event: dict[str, Any]) -> str:
    conference_data = event.get("conferenceData")
    if isinstance(conference_data, dict):
        solution = conference_data.get("conferenceSolution")
        if isinstance(solution, dict):
            name = _compact(solution.get("name"), 40)
            if name:
                return name
    if str(event.get("hangoutLink") or "").strip():
        return "Google Meet"
    match = _CONFERENCE_RE.search(
        f"{event.get('location') or ''} {event.get('description') or ''}"
    )
    if match:
        return _CONFERENCE_HOSTS[match.group(1).casefold()]
    return ""


def _enriched_event_row(
    event: dict[str, Any],
    *,
    account: str,
    calendar_name: str,
    start: datetime,
    end: datetime,
    all_day: bool,
    self_addresses: frozenset[str] | set[str] = frozenset(),
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "summary": _compact(event.get("summary"), 160) or "(untitled event)",
        "start_iso": start.isoformat(),
        "end_iso": end.isoformat(),
        "all_day": all_day,
        "account": account,
        "calendar": calendar_name,
        "event_type": _event_type(event),
    }
    working_location = _working_location(event) if row["event_type"] == "workingLocation" else None
    if working_location:
        row["working_location"] = working_location
    ical_uid = str(event.get("iCalUID") or "").strip()
    if ical_uid:
        row["ical_uid"] = ical_uid[:255]
    location = _compact(event.get("location"), 240)
    if location:
        row["location"] = location
    attendees, attendee_count = _attendee_entries(event, self_addresses=self_addresses)
    if attendees:
        row["attendees"] = attendees
        row["attendee_count"] = attendee_count
    description = " ".join(str(event.get("description") or "").split())
    if description:
        row["description_excerpt"] = _compact(description, 500)
    organizer = event.get("organizer")
    if isinstance(organizer, dict):
        organizer_name = _compact(
            organizer.get("displayName") or organizer.get("email"), 120
        )
        if organizer_name and organizer.get("self") is not True:
            row["organizer"] = organizer_name
    conference = _conference(event)
    if conference:
        row["conference"] = conference
    start_info = event.get("start")
    event_tz = ""
    if isinstance(start_info, dict):
        event_tz = str(start_info.get("timeZone") or "").strip()
        if event_tz:
            row["event_tz"] = event_tz[:64]
    end_info = event.get("end")
    if isinstance(end_info, dict):
        # Google's flight rows end in the arrival airport's zone.
        end_tz = str(end_info.get("timeZone") or "").strip()
        if end_tz and end_tz != event_tz:
            row["end_tz"] = end_tz[:64]
    if all_day and (end - start) >= timedelta(days=2):
        row["multi_day"] = True
    if all_day and _LODGING_RE.search(f"{row['summary']} {location}"):
        row["lodging_hint"] = True
    return row


def _candidate(
    events: list[dict[str, Any]],
    row: dict[str, Any],
    start: datetime,
    now: datetime,
    user_tz: tzinfo,
) -> dict[str, Any] | None:
    event_id = str(events[0].get("id") or "").strip()
    if not event_id:
        return None
    account = row["account"]
    start_iso = start.isoformat()
    canonical = "\n".join((account.casefold(), event_id, start_iso))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "key": digest[:16],
        "content_hash": digest,
        "source": f"calendar:{account}:{event_id}",
        "headline": f"{row['summary']} — {_relative_start(start, now, False, user_tz)}",
        "summary": _event_details(events, row),
        "created_at": start_iso,
        "created_at_ts": start.timestamp(),
    }


def _identity_keys(event: dict[str, Any], start: datetime, end: datetime) -> list[tuple[Any, ...]]:
    """Keys under which copies of one event collide across calendars.

    iCalUID is shared by every instance of a recurring series, so it only
    identifies an occurrence together with its start instant.
    """
    keys: list[tuple[Any, ...]] = []
    event_id = str(event.get("id") or "").strip()
    if event_id:
        keys.append(("id", event_id))
    ical_uid = str(event.get("iCalUID") or "").strip()
    if ical_uid:
        keys.append(("ical", ical_uid, start.timestamp()))
    summary = _compact(event.get("summary"), 160).casefold()
    if summary:
        keys.append(("text", summary, start.timestamp(), end.timestamp()))
    return keys


def _group_duplicates(items: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Union copies sharing any identity key; groups keep first-seen order."""
    parent = list(range(len(items)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    owner: dict[tuple[Any, ...], int] = {}
    for index, item in enumerate(items):
        for key in item["keys"]:
            if key not in owner:
                owner[key] = index
                continue
            first, second = find(owner[key]), find(index)
            if first != second:
                parent[max(first, second)] = min(first, second)
    groups: dict[int, list[dict[str, Any]]] = {}
    for index, item in enumerate(items):
        groups.setdefault(find(index), []).append(item)
    return [groups[root] for root in sorted(groups)]


def _operator_profile(accounts: Iterable[str]) -> dict[str, Any] | None:
    """The installed plugin's operator profile (with connected accounts), if any."""
    if not OPERATOR_PROFILE_PATH.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(
            _OPERATOR_PROFILE_MODULE_NAME, OPERATOR_PROFILE_PATH
        )
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        profile = module.load_operator_profile(HERMES_HOME, account_addresses=list(accounts))
    except Exception:
        return None
    return profile if isinstance(profile, dict) else None


def _people_enricher(accounts: Iterable[str]) -> Callable[[dict[str, Any]], dict[str, Any]] | None:
    """Directory-backed attendee enrichment (S2); None without a people cache.

    Reads ``people-cache.json`` through the installed plugin's ``people.py``.
    A missing module or cache means no enrichment, never a failed scan.
    """
    if not PEOPLE_PATH.is_file():
        return None
    try:
        spec = importlib.util.spec_from_file_location(_PEOPLE_MODULE_NAME, PEOPLE_PATH)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        directory = module.load_directory(HERMES_HOME)
    except Exception:
        return None
    if not directory:
        return None
    profile = _operator_profile(accounts) or {"self_addresses": sorted(accounts)}

    def enrich(attendee: dict[str, Any]) -> dict[str, Any]:
        try:
            return module.enrich_attendee(attendee, directory, profile)
        except Exception:
            return attendee

    return enrich


def _event_items(
    entries: Iterable[tuple[str, str, dict[str, Any]]], user_tz: tzinfo
) -> list[dict[str, Any]]:
    """Parsed, non-cancelled, non-declined copies of raw Google events."""
    items: list[dict[str, Any]] = []
    for account, calendar_name, event in entries:
        if not isinstance(event, dict) or event.get("status") == "cancelled":
            continue
        if not str(event.get("id") or "").strip() or _self_declined(event):
            continue
        parsed_start = _parse_event_time(event.get("start"), user_tz)
        parsed_end = _parse_event_time(event.get("end"), user_tz)
        if parsed_start is None or parsed_end is None:
            continue
        start, all_day = parsed_start
        end, _ = parsed_end
        items.append(
            {
                "account": account,
                "calendar": calendar_name,
                "event": event,
                "start": start,
                "end": end,
                "all_day": all_day,
                "keys": _identity_keys(event, start, end),
            }
        )
    return items


def _recent_travel(
    recent_entries: Iterable[tuple[str, str, dict[str, Any]]],
    now: datetime,
    user_tz: tzinfo,
) -> list[dict[str, Any]]:
    """Flights and stays that ended within RECENT_TRAVEL_WINDOW (``events`` row shape)."""
    where = _where_module()
    if where is None:
        return []
    items = [
        item
        for item in _event_items(recent_entries, user_tz)
        if now - RECENT_TRAVEL_WINDOW <= item["end"] <= now
    ]
    travel: list[tuple[datetime, dict[str, Any]]] = []
    for group in _group_duplicates(items):
        first = group[0]
        row = _enriched_event_row(
            first["event"],
            account=first["account"],
            calendar_name=first["calendar"],
            start=first["start"],
            end=first["end"],
            all_day=first["all_day"],
        )
        try:
            keep = where.is_travel_row(row)
        except Exception:
            keep = False
        if keep:
            travel.append((group[0]["start"], row))
    travel.sort(key=lambda entry: entry[0])
    return [row for _, row in travel[-MAX_RECENT_TRAVEL:]]


def _build_payload(
    entries: Iterable[tuple[str, str, dict[str, Any]]],
    now: datetime,
    accounts: Iterable[str] = (),
    recent_entries: Iterable[tuple[str, str, dict[str, Any]]] = (),
    enrich_attendee: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Turn raw ``(account, calendar name, Google event)`` rows into the scan payload.

    ``recent_entries`` come from the look-back query; only flights and stays
    that already ended are kept from them (``recent_travel``). Pure apart from
    resolving the operator's time zone and loading where.py; ``now`` is injected.
    ``enrich_attendee`` (S2) adds directory facts to each non-self attendee.
    """
    user_tz = _user_timezone()
    self_addresses = {str(account).strip().casefold() for account in accounts}
    items = [item for item in _event_items(entries, user_tz) if item["end"] > now]
    recent_travel = _recent_travel(recent_entries, now, user_tz)

    candidates: list[dict[str, Any]] = []
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    next_event: tuple[datetime, dict[str, Any]] | None = None
    for group in _group_duplicates(items):
        first = group[0]
        events = [item["event"] for item in group]
        start, all_day = first["start"], first["all_day"]
        rows = [
            _enriched_event_row(
                item["event"],
                account=item["account"],
                calendar_name=item["calendar"],
                start=item["start"],
                end=item["end"],
                all_day=item["all_day"],
                self_addresses=self_addresses,
            )
            for item in group
        ]
        row = rows[0]
        if len(group) > 1:
            calendars = list(dict.fromkeys(item["calendar"] for item in group))
            if len(calendars) > 1:
                row["calendars"] = calendars
            attendees, attendee_count = _attendee_entries(*events, self_addresses=self_addresses)
            if attendees:
                row["attendees"] = attendees
                row["attendee_count"] = attendee_count
            for field in ("location", "conference", "description_excerpt"):
                value = next((copy[field] for copy in rows if copy.get(field)), None)
                if value and field not in row:
                    row[field] = value
        if enrich_attendee is not None and row.get("attendees"):
            row["attendees"] = [enrich_attendee(attendee) for attendee in row["attendees"]]
        # Ongoing events count too: an in-progress multi-day hotel stay is
        # the downstream trip signal, not a past event.
        upcoming.append((start, row))

        commitment = not all_day and row["event_type"] in _COMMITMENT_EVENT_TYPES
        if commitment and start >= now and (next_event is None or start < next_event[0]):
            next_event = (start, row)
        if (
            commitment
            and now <= start <= now + NTK_WINDOW
            and (
                _has_other_attendee(events, self_addresses)
                or row.get("conference")
                or row.get("location")
            )
        ):
            candidate = _candidate(events, row, start, now, user_tz)
            if candidate:
                candidates.append(candidate)

    if not upcoming and not recent_travel:
        return None
    candidates.sort(key=lambda candidate: float(candidate["created_at_ts"]))
    slim: dict[str, Any] | None = None
    if next_event:
        row = next_event[1]
        slim = {
            "summary": row["summary"],
            "start_iso": row["start_iso"],
            "account": row["account"],
        }
        if row.get("location"):
            slim["location"] = row["location"]
    events_out = [row for _, row in sorted(upcoming, key=lambda entry: entry[0])[:20]]
    return {
        "candidates": candidates,
        "next_event": slim,
        "events": events_out,
        "recent_travel": recent_travel,
    }


def _run() -> tuple[int, dict[str, Any] | None]:
    try:
        calendar_tokens = _calendar_tokens()
    except Exception as exc:
        _eprint(f"town-calendar-scan: token/config failure: {exc}")
        return 2, None

    # Scope detection intentionally happens before Google imports. Gmail-only
    # installations are the shipping state and must be a completely silent no-op.
    if not calendar_tokens:
        return 0, None

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:
        _eprint(f"town-calendar-scan: Google API dependencies are unavailable: {exc}")
        return 3, None

    now = datetime.now(timezone.utc)
    time_max = now + SCAN_WINDOW
    time_back = now - RECENT_TRAVEL_WINDOW
    entries: list[tuple[str, str, dict[str, Any]]] = []
    recent_entries: list[tuple[str, str, dict[str, Any]]] = []
    seen_accounts: set[str] = set()
    fatal_errors: list[str] = []
    calendar_errors: list[str] = []

    for token_path, token in calendar_tokens:
        try:
            scopes = sorted(_stored_scopes(token))
            creds = Credentials.from_authorized_user_info(token, scopes=scopes)
            if creds.expired and creds.refresh_token:
                # Token files belong to the google-workspace skill. Refresh in
                # memory only; this scanner never writes shared credentials.
                creds.refresh(Request())
            if not creds.valid:
                raise RuntimeError("OAuth credentials are invalid or expired and could not be refreshed")

            service = build("calendar", "v3", credentials=creds, cache_discovery=False)
            calendars: list[dict[str, Any]] = []
            page_token: str | None = None
            while True:
                response = service.calendarList().list(
                    **({"pageToken": page_token} if page_token else {})
                ).execute()
                calendars.extend(
                    calendar
                    for calendar in response.get("items", []) or []
                    if isinstance(calendar, dict)
                )
                page_token = str(response.get("nextPageToken") or "").strip() or None
                if not page_token:
                    break

            primary = next(
                (calendar for calendar in calendars if calendar.get("primary") is True),
                None,
            )
            if primary is None:
                raise RuntimeError("calendar list did not return a primary calendar")
            account = str(primary.get("id") or "").strip().casefold()
            if not account:
                raise RuntimeError("primary calendar did not return an account id")
            if account in seen_accounts:
                continue
            seen_accounts.add(account)
            for calendar in calendars:
                calendar_id = str(calendar.get("id") or "").strip()
                if (
                    not calendar_id
                    or calendar.get("selected") is False
                    or calendar_id.endswith(HOLIDAY_CALENDAR_SUFFIX)
                    or calendar_id == BIRTHDAYS_CALENDAR_ID
                ):
                    continue
                calendar_name = _compact(
                    calendar.get("summaryOverride") or calendar.get("summary"), 160
                )
                try:
                    response = service.events().list(
                        calendarId=calendar_id,
                        timeMin=now.isoformat().replace("+00:00", "Z"),
                        timeMax=time_max.isoformat().replace("+00:00", "Z"),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=MAX_EVENTS_PER_ACCOUNT,
                    ).execute()
                except Exception as exc:
                    calendar_errors.append(
                        f"{token_path}: {calendar_name or calendar_id}: {exc}"
                    )
                    continue
                entries.extend(
                    (account, calendar_name or calendar_id, event)
                    for event in response.get("items", []) or []
                )
                # Best-effort look-back for travel that already ended; the
                # forward window above never returns it.
                try:
                    response = service.events().list(
                        calendarId=calendar_id,
                        timeMin=time_back.isoformat().replace("+00:00", "Z"),
                        timeMax=now.isoformat().replace("+00:00", "Z"),
                        singleEvents=True,
                        orderBy="startTime",
                        maxResults=MAX_LOOKBACK_EVENTS,
                    ).execute()
                except Exception as exc:
                    calendar_errors.append(
                        f"{token_path}: {calendar_name or calendar_id} (recent travel): {exc}"
                    )
                    continue
                recent_entries.extend(
                    (account, calendar_name or calendar_id, event)
                    for event in response.get("items", []) or []
                )
        except Exception as exc:
            fatal_errors.append(f"{token_path}: {exc}")

    for error in fatal_errors + calendar_errors:
        _eprint(f"town-calendar-scan: Calendar API failure: {error}")
    if fatal_errors:
        return 5, None
    return 0, _build_payload(
        entries, now, seen_accounts, recent_entries, _people_enricher(seen_accounts)
    )


def main() -> int:
    if _RUNTIME_ERROR:
        _eprint(f"town-calendar-scan: {_RUNTIME_ERROR}")
        return 3
    code, payload = _run()
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
