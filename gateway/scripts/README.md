# Town Feed cron scripts

Deploy these four files together to `~/.hermes/scripts/`. Hermes cron resolves the stored basenames inside that directory; each job runs with `no_agent: true` and `deliver: local`.

| Script | stdout JSON contract | Exit codes | Persistent state |
|---|---|---|---|
| `town-gmail-scan.py` | Always prints `{"candidates": [...], "labeling": {"applied": n, "skipped": [...], "errors": [...]}, "watermarks_advanced": bool}`. | `0` success, `2` config/state, `3` plugin/Hermes import, `4` auxiliary classifier, `5` Gmail API or labeling. | Owns `~/.hermes/town-feed/gmail-state.json` and `.gmail-state.lock`; on first load it copies Gmail-owned legacy fields from the plugin's `feed-state.json` without modifying that file. |
| `town-telegram-scan.py` | Prints `{"candidates": [...]}` only when changed threads exist; successful quiet scans have empty stdout. | `0` success, `2` config/state, `3` runtime import, `5` local Telegram daemon API. | Owns `~/.hermes/town-feed/telegram-state.json` and `.telegram-state.lock` with per-chat watermarks. |
| `town-calendar-scan.py` | Prints `{"candidates": [...], "next_event": {...} | null}` when events exist; no scoped tokens or no events produce empty stdout. | `0` success/quiet, `2` token/config, `3` Google API imports, `5` Calendar auth/API. | No state file. Reads `~/.hermes/google_token.json` and `~/.hermes/google-accounts/*/google_token.json`; refreshes credentials in memory only. |
| `town-curate.py` | Always prints the installed plugin's curator result object, including `{"ok": false, "error": ...}` on setup/state failures. | `0` success, `2` curator state/result, `3` plugin/Hermes import, `4` heuristic fallback after auxiliary LLM failure. | The installed plugin owns `~/.hermes/plugins/town-feed/feed-state.json` and its lock. |

Deploy from the repository root:

```bash
install -d -m 700 ~/.hermes/scripts
install -m 700 gateway/scripts/town-{gmail-scan,telegram-scan,calendar-scan,curate}.py ~/.hermes/scripts/
install -m 600 gateway/scripts/town_telegram_common.py ~/.hermes/scripts/
```

`town_telegram_common.py` is a pure, stdlib-only helper shared by
`town-telegram-scan.py` and the daily digest `telegram-action-items-v2.py`
(installed in the same directory): operator identity from the TDLib
daemon's `/me`, DM/group/channel typing, `(date, id)` message order, the
`[YOU]` / `↩ <name>` / `@you` line markers, the addressing policy (DMs
always; groups only when someone replies to, @mentions, or names the
operator, or the operator left an unanswered promise), and local-time
rendering. Each script loads it by path from its own directory, so it must
be installed next to them; without it the scanner exits `3` and the digest
fails before any delivery.

Cron jobs should reference the installed basenames, not paths in the checkout.
