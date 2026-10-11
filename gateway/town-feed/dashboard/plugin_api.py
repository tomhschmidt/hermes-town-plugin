"""Namespaced REST API and curator for the Hermes Town Need-to-Know feed.

Hermes mounts ``router`` at ``/api/plugins/town-feed``. Persistence lives in
``~/.hermes/plugins/town-feed/feed-state.json`` after installation.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from hermes_constants import get_hermes_home


router = APIRouter()

STATE_VERSION = 6
MAX_FEED_ITEMS = 10
# Retention exceeds display so aging actionable items survive fresh noise
# and stay available for suggestion re-raises (Town keeps deep thread memory).
MAX_RETAINED_ITEMS = 30
MAX_TOWN_SCAN_CANDIDATES = 100
MAX_DECISIONS = 500
RECENT_SECONDS = 14 * 24 * 60 * 60
SESSION_LIMIT = 24
MAX_CANDIDATES_FOR_LLM = 40
MAX_SUGGESTIONS = 5
MAX_SCHEDULE_EVENTS = 20
SCHEDULE_STALE_SECONDS = 3 * 60 * 60
MAX_ACTION_SIGNALS = 8
MAX_AWAITING_REPLY_SIGNALS = 10
MAX_OPEN_LOOP_SIGNALS = 15
MAX_OPEN_LOOP_SUPPRESSIONS = 100
MAX_ORDER_SIGNALS = 6
MAX_EVENT_SIGNALS = 8
MAX_CHAT_AWAITING_SIGNALS = 5
MAX_KNOWN_CONTACTS = 2000
DAY_BRIEF_MAX_CALLOUTS = 4
DAY_BRIEF_STALE_SECONDS = 3 * 60 * 60
DAY_BRIEF_REFRESH_SECONDS = 60 * 60
DAY_BRIEF_OPENER_CHARS = 140
SUGGESTION_KINDS = {"followup", "digest", "maintenance"}
DAY_BRIEF_CALLOUT_KINDS = {"conflict", "travel", "timezone", "prep", "deadline", "trip"}
BOARD_COLUMNS = (
    "triage",
    "todo",
    "scheduled",
    "ready",
    "running",
    "blocked",
    "review",
    "done",
)

_THREAD_LOCK = threading.RLock()
_ERROR_RE = re.compile(
    r"\b(error|failed|failure|exception|traceback|blocked|timeout|denied|broken)\b",
    re.IGNORECASE,
)
_ACTION_RE = re.compile(
    r"\b(action|required|needs?|follow[ -]?up|waiting|todo|fix|review|decide)\b",
    re.IGNORECASE,
)
_DEADLINE_RE = re.compile(
    r"\b(today|tomorrow|tonight|deadline|due|overdue|by\s+(?:mon|tue|wed|thu|fri|sat|sun)|"
    r"\d{4}-\d{2}-\d{2}|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{1,2})\b",
    re.IGNORECASE,
)
_SCHEDULER_PREAMBLE_RE = re.compile(
    r"\A[ \t]*\[IMPORTANT:\s*You are running as a scheduled cron job\..*?\]"
    r"(?:[ \t]*(?:\r?\n){2}|[ \t]*\Z)",
    re.IGNORECASE | re.DOTALL,
)
_SESSION_REF_RE = re.compile(r"(?<![\w-])session:([A-Za-z0-9][A-Za-z0-9_-]*)", re.IGNORECASE)
_AMOUNT_RE = re.compile(
    r"(?<!\w)(?:[$€£]\s?\d+(?:,\d{3})*(?:\.\d{2})?|"
    r"(?:USD|EUR|GBP)\s+\d+(?:,\d{3})*(?:\.\d{2})?)\b",
    re.IGNORECASE,
)

_TOWN_LABEL_SCORE_ADJUSTMENTS = {
    "action needed": 34.0,
    "new sender": 18.0,
    "awaiting reply": 10.0,
    "orders": -30.0,
    "travel": -30.0,
    "updates": -30.0,
}
_TOWN_LABEL_NTK_EXCLUSIONS = {"newsletters", "promotional"}


class ItemRequest(BaseModel):
    id: str


class TaskRequest(BaseModel):
    title: str
    prompt: str
    session_id: Optional[str] = None
    client_key: str


def _plugin_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _state_path() -> Path:
    return _plugin_root() / "feed-state.json"


def _lock_path() -> Path:
    return _plugin_root() / ".feed-state.lock"


_OPERATOR_PROFILE_MODULE = "hermes_town_operator_profile"


def _operator_profile_module() -> Optional[Any]:
    """The sibling operator_profile.py, loaded by path like this module.

    ``None`` when it cannot load: curation then runs without the profile's
    self/assistant rules instead of failing.
    """
    cached = sys.modules.get(_OPERATOR_PROFILE_MODULE)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("operator_profile.py")
    try:
        spec = importlib.util.spec_from_file_location(_OPERATOR_PROFILE_MODULE, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_OPERATOR_PROFILE_MODULE] = module
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_OPERATOR_PROFILE_MODULE, None)
        return None
    return module


def _load_profile(accounts: Iterable[str] = ()) -> dict[str, Any]:
    module = _operator_profile_module()
    if module is None:
        return {"self_addresses": [], "assistant_senders": []}
    return module.load_operator_profile(get_hermes_home(), account_addresses=accounts)


_WHERE_MODULE = "hermes_town_where"


def _where_module() -> Optional[Any]:
    """The sibling where.py (S1 location + meaningful-event rules), by path.

    ``None`` when it cannot load: the feed then omits ``where`` and treats
    every timed event as meaningful instead of failing.
    """
    cached = sys.modules.get(_WHERE_MODULE)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("where.py")
    try:
        spec = importlib.util.spec_from_file_location(_WHERE_MODULE, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_WHERE_MODULE] = module
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_WHERE_MODULE, None)
        return None
    return module


def _empty_state() -> dict[str, Any]:
    return {
        "version": STATE_VERSION,
        "items": [],
        "decisions": {},
        "source_state": {"memory_hashes": {}},
        "last_curated_at": None,
        "ranking_mode": None,
        "last_curate_errors": [],
        "suggestions": [],
        "suggestions_generated_at": None,
        "label_stats": {},
        "last_labeling_summary": None,
        "next_event": None,
        "schedule": {"events": [], "generated_at": None, "context": None, "recent_travel": []},
        "day_brief": None,
        "open_loop_suppressed": [],
    }


@contextlib.contextmanager
def _process_lock() -> Iterator[None]:
    """Serialize state mutations across the web process and cron processes."""
    _plugin_root().mkdir(parents=True, exist_ok=True)
    with _THREAD_LOCK:
        with _lock_path().open("a+", encoding="utf-8") as lock_file:
            try:
                import fcntl
            except ImportError:  # pragma: no cover - Hermes gateway targets are POSIX
                yield
                return
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_state_unlocked() -> dict[str, Any]:
    path = _state_path()
    if not path.exists():
        return _empty_state()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuntimeError(f"invalid Town Feed state in {path}: expected an object")
    state = _empty_state()
    state.update(raw)
    state["version"] = STATE_VERSION
    raw_items = state.get("items")
    state["items"] = []
    if isinstance(raw_items, list):
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            item = dict(raw_item)
            source_activity = _parse_timestamp(
                item.get("source_activity_at"),
                _parse_timestamp(item.get("created_at")),
            )
            if source_activity:
                source_activity_at = _iso_timestamp(source_activity)
                item["source_activity_at"] = source_activity_at
                item.setdefault("created_at", source_activity_at)
            state["items"].append(item)
    if not isinstance(state.get("decisions"), dict):
        state["decisions"] = {}
    raw_suppressed = state.get("open_loop_suppressed")
    state["open_loop_suppressed"] = []
    if isinstance(raw_suppressed, list):
        seen_suppressed: set[str] = set()
        for raw_key in raw_suppressed:
            if not isinstance(raw_key, str):
                continue
            key = raw_key.strip()
            if not key or key in seen_suppressed:
                continue
            state["open_loop_suppressed"].append(key)
            seen_suppressed.add(key)
        state["open_loop_suppressed"] = state["open_loop_suppressed"][
            -MAX_OPEN_LOOP_SUPPRESSIONS:
        ]
    for dropped_key in (
        "inbox_candidates",
        "labeling_log",
        "known_correspondents",
        "awaiting_reply_threads",
    ):
        state.pop(dropped_key, None)
    if not isinstance(state.get("source_state"), dict):
        state["source_state"] = {"memory_hashes": {}}
    else:
        state["source_state"] = dict(state["source_state"])
        if not isinstance(state["source_state"].get("memory_hashes"), dict):
            state["source_state"]["memory_hashes"] = {}
        state["source_state"].pop("gmail", None)
        state["source_state"].pop("gmail_sent", None)
        cron_consumed = _merge_cron_consumed(state["source_state"].get("cron_consumed"), {})
        if cron_consumed:
            state["source_state"]["cron_consumed"] = cron_consumed
        else:
            state["source_state"].pop("cron_consumed", None)
    if not isinstance(state.get("last_curate_errors"), list):
        state["last_curate_errors"] = []
    else:
        state["last_curate_errors"] = [
            str(error) for error in state["last_curate_errors"] if str(error).strip()
        ][:20]
    state["suggestions"] = _normalize_suggestions(state.get("suggestions"))
    suggestions_generated_at = _parse_timestamp(state.get("suggestions_generated_at"))
    state["suggestions_generated_at"] = (
        _iso_timestamp(suggestions_generated_at) if suggestions_generated_at else None
    )
    state["label_stats"] = _normalize_label_stats(state.get("label_stats"))
    if not isinstance(state.get("last_labeling_summary"), dict):
        state["last_labeling_summary"] = None
    else:
        state["last_labeling_summary"] = dict(state["last_labeling_summary"])
    state["next_event"] = _normalize_next_event(state.get("next_event"))
    raw_schedule = state.get("schedule")
    if not isinstance(raw_schedule, dict):
        raw_schedule = {}
    schedule_generated_at = _parse_timestamp(raw_schedule.get("generated_at"))
    raw_context = raw_schedule.get("context")
    state["schedule"] = {
        "events": _normalize_schedule_events(raw_schedule.get("events")),
        "generated_at": (
            _iso_timestamp(schedule_generated_at) if schedule_generated_at else None
        ),
        "context": dict(raw_context) if isinstance(raw_context, dict) else None,
        "recent_travel": _normalize_schedule_events(raw_schedule.get("recent_travel")),
    }
    state["day_brief"] = _normalize_day_brief(state.get("day_brief"))
    return state


def _write_state_unlocked(state: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".feed-state-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _parse_timestamp(value: Any, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str) or not value.strip():
        return default
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except ValueError:
        return default


def _iso_timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _normalize_next_event(value: Any) -> Optional[dict[str, str]]:
    if not isinstance(value, dict):
        return None
    summary = str(value.get("summary") or "").strip()
    start_iso = str(value.get("start_iso") or "").strip()
    account = str(value.get("account") or "").strip()
    start_timestamp = _parse_timestamp(start_iso)
    if not summary or not start_timestamp or not account:
        return None
    normalized = {
        "summary": summary,
        "start_iso": _iso_timestamp(start_timestamp),
        "account": account,
    }
    location = str(value.get("location") or "").strip()
    if location:
        normalized["location"] = _compact(location, 240)
    return normalized


def _normalize_event_attendees(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    attendees: list[dict[str, Any]] = []
    for raw in value[:8]:
        if not isinstance(raw, dict):
            continue
        email = str(raw.get("email") or "").strip().casefold()
        name = str(raw.get("name") or "").strip()
        if not email and not name:
            continue
        entry: dict[str, Any] = {}
        if email:
            entry["email"] = email[:120]
        if name:
            entry["name"] = _compact(name, 80)
        response = str(raw.get("response") or "").strip()
        if response:
            entry["response"] = response[:24]
        if raw.get("self") is True:
            entry["self"] = True
        # S2: directory facts from the calendar scan's people enrichment.
        org = str(raw.get("org") or "").strip()
        if org:
            entry["org"] = _compact(org, 80)
        if raw.get("relationship") in _PEOPLE_RELATIONSHIPS:
            entry["relationship"] = raw["relationship"]
        last_contact = raw.get("last_contact_ts")
        if isinstance(last_contact, (int, float)) and not isinstance(last_contact, bool):
            if math.isfinite(last_contact) and last_contact > 0:
                entry["last_contact_ts"] = float(last_contact)
        attendees.append(entry)
    return attendees


def _normalize_schedule_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    events: list[dict[str, Any]] = []
    for raw_event in value:
        if not isinstance(raw_event, dict):
            continue
        summary = str(raw_event.get("summary") or "").strip()
        start_timestamp = _parse_timestamp(raw_event.get("start_iso"))
        end_timestamp = _parse_timestamp(raw_event.get("end_iso"))
        all_day = raw_event.get("all_day")
        account = str(raw_event.get("account") or "").strip()
        if (
            not summary
            or not start_timestamp
            or not end_timestamp
            or not isinstance(all_day, bool)
            or not account
        ):
            continue
        event = {
            "summary": summary,
            "start_iso": _iso_timestamp(start_timestamp),
            "end_iso": _iso_timestamp(end_timestamp),
            "all_day": all_day,
            "account": account,
        }
        location = str(raw_event.get("location") or "").strip()
        if location:
            event["location"] = _compact(location, 240)
        attendees = _normalize_event_attendees(raw_event.get("attendees"))
        if attendees:
            event["attendees"] = attendees
            try:
                event["attendee_count"] = max(
                    len(attendees), int(raw_event.get("attendee_count") or 0)
                )
            except (TypeError, ValueError):
                event["attendee_count"] = len(attendees)
        description = str(raw_event.get("description_excerpt") or "").strip()
        if description:
            event["description_excerpt"] = _compact(description, 500)
        organizer = str(raw_event.get("organizer") or "").strip()
        if organizer:
            event["organizer"] = _compact(organizer, 120)
        conference = str(raw_event.get("conference") or "").strip()
        if conference:
            event["conference"] = conference[:40]
        event_tz = str(raw_event.get("event_tz") or "").strip()
        if event_tz:
            event["event_tz"] = event_tz[:64]
        end_tz = str(raw_event.get("end_tz") or "").strip()
        if end_tz:
            event["end_tz"] = end_tz[:64]
        if raw_event.get("multi_day") is True:
            event["multi_day"] = True
        if raw_event.get("lodging_hint") is True:
            event["lodging_hint"] = True
        event_type = str(raw_event.get("event_type") or "").strip()
        if event_type:
            event["event_type"] = event_type[:32]
        working_location = raw_event.get("working_location")
        if isinstance(working_location, dict) and working_location.get("type") in {
            "home",
            "office",
            "custom",
        }:
            event["working_location"] = {"type": working_location["type"]}
            label = str(working_location.get("label") or "").strip()
            if label:
                event["working_location"]["label"] = _compact(label, 120)
        ical_uid = str(raw_event.get("ical_uid") or "").strip()
        if ical_uid:
            event["ical_uid"] = ical_uid[:255]
        event["event_key"] = _event_key(event)
        events.append(event)
        if len(events) >= MAX_SCHEDULE_EVENTS:
            break
    return events


def _normalize_day_brief(value: Any) -> Optional[dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    day_summary = _compact(str(value.get("day_summary") or ""), 220)
    if not day_summary:
        return None
    callouts: list[dict[str, str]] = []
    raw_callouts = value.get("callouts")
    if isinstance(raw_callouts, list):
        for raw in raw_callouts[:DAY_BRIEF_MAX_CALLOUTS]:
            if not isinstance(raw, dict):
                continue
            kind = str(raw.get("kind") or "").strip().casefold()
            text = _compact(str(raw.get("text") or ""), 240)
            if kind in DAY_BRIEF_CALLOUT_KINDS and text:
                callouts.append({"kind": kind, "text": text})
    trip: Optional[dict[str, Any]] = None
    raw_trip = value.get("trip")
    if isinstance(raw_trip, dict):
        label = _compact(str(raw_trip.get("label") or ""), 40)
        if label:
            trip = {"label": label}
            for field in ("day_n", "total_days"):
                try:
                    number = int(raw_trip.get(field))
                except (TypeError, ValueError):
                    continue
                if number >= 1:
                    trip[field] = number
    generated_at = _parse_timestamp(value.get("generated_at"))
    normalized: dict[str, Any] = {
        "day_summary": day_summary,
        "callouts": callouts,
        "trip": trip,
        "generated_at": _iso_timestamp(generated_at) if generated_at else None,
    }
    # S1: the where-grounded first line; briefs from before S1 have none.
    opener = _bounded_compact(str(value.get("opener") or ""), DAY_BRIEF_OPENER_CHARS)
    if opener:
        normalized["opener"] = opener
    for field in ("input_hash", "local_date"):
        text = str(value.get(field) or "").strip()
        if text:
            normalized[field] = text
    # S2: meeting prep (absent from briefs written before S2).
    prep = _normalize_day_brief_prep(value.get("prep"))
    if prep:
        normalized["prep"] = prep
    return normalized


def _source_activity_timestamp(item: dict[str, Any], default: float = 0.0) -> float:
    return _parse_timestamp(
        item.get("source_activity_at"),
        _parse_timestamp(
            item.get("created_at"),
            _parse_timestamp(item.get("created_at_ts"), default),
        ),
    )


def _is_expired(item: dict[str, Any], now: float) -> bool:
    source_activity = _source_activity_timestamp(item)
    return bool(source_activity and now - source_activity > RECENT_SECONDS)


def _normalize_label_stats(value: Any) -> dict[str, dict[str, int]]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, dict[str, int]] = {}
    for raw_account, raw_counts in value.items():
        account = str(raw_account or "").strip().casefold()
        if not account or not isinstance(raw_counts, dict):
            continue
        counts: dict[str, int] = {}
        for raw_label, raw_count in raw_counts.items():
            label = str(raw_label or "").strip()
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                continue
            if label and count >= 0:
                counts[label] = count
        if counts:
            normalized[account] = counts
    return normalized


def _plain_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = [_plain_text(part) for part in value]
        return " ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in ("text", "content", "summary", "message", "output"):
            if key in value:
                text = _plain_text(value[key])
                if text:
                    return text
    return ""


def _compact(text: str, limit: int = 360) -> str:
    compacted = " ".join((text or "").split())
    if len(compacted) <= limit:
        return compacted
    return compacted[: limit - 1].rstrip() + "..."


def _bounded_compact(text: str, limit: int) -> str:
    compacted = " ".join((text or "").split())
    if len(compacted) <= limit:
        return compacted
    if limit <= 3:
        return compacted[:limit]
    return compacted[: limit - 3].rstrip() + "..."


def _strip_scheduler_preamble(text: str) -> str:
    return _SCHEDULER_PREAMBLE_RE.sub("", text or "", count=1).lstrip()


def _references_curator(text: str) -> bool:
    folded = (text or "").casefold()
    return "town_feed_curate" in folded or "town feed curator" in folded


def _headline(text: str) -> str:
    cleaned = _strip_scheduler_preamble(text)
    if cleaned.casefold().startswith("[important"):
        cleaned = cleaned.partition("]")[2].lstrip()
    return _compact(cleaned, 140)


def _content_hash(source: str, headline: str, summary: str) -> str:
    canonical = "\n".join(
        (source.split(":", 1)[0].lower(), headline.strip().lower(), summary.strip().lower())
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _candidate(
    *, source: str, headline: str, summary: str, created_at: float
) -> Optional[dict[str, Any]]:
    headline = _headline(headline)
    summary = _compact(_strip_scheduler_preamble(summary), 420)
    if not headline or not summary:
        return None
    digest = _content_hash(source, headline, summary)
    return {
        "key": digest[:16],
        "content_hash": digest,
        "source": source,
        "headline": headline,
        "summary": summary,
        "created_at_ts": created_at or time.time(),
    }


# Interrupt/error placeholders the agent appends as a closing assistant row;
# the session's real outcome is the substantive message before them.
_SESSION_STUB_RE = re.compile(
    r"\A\s*(?:operation interrupted\b|interrupted\b|error\s*:|request timed out\b|"
    r"api call failed\b)",
    re.IGNORECASE,
)


def _scan_sessions(now: float) -> tuple[list[dict[str, Any]], list[str]]:
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        from hermes_state import SessionDB

        db = SessionDB()
        try:
            sessions = db.list_sessions_rich(
                limit=SESSION_LIMIT,
                include_children=False,
                order_by_last_active=True,
                project_compression_tips=True,
                compact_rows=True,
            )
            session_candidates: list[dict[str, Any]] = []
            for meta in sessions:
                created = _parse_timestamp(meta.get("last_active"), _parse_timestamp(meta.get("started_at")))
                if created and now - created > RECENT_SECONDS:
                    continue
                session_id = str(meta.get("id") or "")
                # Cron/subagent/tool run sessions are machinery, not user
                # activity — Recents' cron lane and run history own that
                # surface (same exclusion set as the host sidebar). The id
                # prefix covers listers that omit a source field.
                meta_source = str(meta.get("source") or "").strip().casefold()
                if meta_source in {"cron", "subagent", "tool"} or session_id.startswith("cron_"):
                    continue
                title = str(meta.get("title") or meta.get("preview") or "Recent Hermes session")
                first_prompt = str(meta.get("preview") or "")
                if _references_curator(title) or _references_curator(first_prompt):
                    continue
                summary = ""
                if session_id:
                    try:
                        messages = db.get_messages(session_id)
                        first_prompt = next(
                            (
                                _plain_text(message.get("content"))
                                for message in messages
                                if str(message.get("role") or "").lower() == "user"
                            ),
                            first_prompt,
                        )
                        if _references_curator(first_prompt):
                            continue
                        for message in reversed(messages):
                            if str(message.get("role") or "").lower() == "assistant":
                                candidate_text = _plain_text(message.get("content"))
                                if candidate_text and not _SESSION_STUB_RE.match(candidate_text):
                                    summary = candidate_text
                                    break
                    except Exception as exc:
                        errors.append(f"session {session_id}: {exc}")
                normalized_prompt = " ".join(re.findall(r"\w+", first_prompt.casefold()))
                normalized_summary = " ".join(re.findall(r"\w+", summary.casefold()))
                if (
                    len(normalized_summary) < 40
                    or len(normalized_summary.split()) < 6
                    or normalized_summary == normalized_prompt
                    or (
                        normalized_prompt
                        and normalized_prompt in normalized_summary
                        and len(normalized_summary) <= len(normalized_prompt) + 40
                    )
                ):
                    continue
                item = _candidate(
                    source=f"session:{session_id or 'unknown'}",
                    headline=_strip_scheduler_preamble(title),
                    summary=_strip_scheduler_preamble(summary),
                    created_at=created or now,
                )
                if item:
                    session_candidates.append(item)
            seen_headlines: set[str] = set()
            for item in sorted(
                session_candidates,
                key=lambda candidate: float(candidate["created_at_ts"]),
                reverse=True,
            ):
                normalized_headline = " ".join(
                    re.findall(r"\w+", str(item["headline"]).casefold())
                )
                if not normalized_headline or normalized_headline in seen_headlines:
                    continue
                seen_headlines.add(normalized_headline)
                candidates.append(item)
        finally:
            close = getattr(db, "close", None)
            if close:
                close()
    except Exception as exc:
        errors.append(f"sessions: {exc}")
    return candidates, errors


def _latest_cron_output_time(output_root: Path, job_id: str) -> Optional[float]:
    job_dir = output_root / job_id
    try:
        latest = max(
            (path.stat().st_mtime for path in job_dir.glob("*.md") if path.is_file()),
            default=None,
        )
    except OSError:
        return None
    return float(latest) if latest else None


# Scanners run every 10 min, the curator every 30: every output doc a job
# wrote since the last WRITTEN curation is read (oldest first), bounded to the
# newest docs, and to recent ones when a job has no consumption watermark.
MAX_CRON_DOCS_PER_JOB = 12
CRON_BACKFILL_SECONDS = 6 * 60 * 60


def _cron_output_docs(output_root: Path, job_id: str) -> list[tuple[int, Path]]:
    """A job's output docs as ``(mtime_ns, path)``, oldest first."""
    docs: list[tuple[int, Path]] = []
    try:
        paths = list((output_root / job_id).glob("*.md"))
    except OSError:
        return docs
    for path in paths:
        try:
            if path.is_file():
                docs.append((path.stat().st_mtime_ns, path))
        except OSError:
            continue
    docs.sort(key=lambda doc: (doc[0], doc[1].name))
    return docs


