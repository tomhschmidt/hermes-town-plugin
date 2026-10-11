"""Who the operator is, for the Town gateway plugin and its cron scripts.

Source of truth is ``$HERMES_HOME/town-feed/operator.json`` (user-owned,
never in the repo). Missing fields fall back to the structured lines of
``$HERMES_HOME/memories/USER.md`` (``**Name:**``, ``**What to call them:**``,
``**Timezone:**``, ``**Home city:**``, ``**Telegram:**``), then (timezone
only) to Hermes ``config.yaml`` ``timezone``, then to the system timezone.
Only those structured fields are read: free-text memory never enters the
profile.

Loading is plain file reading: it never raises and never touches the
network. The mail predicates below apply the profile to Gmail addressing
(self-exhaust and awaiting-reply junk) for both the scanner and the curator;
``operator_context`` is the who/where/when block both LLM lanes receive (S1).
"""

from __future__ import annotations

import email.utils
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

OPERATOR_FILE = Path("town-feed") / "operator.json"
USER_MEMORY_FILE = Path("memories") / "USER.md"
MAX_USER_MEMORY_BYTES = 256 * 1024
MAX_FIELD_CHARS = 120
MAX_WORK_DOMAINS = 20

_USER_MD_LINE = re.compile(r"^\s*(?:[-*+]\s+)?\*\*([^*:]+):\*\*\s*(.*?)\s*$")
_USER_MD_FIELDS = {
    "name": "name",
    "what to call them": "preferred_name",
    "timezone": "home_timezone",
    "home city": "home_city",
    "city": "home_city",
    "telegram": "telegram",
}
_TELEGRAM_HANDLE = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{3,31})$")
_FORWARD_SUBJECT = re.compile(r"^\s*fwd?\s*:", re.IGNORECASE)
_UNSUBSCRIBE_SUBJECT = re.compile(r"^\s*unsubscribe\b", re.IGNORECASE)
_MAILTO = re.compile(r"<\s*mailto:([^>?\s]+)", re.IGNORECASE)
_DOMAIN = re.compile(r"^(?=.{3,120}$)[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]*[a-z0-9])?)+$")


