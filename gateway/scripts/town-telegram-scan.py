#!/usr/bin/env python3
"""Scan recent Telegram threads and emit Town Feed candidates.

Exit codes mirror the Town Gmail scanner where applicable:
  0: completed successfully
  2: invalid config or unreadable/unwritable state
  3: required runtime import unavailable
  5: Telegram daemon API failed
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import sys
import tempfile
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator


HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
STATE_PATH = HERMES_HOME / "town-feed" / "telegram-state.json"
LOCK_PATH = HERMES_HOME / "town-feed" / ".telegram-state.lock"
DAEMON_URL = "http://127.0.0.1:18784"
LOOKBACK = timedelta(hours=48)
MAX_CANDIDATES = 15
STATE_VERSION = 1
# Fallback operator name, used only when the daemon's /me is unavailable.
# Attribution is by Telegram user id (/me), never by display name.
OWNER_NAME = os.environ.get("TOWN_OWNER_NAME", "").strip()
MAX_BODY_MESSAGES = 12
MAX_BODY_CHARS = 1200

# Keep this collection policy in sync with the live source of truth:
# ~/.hermes/scripts/telegram-action-items-v2.py. It is mirrored here rather
# than imported so this every-10m scanner cannot affect the daily cron.
SKIP_CHAT_NAMES = {
    "Wu Blockchain News",
    "aggrnews",
    "Club Lido",
    "dYdX Official Announcements",
    "Bybit API Discussion",
    "DODO Official Community",
    "Handshake (HNS)",
    "Telegram",
    "BotFather",
    "Spambot",
    "Nicegram",
}


class ConfigError(RuntimeError):
    """The scanner's local configuration is invalid."""


class TelegramAPIError(RuntimeError):
    """The local Telegram daemon could not complete a scan."""


def _load_common() -> Any:
    """Load town_telegram_common.py, deployed next to this script."""
    path = Path(__file__).resolve().with_name("town_telegram_common.py")
    spec = importlib.util.spec_from_file_location("town_telegram_common", path)
    if spec is None or spec.loader is None or not path.is_file():
        raise ImportError(f"missing {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


try:
    tg: Any = _load_common()
    _COMMON_ERROR: ImportError | None = None
except ImportError as _exc:  # surfaced as exit 3 by _run()
    tg = None
    _COMMON_ERROR = _exc


def _eprint(*args: Any) -> None:
    print(*args, file=sys.stderr)


def load_env_var(name: str, env_path: Path) -> str:
    """Read one shell-style KEY=VALUE entry without requiring python-dotenv."""
    if not env_path.exists():
        return ""
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key.strip() != name:
            continue
        return value.strip().strip("'\"")
    return ""


def get_bot_token() -> str:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token

    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    token = load_env_var("TELEGRAM_BOT_TOKEN", hermes_home / ".env")
    if token:
        return token

    raise RuntimeError(f"TELEGRAM_BOT_TOKEN is missing from environment and {hermes_home / '.env'}")


def _default_state() -> dict[str, Any]:
    return {"version": STATE_VERSION, "watermarks": {}}


def _normalize_state(raw: Any) -> dict[str, Any]:
    watermarks: dict[str, str] = {}
    if isinstance(raw, dict) and isinstance(raw.get("watermarks"), dict):
        for raw_chat_id, raw_message_id in raw["watermarks"].items():
            chat_id = str(raw_chat_id).strip()
            message_id = str(raw_message_id).strip()
            if chat_id and message_id:
                watermarks[chat_id] = message_id
    return {"version": STATE_VERSION, "watermarks": watermarks}


def _load_state() -> dict[str, Any]:
    if not STATE_PATH.exists():
        return _default_state()
    try:
        raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"could not read {STATE_PATH}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"invalid state in {STATE_PATH}: expected a JSON object")
    return _normalize_state(raw)


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        _normalize_state(state), ensure_ascii=False, indent=2, sort_keys=True
    ) + "\n"
    fd, temp_name = tempfile.mkstemp(
        dir=str(STATE_PATH.parent), prefix=".telegram-state-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, STATE_PATH)
        os.chmod(STATE_PATH, 0o600)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@contextmanager
def _state_lock() -> Iterator[None]:
    try:
        import fcntl
    except ImportError as exc:  # pragma: no cover - Hermes cron targets POSIX
        raise RuntimeError(f"state locking is unavailable: {exc}") from exc

    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _get_json(url: str, timeout: int) -> dict[str, Any]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.load(response)
    except Exception as exc:
        raise TelegramAPIError(f"GET {url} failed: {exc}") from exc
    if not isinstance(payload, dict):
        raise TelegramAPIError(f"GET {url} returned a non-object JSON payload")
    return payload


def get_muted_chats() -> set[int]:
    payload = _get_json(f"{DAEMON_URL}/muted-chats", timeout=15)
    return {
        chat_id
        for item in payload.get("muted_chats", [])
        if isinstance(item, dict) and isinstance((chat_id := item.get("id")), int)
    }


def get_me() -> dict[str, Any] | None:
    """The session owner from the daemon's /me, or None (fallback identity)."""
    try:
        return _get_json(f"{DAEMON_URL}/me", timeout=15)
    except TelegramAPIError as exc:
        _eprint(f"town-telegram-scan: /me unavailable, using fallback identity: {exc}")
        return None


def get_chats(bot_user_id: int) -> dict[int, dict[str, Any]]:
    """Daemon /chats entries by id (title stripped), minus muted/self/skipped."""
    muted = get_muted_chats()
    payload = _get_json(f"{DAEMON_URL}/chats", timeout=30)
    chats: dict[int, dict[str, Any]] = {}
    for item in payload.get("chats", []):
        if not isinstance(item, dict):
            continue
        chat_id = item.get("id")
        title = str(item.get("title") or "").strip()
        if (
            not isinstance(chat_id, int)
            or not title
            or chat_id in muted
            or chat_id == bot_user_id
            or title in SKIP_CHAT_NAMES
        ):
            continue
        chats[chat_id] = {**item, "title": title}
    return chats


def get_messages(chat_id: int, cutoff_ts: int) -> list[dict[str, Any]]:
    query = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "limit": 100,
            "from_message_id": 0,
            "cutoff_ts": cutoff_ts,
        }
    )
    payload = _get_json(f"{DAEMON_URL}/messages?{query}", timeout=60)
    return [item for item in payload.get("messages", []) if isinstance(item, dict)]


