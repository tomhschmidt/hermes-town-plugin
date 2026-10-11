"""Who people are: the Town people directory (GROUNDING S2).

The directory answers "who is this address?" for the scanners and the
curator: a name, an org, a relationship to the operator, and when they last
exchanged email. It is built from three read-only sources:

- Google Contacts (People API ``connections.list``) for accounts whose token
  carries ``contacts.readonly``;
- recipients (To/Cc, with display names) of the operator's SENT mail from the
  last 180 days, read with ``format=metadata``;
- inbound senders the normal Gmail scan sees (merged incrementally).

Relationship precedence: ``colleague`` (a ``work_domains`` domain, or a
non-consumer domain of the operator's own addresses) > ``contact`` (in Google
Contacts) > ``correspondent`` (any sent or received history) > ``new``.

The cache is ``$HERMES_HOME/town-feed/people-cache.json`` (atomic, mode 0600,
at most ``MAX_PEOPLE`` people). Contacts and sent history refresh at most once
per 24 h per account. Nothing here imports Google: callers inject the People
and Gmail services, so the module is pure apart from the cache file. Nothing
here prints people data; the refresh logs only counts and error types.
"""

from __future__ import annotations

import email.utils
import json
import math
import os
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, MutableMapping, Optional

CACHE_FILE = Path("town-feed") / "people-cache.json"
CACHE_VERSION = 1
MAX_PEOPLE = 5000
MAX_SOURCE_ENTRIES = 5000
REFRESH_SECONDS = 24 * 60 * 60
RETRY_SECONDS = 60 * 60
SCOPE_LOG_SECONDS = 24 * 60 * 60
SENT_QUERY = "in:sent newer_than:180d"
SENT_HEADERS = ("To", "Cc", "Date")
MAX_SENT_MESSAGES = 500
CONTACTS_PAGE_SIZE = 1000
MAX_CONTACT_PAGES = 10
PERSON_FIELDS = "names,emailAddresses,organizations"
CONTACTS_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/contacts.readonly",
        "https://www.googleapis.com/auth/contacts",
    }
)
RELATIONSHIPS = ("colleague", "contact", "correspondent", "new")
MAX_NAME_CHARS = 80
MAX_ORG_CHARS = 80
MAX_TITLE_CHARS = 80
# The message's own arrival can trail its header timestamps by a little.
SAME_MESSAGE_SLACK_SECONDS = 60

# Webmail and ISP domains: sharing one with the operator makes nobody a colleague.
CONSUMER_DOMAINS = frozenset(
    {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "msn.com", "yahoo.com", "ymail.com", "rocketmail.com", "icloud.com",
        "me.com", "mac.com", "aol.com", "proton.me", "protonmail.com", "pm.me",
        "gmx.com", "gmx.net", "gmx.de", "mail.com", "fastmail.com", "fastmail.fm",
        "hey.com", "zoho.com", "yandex.com", "yandex.ru", "qq.com", "163.com",
        "126.com", "comcast.net", "verizon.net", "att.net", "sbcglobal.net",
        "btinternet.com", "web.de", "t-online.de", "orange.fr", "free.fr",
        "naver.com", "hanmail.net", "rediffmail.com", "tutanota.com", "duck.com",
    }
)
_CONSUMER_PREFIXES = ("yahoo.", "hotmail.", "outlook.", "live.", "gmx.", "aol.", "yandex.")

_EMAIL_RE = re.compile(r"^[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+$")
_REDACT_EMAIL_RE = re.compile(r"[^@\s<>\"',;()]+@[^@\s<>\"',;()]+")

Person = dict  # {email, name, org, title, relationship, last_sent_ts, last_received_ts, interactions}


# --- Normalization ------------------------------------------------------------


def normalize_email(value: Any) -> str:
    """The lowercased address in ``value`` ("" when there is none)."""
    if not isinstance(value, str) or not value.strip():
        return ""
    _name, address = email.utils.parseaddr(value.strip())
    address = (address or "").strip().strip("<>").casefold()
    return address[:254] if _EMAIL_RE.match(address) else ""


def clean_name(value: Any) -> str:
    """A display name fit to show, or "" (addresses are never names)."""
    if not isinstance(value, str):
        return ""
    text = " ".join(value.split()).strip().strip("'\"").strip()
    if not text or "@" in text:
        return ""
    return text[:MAX_NAME_CHARS]


def _text(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _timestamp(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) and number > 0 else None


def _count(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value)) if math.isfinite(float(value)) else 0


def _domain(value: Any) -> str:
    text = str(value or "").strip().casefold().lstrip("@")
    if "@" in text:
        text = text.rpartition("@")[2]
    return text if "." in text and not re.search(r"\s", text) else ""


def is_consumer_domain(domain: str) -> bool:
    domain = _domain(domain)
    return domain in CONSUMER_DOMAINS or domain.startswith(_CONSUMER_PREFIXES)


