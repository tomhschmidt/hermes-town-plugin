# U10 — Curation parity: coverage, memory, noise, detail

*2026-07-18, from the live Town-vs-ours divergence analysis (all six
items approved). Root causes verified: work calendar invisible
(primary-only query — a shared work calendar holds the real
schedule), watermark-only gmail (pre-install mail never enters),
no durable thread memory, cron/session noise at source, flat orders
demotion, snippet-only summaries. Conventions: docs/specs/README.md
binds (incl. NO lane git commits).*

## Payload contract (both lanes code against this)

- Gmail candidate dicts gain `"body"`: message body, ≤1500 chars,
  gmail candidates only.
- Scan payload gains top-level `"open_loops"`: up to 15 entries,
  oldest-first: `{account, thread_id, person (display name or address),
  subject, ask (≤140 chars, what the sender wants), deadline_iso
  (ISO string or null), first_seen_ts, last_seen_ts}`.
- Calendar events keep their shape; each event MAY add `"calendar"`
  (display name of the source calendar). `next_event` unchanged.

## Lane S — scanners (gateway/scripts/town-calendar-scan.py,
## gateway/scripts/town-gmail-scan.py, gateway/town-feed/dashboard/google_scan.py)

### S1. Full calendar coverage (calendar script)

Replace the single `calendarId="primary"` query per account with the
account's `calendarList` (users/me/calendarList): every entry with
`selected != false`, skipping calendars whose id ends in
`#holiday@group.v.calendar.google.com` or equals a birthdays feed.
Query each with the existing timeMin/timeMax/singleEvents parameters.
Dedupe events by event `id` ACROSS calendars and accounts (shared
calendars appear in both accounts). Existing caps, candidate logic,
next_event, and quiet-day payload behavior unchanged. Per-calendar
failures append to errors and never kill the scan.

### S2. Gmail 7-day lookback (google_scan + script plumbing)

Alongside the watermark stream, each scan additionally lists
`in:inbox -in:chats newer_than:7d` (cap 40 refs) and processes ONLY
messages that carry none of our taxonomy label ids and are absent from
a new script-state `lookback_seen` set (account → message ids, capped
500, prunable). Matches feed both labeling AND candidate emission like
watermark messages, then enter `lookback_seen` (so each historical
message is considered exactly once). Watermarks unchanged.

### S3. Open-loops state (google_scan)

New script-state mapping `open_loops[account][thread_id]` maintained in
the labeling pass: created/updated when a message classifies
`action needed` (fields per the payload contract; `ask` from the
classifier — extend the town_inbox classification schema with an
optional short `ask` string and optional `deadline` the model extracts;
absent → derive ask from subject, deadline null). Entries REMOVED when
the sent-mail pass detects the owner replied to that thread (the
awaiting-reply lifecycle already detects owner outbound per thread —
reuse that signal). Cap 40 per account, oldest evicted. Emit the
payload `open_loops` list (both accounts merged, oldest-first, cap 15).

### S4. Body pass-through (script)

The enriched candidates the script emits gain `body` (≤1500 chars) from
the scan candidate's existing body field. Nothing else in the payload
changes.

## Lane C — curator (gateway/town-feed/dashboard/plugin_api.py)

### C1. Noise at source

- `_scan_cron`: STOP emitting cron-run documents as NTK candidates
  entirely (all jobs — Recents + run history own that surface). Keep
  ingesting town-scan payloads, next_event, schedule, awaiting_reply,
  cron-health failure candidates, and everything else.
- `_scan_sessions`: emit a session candidate ONLY when the session
  shows real work (inspect available fields; at minimum require a
  meaningful outcome/summary beyond an echo of a short prompt — report
  what signal you used), and collapse near-duplicates (normalized
  headline seen this scan → keep newest only).

### C2. Notable-charge path

In the label-aware scoring: `orders` candidates whose headline+summary
match the existing `_AMOUNT_RE` with any amount ≥ $100 (parse the
number; currency symbol prefixes) get +15 INSTEAD of −30, and the
ranking instruction tells the model to lead such headlines with the
amount and merchant. Routine/no-amount or <$100 orders keep −30.

### C3. Open loops → suggestions + dismiss-close

- Parse payload `open_loops` defensively (mirror awaiting_reply
  handling: validate, dedupe by (account, thread_id), oldest-first,
  cap 15).
- The suggestions action-mail signal group is UNIONED with open loops
  (one entry per loop: person, ask, deadline, age) — loops persist
  across item eviction, which is what makes "M., still" work.
- Suppression: when an NTK item whose source is a gmail thread in an
  open loop gets dismissed, record the thread key in a new
  `state["open_loop_suppressed"]` list (cap 100); suppressed loops are
  excluded from the signal group. Additive state key, defensive reads.

### C4. Body-aware detail extraction

Gmail candidates' `body` (when present) is included in the LLM ranking
payload rows (≤1500 chars each; keep MAX_CANDIDATES_FOR_LLM in mind —
if total payload size is a concern, include body only for the top 15
candidates by heuristic score). Instruction addition: headlines and
summaries for mail must extract the concrete specifics from the body —
amounts, quantities, dates, deadlines, names — Town-style ("a notable amount
charge from an SaaS vendor — Enterprise plan, 15 seats, Jul 18–Aug 18"),
never vague snippet echoes.

## DoD per lane

Lane S: `python3 -m py_compile` on all three files; no files outside
them. Lane C: `python3 -m py_compile gateway/town-feed/dashboard/plugin_api.py
gateway/town-feed/__init__.py`; no files outside plugin_api.py. Both:
old payloads/state must keep working (all new keys optional), NO git
commits, report the exact schema/signal decisions you made and anything
the spec got wrong. Do NOT deploy, restart, or touch ~/.hermes.
