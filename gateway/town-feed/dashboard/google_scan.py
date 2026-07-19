"""Ambient Google source scanners for the Town Need-to-Know feed."""

from __future__ import annotations

import base64
import email.utils
import hashlib
import html
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, MutableMapping


try:
    from hermes_constants import get_hermes_home
except Exception:  # pragma: no cover - fallback for standalone tests.
    def get_hermes_home() -> Path:
        value = os.environ.get("HERMES_HOME", "").strip()
        return Path(value).expanduser() if value else Path.home() / ".hermes"


GMAIL_SCOPES = (
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
)
GMAIL_QUERY = "in:inbox -in:chats newer_than:2d"
GMAIL_LOOKBACK_QUERY = "in:inbox -in:chats newer_than:7d"
GMAIL_SENT_QUERY = "in:sent -in:chats newer_than:2d"
MAX_GMAIL_MESSAGES = 25
MAX_GMAIL_LOOKBACK_MESSAGES = 40
MAX_LOOKBACK_SEEN = 500
MAX_OPEN_LOOPS_PER_ACCOUNT = 40
MAX_OPEN_LOOPS_PAYLOAD = 15
MAX_LABEL_DEPREFIX_MESSAGES = 200
MIN_LABEL_CONFIDENCE = 0.6
MAX_LABELING_LOG = 500
NEW_SENDER_LABEL = "new sender"
AWAITING_REPLY_LABEL = "awaiting reply"
DEFAULT_INBOX_TAXONOMY = {
    NEW_SENDER_LABEL: (
        "Real-person mail from a sender with no prior meaningful sent email; never use "
        "for bulk or no-reply mail."
    ),
    AWAITING_REPLY_LABEL: (
        "A thread whose latest message is outbound from the account owner and is awaiting "
        "the recipient's reply."
    ),
    "action needed": "The account owner needs to take a concrete action or respond.",
    "newsletters": "Opted-in newsletter, digest, or other recurring subscribed content.",
    "promotional": "Unsolicited marketing, sales, or promotional email.",
    "orders": (
        "An order, receipt, invoice, payment, shipping, delivery, or transaction update."
    ),
    "travel": (
        "A travel booking, itinerary, reservation, check-in, or transportation update."
    ),
    "updates": "An informational update that needs awareness but no action.",
}
LABEL_COLORS: dict[str, dict[str, str]] = {
    "action needed": {"backgroundColor": "#ffad47", "textColor": "#ffffff"},
    "awaiting reply": {"backgroundColor": "#fad165", "textColor": "#594c05"},
    "new sender": {"backgroundColor": "#a479e2", "textColor": "#ffffff"},
    "newsletters": {"backgroundColor": "#16a766", "textColor": "#ffffff"},
    "promotional": {"backgroundColor": "#999999", "textColor": "#ffffff"},
    "orders": {"backgroundColor": "#4a86e8", "textColor": "#ffffff"},
    "travel": {"backgroundColor": "#2da2bb", "textColor": "#ffffff"},
    "updates": {"backgroundColor": "#666666", "textColor": "#ffffff"},
}
_GMAIL_SYSTEM_LABELS = {
    "inbox",
    "sent",
    "draft",
    "spam",
    "trash",
    "unread",
    "starred",
    "important",
}

_LABEL_IDS_BY_ACCOUNT: dict[str, dict[str, str]] = {}
_LABEL_RESOURCES_BY_ACCOUNT: dict[str, list[dict[str, Any]]] = {}
_NO_REPLY_RE = re.compile(
    r"(?:^|[._+-])(?:no[._-]?reply|do[._-]?not[._-]?reply|donotreply)(?:[._+-]|$)",
    re.IGNORECASE,
)


def _gmail_token_paths() -> list[Path]:
    hermes_home = get_hermes_home()
    paths = [hermes_home / "google_token.json"]
    for path in sorted((hermes_home / "google-accounts").glob("*/google_token.json")):
        if path not in paths:
            paths.append(path)
    return paths


def _headers(raw_headers: Any) -> dict[str, list[str]]:
    headers: dict[str, list[str]] = {}
    if not isinstance(raw_headers, list):
        return headers
    for raw in raw_headers:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").lower()
        value = str(raw.get("value") or "")
        if name:
            headers.setdefault(name, []).append(value)
    return headers


def _first_header(headers: dict[str, list[str]], name: str) -> str:
    values = headers.get(name.lower()) or []
    return values[0] if values else ""


def _decode_gmail_body(data: str) -> str:
    try:
        padded = data + "=" * ((4 - len(data) % 4) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        return ""


def _html_to_text(value: str) -> str:
    value = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    return html.unescape(value)


def _message_body_texts(message: dict[str, Any]) -> list[str]:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return []
    texts: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType") or "")
        body = part.get("body") if isinstance(part.get("body"), dict) else {}
        data = body.get("data")
        if data and (mime_type.startswith("text/plain") or mime_type.startswith("text/html")):
            decoded = _decode_gmail_body(str(data))
            if mime_type.startswith("text/html"):
                decoded = _html_to_text(decoded)
            if decoded:
                texts.append(decoded)
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    return texts


def _compact(value: str, limit: int) -> str:
    compacted = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    if len(compacted) <= limit:
        return compacted
    return compacted[: max(0, limit - 3)].rstrip() + "..."


def _iso_timestamp() -> str:
    return datetime.now(tz=timezone.utc).isoformat().replace("+00:00", "Z")


def get_town_inbox_settings() -> tuple[bool, float, dict[str, str]]:
    """Read the Town inbox settings from the host's canonical config loader."""
    from hermes_cli.config import cfg_get, load_config_readonly

    config = load_config_readonly()
    entry = cfg_get(config, "plugins", "entries", "town-feed", default={})
    if entry is None:
        entry = {}
    if not isinstance(entry, dict):
        raise ValueError("plugins.entries.town-feed must be a mapping")

    labeling_enabled = entry.get("labeling_enabled", True)
    if not isinstance(labeling_enabled, bool):
        raise ValueError("plugins.entries.town-feed.labeling_enabled must be a boolean")

    raw_confidence = entry.get("label_confidence_min", MIN_LABEL_CONFIDENCE)
    if isinstance(raw_confidence, bool):
        raise ValueError(
            "plugins.entries.town-feed.label_confidence_min must be a number from 0 to 1"
        )
    try:
        confidence = float(raw_confidence)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "plugins.entries.town-feed.label_confidence_min must be a number from 0 to 1"
        ) from exc
    if not math.isfinite(confidence) or not 0 <= confidence <= 1:
        raise ValueError(
            "plugins.entries.town-feed.label_confidence_min must be a number from 0 to 1"
        )

    raw_taxonomy = entry.get("taxonomy", {})
    if raw_taxonomy is None:
        raw_taxonomy = {}
    if not isinstance(raw_taxonomy, dict):
        raise ValueError("plugins.entries.town-feed.taxonomy must be a mapping")

    labels = dict(DEFAULT_INBOX_TAXONOMY)
    for raw_label, raw_description in raw_taxonomy.items():
        label = str(raw_label or "").strip()
        description = str(raw_description or "").strip()
        folded_label = label.casefold()
        if not label or len(label) > 80:
            raise ValueError(
                "plugins.entries.town-feed.taxonomy key "
                f"{label!r} must be non-empty and at most 80 characters"
            )
        if folded_label in _GMAIL_SYSTEM_LABELS or folded_label.startswith("category_"):
            raise ValueError(
                "plugins.entries.town-feed.taxonomy key "
                f"{label!r} must not be a Gmail system label name"
            )
        if not description:
            raise ValueError(
                "plugins.entries.town-feed.taxonomy description for "
                f"{label!r} is empty"
            )
        labels[label] = description
    return labeling_enabled, confidence, labels