def self_addresses(profile: Optional[Mapping[str, Any]], extra: Iterable[str] = ()) -> set[str]:
    """The operator's own addresses plus their assistants' (never people)."""
    profile = profile if isinstance(profile, Mapping) else {}
    values: list[Any] = []
    for key in ("self_addresses", "assistant_senders"):
        raw = profile.get(key)
        if isinstance(raw, (list, tuple, set, frozenset)):
            values.extend(raw)
    values.extend(extra or ())
    return {address for address in (normalize_email(value) for value in values) if address}


def colleague_domains(profile: Optional[Mapping[str, Any]]) -> dict[str, str]:
    """Domain -> org name ("" when unknown) for the operator's workplaces.

    ``work_domains`` (S1's ``{domain: org}``; a plain list is tolerated) plus
    every non-consumer domain of the operator's own self addresses.
    """
    profile = profile if isinstance(profile, Mapping) else {}
    domains: dict[str, str] = {}
    raw = profile.get("work_domains")
    if isinstance(raw, Mapping):
        for raw_domain, raw_org in raw.items():
            domain = _domain(raw_domain)
            if domain:
                domains[domain] = _text(raw_org, MAX_ORG_CHARS)
    elif isinstance(raw, (list, tuple)):
        for raw_domain in raw:
            domain = _domain(raw_domain)
            if domain:
                domains.setdefault(domain, "")
    for address in self_addresses({"self_addresses": profile.get("self_addresses") or []}):
        domain = _domain(address)
        if domain and not is_consumer_domain(domain):
            domains.setdefault(domain, "")
    return domains


def _colleague_org(address: str, domains: Mapping[str, str]) -> Optional[str]:
    """The org for a colleague address ("" if unnamed); None when not a colleague."""
    domain = _domain(address)
    while domain:
        if domain in domains:
            return domains[domain]
        _head, dot, rest = domain.partition(".")
        if not dot or "." not in rest:
            break
        domain = rest
    return None


def _person(address: str, **fields: Any) -> Person:
    person: Person = {
        "email": address,
        "name": None,
        "org": None,
        "title": None,
        "relationship": "new",
        "last_sent_ts": None,
        "last_received_ts": None,
        "interactions": 0,
    }
    person.update(fields)
    return person


# --- Directory -----------------------------------------------------------------


def _merge_observed(into: dict[str, dict[str, Any]], source: Any) -> None:
    if not isinstance(source, Mapping):
        return
    for raw_address, raw_entry in source.items():
        address = normalize_email(raw_address)
        if not address or not isinstance(raw_entry, Mapping):
            continue
        entry = into.setdefault(address, {"name": None, "last_ts": None, "count": 0})
        last_ts = _timestamp(raw_entry.get("last_ts"))
        name = clean_name(raw_entry.get("name"))
        if last_ts and (entry["last_ts"] is None or last_ts >= entry["last_ts"]):
            entry["last_ts"] = last_ts
            entry["name"] = name or entry["name"]
        elif name and not entry["name"]:
            entry["name"] = name
        entry["count"] += _count(raw_entry.get("count"))


def _person_sort_key(person: Person) -> tuple[float, int, int]:
    recency = max(person.get("last_sent_ts") or 0.0, person.get("last_received_ts") or 0.0)
    return (-recency, -int(person.get("interactions") or 0), RELATIONSHIPS.index(person["relationship"]))


def build_directory(
    contacts: Iterable[Mapping[str, Any]],
    sent_recipients: Mapping[str, Mapping[str, Any]],
    inbound_senders: Mapping[str, Mapping[str, Any]],
    known_contacts: Iterable[str],
    profile: Optional[Mapping[str, Any]],
) -> dict[str, Person]:
    """email -> Person from the three sources (pure).

    ``sent_recipients``/``inbound_senders`` map an address to
    ``{"name", "last_ts", "count"}``; ``known_contacts`` are addresses the
    operator is known to have written to (the Gmail scan's correspondent
    cache). Names: Contacts, then message display names. Orgs: Contacts, then
    the ``work_domains`` org for colleagues. The operator's own addresses never
    appear. Capped at ``MAX_PEOPLE`` (most recent exchange first).
    """
    own = self_addresses(profile)
    domains = colleague_domains(profile)
    people: dict[str, Person] = {}
    in_contacts: set[str] = set()
    known: set[str] = set()

    def entry(address: str) -> Optional[Person]:
        if not address or address in own:
            return None
        return people.setdefault(address, _person(address))

    for contact in contacts or ():
        if not isinstance(contact, Mapping):
            continue
        address = normalize_email(contact.get("email"))
        person = entry(address)
        if person is None:
            continue
        in_contacts.add(address)
        for field, limit in (("name", MAX_NAME_CHARS), ("org", MAX_ORG_CHARS), ("title", MAX_TITLE_CHARS)):
            value = clean_name(contact.get(field)) if field == "name" else _text(contact.get(field), limit)
            if value and not person[field]:
                person[field] = value

    for source, ts_field in ((inbound_senders, "last_received_ts"), (sent_recipients, "last_sent_ts")):
        observed: dict[str, dict[str, Any]] = {}
        _merge_observed(observed, source)
        for address, seen in observed.items():
            person = entry(address)
            if person is None:
                continue
            if seen["last_ts"]:
                person[ts_field] = max(person[ts_field] or 0.0, seen["last_ts"])
            person["interactions"] += seen["count"]
            if seen["name"] and not person["name"]:
                person["name"] = seen["name"]

    for raw in known_contacts or ():
        address = normalize_email(raw)
        if entry(address) is not None:
            known.add(address)

    for address, person in people.items():
        org = _colleague_org(address, domains)
        if org is not None:
            person["relationship"] = "colleague"
            person["org"] = person["org"] or org or None
        elif address in in_contacts:
            person["relationship"] = "contact"
        elif (
            address in known
            or person["last_sent_ts"]
            or person["last_received_ts"]
            or person["interactions"]
        ):
            person["relationship"] = "correspondent"
        else:
            person["relationship"] = "new"

    ranked = sorted(people.values(), key=_person_sort_key)[:MAX_PEOPLE]
    return {person["email"]: person for person in ranked}


