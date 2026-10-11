# R3 — Calendar: local time, cross-calendar dedupe, event types

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`.*

## Problem (observed in production, anonymized)

A work calendar whose timezone is still set to Asia/Singapore (left over
from a trip) renders every event 12 h off for an operator living in
America/New_York: "Weekly Call — Mon at 11:00 PM" (really 11 AM),
"Lunch — Tue at 12:00 AM", "Podcast taping — Tue at 8:00 AM" (really Mon
8 PM) — and a suggestion copied the wrong time. All-day events are anchored
at midnight in the calendar's zone (noon-to-noon locally), so `next_event`
became the all-day "Home" working-location entry. The same HVAC visit on two
calendars produced a "duplicate — delete one" conflict callout. Birthdays,
"Home" working-location, and Focus Time blocks crowd Need to Know.

Root causes in `gateway/scripts/town-calendar-scan.py`: `_relative_start`
formats the event's own offset (`strftime` on an unconverted datetime) and
computes day deltas in that zone; `_parse_event_time` anchors all-day dates
in the calendar zone; dedupe is by `event.id` only (ids differ across
calendars); `eventType` is ignored; self-declined events are kept.
`_schedule_context` in `plugin_api.py` then treats duplicates as conflicts.

## Scope

`gateway/scripts/town-calendar-scan.py`, `_schedule_context` and any
helper it uses in `gateway/town-feed/dashboard/plugin_api.py` (only for
duplicate/event-type robustness), tests. Not in scope: bootstrap header
(R2), NTK caps (R4), location inference (S1).

## Hard requirements

1. **User timezone.** One helper `_user_timezone()` resolves: env
   `TOWN_TIMEZONE` → `operator_profile.home_timezone` if the
   `operator_profile` module is importable (it may not exist yet — guard it)
   → Hermes `config.yaml` top-level `timezone` if non-empty → system local
   tz. Invalid names fall through.
2. All human-readable times (`headline` relative text, any label) are
   computed after `.astimezone(user_tz)`; day deltas ("today",
   "tomorrow", weekday) use the user's local date. `start_iso`/`end_iso`
   stay exact ISO instants (offset may be any zone; consumers convert).
3. All-day dates are anchored to local midnight in `user_tz` (start and
   end), flagged `all_day: true` as today.
4. **Dedupe across calendars** by `iCalUID` (fallback: casefolded summary
   + start instant + end instant). When duplicates collapse, keep one row,
   record `calendars: [<names>]` (deduped, ordered), and union attendee
   lists by email (respect `MAX_ATTENDEE_ENTRIES`, and report the TRUE
   total in `attendee_count`).
5. **Event types.** Emit `event_type` (Google `eventType`, default
   `default`). `workingLocation` and `birthday` events: never NTK
   candidates, never `next_event`, excluded from conflict/cluster
   computation; still listed in `events` (S1 reads working location).
   `focusTime` and `outOfOffice`: never NTK candidates, never `next_event`;
   kept in `events`. For `workingLocation`, emit `working_location`
   (`home` | `office` | `custom`, plus label when present).
6. **Self-declined** events (self attendee `responseStatus == declined`)
   are dropped entirely.
7. **NTK candidates** are emitted only for timed, non-declined,
   `default`-type events that start within the next 24 h AND have at least
   one other attendee, a conference link, or a location. Everything else
   is schedule-only (`events`), not Need to Know.
8. **next_event** = the earliest future timed, `default`-type,
   non-declined event.
9. `_schedule_context` never reports two rows with the same identity
   (iCalUID or summary+start+end) as a conflict, and ignores
   `workingLocation`/`birthday` rows — defensive even if the scanner already
   deduped.
10. Output JSON contract stays a superset of today's (README table);
    existing consumers keep working.

## Required tests (`gateway/tests/test_r3_calendar.py`)

Synthetic event dicts; inject `now`; patch `_user_timezone` to
America/New_York (and one case for Europe/London).
- Event `2026-10-12T23:00:00+08:00` on a Singapore-zoned calendar, now =
  Sun 2026-10-11 10:00 ET → headline "… — Mon at 11:00 AM" (or "tomorrow"
  form if that is the established wording for +1 day — match the code's
  existing vocabulary, but the hour must be 11 AM local).
- All-day `date: 2026-10-12` on the Singapore calendar → start = local
  midnight 2026-10-12 ET.
- Same iCalUID on two calendars → one row with both calendar names;
  attendee union; `attendee_count` = true total for a 41-attendee event
  capped at 8 entries.
- workingLocation, birthday, focusTime → no candidate, not next_event;
  workingLocation row carries `working_location`.
- Self-declined event → absent.
- NTK candidate policy: solo "Gym" → no candidate; meeting with 1 other
  attendee in 3 h → candidate; meeting in 30 h → no candidate.
- DST: event on 2026-11-01 (US fall-back day) labeled with the correct
  local hour.
- `_schedule_context` with two identical rows → no conflict.