def _gist(text: Any, limit: int | None = None) -> str:
    gist = re.sub(r"\s+", " ", str(text or "")).strip()
    if limit is None or len(gist) <= limit:
        return gist
    return gist[: max(0, limit - 1)].rstrip() + "…"


def _content_hash(chat_id: int, last_message_id: str) -> str:
    identity = json.dumps(
        [str(chat_id), last_message_id], ensure_ascii=False, separators=(",", ":")
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _iso_timestamp(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _thread_body(thread: dict[str, Any], operator: Any) -> str:
    """Bounded conversation context for the curator's judgment layer.

    " | " separates messages because the curator's whitespace compaction
    collapses newlines; the separator must survive it. Lines carry the
    addressing markers ([YOU], ↩ <name>/you, @you). When the newest message
    addressed to the operator is older than the tail, it leads the body.
    """
    messages = thread["messages"]
    tail = messages[-MAX_BODY_MESSAGES:]
    anchor = thread.get("last_addressed")
    lead: list[str] = []
    if anchor is not None and all(anchor is not message for message in tail):
        lead = [tg.render_message(anchor, with_time=False, limit=180), "…"]
    lines = [tg.render_message(message, with_time=False, limit=180) for message in tail]
    while lines and len(" | ".join(lead + lines)) > MAX_BODY_CHARS:
        lines.pop(0)
    if not lines:
        return ""
    kind = "DM" if thread["kind"] == "dm" else "group"
    header = f"[chat: {thread['title']} ({kind}); you are {operator.name}]"
    return f"{header} {' | '.join(lead + lines)}"


def _candidate(chat_id: int, thread: dict[str, Any], operator: Any) -> dict[str, Any] | None:
    chat_name = thread["title"]
    messages = thread["messages"]
    newest = messages[-1]
    last_message_id = str(newest["id"])
    last_timestamp = newest["date"]
    last_gist = _gist(newest["content"], 60)
    if not last_message_id or not last_gist:
        return None

    recent_gists = [
        f"{'You' if message['own'] else _gist(message['sender'])}: {_gist(message['content'])}"
        for message in messages[-3:]
    ]

    last_sender = _gist(newest["sender"], 40)
    # Attribute the actual speaker: "Bob: I'm free tomorrow" when the
    # operator wrote it misattributes the quote to the chat's namesake.
    speaker = "You" if newest["own"] else last_sender
    if speaker and speaker.casefold() != chat_name.casefold():
        headline = f"{chat_name} — {speaker}: {last_gist}"
    else:
        headline = f"{chat_name}: {last_gist}"
    body = _thread_body(thread, operator)

    candidate: dict[str, Any] = {
        "headline": headline,
        "summary": "\n".join(recent_gists),
        "source": f"telegram:{chat_id}",
        "created_at": _iso_timestamp(last_timestamp),
        "content_hash": _content_hash(chat_id, last_message_id),
        "chat_name": chat_name,
        "chat_type": thread["kind"],
        "last_sender": last_sender,
        "user_spoke_last": bool(newest["own"]),
        "addressed_to_user": bool(thread["addressed"]),
        "operator_replied_after_ask": bool(thread["operator_replied_after_ask"]),
    }
    if body:
        candidate["body"] = body
    return candidate


def _scan(
    now: datetime | None = None,
) -> tuple[list[dict[str, Any]], dict[str, str], dict[str, str]]:
    """(eligible candidates newest first, their newest message ids, newest
    message ids of chats that were scanned but are not eligible)."""
    if tg is None:
        raise ImportError(f"town_telegram_common unavailable: {_COMMON_ERROR}")
    token = get_bot_token()
    try:
        bot_user_id = int(token.split(":", 1)[0])
    except (TypeError, ValueError) as exc:
        raise ConfigError("TELEGRAM_BOT_TOKEN has an invalid bot user ID") from exc

    now = now or datetime.now(timezone.utc)
    cutoff_ts = int((now - LOOKBACK).timestamp())
    operator = tg.resolve_operator(get_me(), OWNER_NAME)
    chats = get_chats(bot_user_id)
    # The operator's own Saved Messages chat has their user id.
    chats.pop(operator.user_id, None)
    active: list[tuple[int, int, dict[str, Any]]] = []
    newest_by_chat: dict[str, str] = {}
    ineligible: dict[str, str] = {}

    for chat_id, chat in chats.items():
        thread = tg.analyze_chat(
            chat_id, chat, get_messages(chat_id, cutoff_ts), operator, cutoff_ts
        )
        if thread is None:
            continue
        newest = thread["messages"][-1]
        if not thread["eligible"]:
            ineligible[str(chat_id)] = str(newest["id"])
            continue
        candidate = _candidate(chat_id, thread, operator)
        if candidate is None:
            continue
        newest_by_chat[str(chat_id)] = str(newest["id"])
        active.append((newest["date"], chat_id, candidate))

    active.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in active], newest_by_chat, ineligible


def _run(now: datetime | None = None) -> tuple[int, list[dict[str, Any]]]:
    try:
        with _state_lock():
            state = _load_state()
            candidates, newest_by_chat, ineligible = _scan(now)
            watermarks = state["watermarks"]
            reportable = [
                candidate
                for candidate in candidates
                if watermarks.get(candidate["source"].split(":", 1)[1])
                != newest_by_chat[candidate["source"].split(":", 1)[1]]
            ][:MAX_CANDIDATES]
            # Advance only what this run reported (plus ineligible chats): a
            # chat cut by MAX_CANDIDATES keeps its watermark and is reported
            # next run.
            for candidate in reportable:
                chat_key = candidate["source"].split(":", 1)[1]
                watermarks[chat_key] = newest_by_chat[chat_key]
            watermarks.update(ineligible)
            try:
                _save_state(state)
            except Exception as exc:
                raise ConfigError(f"could not save {STATE_PATH}: {exc}") from exc
            return 0, reportable
    except ConfigError as exc:
        _eprint(f"town-telegram-scan: config/state failure: {exc}")
        return 2, []
    except (ImportError, ModuleNotFoundError) as exc:
        _eprint(f"town-telegram-scan: required import unavailable: {exc}")
        return 3, []
    except TelegramAPIError as exc:
        _eprint(f"town-telegram-scan: Telegram scan failed: {exc}")
        return 5, []
    except Exception as exc:
        _eprint(f"town-telegram-scan: state/config failure: {exc}")
        return 2, []


def main() -> int:
    code, candidates = _run()
    if candidates:
        print(json.dumps({"candidates": candidates}, ensure_ascii=False, separators=(",", ":")))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
