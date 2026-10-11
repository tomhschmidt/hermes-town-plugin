"""Telegram attribution helpers shared by the Telegram digest and the Town
Feed scanner (``telegram-action-items-v2.py`` and ``town-telegram-scan.py``).

Deployed next to both scripts in ``~/.hermes/scripts/``; each script loads
this file by path relative to its own ``__file__``. Pure functions, stdlib
only, no I/O: callers pass daemon payloads (the R1a ``/me``, ``/chats`` and
``/messages`` shapes, with older payloads degrading gracefully), the operator
identity, and the timezone used for rendering.

Attribution policy (deterministic, before any LLM):
- A message is the operator's when ``is_outgoing`` is true or its
  ``sender_user_id`` is the operator's user id — never by display name.
- DMs are always eligible. A group is eligible only when someone else replies
  to the operator, mentions them (user id / @username), or names their first
  name as a whole word — or the operator made a commitment there that nobody
  has replied to. Channels, channel posts inside groups, and bot DMs are
  excluded.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone, tzinfo
from typing import Any, Iterable, Optional

MEDIA_LABELS = {
    "photo": "[photo]",
    "video": "[video]",
    "voice": "[voice]",
    "video_note": "[video note]",
    "sticker": "[sticker]",
    "animation": "[GIF]",
    "document": "[document]",
    "audio": "[audio]",
    "poll": "[poll]",
    "location": "[location]",
    "contact": "[contact]",
}

# The operator's own promises: "I'll", "I will", "will send/check/do",
# "let me" (but not "let me know", which is a request).
_COMMITMENT = re.compile(
    r"(?<!\w)(?:i['’]ll|i will|will (?:send|check|do)|let me(?!\s+know))(?!\w)",
    re.IGNORECASE,
)


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def clip(text: str, limit: Optional[int]) -> str:
    if limit is None or len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "…"


def as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


class Operator:
    """Who "you" is. ``user_id`` is None only when ``/me`` was unavailable."""

    __slots__ = ("user_id", "first_name", "last_name", "usernames")

    def __init__(
        self,
        user_id: Optional[int],
        first_name: str = "",
        last_name: str = "",
        usernames: Iterable[str] = (),
    ) -> None:
        self.user_id = user_id
        self.first_name = first_name
        self.last_name = last_name
        self.usernames = tuple(usernames)

    @property
    def name(self) -> str:
        return " ".join(part for part in (self.first_name, self.last_name) if part) or "the operator"

    @property
    def call_name(self) -> str:
        return self.first_name or self.name

    @property
    def username(self) -> str:
        return self.usernames[0] if self.usernames else ""


def resolve_operator(me: Any, fallback_name: str = "") -> Operator:
    """Identity from a daemon ``/me`` payload, else from ``fallback_name``."""
    if isinstance(me, dict):
        user_id = as_int(me.get("user_id"))
        if user_id is not None and user_id <= 0:
            user_id = None
        first = clean_text(me.get("first_name"))
        last = clean_text(me.get("last_name"))
        if user_id is not None or first:
            listed = me.get("usernames") if isinstance(me.get("usernames"), list) else []
            usernames: list[str] = []
            for value in [me.get("username"), *listed]:
                handle = clean_text(value).lstrip("@").lower()
                if handle and handle not in usernames:
                    usernames.append(handle)
            return Operator(user_id, first, last, usernames)
    first, _, last = clean_text(fallback_name).partition(" ")
    return Operator(None, first, last.strip())


def chat_kind(chat_id: int, chat: Optional[dict[str, Any]]) -> str:
    """``dm``, ``group``, or ``channel`` from the /chats ``type``, else the id.

    TDLib ids: users (private chats) are positive, supergroups and channels
    are ``<= -1000000000000``, other negatives are basic groups. Without a
    declared type a supergroup-range id is treated as a group: a channel is
    only "proven" by its posts, which ``analyze_chat`` drops.
    """
    declared = clean_text((chat or {}).get("type")).lower()
    if declared in ("private", "secret"):
        return "dm"
    if declared in ("group", "supergroup"):
        return "group"
    if declared == "channel":
        return "channel"
    return "dm" if chat_id > 0 else "group"


def is_operator_message(message: dict[str, Any], operator: Operator) -> bool:
    if message.get("is_outgoing") is True:
        return True
    sender = as_int(message.get("sender_user_id"))
    return sender is not None and operator.user_id is not None and sender == operator.user_id


def is_chat_post(message: dict[str, Any]) -> bool:
    """Posted by a chat (channel post, anonymous admin) rather than a person."""
    return as_int(message.get("sender_user_id")) is None and message.get("is_outgoing") is not True


def message_content(message: dict[str, Any]) -> str:
    """Text, or ``[photo] caption`` style for media; "" when nothing to show."""
    text = clean_text(message.get("text"))
    if text:
        return text
    caption = clean_text(message.get("caption"))
    label = MEDIA_LABELS.get(clean_text(message.get("content_type")).lower())
    if label:
        return f"{label} {caption}".strip()
    return caption


def _message_id(message: dict[str, Any]) -> Optional[int]:
    value = message.get("id")
    return as_int(value if value is not None else message.get("message_id"))


def _name_pattern(first_name: str) -> Optional[re.Pattern[str]]:
    if not first_name:
        return None
    return re.compile(rf"(?<!\w){re.escape(first_name)}(?!\w)", re.IGNORECASE)


def mentions_operator(
    message: dict[str, Any], operator: Operator, name_pattern: Optional[re.Pattern[str]] = None
) -> bool:
    """@mention by user id or username, or the first name as a whole word."""
    if operator.user_id is not None:
        ids = message.get("mention_user_ids")
        if isinstance(ids, list) and any(as_int(value) == operator.user_id for value in ids):
            return True
    text = f"{message.get('text') or ''} {message.get('caption') or ''}"
    if operator.usernames:
        handles = message.get("mention_usernames")
        if isinstance(handles, list) and any(
            clean_text(value).lstrip("@").lower() in operator.usernames for value in handles
        ):
            return True
        # Older daemons carry no entities: fall back to a literal @handle.
        for handle in operator.usernames:
            if re.search(rf"(?<![\w@])@{re.escape(handle)}(?!\w)", text, re.IGNORECASE):
                return True
    if name_pattern is None:
        name_pattern = _name_pattern(operator.first_name)
    return bool(name_pattern and name_pattern.search(text))


def reply_info(
    message: dict[str, Any], by_id: dict[int, dict[str, Any]], operator: Operator
) -> Optional[tuple[int, bool, str]]:
    """(target id, replies to the operator, label) or None for a non-reply."""
    target_id = as_int(message.get("reply_to_message_id"))
    if target_id is None:
        return None
    target = by_id.get(target_id)
    if target is not None and is_operator_message(target, operator):
        return target_id, True, "you"
    reply_sender = as_int(message.get("reply_to_sender_user_id"))
    if reply_sender is not None and operator.user_id is not None and reply_sender == operator.user_id:
        return target_id, True, "you"
    label = (
        clean_text(message.get("reply_to_sender_name"))
        or clean_text((target or {}).get("sender_name"))
        or "an earlier message"
    )
    return target_id, False, label


def _order(view: dict[str, Any]) -> tuple[int, int]:
    return view["date"], view["id"]


def analyze_chat(
    chat_id: int,
    chat: Optional[dict[str, Any]],
    raw_messages: Optional[Iterable[Any]],
    operator: Operator,
    cutoff_ts: int = 0,
) -> Optional[dict[str, Any]]:
    """Attribution analysis for one chat's window.

    Returns None for excluded chats (channels, bot DMs) and chats with nothing
    renderable in the window. Messages come back oldest first, ordered by
    ``(date, id)`` — daemons return newest first.
    """
    chat = chat if isinstance(chat, dict) else {}
    kind = chat_kind(chat_id, chat)
    if kind == "channel" or chat.get("is_bot") is True:
        return None
    raw = [message for message in raw_messages or [] if isinstance(message, dict)]
    by_id: dict[int, dict[str, Any]] = {}
    for message in raw:
        message_id = _message_id(message)
        if message_id is not None:
            by_id.setdefault(message_id, message)

    name_pattern = _name_pattern(operator.first_name)
    views: list[dict[str, Any]] = []
    for message in raw:
        message_id = _message_id(message)
        date = message.get("date")
        if message_id is None or isinstance(date, bool) or not isinstance(date, int):
            continue
        if date < cutoff_ts or (kind == "group" and is_chat_post(message)):
            continue
        content = message_content(message)
        if not content:
            continue
        own = is_operator_message(message, operator)
        reply = reply_info(message, by_id, operator)
        views.append(
            {
                "id": message_id,
                "date": date,
                "own": own,
                "sender": clean_text(message.get("sender_name"))
                or (operator.name if own else "Unknown"),
                "content": content,
                "reply_to_id": reply[0] if reply else None,
                "reply_to_you": bool(reply and reply[1]),
                "reply_label": reply[2] if reply else "",
                "mentions_you": not own and mentions_operator(message, operator, name_pattern),
            }
        )
    if not views:
        return None
    views.sort(key=_order)

    is_dm = kind == "dm"
    for view in views:
        view["addresses_you"] = not view["own"] and (
            is_dm or view["reply_to_you"] or view["mentions_you"]
        )
    addressed = is_dm or any(view["addresses_you"] for view in views)
    open_commitment = any(
        view["own"]
        and _COMMITMENT.search(view["content"])
        and not any(
            not later["own"] and later["reply_to_id"] == view["id"] for later in views[index + 1 :]
        )
        for index, view in enumerate(views)
    )
    last_addressed = max((view for view in views if view["addresses_you"]), key=_order, default=None)
    last_own = max((view for view in views if view["own"]), key=_order, default=None)
    title = clean_text(chat.get("title")) or str(chat_id)
    counterpart = ""
    if is_dm:
        counterpart = next(
            (view["sender"] for view in views if not view["own"] and view["sender"] != "Unknown"),
            title,
        )
    return {
        "chat_id": chat_id,
        "title": title,
        "kind": kind,
        "counterpart": counterpart,
        "messages": views,
        "addressed": addressed,
        "open_commitment": bool(open_commitment),
        "eligible": addressed or bool(open_commitment),
        "last_addressed": last_addressed,
        "last_addressed_at": last_addressed["date"] if last_addressed else None,
        "operator_last_spoke_at": last_own["date"] if last_own else None,
        "operator_replied_after_ask": bool(
            last_addressed and last_own and _order(last_own) > _order(last_addressed)
        ),
        "user_spoke_last": views[-1]["own"],
    }


def to_local(value: Any, tz: Optional[tzinfo] = None) -> datetime:
    """A unix timestamp or datetime in ``tz`` (None: the system timezone)."""
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    else:
        moment = datetime.fromtimestamp(value, timezone.utc)
    return moment.astimezone(tz) if tz is not None else moment.astimezone()


def describe_now(now: Any, tz: Optional[tzinfo] = None) -> str:
    """e.g. ``Sunday 2026-10-11 10:30 America/New_York (EDT, UTC-04:00)``."""
    local = to_local(now, tz)
    offset = local.strftime("%z")
    offset_label = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    label = ", ".join(part for part in (local.strftime("%Z"), offset_label) if part)
    zone = str(getattr(local.tzinfo, "key", "") or "")
    zone_label = f"{zone} ({label})" if zone and label else (zone or label)
    return f"{local.strftime('%A %Y-%m-%d %H:%M')} {zone_label}".strip()


def render_message(
    view: dict[str, Any],
    *,
    tz: Optional[tzinfo] = None,
    with_time: bool = True,
    limit: Optional[int] = None,
) -> str:
    """One line with addressing markers.

    ``[YOU] text`` for the operator; ``Name ↩ <replied-to | you> @you: text``
    for everyone else (markers only when they apply).
    """
    content = clip(view["content"], limit)
    if view["own"]:
        head = "[YOU]"
        if view["reply_label"]:
            head += f" ↩ {view['reply_label']}:"
    else:
        head = view["sender"]
        if view["reply_label"]:
            head += f" ↩ {view['reply_label']}"
        if view["mentions_you"]:
            head += " @you"
        head += ":"
    line = f"{head} {content}"
    if with_time:
        line = f"[{to_local(view['date'], tz).strftime('%Y-%m-%d %H:%M')}] {line}"
    return line
