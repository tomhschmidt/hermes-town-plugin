# S1 — Where & when: operator profile, location, local time, Up next

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`. Builds on R3
(local-time calendar rows, `event_type`, `working_location`) and R4
(`operator_profile.py`). Runs in parallel with S2 — see "Seams".*

## Why (what Town does that we don't)

Town's briefing opens from the operator's actual situation — e.g. "Welcome
back to <home city> — <flight> touched down from <origin> just before the
sun came up, closing out the <conference> trip", or "<event> is in full
swing outside your window, and you're working from home today" — and shows
an event in two zones while away ("11 AM–1 PM ET, lands at 11 PM–1 AM
<away city> time").

Ours today (anonymized from production):
- The header context line is "Light Sunday — 3 events, 105 booked
  minutes…", counting a 15-min solo reminder as an event and missing that
  tomorrow has 8.
- `next_event` was an all-day "Home" working-location entry (fixed at the
  scanner by R3).
- The hero shows developer chrome: "Gateway online" and "Model <id>" pills.
- The schedule lives only in a dialog; there is no inline "what's next".
- The ranking LLM call gets UTC `created_at`/`start_at` only and no local
  "now". It doesn't know where the operator lives, so it produced "you
  landed in <home city> this morning — offer a new time while you're in
  town".
- A flight that landed this morning is invisible, because the calendar
  scan drops ended events and lodging inference only runs on all-day
  events.

## Seams (parallel with S2)

S1 owns: `operator_profile.py` extension, a new pure `where.py`, the
`operator`/`where` blocks and local-time strings in BOTH LLM calls, the day
brief `opener` + meaningful-event counts, `/feed` serving
`schedule_context`/`operator`/`where`, the desktop header, and a new Up
next strip (`src/town-home/upnext.tsx` + pure `lib/` helpers).

S2 owns people data and the attendee UI. In the Up next row for the next
meeting, render a slot `{renderPeople?.(event)}` (an optional prop/no-op
by default) where S2's component will be wired by the orchestrator. Add
fields to the day-brief schema only for S1's needs (`opener`); S2 adds
`prep`.

## Hard requirements

**Gateway**

1. `operator_profile.load_operator_profile()` adds `home_city` and
   `work_domains` (map domain → org name), and validates `home_timezone`
   with zoneinfo (falling back on invalid values).
   `operator_context(profile, now, where)` returns the block passed to the
   LLMs:
   - `name` and `preferred_name`;
   - `home_city` and `home_timezone`;
   - `local_now` (ISO);
   - `local_now_label` ("Sun Oct 11, 10:42 AM ET");
   - `where`.
2. The calendar scanner adds `recent_travel`: flight-like and lodging rows
   that ended within the last 36 h (same row shape as `events`). Flight
   detection is a pure function:
   - an airline code + number ("SQ 22", "UA123");
   - "Flight to/from <X>";
   - an IATA pair "XXX→YYY" / "XXX-YYY";
   - Google's auto-added flight events (`eventType`/source/description cues).

   Extract origin and destination city/IATA when present.
3. `where.infer_where(events, recent_travel, profile, now) -> dict` (pure)
   returns:
   - `status` ∈ {`home`, `away`, `traveling_today`, `just_returned`};
   - `city` and `timezone` (when known);
   - `label` (short human phrase, e.g. "Home in <city>", "In <city> ·
     +12h from home", "Back in <city> after <origin>");
   - `evidence` (≤ 3 short strings);
   - `since`/`until` (when known).

   Precedence:
   1. a flight today or one that landed in the last 36 h;
   2. an active lodging/multi-day stay;
   3. today's `workingLocation`;
   4. the timezone of today's timed events if it differs from home and at
      least 2 events agree.

   With no evidence the result is `home` / `home_city`. Never assert a
   city without evidence.
4. The ranking call (`_llm_rank`) input carries `operator` (per 1). Every
   time it shows is ALSO given as a local string (`created_local`,
   `start_local`). The rules text says:
   - the operator lives in `home_city`, so never describe home as a
     destination or a visit;
   - use local times;
   - when `where.status != home`, mention home-time only where it matters.
5. Day brief: input carries `operator` and `meaningful_today` /
   `meaningful_tomorrow` counts. Meaningful = timed, `default` type, not
   declined, and (another attendee, OR a location/conference, OR ≥ 30 min).

   Output adds `opener` (≤ 140 chars, grounded only in `where` plus the
   schedule).

   Rules:
   - `day_summary` counts only meaningful events;
   - when today has < 3 meaningful events and tomorrow ≥ 5, it says so.

   The regeneration hash includes `where.status`/`city` and `local_date`.
   `_normalize_day_brief` accepts old briefs without `opener`.
6. `/feed` additionally returns `schedule_context` (already computed and
   stored, or recomputed cheaply), `operator` (name, preferred_name,
   home_city, home_timezone), `where`, and `day_brief.opener`. All are
   additive, so old desktops ignore them.

**Desktop**

7. Hero: remove the "Gateway online" and "Model <id>" pills. Show a subtle
   "Offline" indicator only when the gateway is not open; model details can
   move into Customize. The greeting uses `operator.preferred_name` when the
   user has not set a name in Customize.
8. Context line priority: `day_brief.opener` → existing `day_summary`
   chain. Add a location chip from `where.label` (theme tokens only, no
   emoji), shown when `where.status != home`, or always if the existing
   trip badge is replaced by it — pick one and keep the header to one chip
   row.
9. Up next strip (new section directly under the header/composer, above
   the main cards) shows the next 2–3 meaningful events (from `schedule`
   plus `schedule_context`). After 6 PM local it may include tomorrow
   before noon.

   Each row shows:
   - the local start time;
   - relative time ("in 40m", "now");
   - the title;
   - a location/conference hint;
   - a conflict marker when the event is in a `schedule_context` conflict
     or cluster;
   - a dual time ("11:00 AM ET · 11:00 PM SGT") when the event's timezone
     or `where.timezone` differs from `home_timezone`.

   Clicking a row opens the existing Schedule dialog. When empty: "Nothing
   else on the calendar today", plus tomorrow's first event if known.

   Respects Customize visibility like the other cards (default on).
10. All desktop time rendering for these surfaces uses
    `operator.home_timezone`, or `where.timezone` when away, via
    `Intl.DateTimeFormat` with an explicit `timeZone`, never the renderer's
    implicit zone.

## Required tests

Gateway (`gateway/tests/test_s1_where.py`):
- Flight detection: positive and negative titles (e.g. "SQ 22 SIN→EWR",
  "Flight to Lisbon", "UA123" positive; "Sync 22" and "Q3 planning"
  negative).
- `infer_where` cases:
  - no evidence → home;
  - landed 6 h ago from abroad → `just_returned` with origin evidence;
  - active 3-night hotel in another city → `away`;
  - `workingLocation` office label;
  - tz-of-events rule needs 2 events;
  - never claims a city without evidence.
- Meaningful counts: a 15-min solo reminder is excluded; a 30-min solo
  block is included; an attended 15-min call is included.
- Captured ranking-call input includes `operator.local_now_label`,
  `home_city`, and `start_local`/`created_local` for an event row. The rules
  text contains the home-city rule.
- Day brief: `opener` normalized and persisted; an old brief without
  `opener` loads; the hash changes when `where.status` changes.
- `/feed` includes `schedule_context`, `operator`, and `where`, and is
  backward-compatible.

Desktop (`tests/desktop/s1-*.test.ts`):
- `formatEventTime(startIso, {homeTz, displayTz, eventTz})`: single zone,
  and dual zone across ET/SGT including the day change.
- `pickUpNext(events, context, now, tz)`:
  - ordering;
  - meaningful filter;
  - after-6 PM tomorrow inclusion;
  - conflict flagging;
  - the empty state.
- `contextLineSource` priority: opener > day_summary > fallbacks.
