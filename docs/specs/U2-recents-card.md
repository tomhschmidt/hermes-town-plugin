# U2 — Recents activity stream on /town

*2026-07-18, polish-loop iteration 2. Town's home shows a "Recents"
section under Tasks: an interleaved activity stream of session rows
(chat icon, title) and routine-outcome one-liners (doc icon), each with
a right-aligned relative time, quiet until hover. Conventions:
docs/specs/README.md binds. Single lane: src/town-home/plugin.tsx ONLY —
no gateway changes (all data already reachable).*

## Data

- Sessions: `host.request('session.list', {limit: 12})` →
  `{sessions: [{id, title, preview, started_at, message_count, source}]}`
  (verified: docs/rpc-surface.md line 59). Poll on the card's existing
  refresh cadence (piggyback the page's polling patterns; never faster
  than a few seconds; a one-shot fetch on mount + the standard interval
  used by other cards is fine).
- Routine outcomes: reuse the ALREADY-FETCHED cron list rows
  (`normalizeCronRows(cron.data)` — same source the Routines card uses).
  Each enabled job with last-run info contributes ONE row (its most
  recent run). No per-job `/cron-runs` fan-out — that endpoint is for
  the on-click dialog only.

## Card

New "Recents" card on /town, default position directly AFTER the Tasks
card (Town's placement). Register it in `CARD_IDS` and the Customize
dialog metadata (label "Recents").

REQUIRED loader fix (verified 2026-07-18): `normalizeTownHomePreferences`
(line ~116) currently accepts a stored `cardOrder` only when
`storedOrder.length === CARD_IDS.length` — adding a new card id makes
every existing stored order fail validation and silently RESET to
defaults, destroying the user's customization. Replace that validation
with a merge: (1) filter the stored order to known ids via `isCardId`,
dedupe preserving first occurrence; (2) for each id in `CARD_IDS` (in
default order) missing from the result, insert it after the
nearest PRECEDING default-order id that is present (i.e. find its index
in `CARD_IDS`, walk earlier default ids, insert after the last one
present in the sanitized order; insert at position 0 if none). This
places 'recents' right after wherever the user put Tasks. Visibility
needs no change — it already defaults per-id.

Rows (cap 8 after merge, sorted by timestamp desc):
- Session row: comment-discussion Codicon in a small muted leading
  glyph, single-line title (fallback: preview first ~60 chars, else
  "Untitled session"), right-aligned relative time from `started_at`
  (reuse the existing `relativeTime` helper). Click → open the session:
  the Tasks card's open-session logic lives INLINE inside
  `KanbanTaskRow` (verified) — extract it unchanged into a module-scope
  helper as part of this task and call it from both places (pure
  refactor, identical behavior). Skip sessions with no title AND no
  preview.
- Routine row: a distinct muted leading glyph (reuse a Codicon already
  imported/rendered in this file or town-routines — do NOT invent
  unverified icon names), text "<Humanized name> ran — <outcome>".
  There is NO existing name-humanization helper (verified) — add a tiny
  local `humanizeJobName(name)`: replace runs of `-`/`_` with single
  spaces, collapse/trim whitespace, uppercase the first character only
  ("town-gmail-scan" → "Town gmail scan"). Outcome from the CronRow
  fields `normalizeCronRows` already provides: `lastStatus` equal to
  'completed' (case-insensitive) → "completed"; `lastStatus` containing
  'error'/'fail' OR a non-empty `lastError` → "failed"; otherwise omit
  the "— <outcome>" suffix entirely. Timestamp = `lastRunAt`; only jobs
  with a `lastRunAt` contribute a row. Right-aligned relative last-run
  time. Click → open the existing run-history dialog for that job (the
  existing `openRunHistory` handler).
- P2 interaction model applies: rows quiet, `hover` background via the
  established row-hover var, 150ms transitions, generic `group` /
  `group-hover:` ONLY (named group variants are broken in the host CSS).
- Header: "Recents", sentence case, same SectionCard treatment as
  siblings. Empty state per the T23 pattern: one line ("No recent
  activity yet") — no action button needed.
- Loading: keep it quiet — if sessions haven't loaded yet but cron rows
  exist, render what's available; no skeletons this round.

## Hard constraints

- Handlers read state imperatively (refs/store.get()), never render
  closures. Theme vars only. Verify every rendered identifier is
  imported. No polling faster than a few seconds. No new dependencies.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files changed
outside src/town-home/plugin.tsx; report any spec assumption that turned
out wrong (especially the T21 loader behavior and which session-open
navigation helper you reused) rather than silently working around it.
Do NOT run npm run deploy, do not touch dist/, do not restart anything.