def _read_cron_doc(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _unconsumed_cron_docs(
    docs: list[tuple[int, Path]], watermark: Optional[int], now: float
) -> list[tuple[int, Path]]:
    if watermark is None:
        cutoff_ns = int((now - CRON_BACKFILL_SECONDS) * 1_000_000_000)
        unread = [doc for doc in docs if doc[0] >= cutoff_ns]
    else:
        unread = [doc for doc in docs if doc[0] > watermark]
    return unread[-MAX_CRON_DOCS_PER_JOB:]


def _merge_cron_consumed(current: Any, update: dict[str, int]) -> dict[str, int]:
    """Per-job consumption watermarks (newest consumed doc mtime_ns); never regress."""
    merged: dict[str, int] = {}
    for source in (current, update):
        if not isinstance(source, dict):
            continue
        for raw_job_id, raw_mtime in source.items():
            job_id = str(raw_job_id or "").strip()
            if not job_id or isinstance(raw_mtime, bool):
                continue
            try:
                mtime_ns = int(raw_mtime)
            except (TypeError, ValueError):
                continue
            if mtime_ns > 0:
                merged[job_id] = max(merged.get(job_id, 0), mtime_ns)
    return merged


def _newest_doc_with(docs: list[tuple[str, Optional[dict[str, Any]]]], key: str) -> str:
    """The newest doc text whose town-scan payload carries ``key`` ("" if none)."""
    for text, payload in docs:
        if payload is not None and key in payload:
            return text
    return ""


def _town_scan_payload(output: str) -> Optional[dict[str, Any]]:
    """Extract the town-scan JSON object from a cron output document.

    Cron wraps script stdout in a markdown header ("# Cron Job: ...\n---\n");
    tolerate that by scanning for the first parseable JSON object line, then
    falling back to a brace-slice of the whole document.
    """
    text = (output or "").strip()
    if not text:
        return None
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return payload if isinstance(payload, dict) else None


def _town_scan_candidates(output: str) -> Optional[list[dict[str, Any]]]:
    """Return normalized candidates for a structured town-scan output.

    ``None`` means the output is not the town-scan contract. An empty list is
    a valid, quiet scan and must not be headline-ized as generic cron output.
    """
    payload = _town_scan_payload(output)
    if payload is None or not isinstance(payload.get("candidates"), list):
        return None

    candidates: list[dict[str, Any]] = []
    for raw in payload["candidates"][:MAX_TOWN_SCAN_CANDIDATES]:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        digest = str(candidate.get("content_hash") or "").strip()
        source = str(candidate.get("source") or "").strip()
        headline = str(candidate.get("headline") or "").strip()
        summary = str(candidate.get("summary") or "").strip()
        created_at = _source_activity_timestamp(candidate)
        if not digest or not source or not headline or not summary or not created_at:
            continue
        candidate.update(
            {
                "key": digest[:16],
                "content_hash": digest,
                "source": source,
                "headline": headline,
                "summary": summary,
                "created_at_ts": created_at,
            }
        )
        if "town_label" in candidate:
            label = candidate.get("town_label")
            candidate["town_label"] = (
                str(label).strip().casefold() if isinstance(label, str) and label.strip() else None
            )
        if "town_label_confidence" in candidate:
            try:
                confidence = float(candidate["town_label_confidence"])
            except (TypeError, ValueError):
                confidence = None
            candidate["town_label_confidence"] = (
                max(0.0, min(1.0, confidence))
                if confidence is not None and math.isfinite(confidence)
                else None
            )
        if "body" in candidate:
            candidate["body"] = _bounded_compact(str(candidate.get("body") or ""), 1500)
        for field in ("account", "thread_id"):
            if field in candidate:
                candidate[field] = str(candidate.get(field) or "").strip()
        candidates.append(candidate)
    return candidates


def _town_scan_awaiting_reply(output: str) -> Optional[list[dict[str, Any]]]:
    payload = _town_scan_payload(output)
    if payload is None or "awaiting_reply" not in payload:
        return None
    if not isinstance(payload.get("awaiting_reply"), list):
        return []

    entries: list[dict[str, Any]] = []
    for raw in payload["awaiting_reply"]:
        if not isinstance(raw, dict):
            continue
        account = str(raw.get("account") or "").strip().casefold()
        thread_id = str(raw.get("thread_id") or "").strip()
        recipient = str(raw.get("to") or "").strip()
        subject = str(raw.get("subject") or "").strip()
        last_sent_ts = _parse_timestamp(raw.get("last_sent_ts"))
        if not account or not thread_id or not recipient or not subject or not last_sent_ts:
            continue
        entries.append(
            {
                "account": account,
                "thread_id": thread_id,
                "to": _compact(recipient, 120),
                "subject": _compact(subject, 180),
                "last_sent_ts": last_sent_ts,
            }
        )
    entries.sort(key=lambda entry: float(entry["last_sent_ts"]), reverse=True)
    return entries[:MAX_AWAITING_REPLY_SIGNALS]


def _town_scan_open_loops(output: str) -> Optional[list[dict[str, Any]]]:
    payload = _town_scan_payload(output)
    if payload is None or "open_loops" not in payload:
        return None
    if not isinstance(payload.get("open_loops"), list):
        return []

    entries: list[dict[str, Any]] = []
    for raw in payload["open_loops"]:
        if not isinstance(raw, dict):
            continue
        account = str(raw.get("account") or "").strip().casefold()
        thread_id = str(raw.get("thread_id") or "").strip()
        person = str(raw.get("person") or "").strip()
        subject = str(raw.get("subject") or "").strip()
        ask = str(raw.get("ask") or "").strip()
        first_seen_ts = _parse_timestamp(raw.get("first_seen_ts"))
        last_seen_ts = _parse_timestamp(raw.get("last_seen_ts"))
        deadline_raw = raw.get("deadline_iso")
        deadline_ts = _parse_timestamp(deadline_raw)
        if (
            not account
            or not thread_id
            or not person
            or not subject
            or not ask
            or not first_seen_ts
            or not last_seen_ts
        ):
            continue
        entry = {
            "account": account,
            "thread_id": thread_id,
            "person": _compact(person, 120),
            "subject": _compact(subject, 180),
            "ask": _bounded_compact(ask, 140),
            "deadline_iso": _iso_timestamp(deadline_ts) if deadline_ts else None,
            "first_seen_ts": first_seen_ts,
            "last_seen_ts": last_seen_ts,
        }
        if raw.get("bulk_sender") is True:
            entry["bulk_sender"] = True
        from_address = str(raw.get("from_address") or "").strip().casefold()
        if from_address:
            entry["from_address"] = from_address
        entries.append(entry)
    entries.sort(
        key=lambda entry: (float(entry["last_seen_ts"]), float(entry["first_seen_ts"])),
        reverse=True,
    )
    return entries[:MAX_OPEN_LOOP_SIGNALS]


def _town_scan_has_enriched_contract(output: str) -> bool:
    payload = _town_scan_payload(output)
    if payload is None:
        return False
    if "awaiting_reply" in payload:
        return True
    if "open_loops" in payload:
        return True
    raw_candidates = payload.get("candidates")
    return isinstance(raw_candidates, list) and any(
        isinstance(candidate, dict)
        and ("town_label" in candidate or "town_label_confidence" in candidate)
        for candidate in raw_candidates
    )


def _town_scan_next_event(output: str) -> Optional[dict[str, str]]:
    payload = _town_scan_payload(output)
    if payload is None or "next_event" not in payload:
        return None
    return _normalize_next_event(payload.get("next_event"))


def _town_scan_known_contacts(output: str) -> Optional[set[str]]:
    payload = _town_scan_payload(output)
    if payload is None or "known_contacts" not in payload:
        return None
    raw = payload.get("known_contacts")
    if not isinstance(raw, list):
        return set()
    contacts: set[str] = set()
    for value in raw[:MAX_KNOWN_CONTACTS]:
        address = str(value or "").strip().casefold()
        if address and "@" in address:
            contacts.add(address)
    return contacts


def _town_scan_schedule_events(output: str) -> Optional[list[dict[str, Any]]]:
    payload = _town_scan_payload(output)
    if payload is None or "events" not in payload:
        return None
    return _normalize_schedule_events(payload.get("events"))


def _town_scan_recent_travel(output: str) -> Optional[list[dict[str, Any]]]:
    payload = _town_scan_payload(output)
    if payload is None or "recent_travel" not in payload:
        return None
    return _normalize_schedule_events(payload.get("recent_travel"))


_CRON_ERROR_NOISE = (
    re.compile(r"^Plugin \S.*?: unknown kind\b"),
    re.compile(r"^providers\.\S+?: unknown config keys\b"),
    re.compile(r"^Script exited with code -?\d+$"),
)
_CRON_SCRIPT_PREFIX = re.compile(r"^town-[\w.-]+:\s+")
MAX_CRON_DETAIL = 80


def _cap_cron_detail(detail: str) -> str:
    if len(detail) <= MAX_CRON_DETAIL:
        return detail
    # "context: wrapper: cause" chains: elide the wrappers nearest the cause
    # so both the failing step and the root error stay visible.
    parts = detail.split(": ")
    if len(parts) > 2:
        head = parts[:-1]
        while len(head) > 1:
            head = head[:-1]
            elided = ": ".join(head) + ": … " + parts[-1]
            if len(elided) <= MAX_CRON_DETAIL:
                return elided
    return detail[: MAX_CRON_DETAIL - 3].rstrip() + "..."


def _cron_failure_detail(error: Any, status: str) -> str:
    """The most specific line of a failed town-* script run, capped at 80 chars.

    Hermes stores a failed script as ``Script exited with code N`` followed by
    ``stderr:`` and ``stdout:`` sections. Plugin-loader warnings and the stdout
    JSON contract are noise; the script's own ``town-…:`` line names the real
    error, else the last remaining line does, else the job status.
    """
    sections: dict[str, list[str]] = {"": [], "stderr": [], "stdout": []}
    section = ""
    for raw_line in str(error or "").splitlines():
        line = " ".join(raw_line.split())
        if line in {"stderr:", "stdout:"}:
            section = line[:-1]
            continue
        if line and not any(pattern.match(line) for pattern in _CRON_ERROR_NOISE):
            sections[section].append(line)
    stdout = sections["stdout"]
    if stdout and stdout[0].startswith(("{", "[")):
        stdout = []
    stderr = sections[""] + sections["stderr"]
    lines = stderr + stdout
    detail = next(
        (
            _CRON_SCRIPT_PREFIX.sub("", line, count=1) or line
            for line in reversed(stderr)
            if line.startswith("town-")
        ),
        lines[-1] if lines else " ".join(str(status or "").split()),
    )
    return _cap_cron_detail(detail)


def _scan_cron(
    now: float,
    consumed: Optional[dict[str, int]] = None,
    profile: Optional[dict[str, Any]] = None,
    snapshot: Optional[dict[str, Any]] = None,
) -> tuple[
    list[dict[str, Any]],
    list[str],
    Optional[dict[str, str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    set[str],
    bool,
    dict[str, int],
]:
    """Collect cron candidates and the current snapshot signals.

    Candidates come from every output doc newer than the job's ``consumed``
    watermark (oldest first); the last element is the watermark update to
    persist once the curation is written. Snapshot fields (awaiting reply,
    open loops, known contacts, next event, schedule) come from the newest doc
    that carries each. When ``snapshot`` is a dict it also receives
    ``recent_travel`` (S1), so the tuple shape stays stable for callers.
    """
    consumed = _merge_cron_consumed(consumed, {})
    consumed_out: dict[str, int] = {}
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    next_events: list[dict[str, str]] = []
    schedule_events: list[dict[str, Any]] = []
    awaiting_reply: list[dict[str, Any]] = []
    open_loops: list[dict[str, Any]] = []
    known_contacts: set[str] = set()
    recent_travel: list[dict[str, Any]] = []
    has_enriched_contract = False
    try:
        from cron.jobs import get_cron_output_dir, list_jobs

        output_root = get_cron_output_dir()
        for job in list_jobs(include_disabled=True):
            name = str(job.get("name") or job.get("id") or "Cron job")
            prompt = str(job.get("prompt") or "")
            folded_name = name.casefold()
            if (
                folded_name in {"town feed curator", "town-feed-curate"}
                or "town_feed_curate" in prompt.casefold()
            ):
                continue
            status = str(job.get("last_status") or "never run")
            folded_status = status.casefold()
            is_town_failure = folded_name.startswith("town-") and folded_status in {
                "error",
                "failed",
            }
            last_run = _parse_timestamp(job.get("last_run_at"), _parse_timestamp(job.get("created_at"), now))
            if last_run and now - last_run > RECENT_SECONDS and not is_town_failure:
                continue
            job_id = str(job.get("id") or "unknown")
            if is_town_failure:
                detail = _cron_failure_detail(job.get("last_error"), status)
                health_item = _candidate(
                    source=f"cron:{job_id}",
                    headline=f"{name} failing — {detail}",
                    summary=f"Last status: {status}. Error: {detail}",
                    created_at=last_run or now,
                )
                if health_item:
                    candidates.append(health_item)

            # Silent-streak guard (2026-07-20 incident): an orphaned gateway
            # process ran town-calendar-scan "completed" for 16h with no
            # payload. Runs completing while the newest output doc goes stale
            # is invisible to the failure path above — surface it.
            if folded_name.startswith("town-") and not is_town_failure:
                doc_ts = _latest_cron_output_time(output_root, job_id)
                if (
                    last_run
                    and now - last_run < 45 * 60
                    and (doc_ts is None or last_run - doc_ts > 2 * 3600)
                ):
                    newest = _relative_age(doc_ts, now) if doc_ts else "never"
                    health_item = _candidate(
                        source=f"cron:{job_id}",
                        headline=f"{name} running but producing no data",
                        summary=(
                            f"Runs are completing but no output has been written "
                            f"(newest data: {newest}). A stale or orphaned gateway "
                            f"process may be executing this job — restart the "
                            f"gateway LaunchAgent and the desktop app."
                        ),
                        created_at=now,
                    )
                    if health_item:
                        candidates.append(health_item)

            docs = _cron_output_docs(output_root, job_id)
            watermark = consumed.get(job_id)
            unconsumed = _unconsumed_cron_docs(docs, watermark, now)
            texts = {path: _read_cron_doc(path) for _, path in unconsumed}
            for _, path in unconsumed:
                scan_candidates = _town_scan_candidates(texts[path])
                if scan_candidates is not None:
                    candidates.extend(scan_candidates)
            if unconsumed:
                consumed_out[job_id] = max(watermark or 0, unconsumed[-1][0])
            elif watermark is None and docs:
                consumed_out[job_id] = docs[-1][0]
            if docs and docs[-1][1] not in texts:
                texts[docs[-1][1]] = _read_cron_doc(docs[-1][1])
            snapshot_docs = [
                (texts[path], _town_scan_payload(texts[path]))
                for _, path in reversed(docs)
                if path in texts
            ]
            has_enriched_contract = has_enriched_contract or any(
                _town_scan_has_enriched_contract(text) for text, _ in snapshot_docs
            )
            scan_awaiting_reply = _town_scan_awaiting_reply(
                _newest_doc_with(snapshot_docs, "awaiting_reply")
            )
            if scan_awaiting_reply is not None:
                awaiting_reply.extend(scan_awaiting_reply)
            scan_open_loops = _town_scan_open_loops(_newest_doc_with(snapshot_docs, "open_loops"))
            if scan_open_loops is not None:
                open_loops.extend(scan_open_loops)
            scan_known_contacts = _town_scan_known_contacts(
                _newest_doc_with(snapshot_docs, "known_contacts")
            )
            if scan_known_contacts is not None:
                known_contacts.update(scan_known_contacts)
            next_event = _town_scan_next_event(_newest_doc_with(snapshot_docs, "next_event"))
            if next_event:
                next_events.append(next_event)
            scan_schedule_events = _town_scan_schedule_events(
                _newest_doc_with(snapshot_docs, "events")
            )
            if scan_schedule_events is not None:
                schedule_events.extend(scan_schedule_events)
            scan_recent_travel = _town_scan_recent_travel(
                _newest_doc_with(snapshot_docs, "recent_travel")
            )
            if scan_recent_travel is not None:
                recent_travel.extend(scan_recent_travel)
            if is_town_failure:
                continue
    except Exception as exc:
        errors.append(f"cron: {exc}")
    next_event = min(
        next_events,
        key=lambda event: _parse_timestamp(event["start_iso"]),
        default=None,
    )
    schedule_events.sort(key=lambda event: _parse_timestamp(event["start_iso"]))
    if snapshot is not None:
        recent_travel.sort(key=lambda event: _parse_timestamp(event["start_iso"]))
        snapshot["recent_travel"] = recent_travel[-MAX_SCHEDULE_EVENTS:]
    awaiting_by_thread: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in awaiting_reply:
        key = (str(entry["account"]), str(entry["thread_id"]))
        existing = awaiting_by_thread.get(key)
        if existing is None or float(entry["last_sent_ts"]) < float(existing["last_sent_ts"]):
            awaiting_by_thread[key] = entry
    awaiting_reply = _filter_awaiting_signals(list(awaiting_by_thread.values()), now, profile)
    open_loops_by_thread: dict[tuple[str, str], dict[str, Any]] = {}
    for entry in open_loops:
        key = (str(entry["account"]), str(entry["thread_id"]))
        existing = open_loops_by_thread.get(key)
        if existing is None or float(entry["last_seen_ts"]) > float(existing["last_seen_ts"]):
            open_loops_by_thread[key] = entry
    open_loops = _filter_open_loop_signals(list(open_loops_by_thread.values()), now, profile)
    return (
        candidates,
        errors,
        next_event,
        schedule_events[:MAX_SCHEDULE_EVENTS],
        awaiting_reply[:MAX_AWAITING_REPLY_SIGNALS],
        open_loops[:MAX_OPEN_LOOP_SIGNALS],
        known_contacts,
        has_enriched_contract,
        consumed_out,
    )


SIGNAL_MAX_AGE_SECONDS = 21 * 24 * 60 * 60


def _filter_awaiting_signals(
    entries: list[dict[str, Any]], now: float, profile: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Recent, real awaiting-reply threads, newest send first.

    Junk tracked before the R4 scanner rules (self-only mail, unsubscribe
    requests, FYI forwards) is left out here at read time; Gmail is untouched.
    """
    rules = _operator_profile_module()
    self_addresses = (
        rules.self_address_set(profile, [entry["account"] for entry in entries])
        if rules
        else set()
    )
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if now - float(entry["last_sent_ts"]) > SIGNAL_MAX_AGE_SECONDS:
            continue
        if rules and rules.awaiting_reply_junk_reason(
            rules.addresses(str(entry.get("to") or "")), entry.get("subject"), self_addresses
        ):
            continue
        kept.append(entry)
    kept.sort(key=lambda entry: float(entry["last_sent_ts"]), reverse=True)
    return kept


def _filter_open_loop_signals(
    entries: list[dict[str, Any]], now: float, profile: Optional[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Recent open loops from real senders, newest activity first."""
    rules = _operator_profile_module()
    self_addresses = (
        rules.self_address_set(profile, [entry["account"] for entry in entries])
        if rules
        else set()
    )
    assistant_senders = rules.assistant_sender_set(profile) if rules else set()
    kept: list[dict[str, Any]] = []
    for entry in entries:
        if now - float(entry["last_seen_ts"]) > SIGNAL_MAX_AGE_SECONDS:
            continue
        if entry.get("bulk_sender") is True:
            continue
        sender = str(entry.get("from_address") or "").strip().casefold()
        if not sender:
            person = str(entry.get("person") or "").strip().casefold()
            sender = person if "@" in person and " " not in person else ""
        if sender and (sender in self_addresses or sender in assistant_senders):
            continue
        kept.append(entry)
    kept.sort(
        key=lambda entry: (float(entry["last_seen_ts"]), float(entry["first_seen_ts"])),
        reverse=True,
    )
    return kept


def _memory_files() -> list[Path]:
    root = get_hermes_home() / "memories"
    if not root.is_dir():
        return []
    files: list[Path] = []
    try:
        for path in root.rglob("*"):
            if path.is_file() and path.suffix.lower() in {".md", ".txt", ".json"}:
                files.append(path)
    except OSError:
        return files
    try:
        files.sort(key=lambda path: path.stat().st_mtime_ns, reverse=True)
    except OSError:
        pass
    return files[:50]


def _scan_memory(
    now: float, previous_hashes: dict[str, str]
) -> tuple[list[dict[str, Any]], dict[str, str], list[str]]:
    candidates: list[dict[str, Any]] = []
    current_hashes: dict[str, str] = {}
    errors: list[str] = []
    memory_root = get_hermes_home() / "memories"
    for path in _memory_files():
        try:
            stat = path.stat()
            if stat.st_size > 1024 * 1024:
                continue
            raw = path.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            relative = str(path.relative_to(memory_root))
            current_hashes[relative] = digest
            if previous_hashes.get(relative) == digest:
                continue
            if now - stat.st_mtime > RECENT_SECONDS:
                continue
            text = raw.decode("utf-8", errors="replace")
            excerpt = ""
            for block in reversed(re.split(r"\n\s*\n", text)):
                if block.strip():
                    excerpt = block.strip()
                    break
            item = _candidate(
                source=f"memory:{relative}",
                headline=f"Memory updated: {path.stem}",
                summary=excerpt or f"{relative} changed.",
                created_at=stat.st_mtime,
            )
            if item:
                candidates.append(item)
        except OSError as exc:
            errors.append(f"memory {path}: {exc}")
    return candidates, current_hashes, errors


def _town_label(candidate: dict[str, Any]) -> Optional[str]:
    if not str(candidate.get("source") or "").startswith("gmail:"):
        return None
    label = candidate.get("town_label")
    if not isinstance(label, str) or not label.strip():
        return None
    return label.strip().casefold()


def _is_ntk_candidate(candidate: dict[str, Any]) -> bool:
    return _town_label(candidate) not in _TOWN_LABEL_NTK_EXCLUSIONS


def _notable_order_amounts(candidate: dict[str, Any]) -> list[str]:
    if _town_label(candidate) != "orders":
        return []
    text = f"{candidate['headline']} {candidate['summary']}"
    notable: list[str] = []
    for match in _AMOUNT_RE.finditer(text):
        amount_text = match.group(0)
        numeric = re.sub(r"[^\d.]", "", amount_text.replace(",", ""))
        try:
            amount = float(numeric)
        except ValueError:
            continue
        if amount >= 100.0 and amount_text not in notable:
            notable.append(amount_text)
    return notable


def _open_loop_key(account: Any, thread_id: Any) -> str:
    normalized_account = str(account or "").strip().casefold()
    normalized_thread = str(thread_id or "").strip()
    if not normalized_account or not normalized_thread:
        return ""
    return f"{normalized_account}:{normalized_thread}"


def _heuristic_score(candidate: dict[str, Any], now: float) -> float:
    age_hours = max(0.0, (now - float(candidate["created_at_ts"])) / 3600.0)
    recency = 42.0 * math.exp(-age_hours / 48.0)
    text = f"{candidate['headline']} {candidate['summary']}"
    score = 20.0 + recency
    if _ERROR_RE.search(text):
        score += 24.0
    if _DEADLINE_RE.search(text):
        score += 16.0
    if _ACTION_RE.search(text):
        score += 10.0
    if str(candidate["source"]).startswith("cron:"):
        score += 4.0
    if (
        str(candidate["source"]).startswith("gmail:")
        and candidate.get("unread") is True
        and candidate.get("important") is True
    ):
        score += 6.0
    label = _town_label(candidate) or ""
    if label == "orders" and _notable_order_amounts(candidate):
        score += 15.0
    else:
        score += _TOWN_LABEL_SCORE_ADJUSTMENTS.get(label, 0.0)
    return round(max(0.0, min(100.0, score)), 2)


def _relative_age(timestamp: float, now: float) -> str:
    seconds = max(0.0, now - timestamp)
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 48 * 3600:
        hours = max(1, round(seconds / 3600))
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = max(2, round(seconds / 86400))
    return f"{days} days ago"


def _relative_future(timestamp: float, now: float) -> str:
    seconds = max(0.0, timestamp - now)
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"in {minutes} minute{'s' if minutes != 1 else ''}"
    if seconds < 48 * 3600:
        hours = max(1, round(seconds / 3600))
        return f"in {hours} hour{'s' if hours != 1 else ''}"
    days = max(2, round(seconds / 86400))
    return f"in {days} days"


def _zone_or_none(name: Any) -> Any:
    text = str(name or "").strip()
    if not text:
        return None
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception:
        return None


def _local_timezone(profile: Optional[dict[str, Any]] = None) -> Any:
    """The operator's home zone (zoneinfo, DST-aware) from their profile.

    The host's current offset is only the last resort, when no profile source
    (operator.json, USER.md, config.yaml, system zone) names a valid zone.
    """
    if profile is None:
        profile = _load_profile()
    zone = _zone_or_none((profile or {}).get("home_timezone"))
    return zone or datetime.now(timezone.utc).astimezone().tzinfo


# --- S1: where the operator is, and their local "now" -----------------------

# Instants in LLM inputs and the local-time string written beside each (S1:
# the models never convert UTC themselves). Date-only values stay as they are.
_LOCAL_TIME_FIELDS = {
    "created_at": "created_local",
    "start_at": "start_local",
    "start_iso": "start_local",
    "end_iso": "end_local",
    "received_at": "received_local",
    "first_seen_at": "first_seen_local",
    "last_seen_at": "last_seen_local",
    "last_sent_at": "last_sent_local",
    "sent_at": "sent_local",
    "deadline": "deadline_local",
    "a_start_iso": "a_start_local",
    "b_start_iso": "b_start_local",
    "starts_iso": "starts_local",
    "first_start_iso": "first_start_local",
    "last_end_iso": "last_end_local",
    "since": "since_local",
    "until": "until_local",
}

_OPERATOR_RULES = (
    "Operator context: operator says who the operator is and where they are "
    "right now (operator.where: status home, away, traveling_today, or "
    "just_returned, with its evidence). They live in operator.home_city: never "
    "describe home_city as a destination, a visit, a trip, or somewhere they "
    "are 'in town' — going back there is coming home. Every instant in the "
    "input has a *_local string in the operator's current zone "
    "(operator.local_now_label is now): use those for every time you write and "
    "never convert UTC yourself. When operator.where.status is not home, "
    "mention home time (the *_home strings) only where it matters, such as a "
    "call with people back home. "
)


def _infer_where(
    profile: Optional[dict[str, Any]],
    schedule_events: list[dict[str, Any]],
    recent_travel: list[dict[str, Any]],
    now: float,
) -> Optional[dict[str, Any]]:
    module = _where_module()
    if module is None:
        return None
    try:
        return module.infer_where(schedule_events, recent_travel, profile or {}, now)
    except Exception:
        return None


def _display_timezone(profile: Optional[dict[str, Any]], where: Optional[dict[str, Any]]) -> Any:
    """Where "local" is: the away zone while the operator is away, else home."""
    if isinstance(where, dict) and where.get("status") == "away":
        zone = _zone_or_none(where.get("timezone"))
        if zone is not None:
            return zone
    return _local_timezone(profile)


def _operator_block(
    profile: Optional[dict[str, Any]], now: float, where: Optional[dict[str, Any]]
) -> dict[str, Any]:
    module = _operator_profile_module()
    if module is None or not hasattr(module, "operator_context"):
        return {}
    try:
        return module.operator_context(profile or {}, now, where)
    except Exception:
        return {}


def _localize_times(value: Any, zone_name: str, home_zone_name: str = "") -> Any:
    """A copy of an LLM input with ``*_local`` (and, away, ``*_home``) strings.

    Every instant in ``_LOCAL_TIME_FIELDS`` gains a label such as "Mon Oct 12,
    9:00 AM ET" in ``zone_name``; existing labels are never overwritten.
    """
    module = _operator_profile_module()
    if module is None or not hasattr(module, "local_time_label") or not zone_name:
        return value
    with_home = bool(home_zone_name) and home_zone_name != zone_name

    def labels(raw: Any) -> tuple[str, str]:
        text = str(raw or "").strip()
        timestamp = _parse_timestamp(text) if "T" in text else 0.0
        if not timestamp:
            return "", ""
        home = module.local_time_label(timestamp, home_zone_name) if with_home else ""
        return module.local_time_label(timestamp, zone_name), home

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        localized = {key: walk(item) for key, item in node.items()}
        for field, target in _LOCAL_TIME_FIELDS.items():
            if field not in node or target in node:
                continue
            raw = node[field]
            if isinstance(raw, list):
                local_values = [labels(item)[0] for item in raw]
                if any(local_values):
                    localized[target] = local_values
                continue
            local, home = labels(raw)
            if local:
                localized[target] = local
            if home:
                localized[target[: -len("_local")] + "_home"] = home
        return localized

    return walk(value)


def _is_meaningful(event: dict[str, Any]) -> bool:
    module = _where_module()
    if module is None:
        return not event.get("all_day")
    try:
        return bool(module.is_meaningful(event))
    except Exception:
        return not event.get("all_day")


def _meaningful_counts(
    schedule_events: list[dict[str, Any]], now: float, local_tz: Any
) -> Optional[dict[str, int]]:
    module = _where_module()
    if module is None:
        return None
    try:
        return module.meaningful_counts(schedule_events, now, local_tz)
    except Exception:
        return None


def _next_meaningful_event(
    schedule_events: list[dict[str, Any]], now: float
) -> Optional[dict[str, str]]:
    """The scanner's slim ``next_event`` shape, for the next meaningful event."""
    module = _where_module()
    if module is None:
        return None
    try:
        event = module.next_meaningful_event(schedule_events, now)
    except Exception:
        return None
    return _normalize_next_event(event) if isinstance(event, dict) else None


# Context rows, not commitments: never conflicts, clusters, load, or trips.
_SCHEDULE_IGNORED_EVENT_TYPES = {"workingLocation", "birthday"}


def _schedule_identity_keys(
    event: dict[str, Any], start_ts: float, end_ts: float
) -> set[tuple[Any, ...]]:
    """One occurrence's identities: iCalUID + start, or summary + start + end."""
    keys: set[tuple[Any, ...]] = set()
    ical_uid = str(event.get("ical_uid") or "").strip()
    if ical_uid:
        keys.add(("ical", ical_uid, start_ts))
    summary = " ".join(str(event.get("summary") or "").split()).casefold()
    if summary:
        keys.add(("text", summary, start_ts, end_ts))
    return keys


def _schedule_context(
    schedule_events: list[dict[str, Any]], now: float, local_tz: Any = None
) -> Optional[dict[str, Any]]:
    """Deterministic day-shape facts computed from the normalized schedule.

    Pure interval math — the day-brief LLM narrates these facts but cannot
    invent them (the G6R sensing/judgment split, applied to synthesis).
    ``local_tz`` is the operator's display zone (home, or away zone).
    """
    if not schedule_events:
        return None
    local_tz = local_tz or _local_timezone()
    local_now = datetime.fromtimestamp(now, tz=local_tz)
    today = local_now.date()

    timed: list[dict[str, Any]] = []
    all_day: list[dict[str, Any]] = []
    seen_identities: set[tuple[Any, ...]] = set()
    for event in schedule_events:
        if str(event.get("event_type") or "") in _SCHEDULE_IGNORED_EVENT_TYPES:
            continue
        start_ts = _parse_timestamp(event.get("start_iso"))
        end_ts = _parse_timestamp(event.get("end_iso"))
        if not start_ts or not end_ts or end_ts <= start_ts:
            continue
        identities = _schedule_identity_keys(event, start_ts, end_ts)
        if identities & seen_identities:
            seen_identities |= identities
            continue
        seen_identities |= identities
        row = dict(event)
        row["_start_ts"] = start_ts
        row["_end_ts"] = end_ts
        row["_start_local"] = datetime.fromtimestamp(start_ts, tz=local_tz)
        (all_day if event.get("all_day") else timed).append(row)
    timed.sort(key=lambda row: (row["_start_ts"], row["_end_ts"]))

    conflicts: list[dict[str, Any]] = []
    for index, first in enumerate(timed):
        for second in timed[index + 1 :]:
            if second["_start_ts"] >= first["_end_ts"]:
                break
            overlap = min(first["_end_ts"], second["_end_ts"]) - second["_start_ts"]
            if overlap <= 0:
                continue
            conflicts.append(
                {
                    "a": first["summary"],
                    "b": second["summary"],
                    "a_start_iso": first["start_iso"],
                    "b_start_iso": second["start_iso"],
                    "overlap_minutes": int(overlap // 60),
                }
            )
            if len(conflicts) >= 6:
                break
        if len(conflicts) >= 6:
            break

    start_clusters: list[dict[str, Any]] = []
    cluster: list[dict[str, Any]] = []
    for row in timed:
        if cluster and row["_start_ts"] - cluster[-1]["_start_ts"] <= 45 * 60:
            cluster.append(row)
            continue
        if len(cluster) >= 2 and len(start_clusters) < 3:
            start_clusters.append(cluster)
        cluster = [row]
    if len(cluster) >= 2 and len(start_clusters) < 3:
        start_clusters.append(cluster)
    cluster_rows = [
        {
            "summaries": [row["summary"] for row in group[:4]],
            "starts_iso": [row["start_iso"] for row in group[:4]],
            "evening": group[0]["_start_local"].hour >= 17,
        }
        for group in start_clusters
    ]

    back_to_back: list[list[str]] = []
    chain: list[dict[str, Any]] = []
    for row in timed:
        if chain and 0 <= row["_start_ts"] - chain[-1]["_end_ts"] <= 10 * 60:
            chain.append(row)
            continue
        if len(chain) >= 2 and len(back_to_back) < 3:
            back_to_back.append([entry["summary"] for entry in chain[:5]])
        chain = [row]
    if len(chain) >= 2 and len(back_to_back) < 3:
        back_to_back.append([entry["summary"] for entry in chain[:5]])

    tz_skew: list[dict[str, Any]] = []
    for row in timed:
        event_tz_name = str(row.get("event_tz") or "").strip()
        if not event_tz_name or len(tz_skew) >= 5:
            continue
        try:
            from zoneinfo import ZoneInfo

            event_tz = ZoneInfo(event_tz_name)
        except Exception:
            continue
        moment = datetime.fromtimestamp(row["_start_ts"], tz=timezone.utc)
        if moment.astimezone(event_tz).utcoffset() != moment.astimezone(local_tz).utcoffset():
            tz_skew.append(
                {
                    "summary": row["summary"],
                    "event_tz": event_tz_name,
                    "start_iso": row["start_iso"],
                }
            )

    trips: list[dict[str, Any]] = []
    for row in all_day:
        if not (row.get("multi_day") or row.get("lodging_hint")):
            continue
        start_date = row["_start_local"].date()
        end_date = datetime.fromtimestamp(row["_end_ts"], tz=local_tz).date()
        total_days = max(1, (end_date - start_date).days)
        day_n = min(max(1, (today - start_date).days + 1), total_days)
        trip = {
            "summary": row["summary"],
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "day_n": day_n,
            "total_days": total_days,
        }
        if row.get("location"):
            trip["location"] = row["location"]
        trips.append(trip)
        if len(trips) >= 2:
            break

    today_rows = [row for row in timed if row["_start_local"].date() == today]
    booked_minutes = sum(
        int((row["_end_ts"] - row["_start_ts"]) // 60) for row in today_rows
    )
    largest_gap = 0
    for previous, following in zip(today_rows, today_rows[1:]):
        gap = int((following["_start_ts"] - previous["_end_ts"]) // 60)
        largest_gap = max(largest_gap, gap)
    load: dict[str, Any] = {
        "events_today": len(today_rows),
        "booked_minutes_today": booked_minutes,
    }
    if today_rows:
        load["first_start_iso"] = today_rows[0]["start_iso"]
        load["last_end_iso"] = max(row["_end_ts"] for row in today_rows)
        load["last_end_iso"] = _iso_timestamp(load["last_end_iso"])
        load["largest_gap_minutes"] = largest_gap

    venue_changes: list[dict[str, Any]] = []
    for previous, following in zip(today_rows, today_rows[1:]):
        from_location = str(previous.get("location") or "").strip()
        to_location = str(following.get("location") or "").strip()
        gap_minutes = int((following["_start_ts"] - previous["_end_ts"]) // 60)
        if (
            from_location
            and to_location
            and from_location.casefold() != to_location.casefold()
            and gap_minutes <= 90
        ):
            venue_changes.append(
                {
                    "from_summary": previous["summary"],
                    "from_location": from_location,
                    "to_summary": following["summary"],
                    "to_location": to_location,
                    "gap_minutes": max(0, gap_minutes),
                }
            )
        if len(venue_changes) >= 4:
            break

    return {
        "local_tz": str(getattr(local_tz, "key", None) or local_now.tzname() or "local"),
        "date": today.isoformat(),
        "conflicts": conflicts,
        "start_clusters": cluster_rows,
        "back_to_back": back_to_back,
        "tz_skew": tz_skew,
        "trips": trips,
        "load": load,
        "venue_changes": venue_changes,
    }


def _day_brief_input_hash(
    schedule_events: list[dict[str, Any]],
    schedule_context: Optional[dict[str, Any]],
    where: Optional[dict[str, Any]] = None,
    local_date: str = "",
) -> str:
    identity = {
        "events": [
            [
                str(event.get("summary") or ""),
                str(event.get("start_iso") or ""),
                str(event.get("end_iso") or ""),
                str(event.get("location") or ""),
            ]
            for event in schedule_events
        ],
        "context": schedule_context or {},
    }
    # S1: a move (home -> away, landing) or a new local day re-briefs.
    if isinstance(where, dict):
        identity["where"] = [str(where.get("status") or ""), str(where.get("city") or "")]
    if local_date:
        identity["local_date"] = local_date
    canonical = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# --- S2: who people are (directory facts for both LLM lanes) --------------

_PEOPLE_MODULE = "hermes_town_people"
_PEOPLE_RELATIONSHIPS = {"colleague", "contact", "correspondent", "new"}
_MEETING_EVENT_TYPES = {"", "default", "fromGmail"}
MAX_ROW_PEOPLE_ATTENDEES = 5
MAX_MEETING_PEOPLE_EVENTS = 4
MAX_MEETING_PEOPLE = 6
DAY_BRIEF_MAX_PREP = 2
DAY_BRIEF_PREP_LINE_CHARS = 90
DAY_BRIEF_TALKING_POINT_CHARS = 140


def _people_module() -> Optional[Any]:
    """The sibling people.py, loaded by path; ``None`` means no people facts."""
    cached = sys.modules.get(_PEOPLE_MODULE)
    if cached is not None:
        return cached
    path = Path(__file__).resolve().with_name("people.py")
    try:
        spec = importlib.util.spec_from_file_location(_PEOPLE_MODULE, path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        sys.modules[_PEOPLE_MODULE] = module
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(_PEOPLE_MODULE, None)
        return None
    return module


def _people_index(
    profile: dict[str, Any],
    accounts: Iterable[Any],
    known_contacts: Optional[set[str]],
    now: float,
) -> Optional[Any]:
    """One curation pass's view of the people directory (None without it).

    Connected accounts count as the operator's own addresses (their custom
    domains make colleagues), exactly as when the directory was built.
    """
    module = _people_module()
    if module is None:
        return None
    try:
        own = list(profile.get("self_addresses") or [])
        own.extend(str(account or "").strip().casefold() for account in accounts if account)
        return module.PeopleIndex(
            module.load_directory(get_hermes_home()),
            dict(profile, self_addresses=own),
            now=now,
            known=known_contacts or (),
        )
    except Exception:
        return None


def _event_key(event: dict[str, Any]) -> str:
    """A stable id for one occurrence, shared by /feed rows and day-brief prep."""
    identity = str(event.get("ical_uid") or "").strip() or " ".join(
        str(event.get("summary") or "").split()
    ).casefold()
    start = _parse_timestamp(event.get("start_iso"))
    digest = hashlib.sha256(f"{identity}\n{start:.0f}".encode("utf-8")).hexdigest()
    return f"ev_{digest[:12]}"


def _schedule_event_for(
    candidate: dict[str, Any], schedule_events: list[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """The schedule row behind a ``calendar:`` candidate (account + start, then title)."""
    kind, _, rest = str(candidate.get("source") or "").partition(":")
    if kind != "calendar" or not rest:
        return None
    account = rest.partition(":")[0].strip().casefold()
    start = float(candidate.get("created_at_ts") or 0.0)
    matches = [
        event
        for event in schedule_events
        if str(event.get("account") or "").strip().casefold() == account
        and abs(_parse_timestamp(event.get("start_iso")) - start) < 1.0
    ]
    if len(matches) <= 1:
        return matches[0] if matches else None
    headline = " ".join(str(candidate.get("headline") or "").split()).casefold()
    for event in matches:
        summary = " ".join(str(event.get("summary") or "").split()).casefold()
        if summary and headline.startswith(summary):
            return event
    return None


def _row_people(
    item: dict[str, Any], people: Any, schedule_events: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Ranking-row people: a Gmail sender + up to 4 recipients, or up to 5 attendees."""
    source = str(item.get("source") or "")
    if source.startswith("gmail:"):
        return people.message_people(item)
    event = _schedule_event_for(item, schedule_events)
    entries: list[dict[str, Any]] = []
    for attendee in (event or {}).get("attendees") or []:
        entry = people.attendee_entry(attendee) if isinstance(attendee, dict) else None
        if entry:
            entries.append(entry)
        if len(entries) >= MAX_ROW_PEOPLE_ATTENDEES:
            break
    return entries


_PEOPLE_RANKING_RULES = (
    "A row's people entries describe its sender, recipients, or attendees: "
    "name, org, relationship (colleague, contact, correspondent, or new), "
    "last_contact, and title when known. Introduce people by name, with their "
    "org or relationship when supplied. Never invent roles, titles, orgs, or "
    "relationships beyond those fields. Say there is no prior email history "
    "with someone only when their relationship is new — never for a "
    "colleague, contact, or correspondent. "
)


def _meeting_people(
    schedule_events: list[dict[str, Any]], people: Any, now: float, local_tz: Any
) -> list[dict[str, Any]]:
    """Day-brief input: up to 4 meetings in the next 24 h x up to 6 people each."""
    meetings: list[dict[str, Any]] = []
    for event in sorted(schedule_events, key=lambda row: _parse_timestamp(row.get("start_iso"))):
        if event.get("all_day") is True:
            continue
        if str(event.get("event_type") or "") not in _MEETING_EVENT_TYPES:
            continue
        start_ts = _parse_timestamp(event.get("start_iso"))
        if not start_ts or start_ts < now or start_ts > now + 24 * 3600:
            continue
        entries: list[dict[str, Any]] = []
        for attendee in event.get("attendees") or []:
            entry = people.attendee_entry(attendee, with_email=True) if isinstance(attendee, dict) else None
            if entry and entry.get("email"):
                entries.append(entry)
            if len(entries) >= MAX_MEETING_PEOPLE:
                break
        if not entries:
            continue
        meeting: dict[str, Any] = {
            "event_key": str(event.get("event_key") or "") or _event_key(event),
            "summary": _compact(str(event.get("summary") or ""), 160),
            "start_local": datetime.fromtimestamp(start_ts, tz=local_tz).strftime("%a %-I:%M %p"),
            "people": entries,
        }
        count = event.get("attendee_count")
        if isinstance(count, int) and count > len(entries) + 1:
            meeting["attendee_count"] = count
        details = str(event.get("description_excerpt") or "").strip()
        if details:
            meeting["details"] = _compact(details, 400)
        meetings.append(meeting)
        if len(meetings) >= MAX_MEETING_PEOPLE_EVENTS:
            break
    return meetings


_DAY_BRIEF_PREP_SCHEMA = {
    "type": "array",
    "maxItems": DAY_BRIEF_MAX_PREP,
    "items": {
        "type": "object",
        "properties": {
            "event_key": {"type": "string"},
            "people": {
                "type": "array",
                "maxItems": MAX_MEETING_PEOPLE,
                "items": {
                    "type": "object",
                    "properties": {
                        "email": {"type": "string"},
                        "line": {"type": "string", "maxLength": DAY_BRIEF_PREP_LINE_CHARS},
                    },
                    "required": ["email", "line"],
                    "additionalProperties": False,
                },
            },
            "talking_point": {"type": "string", "maxLength": DAY_BRIEF_TALKING_POINT_CHARS},
        },
        "required": ["event_key", "people"],
        "additionalProperties": False,
    },
}

_DAY_BRIEF_PEOPLE_RULES = (
    " meeting_people lists the people in the next meetings with name, org, "
    "relationship (colleague, contact, correspondent, or new), last_contact, "
    "and title when known: introduce attendees by name with that org or "
    "relationship, and never invent roles or relationships. The prep field "
    "(separate from prep callouts): for at most the next two meetings in "
    "meeting_people, return event_key copied unchanged, people as {email "
    "copied unchanged, line} for attendees worth a word, and an optional "
    "talking_point. Each line is at most 90 characters and grounded only in "
    "that person's supplied facts and the meeting details; say there is no "
    "prior email history only when relationship is new. talking_point is one "
    "concrete opener grounded in the meeting details or the people facts; "
    "omit it when nothing grounded fits. Skip declined attendees. With no "
    "meeting_people, return an empty prep array."
)


def _normalize_day_brief_prep(value: Any) -> list[dict[str, Any]]:
    """``[{event_key, people: [{email, line}], talking_point?}]``, at most 2."""
    if not isinstance(value, list):
        return []
    prep: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        event_key = str(raw.get("event_key") or "").strip()[:64]
        if not event_key or event_key in seen_keys:
            continue
        people: list[dict[str, str]] = []
        emails: set[str] = set()
        raw_people = raw.get("people")
        for person in raw_people[:MAX_MEETING_PEOPLE] if isinstance(raw_people, list) else []:
            if not isinstance(person, dict):
                continue
            email = str(person.get("email") or "").strip().casefold()[:254]
            line = _bounded_compact(str(person.get("line") or ""), DAY_BRIEF_PREP_LINE_CHARS)
            if "@" in email and line and email not in emails:
                emails.add(email)
                people.append({"email": email, "line": line})
        entry: dict[str, Any] = {"event_key": event_key, "people": people}
        talking_point = _bounded_compact(
            str(raw.get("talking_point") or ""), DAY_BRIEF_TALKING_POINT_CHARS
        )
        if talking_point:
            entry["talking_point"] = talking_point
        if people or talking_point:
            prep.append(entry)
            seen_keys.add(event_key)
        if len(prep) >= DAY_BRIEF_MAX_PREP:
            break
    return prep


def _ground_prep(value: Any, meetings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Raw LLM prep kept only for the next two supplied meetings and their people.

    Grounding runs before normalization caps the list, so an invented entry
    can never crowd out a real one.
    """
    allowed = {
        meeting["event_key"]: {
            person["email"] for person in meeting["people"] if person.get("email")
        }
        for meeting in meetings[:DAY_BRIEF_MAX_PREP]
    }
    grounded: list[dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        event_key = str(raw.get("event_key") or "").strip()
        emails = allowed.get(event_key)
        if emails is None:
            continue
        raw_people = raw.get("people") if isinstance(raw.get("people"), list) else []
        people = [
            person
            for person in raw_people
            if isinstance(person, dict)
            and str(person.get("email") or "").strip().casefold() in emails
        ]
        grounded.append(dict(raw, event_key=event_key, people=people))
    return _normalize_day_brief_prep(grounded)


def _suggestion_signal_groups(
    candidates: list[dict[str, Any]],
    awaiting_reply: list[dict[str, Any]],
    open_loops: list[dict[str, Any]],
    open_loop_suppressed: list[str],
    next_event: Optional[dict[str, str]],
    schedule_events: list[dict[str, Any]],
    now: float,
    known_contacts: Optional[set[str]] = None,
    schedule_context: Optional[dict[str, Any]] = None,
    people: Optional[Any] = None,
) -> dict[str, Any]:
    action_mail: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    for candidate in candidates:
        source = str(candidate.get("source") or "")
        if not source.startswith("gmail:"):
            continue
        label = _town_label(candidate)
        created_at = float(candidate["created_at_ts"])
        # Older retained mail decays below the fresh-mail bar; a lower aged
        # threshold keeps deadline re-raises (e.g. a missed RSVP) alive.
        score = float(candidate.get("score") or 0.0)
        aged = now - created_at >= 24 * 3600
        if label == "action needed" or (
            label not in {"newsletters", "promotional", "orders", "travel", "updates"}
            and (score >= 70.0 or (aged and score >= 55.0))
        ):
            action_mail.append(
                {
                    "key": candidate["key"],
                    "headline": _compact(str(candidate["headline"]), 160),
                    "snippet": _compact(str(candidate["summary"]), 280),
                    "received_at": _iso_timestamp(created_at),
                    "received_ago": _relative_age(created_at, now),
                    "town_label": label,
                    "account": str(candidate.get("account") or "").strip().casefold(),
                    "thread_id": str(candidate.get("thread_id") or "").strip(),
                }
            )
        if label == "orders":
            text = f"{candidate['headline']} {candidate['summary']}"
            amounts = list(dict.fromkeys(match.group(0) for match in _AMOUNT_RE.finditer(text)))
            if amounts:
                orders.append(
                    {
                        "key": candidate["key"],
                        "headline": _compact(str(candidate["headline"]), 160),
                        "snippet": _compact(str(candidate["summary"]), 280),
                        "amounts": amounts[:3],
                        "received_at": _iso_timestamp(created_at),
                    }
                )

    action_mail.sort(key=lambda item: _parse_timestamp(item["received_at"]))
    orders.sort(key=lambda item: _parse_timestamp(item["received_at"]), reverse=True)

    suppressed = {
        str(key or "").strip() for key in open_loop_suppressed if str(key or "").strip()
    }
    open_loop_keys = {
        _open_loop_key(entry.get("account"), entry.get("thread_id")) for entry in open_loops
    }
    open_loop_keys.discard("")
    action_mail = [
        item
        for item in action_mail
        if _open_loop_key(item.get("account"), item.get("thread_id")) not in open_loop_keys
    ][:MAX_ACTION_SIGNALS]
    for entry in open_loops[:MAX_OPEN_LOOP_SIGNALS]:
        thread_key = _open_loop_key(entry.get("account"), entry.get("thread_id"))
        if not thread_key or thread_key in suppressed:
            continue
        deadline_iso = entry.get("deadline_iso")
        action_mail.append(
            {
                "account": entry["account"],
                "thread_id": entry["thread_id"],
                "person": entry["person"],
                "subject": entry["subject"],
                "ask": entry["ask"],
                "deadline": deadline_iso,
                "first_seen_at": _iso_timestamp(float(entry["first_seen_ts"])),
                "last_seen_at": _iso_timestamp(float(entry["last_seen_ts"])),
                "age": _relative_age(float(entry["first_seen_ts"]), now),
                "open_loop": True,
            }
        )

    awaiting_signals = [
        {
            "account": entry["account"],
            "thread_id": entry["thread_id"],
            "to": entry["to"],
            "subject": entry["subject"],
            "last_sent_at": _iso_timestamp(float(entry["last_sent_ts"])),
            "sent_ago": _relative_age(float(entry["last_sent_ts"]), now),
        }
        for entry in awaiting_reply[:MAX_AWAITING_REPLY_SIGNALS]
    ]

    chat_awaiting: list[dict[str, Any]] = []
    for candidate in candidates:
        if not str(candidate.get("source") or "").startswith("telegram:"):
            continue
        if candidate.get("user_spoke_last") is not True:
            continue
        chat_name = str(candidate.get("chat_name") or "").strip()
        if not chat_name:
            continue
        created_at = float(candidate["created_at_ts"])
        chat_awaiting.append(
            {
                "person": _compact(chat_name, 80),
                "last_message": _compact(str(candidate.get("headline") or ""), 160),
                "sent_at": _iso_timestamp(created_at),
                "sent_ago": _relative_age(created_at, now),
            }
        )
        if len(chat_awaiting) >= MAX_CHAT_AWAITING_SIGNALS:
            break

    event_signals: list[dict[str, Any]] = []
    seen_events: set[tuple[str, str]] = set()
    event_rows: list[dict[str, Any]] = list(schedule_events)
    if next_event:
        event_rows.append(next_event)
    for event in sorted(event_rows, key=lambda row: _parse_timestamp(row.get("start_iso"))):
        start_timestamp = _parse_timestamp(event.get("start_iso"))
        if not start_timestamp or start_timestamp < now or start_timestamp > now + 48 * 3600:
            continue
        summary = str(event.get("summary") or "").strip()
        event_key = (summary.casefold(), _iso_timestamp(start_timestamp))
        if not summary or event_key in seen_events:
            continue
        seen_events.add(event_key)
        signal = {
            "summary": _compact(summary, 180),
            "start_at": event_key[1],
            "starts_in": _relative_future(start_timestamp, now),
            "account": str(event.get("account") or "").strip(),
            "all_day": event.get("all_day") if isinstance(event.get("all_day"), bool) else None,
        }
        location = str(event.get("location") or "").strip()
        if location:
            signal["location"] = _compact(location, 120)
        conference = str(event.get("conference") or "").strip()
        if conference:
            signal["conference"] = conference
        organizer = str(event.get("organizer") or "").strip()
        if organizer:
            signal["organizer"] = _compact(organizer, 80)
        attendees = event.get("attendees")
        if isinstance(attendees, list) and attendees:
            display: list[str] = []
            first_contact: list[str] = []
            for attendee in attendees[:6]:
                if not isinstance(attendee, dict) or attendee.get("self") is True:
                    continue
                email = str(attendee.get("email") or "").strip().casefold()
                name = str(attendee.get("name") or "").strip()
                if name or email:
                    display.append(name or email)
                # S2: first contact = relationship ``new`` in the people
                # directory; colleagues, contacts, and correspondents never are.
                if people is not None and email and people.is_first_contact(attendee):
                    first_contact.append(email)
            if display:
                signal["attendees"] = display
            count = event.get("attendee_count")
            if isinstance(count, int) and count > len(display):
                signal["attendee_count"] = count
            # Grounded first-contact: claimable only against the people
            # directory — absent it, no claim is possible.
            if first_contact:
                signal["first_contact_attendees"] = first_contact
        description = str(event.get("description_excerpt") or "").strip()
        if description:
            signal["details"] = _compact(description, 240)
        event_signals.append(signal)
        if len(event_signals) >= MAX_EVENT_SIGNALS:
            break

    schedule_pressure: dict[str, Any] = {}
    if isinstance(schedule_context, dict):
        for key in ("conflicts", "start_clusters", "venue_changes", "tz_skew", "trips"):
            value = schedule_context.get(key)
            if isinstance(value, list) and value:
                schedule_pressure[key] = value

    return {
        "action_mail": action_mail[: MAX_ACTION_SIGNALS + MAX_OPEN_LOOP_SIGNALS],
        "awaiting_replies": awaiting_signals,
        "awaiting_replies_chat": chat_awaiting,
        "orders_with_amounts": orders[:MAX_ORDER_SIGNALS],
        "events_next_48h": event_signals,
        "schedule_pressure": schedule_pressure,
    }


def _normalize_suggestions(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []

    suggestions: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for raw in value:
        if not isinstance(raw, dict):
            continue
        suggestion_id = str(raw.get("id") or "").strip()
        label = _compact(str(raw.get("label") or ""), 60)
        prompt = str(raw.get("prompt") or "").strip()
        kind = str(raw.get("kind") or "").strip().casefold()
        if (
            not suggestion_id
            or suggestion_id in seen_ids
            or not label
            or not prompt
            or kind not in SUGGESTION_KINDS
            or _references_curator(f"{label} {prompt}")
        ):
            continue
        suggestions.append(
            {"id": suggestion_id, "label": label, "prompt": prompt, "kind": kind}
        )
        seen_ids.add(suggestion_id)
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
    return suggestions


# U13: deterministic post-filter so a suggestion never repeats what Up next
# or a conflict callout already says. No backfill: fewer chips beat padding.
SUGGESTION_PREP_WINDOW_SECONDS = 18 * 60 * 60
_SUGGESTION_PREP_RE = re.compile(r"(?:prep|prepare|get\s+ready|join)\b", re.IGNORECASE)
# "Prepare for tonight's calls": prep for near-term events named only
# generically — Up next already shows them.
_SUGGESTION_GENERIC_PREP_RE = re.compile(
    r"\b(?:tonight|today|this\s+(?:morning|afternoon|evening))\b.*"
    r"\b(?:calls?|meetings?|syncs?|events?|schedule)\b",
    re.IGNORECASE,
)
_SUGGESTION_NAME_SPLIT_RE = re.compile(r"[\W_]+", re.UNICODE)


def _suggestion_text(value: Any) -> str:
    return " ".join(_SUGGESTION_NAME_SPLIT_RE.sub(" ", str(value or "")).casefold().split())


_EVENT_TITLE_TAIL_RE = re.compile(r"\s+[-\u2013\u2014|]\s+.*$|\s*[:(].*$")


def _event_names(summary: Any) -> list[str]:
    """The full summary and its main title ("Weekly Call - Alice leads" ->
    "weekly call"), both normalized; chips name events the short way."""
    full = str(summary or "").strip()
    names = [_suggestion_text(full), _suggestion_text(_EVENT_TITLE_TAIL_RE.sub("", full))]
    return [name for index, name in enumerate(names) if name and name not in names[:index]]


def _names_event(label: str, summary: Any, *, distinctive: bool = True) -> bool:
    """The label names the event by its full summary or main title. A lone
    event needs a distinctive name; a conflict pair (both must match) may use
    short ones like "Lunch"."""
    padded = f" {label} "
    for name in _event_names(summary):
        if distinctive and len(name.split()) < 2 and len(name) < 6:
            continue
        if len(name) >= 3 and f" {name} " in padded:
            return True
    return False


def _ensure_conflict_callout(
    day_brief: Optional[dict[str, Any]],
    schedule_context: Optional[dict[str, Any]],
    now: float,
    local_tz: Any,
) -> Optional[dict[str, Any]]:
    """Guarantee a conflict starting within 24h is called out once.

    Callouts are LLM-chosen and vary run to run; the conflict callout is the
    one place a schedule conflict shows (U13), so when the brief omits it the
    curator adds a plain factual one first.
    """
    if not isinstance(day_brief, dict):
        return day_brief
    callouts = [callout for callout in day_brief.get("callouts") or [] if isinstance(callout, dict)]
    covered = [
        _suggestion_text(callout.get("text"))
        for callout in callouts
        if callout.get("kind") == "conflict"
    ]
    added: list[dict[str, str]] = []
    for conflict in (schedule_context or {}).get("conflicts") or []:
        if not isinstance(conflict, dict):
            continue
        a, b = conflict.get("a"), conflict.get("b")
        a_start = _parse_timestamp(conflict.get("a_start_iso"))
        b_start = _parse_timestamp(conflict.get("b_start_iso"))
        if not a or not b or not a_start or not b_start:
            continue
        first = min(a_start, b_start)
        if max(a_start, b_start) <= now or first > now + 24 * 60 * 60:
            continue
        # A conflict callout naming either event already shows this overlap
        # (the LLM often shortens "Lunch with Bob" to "Lunch").
        if any(
            _names_event(text, a, distinctive=False) or _names_event(text, b, distinctive=False)
            for text in covered
        ):
            continue
        start = datetime.fromtimestamp(first, tz=local_tz)
        today = datetime.fromtimestamp(now, tz=local_tz).date()
        day = "today" if start.date() == today else (
            "tomorrow" if (start.date() - today).days == 1 else start.strftime("%a")
        )
        clock = start.strftime("%-I:%M %p")
        title_a = _EVENT_TITLE_TAIL_RE.sub("", str(a)).strip() or str(a)
        title_b = _EVENT_TITLE_TAIL_RE.sub("", str(b)).strip() or str(b)
        added.append({
            "kind": "conflict",
            "text": f"{title_a} overlaps {title_b} {day} at {clock}; pick a priority.",
        })
        covered.append(_suggestion_text(f"{a} {b}"))
    if not added:
        return day_brief
    return {**day_brief, "callouts": (added + callouts)[:DAY_BRIEF_MAX_CALLOUTS]}


def _filter_suggestions(
    suggestions: list[dict[str, str]],
    schedule_events: list[dict[str, Any]],
    schedule_context: Optional[dict[str, Any]],
    now: float,
    callouts: Optional[list[dict[str, Any]]] = None,
) -> list[dict[str, str]]:
    """Drop prep chips for an Up-next event, and chips restating a conflict
    that a day-brief conflict callout already shows (an uncovered conflict
    keeps its chip so it is never shown nowhere)."""
    # Up next shows meaningful events under way or starting soon.
    upcoming: list[Any] = []
    for event in schedule_events:
        if not isinstance(event, dict) or not _is_meaningful(event):
            continue
        start_ts = _parse_timestamp(event.get("start_iso"))
        end_ts = _parse_timestamp(event.get("end_iso"))
        if start_ts and end_ts > now and start_ts <= now + SUGGESTION_PREP_WINDOW_SECONDS:
            upcoming.append(event.get("summary"))
    conflicts = [
        (conflict.get("a"), conflict.get("b"))
        for conflict in (schedule_context or {}).get("conflicts") or []
        if isinstance(conflict, dict)
    ]
    callout_texts = [
        _suggestion_text(callout.get("text"))
        for callout in callouts or []
        if isinstance(callout, dict) and callout.get("kind") == "conflict"
    ]
    conflicts = [
        (a, b)
        for a, b in conflicts
        if any(
            _names_event(text, a, distinctive=False) or _names_event(text, b, distinctive=False)
            for text in callout_texts
        )
    ]
    kept: list[dict[str, str]] = []
    for suggestion in suggestions:
        raw_label = str(suggestion.get("label") or "").strip()
        label = _suggestion_text(raw_label)
        if _SUGGESTION_PREP_RE.match(raw_label) and (
            any(_names_event(label, summary) for summary in upcoming)
            or _SUGGESTION_GENERIC_PREP_RE.search(raw_label)
        ):
            continue
        if any(
            _names_event(label, a, distinctive=False) and _names_event(label, b, distinctive=False)
            for a, b in conflicts
        ):
            continue
        kept.append(suggestion)
    return kept


# C7: the one voice, injected into every Town LLM lane (ranking + day brief).
TOWN_VOICE = (
    "Voice: you write for one operator, in second person, as a sharp chief of "
    "staff. Name people, never email addresses, when a name is available. Use "
    "concrete numbers, amounts, dates, and times. Verdicts and recommendations "
    "are welcome; hedging is not. Warm but direct; no exclamation marks. Never "
    "use generic scheduler or system phrasing such as 'Cron job', 'scheduled "
    "run', 'system update', or 'session update', and never mention the curator, "
    "scheduler mechanics, or the curation task itself. Use zero operations "
    "jargon: never say 'verify auth state', 'check config', or 'review logs' "
    "unless the item genuinely concerns infrastructure the user owns. "
)

_RANKING_RULES = (
    "Rank timely Need-to-Know updates for one operator. Favor recent failures, "
    "deadlines, decisions, and actionable outcomes. For each selected item, use "
    "its input key unchanged. Every headline must lead with the concrete entity, "
    "person, project, or device involved (for example, 'Kasa smart plug setup — "
    "unresolved'). Call out any source deadline or date explicitly. For mail, "
    "extract concrete specifics from the body—amounts, quantities, dates, "
    "deadlines, and names—and never echo a vague snippet. For an orders message "
    "with an amount of at least $100, lead the headline with the amount and "
    "merchant. For chat threads (telegram sources), never echo raw message "
    "lines: use the supplied body to say what happened and what, if anything, "
    "the user must do, attributing every statement to its sender by name. In "
    "a telegram body, [YOU] marks the user's own messages, '↩ <name>' a reply "
    "to <name> ('↩ you' is a reply to the user), and '@you' a message that "
    "mentions or names the user. When user_spoke_last is true the newest "
    "message is the user's own — phrase the item as an open loop ('You told "
    "Bob you're free tomorrow and offered to bring dinner; waiting on his "
    "confirm'), never as news from the other person. When a telegram row has "
    "chat_type group and addressed_to_user false, nobody in it addressed the "
    "user: never phrase it as the user's task — score it below 20 or omit "
    "it; only the user's own unanswered promise there may appear, as an open "
    "loop. When operator_replied_after_ask is true the user already answered "
    "the latest ask: do not present that ask as open — at most an open loop "
    "if the user's reply promised something. Summaries are the takeaway plus "
    "its implication or next step, "
    "never a restatement of the source; when a source states a deadline and a "
    "consequence, carry both ('trial ends Jul 29 — routines lock'). Summaries "
    "must be concrete and at most two sentences. For an item with an obvious "
    "next action, also return action_hint: an imperative verb phrase of at most "
    "48 characters naming that action ('Reply about tomorrow night', 'Confirm "
    "by Thu'); omit action_hint otherwise. "
)

_CLOSING_RULES = (
    "Write only factual concise headlines, summaries, and suggestions, score "
    "0-100, do not invent facts, and return only items worth showing."
)


def _llm_rank(
    candidates: list[dict[str, Any]],
    llm: Any,
    *,
    suggestion_candidates: Optional[list[dict[str, Any]]] = None,
    awaiting_reply: Optional[list[dict[str, Any]]] = None,
    open_loops: Optional[list[dict[str, Any]]] = None,
    open_loop_suppressed: Optional[list[str]] = None,
    next_event: Optional[dict[str, str]] = None,
    schedule_events: Optional[list[dict[str, Any]]] = None,
    now: Optional[float] = None,
    known_contacts: Optional[set[str]] = None,
    schedule_context: Optional[dict[str, Any]] = None,
    has_enriched_contract: bool = False,
    people: Optional[Any] = None,
    operator: Optional[dict[str, Any]] = None,
) -> tuple[dict[str, dict[str, Any]], list[dict[str, str]], Optional[str]]:
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "headline": {"type": "string"},
                        "summary": {"type": "string"},
                        "action_hint": {"type": "string", "maxLength": 60},
                        "score": {"type": "number", "minimum": 0, "maximum": 100},
                    },
                    "required": ["key", "headline", "summary", "score"],
                    "additionalProperties": False,
                },
            },
            "suggestions": {
                "type": "array",
                "minItems": 3,
                "maxItems": MAX_SUGGESTIONS,
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "label": {"type": "string", "maxLength": 60},
                        "prompt": {"type": "string"},
                        "kind": {
                            "type": "string",
                            "enum": sorted(SUGGESTION_KINDS),
                        },
                    },
                    "required": ["id", "label", "prompt", "kind"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    payload: list[dict[str, Any]] = []
    for index, item in enumerate(candidates[:MAX_CANDIDATES_FOR_LLM]):
        row = {
            "key": item["key"],
            "source": item["source"],
            "headline": item["headline"],
            "summary": item["summary"],
            "heuristic_score": item["score"],
            "created_at": _iso_timestamp(item["created_at_ts"]),
        }
        if "town_label" in item:
            row["town_label"] = item.get("town_label")
        if "town_label_confidence" in item:
            row["town_label_confidence"] = item.get("town_label_confidence")
        source = str(item.get("source") or "")
        if (
            index < 15
            and source.startswith(("gmail:", "telegram:"))
            and str(item.get("body") or "").strip()
        ):
            row["body"] = _bounded_compact(str(item["body"]), 1500)
        if source.startswith("telegram:"):
            for field in ("chat_name", "last_sender", "chat_type"):
                if str(item.get(field) or "").strip():
                    row[field] = _compact(str(item[field]), 80)
            for field in ("user_spoke_last", "addressed_to_user", "operator_replied_after_ask"):
                if isinstance(item.get(field), bool):
                    row[field] = item[field]
        # S2: who the people on this row are.
        if people is not None:
            row_people = _row_people(item, people, schedule_events or [])
            if row_people:
                row["people"] = row_people
        payload.append(row)

    legacy_instructions = (
        TOWN_VOICE
        + _RANKING_RULES
        + _PEOPLE_RANKING_RULES
        + (
            "Also return 3-5 contextual suggestions fully grounded in the items you "
            "return and, whenever possible, derived from the top-ranked items. Write "
            "every chip label as a conversational, verb-led imperative of at most 70 "
            "characters. For a follow-up, name the person; for a next concrete step, "
            "name the event. Use a natural mix of followup, digest, and maintenance "
            "kinds rather than repeating one kind. Each suggestion needs a unique "
            "stable id, a complete ready-to-send task prompt, and a best-fit kind. "
            "Suggestions must name the concrete subject they act on and must not be "
            "generic. "
        )
        + _CLOSING_RULES
    )
    instructions = legacy_instructions
    llm_input: Any = payload
    if has_enriched_contract:
        signal_groups = _suggestion_signal_groups(
            suggestion_candidates if suggestion_candidates is not None else candidates,
            awaiting_reply or [],
            open_loops or [],
            open_loop_suppressed or [],
            next_event,
            schedule_events or [],
            time.time() if now is None else now,
            known_contacts=known_contacts,
            schedule_context=schedule_context,
            people=people,
        )
        llm_input = {
            "ranking_candidates": payload,
            "suggestion_signals": signal_groups,
            "suggestion_fallback_candidates": payload[:MAX_SUGGESTIONS],
        }
        instructions = (
            TOWN_VOICE
            + (
                "Preserve the supplied Town label signal: 'action needed' is strong "
                "headline evidence, 'new sender' is moderate evidence, and 'awaiting "
                "reply' is mild evidence; orders, travel, and updates are strongly "
                "demoted from headline status unless their content shows a separate "
                "urgent reason, except an order of at least $100 is notable. "
            )
            + _RANKING_RULES
            + _PEOPLE_RANKING_RULES
            + (
                "Also return 3-5 suggestions by independently analyzing "
                "suggestion_signals. Use the capped groups before "
                "suggestion_fallback_candidates: (1) action mail, including persistent "
                "open-loop entries with person, ask, deadline, and age, using timing "
                "fields to re-raise stale or missed deadlines; (2) awaiting replies, "
                "naming the recipient and how long ago the message was sent — "
                "awaiting_replies_chat entries are chat threads where the user spoke "
                "last and is waiting on the named person, good for one nudge chip; "
                "(3) orders with visible amounts, but only when the amount or merchant "
                "makes a verification or investigation notably worthwhile—never merely "
                "summarize a routine receipt; (4) events in the next 48 hours that "
                "genuinely merit preparation: meetings, syncs, gatherings, travel, or "
                "anything involving other people or a deliverable — events starting "
                "within the next 18 hours are already on the dashboard's Up next "
                "list, so suggest prep for one only when there is a concrete "
                "deliverable to produce and the chip names it (never a generic "
                "'prepare for tonight's calls') — use the supplied "
                "location, conference, organizer, and attendees to make prep chips "
                "concrete, and when first_contact_attendees is present you may note "
                "there is no prior email history with them (never claim that "
                "otherwise); and (5) schedule_pressure: overlapping events or tight "
                "start clusters deserve one pick-a-priority chip naming the competing "
                "events, and venue_changes with tight gaps deserve a travel-buffer "
                "chip naming both venues — never invent travel durations. NEVER "
                "suggest preparation for personal reminders or routine logistics "
                "(medication, food deliveries, chores, recurring personal tasks) — "
                "skip those events entirely; a birthday may yield at most one warm "
                "send-wishes follow-up. At most 2 suggestions may come from calendar "
                "events and schedule_pressure combined; when the mail groups (1)-(3) "
                "have any usable signal, at least one suggestion must come from them. "
                "Use an NTK fallback candidate only to fill a remaining suggestion "
                "slot after useful signals from those groups are exhausted; returning "
                "fewer, sharper suggestions beats padding with weak ones. Never make "
                "two suggestions from the same underlying signal. Write every chip "
                "label as a conversational, verb-led imperative of at most 70 "
                "characters. Name the person for a follow-up and the event for event "
                "prep; otherwise name the concrete subject. Mix followup, digest, and "
                "maintenance kinds across the output rather than repeating one kind. "
                "Each suggestion needs a unique stable id, a complete ready-to-send "
                "task prompt, and a best-fit kind. "
            )
            + _CLOSING_RULES
        )
    # S1: who and where the operator is, and local strings for every instant.
    if operator is None:
        operator = _operator_block(_load_profile(), time.time() if now is None else now, None)
    if operator:
        zones = (str(operator.get("timezone") or ""), str(operator.get("home_timezone") or ""))
        operator = _localize_times(operator, *zones)
        llm_input = _localize_times(llm_input, *zones)
        if isinstance(llm_input, dict):
            llm_input = {"operator": operator, **llm_input}
        else:
            # The legacy contract's input is the bare candidate list.
            instructions += " Operator (JSON): " + json.dumps(operator, ensure_ascii=False) + ". "
        instructions += " " + _OPERATOR_RULES
    try:
        result = llm.complete_structured(
            instructions=instructions,
            input=[{"type": "text", "text": json.dumps(llm_input, ensure_ascii=False)}],
            json_schema=schema,
            schema_name="town_feed_ranking",
            temperature=0.1,
            max_tokens=3000,
            purpose="town-feed-curation",
        )
        parsed = getattr(result, "parsed", None)
        rows = parsed.get("items") if isinstance(parsed, dict) else None
        if not isinstance(rows, list):
            return {}, [], "LLM returned no structured items"
        allowed = {item["key"] for item in candidates}
        ranked: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict) or row.get("key") not in allowed:
                continue
            ranked[str(row["key"])] = row
        if not ranked:
            return {}, [], "LLM returned no recognized candidate keys"
        suggestions = _normalize_suggestions(parsed.get("suggestions"))
        return ranked, suggestions, None
    except Exception as exc:
        return {}, [], str(exc)


# S1: the brief opens from where the operator is, and counts only real events.
_DAY_BRIEF_S1_RULES = (
    "opener: ONE sentence of at most 140 characters that orients the operator "
    "from operator.where and the shape of the schedule, grounded ONLY in "
    "operator.where (status, city, label, evidence) and the events: "
    "just_returned names the return and where from; away says where they are "
    "(and the time offset when it matters); traveling_today names the flight; "
    "a working location says where they are working; otherwise lead with the "
    "first meaningful commitment. Never invent travel, weather, places, or "
    "events, and never restate the date. Meaningful events are timed "
    "commitments with another person, a place or call link, or at least 30 "
    "minutes; events marked minor are personal reminders — never count, lead "
    "with, or call them meetings. day_summary counts ONLY meaningful events: "
    "use meaningful_today and meaningful_tomorrow, never "
    "schedule_context.load.events_today. When meaningful_today is below 3 and "
    "meaningful_tomorrow is 5 or more, day_summary says tomorrow is the "
    "heavier day ('Light day — 2 meetings; tomorrow has 8'). "
)

# U13: at home (or just back) the operator lives in their home zone, so an
# invite's foreign organizer zone is not worth converting.
_HOME_WHERE_STATUSES = {"home", "just_returned"}
_DAY_BRIEF_U13_RULES = (
    "When operator.where.status is home or just_returned, produce NO timezone "
    "callout: the operator is in their home zone, and an invite's organizer "
    "zone is not worth converting. "
)


def _drop_home_timezone_callouts(
    brief: Optional[dict[str, Any]], where: Optional[dict[str, Any]]
) -> Optional[dict[str, Any]]:
    """Drop ``timezone`` callouts while the operator is home or just back."""
    if not isinstance(brief, dict) or not isinstance(where, dict):
        return brief
    if str(where.get("status") or "") not in _HOME_WHERE_STATUSES:
        return brief
    callouts = brief.get("callouts")
    if isinstance(callouts, list):
        brief["callouts"] = [
            callout
            for callout in callouts
            if not (isinstance(callout, dict) and callout.get("kind") == "timezone")
        ]
    return brief


def _day_brief_event_projection(
    schedule_events: list[dict[str, Any]], local_tz: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in schedule_events:
        start_ts = _parse_timestamp(event.get("start_iso"))
        end_ts = _parse_timestamp(event.get("end_iso"))
        if not start_ts or not end_ts:
            continue
        start_local = datetime.fromtimestamp(start_ts, tz=local_tz)
        end_local = datetime.fromtimestamp(end_ts, tz=local_tz)
        row: dict[str, Any] = {"summary": event.get("summary")}
        if event.get("all_day"):
            row["all_day"] = True
            row["start_local"] = start_local.strftime("%a %b %-d")
            row["end_local"] = end_local.strftime("%a %b %-d")
        else:
            row["start_local"] = start_local.strftime("%a %-I:%M %p")
            row["end_local"] = end_local.strftime("%-I:%M %p")
        for field in (
            "location",
            "conference",
            "organizer",
            "event_tz",
            "multi_day",
            "lodging_hint",
        ):
            if event.get(field):
                row[field] = event[field]
        attendees = event.get("attendees")
        if isinstance(attendees, list) and attendees:
            names = [
                str(attendee.get("name") or attendee.get("email") or "").strip()
                for attendee in attendees[:6]
                if isinstance(attendee, dict) and attendee.get("self") is not True
            ]
            names = [name for name in names if name]
            if names:
                row["attendees"] = names
        details = str(event.get("description_excerpt") or "").strip()
        if details:
            row["details"] = _compact(details, 240)
        if not event.get("all_day") and not _is_meaningful(event):
            row["minor"] = True
        rows.append(row)
    return rows


def _generate_day_brief(
    llm: Any,
    *,
    schedule_events: list[dict[str, Any]],
    schedule_context: Optional[dict[str, Any]],
    next_event: Optional[dict[str, str]],
    awaiting_reply: list[dict[str, Any]],
    top_headlines: list[str],
    known_contacts: Optional[set[str]],
    now: float,
    people: Optional[Any] = None,
    operator: Optional[dict[str, Any]] = None,
    local_tz: Any = None,
    meaningful: Optional[dict[str, int]] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """One narrative sentence + grounded callouts for the dashboard header."""
    local_tz = local_tz or _local_timezone()
    local_now = datetime.fromtimestamp(now, tz=local_tz)
    schema = {
        "type": "object",
        "properties": {
            "opener": {"type": "string", "maxLength": DAY_BRIEF_OPENER_CHARS},
            "day_summary": {"type": "string", "maxLength": 220},
            "callouts": {
                "type": "array",
                "maxItems": DAY_BRIEF_MAX_CALLOUTS,
                "items": {
                    "type": "object",
                    "properties": {
                        "kind": {
                            "type": "string",
                            "enum": sorted(DAY_BRIEF_CALLOUT_KINDS),
                        },
                        "text": {"type": "string", "maxLength": 240},
                    },
                    "required": ["kind", "text"],
                    "additionalProperties": False,
                },
            },
            "trip": {
                "anyOf": [
                    {"type": "null"},
                    {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "maxLength": 40},
                            "day_n": {"type": "integer", "minimum": 1},
                            "total_days": {"type": "integer", "minimum": 1},
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                ]
            },
        },
        "required": ["opener", "day_summary", "callouts"],
        "additionalProperties": False,
    }
    # S2: meeting prep output; first contact = relationship ``new`` only.
    schema["properties"]["prep"] = _DAY_BRIEF_PREP_SCHEMA
    meeting_people = (
        _meeting_people(schedule_events, people, now, local_tz) if people is not None else []
    )
    first_contact: list[str] = []
    if people is not None:
        for event in schedule_events:
            attendees = event.get("attendees")
            if not isinstance(attendees, list):
                continue
            for attendee in attendees:
                if not isinstance(attendee, dict) or not people.is_first_contact(attendee):
                    continue
                email = str(attendee.get("email") or "").strip().casefold()
                if email and email not in first_contact:
                    first_contact.append(email)
    llm_input = {
        "local_now": local_now.strftime("%A %b %-d, %-I:%M %p"),
        "local_tz": str(getattr(local_tz, "key", None) or local_now.tzname() or "local"),
        "events": _day_brief_event_projection(schedule_events, local_tz),
        "schedule_context": schedule_context or {},
        "next_event": next_event,
        "awaiting_replies": [
            {
                "to": entry.get("to"),
                "subject": entry.get("subject"),
                "sent_ago": _relative_age(float(entry["last_sent_ts"]), now),
            }
            for entry in awaiting_reply[:5]
        ],
        "first_contact_attendees": first_contact[:8],
        "top_need_to_know": top_headlines[:5],
        "meeting_people": meeting_people,
    }
    # S1: where the operator is, meaningful-event counts, local strings.
    if operator:
        zones = (str(operator.get("timezone") or ""), str(operator.get("home_timezone") or ""))
        llm_input = {"operator": operator, **llm_input}
        llm_input = _localize_times(llm_input, *zones)
    if meaningful is not None:
        llm_input["meaningful_today"] = int(meaningful.get("today") or 0)
        llm_input["meaningful_tomorrow"] = int(meaningful.get("tomorrow") or 0)
    instructions = (
        TOWN_VOICE
        + (
            "You write the day brief at the top of the operator's dashboard. The "
            "input is the complete ground truth: never invent events, people, "
            "venues, times, or relationships, and express every time in the "
            "supplied local timezone using the start_local/end_local fields. "
            "day_summary: ONE sentence of at most 160 characters capturing the "
            "shape of the day — the load (from schedule_context.load), the single "
            "thing that matters most, and the trip day when schedule_context.trips "
            "is non-empty. Do not greet and do not state the date; the dashboard "
            "renders both. callouts: zero to four, each one concrete sentence the "
            "operator should act on before it bites. kind rules — conflict: only "
            "from schedule_context.conflicts or start_clusters; name the competing "
            "events and their local times and recommend picking a priority. "
            "travel: only from schedule_context.venue_changes or event locations; "
            "flag tight venue-to-venue transitions and say to factor in travel — "
            "never invent travel durations. timezone: only from "
            "schedule_context.tz_skew, or when a trip's location plainly disagrees "
            "with the local timezone; spell out the concrete conversion. prep: a "
            "meeting involving other people that deserves preparation; name the "
            "attendees, and when first_contact_attendees lists them you may say "
            "there is no prior email history — never claim that otherwise. "
            "deadline: a hard time today or tomorrow from events or "
            "top_need_to_know; state the consequence when the source states one. "
            "trip: on a trip day, one orienting callout is welcome. Pull concrete "
            "details — reservation names, confirmation numbers, street addresses — "
            "from event details when present. A plain day yields an empty callouts "
            "array; padding is worse than silence. trip field: only when "
            "schedule_context.trips is non-empty; label is a short name derived "
            "from the trip event or location text ('SF trip', 'SBC week'); copy "
            "day_n and total_days from the trip entry."
        )
        + _DAY_BRIEF_PEOPLE_RULES
        + " "
        + _OPERATOR_RULES
        + _DAY_BRIEF_S1_RULES
        + _DAY_BRIEF_U13_RULES
    )
    try:
        result = llm.complete_structured(
            instructions=instructions,
            input=[{"type": "text", "text": json.dumps(llm_input, ensure_ascii=False)}],
            json_schema=schema,
            schema_name="town_day_brief",
            temperature=0.2,
            # S2: room for prep lines (2 meetings x up to 6 people) after the callouts.
            max_tokens=1200,
            purpose="town-day-brief",
        )
        parsed = getattr(result, "parsed", None)
        brief = _normalize_day_brief(parsed if isinstance(parsed, dict) else None)
        if brief is None:
            return None, "day brief response had no usable day_summary"
        _drop_home_timezone_callouts(brief, (operator or {}).get("where"))
        brief.pop("prep", None)
        prep = _ground_prep(parsed.get("prep"), meeting_people)
        if prep:
            brief["prep"] = prep
        return brief, None
    except Exception as exc:
        return None, str(exc)


def _prune_decisions(decisions: dict[str, Any], now: Optional[float] = None) -> dict[str, Any]:
    cutoff = (time.time() if now is None else now) - RECENT_SECONDS
    retained = {
        item_id: decision
        for item_id, decision in decisions.items()
        if isinstance(decision, dict)
        and (
            not _parse_timestamp(decision.get("source_activity_at"))
            or _parse_timestamp(decision.get("source_activity_at")) >= cutoff
        )
    }
    ordered = sorted(
        retained.items(),
        key=lambda pair: _parse_timestamp(pair[1].get("updated_at")),
        reverse=True,
    )
    return dict(ordered[:MAX_DECISIONS])


def _identity_key(candidate: dict[str, Any]) -> str:
    """The source thing an item is about: one live item per identity.

    ``telegram:<chat_id>``, ``session:<session_id>``,
    ``gmail:<account>:<thread_id>`` (message id when threadless),
    ``calendar:<account>:<event_id>``; anything else is its own identity.
    """
    source = str(candidate.get("source") or "").strip()
    kind, _, rest = source.partition(":")
    rest = rest.strip()
    if kind == "telegram" and rest:
        return f"telegram:{rest.split(':', 1)[0]}"
    if kind == "session" and rest and rest != "unknown":
        return f"session:{rest}"
    if kind == "gmail" and rest:
        account_part, _, message_id = rest.partition(":")
        account = (str(candidate.get("account") or "").strip() or account_part).casefold()
        thread = str(candidate.get("thread_id") or "").strip() or message_id.strip()
        if account and thread:
            return f"gmail:{account}:{thread}"
    if kind == "calendar" and rest:
        return f"calendar:{rest}"
    return f"content:{candidate.get('content_hash') or source}"


def _newest_per_identity(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep the newest candidate (by source activity) per identity.

    Ties go to the later entry, so callers list retained items before fresh
    ones and older scan docs before newer ones.
    """
    newest: dict[str, tuple[float, int, dict[str, Any]]] = {}
    for index, candidate in enumerate(candidates):
        identity = _identity_key(candidate)
        activity = float(candidate.get("created_at_ts") or 0.0)
        current = newest.get(identity)
        if current is None or (activity, index) > current[:2]:
            newest[identity] = (activity, index, candidate)
    return [entry[2] for entry in sorted(newest.values(), key=lambda entry: entry[1])]


def _is_dismissed(decisions: dict[str, Any], candidate: dict[str, Any]) -> bool:
    decision = decisions.get(f"ntk_{candidate['content_hash'][:16]}")
    return isinstance(decision, dict) and bool(decision.get("dismissed"))


def _is_assistant_mail(candidate: dict[str, Any], assistant_senders: set[str]) -> bool:
    """Mail the operator's assistant sent (e.g. their own briefing) is exhaust."""
    return (
        bool(assistant_senders)
        and str(candidate.get("source") or "").startswith("gmail:")
        and str(candidate.get("from_address") or "").strip().casefold() in assistant_senders
    )


def _top_headlines(candidates: list[dict[str, Any]], hidden_keys: set[str]) -> list[str]:
    shown = sorted(
        (candidate for candidate in candidates if candidate["key"] not in hidden_keys),
        key=lambda item: (float(item["score"]), float(item["created_at_ts"])),
        reverse=True,
    )
    return [str(candidate.get("headline") or "") for candidate in shown[:5]]


MAX_CALENDAR_SERVED = 3


def _select_served(visible: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The served top items: at most ``MAX_CALENDAR_SERVED`` calendar items.

    Serve-time only — state keeps every item, so the cap never deletes.
    """
    served: list[dict[str, Any]] = []
    calendar_items = 0
    for item in visible:
        if str(item.get("source") or "").startswith("calendar:"):
            if calendar_items >= MAX_CALENDAR_SERVED:
                continue
            calendar_items += 1
        served.append(item)
        if len(served) >= MAX_FEED_ITEMS:
            break
    return served


def run_curator(llm: Any = None) -> dict[str, Any]:
    """Scan sources, rank candidates, merge durable decisions, and persist.

    Locking is two-phase so readers stay responsive: a brief lock snapshots
    state, the slow work (source scans and LLM ranking) runs UNLOCKED, and a
    second brief lock re-reads state and merges against the freshest
    decisions — a dismissal that lands mid-curation wins over the snapshot.
    Holding the lock across the LLM call starved /feed into the renderer's
    15s IPC timeout once every curation cycle.
    """
    now = time.time()
    with _process_lock():
        snapshot = _read_state_unlocked()

    source_state = snapshot.get("source_state") or {}
    previous_hashes = source_state.get("memory_hashes") or {}
    profile = _load_profile()
    assistant_senders = set(profile.get("assistant_senders") or [])

    session_items, session_errors = _scan_sessions(now)
    scan_snapshot: dict[str, Any] = {}
    (
        cron_items,
        cron_errors,
        next_event,
        schedule_events,
        awaiting_reply,
        open_loops,
        known_contacts,
        has_enriched_contract,
        cron_consumed,
    ) = _scan_cron(now, source_state.get("cron_consumed"), profile, scan_snapshot)
    memory_items, memory_hashes, memory_errors = _scan_memory(now, previous_hashes)
    errors = session_errors + cron_errors + memory_errors
    # S1: where the operator is decides what "local" means for this cycle.
    recent_travel = list(scan_snapshot.get("recent_travel") or [])
    where = _infer_where(profile, schedule_events, recent_travel, now)
    local_tz = _display_timezone(profile, where)
    operator = _operator_block(profile, now, where)
    schedule_context = _schedule_context(schedule_events, now, local_tz)
    # S2: one people-directory view for both LLM lanes.
    people_index = _people_index(
        profile,
        [event.get("account") for event in schedule_events]
        + [
            item.get("account")
            for item in cron_items
            if str(item.get("source") or "").startswith("gmail:")
        ],
        known_contacts,
        now,
    )

    by_hash: dict[str, dict[str, Any]] = {}
    for candidate in session_items + cron_items + memory_items:
        if not _is_ntk_candidate(candidate) or _is_assistant_mail(candidate, assistant_senders):
            continue
        candidate["score"] = _heuristic_score(candidate, now)
        existing = by_hash.get(candidate["content_hash"])
        if existing is None or candidate["created_at_ts"] > existing["created_at_ts"]:
            by_hash[candidate["content_hash"]] = candidate
    fresh_hashes = set(by_hash)

    # A memory change is an event, not a continuously changing source. Keep
    # recent prior items until they age out or are dismissed; otherwise a
    # successful second scan would make unchanged memory updates disappear.
    # Reading from the snapshot is safe: a dismissal racing this phase is
    # honored by the fresh-decisions filter in the merge below.
    for prior in snapshot.get("items", []):
        if not isinstance(prior, dict) or prior.get("dismissed"):
            continue
        # Cron-doc candidates were retired at the source (U10); stop stale
        # pre-U10 cron items from re-entering via retention until they age out.
        if str(prior.get("source") or "").startswith("cron:"):
            continue
        if _references_curator(
            f"{prior.get('headline') or ''} {prior.get('summary') or ''}"
        ) or str(prior.get("headline") or "").lstrip().casefold().startswith("[important"):
            continue
        digest = str(prior.get("content_hash") or "")
        created = _source_activity_timestamp(prior)
        if not digest or digest in by_hash or (created and now - created > RECENT_SECONDS):
            continue
        candidate = {
            "key": digest[:16],
            "content_hash": digest,
            "source": str(prior.get("source") or "unknown"),
            "headline": str(prior.get("headline") or "Need to Know"),
            "summary": str(prior.get("summary") or ""),
            "created_at_ts": created or now,
        }
        for field in (
            "town_label",
            "town_label_confidence",
            "body",
            "account",
            "thread_id",
            "chat_name",
            "last_sender",
            "chat_type",
            "user_spoke_last",
            "addressed_to_user",
            "operator_replied_after_ask",
            "action_hint",
            "from",
            "from_address",
            "recipients",
        ):
            if field in prior:
                candidate[field] = prior.get(field)
        # Group chat items from before addressing data existed (R1b) cannot be
        # shown to address the operator; the scanner re-emits any that do.
        if candidate["source"].startswith("telegram:-") and "addressed_to_user" not in prior:
            continue
        if not _is_ntk_candidate(candidate) or _is_assistant_mail(candidate, assistant_senders):
            continue
        candidate["score"] = _heuristic_score(candidate, now)
        by_hash[digest] = candidate

    # One live item per source: a newer message in the same chat, thread,
    # session, or event supersedes the older item instead of stacking beside it.
    candidates = sorted(
        (
            candidate
            for candidate in _newest_per_identity(
                [by_hash[digest] for digest in by_hash if digest not in fresh_hashes]
                + [by_hash[digest] for digest in by_hash if digest in fresh_hashes]
            )
            if now - float(candidate["created_at_ts"]) <= RECENT_SECONDS
        ),
        key=lambda item: (float(item["score"]), float(item["created_at_ts"])),
        reverse=True,
    )

    ranking_mode = "heuristic"
    llm_suggestions: Optional[list[dict[str, str]]] = None
    snapshot_decisions = snapshot.get("decisions") or {}
    dismissed_keys = {
        candidate["key"] for candidate in candidates if _is_dismissed(snapshot_decisions, candidate)
    }
    # Not returned by the LLM (declined, or beyond its cap): not shown (LLM mode only).
    declined_keys: set[str] = set()
    # Dismissed candidates are never shown, so they inform neither the ranking
    # nor the suggestions (whose fallback reads the ranking payload).
    ranking_candidates = [
        candidate for candidate in candidates if candidate["key"] not in dismissed_keys
    ]
    if llm is not None and ranking_candidates:
        llm_rows, suggestions, llm_error = _llm_rank(
            ranking_candidates,
            llm,
            suggestion_candidates=ranking_candidates,
            awaiting_reply=awaiting_reply,
            open_loops=open_loops,
            open_loop_suppressed=snapshot.get("open_loop_suppressed") or [],
            next_event=next_event,
            schedule_events=schedule_events,
            now=now,
            known_contacts=known_contacts,
            schedule_context=schedule_context,
            has_enriched_contract=has_enriched_contract,
            people=people_index,
            operator=operator,
        )
        if llm_rows:
            ranking_mode = "llm"
            if suggestions:
                llm_suggestions = suggestions
            for candidate in candidates:
                row = llm_rows.get(candidate["key"])
                if row:
                    candidate["headline"] = _headline(str(row.get("headline") or candidate["headline"]))
                    candidate["summary"] = _compact(
                        _strip_scheduler_preamble(str(row.get("summary") or candidate["summary"])),
                        420,
                    )
                    action_hint = _compact(str(row.get("action_hint") or ""), 60)
                    if action_hint:
                        candidate["action_hint"] = action_hint
                    else:
                        candidate.pop("action_hint", None)
                    try:
                        candidate["score"] = round(max(0.0, min(100.0, float(row["score"]))), 2)
                    except (KeyError, TypeError, ValueError):
                        pass
                else:
                    # Declined, or beyond the LLM cap and never vetted: in LLM
                    # mode only judged items are shown.
                    declined_keys.add(candidate["key"])
        elif llm_error:
            ranking_mode = "heuristic (LLM fallback)"
            errors.append(f"llm: {llm_error}")

    # C3: the day brief regenerates only when its inputs change, the local date
    # rolls, or the last one is over an hour old — not on every curation cycle.
    day_brief = _normalize_day_brief(snapshot.get("day_brief"))
    if llm is not None:
        if schedule_events:
            local_date = datetime.fromtimestamp(now, tz=local_tz).date().isoformat()
            input_hash = _day_brief_input_hash(
                schedule_events, schedule_context, where=where, local_date=local_date
            )
            current = day_brief if isinstance(day_brief, dict) else {}
            fresh_enough = (
                current.get("input_hash") == input_hash
                and current.get("local_date") == local_date
                and now - _parse_timestamp(current.get("generated_at"))
                <= DAY_BRIEF_REFRESH_SECONDS
            )
            if not fresh_enough:
                generated, day_brief_error = _generate_day_brief(
                    llm,
                    schedule_events=schedule_events,
                    schedule_context=schedule_context,
                    next_event=(
                        _next_meaningful_event(schedule_events, now)
                        if _where_module() is not None
                        else next_event
                    ),
                    awaiting_reply=awaiting_reply,
                    top_headlines=_top_headlines(candidates, dismissed_keys | declined_keys),
                    known_contacts=known_contacts,
                    now=now,
                    people=people_index,
                    operator=operator,
                    local_tz=local_tz,
                    meaningful=_meaningful_counts(schedule_events, now, local_tz),
                )
                if generated is not None:
                    generated["generated_at"] = _iso_timestamp(now)
                    generated["input_hash"] = input_hash
                    generated["local_date"] = local_date
                    day_brief = generated
                elif day_brief_error:
                    errors.append(f"day_brief: {day_brief_error}")
        else:
            day_brief = None

    day_brief = _ensure_conflict_callout(day_brief, schedule_context, now, local_tz)

    # U13: filtered after the day brief is settled, so a conflict chip is
    # dropped only when a callout in the brief being served already shows it.
    if llm_suggestions is not None:
        llm_suggestions = _filter_suggestions(
            llm_suggestions,
            schedule_events,
            schedule_context,
            now,
            callouts=(day_brief or {}).get("callouts") if isinstance(day_brief, dict) else None,
        )

    with _process_lock():
        state = _read_state_unlocked()
        existing_by_hash = {
            str(item.get("content_hash")): item
            for item in state.get("items", [])
            if isinstance(item, dict) and item.get("content_hash")
        }
        decisions = state.get("decisions") or {}
        open_loop_keys = {
            _open_loop_key(entry.get("account"), entry.get("thread_id"))
            for entry in open_loops
        }
        open_loop_keys.discard("")
        merged: list[dict[str, Any]] = []
        for candidate in candidates:
            if candidate["key"] in declined_keys:
                continue
            item_id = f"ntk_{candidate['content_hash'][:16]}"
            decision = decisions.get(item_id) if isinstance(decisions.get(item_id), dict) else {}
            source_activity_at = _iso_timestamp(float(candidate["created_at_ts"]))
            if decision:
                decision["source_activity_at"] = source_activity_at
            if decision.get("dismissed"):
                continue
            prior = existing_by_hash.get(candidate["content_hash"], {})
            merged_item = {
                "id": item_id,
                "headline": candidate["headline"],
                "summary": candidate["summary"],
                "source": candidate["source"],
                "score": candidate["score"],
                "created_at": source_activity_at,
                "source_activity_at": source_activity_at,
                "dismissed": False,
                "promoted": bool(decision.get("promoted") or prior.get("promoted")),
                "task": decision.get("task") or prior.get("task"),
                "content_hash": candidate["content_hash"],
            }
            for field in ("town_label", "town_label_confidence"):
                if field in candidate:
                    merged_item[field] = candidate.get(field)
            for field in (
                "body", "account", "thread_id", "chat_name", "last_sender", "chat_type", "action_hint"
            ):
                if candidate.get(field):
                    merged_item[field] = candidate[field]
            if candidate.get("user_spoke_last") is True:
                merged_item["user_spoke_last"] = True
            # S2: Gmail addressing survives retention so re-ranked mail keeps its people.
            if str(candidate.get("source") or "").startswith("gmail:"):
                for field in ("from", "from_address", "recipients"):
                    if candidate.get(field):
                        merged_item[field] = candidate[field]
            # R1b attribution signals must survive retention so re-ranked chat
            # items keep their addressing context.
            for field in ("addressed_to_user", "operator_replied_after_ask"):
                if isinstance(candidate.get(field), bool):
                    merged_item[field] = candidate[field]
            candidate_open_loop_key = _open_loop_key(
                candidate.get("account"), candidate.get("thread_id")
            )
            if candidate_open_loop_key in open_loop_keys:
                merged_item["open_loop_key"] = candidate_open_loop_key
            merged.append(merged_item)

        merged.sort(
            key=lambda item: (float(item["score"]), _parse_timestamp(item["created_at"])),
            reverse=True,
        )
        state["items"] = merged[:MAX_RETAINED_ITEMS]
        state["decisions"] = _prune_decisions(decisions, now)
        merged_source_state = dict(state.get("source_state") or {})
        merged_source_state["memory_hashes"] = memory_hashes
        # Persisted only with the curation result: a failed run re-reads the docs.
        merged_source_state["cron_consumed"] = _merge_cron_consumed(
            merged_source_state.get("cron_consumed"), cron_consumed
        )
        state["source_state"] = merged_source_state
        completed_at = _iso_timestamp(time.time())
        state["last_curated_at"] = completed_at
        state["ranking_mode"] = ranking_mode
        state["next_event"] = next_event
        state["schedule"] = {
            "events": schedule_events,
            "generated_at": completed_at,
            "context": schedule_context,
            "recent_travel": recent_travel,
        }
        state["day_brief"] = _normalize_day_brief(day_brief)
        # Scanner failures surface via the town-cron-health candidates, not by
        # carrying stale gmail: errors forward (which would never clear).
        state["last_curate_errors"] = errors[:20]
        # A filtered-empty list still replaces the last set (U13: no backfill).
        if ranking_mode == "llm" and llm_suggestions is not None:
            state["suggestions"] = llm_suggestions
            state["suggestions_generated_at"] = completed_at
        _write_state_unlocked(state)

    return {
        "ok": True,
        "items": len(state["items"]),
        "candidates": len(candidates),
        "ranking_mode": ranking_mode,
        "errors": errors[:20],
        "curated_at": state["last_curated_at"],
    }


_PUBLIC_SOURCE_FIELDS = ("chat_name", "last_sender", "chat_type", "account", "thread_id")


def _public_item(item: dict[str, Any]) -> dict[str, Any]:
    public = {
        "id": item["id"],
        "headline": item["headline"],
        "summary": item["summary"],
        "source": item["source"],
        "score": item["score"],
        "created_at": item["created_at"],
        "dismissed": False,
    }
    action_hint = str(item.get("action_hint") or "").strip()
    if action_hint:
        public["action_hint"] = action_hint
    # Source context the desktop needs for chips and deep links (R5): chat
    # titles instead of raw Telegram ids, the Gmail account for /mail/u/<acct>.
    for field in _PUBLIC_SOURCE_FIELDS:
        value = item.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            value = str(value)
        if isinstance(value, str) and value.strip():
            public[field] = value.strip()
    if isinstance(item.get("user_spoke_last"), bool):
        public["user_spoke_last"] = item["user_spoke_last"]
    return public


@router.get("/feed")
def get_feed() -> dict[str, Any]:
    with _process_lock():
        state = _read_state_unlocked()
    now = time.time()
    curated_timestamp = _parse_timestamp(state.get("last_curated_at"))
    schedule = state.get("schedule")
    schedule_generated_at = (
        _parse_timestamp(schedule.get("generated_at")) if isinstance(schedule, dict) else 0.0
    )
    schedule_events = (
        schedule.get("events", [])
        if isinstance(schedule, dict)
        and schedule_generated_at
        and now - schedule_generated_at <= SCHEDULE_STALE_SECONDS
        else []
    )
    visible = [
        _public_item(item)
        for item in state["items"]
        if isinstance(item, dict) and not item.get("dismissed") and not _is_expired(item, now)
    ]
    visible.sort(
        key=lambda item: (float(item["score"]), _parse_timestamp(item["created_at"])),
        reverse=True,
    )
    # S1: who/where, recomputed per request from stored rows (cheap, pure).
    profile = _load_profile()
    stored_events = schedule.get("events", []) if isinstance(schedule, dict) else []
    recent_travel = schedule.get("recent_travel", []) if isinstance(schedule, dict) else []
    where = _infer_where(profile, stored_events or [], recent_travel or [], now)
    local_tz = _display_timezone(profile, where)
    operator = {
        field: str(profile.get(field) or "")
        for field in ("name", "preferred_name", "home_city", "home_timezone")
    }
    day_brief = state.get("day_brief")
    day_brief_payload = None
    if isinstance(day_brief, dict):
        brief_generated_at = _parse_timestamp(day_brief.get("generated_at"))
        local_today = datetime.fromtimestamp(now, tz=local_tz).date().isoformat()
        if (
            brief_generated_at
            and now - brief_generated_at <= DAY_BRIEF_STALE_SECONDS
            and str(day_brief.get("local_date") or "") == local_today
        ):
            day_brief_payload = {
                "day_summary": day_brief.get("day_summary"),
                "callouts": day_brief.get("callouts") or [],
                "trip": day_brief.get("trip"),
                "generated_at": day_brief.get("generated_at"),
            }
            # S2: per-meeting prep lines, keyed by the schedule rows' event_key.
            if day_brief.get("prep"):
                day_brief_payload["prep"] = day_brief["prep"]
            if day_brief.get("opener"):
                day_brief_payload["opener"] = day_brief["opener"]
    return {
        "items": _select_served(visible),
        "curated_at": _iso_timestamp(curated_timestamp) if curated_timestamp else None,
        "suggestions": state["suggestions"],
        "suggestions_generated_at": state["suggestions_generated_at"],
        "next_event": state["next_event"],
        "schedule": schedule_events,
        "day_brief": day_brief_payload,
        # S1 (additive): older desktops ignore these.
        "schedule_context": _schedule_context(schedule_events, now, local_tz),
        "operator": operator,
        "where": where,
    }


@router.get("/cron-prompt/{job_id}")
def get_cron_prompt(job_id: str) -> dict[str, Any]:
    from cron.jobs import list_jobs

    job = next(
        (row for row in list_jobs(include_disabled=True) if str(row.get("id") or "") == job_id),
        None,
    )
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job {job_id!r} not found")
    return {"ok": True, "job_id": job_id, "prompt": str(job.get("prompt") or "")}


CRON_RUN_OUTPUT_LIMIT = 2000
_RUN_DOC_TAIL_BYTES = 16 * 1024
_RESPONSE_HEADING_RE = re.compile(r"(?m)^## Response[ \t]*\r?$")


def _cap_run_output(text: str, limit: int = CRON_RUN_OUTPUT_LIMIT) -> str:
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _run_doc_response(path: Path, limit: int = CRON_RUN_OUTPUT_LIMIT) -> str:
    """The text after the LAST ``## Response`` heading of a cron run document.

    Run docs open with the job's full prompt (often tens of KB, and it can quote
    the heading itself), so read backwards from the end in growing windows; the
    response normally sits inside the first one. Docs without a Response section
    (script-mode runs, blocked runs) yield ``""``.
    """
    try:
        with path.open("rb") as handle:
            size = handle.seek(0, os.SEEK_END)
            window = _RUN_DOC_TAIL_BYTES
            while True:
                start = max(0, size - window)
                handle.seek(start)
                text = handle.read(size - start).decode("utf-8", errors="replace")
                if start:
                    # A window can open mid-line; only whole lines may match.
                    text = text.partition("\n")[2]
                match = None
                for match in _RESPONSE_HEADING_RE.finditer(text):
                    pass
                if match is not None:
                    return _cap_run_output(text[match.end():], limit)
                if not start:
                    return ""
                window *= 4
    except OSError:
        return ""


def _run_delivered(job: dict[str, Any]) -> Optional[bool]:
    """Whether the job's latest run reached its delivery target, when the record says so."""
    status = str(job.get("last_status") or "").strip().casefold()
    if status == "delivery_failed" or str(job.get("last_delivery_error") or "").strip():
        return False
    deliver = str(job.get("deliver") or "").strip().casefold()
    if status != "ok" or not deliver or deliver == "local":
        return None
    if deliver == "origin" and not job.get("origin"):
        # Origin-less "origin" jobs are skipped like local ones (nothing is sent).
        return None
    return True


@router.get("/cron-runs/{job_id}")
def get_cron_runs(job_id: str) -> dict[str, Any]:
    from cron.jobs import get_cron_output_dir, list_jobs

    job = next(
        (row for row in list_jobs(include_disabled=True) if str(row.get("id") or "") == job_id),
        None,
    )
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job {job_id!r} not found")

    job_dir = get_cron_output_dir() / str(job["id"])
    try:
        output_files = sorted(
            (path for path in job_dir.glob("*.md") if path.is_file()),
            key=lambda path: path.name,
            reverse=True,
        )[:5]
    except OSError:
        output_files = []

    runs: list[dict[str, Any]] = []
    for path in output_files:
        try:
            started = datetime.strptime(path.stem, "%Y-%m-%d_%H-%M-%S").astimezone().timestamp()
        except ValueError:
            continue
        run: dict[str, Any] = {
            "started_at": _iso_timestamp(started),
            "status": str(job.get("last_status") or "unknown") if not runs else "unknown",
            "error": str(job.get("last_error") or "") if not runs else "",
            "output": _run_doc_response(path),
        }
        delivered = _run_delivered(job) if not runs else None
        if delivered is not None:
            run["delivered"] = delivered
        runs.append(run)

    return {
        "ok": True,
        "job_id": job_id,
        "last_status": job.get("last_status"),
        "last_error": job.get("last_error"),
        "next_run_at": job.get("next_run_at"),
        "runs": runs,
    }


@router.post("/cron-trigger/{job_id}")
def trigger_cron(job_id: str) -> dict[str, Any]:
    from cron.jobs import list_jobs
    from tools.cronjob_tools import _execute_job_now

    job = next(
        (row for row in list_jobs(include_disabled=True) if str(row.get("id") or "") == job_id),
        None,
    )
    if job is None:
        raise HTTPException(status_code=404, detail=f"cron job {job_id!r} not found")

    outcome = _execute_job_now(job)
    if not outcome.get("claimed"):
        status = "skipped"
    else:
        status = "ok" if outcome.get("success") else "error"
    response = {"ok": bool(outcome.get("claimed") and outcome.get("success")), "status": status}
    if outcome.get("error"):
        response["error"] = str(outcome["error"])
    return response


@router.post("/dismiss")
def dismiss_item(payload: ItemRequest) -> dict[str, Any]:
    with _process_lock():
        state = _read_state_unlocked()
        item = next((row for row in state["items"] if row.get("id") == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=f"feed item {payload.id!r} not found")
        item["dismissed"] = True
        decisions = state.setdefault("decisions", {})
        decision = decisions.setdefault(payload.id, {})
        decision["dismissed"] = True
        decision["updated_at"] = _iso_timestamp(time.time())
        decision["source_activity_at"] = item.get("source_activity_at") or item.get("created_at")
        open_loop_key = str(item.get("open_loop_key") or "").strip()
        if open_loop_key:
            suppressed = [
                str(key or "").strip()
                for key in state.get("open_loop_suppressed") or []
                if str(key or "").strip() and str(key or "").strip() != open_loop_key
            ]
            suppressed.append(open_loop_key)
            state["open_loop_suppressed"] = suppressed[-MAX_OPEN_LOOP_SUPPRESSIONS:]
        state["decisions"] = _prune_decisions(decisions)
        _write_state_unlocked(state)
    return {"ok": True, "id": payload.id, "dismissed": True}


@router.post("/undismiss")
def undismiss_item(payload: ItemRequest) -> dict[str, Any]:
    with _process_lock():
        state = _read_state_unlocked()
        item = next((row for row in state["items"] if row.get("id") == payload.id), None)
        decisions = state.setdefault("decisions", {})
        decision = decisions.setdefault(payload.id, {})
        decision["dismissed"] = False
        decision["updated_at"] = _iso_timestamp(time.time())
        if item is not None:
            item["dismissed"] = False
            decision["source_activity_at"] = item.get("source_activity_at") or item.get("created_at")
        state["decisions"] = _prune_decisions(decisions)
        _write_state_unlocked(state)
    return {
        "ok": True,
        "id": payload.id,
        "dismissed": False,
        "restored": item is not None,
    }


def _kanban_connect() -> Any:
    """A closing kanban DB connection, wherever this Hermes keeps the helper.

    Hermes moved ``connect_closing`` to ``hermes_cli.kanban_db_connect`` and
    dropped the ``kanban_db`` alias in v0.21.x; older installs only have the
    original. Every kanban route opens its connection here (U13).
    """
    try:
        from hermes_cli import kanban_db_connect
    except ImportError:
        kanban_db_connect = None
    connect_closing = getattr(kanban_db_connect, "connect_closing", None)
    if connect_closing is None:
        from hermes_cli import kanban_db

        connect_closing = kanban_db.connect_closing
    return connect_closing()


def _create_kanban_task(item: dict[str, Any]) -> dict[str, Any]:
    from hermes_cli import kanban_db

    with _kanban_connect() as conn:
        task_id = kanban_db.create_task(
            conn,
            title=str(item["headline"]),
            body=(
                f"{item['summary']}\n\n"
                f"Source: {item['source']}\n"
                f"Town Feed item: {item['id']}"
            ),
            created_by="town-feed",
            priority=max(0, min(3, int(float(item.get("score", 0)) // 25))),
            triage=True,
            idempotency_key=f"town-feed:{item['id']}",
        )
        task = kanban_db.get_task(conn, task_id)
        return {
            "id": task_id,
            "title": task.title if task else item["headline"],
            "status": task.status if task else "triage",
        }


@router.post("/task")
def create_composer_task(payload: TaskRequest) -> dict[str, Any]:
    from hermes_cli import kanban_db

    body = f"{payload.prompt}\n\nSource: town-composer"
    if payload.session_id:
        body += f"\nSession: session:{payload.session_id}"

    try:
        with _kanban_connect() as conn:
            task_id = kanban_db.create_task(
                conn,
                title=payload.title,
                body=body,
                created_by="town-feed",
                triage=True,
                session_id=payload.session_id,
                idempotency_key=f"town-composer:{payload.client_key}",
            )
            task = kanban_db.get_task(conn, task_id)
            task_ref = {
                "id": task_id,
                "title": task.title if task else payload.title,
                "status": task.status if task else "triage",
            }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"kanban task creation failed: {exc}") from exc

    return {"ok": True, "task": task_ref}


@router.post("/promote-to-task")
def promote_to_task(payload: ItemRequest) -> dict[str, Any]:
    with _process_lock():
        state = _read_state_unlocked()
        item = next((row for row in state["items"] if row.get("id") == payload.id), None)
        if item is None:
            raise HTTPException(status_code=404, detail=f"feed item {payload.id!r} not found")
        existing = (state.get("decisions") or {}).get(payload.id) or {}
        if existing.get("promoted") and isinstance(existing.get("task"), dict):
            return {"ok": True, "id": payload.id, "task": existing["task"], "already_promoted": True}

    try:
        task_ref = _create_kanban_task(item)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"kanban task creation failed: {exc}") from exc

    with _process_lock():
        state = _read_state_unlocked()
        current = next((row for row in state["items"] if row.get("id") == payload.id), None)
        if current is None:
            raise HTTPException(status_code=409, detail="feed item changed while task was being created")
        current["promoted"] = True
        current["task"] = task_ref
        decisions = state.setdefault("decisions", {})
        decision = decisions.setdefault(payload.id, {})
        decision.update(
            {
                "promoted": True,
                "task": task_ref,
                "updated_at": _iso_timestamp(time.time()),
                "source_activity_at": current.get("source_activity_at") or current.get("created_at"),
            }
        )
        state["decisions"] = _prune_decisions(decisions)
        _write_state_unlocked(state)
    return {"ok": True, "id": payload.id, "task": task_ref, "already_promoted": False}


@router.post("/kanban/complete")
def complete_kanban_task(payload: ItemRequest) -> dict[str, Any]:
    from hermes_cli import kanban_db

    with _kanban_connect() as conn:
        task = kanban_db.get_task(conn, payload.id)
        if task is None:
            raise HTTPException(status_code=404, detail=f"kanban task {payload.id!r} not found")
        if task.status in {"done", "archived"}:
            raise HTTPException(
                status_code=409,
                detail=f"kanban task {payload.id!r} is already {task.status}",
            )

        if task.status == "triage":
            kanban_db.specify_triage_task(conn, payload.id)
            task = kanban_db.get_task(conn, payload.id)

        if task is not None and task.status == "todo":
            promoted, refusal = kanban_db.promote_task(
                conn,
                payload.id,
                actor="town-dashboard",
                reason="Completion requested from Town dashboard",
            )
            if not promoted:
                raise HTTPException(
                    status_code=409,
                    detail=f"kanban task {payload.id!r} cannot be completed: {refusal or 'promotion refused'}",
                )
            task = kanban_db.get_task(conn, payload.id)

        if task is None:
            raise HTTPException(status_code=404, detail=f"kanban task {payload.id!r} not found")
        if task.status not in {"running", "ready", "blocked"}:
            raise HTTPException(
                status_code=409,
                detail=f"kanban task {payload.id!r} cannot be completed from {task.status}",
            )

        completed = kanban_db.complete_task(
            conn,
            payload.id,
            result="Completed from Town dashboard",
        )
        if not completed:
            current = kanban_db.get_task(conn, payload.id)
            if current is None:
                raise HTTPException(status_code=404, detail=f"kanban task {payload.id!r} not found")
            raise HTTPException(
                status_code=409,
                detail=f"kanban task {payload.id!r} cannot be completed from {current.status}",
            )

        return {"ok": True, "id": payload.id, "status": "done"}


@router.get("/kanban")
def get_kanban_summary() -> dict[str, Any]:
    from hermes_cli import kanban_db

    with _kanban_connect() as conn:
        tasks = kanban_db.list_tasks(conn, include_archived=False)
        task_ids = [task.id for task in tasks]
        updated_at_by_task: dict[str, int] = {}
        if task_ids:
            placeholders = ",".join("?" for _ in task_ids)
            updated_at_by_task = {
                str(row["task_id"]): int(row["updated_at"])
                for row in conn.execute(
                    "SELECT task_id, MAX(created_at) AS updated_at "
                    f"FROM task_events WHERE task_id IN ({placeholders}) GROUP BY task_id",
                    task_ids,
                )
                if row["updated_at"] is not None
            }
        columns: dict[str, list[dict[str, Any]]] = {name: [] for name in BOARD_COLUMNS}
        for task in tasks:
            data = asdict(task)
            body = str(data.get("body") or "")
            session_matches = _SESSION_REF_RE.findall(body)
            data["body"] = _compact(body, 240)
            # Prefer the structured column; body scraping only covers legacy
            # cards whose linkage predates create_task(session_id=...).
            data["session_id"] = task.session_id or (session_matches[-1] if session_matches else None)
            data["updated_at"] = max(
                timestamp
                for timestamp in (
                    updated_at_by_task.get(task.id),
                    task.completed_at,
                    task.started_at,
                    task.created_at,
                )
                if timestamp is not None
            )
            column = task.status if task.status in columns else "todo"
            columns[column].append(data)
        latest_event = conn.execute(
            "SELECT COALESCE(MAX(id), 0) AS latest FROM task_events"
        ).fetchone()
        return {
            "columns": [
                {"name": name, "tasks": columns[name]} for name in BOARD_COLUMNS
            ],
            "latest_event_id": int(latest_event["latest"]),
            "now": int(time.time()),
        }


CURATE_JOB_NAME = "town-feed-curate"
CURATE_JOB_SCRIPT = "town-curate.py"
_CURATE_TRIGGER_LOCK = threading.Lock()
_curate_trigger_thread: Optional[threading.Thread] = None


def _find_curate_job(jobs: Any) -> Optional[dict[str, Any]]:
    matches = [
        job
        for job in jobs or []
        if isinstance(job, dict)
        and (
            str(job.get("name") or "").strip().casefold() == CURATE_JOB_NAME
            or Path(str(job.get("script") or "")).name == CURATE_JOB_SCRIPT
        )
    ]
    # Prefer a live job when a paused duplicate lingers.
    matches.sort(key=lambda job: job.get("enabled") is False)
    return matches[0] if matches else None


def _run_curate_job(job: dict[str, Any], execute: Any) -> None:
    try:
        outcome = execute(job)
        if isinstance(outcome, dict) and outcome.get("error"):
            import logging

            logging.getLogger(__name__).warning("town-feed curate run: %s", outcome["error"])
    except Exception:
        import logging

        logging.getLogger(__name__).exception("town-feed curate run failed")


@router.post("/curate")
def curate_feed() -> dict[str, Any]:
    # Never curate inline: dashboard routes have no LLM, so run_curator(llm=None)
    # would overwrite LLM headlines with heuristic ones, and an LLM pass outlives
    # the renderer's ~15 s IPC timeout. Fire the curate cron job (same
    # _execute_job_now path as /cron-trigger) on a thread; the desktop polls /feed
    # until curated_at moves past `since`.
    global _curate_trigger_thread
    try:
        from cron.jobs import list_jobs
        from tools.cronjob_tools import _execute_job_now

        job = _find_curate_job(list_jobs(include_disabled=True))
    except Exception as exc:
        return {"ok": False, "error": f"Could not read routines: {exc}"}
    if job is None:
        return {
            "ok": False,
            "error": f"No {CURATE_JOB_NAME} routine is scheduled; reinstall the town-feed plugin.",
        }
    if job.get("enabled") is False:
        # A paused job would lose the manual-run claim; say so instead of a silent timeout.
        return {"ok": False, "error": f"The {CURATE_JOB_NAME} routine is paused; resume it to refresh."}

    with _process_lock():
        state = _read_state_unlocked()
    curated_timestamp = _parse_timestamp(state.get("last_curated_at"))
    since = _iso_timestamp(curated_timestamp) if curated_timestamp else None

    with _CURATE_TRIGGER_LOCK:
        running = _curate_trigger_thread
        if running is not None and running.is_alive():
            return {"ok": True, "triggered": False, "since": since}
        thread = threading.Thread(
            target=_run_curate_job,
            args=(job, _execute_job_now),
            name="town-feed-curate-trigger",
            daemon=True,
        )
        thread.start()
        _curate_trigger_thread = thread
    return {"ok": True, "triggered": True, "since": since}
