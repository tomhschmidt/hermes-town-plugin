# CHARACTER spec pack (C1–C7) — context & character for the dashboard

*Drafted 2026-07-28 from a review of the population pipeline, prompted by
two Town.com Daily Digests (2026-07-27/28) that outclass our surfaces on
context, and an NTK screenshot showing a raw Telegram dump. Shared
conventions: docs/specs/README.md binds; scripts-for-sensing /
agents-for-judgment (G6R) binds; write envelopes unchanged.*

**STATUS: IMPLEMENTED 2026-07-28 (all of C1–C7), NOT YET DEPLOYED.**
Verified: `npm run check` + build clean; py_compile clean; 76 synthetic
checks over the new pure logic (`_schedule_context`, scanners, signal
groups, `_llm_rank` contract, day-brief gating/persistence//feed serving,
v5→v6 state compat). Deploy needs: scanner scripts →
`~/.hermes/scripts/`, gateway plugin → `~/.hermes/plugins/town-feed/`
(restart BOTH gateway processes), `dist/` → desktop plugins folder.
Deviations from spec: callouts carry no `event_keys` (every callout click
opens the Schedule dialog — smallest honest version); trip detection uses
an explicit `lodging_hint`/`multi_day` flag from the scanner rather than
metro inference; `awaiting_replies_chat` signals only fire on cycles
where the thread has fresh messages or while the item is retained
(scanner reports on watermark advance).

## Why: what Town does that we structurally cannot

From the two digests and the NTK screenshot, concretely:

1. **Narrative day frame** — "Day two of SBC week — you've got a packed
   one today." Day-count of a multi-day context + a load verdict.
2. **Trip awareness** — infers "you're in San Francisco for the week"
   from the all-day Ritz-Carlton stay event, then re-anchors everything
   (times, travel) to the trip.
3. **Timezone intelligence** — "times are in EDT on your calendar
   (3 PM EDT = noon PDT), so worth double-checking you're oriented for
   local Palo Alto time."
4. **Physical-world reasoning** — "Three events start within 30 minutes
   of each other in PDT … These physically conflict. Worth picking your
   priority before the afternoon." / "venue is in Redwood City (Selby's),
   so factor in travel from SF."
5. **Event-detail extraction** — reservation holder + confirmation
   number, street addresses, attendee names/emails, platform (Meet/Zoom/
   StreamYard) pulled from event location/description fields.
6. **Relationship context** — "new external meeting — no CRM context
   yet. Good to do a quick prep before jumping on."
7. **Stakes on deadlines** — "your Pro trial expires tomorrow; routines
   and the full assistant lock if you don't upgrade" (consequence, not
   just date).
8. **Voice** — second person, warm-direct, gives verdicts and
   recommendations; never reads like a machine log.

Meanwhile our NTK card showed: headline `Hunter Horsley: I'm free
tomorrow night…` (which is **Tom's own message**, misattributed by the
`chat_name: last_gist` headline format) over a summary that is three raw
`Tom Schmidt: …` message lines. No takeaway, no action, wrong speaker.

## Review: how each surface is populated today

**Sensing (mechanical scripts, correct per G6R, but lossy):**

- `gateway/scripts/town-calendar-scan.py` — 48h window. NTK candidate =
  `"title — in 2h"` + `"location · N attendees · first description
  line"` (`_event_details`). But the **schedule payload** (`events[]`,
  lines 310–322) carries only `summary/start_iso/end_iso/all_day/
  account/calendar`, and `next_event` (335–341) only
  `summary/start_iso/account`. Location, attendees, description,
  organizer, conference link, and the event's own `start.timeZone` are
  **dropped at the edge** — nothing downstream can reason about venues,
  people, or TZ skew.
- `gateway/scripts/town-telegram-scan.py` — per chat: headline =
  `f"{chat_name}: {last_gist(60)}"` (speaker conflated with chat name,
  the misattribution above), summary = last **3** raw `Sender: text`
  lines (`_candidate`, 238–262). No `body`, so the curator physically
  lacks the thread context to summarize even if prompted. Threads where
  Tom spoke last surface as news instead of "awaiting their reply".
- `gateway/scripts/town-gmail-scan.py` — the good citizen: candidates
  carry `body` (1500 chars), `town_label`, plus `awaiting_reply`,
  `open_loops`, and a persistent `known_correspondents` set in scanner
  state.

**Judgment (curator, `plugin_api.py`):**

- `_llm_rank` (1213–1404) can rewrite headline/summary per item, but:
  `body` is attached **only for gmail** candidates (1280–1285); the
  enriched instructions have extraction rules **for mail only** — no
  conversation/telegram rules, no synthesis across items, no voice
  guidance beyond "concise factual".
- Suggestion signals (`_suggestion_signal_groups`, 1050–1179):
  `events_next_48h` passes `summary/start_at/starts_in/account/all_day`
  — again no location/attendees, so event-prep chips can't cite people
  or venues, and conflict/travel chips are impossible.