class Directory(dict):
    """email -> Person, plus ``complete``: sent history backs ``new`` claims.

    A directory built without any successful sent-mail refresh cannot say
    "no prior email" about anyone, so unknown addresses then stay unknown.
    """

    complete = False


def lookup(directory: Optional[Mapping[str, Any]], address: Any) -> Optional[Person]:
    """The directory entry for ``address`` (case-insensitive), else None."""
    if not isinstance(directory, Mapping):
        return None
    person = directory.get(normalize_email(address) if isinstance(address, str) else "")
    return dict(person) if isinstance(person, Mapping) else None


def resolve(
    directory: Optional[Mapping[str, Any]],
    address: Any,
    profile: Optional[Mapping[str, Any]] = None,
    *,
    domains: Optional[Mapping[str, str]] = None,
    own: Optional[set[str]] = None,
    known: Iterable[str] = (),
) -> Optional[Person]:
    """The best grounded view of ``address``; None for self or unknowable.

    A colleague domain always wins (the profile may be newer than the cache).
    An address absent from the directory is ``new`` only when the directory is
    complete; otherwise nothing can be claimed about it.
    """
    address = normalize_email(address) if isinstance(address, str) else ""
    own = self_addresses(profile) if own is None else own
    if not address or address in own:
        return None
    domains = colleague_domains(profile) if domains is None else domains
    org = _colleague_org(address, domains)
    person = lookup(directory, address)
    if person is not None:
        if org is not None:
            person["relationship"] = "colleague"
            person["org"] = person.get("org") or org or None
        return person
    if org is not None:
        return _person(address, org=org or None, relationship="colleague")
    if address in {normalize_email(value) for value in known}:
        return _person(address, relationship="correspondent")
    complete = getattr(directory, "complete", bool(directory))
    return _person(address) if complete else None


def last_contact_ts(person: Optional[Mapping[str, Any]]) -> Optional[float]:
    if not isinstance(person, Mapping):
        return None
    stamps = [
        stamp
        for stamp in (_timestamp(person.get("last_sent_ts")), _timestamp(person.get("last_received_ts")))
        if stamp
    ]
    return max(stamps) if stamps else None


def human_age(seconds: float) -> str:
    """'3 days ago' style text for an age in seconds."""
    seconds = max(0.0, float(seconds))
    if seconds < 3600:
        minutes = max(1, round(seconds / 60))
        return f"{minutes} minute{'s' if minutes != 1 else ''} ago"
    if seconds < 48 * 3600:
        hours = max(1, round(seconds / 3600))
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = seconds / 86400
    if days < 14:
        return f"{round(days)} days ago"
    if days < 60:
        return f"{round(days / 7)} weeks ago"
    if days < 365:
        return f"{max(2, round(days / 30))} months ago"
    return "over a year ago"


def last_contact_text(person: Optional[Mapping[str, Any]], now: float) -> Optional[str]:
    """Human last-touch text: 'you emailed 3 days ago', 'no prior email'.

    'no prior email' only for ``new``; a colleague or contact without email
    history on record gets None (absence of data is not absence of contact).
    """
    if not isinstance(person, Mapping):
        return None
    if person.get("relationship") == "new":
        return "no prior email"
    sent = _timestamp(person.get("last_sent_ts"))
    received = _timestamp(person.get("last_received_ts"))
    if sent and (not received or sent >= received):
        return f"you emailed {human_age(now - sent)}"
    if received:
        return f"emailed you {human_age(now - received)}"
    if person.get("relationship") == "correspondent":
        return "emailed before"
    return None


