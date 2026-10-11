"""Who the operator is, for the Town gateway plugin and its cron scripts.

Source of truth is ``$HERMES_HOME/town-feed/operator.json`` (user-owned,
never in the repo). Missing fields fall back to the structured lines of
``$HERMES_HOME/memories/USER.md`` (``**Name:**``, ``**What to call them:**``,
``**Timezone:**``, ``**Telegram:**``), then (timezone only) to Hermes
``config.yaml`` ``timezone``, then to the system timezone. Only
those structured fields are read: free-text memory never enters the profile.

Loading is plain file reading: it never raises and never touches the
network. The mail predicates below apply the profile to Gmail addressing
(self-exhaust and awaiting-reply junk) for both the scanner and the curator.
"""

from __future__ import annotations

import email.utils
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Optional

OPERATOR_FILE = Path("town-feed") / "operator.json"
USER_MEMORY_FILE = Path("memories") / "USER.md"
MAX_USER_MEMORY_BYTES = 256 * 1024
MAX_FIELD_CHARS = 120

_USER_MD_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*([^*:]+):\*\*\s*(.*?)\s*$")
_USER_MD_FIELDS = {
    "name": "name",
    "what to call them": "preferred_name",
    "timezone": "home_timezone",
    "telegram": "telegram",
}
_TELEGRAM_HANDLE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
_FORWARD_SUBJECT = re.compile(r"^\s*fwd?\s*:", re.IGNORECASE)
_UNSUBSCRIBE_SUBJECT = re.compile(r"^\s*unsubscribe\b", re.IGNORECASE)
_MAILTO = re.compile(r"<\s*mailto:([^>?\s]+)", re.IGNORECASE)


def _default_profile() -> dict[str, Any]:
    return {
        "name": "",
        "preferred_name": "",
        "home_timezone": "",
        "self_addresses": [],
        "assistant_senders": [],
        "telegram_user_id": None,
        "telegram_username": "",
    }


def _hermes_home() -> Path:
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        value = os.environ.get("HERMES_HOME", "").strip()
        return Path(value).expanduser() if value else Path.home() / ".hermes"


def _text(value: Any) -> str:
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ""
    return " ".join(str(value).split())[:MAX_FIELD_CHARS]


def _valid_timezone(value: Any) -> str:
    """An IANA zone name, tolerating a trailing note ("America/New_York (ET)")."""
    words = _text(value).split()
    if not words:
        return ""
    try:
        from zoneinfo import ZoneInfo

        ZoneInfo(words[0])
    except Exception:
        return ""
    return words[0]


def _config_timezone(home: Path) -> str:
    """Hermes ``config.yaml`` top-level ``timezone`` (empty when unset)."""
    try:
        text = (home / "config.yaml").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        match = re.match(r"timezone:(.*)$", line)
        if match:
            value = re.sub(r"\s+#.*$", "", match.group(1)).strip().strip("'\"")
            return _valid_timezone(value)
    return ""


def _system_timezone() -> str:
    name = _valid_timezone(os.environ.get("TZ", "").strip().lstrip(":"))
    if name:
        return name
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return ""
    marker = "zoneinfo/"
    return _valid_timezone(target.split(marker, 1)[1]) if marker in target else ""


def addresses(value: Any) -> list[str]:
    """Lowercased email addresses from a header string or a list of them."""
    if isinstance(value, str):
        raw = [value]
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw = [str(item) for item in value if isinstance(item, str)]
    else:
        return []
    found: list[str] = []
    for _name, address in email.utils.getaddresses(raw):
        address = address.strip().strip("<>").casefold()
        if "@" in address and address not in found:
            found.append(address)
    return found


def _telegram_fields(value: Any) -> dict[str, Any]:
    text = _text(value)
    if not text:
        return {}
    first = text.split()[0]
    if first.lstrip("-").isdigit():
        return {"telegram_user_id": int(first)}
    match = _TELEGRAM_HANDLE.match(first)
    return {"telegram_username": match.group(1)} if match else {}


def _read_operator_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("expected a JSON object")
    except (OSError, ValueError) as exc:
        print(
            f"town-feed: operator profile {path} is unreadable ({exc}); using defaults",
            file=sys.stderr,
        )
        return {}
    return raw


def _read_user_memory(path: Path) -> dict[str, Any]:
    try:
        if not path.is_file() or path.stat().st_size > MAX_USER_MEMORY_BYTES:
            return {}
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    fields: dict[str, Any] = {}
    for line in text.splitlines():
        match = _USER_MD_LINE.match(line)
        if not match:
            continue
        field = _USER_MD_FIELDS.get(match.group(1).strip().casefold())
        if not field or field in fields:
            continue
        fields[field] = match.group(2)
    return fields


