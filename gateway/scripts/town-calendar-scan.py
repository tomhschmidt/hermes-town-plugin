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
import json
import os
import re
import sys
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
HERMES_AGENT = HERMES_HOME / "hermes-agent"
HERMES_VENV_PYTHON = HERMES_AGENT / ".venv" / "bin" / "python"
if not HERMES_VENV_PYTHON.exists():
    HERMES_VENV_PYTHON = HERMES_AGENT / "venv" / "bin" / "python"

# Hermes and Google dependencies live in the Hermes venv. Re-exec once so a
# manual invocation matches cron execution.
if (
    HERMES_VENV_PYTHON.exists()
    and Path(sys.executable).resolve() != HERMES_VENV_PYTHON.resolve()
    and os.environ.get("HERMES_CALENDAR_SCAN_BOOTSTRAPPED") != "1"
):
    os.environ["HERMES_CALENDAR_SCAN_BOOTSTRAPPED"] = "1"
    os.execv(
        str(HERMES_VENV_PYTHON),
        [str(HERMES_VENV_PYTHON), __file__, *sys.argv[1:]],
    )

if HERMES_AGENT.exists():
    sys.path.insert(0, str(HERMES_AGENT))


CALENDAR_SCOPES = {
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.readonly",
}
MAX_EVENTS_PER_ACCOUNT = 20
SCAN_WINDOW = timedelta(hours=48)
BIRTHDAYS_CALENDAR_ID = "addressbook#contacts@group.v.calendar.google.com"
HOLIDAY_CALENDAR_SUFFIX = "#holiday@group.v.calendar.google.com"


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


def _parse_event_time(
    value: Any, default_timezone: timezone | ZoneInfo = timezone.utc
) -> tuple[datetime, bool] | None:
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
        return datetime.combine(parsed_date, datetime_time.min, tzinfo=default_timezone), True
    return None


def _relative_start(start: datetime, now: datetime, all_day: bool) -> str:
    if all_day:
        day_delta = (start.date() - now.astimezone(start.tzinfo).date()).days
        if day_delta <= 0:
            return "today"
        if day_delta == 1:
            return "tomorrow"
        return start.strftime("%a")

    seconds = (start - now).total_seconds()
    if seconds < 0:
        minutes = max(1, round(abs(seconds) / 60))
        return f"started {minutes}m ago"
    if seconds < 60 * 60:
        return f"in {max(1, round(seconds / 60))}m"
    if seconds < 24 * 60 * 60:
        return f"in {max(1, round(seconds / 3600))}h"
    return f"{start.strftime('%a')} at {start.strftime('%-I:%M %p')}"


def _compact(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _event_details(event: dict[str, Any]) -> str:
    parts: list[str] = []
    location = _compact(event.get("location"), 120)
    if location:
        parts.append(location)

    attendees = event.get("attendees")
    if isinstance(attendees, list):
        count = len([attendee for attendee in attendees if isinstance(attendee, dict)])
        if count:
            parts.append(f"{count} attendee" if count == 1 else f"{count} attendees")

    description = str(event.get("description") or "")
    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
    if first_line:
        parts.append(_compact(first_line, 240))
    return " · ".join(parts) or "No additional event details."


def _candidate(
    event: dict[str, Any], account: str, start: datetime, now: datetime, all_day: bool
) -> dict[str, Any] | None:
    event_id = str(event.get("id") or "").strip()
    if not event_id:
        return None
    title = _compact(event.get("summary"), 160) or "(untitled event)"
    start_iso = start.isoformat()
    canonical = "\n".join((account.casefold(), event_id, start_iso))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return {
        "key": digest[:16],
        "content_hash": digest,
        "source": f"calendar:{account}:{event_id}",
        "headline": f"{title} — {_relative_start(start, now, all_day)}",
        "summary": _event_details(event),
        "created_at": start_iso,
        "created_at_ts": start.timestamp(),
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
    candidates: list[dict[str, Any]] = []
    upcoming: list[tuple[datetime, dict[str, Any]]] = []
    seen_accounts: set[str] = set()
    seen_event_ids: set[str] = set()
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
                    try:
                        calendar_timezone: timezone | ZoneInfo = ZoneInfo(
                            str(
                                calendar.get("timeZone")
                                or primary.get("timeZone")
                                or "UTC"
                            )
                        )
                    except ZoneInfoNotFoundError:
                        calendar_timezone = timezone.utc

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
                for event in response.get("items", []) or []:
                    if not isinstance(event, dict) or event.get("status") == "cancelled":
                        continue
                    event_id = str(event.get("id") or "").strip()
                    if not event_id or event_id in seen_event_ids:
                        continue
                    parsed_start = _parse_event_time(event.get("start"), calendar_timezone)
                    parsed_end = _parse_event_time(event.get("end"), calendar_timezone)
                    if parsed_start is None or parsed_end is None:
                        continue
                    start, all_day = parsed_start
                    end, _ = parsed_end
                    if end <= now:
                        continue
                    seen_event_ids.add(event_id)
                    candidate = _candidate(event, account, start, now, all_day)
                    if candidate:
                        candidates.append(candidate)
                    if start >= now:
                        upcoming.append(
                            (
                                start,
                                {
                                    "summary": _compact(event.get("summary"), 160)
                                    or "(untitled event)",
                                    "start_iso": start.isoformat(),
                                    "end_iso": end.isoformat(),
                                    "all_day": all_day,
                                    "account": account,
                                    "calendar": calendar_name or calendar_id,
                                },
                            )
                        )
        except Exception as exc:
            fatal_errors.append(f"{token_path}: {exc}")

    for error in fatal_errors + calendar_errors:
        _eprint(f"town-calendar-scan: Calendar API failure: {error}")
    if fatal_errors:
        return 5, None
    if not upcoming and not candidates:
        return 0, None

    candidates.sort(key=lambda candidate: float(candidate["created_at_ts"]))
    next_event = min(upcoming, key=lambda row: row[0])[1] if upcoming else None
    if next_event:
        next_event = {
            "summary": next_event["summary"],
            "start_iso": next_event["start_iso"],
            "account": next_event["account"],
        }
    events = [event for _, event in sorted(upcoming, key=lambda row: row[0])[:20]]
    return 0, {"candidates": candidates, "next_event": next_event, "events": events}


def main() -> int:
    code, payload = _run()
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
