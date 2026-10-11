#!/usr/bin/env python3
"""Daily Telegram action items report v2 - direct Bot API delivery."""
import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import urllib.parse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode

HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")).expanduser()
# Persisted summary artifact (read downstream by the Morning briefing cron).
ARTIFACT_DIR = HERMES_HOME / "cron" / "output" / "telegram-action-items-direct"
HERMES_AGENT = HERMES_HOME / "hermes-agent"
if HERMES_AGENT.exists():
    sys.path.insert(0, str(HERMES_AGENT))

# Bootstrap BEFORE importing agent dependencies. PM chooses a matching Python
# and activates its packages; forcing the legacy .venv mixes Python ABIs.
import hermes_bootstrap  # noqa: F401
from agent import auxiliary_client

DAEMON_URL = "http://127.0.0.1:18784"
CHAT_ID = 372430020
CRON_THREAD_ID = os.environ.get("TELEGRAM_CRON_THREAD_ID", "").strip()


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


BOT_TOKEN = get_bot_token()
BOT_USER_ID = int(BOT_TOKEN.split(":", 1)[0])
# Bot's own chat ID in TDLib (bot's user ID, not Tom's) - skip bot/Tom meta-chatter.
SELF_CHAT_IDS = {CHAT_ID, BOT_USER_ID}

# Broadcast/news channels and bot chats to skip
SKIP_CHAT_NAMES = {
    "Wu Blockchain News",
    "aggrnews",
    "Club Lido",
    "dYdX Official Announcements",
    "Bybit API Discussion",
    "DODO Official Community",
    "Handshake (HNS)",
    # Telegram system bots
    "Telegram",
    "BotFather",
    "Spambot",
    "Nicegram",
}

_chat_cache = None
_muted_chat_ids = None


# ── Telegram delivery ────────────────────────────────────────────────────────