def _default_profile() -> dict[str, Any]:
    return {
        "name": "",
        "preferred_name": "",
        "home_timezone": "",
        "home_city": "",
        "work_domains": {},
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


def _domain(value: Any) -> str:
    domain = _text(value).casefold().lstrip("@").rstrip(".")
    return domain if _DOMAIN.match(domain) else ""


def _domain_org(domain: str) -> str:
    """"example-capital.com" -> "Example Capital" (list-form work_domains)."""
    label = domain.split(".", 1)[0]
    return " ".join(part.capitalize() for part in re.split(r"[-_]+", label) if part)


def _work_domains(value: Any) -> dict[str, str]:
    """``{domain: org name}``; a plain list of domains derives the org name."""
    if isinstance(value, dict):
        pairs = list(value.items())
    elif isinstance(value, (list, tuple)):
        pairs = [(item, "") for item in value]
    else:
        return {}
    domains: dict[str, str] = {}
    for raw_domain, raw_org in pairs:
        domain = _domain(raw_domain)
        if not domain or domain in domains:
            continue
        domains[domain] = _text(raw_org) or _domain_org(domain)
        if len(domains) >= MAX_WORK_DOMAINS:
            break
    return domains


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
        profile["home_city"] = _text(configured.get("home_city")) or _text(memory.get("home_city"))
        profile["work_domains"] = _work_domains(configured.get("work_domains"))

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


# --- Who, where, and when for the LLM lanes (S1) ----------------------------

# Spoken abbreviations, kept in sync with src/town-home/lib/when.ts. US zones
# use the DST-neutral form ("ET"); others carry (standard, daylight) names;
# anything unlisted renders as a UTC offset ("UTC+8") rather than zoneinfo's
# bare "+08".
_GENERIC_ZONE_NAMES = {
    "America/New_York": "ET",
    "America/Detroit": "ET",
    "America/Toronto": "ET",
    "America/Indiana/Indianapolis": "ET",
    "America/Chicago": "CT",
    "America/Winnipeg": "CT",
    "America/Denver": "MT",
    "America/Edmonton": "MT",
    "America/Boise": "MT",
    "America/Phoenix": "MT",
    "America/Los_Angeles": "PT",
    "America/Vancouver": "PT",
    "America/Anchorage": "AKT",
    "Pacific/Honolulu": "HT",
    "Asia/Singapore": "SGT",
    "Asia/Hong_Kong": "HKT",
    "Asia/Tokyo": "JST",
    "Asia/Seoul": "KST",
    "Asia/Shanghai": "CST",
    "Asia/Kolkata": "IST",
    "Asia/Dubai": "GST",
    "Asia/Bangkok": "ICT",
    "Asia/Ho_Chi_Minh": "ICT",
    "Asia/Jakarta": "WIB",
    "Asia/Manila": "PHT",
    "Asia/Kuala_Lumpur": "MYT",
    "Atlantic/Reykjavik": "GMT",
}
_SEASONAL_ZONE_NAMES = {
    "Europe/London": ("GMT", "BST"),
    "Europe/Dublin": ("GMT", "IST"),
    "Europe/Lisbon": ("WET", "WEST"),
    "Europe/Athens": ("EET", "EEST"),
    "Europe/Helsinki": ("EET", "EEST"),
    "Australia/Sydney": ("AEST", "AEDT"),
    "Australia/Melbourne": ("AEST", "AEDT"),
    "Pacific/Auckland": ("NZST", "NZDT"),
    **{
        zone: ("CET", "CEST")
        for zone in (
            "Europe/Paris", "Europe/Berlin", "Europe/Madrid", "Europe/Rome",
            "Europe/Amsterdam", "Europe/Brussels", "Europe/Zurich", "Europe/Vienna",
            "Europe/Stockholm", "Europe/Copenhagen", "Europe/Oslo", "Europe/Prague",
            "Europe/Warsaw",
        )
    },
}


def _zone_info(name: Any) -> Any:
    valid = _valid_timezone(name)
    if not valid:
        return None
    from zoneinfo import ZoneInfo

    return ZoneInfo(valid)


def _moment(now: Any) -> datetime:
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromtimestamp(float(now), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return datetime.now(timezone.utc)


def zone_abbreviation(zone_name: Any, now: Any) -> str:
    """"ET", "SGT", "BST", else "UTC+8" — the zone as people say it at ``now``."""
    zone = _zone_info(zone_name)
    if zone is None:
        return "UTC"
    name = _valid_timezone(zone_name)
    local = _moment(now).astimezone(zone)
    if name in _GENERIC_ZONE_NAMES:
        return _GENERIC_ZONE_NAMES[name]
    if name in _SEASONAL_ZONE_NAMES:
        standard, daylight = _SEASONAL_ZONE_NAMES[name]
        return daylight if local.dst() else standard
    offset = local.utcoffset()
    minutes = int(offset.total_seconds() // 60) if offset is not None else 0
    if minutes == 0:
        return "UTC"
    sign = "+" if minutes > 0 else "-"
    hours, remainder = divmod(abs(minutes), 60)
    return f"UTC{sign}{hours}{f':{remainder:02d}' if remainder else ''}"


def local_time_label(when: Any, zone_name: Any) -> str:
    """"Sun Oct 11, 10:42 AM ET" in ``zone_name`` (UTC when it is invalid)."""
    zone = _zone_info(zone_name) or timezone.utc
    local = _moment(when).astimezone(zone)
    return f"{local.strftime('%a %b %-d, %-I:%M %p')} {zone_abbreviation(zone_name, local)}"


def display_timezone(profile: Optional[dict[str, Any]], where: Optional[dict[str, Any]] = None) -> str:
    """The zone "local" means: where the operator is when away, else home."""
    if isinstance(where, dict) and where.get("status") == "away":
        away = _valid_timezone(where.get("timezone"))
        if away:
            return away
    return _valid_timezone((profile or {}).get("home_timezone")) or "UTC"


def operator_context(
    profile: Optional[dict[str, Any]], now: Any, where: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """Who the operator is, where they are, and their local now — for the LLMs.

    Only structured profile fields: never addresses, ids, or memory text.
    """
    profile = profile or {}
    home_timezone = _valid_timezone(profile.get("home_timezone"))
    home_city = _text(profile.get("home_city"))
    if not isinstance(where, dict):
        where = {
            "status": "home",
            "city": home_city,
            "timezone": home_timezone,
            "label": f"Home in {home_city}" if home_city else "Home",
            "evidence": [],
        }
    zone_name = display_timezone(profile, where)
    zone = _zone_info(zone_name) or timezone.utc
    local_now = _moment(now).astimezone(zone)
    context: dict[str, Any] = {
        "name": _text(profile.get("name")),
        "preferred_name": _text(profile.get("preferred_name")),
        "home_city": home_city,
        "home_timezone": home_timezone,
        "timezone": zone_name,
        "local_now": local_now.isoformat(timespec="seconds"),
        "local_now_label": local_time_label(local_now, zone_name),
        "where": dict(where),
    }
    if home_timezone and home_timezone != zone_name:
        context["home_now_label"] = local_time_label(local_now, home_timezone)
    return context