def _gmail_watermark_maps(
    state: MutableMapping[str, Any],
) -> tuple[MutableMapping[str, Any], MutableMapping[str, Any]]:
    source_state = state.get("source_state")
    if not isinstance(source_state, MutableMapping):
        source_state = {}
        state["source_state"] = source_state

    gmail_state = source_state.get("gmail")
    if not isinstance(gmail_state, MutableMapping):
        gmail_state = {}
        source_state["gmail"] = gmail_state

    gmail_sent_state = source_state.get("gmail_sent")
    if not isinstance(gmail_sent_state, MutableMapping):
        gmail_sent_state = {}
        source_state["gmail_sent"] = gmail_sent_state
    return gmail_state, gmail_sent_state


def _state_mapping(
    state: MutableMapping[str, Any], key: str
) -> MutableMapping[str, Any]:
    value = state.get(key)
    if not isinstance(value, MutableMapping):
        value = {}
        state[key] = value
    return value


def _state_id_list(
    mapping: MutableMapping[str, Any], account: str
) -> list[str]:
    raw = mapping.get(account)
    if isinstance(raw, list):
        values = raw
    elif isinstance(raw, MutableMapping):
        values = list(raw)
    else:
        values = []
    normalized = list(
        dict.fromkeys(
            str(value or "").strip()
            for value in values
            if str(value or "").strip()
        )
    )[-MAX_LOOKBACK_SEEN:]
    mapping[account] = normalized
    return normalized


def _references_curator(text: str) -> bool:
    folded = (text or "").casefold()
    return "town_feed_curate" in folded or "town feed curator" in folded


def _internal_date(message: dict[str, Any]) -> int:
    try:
        return max(0, int(message.get("internalDate") or 0))
    except (TypeError, ValueError):
        return 0


