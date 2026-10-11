# Town Feed cron scripts

Deploy these four files together to `~/.hermes/scripts/`. Hermes cron resolves the stored basenames inside that directory; each job runs with `no_agent: true` and `deliver: local`.

| Script | stdout JSON contract | Exit codes | Persistent state |
|---|---|---|---|
| `town-gmail-scan.py` | Always prints `{"candidates": [...], "labeling": {"applied": n, "skipped": [...], "errors": [...]}, "watermarks_advanced": bool}`. Candidates carry `recipients` (To/Cc `{email, name?}`, at most 8) for the curator's people context. | `0` success, `2` config/state, `3` plugin/Hermes import, `4` auxiliary classifier, `5` Gmail API or labeling. | Owns `~/.hermes/town-feed/gmail-state.json` and `.gmail-state.lock`; on first load it copies Gmail-owned legacy fields from the plugin's `feed-state.json` without modifying that file. |
| `town-telegram-scan.py` | Prints `{"candidates": [...]}` only when changed threads exist; successful quiet scans have empty stdout. | `0` success, `2` config/state, `3` runtime import, `5` local Telegram daemon API. | Owns `~/.hermes/town-feed/telegram-state.json` and `.telegram-state.lock` with per-chat watermarks. |
| `town-calendar-scan.py` | Prints `{"candidates": [...], "next_event": {...} | null, "events": [...]}` when events exist (see [Calendar time and event types](#calendar-time-and-event-types)); no scoped tokens or no events produce empty stdout. | `0` success/quiet, `2` token/config, `3` Google API imports, `5` Calendar auth/API. | No state file. Reads `~/.hermes/google_token.json` and `~/.hermes/google-accounts/*/google_token.json`; refreshes credentials in memory only. |
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

## Calendar time and event types

`town-calendar-scan.py` renders every human-readable time in the operator's
zone, resolved in order: `TOWN_TIMEZONE`, `home_timezone` from the installed
plugin's `operator_profile.py` (when present), Hermes `config.yaml`
top-level `timezone`, then the host zone. Invalid names fall through.
`start_iso`/`end_iso` stay exact instants in whatever offset Google sent;
all-day dates anchor to local midnight.

Copies of one event across calendars or accounts (same event id, same
`iCalUID` + start, or same title + start + end) collapse to one row:
`calendars` lists the distinct calendar names, attendees are unioned by
email, and `attendee_count` is the true union size. Self-declined events are
dropped. Each row carries `event_type` (Google `eventType`, default
`default`), `ical_uid` when Google sends one, and, for working-location
rows, `working_location` (`{"type": "home" | "office" | "custom",
"label"?}`). Need-to-Know candidates are timed `default`/`fromGmail`
events starting within 24 h that have another attendee, a conference link,
or a location; `next_event` is the earliest future timed `default`/`fromGmail`
event. Working-location, birthday, focus-time, and out-of-office rows are
schedule-only.

## People directory

`town-gmail-scan.py` also maintains `~/.hermes/town-feed/people-cache.json`
(mode `0600`, written atomically, at most 5000 people) through the installed
plugin's `people.py`: who each address is (name, org, title), its
relationship to the operator (`colleague` > `contact` > `correspondent` >
`new`), and when email was last exchanged. Per connected account it reads,
read-only and at most once per 24 h (a failed source retries after an hour):

- Google Contacts via the People API `people/me` connections
  (`names,emailAddresses,organizations`), only when the account's token
  carries `contacts.readonly` (otherwise skipped; one stderr line a day);
- To/Cc recipients of up to 500 SENT messages from the last 180 days
  (`messages.list` + `messages.get(format=metadata)`).

Inbound real-person senders and the operator's own sends seen by every scan
merge in incrementally. A colleague is anyone on a `work_domains` domain from
`operator.json` or on a non-consumer domain of the operator's own addresses.
The refresh runs before the scan's own errors are printed and never changes
the exit code or stdout; failures log one `town-gmail-scan: people refresh:`
line with addresses redacted. `town-calendar-scan.py` reads the cache to add
`name`, `org`, `relationship`, and `last_contact_ts` to each non-self
attendee (no cache, no enrichment) and flags the operator's own addresses
`self`, which also survive the 8-entry attendee cap.
