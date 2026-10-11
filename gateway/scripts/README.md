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
```

Cron jobs should reference the installed basenames, not paths in the checkout.

## Python runtime

Hermes cron runs `.py` scripts on the package manager's (PM) dependency
environment interpreter. `town-gmail-scan.py`, `town-calendar-scan.py`, and
`town-curate.py` bootstrap like `telegram-action-items-v2.py`: put
`~/.hermes/hermes-agent` on `sys.path`, `import hermes_bootstrap`, then import
agent and Google modules, all in-process. None of them re-executes itself or
points at the pre-PM `hermes-agent` venv; loading that venv's Python against
PM's packages fails on compiled modules
(`No module named 'pydantic_core._pydantic_core'`).

A manual run under another Python minor version (a pyenv or system `python3`)
fails fast before touching any state: exit `3`, the usual stdout contract, and
one stderr line naming the interpreter to use, resolved through PM's own API,
for example:

```text
town-gmail-scan: Python 3.10 cannot load Hermes' managed environment (Python 3.14); run: <PM environment>/bin/python ~/.hermes/scripts/town-gmail-scan.py
```

On a Python too old to load Hermes' launcher modules at all, the line points
at `hermes cron run <job_id>` instead.

## Gmail watermark hold

`town-gmail-scan.py` advances each account's inbox watermark only over
messages whose labeling pass finished. When the classifier fails for a batch
(skip reason `classifier_error`, exit `4`), the account's watermark stops at
the newest handled message older than its oldest unclassified one, and the
unclassified ids leave the 7-day lookback seen-list, so the next run sends
them to the classifier again. Handled messages newer than the hold are
re-scanned, and the labeler skips them again. The watermark never moves
below its pre-run value (no backfill), sent-mail watermarks advance as before, and
`watermarks_advanced` reports the saved state. Successful classification
advances watermarks exactly as before; label removal is unchanged.