- Items are ranked **independently**; there is no output slot for
  anything day-level (narrative, conflicts, trip state).

**Presentation (`src/town-home/plugin.tsx`):**

- Greeting (3299–3316): `Good {daypart}, Tom.` + date + one mechanical
  `contextLine` — `Next: {event} · {relative}` → waiting count → next
  routine. No narrative slot.
- NTK card (2517–2560): headline + **one hard-truncated summary line**
  (`truncate`, 2532) + source chip + age. Even a great 2-sentence
  curator summary dies here.
- Schedule dialog (1622–1670): `time – time summary [account]` day
  groups. No location, no conflict flags (the data isn't there).
- Briefing card (2965–3076): last morning-brief cron run status + 200
  chars of raw output. Not a rendered brief.

**Root cause, one sentence:** context is discarded at the sensing edge,
the judgment layer neither receives enough input nor has an output slot
for day-level synthesis, and the renderer only has slots for atoms.

## The pack

### C1 — Carry full event context through the pipeline

Calendar scanner: extend each `events[]` row (and the NTK candidate)
with `location` (240c), `attendees` (up to 8 × `{name-or-email,
response}` + total count), `description_excerpt` (bounded ~500c —
capture confirmation numbers, reservation names, agendas),
`organizer`, `conference` (Meet/Zoom/etc. from conferenceData/
hangoutLink, else description sniff), `event_tz` (`start.timeZone` when
present), and `multi_day: bool` for all-day spans ≥2 days. Keep
`next_event` slim but add `location`. Bump the scanner's enriched-
contract marker so `_scan_cron`/`_town_scan_schedule_events` accept the
new fields; `/feed`'s `schedule` passes them through; `normalizeTownSchedule`
(town-home 658–688) learns the optional fields. Schedule dialog renders
location + conference glyph per row. Pure plumbing, no LLM change; the
fields are already in the Google API response. Unblocks C2/C3/C6.
Files: gateway/scripts/town-calendar-scan.py, gateway plugin_api.py,
src/town-feed (bridge types), src/town-home.

### C2 — Deterministic day-shape computation (`schedule_context`)

In the curator (pure interval math over C1 events, no LLM — sensing
principle holds): (a) **conflicts** — overlapping non-all-day event
pairs and <30min-apart starts; (b) **back-to-back chains** (gap ≤10min);
(c) **tz skew** — events whose `event_tz` differs from the primary
calendar tz; (d) **trip candidates** — multi-day all-day events, plus
lodging/flight keyword match (`hotel|inn|ritz|airbnb|stay|flight|
UA\d+`); (e) **load stats** — meeting count, total booked hours,
first/last commitment, largest free block; (f) **evening cluster** —
2+ events starting within 45min after 5pm local. Emit as a
`schedule_context` dict persisted next to `schedule` in feed state and
included in `/feed`. All of it is auditable arithmetic; the LLM
narrates it but never invents it.
Files: gateway plugin_api.py only.

### C3 — The day brief: one narrative + callouts (the centerpiece)

New curator output `day_brief`, generated by a **second, small
structured call** in the curation cycle (separate from ranking so
neither prompt degrades):

- **Input:** C1 `events[]` + C2 `schedule_context` + `next_event` +
  trip candidates + (C4) awaiting-reply signals + top NTK headlines.
- **Output schema:** `{ day_summary: string ≤160c, callouts: [{kind:
  "conflict"|"travel"|"timezone"|"prep"|"deadline"|"trip", text ≤200c,
  event_keys: [...]}] (≤4), trip: {label, day_n, total_days}|null }`.
- **Prompt:** chief-of-staff voice (see C7); every callout must cite a
  supplied fact (event key or schedule_context entry) — *"three of
  tonight's events start within 30 minutes"* is legal only because C2
  computed it; recommendations/verdicts encouraged ("worth picking a
  priority"), fabrication banned; skip callouts when the day is plain
  (empty is a valid, expected output).
- **Regeneration policy (cost):** rebuild only when the
  `schedule_context` hash changes, the local date rolls, or the brief is
  >60min stale; otherwise reuse persisted `day_brief`. Ranking cadence
  is untouched.
- **Render:** header `contextLine` (town-home 3310) ← `day_summary`
  (mechanical Next:-line remains the fallback when `day_brief` is
  absent); under it a slim **Today strip** — up to 3 callout rows, icon
  by kind (Codicon: warning/location/clock-ish), one line each,
  clicking a callout with `event_keys` opens the Schedule dialog. Trip
  state renders as a small `SBC week · day 2` badge next to the date.
Files: gateway plugin_api.py, gateway/scripts/town-curate.py (schema
passthrough only if needed), src/town-feed (bridge), src/town-home.

### C4 — Telegram: summarize at the judgment layer, fix attribution

Mirror the gmail body path instead of adding an LLM to the scanner:

- Scanner: include bounded thread context as `body` — last ~12 messages
  or 1200 chars, `Sender: text` lines, newest last, with a first line
  `participants: A, B; last_speaker: X; user: Tom Schmidt`. Fix the
  headline to attribute the actual last speaker:
  `f"{chat_name} — {last_sender}: {last_gist}"`. Emit
  `user_spoke_last: bool`.
- Curator `_llm_rank`: attach `body` for telegram candidates under the
  same top-15 guard; add a conversation rule to the instructions: *"For
  chat threads, never echo raw message lines. Summarize what happened
  and what Tom must do, attributing statements to their senders. If Tom
  spoke last, phrase it as an open loop — 'You told Hunter you're free
  tomorrow night and offered to pick up dinner; waiting on his
  confirm.'"*
- Signal groups: `user_spoke_last` threads join a new
  `awaiting_replies_chat` group (person = chat name) so chips like
  "Nudge Hunter about tomorrow night" emerge.
The screenshot case becomes: headline *"Hunter Horsley — dinner
tomorrow night"*, summary *"You offered to host tomorrow and flagged a
Thursday conflict; waiting on Hunter to confirm."*
Files: gateway/scripts/town-telegram-scan.py, gateway plugin_api.py.

### C5 — NTK items: takeaway + action, and room to breathe

- Prompt (both instruction variants): summaries must be *takeaway →
  implication/next step*, never a restatement of the source; add
  optional per-item `action_hint` (≤48c verb phrase: "Reply about
  tomorrow night", "Confirm by Thu") to the ranking schema, persisted
  and served in `_public_item`.
- Render (town-home NTK card + town-feed pane): summary gets
  `line-clamp-2` instead of `truncate`; `action_hint`, when present,
  renders as the item's leading quick-action (prefills the composer
  with a grounded prompt via the existing suggestion machinery, or
  simply sits as an emphasized fragment in v1 — decide in
  implementation, smallest honest version wins).
- Deadline stakes rule in the prompt: when a source states a deadline
  and a consequence, the summary must carry both ("trial ends Jul 29 —
  routines lock").
Files: gateway plugin_api.py, src/town-home, src/town-feed.

### C6 — Suggestions that know about the day

Extend `_suggestion_signal_groups`: `events_next_48h` rows gain
`location/attendees/conference` (C1) and a new `schedule_pressure`
group carries C2 conflicts/tz-skew/travel candidates. Instruction
additions: conflict-resolution chips ("Pick tonight's priority:
Archetype, Circle × Pantera, or Haun"), travel-buffer chips when
consecutive events have different venues, prep chips that name
attendees. Keep the existing ≤2-calendar-chips cap. **Grounded-people
rule:** a "no prior contact" claim is allowed only when the attendee
email is absent from the gmail scanner's `known_correspondents` —
expose that set (emails only, hashed match, no bodies) to the curator
input; this is the deterministic seed of People-lite phase 2, not a
replacement for it.
Files: gateway/scripts/town-gmail-scan.py (export correspondent set in
scan output), gateway plugin_api.py.

### C7 — One voice, everywhere

A single `TOWN_VOICE` constant in plugin_api.py injected into ranking,
day-brief, and (later) any other lane: second person; name people, not
addresses; concrete numbers and times; verdicts allowed, hedging
banned; no system/scheduler jargon (keep the existing bans); warmth
without exclamation marks; British-butler-free. Greeting variants stay
**client-side and grounded**: `Happy Monday` only from the local date,
`Day {n} of {trip}` only from C3's `trip` object. Also sweep the
existing legacy/enriched instruction duplication into composed shared
blocks so C4/C5 edits land once, not twice.
Files: gateway plugin_api.py, src/town-home (greeting variants).

## Sequencing & cost

Order: **C1 → C2 → C4 → C3 → C5 → C6 → C7** (C1/C2 are risk-free
plumbing+math that everything else eats; C4 kills the visible raw-dump
embarrassment; C3 is the character payoff; C5/C6/C7 compound it).
C1+C2 ship together; C4 is independently shippable; C3 depends on
C1+C2; C6 depends on C1+C2 (and reads C4's chat-awaiting group if
present).

Token cost: ranking input grows by telegram bodies (≤15 × ≤1200c) and
event fields (≤20 events × ~300c) ≈ a few KB per cycle on the existing
aux-LLM lane; the day-brief call is small and gated by the C3
regeneration policy (hash change / date roll / >60min), so steady-state
adds ≈1 small call per hour. No new LLM lanes, no new write scopes —
everything stays read-only over already-scanned data.

## Non-goals

- No geocoding/Maps dependency — "factor in travel" comes from venue
  strings + the LLM, flagged as judgment, never as computed minutes.
- No fabricated CRM/person facts (C6's grounded-people rule is the
  whole mechanism); People-lite phase 2 remains its own track.
- No scanner-side LLM calls — sensing stays mechanical (G6R).
- Briefing card's morning-brief cron integration is untouched; C3's
  Today strip lives in the header, not that card.
