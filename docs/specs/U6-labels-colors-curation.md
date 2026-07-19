# U6 — Label colors + de-prefixing, and label-driven curation

*2026-07-18, from Tom's review request. Evidence (live, both Gmail
accounts + Town home): (a) our labels are `Town/`-prefixed and
colorless gray; Town's on <work-domain> are bare lowercase WITH
colors (`newsletters` green, `action needed` orange) — rows carry both
as duplicates; (b) our curation discards the town_inbox classification
after labeling — the curator re-guesses importance from raw
headline/snippet, every newsletter/promo competes as a candidate, and
suggestions are derivative rewrites of top NTK items. Town's
suggestions for the same inbox: "Reply to M. — missed an event
registration deadline" (stale-thread re-raise with deadline awareness),
"Research an unexpected vendor charge" (receipt
analysis), "Prep talking points for Sunday's weekly sync" (event
prep). Conventions: docs/specs/README.md binds (incl. no self-commits).*

## Lane L — gateway/town-feed/dashboard/google_scan.py + gateway/scripts/town-gmail-scan.py

*(Scope amended after the first run: the top-level cron payload is
assembled in gateway/scripts/town-gmail-scan.py, not google_scan.py —
the script must propagate the new `awaiting_reply` list and the
enriched candidates. Keep the script changes minimal: plumbing only.)*

### L1. De-prefix the taxonomy (bare lowercase names)

`DEFAULT_INBOX_TAXONOMY` keys become bare, lowercase: `new sender`,
`awaiting reply`, `action needed`, `newsletters`, `promotional`,
`orders`, `travel`, `updates` (same descriptions; update
NEW_SENDER_LABEL / AWAITING_REPLY_LABEL constants). Replace the
config-override validation that REQUIRES the `Town/` prefix (~line 185)
with: non-empty, ≤80 chars, must not equal (case-insensitive) a Gmail
system label name (INBOX, SENT, DRAFT, SPAM, TRASH, UNREAD, STARRED,
IMPORTANT, CATEGORY_*). Update the labeling prompt text that mentions
Town/-prefixed lifecycle labels (~line 923) to the bare names.

### L2. Colors (Gmail API label palette ONLY)

Add a module-level `LABEL_COLORS: dict[str, dict[str, str]]` keyed by
bare label name, values `{"backgroundColor": ..., "textColor": ...}`.
Gmail rejects colors outside its fixed palette — use EXACTLY these
(all palette-legal):

- `action needed`   → bg `#ffad47` text `#ffffff`  (orange — matches Town)
- `awaiting reply`  → bg `#fad165` text `#594c05`  (yellow, dark text)
- `new sender`      → bg `#a479e2` text `#ffffff`  (purple)
- `newsletters`     → bg `#16a766` text `#ffffff`  (green — matches Town)
- `promotional`     → bg `#999999` text `#ffffff`  (gray)
- `orders`          → bg `#4a86e8` text `#ffffff`  (blue)
- `travel`          → bg `#2da2bb` text `#ffffff`  (teal)
- `updates`         → bg `#666666` text `#ffffff`  (dark gray)

Applied on label CREATE (body includes `color`) and during migration
(patch). Wrap every color patch in try/except: a rejected color logs to
errors and never blocks labeling. Custom taxonomy overrides without a
color entry simply get no color.

### L3. One-time migration (runs inside the scan, per account, idempotent)

Before labeling, for each taxonomy label name:
1. If a label whose name matches BARE name case-insensitively exists →
   adopt its id (and exact existing name/case — Town's `newsletters` /
   `action needed` on <work-domain> get ADOPTED, never duplicated).
   Do NOT recolor adopted labels that already have a color; set color
   only if the existing label has none.
2. Elif `Town/<Name>` (the old name, original casing) exists → PATCH it:
   new bare name + color. Renaming preserves the label id and every
   message association.
3. Else → create bare + color.
Record `migrations["label_deprefix_v1"][account] = ISO timestamp` in the
script state so the rename/adopt scan runs once per account (label
lookup/create stays as normal afterward). Migration errors are
per-label try/except → errors list, never fatal.

