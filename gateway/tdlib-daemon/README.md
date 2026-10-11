# tdlib-daemon — Telegram user-session HTTP API

`telegram-tdlib-daemon.py` keeps one TDLib **user** session open and serves a
small read-only JSON API on `http://127.0.0.1:18784` (loopback only). The
Telegram scanners (`gateway/scripts/town-telegram-scan.py`,
`gateway/scripts/telegram-action-items-v2.py`) read from it.

## Runtime location contract

| What | Where |
|---|---|
| Script | `$HERMES_HOME/scripts/telegram-tdlib-daemon.py` (copy of this folder's file) |
| LaunchAgent | label `local.hermes-tdlib-daemon`, `~/Library/LaunchAgents/local.hermes-tdlib-daemon.plist`, rendered from `local.hermes-tdlib-daemon.example.plist` |
| Interpreter | the existing venv that has `tdjson` installed (path supplied at install time) |
| Log | `$HERMES_HOME/logs/tdlib-daemon.log` (script log lines + launchd stdout/stderr) |
| Session | `~/.tdlib_daemon/` — `config.json` (api id/hash), `sync-state.json`, `tdlib_files/` (TDLib database). Unchanged from the legacy install; the script location does not matter. |
| Port | `127.0.0.1:18784` |

Never run two daemons at once: they would share the TDLib database in
`~/.tdlib_daemon/` and the port. Importing the module has no side effects
(no `tdjson`, no directories, no log handlers) — `main()` does the setup.

## API

All endpoints are `GET`. Fields marked *new* are additive; every older field
keeps its name, type, and meaning.

- `/health` → `{"status": "ok"}`
- `/me` → `{user_id, first_name, last_name, username, usernames}`
  - *new* `username`: first active username or `""`; *new* `usernames`: all
    active usernames (case as set in Telegram — compare case-insensitively).
- `/chats` → `{"chats": [{id, title, type, member_count?, user_id?, is_bot?}]}`
  (first 100 chats of the main list); `/muted-chats` → `{"muted_chats": [...]}`
  with the same entry shape (first 200 chats, muted only).
  - *new* `type`: `private` | `group` | `supergroup` | `channel` | `secret` |
    `unknown` (a supergroup with `is_channel` is `channel`).
  - *new* `member_count`: groups/supergroups/channels, only when TDLib knows it
    locally (omitted otherwise).
  - *new* `user_id`, `is_bot`: private chats only (`is_bot` omitted if the user
    lookup fails).
- `/messages?chat_id=…[&limit=50][&cutoff_ts=…][&from_message_id=…]` →
  `{"messages": [...]}`, newest first. **Without `from_message_id` the
  request reads from, and then advances, the per-chat sync state** — scanners
  pass `from_message_id=0`.
  - `id`, `date`, `text` (`messageText` text only; `""` for media),
    `sender_user_id` + `sender_name` (user senders only).
  - *new* `sender_chat_id`: the sender is a chat (channel post, anonymous admin).
  - *new* `is_outgoing`: sent by the session owner.
  - *new* `content_type`: `text` | `photo` | `video` | `voice` | `video_note` |
    `sticker` | `animation` | `document` | `audio` | `poll` | `location` |
    `contact` | `other`.
  - *new* `caption`: media caption text, only when non-empty.
  - *new* `mention_user_ids` (ints, `textEntityTypeMentionName`) and
    `mention_usernames` (lowercased, no `@`, `textEntityTypeMention`), from the
    text or the caption. Always present (possibly empty), so a missing key means
    an older daemon.
  - *new* `reply_to_message_id`: the replied-to message, same chat only.
    `reply_to_sender_user_id` / `reply_to_sender_name` when the target's sender
    is a user and resolvable — from the same response first, else one
    `getMessage` per distinct target, at most 20 per request (stops early after
    a TDLib timeout).
- `/search?query=…[&chat_id=…][&limit=50]`, `/sync-status` — unchanged.

## Install / cut-over from a legacy agent

Run on the gateway host. Set the variables first; `LEGACY_LABEL` is the label
of the LaunchAgent currently running the daemon (on the reference install,
`ai.openclaw.tdlib-daemon`).

```sh
REPO=/path/to/hermes-town-plugin
HERMES_HOME="$HOME/.hermes"
TDLIB_PYTHON=/path/to/venv-with-tdjson/bin/python3
LEGACY_LABEL=ai.openclaw.tdlib-daemon
NEW=local.hermes-tdlib-daemon
PLIST="$HOME/Library/LaunchAgents/$NEW.plist"

# 1. Pre-flight (nothing running is touched yet)
"$TDLIB_PYTHON" -c 'import tdjson'
"$TDLIB_PYTHON" -m py_compile "$REPO/gateway/tdlib-daemon/telegram-tdlib-daemon.py"

# 2. Install the script and the rendered LaunchAgent
mkdir -p "$HERMES_HOME/scripts" "$HERMES_HOME/logs"
install -m 755 "$REPO/gateway/tdlib-daemon/telegram-tdlib-daemon.py" \
  "$HERMES_HOME/scripts/telegram-tdlib-daemon.py"
sed -e "s|__HERMES_HOME__|$HERMES_HOME|g" -e "s|__TDLIB_PYTHON__|$TDLIB_PYTHON|g" \
  "$REPO/gateway/tdlib-daemon/local.hermes-tdlib-daemon.example.plist" > "$PLIST"
plutil -lint "$PLIST"
! grep -q '__[A-Z_]*__' "$PLIST" || echo "unrendered placeholder in $PLIST"

# 3. Cut over. The legacy agent is KeepAlive/RunAtLoad: disable it (persists
#    across logins) before booting it out. Its plist file stays in place.
launchctl disable "gui/$(id -u)/$LEGACY_LABEL"
launchctl bootout "gui/$(id -u)/$LEGACY_LABEL"
while lsof -nP -iTCP:18784 -sTCP:LISTEN >/dev/null 2>&1; do sleep 1; done
launchctl bootstrap "gui/$(id -u)" "$PLIST"

# 4. Verify (prints keys only — no message content)
sleep 5
curl -s http://127.0.0.1:18784/health
curl -s http://127.0.0.1:18784/me | python3 -c 'import json,sys; print(sorted(json.load(sys.stdin)))'
curl -s http://127.0.0.1:18784/chats | python3 -c \
  'import json,sys; c=json.load(sys.stdin)["chats"]; print(len(c), sorted({k for x in c for k in x}))'
tail -n 20 "$HERMES_HOME/logs/tdlib-daemon.log"
launchctl print "gui/$(id -u)/$NEW" | grep -E 'state|pid'
```

If `launchctl bootstrap` returns `Input/output error`, wait a few seconds and
re-run it (`launchctl load -w "$PLIST"` is the fallback).

## Rollback

The legacy script and plist are never modified, and `sync-state.json` keeps
its format, so rolling back restores the previous daemon exactly.

```sh
launchctl bootout "gui/$(id -u)/local.hermes-tdlib-daemon"
while lsof -nP -iTCP:18784 -sTCP:LISTEN >/dev/null 2>&1; do sleep 1; done
launchctl enable "gui/$(id -u)/$LEGACY_LABEL"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/$LEGACY_LABEL.plist"
curl -s http://127.0.0.1:18784/health
# Keep the new agent from loading at the next login:
rm "$HOME/Library/LaunchAgents/local.hermes-tdlib-daemon.plist"
```

## Tests

`gateway/tests/test_r1a_daemon.py` (synthetic TDLib dicts, no `tdjson`):
`npm run test:gateway`.
