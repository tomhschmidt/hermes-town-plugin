# G3 — Calendar scanner (REWRITTEN 2026-07-18: script shape)

*Original plugin-scanner spec replaced after the Hermes-native alignment
review. This spec is now a pointer: G3 is built as component **B** of
`docs/specs/G6R-native-restructure.md` (`town-calendar-scan.py`, a
`no_agent` cron script) during phase G6R-c.*

Retained decisions from the original spec that still bind:
- Scope-aware per token — accepts full `calendar` OR `calendar.readonly`;
  activates per account as re-auths land; ships as a silent no-op wherever
  scope is absent (verified as the shipping state for `secondary`).
- Candidate shape: headline "<summary> — <relative start>", summary =
  location + attendee count + description first line, source
  `calendar:<account>:<event_id>`, created_at = event start (imminent =
  hottest), hash on (account, event id, start) so reschedules resurface.
- All-day events included; ended events skipped; cap 20/account, 48h
  window; primary calendar only.
- Desktop: `next_event` drives one muted greeting line on /town
  ("Next: <summary> · <relative time>"), hidden when null. The `/feed`
  plumbing for `next_event` lands in phase G6R-b (curator ingestion).
- Non-goals unchanged: no calendar writes, no invite actions, no full
  schedule panel (Tier 2), no non-primary calendars.