def _watermark(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _is_bulk_sender(address: str, has_list_unsubscribe: bool) -> bool:
    local_part = address.partition("@")[0]
    return has_list_unsubscribe or bool(_NO_REPLY_RE.search(local_part))


def _gmail_candidate(
    message: dict[str, Any], account: str, *, allow_empty_body: bool = False
) -> dict[str, Any] | None:
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = _headers(payload.get("headers") or message.get("headers") or [])
    sender = _first_header(headers, "from") or str(message.get("from") or "")
    subject = _first_header(headers, "subject") or str(message.get("subject") or "")
    date = _first_header(headers, "date") or str(message.get("date") or "")
    recipient = _first_header(headers, "to") or str(message.get("to") or "")
    message_id = str(message.get("id") or "").strip()
    thread_id = str(message.get("threadId") or message.get("thread_id") or "").strip()
    internal_date = _internal_date(message)
    if not message_id or not internal_date:
        return None

    from_name, from_address = email.utils.parseaddr(sender)
    sender_label = _compact(from_name or from_address or sender or "Unknown sender", 100)
    subject_text = re.sub(r"\s+", " ", html.unescape(subject or "")).strip()
    if not subject_text:
        subject_text = "(no subject)"
    subject_label = _compact(subject_text, 160)
    body = "\n".join(_message_body_texts(message))
    if not body:
        body = str(message.get("body") or message.get("text") or message.get("snippet") or "")
    body_for_classification = _compact(body, 2000)
    snippet = _compact(body, 300)
    if not snippet and not allow_empty_body:
        return None
    if not snippet:
        snippet = "(no body)"

    headline = f"{sender_label}: {subject_label}"
    if _references_curator(f"{headline} {snippet}"):
        return None

    canonical = "\n".join(
        (account.casefold(), (thread_id or message_id).casefold(), subject_text.casefold())
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    label_ids = {
        str(label)
        for label in message.get("labelIds") or []
        if isinstance(label, str)
    }
    system_label_ids = {label.upper() for label in label_ids}
    from_address = from_address.strip().casefold()
    has_list_unsubscribe = bool(_first_header(headers, "list-unsubscribe").strip())
    created_at = internal_date / 1000.0
    return {
        "key": digest[:16],
        "content_hash": digest,
        "source": f"gmail:{account}:{message_id}",
        "headline": headline,
        "summary": snippet,
        "created_at_ts": created_at,
        "from": sender,
        "from_address": from_address,
        "subject": subject_text,
        "date": date,
        "_awaiting_reply_to": _compact(recipient, 200),
        "snippet": snippet,
        "body": body_for_classification,
        "message_id": message_id,
        "thread_id": thread_id,
        "internal_date": internal_date,
        "account": account,
        "label_ids": sorted(label_ids),
        "unread": "UNREAD" in system_label_ids,
        "important": "IMPORTANT" in system_label_ids,
        "starred": "STARRED" in system_label_ids,
        "direction": (
            "outbound"
            if "SENT" in system_label_ids or from_address == account.casefold()
            else "inbound"
        ),
        "bulk_sender": _is_bulk_sender(from_address, has_list_unsubscribe),
    }


def scan_gmail(
    now: float,
    state: MutableMapping[str, Any],
    gmail_services: MutableMapping[str, Any] | None = None,
    labeling_messages: list[dict[str, Any]] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch new inbox messages and advance per-email internalDate watermarks.

    ``state`` is explicit script-owned state. Its Gmail watermarks are mutated
    only for accounts that resolve and scan successfully; the caller persists
    the state after scanning and labeling finish.
    """
    del now
    errors: list[str] = []
    watermarks, sent_watermarks = _gmail_watermark_maps(state)
    lookback_seen = _state_mapping(state, "lookback_seen")

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build
    except Exception as exc:
        return [], [f"gmail: Google API dependencies are unavailable: {exc}"]

    token_paths = _gmail_token_paths()
    existing_token_paths = [path for path in token_paths if path.exists()]
    if not existing_token_paths:
        return [], [
            "gmail: no OAuth tokens found; checked "
            + ", ".join(str(path) for path in token_paths)
        ]

    by_hash: dict[str, dict[str, Any]] = {}
    seen_labeling_messages: set[tuple[str, str]] = set()
    seen_candidate_messages: set[tuple[str, str]] = set()
    seen_accounts: set[str] = set()
    for token_path in existing_token_paths:
        try:
            creds = Credentials.from_authorized_user_file(
                str(token_path), scopes=list(GMAIL_SCOPES)
            )
            granted = set(
                getattr(creds, "granted_scopes", None)
                or getattr(creds, "scopes", None)
                or []
            )
            if granted and GMAIL_SCOPES[0] not in granted:
                raise RuntimeError("token does not include gmail.readonly scope")
            if creds.expired and creds.refresh_token:
                # Token files belong to the google-workspace skill. Refresh in
                # memory only; this scanner never writes shared credentials.
                creds.refresh(Request())
            if not creds.valid:
                raise RuntimeError("OAuth credentials are invalid or expired and could not be refreshed")

            service = build("gmail", "v1", credentials=creds, cache_discovery=False)
            profile = service.users().getProfile(userId="me").execute()
            account = str(profile.get("emailAddress") or "").strip().casefold()
            if not account:
                raise RuntimeError("Gmail profile did not return an account email")
            if account in seen_accounts:
                continue
            if gmail_services is not None:
                gmail_services[account] = service

            previous_watermark = _watermark(watermarks.get(account))
            listed = service.users().messages().list(
                userId="me",
                q=GMAIL_QUERY,
                maxResults=MAX_GMAIL_MESSAGES,
            ).execute()
            refs = listed.get("messages", []) or []
            next_watermark = previous_watermark
            for ref in refs:
                if not isinstance(ref, dict) or not ref.get("id"):
                    continue
                message = service.users().messages().get(
                    userId="me", id=ref["id"], format="full"
                ).execute()
                if not isinstance(message, dict):
                    continue
                internal_date = _internal_date(message)
                next_watermark = max(next_watermark, internal_date)
                if internal_date <= previous_watermark:
                    continue
                candidate = _gmail_candidate(message, account)
                if candidate is None:
                    continue
                labeling_key = (account, str(candidate["message_id"]))
                seen_candidate_messages.add(labeling_key)
                if (
                    labeling_messages is not None
                    and labeling_key not in seen_labeling_messages
                ):
                    labeling_messages.append(dict(candidate))
                    seen_labeling_messages.add(labeling_key)
                existing = by_hash.get(candidate["content_hash"])
                if (
                    existing is None
                    or candidate["created_at_ts"] > existing["created_at_ts"]
                ):
                    by_hash[candidate["content_hash"]] = candidate
            if next_watermark:
                watermarks[account] = next_watermark

            if labeling_messages is not None:
                try:
                    _enabled, _confidence, taxonomy = get_town_inbox_settings()
                    folded_taxonomy_names = {
                        name.casefold() for name in taxonomy
                    }
                    taxonomy_label_ids = {
                        str(resource.get("id") or "")
                        for resource in _gmail_label_resources(service, account)
                        if str(resource.get("name") or "").strip().casefold()
                        in folded_taxonomy_names
                        or str(resource.get("name") or "").strip().casefold()
                        in {f"town/{name}" for name in folded_taxonomy_names}
                    }
                    action_needed_label_ids = {
                        str(resource.get("id") or "")
                        for resource in _gmail_label_resources(service, account)
                        if str(resource.get("name") or "").strip().casefold()
                        in {"action needed", "town/action needed"}
                    }
                    account_lookback_seen = _state_id_list(lookback_seen, account)
                    account_lookback_seen_set = set(account_lookback_seen)
                    listed_lookback = service.users().messages().list(
                        userId="me",
                        q=GMAIL_LOOKBACK_QUERY,
                        maxResults=MAX_GMAIL_LOOKBACK_MESSAGES,
                    ).execute()
                    for ref in listed_lookback.get("messages", []) or []:
                        if not isinstance(ref, dict):
                            continue
                        lookback_message_id = str(ref.get("id") or "").strip()
                        if (
                            not lookback_message_id
                            or lookback_message_id in account_lookback_seen_set
                        ):
                            continue
                        message = service.users().messages().get(
                            userId="me", id=lookback_message_id, format="full"
                        ).execute()
                        if not isinstance(message, dict):
                            continue
                        message_label_ids = {
                            str(label_id)
                            for label_id in message.get("labelIds") or []
                            if isinstance(label_id, str)
                        }
                        carried = message_label_ids.intersection(taxonomy_label_ids)
                        if carried and not carried.intersection(action_needed_label_ids):
                            account_lookback_seen.append(lookback_message_id)
                            account_lookback_seen_set.add(lookback_message_id)
                            continue

                        candidate = _gmail_candidate(message, account)
                        candidate_key = (account, lookback_message_id)
                        if (
                            candidate is not None
                            and candidate_key not in seen_candidate_messages
                        ):
                            seen_candidate_messages.add(candidate_key)
                            if candidate_key not in seen_labeling_messages:
                                labeling_messages.append(dict(candidate))
                                seen_labeling_messages.add(candidate_key)
                            existing = by_hash.get(candidate["content_hash"])
                            if (
                                existing is None
                                or candidate["created_at_ts"] > existing["created_at_ts"]
                            ):
                                by_hash[candidate["content_hash"]] = candidate

                        account_lookback_seen.append(lookback_message_id)
                        account_lookback_seen_set.add(lookback_message_id)
                    lookback_seen[account] = account_lookback_seen[-MAX_LOOKBACK_SEEN:]
                except Exception as exc:
                    errors.append(f"gmail: {token_path}: lookback scan: {exc}")

            if labeling_messages is not None:
                previous_sent_watermark = _watermark(sent_watermarks.get(account))
                next_sent_watermark = previous_sent_watermark
                try:
                    listed_sent = service.users().messages().list(
                        userId="me",
                        q=GMAIL_SENT_QUERY,
                        maxResults=MAX_GMAIL_MESSAGES,
                    ).execute()
                    for ref in listed_sent.get("messages", []) or []:
                        if not isinstance(ref, dict) or not ref.get("id"):
                            continue
                        message = service.users().messages().get(
                            userId="me", id=ref["id"], format="full"
                        ).execute()
                        if not isinstance(message, dict):
                            continue
                        internal_date = _internal_date(message)
                        next_sent_watermark = max(next_sent_watermark, internal_date)
                        if internal_date <= previous_sent_watermark:
                            continue
                        candidate = _gmail_candidate(message, account)
                        if candidate is None:
                            continue
                        candidate["direction"] = "outbound"
                        labeling_key = (account, str(candidate["message_id"]))
                        if labeling_key not in seen_labeling_messages:
                            labeling_messages.append(dict(candidate))
                            seen_labeling_messages.add(labeling_key)
                    if next_sent_watermark:
                        sent_watermarks[account] = next_sent_watermark
                except Exception as exc:
                    errors.append(f"gmail: {token_path}: sent scan: {exc}")
            seen_accounts.add(account)
        except Exception as exc:
            errors.append(f"gmail: {token_path}: {exc}")

    candidates = sorted(
        by_hash.values(), key=lambda candidate: float(candidate["created_at_ts"]), reverse=True
    )
    return candidates, errors


def _gmail_label_resources(service: Any, account: str) -> list[dict[str, Any]]:
    cached = _LABEL_RESOURCES_BY_ACCOUNT.get(account)
    if cached is not None:
        return cached

    response = service.users().labels().list(userId="me").execute()
    resources: list[dict[str, Any]] = []
    for raw in response.get("labels", []) or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        label_id = str(raw.get("id") or "").strip()
        if name and label_id:
            resources.append(dict(raw))
    _LABEL_RESOURCES_BY_ACCOUNT[account] = resources
    return resources


def _gmail_label_ids(service: Any, account: str) -> dict[str, str]:
    cached = _LABEL_IDS_BY_ACCOUNT.get(account)
    if cached is not None:
        return cached

    label_ids = {
        str(raw["name"]): str(raw["id"])
        for raw in _gmail_label_resources(service, account)
    }
    _LABEL_IDS_BY_ACCOUNT[account] = label_ids
    return label_ids


def _ensure_gmail_label(service: Any, account: str, label: str) -> str:
    label_ids = _gmail_label_ids(service, account)
    folded_label = label.casefold()
    for existing_name, existing_id in label_ids.items():
        if existing_name.casefold() == folded_label:
            return existing_id

    body: dict[str, Any] = {
        "name": label,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    }
    color = LABEL_COLORS.get(folded_label)
    if color is not None:
        body["color"] = dict(color)
    created = service.users().labels().create(
        userId="me",
        body=body,
    ).execute()
    label_id = str(created.get("id") or "").strip()
    if not label_id:
        raise RuntimeError(f"Gmail did not return an id for newly created label {label!r}")
    label_ids[label] = label_id
    _gmail_label_resources(service, account).append(dict(created))
    return label_id


def _label_has_color(label: dict[str, Any]) -> bool:
    color = label.get("color")
    return isinstance(color, dict) and bool(
        str(color.get("backgroundColor") or "").strip()
        or str(color.get("textColor") or "").strip()
    )


def _patch_gmail_label_color(
    service: Any,
    account: str,
    label: dict[str, Any],
    bare_name: str,
    errors: list[str],
) -> None:
    color = LABEL_COLORS.get(bare_name.casefold())
    if color is None:
        return
    try:
        service.users().labels().patch(
            userId="me",
            id=str(label["id"]),
            body={"color": dict(color)},
        ).execute()
        label["color"] = dict(color)
    except Exception as exc:
        errors.append(
            f"labeler: {account}: {bare_name}: Gmail label color patch failed: {exc}"
        )


def _migrate_gmail_labels(
    service: Any,
    account: str,
    taxonomy: dict[str, str],
    state: MutableMapping[str, Any],
    errors: list[str],
    log_entries: list[dict[str, Any]],
) -> None:
    migrations = _state_mapping(state, "migrations")
    migration_v1 = migrations.get("label_deprefix_v1")
    if not isinstance(migration_v1, MutableMapping):
        migration_v1 = {}
        migrations["label_deprefix_v1"] = migration_v1

    try:
        resources = _gmail_label_resources(service, account)
        label_ids = _gmail_label_ids(service, account)
    except Exception as exc:
        errors.append(f"labeler: {account}: could not list Gmail labels for migration: {exc}")
        return

    if not str(migration_v1.get(account) or "").strip():
        migration_succeeded = True
        for bare_name in taxonomy:
            folded_name = bare_name.casefold()
            try:
                bare_label = next(
                    (
                        raw
                        for raw in resources
                        if str(raw.get("name") or "").strip().casefold() == folded_name
                    ),
                    None,
                )
                if bare_label is not None:
                    label_ids[bare_name] = str(bare_label["id"])
                    try:
                        details = service.users().labels().get(
                            userId="me", id=str(bare_label["id"])
                        ).execute()
                    except Exception as exc:
                        migration_succeeded = False
                        errors.append(
                            f"labeler: {account}: {bare_name}: Gmail label detail read failed: {exc}"
                        )
                        continue
                    if isinstance(details, dict):
                        bare_label.update(details)
                    if not _label_has_color(bare_label):
                        _patch_gmail_label_color(
                            service, account, bare_label, bare_name, errors
                        )
                    continue

                legacy_name = f"town/{folded_name}"
                legacy_label = next(
                    (
                        raw
                        for raw in resources
                        if str(raw.get("name") or "").strip().casefold() == legacy_name
                    ),
                    None,
                )
                if legacy_label is not None:
                    old_name = str(legacy_label["name"])
                    service.users().labels().patch(
                        userId="me",
                        id=str(legacy_label["id"]),
                        body={"name": bare_name},
                    ).execute()
                    legacy_label["name"] = bare_name
                    label_ids.pop(old_name, None)
                    label_ids[bare_name] = str(legacy_label["id"])
                    _patch_gmail_label_color(
                        service, account, legacy_label, bare_name, errors
                    )
                    continue

                _ensure_gmail_label(service, account, bare_name)
            except Exception as exc:
                migration_succeeded = False
                errors.append(
                    f"labeler: {account}: {bare_name}: Gmail label migration failed: {exc}"
                )

        if migration_succeeded:
            migration_v1[account] = _iso_timestamp()

    if not str(migration_v1.get(account) or "").strip():
        return

    migration_v2 = migrations.get("label_deprefix_v2")
    if not isinstance(migration_v2, MutableMapping):
        migration_v2 = {}
        migrations["label_deprefix_v2"] = migration_v2
    if str(migration_v2.get(account) or "").strip():
        return

    migration_succeeded = True
    for bare_name in taxonomy:
        folded_name = bare_name.casefold()
        legacy_name = f"town/{folded_name}"
        try:
            bare_label = next(
                (
                    raw
                    for raw in resources
                    if str(raw.get("name") or "").strip().casefold() == folded_name
                ),
                None,
            )
            legacy_label = next(
                (
                    raw
                    for raw in resources
                    if str(raw.get("name") or "").strip().casefold() == legacy_name
                ),
                None,
            )
            if legacy_label is None:
                continue

            actual_legacy_name = str(legacy_label.get("name") or "").strip()
            if not actual_legacy_name.casefold().startswith("town/"):
                raise RuntimeError(
                    f"refusing to mutate non-Town legacy label {actual_legacy_name!r}"
                )

            legacy_id = str(legacy_label["id"])
            if bare_label is None:
                service.users().labels().patch(
                    userId="me",
                    id=legacy_id,
                    body={"name": bare_name},
                ).execute()
                legacy_label["name"] = bare_name
                for cached_name, cached_id in list(label_ids.items()):
                    if (
                        cached_id == legacy_id
                        or cached_name.casefold() == actual_legacy_name.casefold()
                    ):
                        label_ids.pop(cached_name, None)
                label_ids[bare_name] = legacy_id
                _patch_gmail_label_color(
                    service, account, legacy_label, bare_name, errors
                )
                log_entries.append(
                    {
                        "ts": _iso_timestamp(),
                        "account": account,
                        "message_id": "",
                        "subject": f"Renamed {actual_legacy_name} to {bare_name}",
                        "label": bare_name,
                        "confidence": 1.0,
                        "action": "label_deprefix_v2_rename",
                    }
                )
                continue

            bare_id = str(bare_label["id"])
            message_ids: list[str] = []
            page_token: str | None = None
            more_remain = False
            while len(message_ids) < MAX_LABEL_DEPREFIX_MESSAGES:
                listed = service.users().messages().list(
                    userId="me",
                    labelIds=[legacy_id],
                    includeSpamTrash=True,
                    maxResults=min(
                        100, MAX_LABEL_DEPREFIX_MESSAGES - len(message_ids)
                    ),
                    **({"pageToken": page_token} if page_token else {}),
                ).execute()
                message_ids.extend(
                    str(raw["id"])
                    for raw in listed.get("messages", []) or []
                    if isinstance(raw, dict) and raw.get("id")
                )
                page_token = str(listed.get("nextPageToken") or "").strip() or None
                if not page_token:
                    break
            if page_token:
                more_remain = True
                migration_succeeded = False
                errors.append(
                    f"labeler: {account}: {actual_legacy_name}: more than 200 messages "
                    "carry the legacy label; migrated the bounded batch and skipped delete"
                )

            migrated_count = 0
            modify_failures: list[tuple[str, Exception]] = []
            for message_id in message_ids:
                if not actual_legacy_name.casefold().startswith("town/"):
                    raise RuntimeError(
                        f"refusing to remove non-Town label {actual_legacy_name!r}"
                    )
                try:
                    service.users().messages().modify(
                        userId="me",
                        id=message_id,
                        body={
                            "addLabelIds": [bare_id],
                            "removeLabelIds": [legacy_id],
                        },
                    ).execute()
                    migrated_count += 1
                except Exception as exc:
                    migration_succeeded = False
                    modify_failures.append((message_id, exc))
            if modify_failures:
                failed_message_id, first_exc = modify_failures[0]
                errors.append(
                    f"labeler: {account}: {actual_legacy_name}: "
                    f"{len(modify_failures)} message migrations failed "
                    f"(first {failed_message_id}: {first_exc})"
                )

            deleted = False
            if not more_remain:
                remaining = service.users().messages().list(
                    userId="me",
                    labelIds=[legacy_id],
                    includeSpamTrash=True,
                    maxResults=1,
                ).execute()
                if remaining.get("messages"):
                    migration_succeeded = False
                    errors.append(
                        f"labeler: {account}: {actual_legacy_name}: messages still carry "
                        "the legacy label after migration; skipped delete"
                    )
                else:
                    if not actual_legacy_name.casefold().startswith("town/"):
                        raise RuntimeError(
                            f"refusing to delete non-Town label {actual_legacy_name!r}"
                        )
                    service.users().labels().delete(
                        userId="me", id=legacy_id
                    ).execute()
                    resources.remove(legacy_label)
                    for cached_name, cached_id in list(label_ids.items()):
                        if (
                            cached_id == legacy_id
                            or cached_name.casefold() == actual_legacy_name.casefold()
                        ):
                            label_ids.pop(cached_name, None)
                    deleted = True

            log_entries.append(
                {
                    "ts": _iso_timestamp(),
                    "account": account,
                    "message_id": "",
                    "subject": (
                        f"Migrated {migrated_count} messages from "
                        f"{actual_legacy_name} to {bare_name}"
                    ),
                    "label": bare_name,
                    "confidence": 1.0,
                    "action": (
                        "label_deprefix_v2_cleanup"
                        if deleted
                        else "label_deprefix_v2_partial"
                    ),
                }
            )
        except Exception as exc:
            migration_succeeded = False
            errors.append(
                f"labeler: {account}: {bare_name}: Gmail label cleanup failed: {exc}"
            )

    if migration_succeeded:
        migration_v2[account] = _iso_timestamp()


def _taxonomy_label_map(
    service: Any, account: str, taxonomy: dict[str, str]
) -> dict[str, str]:
    label_ids = _gmail_label_ids(service, account)
    result = dict(label_ids)
    folded_ids = {name.casefold(): label_id for name, label_id in label_ids.items()}
    for name in taxonomy:
        label_id = folded_ids.get(name.casefold())
        if label_id:
            result[name] = label_id
    return result


def _add_gmail_label(service: Any, account: str, message_id: str, label: str) -> str:
    label_id = _ensure_gmail_label(service, account, label)
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={"addLabelIds": [label_id]},
    ).execute()
    return label_id


def _latest_thread_candidate(service: Any, account: str, thread_id: str) -> dict[str, Any]:
    response = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    messages = [
        message
        for message in response.get("messages", []) or []
        if isinstance(message, dict) and _internal_date(message)
    ]
    if not messages:
        raise RuntimeError(f"Gmail thread {thread_id!r} returned no messages")
    latest = max(messages, key=lambda message: (_internal_date(message), str(message.get("id") or "")))
    candidate = _gmail_candidate(latest, account, allow_empty_body=True)
    if candidate is None:
        raise RuntimeError(f"Gmail thread {thread_id!r} latest message is invalid")
    return candidate


def _known_correspondent(
    service: Any,
    account: str,
    address: str,
    cache: MutableMapping[str, Any],
) -> bool:
    account_cache = cache.setdefault(account, {})
    if not isinstance(account_cache, MutableMapping):
        account_cache = {}
        cache[account] = account_cache
    cached = account_cache.get(address)
    if (
        isinstance(cached, dict)
        and isinstance(cached.get("known"), bool)
        and str(cached.get("checked_at") or "").strip()
    ):
        return bool(cached["known"])

    response = service.users().messages().list(
        userId="me",
        q=f"in:sent to:{address}",
        maxResults=MAX_GMAIL_MESSAGES,
    ).execute()
    known = False
    for ref in response.get("messages", []) or []:
        if not isinstance(ref, dict) or not ref.get("id"):
            continue
        message = service.users().messages().get(
            userId="me", id=ref["id"], format="full"
        ).execute()
        if not isinstance(message, dict):
            continue
        body = "\n".join(_message_body_texts(message))
        if not body:
            body = str(message.get("body") or message.get("text") or "")
        if len(re.sub(r"\s+", " ", body).strip()) > 50:
            known = True
            break

    account_cache[address] = {"known": known, "checked_at": _iso_timestamp()}
    return known


def _skipped_message(
    candidate: dict[str, Any], reason: str, *, label: str = "skip", confidence: float = 0.0
) -> dict[str, Any]:
    return {
        "account": str(candidate.get("account") or ""),
        "message_id": str(candidate.get("message_id") or ""),
        "reason": reason,
        "label": label,
        "confidence": round(confidence, 3),
    }


def _label_log_entry(
    candidate: dict[str, Any], label: str, confidence: float
) -> dict[str, Any]:
    return {
        "ts": _iso_timestamp(),
        "account": str(candidate.get("account") or "").strip().casefold(),
        "message_id": str(candidate.get("message_id") or ""),
        "subject": _compact(str(candidate.get("subject") or "(no subject)"), 80),
        "label": label,
        "confidence": round(confidence, 3),
    }


def _deadline_iso(value: Any) -> str | None:
    text = _compact(str(value or ""), 80)
    if not text:
        return None
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return text


def _open_loop_entries(open_loops: MutableMapping[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_account, raw_threads in open_loops.items():
        if not isinstance(raw_threads, MutableMapping):
            continue
        account = str(raw_account or "").strip().casefold()
        for raw_thread_id, raw_loop in raw_threads.items():
            if not account or not isinstance(raw_loop, dict):
                continue
            thread_id = str(raw_thread_id or "").strip()
            if not thread_id:
                continue
            try:
                first_seen_ts = float(raw_loop.get("first_seen_ts") or 0.0)
                last_seen_ts = float(raw_loop.get("last_seen_ts") or first_seen_ts)
            except (TypeError, ValueError):
                continue
            if (
                not math.isfinite(first_seen_ts)
                or first_seen_ts < 0
                or not math.isfinite(last_seen_ts)
                or last_seen_ts < 0
            ):
                continue
            entries.append(
                {
                    "account": account,
                    "thread_id": thread_id,
                    "person": _compact(str(raw_loop.get("person") or ""), 160),
                    "subject": _compact(str(raw_loop.get("subject") or ""), 160),
                    "ask": _compact(str(raw_loop.get("ask") or ""), 140),
                    "deadline_iso": _deadline_iso(raw_loop.get("deadline_iso")),
                    "first_seen_ts": first_seen_ts,
                    "last_seen_ts": last_seen_ts,
                }
            )
    return sorted(entries, key=lambda entry: entry["first_seen_ts"])[
        :MAX_OPEN_LOOPS_PAYLOAD
    ]


def _cap_account_open_loops(account_loops: MutableMapping[str, Any]) -> None:
    if len(account_loops) <= MAX_OPEN_LOOPS_PER_ACCOUNT:
        return

    def first_seen(item: tuple[str, Any]) -> float:
        value = item[1]
        try:
            timestamp = float(value.get("first_seen_ts") or 0.0)
        except (AttributeError, TypeError, ValueError):
            return 0.0
        return timestamp if math.isfinite(timestamp) else 0.0

    for thread_id, _loop in sorted(account_loops.items(), key=first_seen)[
        : len(account_loops) - MAX_OPEN_LOOPS_PER_ACCOUNT
    ]:
        account_loops.pop(thread_id, None)


def _labeling_result(
    summary: dict[str, Any],
    log_entries: list[dict[str, Any]],
    known_correspondents: MutableMapping[str, Any],
    awaiting_reply_threads: MutableMapping[str, Any],
    open_loops: MutableMapping[str, Any],
    state: MutableMapping[str, Any],
) -> dict[str, Any]:
    summary["applied"] = len(log_entries)
    current_log = state.get("labeling_log")
    if not isinstance(current_log, list):
        current_log = []
    state["labeling_log"] = [
        dict(entry)
        for entry in current_log + log_entries
        if isinstance(entry, dict)
    ][-MAX_LABELING_LOG:]
    for account_loops in open_loops.values():
        if isinstance(account_loops, MutableMapping):
            _cap_account_open_loops(account_loops)
    state["known_correspondents"] = dict(known_correspondents)
    state["awaiting_reply_threads"] = dict(awaiting_reply_threads)
    state["open_loops"] = dict(open_loops)
    state["last_labeling_summary"] = dict(summary)
    return {
        "summary": summary,
        "log_entries": log_entries,
        "known_correspondents": dict(known_correspondents),
        "awaiting_reply_threads": dict(awaiting_reply_threads),
        "awaiting_reply": _awaiting_reply_entries(awaiting_reply_threads),
        "open_loops": _open_loop_entries(open_loops),
    }


def _awaiting_reply_entries(
    awaiting_reply_threads: MutableMapping[str, Any],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for raw_account, raw_threads in awaiting_reply_threads.items():
        if not isinstance(raw_threads, MutableMapping):
            continue
        account = str(raw_account or "").strip().casefold()
        for raw_thread_id, raw_thread in raw_threads.items():
            if not account or not isinstance(raw_thread, dict):
                continue
            thread_id = str(raw_thread_id or "").strip()
            if not thread_id:
                continue
            try:
                last_sent_ts = float(raw_thread.get("last_sent_ts"))
            except (TypeError, ValueError):
                labeled_at = str(raw_thread.get("labeled_at") or "").strip()
                try:
                    last_sent_ts = datetime.fromisoformat(
                        labeled_at.replace("Z", "+00:00")
                    ).timestamp()
                except (TypeError, ValueError):
                    last_sent_ts = 0.0
            if not math.isfinite(last_sent_ts) or last_sent_ts < 0:
                last_sent_ts = 0.0
            entries.append(
                {
                    "account": account,
                    "thread_id": thread_id,
                    "to": str(raw_thread.get("to") or ""),
                    "subject": str(raw_thread.get("subject") or ""),
                    "last_sent_ts": last_sent_ts,
                }
            )
    return sorted(entries, key=lambda entry: entry["last_sent_ts"])[:10]


def label_gmail_messages(
    candidates: list[dict[str, Any]],
    gmail_services: MutableMapping[str, Any],
    state: MutableMapping[str, Any],
    llm: Any,
) -> dict[str, Any]:
    """Classify one scan batch and add Town labels without changing Gmail state otherwise."""
    cycle_ts = _iso_timestamp()
    labeling_enabled, confidence_min, taxonomy = get_town_inbox_settings()
    candidate_outputs: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in candidates:
        candidate["town_label"] = None
        candidate["town_label_confidence"] = None
        key = (
            str(candidate.get("account") or "").strip().casefold(),
            str(candidate.get("message_id") or "").strip(),
        )
        candidate_outputs[key] = candidate

    def annotate_candidate(
        candidate: dict[str, Any], label: str | None, confidence: float | None
    ) -> None:
        key = (
            str(candidate.get("account") or "").strip().casefold(),
            str(candidate.get("message_id") or "").strip(),
        )
        output = candidate_outputs.get(key)
        if output is not None:
            if label is not None or output.get("town_label") is None:
                output["town_label"] = label
            output["town_label_confidence"] = confidence

    known_cache = _state_mapping(state, "known_correspondents")
    awaiting_threads = _state_mapping(state, "awaiting_reply_threads")
    open_loops = _state_mapping(state, "open_loops")
    errors: list[str] = []
    skipped: list[dict[str, Any]] = []
    eligible: list[tuple[str, dict[str, Any]]] = []
    label_maps: dict[str, dict[str, str]] = {}
    log_entries: list[dict[str, Any]] = []
    removed = 0

    summary: dict[str, Any] = {
        "ts": cycle_ts,
        "candidates": len(candidates),
        "eligible": 0,
        "classified": 0,
        "applied": 0,
        "removed": 0,
        "skipped": skipped,
        "errors": errors,
    }
    if not labeling_enabled:
        skipped.extend(
            _skipped_message(candidate, "labeling_disabled")
            for candidate in candidates
        )
        return _labeling_result(
            summary,
            log_entries,
            known_cache,
            awaiting_threads,
            open_loops,
            state,
        )

    candidates_by_account: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        account = str(candidate.get("account") or "").strip().casefold()
        message_id = str(candidate.get("message_id") or "").strip()
        if not account or not message_id:
            skipped.append(_skipped_message(candidate, "invalid_message"))
            continue
        candidates_by_account.setdefault(account, []).append(candidate)

    for account, service in gmail_services.items():
        account = str(account or "").strip().casefold()
        _migrate_gmail_labels(
            service, account, taxonomy, state, errors, log_entries
        )
        try:
            label_maps[account] = _taxonomy_label_map(service, account, taxonomy)
        except Exception as exc:
            errors.append(f"labeler: {account}: could not list Gmail labels: {exc}")

    for account, account_candidates in candidates_by_account.items():
        service = gmail_services.get(account)
        if service is None:
            errors.append(f"labeler: {account}: no Gmail service for scanned account")
            skipped.extend(
                _skipped_message(candidate, "gmail_unavailable")
                for candidate in account_candidates
            )
            continue
        if account not in label_maps:
            skipped.extend(
                _skipped_message(candidate, "gmail_error")
                for candidate in account_candidates
            )

    classifier_payload: list[dict[str, str]] = []
    processed_threads: set[tuple[str, str]] = set()
    ordered_candidates = sorted(
        candidates,
        key=lambda candidate: int(candidate.get("internal_date") or 0),
        reverse=True,
    )
    for scanned_candidate in ordered_candidates:
        candidate = scanned_candidate
        account = str(candidate.get("account") or "").strip().casefold()
        label_map = label_maps.get(account)
        if label_map is None:
            continue
        service = gmail_services[account]
        message_id = str(candidate.get("message_id") or "").strip()
        thread_id = str(candidate.get("thread_id") or "").strip()
        thread_key = (account, thread_id or f"message:{message_id}")
        if thread_key in processed_threads:
            skipped.append(_skipped_message(candidate, "duplicate_thread_candidate"))
            continue
        processed_threads.add(thread_key)

        if thread_id:
            try:
                candidate = _latest_thread_candidate(service, account, thread_id)
            except Exception as exc:
                errors.append(f"labeler: {account}: {thread_id}: thread read failed: {exc}")
                skipped.append(_skipped_message(scanned_candidate, "thread_error"))
                continue

        latest_message_id = str(candidate.get("message_id") or "").strip()
        latest_is_scanned = latest_message_id == message_id
        current_label_ids = {
            str(label_id)
            for label_id in candidate.get("label_ids") or []
            if isinstance(label_id, str)
        }
        direction = str(candidate.get("direction") or "inbound")
        account_threads = awaiting_threads.setdefault(account, {})
        if not isinstance(account_threads, MutableMapping):
            account_threads = {}
            awaiting_threads[account] = account_threads
        account_open_loops = open_loops.setdefault(account, {})
        if not isinstance(account_open_loops, MutableMapping):
            account_open_loops = {}
            open_loops[account] = account_open_loops
        tracked = account_threads.get(thread_id) if thread_id else None

        if direction == "inbound" and isinstance(tracked, dict):
            tracked_message_id = str(tracked.get("message_id") or "").strip()
            awaiting_label_id = label_map.get(AWAITING_REPLY_LABEL)
            if not awaiting_label_id:
                account_threads.pop(thread_id, None)
            elif tracked_message_id:
                try:
                    # The awaiting-reply lifecycle is the sole sanctioned
                    # removeLabelIds path: remove it from the outbound message
                    # recorded for this thread when its inbound reply arrives.
                    service.users().messages().modify(
                        userId="me",
                        id=tracked_message_id,
                        body={"removeLabelIds": [awaiting_label_id]},
                    ).execute()
                    account_threads.pop(thread_id, None)
                    current_label_ids.discard(awaiting_label_id)
                    removed += 1
                except Exception as exc:
                    errors.append(
                        f"labeler: {account}: {thread_id}: awaiting-reply removal failed: {exc}"
                    )
                    skipped.append(_skipped_message(scanned_candidate, "gmail_error"))
                    continue

        # Gmail may add IMPORTANT automatically, so it is not treated as a
        # user signal. STARRED, by contrast, is an explicit classification skip.
        if "STARRED" in {label_id.upper() for label_id in current_label_ids}:
            skipped.append(_skipped_message(candidate, "starred"))
            continue

        if direction == "outbound":
            if not thread_id:
                skipped.append(_skipped_message(candidate, "missing_thread_id"))
                continue
            account_open_loops.pop(thread_id, None)
            awaiting_label_id = label_map.get(AWAITING_REPLY_LABEL)
            if awaiting_label_id and awaiting_label_id in current_label_ids:
                account_threads[thread_id] = {
                    "message_id": latest_message_id,
                    "labeled_at": cycle_ts,
                    "to": str(candidate.get("_awaiting_reply_to") or ""),
                    "subject": str(candidate.get("subject") or ""),
                    "last_sent_ts": float(candidate.get("created_at_ts") or 0.0),
                }
                skipped.append(
                    _skipped_message(candidate, "already_awaiting_reply", label=AWAITING_REPLY_LABEL)
                )
                continue
            if isinstance(tracked, dict) and str(tracked.get("message_id") or "") == latest_message_id:
                skipped.append(
                    _skipped_message(candidate, "already_awaiting_reply", label=AWAITING_REPLY_LABEL)
                )
                continue
            try:
                _add_gmail_label(
                    service, account, latest_message_id, AWAITING_REPLY_LABEL
                )
            except Exception as exc:
                errors.append(
                    f"labeler: {account}: {latest_message_id}: Gmail label write failed: {exc}"
                )
                skipped.append(
                    _skipped_message(candidate, "gmail_error", label=AWAITING_REPLY_LABEL)
                )
                continue
            account_threads[thread_id] = {
                "message_id": latest_message_id,
                "labeled_at": cycle_ts,
                "to": str(candidate.get("_awaiting_reply_to") or ""),
                "subject": str(candidate.get("subject") or ""),
                "last_sent_ts": float(candidate.get("created_at_ts") or 0.0),
            }
            annotate_candidate(scanned_candidate, AWAITING_REPLY_LABEL, 1.0)
            log_entries.append(_label_log_entry(candidate, AWAITING_REPLY_LABEL, 1.0))
            if not latest_is_scanned:
                skipped.append(_skipped_message(scanned_candidate, "not_latest_in_thread"))
            continue

        if not latest_is_scanned:
            skipped.append(_skipped_message(scanned_candidate, "not_latest_in_thread"))
            continue

        town_label_ids = {
            label_map[name]
            for name in taxonomy
            if name in label_map
        }
        if current_label_ids.intersection(town_label_ids):
            # Already-labeled action mail (ours or Town's — labels are shared
            # post-adoption) still feeds curation and the open-loops memory;
            # everything else is skipped as before. No Gmail writes here.
            action_needed_id = label_map.get("action needed")
            if action_needed_id and action_needed_id in current_label_ids:
                annotate_candidate(scanned_candidate, "action needed", 1.0)
                if thread_id:
                    account_open_loops = open_loops.setdefault(account, {})
                    if not isinstance(account_open_loops, MutableMapping):
                        account_open_loops = {}
                        open_loops[account] = account_open_loops
                    existing_loop = account_open_loops.get(thread_id)
                    existing_loop = existing_loop if isinstance(existing_loop, dict) else {}
                    observed_ts = datetime.now(tz=timezone.utc).timestamp()
                    try:
                        first_seen_ts = float(existing_loop.get("first_seen_ts"))
                    except (TypeError, ValueError):
                        first_seen_ts = observed_ts
                    if not math.isfinite(first_seen_ts) or first_seen_ts < 0:
                        first_seen_ts = observed_ts
                    loop_name, loop_address = email.utils.parseaddr(
                        str(candidate.get("from") or "")
                    )
                    account_open_loops[thread_id] = {
                        "person": _compact(loop_name or loop_address or "Unknown sender", 120),
                        "subject": _compact(str(candidate.get("subject") or ""), 180),
                        "ask": existing_loop.get("ask")
                        or _compact(str(candidate.get("subject") or ""), 140),
                        "deadline_iso": existing_loop.get("deadline_iso"),
                        "first_seen_ts": first_seen_ts,
                        "last_seen_ts": float(candidate.get("created_at_ts") or observed_ts),
                    }
                skipped.append(
                    _skipped_message(candidate, "already_action_labeled", label="action needed")
                )
            else:
                skipped.append(_skipped_message(candidate, "already_town_labeled"))
            continue

        if not bool(candidate.get("bulk_sender")):
            from_address = str(candidate.get("from_address") or "").strip().casefold()
            if not from_address:
                _, from_address = email.utils.parseaddr(str(candidate.get("from") or ""))
                from_address = from_address.strip().casefold()
            if not from_address:
                skipped.append(_skipped_message(candidate, "invalid_sender"))
                continue
            try:
                known = _known_correspondent(
                    service, account, from_address, known_cache
                )
            except Exception as exc:
                errors.append(
                    f"labeler: {account}: {from_address}: known-correspondent check failed: {exc}"
                )
                skipped.append(_skipped_message(candidate, "known_correspondent_error"))
                continue
            if known:
                skipped.append(_skipped_message(candidate, "known_correspondent"))
                continue
            try:
                _add_gmail_label(service, account, message_id, NEW_SENDER_LABEL)
            except Exception as exc:
                errors.append(
                    f"labeler: {account}: {message_id}: Gmail label write failed: {exc}"
                )
                skipped.append(
                    _skipped_message(candidate, "gmail_error", label=NEW_SENDER_LABEL)
                )
                continue
            log_entries.append(_label_log_entry(candidate, NEW_SENDER_LABEL, 1.0))
            annotate_candidate(scanned_candidate, NEW_SENDER_LABEL, 1.0)
            # Fall through: first-time senders still get content classification
            # (a first-time receipt must reach the orders/notable-charge path).

        message_ref = f"message_{len(eligible) + 1}"
        eligible.append((message_ref, candidate))
        classifier_payload.append(
            {
                "message_ref": message_ref,
                "account": account,
                "from": _compact(str(candidate.get("from") or ""), 160),
                "subject": _compact(str(candidate.get("subject") or "(no subject)"), 160),
                "body": _compact(str(candidate.get("body") or ""), 2000),
            }
        )

    summary["eligible"] = len(eligible)
    summary["removed"] = removed
    if not eligible:
        return _labeling_result(
            summary,
            log_entries,
            known_cache,
            awaiting_threads,
            open_loops,
            state,
        )

    content_taxonomy = {
        label: description
        for label, description in taxonomy.items()
        if label not in {NEW_SENDER_LABEL, AWAITING_REPLY_LABEL}
    }
    allowed_choices = ["skip", *content_taxonomy.keys()]
    schema = {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "message_ref": {"type": "string"},
                        "label": {"type": "string", "enum": allowed_choices},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "ask": {"type": "string", "maxLength": 140},
                        "deadline": {
                            "anyOf": [
                                {"type": "string", "maxLength": 80},
                                {"type": "null"},
                            ]
                        },
                    },
                    "required": ["message_ref", "label", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["items"],
        "additionalProperties": False,
    }
    taxonomy_prompt = "\n".join(
        f"- {label}: {description}" for label, description in taxonomy.items()
    )
    completion_kwargs = {
        "instructions": (
            "Classify each inbox message using exactly one permitted content label from the "
            "taxonomy below. These messages are bulk or no-reply mail, so never use "
            "new sender or awaiting reply; those lifecycle labels are handled "
            "deterministically. Use the first-class choice 'skip' when no content label "
            "clearly fits; never force-fit a message. Return each supplied message_ref "
            "unchanged and exactly once, with confidence from 0 to 1. Classify from the "
            "provided body and do not invent message facts. For action needed, also return "
            "a short ask (at most 140 characters) describing what the sender wants and, "
            "when explicit, deadline as an ISO 8601 string; omit either optional field "
            "when the message does not support it.\n\n"
            f"Taxonomy:\n{taxonomy_prompt}"
        ),
        "input": [
            {
                "type": "text",
                "text": json.dumps(classifier_payload, ensure_ascii=False),
            }
        ],
        "json_schema": schema,
        "schema_name": "town_inbox_labeling",
        "temperature": 0.1,
        "max_tokens": 2500,
        "purpose": "town-inbox-labeling",
    }
    try:
        result = llm.complete_structured(**completion_kwargs)
    except Exception as exc:
        summary["errors"].append(f"labeler: classifier failed: {exc}")
        summary["skipped"].extend(
            _skipped_message(candidate, "classifier_error")
            for _, candidate in eligible
        )
        return _labeling_result(
            summary,
            log_entries,
            known_cache,
            awaiting_threads,
            open_loops,
            state,
        )

    parsed = getattr(result, "parsed", None)
    rows = parsed.get("items") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        summary["errors"].append("labeler: classifier returned no structured items")
        summary["skipped"].extend(
            _skipped_message(candidate, "classifier_error")
            for _, candidate in eligible
        )
        return _labeling_result(
            summary,
            log_entries,
            known_cache,
            awaiting_threads,
            open_loops,
            state,
        )

    eligible_refs = {message_ref for message_ref, _ in eligible}
    classified: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        message_ref = str(row.get("message_ref") or "")
        if message_ref in eligible_refs and message_ref not in classified:
            classified[message_ref] = row
    summary["classified"] = len(classified)

    for message_ref, candidate in eligible:
        row = classified.get(message_ref)
        if row is None:
            summary["skipped"].append(_skipped_message(candidate, "no_classification"))
            continue
        label = str(row.get("label") or "").strip()
        try:
            confidence = float(row.get("confidence"))
        except (TypeError, ValueError):
            confidence = -1.0
        if math.isfinite(confidence) and 0 <= confidence <= 1:
            annotate_candidate(candidate, None, confidence)
        if (
            not math.isfinite(confidence)
            or confidence < 0
            or confidence > 1
            or label not in allowed_choices
        ):
            summary["skipped"].append(_skipped_message(candidate, "invalid_classification"))
            continue
        if label == "skip":
            annotate_candidate(candidate, None, confidence)
            summary["skipped"].append(
                _skipped_message(candidate, "classifier_skip", confidence=confidence)
            )
            continue
        if confidence < confidence_min:
            annotate_candidate(candidate, None, confidence)
            summary["skipped"].append(
                _skipped_message(
                    candidate,
                    "low_confidence",
                    label=label,
                    confidence=confidence,
                )
            )
            continue

        account = str(candidate.get("account") or "").strip().casefold()
        service = gmail_services[account]
        annotate_candidate(candidate, label, confidence)
        try:
            _add_gmail_label(
                service, account, str(candidate["message_id"]), label
            )
        except Exception as exc:
            summary["errors"].append(
                f"labeler: {account}: {candidate.get('message_id')}: Gmail label write failed: {exc}"
            )
            summary["skipped"].append(
                _skipped_message(candidate, "gmail_error", label=label, confidence=confidence)
            )
            continue

        log_entries.append(_label_log_entry(candidate, label, confidence))
        if label.casefold() == "action needed":
            thread_id = str(candidate.get("thread_id") or "").strip()
            if thread_id:
                account_loops = open_loops.setdefault(account, {})
                if not isinstance(account_loops, MutableMapping):
                    account_loops = {}
                    open_loops[account] = account_loops
                existing_loop = account_loops.get(thread_id)
                existing_loop = (
                    existing_loop if isinstance(existing_loop, dict) else {}
                )
                observed_ts = datetime.now(tz=timezone.utc).timestamp()
                try:
                    first_seen_ts = float(existing_loop.get("first_seen_ts"))
                except (TypeError, ValueError):
                    first_seen_ts = observed_ts
                if not math.isfinite(first_seen_ts) or first_seen_ts < 0:
                    first_seen_ts = observed_ts
                from_name, from_address = email.utils.parseaddr(
                    str(candidate.get("from") or "")
                )
                person = (
                    from_name
                    or from_address
                    or str(candidate.get("from_address") or "")
                )
                subject = _compact(
                    str(candidate.get("subject") or "(no subject)"), 160
                )
                account_loops[thread_id] = {
                    "person": _compact(person, 160),
                    "subject": subject,
                    "ask": _compact(str(row.get("ask") or subject), 140),
                    "deadline_iso": _deadline_iso(row.get("deadline")),
                    "first_seen_ts": first_seen_ts,
                    "last_seen_ts": observed_ts,
                }
                _cap_account_open_loops(account_loops)

    return _labeling_result(
        summary,
        log_entries,
        known_cache,
        awaiting_threads,
        open_loops,
        state,
    )