### L4. Enrich the emitted payload (contract for Lane C)

- Each gmail candidate dict emitted in the scan payload gains:
  `"town_label": "<bare label or null>"` and
  `"town_label_confidence": <float 0-1 or null>` — the town_inbox
  classification result for that message when one was produced (the
  labeling pass already computes this; thread it through instead of
  discarding). Messages skipped by labeling (starred, kill-switch,
  below MIN_LABEL_CONFIDENCE) emit null label with the raw confidence
  when available.
- The payload gains a top-level `"awaiting_reply": [...]` — up to 10
  entries derived from the existing `awaiting_reply_threads` state:
  `{"account", "thread_id", "to": <recipient display/address>,
  "subject", "last_sent_ts": <float>}`, oldest first. Purely from
  state — no new API calls.

## Lane C — gateway/town-feed/dashboard/plugin_api.py ONLY

### C1. Label-aware candidate scoring

Where gmail-sourced candidates are scored (study the existing heuristic
scoring in the curator path), consume `town_label` defensively:
- `action needed` → strong boost (should reliably reach the NTK top
  when fresh); `new sender` → moderate boost; `awaiting reply` → mild
  boost.
- `newsletters` / `promotional` → EXCLUDED from NTK candidates entirely
  (never headline material). `orders` / `travel` / `updates` → strong
  demotion, but keep as candidates (they feed suggestions context).
- Missing/unknown `town_label` → current behavior unchanged.
Also fix the `label.startswith("Town/")` check in the label-stats path
(~line 329): accept the bare taxonomy names (count anything the scan
payload reports as a label application; do not hardcode the prefix).

### C2. Suggestions — independent analysis, not NTK paraphrase

Rework the suggestions section of the curator LLM instruction. Feed it
distinct signal groups (build from data already reaching the curator
after L4 — keep each group capped and compact):
1. Action mail: candidates labeled `action needed` (or high-score
   gmail) INCLUDING ones older than the feed window that are still
   undismissed — with their dates, so the model can re-raise a missed
   deadline ("Reply to X — deadline passed Friday").
2. Awaiting replies: the `awaiting_reply` payload entries → follow-up
   nudges ("Nudge <person> about <subject> — sent 3 days ago").
3. Money/orders: candidates labeled `orders` with amounts visible in
   the snippet → verification/investigation suggestions ("Investigate the vendor charge") — only when notable (unusual amount or
   unrecognized merchant; the model judges).
4. Upcoming events (next 48h from the schedule/next_event data) →
   preparation suggestions ("Prep talking points for Sunday's
   weekly sync").
Rules stay: verb-led imperatives ≤70 chars, person/event-centric,
NO ops jargon, mix of kinds, at most one suggestion per underlying
signal, prefer the four groups above over paraphrasing NTK items
(paraphrase only to fill when groups are thin).

### C3. DoD note

Old feed-state.json and old scan payloads (no town_label /
awaiting_reply keys) must keep working — everything defensive.

## Deploy (orchestrator, after both lanes reviewed)

Copy plugin → ~/.hermes/plugins/town-feed/ (google_scan.py is loaded
from the INSTALLED copy by the script shim); scripts unchanged on disk
(scanner script imports installed google_scan). Idle-gate → kickstart
local.hermes-serve + relaunch app. Then `hermes cron run
town-gmail-scan && hermes cron run town-feed-curate`; verify in Gmail
UI (bare colored labels on new mail; Town/ labels renamed — check both
accounts) and /feed suggestions quality.

## DoD per lane

Lane L: `python3 -m py_compile gateway/town-feed/dashboard/google_scan.py`
passes; no files outside google_scan.py. Lane C: `python3 -m py_compile
gateway/town-feed/dashboard/plugin_api.py gateway/town-feed/__init__.py`
passes; no files outside plugin_api.py. Both: no git commits, report
what the spec got wrong instead of working around it.