def load_operator_profile(
    hermes_home: Optional[Path] = None,
    *,
    account_addresses: Iterable[str] = (),
) -> dict[str, Any]:
    """The operator profile; ``account_addresses`` are connected mailboxes.

    Callers that know the connected Google accounts pass them so they always
    count as self addresses. Never raises.
    """
    profile = _default_profile()
    try:
        home = Path(hermes_home) if hermes_home is not None else _hermes_home()
        configured = _read_operator_json(home / OPERATOR_FILE)
        memory = _read_user_memory(home / USER_MEMORY_FILE)

        for field in ("name", "preferred_name"):
            profile[field] = _text(configured.get(field)) or _text(memory.get(field))
        if not profile["preferred_name"] and profile["name"]:
            profile["preferred_name"] = profile["name"].split()[0]
        profile["home_timezone"] = (
            _valid_timezone(configured.get("home_timezone"))
            or _valid_timezone(memory.get("home_timezone"))
            or _config_timezone(home)
            or _system_timezone()
        )

        self_addresses = addresses(configured.get("self_addresses"))
        for address in addresses(list(account_addresses or ())):
            if address not in self_addresses:
                self_addresses.append(address)
        profile["self_addresses"] = self_addresses
        profile["assistant_senders"] = addresses(configured.get("assistant_senders"))

        telegram = _telegram_fields(memory.get("telegram"))
        user_id = configured.get("telegram_user_id")
        if isinstance(user_id, int) and not isinstance(user_id, bool):
            telegram["telegram_user_id"] = user_id
        username = _TELEGRAM_HANDLE.match(_text(configured.get("telegram_username")))
        if username:
            telegram["telegram_username"] = username.group(1)
        profile.update(telegram)
    except Exception as exc:  # pragma: no cover - defensive: never raise
        print(f"town-feed: operator profile failed to load ({exc}); using defaults", file=sys.stderr)
        return _default_profile()
    return profile


# --- Mail addressed from or to the operator's own identities ---------------


def self_address_set(profile: Optional[dict[str, Any]], extra: Iterable[str] = ()) -> set[str]:
    values = list((profile or {}).get("self_addresses") or [])
    values.extend(extra or ())
    return set(addresses(values))


def assistant_sender_set(profile: Optional[dict[str, Any]]) -> set[str]:
    return set(addresses(list((profile or {}).get("assistant_senders") or [])))


def is_self_only(recipients: Iterable[str], self_addresses: set[str]) -> bool:
    """True when there is at least one recipient and every one is the operator."""
    found = addresses(list(recipients))
    return bool(found) and all(address in self_addresses for address in found)


def list_unsubscribe_addresses(header: Any) -> list[str]:
    """The ``mailto:`` targets of a List-Unsubscribe header."""
    return addresses(_MAILTO.findall(str(header or "")))


def self_exhaust_reason(
    sender: Any,
    recipients: Iterable[str],
    self_addresses: set[str],
    assistant_senders: set[str],
) -> Optional[str]:
    """Why a message is the operator's own exhaust (``None`` when it is not).

    Mail from an assistant address, or from the operator to nobody but
    themself, is never Need-to-Know and never gets a label.
    """
    found = addresses(str(sender or ""))
    from_address = found[0] if found else ""
    if from_address and from_address in assistant_senders:
        return "assistant_sender"
    recipient_list = addresses(list(recipients))
    if from_address and from_address in self_addresses and (
        not recipient_list or is_self_only(recipient_list, self_addresses)
    ):
        return "self_to_self"
    return None


def awaiting_reply_junk_reason(
    recipients: Iterable[str],
    subject: Any,
    self_addresses: set[str],
    list_unsubscribe: Iterable[str] = (),
) -> Optional[str]:
    """Why an outbound message is not really awaiting anyone's reply.

    Self-only mail, unsubscribe requests (an ``unsubscribe`` address, a
    List-Unsubscribe ``mailto:`` target, or the ``Unsubscribe`` subject those
    mailtos carry), and ``Fwd:``/``Fw:`` FYI forwards.
    """
    recipient_list = addresses(list(recipients))
    if is_self_only(recipient_list, self_addresses):
        return "self_only"
    unsubscribe_targets = set(addresses(list(list_unsubscribe)))
    if any(
        "unsubscribe" in address or address in unsubscribe_targets
        for address in recipient_list
    ):
        return "unsubscribe"
    subject_text = str(subject or "")
    if _UNSUBSCRIBE_SUBJECT.match(subject_text):
        return "unsubscribe"
    if _FORWARD_SUBJECT.match(subject_text):
        return "forward"
    return None