def sender_as_of(
    person: Optional[Person], message_ts: Optional[float], *, known: bool = False
) -> Optional[Person]:
    """The sender's standing BEFORE the message at ``message_ts`` arrived.

    The scan merges every inbound sender, so a first-time sender already looks
    like a correspondent whose last email is this very message. Without sent
    history, an earlier message, or a ``known`` correspondent record, they are
    still ``new`` for that row.
    """
    if not isinstance(person, Mapping) or not message_ts:
        return person
    view = dict(person)
    received = _timestamp(view.get("last_received_ts"))
    if not received or received < message_ts - SAME_MESSAGE_SLACK_SECONDS:
        return view
    view["last_received_ts"] = None
    if (
        view.get("relationship") == "correspondent"
        and not view.get("last_sent_ts")
        and not known
        and int(view.get("interactions") or 0) <= 1
    ):
        view["relationship"] = "new"
    return view


def enrich_attendee(
    attendee: Mapping[str, Any],
    directory: Optional[Mapping[str, Any]],
    profile: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    """A calendar attendee entry plus ``name``/``org``/``relationship``/``last_contact_ts``.

    The directory name beats Google's ``displayName``. The operator's own
    addresses are flagged ``self`` and left otherwise untouched.
    """
    entry = dict(attendee)
    if entry.get("self") is True:
        return entry
    address = normalize_email(entry.get("email"))
    if not address:
        return entry
    if address in self_addresses(profile):
        entry["self"] = True
        return entry
    person = resolve(directory, address, profile)
    if person is None:
        return entry
    if person.get("name"):
        entry["name"] = person["name"]
    if person.get("org"):
        entry["org"] = person["org"]
    entry["relationship"] = person["relationship"]
    stamp = last_contact_ts(person)
    if stamp:
        entry["last_contact_ts"] = stamp
    return entry


class PeopleIndex:
    """Directory lookups for one curation pass (fixed ``now`` and profile)."""

    def __init__(
        self,
        directory: Optional[Mapping[str, Any]],
        profile: Optional[Mapping[str, Any]] = None,
        *,
        now: Optional[float] = None,
        known: Iterable[str] = (),
    ) -> None:
        self.directory = directory if isinstance(directory, Mapping) else Directory()
        self.profile = profile if isinstance(profile, Mapping) else {}
        self.now = time.time() if now is None else float(now)
        self.domains = colleague_domains(self.profile)
        self.own = self_addresses(self.profile)
        self.known = {address for address in (normalize_email(value) for value in known or ()) if address}

    def is_self(self, address: Any) -> bool:
        return normalize_email(address) in self.own if isinstance(address, str) else False

    def resolve(self, address: Any) -> Optional[Person]:
        return resolve(self.directory, address, domains=self.domains, own=self.own, known=self.known)

    def relationship(self, attendee: Mapping[str, Any]) -> Optional[str]:
        """An attendee's relationship: resolved now, else the scanner's."""
        resolved = self.resolve(attendee.get("email"))
        if resolved is not None:
            return str(resolved["relationship"])
        hint = attendee.get("relationship")
        return str(hint) if hint in RELATIONSHIPS else None

    def is_first_contact(self, attendee: Mapping[str, Any]) -> bool:
        """Only ``new`` people are a first contact; never the operator."""
        if not isinstance(attendee, Mapping) or attendee.get("self") is True:
            return False
        if self.is_self(attendee.get("email")):
            return False
        return self.relationship(attendee) == "new"

    def _entry(self, person: Optional[Mapping[str, Any]], name: str) -> dict[str, Any]:
        """``{name, org, relationship, last_contact}`` (+ ``title`` when Contacts has one)."""
        person = person or {}
        relationship = person.get("relationship") if person.get("relationship") in RELATIONSHIPS else None
        entry: dict[str, Any] = {
            "name": person.get("name") or name or None,
            "org": person.get("org") or None,
            "relationship": relationship,
            "last_contact": last_contact_text(person, self.now) if person else None,
        }
        if person.get("title"):
            entry["title"] = person["title"]
        return entry

    def attendee_entry(
        self, attendee: Mapping[str, Any], *, with_email: bool = False
    ) -> Optional[dict[str, Any]]:
        """The LLM view of a calendar attendee; None for the operator or nobody."""
        if not isinstance(attendee, Mapping) or attendee.get("self") is True:
            return None
        address = normalize_email(attendee.get("email"))
        if address and address in self.own:
            return None
        person = self.resolve(address) if address else None
        if person is None and attendee.get("relationship") in RELATIONSHIPS:
            # The scanner's enrichment, when the directory cannot say more now.
            person = _person(address, relationship=attendee["relationship"])
        if person is not None:
            person["org"] = person.get("org") or _text(attendee.get("org"), MAX_ORG_CHARS) or None
        entry = self._entry(person, clean_name(attendee.get("name")))
        scanned_touch = _timestamp(attendee.get("last_contact_ts"))
        if entry["last_contact"] is None and scanned_touch and entry["relationship"] != "new":
            entry["last_contact"] = f"emailed {human_age(self.now - scanned_touch)}"
        if entry["name"] is None and entry["relationship"] is None:
            return None
        if with_email and address:
            entry = {"email": address, **entry}
        if attendee.get("response") == "declined":
            entry["response"] = "declined"
        return entry

    def message_people(self, candidate: Mapping[str, Any], *, limit: int = 4) -> list[dict[str, Any]]:
        """The sender plus up to ``limit`` recipients of a Gmail row."""
        entries: list[dict[str, Any]] = []
        seen: set[str] = set(self.own)
        sender_name, sender_address = email.utils.parseaddr(str(candidate.get("from") or ""))
        sender = normalize_email(candidate.get("from_address") or sender_address)
        if sender and sender not in seen:
            seen.add(sender)
            person = self.resolve(sender)
            if candidate.get("direction") != "outbound":
                person = sender_as_of(
                    person, _timestamp(candidate.get("created_at_ts")), known=sender in self.known
                )
            entry = self._entry(person, clean_name(sender_name))
            if entry["name"] is not None or entry["relationship"] is not None:
                entries.append(entry)
        recipients = 0
        for address, name in _recipient_pairs(candidate.get("recipients")):
            if recipients >= limit:
                break
            if address in seen:
                continue
            seen.add(address)
            entry = self._entry(self.resolve(address), name)
            if entry["name"] is not None or entry["relationship"] is not None:
                entries.append(entry)
                recipients += 1
        return entries


def _recipient_pairs(value: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if not isinstance(value, list):
        return pairs
    for raw in value:
        if isinstance(raw, Mapping):
            address, name = normalize_email(raw.get("email")), clean_name(raw.get("name"))
        elif isinstance(raw, str):
            parsed_name, parsed_address = email.utils.parseaddr(raw)
            address, name = normalize_email(parsed_address), clean_name(parsed_name)
        else:
            continue
        if address:
            pairs.append((address, name))
    return pairs


# --- Source parsing ------------------------------------------------------------


def _primary(values: Any) -> Optional[Mapping[str, Any]]:
    rows = [row for row in values if isinstance(row, Mapping)] if isinstance(values, list) else []
    for row in rows:
        metadata = row.get("metadata")
        if isinstance(metadata, Mapping) and metadata.get("primary") is True:
            return row
    return rows[0] if rows else None


def parse_connections(response: Any) -> list[dict[str, Any]]:
    """Contacts from one People API ``connections.list`` page; one entry per email."""
    rows = response.get("connections") if isinstance(response, Mapping) else None
    contacts: list[dict[str, Any]] = []
    for person in rows if isinstance(rows, list) else []:
        if not isinstance(person, Mapping):
            continue
        names = _primary(person.get("names")) or {}
        organization = _primary(person.get("organizations")) or {}
        name = clean_name(names.get("displayName")) or clean_name(
            " ".join(
                part
                for part in (_text(names.get("givenName"), 40), _text(names.get("familyName"), 40))
                if part
            )
        )
        org = _text(organization.get("name"), MAX_ORG_CHARS)
        title = _text(organization.get("title"), MAX_TITLE_CHARS)
        seen: set[str] = set()
        for raw in person.get("emailAddresses") or []:
            address = normalize_email(raw.get("value")) if isinstance(raw, Mapping) else ""
            if not address or address in seen:
                continue
            seen.add(address)
            contacts.append({"email": address, "name": name or None, "org": org or None, "title": title or None})
    return contacts


def _dedupe_contacts(contacts: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for contact in contacts:
        address = normalize_email(contact.get("email")) if isinstance(contact, Mapping) else ""
        if not address:
            continue
        current = merged.setdefault(address, {"email": address, "name": None, "org": None, "title": None})
        for field in ("name", "org", "title"):
            value = contact.get(field)
            if not current[field] and isinstance(value, str) and value.strip():
                current[field] = value
    return list(merged.values())[:MAX_SOURCE_ENTRIES]


def fetch_contacts(service: Any, *, max_pages: int = MAX_CONTACT_PAGES) -> list[dict[str, Any]]:
    """Every Google Contact with an email (paginated ``people/me`` connections)."""
    contacts: list[dict[str, Any]] = []
    page_token: Optional[str] = None
    for _page in range(max_pages):
        request: dict[str, Any] = {
            "resourceName": "people/me",
            "personFields": PERSON_FIELDS,
            "pageSize": CONTACTS_PAGE_SIZE,
        }
        if page_token:
            request["pageToken"] = page_token
        response = service.people().connections().list(**request).execute()
        contacts.extend(parse_connections(response))
        page_token = (
            str(response.get("nextPageToken") or "").strip() if isinstance(response, Mapping) else ""
        ) or None
        if not page_token or len(contacts) >= MAX_SOURCE_ENTRIES:
            break
    return _dedupe_contacts(contacts)


def parse_sent_message(message: Any) -> tuple[Optional[float], list[tuple[str, str]]]:
    """(sent timestamp, [(address, display name)]) from To/Cc metadata headers."""
    if not isinstance(message, Mapping):
        return None, []
    payload = message.get("payload") if isinstance(message.get("payload"), Mapping) else {}
    raw_headers = payload.get("headers") or message.get("headers") or []
    values: dict[str, list[str]] = {"to": [], "cc": [], "date": []}
    for header in raw_headers if isinstance(raw_headers, list) else []:
        if not isinstance(header, Mapping):
            continue
        name = str(header.get("name") or "").strip().casefold()
        if name in values:
            values[name].append(str(header.get("value") or ""))
    sent_ts: Optional[float] = None
    try:
        internal_date = int(message.get("internalDate") or 0)
    except (TypeError, ValueError):
        internal_date = 0
    if internal_date > 0:
        sent_ts = internal_date / 1000.0
    elif values["date"]:
        try:
            sent_ts = email.utils.parsedate_to_datetime(values["date"][0]).timestamp()
        except (TypeError, ValueError, IndexError, OverflowError):
            sent_ts = None
    recipients: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, address in email.utils.getaddresses(values["to"] + values["cc"]):
        address = normalize_email(address)
        if not address or address in seen:
            continue
        seen.add(address)
        recipients.append((address, clean_name(name)))
    return sent_ts, recipients


def _cap_observed(observed: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if len(observed) <= MAX_SOURCE_ENTRIES:
        return observed
    ranked = sorted(
        observed.items(),
        key=lambda item: (-(item[1].get("last_ts") or 0.0), -int(item[1].get("count") or 0)),
    )
    return dict(ranked[:MAX_SOURCE_ENTRIES])


def aggregate_sent(messages: Iterable[Any], exclude: Iterable[str] = ()) -> dict[str, dict[str, Any]]:
    """address -> {name, last_ts, count} over SENT message metadata."""
    excluded = {normalize_email(value) for value in exclude}
    observed: dict[str, dict[str, Any]] = {}
    for message in messages:
        sent_ts, recipients = parse_sent_message(message)
        for address, name in recipients:
            if address in excluded:
                continue
            entry = observed.setdefault(address, {"name": None, "last_ts": None, "count": 0})
            entry["count"] += 1
            if sent_ts and (entry["last_ts"] is None or sent_ts >= entry["last_ts"]):
                entry["last_ts"] = sent_ts
                entry["name"] = name or entry["name"]
            elif name and not entry["name"]:
                entry["name"] = name
    return _cap_observed(observed)


def fetch_sent_recipients(
    gmail: Any,
    *,
    exclude: Iterable[str] = (),
    max_messages: int = MAX_SENT_MESSAGES,
) -> dict[str, dict[str, Any]]:
    """Recipients of up to ``max_messages`` SENT messages from the last 180 days.

    Read-only: ``messages.list`` + ``messages.get(format="metadata")``. A
    message that fails to load is skipped; if every one fails, the last error
    propagates so the refresh is retried.
    """
    message_ids: list[str] = []
    page_token: Optional[str] = None
    while len(message_ids) < max_messages:
        request: dict[str, Any] = {
            "userId": "me",
            "q": SENT_QUERY,
            "maxResults": min(500, max_messages - len(message_ids)),
        }
        if page_token:
            request["pageToken"] = page_token
        listed = gmail.users().messages().list(**request).execute()
        listed = listed if isinstance(listed, Mapping) else {}
        for ref in listed.get("messages") or []:
            if isinstance(ref, Mapping) and str(ref.get("id") or "").strip():
                message_ids.append(str(ref["id"]).strip())
        page_token = str(listed.get("nextPageToken") or "").strip() or None
        if not page_token:
            break
    message_ids = list(dict.fromkeys(message_ids))[:max_messages]
    messages: list[Any] = []
    last_error: Optional[BaseException] = None
    for message_id in message_ids:
        try:
            messages.append(
                gmail.users()
                .messages()
                .get(userId="me", id=message_id, format="metadata", metadataHeaders=list(SENT_HEADERS))
                .execute()
            )
        except Exception as exc:  # one bad message never sinks the batch
            last_error = exc
    if message_ids and not messages and last_error is not None:
        raise last_error
    return aggregate_sent(messages, exclude)


def inbound_observations(messages: Iterable[Any]) -> list[tuple[str, str, float]]:
    """(sender, display name, ts) for real-person inbound mail from a scan."""
    observations: list[tuple[str, str, float]] = []
    for message in messages or ():
        if not isinstance(message, Mapping):
            continue
        if message.get("direction") == "outbound" or message.get("bulk_sender") is True:
            continue
        name, parsed = email.utils.parseaddr(str(message.get("from") or ""))
        address = normalize_email(message.get("from_address") or parsed)
        sent_ts = _timestamp(message.get("created_at_ts"))
        if address and sent_ts:
            observations.append((address, clean_name(name), sent_ts))
    return observations


def outbound_observations(messages: Iterable[Any]) -> list[tuple[str, str, float]]:
    """(recipient, display name, ts) for the operator's own sends from a scan."""
    observations: list[tuple[str, str, float]] = []
    for message in messages or ():
        if not isinstance(message, Mapping) or message.get("direction") != "outbound":
            continue
        sent_ts = _timestamp(message.get("created_at_ts"))
        if not sent_ts:
            continue
        pairs = _recipient_pairs(message.get("recipients")) or _recipient_pairs(message.get("_recipients"))
        observations.extend((address, name, sent_ts) for address, name in pairs)
    return observations


def merge_observations(
    target: MutableMapping[str, Any],
    observations: Iterable[tuple[str, str, float]],
    exclude: Iterable[str] = (),
) -> int:
    """Merge (address, name, ts) sightings into an observed map; returns new sightings.

    A sighting counts only when it is newer than the address's last one, so
    a message seen again by a later scan is not double counted.
    """
    excluded = {normalize_email(value) for value in exclude}
    added = 0
    for address, name, sent_ts in sorted(observations, key=lambda observation: observation[2]):
        address = normalize_email(address)
        if not address or address in excluded or not _timestamp(sent_ts):
            continue
        entry = target.get(address)
        if not isinstance(entry, dict):
            entry = target[address] = {"name": None, "last_ts": None, "count": 0}
        last = _timestamp(entry.get("last_ts")) or 0.0
        if sent_ts > last:
            entry["last_ts"] = float(sent_ts)
            entry["count"] = _count(entry.get("count")) + 1
            added += 1
            entry["name"] = clean_name(name) or entry.get("name")
        elif name and not entry.get("name"):
            entry["name"] = clean_name(name) or None
    return added


# --- Cache -----------------------------------------------------------------------


def _hermes_home(hermes_home: Optional[Path]) -> Path:
    if hermes_home is not None:
        return Path(hermes_home)
    try:
        from hermes_constants import get_hermes_home

        return Path(get_hermes_home())
    except Exception:
        value = os.environ.get("HERMES_HOME", "").strip()
        return Path(value).expanduser() if value else Path.home() / ".hermes"


def cache_path(hermes_home: Optional[Path] = None) -> Path:
    return _hermes_home(hermes_home) / CACHE_FILE


def _empty_account() -> dict[str, Any]:
    return {
        "contacts_refreshed_at": None,
        "contacts_attempted_at": None,
        "sent_refreshed_at": None,
        "sent_attempted_at": None,
        "scope_logged_at": None,
        "contacts": [],
        "sent": {},
    }


def _empty_cache() -> dict[str, Any]:
    return {"version": CACHE_VERSION, "updated_at": None, "accounts": {}, "inbound": {}, "people": {}}


def _normalize_people(value: Any) -> dict[str, Person]:
    people: dict[str, Person] = {}
    if not isinstance(value, Mapping):
        return people
    for raw_address, raw in value.items():
        address = normalize_email(raw_address)
        if not address or not isinstance(raw, Mapping) or raw.get("relationship") not in RELATIONSHIPS:
            continue
        people[address] = _person(
            address,
            name=clean_name(raw.get("name")) or None,
            org=_text(raw.get("org"), MAX_ORG_CHARS) or None,
            title=_text(raw.get("title"), MAX_TITLE_CHARS) or None,
            relationship=raw["relationship"],
            last_sent_ts=_timestamp(raw.get("last_sent_ts")),
            last_received_ts=_timestamp(raw.get("last_received_ts")),
            interactions=_count(raw.get("interactions")),
        )
        if len(people) >= MAX_PEOPLE:
            break
    return people


def _normalize_cache(raw: Any) -> dict[str, Any]:
    cache = _empty_cache()
    if not isinstance(raw, Mapping):
        return cache
    cache["updated_at"] = _timestamp(raw.get("updated_at"))
    accounts = raw.get("accounts")
    for raw_account, raw_state in (accounts.items() if isinstance(accounts, Mapping) else []):
        account = normalize_email(raw_account)
        if not account or not isinstance(raw_state, Mapping):
            continue
        state = _empty_account()
        for field in (
            "contacts_refreshed_at",
            "contacts_attempted_at",
            "sent_refreshed_at",
            "sent_attempted_at",
            "scope_logged_at",
        ):
            state[field] = _timestamp(raw_state.get(field))
        contacts = raw_state.get("contacts")
        state["contacts"] = _dedupe_contacts(contacts if isinstance(contacts, list) else [])
        sent: dict[str, dict[str, Any]] = {}
        _merge_observed(sent, raw_state.get("sent"))
        state["sent"] = _cap_observed(sent)
        cache["accounts"][account] = state
    inbound: dict[str, dict[str, Any]] = {}
    _merge_observed(inbound, raw.get("inbound"))
    cache["inbound"] = _cap_observed(inbound)
    cache["people"] = _normalize_people(raw.get("people"))
    return cache


def load_cache(hermes_home: Optional[Path] = None) -> dict[str, Any]:
    """The normalized cache; missing or unreadable files yield an empty cache."""
    path = cache_path(hermes_home)
    try:
        if not path.is_file():
            return _empty_cache()
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"town-feed: people cache is unreadable ({type(exc).__name__}); ignoring it", file=sys.stderr)
        return _empty_cache()
    return _normalize_cache(raw)


def load_directory(hermes_home: Optional[Path] = None) -> Directory:
    """email -> Person from the cache ({} without one). Never raises."""
    try:
        cache = load_cache(hermes_home)
    except Exception:  # pragma: no cover - defensive: never raise
        return Directory()
    directory = Directory(cache["people"])
    directory.complete = bool(directory) and any(
        state.get("sent_refreshed_at") for state in cache["accounts"].values()
    )
    return directory


def write_cache(hermes_home: Optional[Path], cache: Mapping[str, Any]) -> Path:
    """Atomically replace the cache file (mode 0600)."""
    path = cache_path(hermes_home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_normalize_cache(cache), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    fd, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".people-cache-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return path


# --- Refresh -------------------------------------------------------------------


def _due(state: Mapping[str, Any], kind: str, now: float) -> bool:
    refreshed = _timestamp(state.get(f"{kind}_refreshed_at"))
    attempted = _timestamp(state.get(f"{kind}_attempted_at"))
    if refreshed and now - refreshed < REFRESH_SECONDS:
        return False
    return not attempted or now - attempted >= RETRY_SECONDS


def safe_error(exc: BaseException) -> str:
    """An error for the logs with every address redacted (people data never logs)."""
    text = " ".join(str(exc).split())
    text = _REDACT_EMAIL_RE.sub("<address>", text)
    return f"{type(exc).__name__}: {text}"[:200] if text else type(exc).__name__


def refresh_people_cache(
    hermes_home: Optional[Path],
    now: float,
    *,
    accounts: Iterable[str],
    gmail_services: Mapping[str, Any],
    people_service: Callable[[str], Any],
    inbound: Iterable[Any] = (),
    outbound: Iterable[Any] = (),
    known_contacts: Iterable[str] = (),
    profile: Optional[Mapping[str, Any]] = None,
    log: Callable[[str], None] = lambda line: print(line, file=sys.stderr),
) -> dict[str, int]:
    """Refresh the people cache and rebuild the directory; returns counts only.

    Per account, Contacts and sent history refresh at most once per 24 h
    (failures retry after an hour). ``people_service(account)`` returns a
    People API client, or None when the token lacks ``contacts.readonly``
    (skipped, logged at most once a day). Each failure logs one line; nothing
    raises past a single source, and no people data is ever logged.
    """
    accounts = sorted({normalize_email(account) for account in accounts} - {""})
    own = self_addresses(profile, accounts)
    # Connected mailboxes are self addresses (their custom domains make
    # colleagues); assistant senders are excluded but never define a domain.
    profile = dict(profile or {})
    profile["self_addresses"] = sorted(
        self_addresses({"self_addresses": profile.get("self_addresses") or []}, accounts)
    )
    cache = load_cache(hermes_home)
    stats = {"contacts_refreshed": 0, "sent_refreshed": 0, "failures": 0}
    for account in accounts:
        state = cache["accounts"].setdefault(account, _empty_account())
        if _due(state, "contacts", now):
            try:
                service = people_service(account)
                if service is None:
                    scope_logged = _timestamp(state.get("scope_logged_at"))
                    if not scope_logged or now - scope_logged >= SCOPE_LOG_SECONDS:
                        log(f"{account}: token lacks contacts.readonly; skipping Google Contacts")
                        state["scope_logged_at"] = now
                else:
                    state["contacts_attempted_at"] = now
                    state["contacts"] = fetch_contacts(service)
                    state["contacts_refreshed_at"] = now
                    stats["contacts_refreshed"] += 1
            except Exception as exc:
                state["contacts_attempted_at"] = now
                stats["failures"] += 1
                log(f"{account}: contacts refresh failed ({safe_error(exc)})")
        gmail = gmail_services.get(account) if isinstance(gmail_services, Mapping) else None
        if gmail is not None and _due(state, "sent", now):
            state["sent_attempted_at"] = now
            try:
                state["sent"] = fetch_sent_recipients(gmail, exclude=own)
                state["sent_refreshed_at"] = now
                stats["sent_refreshed"] += 1
            except Exception as exc:
                stats["failures"] += 1
                log(f"{account}: sent-history refresh failed ({safe_error(exc)})")

    merge_observations(cache["inbound"], inbound_observations(inbound), own)
    cache["inbound"] = _cap_observed(cache["inbound"])
    by_account: dict[str, list[tuple[str, str, float]]] = {}
    for message in outbound or ():
        if isinstance(message, Mapping):
            account = normalize_email(message.get("account"))
            if account in cache["accounts"]:
                by_account.setdefault(account, []).extend(outbound_observations([message]))
    for account, observations in by_account.items():
        sent = cache["accounts"][account]["sent"]
        merge_observations(sent, observations, own)
        cache["accounts"][account]["sent"] = _cap_observed(sent)

    contacts = [contact for state in cache["accounts"].values() for contact in state["contacts"]]
    sent_recipients: dict[str, dict[str, Any]] = {}
    for state in cache["accounts"].values():
        _merge_observed(sent_recipients, state["sent"])
    cache["people"] = build_directory(contacts, sent_recipients, cache["inbound"], known_contacts, profile)
    cache["updated_at"] = now
    write_cache(hermes_home, cache)
    stats["people"] = len(cache["people"])
    return stats