def send_telegram(text: str, parse_mode: str | None = "Markdown") -> bool:
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if CRON_THREAD_ID:
        payload["message_thread_id"] = int(CRON_THREAD_ID)
    payload = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.load(resp)
            return result.get("ok", False)
    except urllib.error.HTTPError as e:
        # Markdown parse errors are content-specific (unpaired */_ in LLM output);
        # retry once as plain text so the report still lands.
        body = ""
        try:
            body = e.read().decode(errors="replace")
        except Exception:
            pass
        if "can't parse entities" in body:
            print("Markdown parse rejected — retrying as plain text.", file=sys.stderr)
            return send_telegram(text, parse_mode=None)
        print(f"Telegram send error: {e}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"Telegram send error: {e}", file=sys.stderr)
        return False


def delivery_note(no_send: bool, ok: bool) -> str:
    if no_send:
        return "Telegram send skipped (--no-send test run)."
    return "Delivered to Telegram." if ok else "Telegram delivery FAILED."


def persist_summary(summary: str, delivered_note: str) -> None:
    """Persist the summary so downstream jobs (e.g. Morning briefing) can read it."""
    try:
        ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        text = f"# Telegram action items — {stamp}\n{delivered_note}\n\n{summary}\n"
        (ARTIFACT_DIR / f"{stamp}.md").write_text(text)
        (ARTIFACT_DIR / "latest.md").write_text(text)
    except Exception as e:
        print(f"Artifact write failed: {e}", file=sys.stderr)


# ── Daemon helpers ───────────────────────────────────────────────────────────

def get_muted_chats() -> set:
    global _muted_chat_ids
    if _muted_chat_ids is not None:
        return _muted_chat_ids
    _muted_chat_ids = set()
    try:
        with urllib.request.urlopen(f"{DAEMON_URL}/muted-chats", timeout=15) as resp:
            for c in json.load(resp).get("muted_chats", []):
                _muted_chat_ids.add(c["id"])
    except Exception as e:
        print(f"Warning: couldn't fetch muted chats: {e}", file=sys.stderr)
    return _muted_chat_ids


def get_chats() -> dict:
    global _chat_cache
    if _chat_cache is None:
        muted = get_muted_chats()
        with urllib.request.urlopen(f"{DAEMON_URL}/chats", timeout=30) as resp:
            raw = {c["id"]: c["title"] for c in json.load(resp).get("chats", [])}
            _chat_cache = {
                cid: name for cid, name in raw.items()
                if cid not in muted and cid not in SELF_CHAT_IDS
                and name not in SKIP_CHAT_NAMES
            }
    return _chat_cache


def get_messages(chat_id: int, limit: int = 100, cutoff_ts: int = 0) -> list:
    # Always bypass sync state; pass cutoff_ts so daemon stops paginating early
    path = f"/messages?{urlencode({'chat_id': chat_id, 'limit': limit, 'from_message_id': 0, 'cutoff_ts': cutoff_ts})}"
    with urllib.request.urlopen(f"{DAEMON_URL}{path}", timeout=60) as resp:
        return json.load(resp).get("messages", [])


def fmt_date(ts) -> str:
    if isinstance(ts, int):
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return str(ts)


# ── LLM ─────────────────────────────────────────────────────────────────────

def get_llm_summary(messages_text: str) -> str:
    prompt = f"""You are helping Tom Schmidt track action items from his Telegram messages.

Identify ONLY items that require Tom's attention:
1. Direct requests or questions TO Tom by name or clearly addressed to him
2. Promises Tom made that are still pending ("I'll do X", "will send", "will check")
3. One-on-one conversations where someone is clearly waiting on Tom's reply

IGNORE:
- Group chat banter, memes, or general discussion not directed at Tom
- FYIs, announcements, news, links shared without asking Tom anything
- Messages where Tom has already replied and the thread is resolved
- Broadcast channels or one-way info feeds

Messages are grouped by chat. Each chat section starts with === Chat Name === followed by messages in chronological order formatted as: [date] Sender Name: message text
Use the sender name and conversation flow to determine if Tom has an open action item.

Format your response as a clean, Telegram-friendly Markdown bullet list (no intro sentence):
🔵 *Chat Name* — _Sender_: brief description of what needs attention

Use emojis as bullets, bold the chat name with `*Chat Name*`, italicize the sender with `_Sender_`, and keep each item to one line.

If nothing qualifies, output exactly: ✅ No action items.

Messages:
---
{messages_text}
---"""

    # Explicit timeout: task='monitor' falls back to _DEFAULT_AUX_TIMEOUT (60s), which
    # a ~330-message prompt exceeds — 2026-09-15 run delivered "LLM error: Request
    # timed out." Pass a generous timeout so large message volumes still summarize.
    try:
        response = auxiliary_client.call_llm(
            task="monitor",
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1024,
            timeout=300,
        )
        summary = (response.choices[0].message.content or "").strip()
        if not summary or summary.startswith("LLM error:"):
            raise RuntimeError("Summarizer returned empty or error content")
        return summary
    except Exception as e:
        raise RuntimeError("Telegram action-items summarization failed; no digest published") from e


# ── Apple Reminders ──────────────────────────────────────────────────────────

def add_to_reminders(summary: str) -> int:
    """Parse action item bullets from summary and add each to Daily Followups list.
    Returns number of items added. Sets due date to today at 5 PM."""
    added = 0
    # Preflight: when TCC Reminders access is "Not determined", remindctl blocks on an
    # unanswered consent prompt (hangs past timeout, adds nothing). Skip until granted.
    try:
        status = subprocess.run(["remindctl", "status"], capture_output=True, text=True, timeout=15)
        if "Not determined" in (status.stdout or ""):
            print(
                "remindctl: Reminders TCC access 'Not determined' — skipping reminder adds "
                "until permission is granted at the Mac.",
                file=sys.stderr,
            )
            return 0
    except Exception as e:
        print(f"remindctl status preflight failed ({e}) — skipping reminder adds.", file=sys.stderr)
        return 0
    # Due today at 5 PM local time
    due_today = datetime.now().strftime("%Y-%m-%d") + " 17:00"
    for line in summary.splitlines():
        line = line.strip()
        # Match lines starting with the bullet emoji
        if not line.startswith("🔵"):
            continue
        # Strip the emoji and clean up markdown bold/italic markers.
        clean = re.sub(r"[\*_]([^\*_]+)[\*_]", r"\1", line[1:].strip())
        # Truncate to reasonable reminder title length
        title = clean[:200]
        try:
            result = subprocess.run(
                ["remindctl", "add", "--title", title, "--list", "Daily Followups",
                 "--due", due_today],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                added += 1
            else:
                print(f"remindctl error: {result.stderr.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"remindctl exception: {e}", file=sys.stderr)
    return added


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--no-send",
        action="store_true",
        help="Skip Telegram delivery and reminder adds; still write the artifact.",
    )
    args = parser.parse_args()
    cutoff_ts = int((datetime.now(timezone.utc) - timedelta(days=1)).timestamp())

    chats = get_chats()
    all_messages = []

    for chat_id, chat_name in chats.items():
        try:
            for m in get_messages(chat_id, limit=100, cutoff_ts=cutoff_ts):
                date = m.get("date", 0)
                text = m.get("text", "")
                if date >= cutoff_ts and text and len(text) > 5:
                    all_messages.append({
                        "chat": chat_name,
                        "date": fmt_date(date),
                        "sender": m.get("sender_name", "Unknown"),
                        "text": text[:500],
                    })
        except Exception as e:
            print(f"Error fetching {chat_name}: {e}", file=sys.stderr)

    print(f"Collected {len(all_messages)} messages from {len(chats)} non-muted chats.", file=sys.stderr)

    if not all_messages:
        ok = False if args.no_send else send_telegram("📋 *Action items* _24h_\n\n✅ _No messages found._")
        persist_summary("✅ No messages found.", delivery_note(args.no_send, ok))
        if not args.no_send and not ok:
            raise RuntimeError("Telegram delivery failed")
        return

    # Group by chat, sort each chat chronologically (oldest → newest)
    from collections import defaultdict
    by_chat = defaultdict(list)
    for m in all_messages:
        by_chat[m["chat"]].append(m)

    # Sort chats by their most recent message (most active first)
    sorted_chats = sorted(by_chat.keys(), key=lambda c: max(m["date"] for m in by_chat[c]), reverse=True)

    sections = []
    for chat_name in sorted_chats:
        msgs = sorted(by_chat[chat_name], key=lambda m: m["date"])  # oldest → newest within chat
        lines = [f"[{m['date']}] {m['sender']}: {m['text']}" for m in msgs]
        sections.append(f"=== {chat_name} ===\n" + "\n".join(lines))

    messages_text = "\n\n".join(sections)

    summary = get_llm_summary(messages_text)

    # Retry once if LLM says no action items but we analyzed a lot of messages
    if ("No action items" in summary or "no action items" in summary.lower()) and len(all_messages) >= 100:
        print(f"LLM returned no action items despite {len(all_messages)} messages — retrying…", file=sys.stderr)
        summary = get_llm_summary(messages_text)

    date_str = datetime.now(timezone.utc).strftime("%b %d")
    if args.no_send:
        ok = False
        print("Dry run: Telegram send skipped.", file=sys.stderr)
    else:
        ok = send_telegram(f"📋 *Action items* — _{date_str}_\n\n{summary}")
        print("Delivered." if ok else "Delivery failed.", file=sys.stderr)
    persist_summary(summary, delivery_note(args.no_send, ok))
    if not args.no_send and not ok:
        raise RuntimeError("Telegram delivery failed")

    # Add each action item to Apple Reminders
    if not args.no_send and "✅ No action items" not in summary:
        n = add_to_reminders(summary)
        print(f"Added {n} item(s) to Daily Followups reminders.", file=sys.stderr)


if __name__ == "__main__":
    main()
