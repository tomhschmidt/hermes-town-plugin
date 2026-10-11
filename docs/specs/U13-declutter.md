# U13 — Declutter the /town dashboard

*Follow-up to the GROUNDING pack (2026-10-11), from a review of the live
dashboard on the operator's MacBook. Conventions: `GROUNDING.md` +
`README.md` (desktop hard rules, Tailwind subset, synthetic data only).
Bundles must keep all imports at the top (`scripts/build.mjs` enforces it —
older desktop clients cannot load mid-file imports).*

## Problem (observed live, anonymized)

The data is right; the screen says things twice and in too much small text:

- Up next lists every attendee of an internal meeting on its own line
  ("Di — Example Co · colleague", ×5, "+3 more").
- Each Need-to-Know card runs ~7 lines: 2-line headline, 2-line truncated
  summary, action link, a chip with the internal mailbox name ("tom",
  "personal.inbox"), an age, and an "Open in Gmail" button on its own row.
- The header's location chip repeats the opener sentence; a time-zone
  callout ("9:00 PM ET, which is 6:00 PM Pacific") appears while the
  operator is home; a schedule conflict appears as a callout AND a
  suggestion; "Prepare for tonight's <event>" duplicates Up next.
- "22 min. ago" freshness stamps on both rail cards; a ~4-line empty
  composer with a permanent "Enter to start · Shift+Enter…" hint.
- Tasks shows "Showing recent sessions (kanban bridge offline)": the kanban
  route crashes (`AttributeError: module 'hermes_cli.kanban_db' has no
  attribute 'connect_closing'` — Hermes moved it to
  `hermes_cli.kanban_db_connect` and removed the old path in v0.21.x), so the
  card falls back to sessions — including cron runs ("Morning briefing ·
  Oct 11 08:20", "telegram-action-items") with a grey prompt-preview line and
  "telegram"/"cron" text tags.

## Scope

`src/town-home/plugin.tsx`, `src/town-home/upnext.tsx`,
`src/town-home/people.tsx`, `src/town-home/lib/*.ts`, `src/shared/*.ts`,
`src/town-feed/plugin.tsx` (only where the NTK pane shares the card
footer), `gateway/town-feed/dashboard/plugin_api.py` (kanban connect helper,
day-brief callout filter, suggestion dedupe), tests.

## Hard requirements

**K. Kanban (bug)**
1. One helper `_kanban_connect()` in `plugin_api.py` returns
   `hermes_cli.kanban_db_connect.connect_closing()` when that module exists,
   else `hermes_cli.kanban_db.connect_closing()`. All kanban routes
   (`/kanban`, `/task`, `/promote-to-task`, `/kanban/complete`, and any other
   call site) use it; other `kanban_db` functions are unchanged.

**A. Up next attendees**
2. A meeting's people line groups colleagues (`relationship == colleague`)
   into one muted phrase per org: "8 Example Co colleagues" (singular for
   1; "colleagues" without org when unknown). Non-colleague, non-self people
   (external / first meeting / contact / correspondent) get individual
   entries — at most 3, "Name (first meeting)" / "Name — Org" — then
   "+N more". When a meeting has only colleagues, the people area is that
   single phrase. Declined attendees are excluded from counts. Prep lines
   (day brief `prep`) render only for the individually listed people.
3. Up next rows: the time column shows only the clock time; relative time
   and the venue/conference hint move inline after the title as muted text
   ("Weekly Sync · in 8h · Zoom"); dual time (when away) stays on the
   detail line. Row height drops accordingly.

**B. Need-to-Know card (home rail; same footer in the town-feed pane)**
4. Summary clamps to ONE line (headline keeps its 2-line clamp).
5. One footer row: action hint link (left, truncating), compact age
   ("27m", "5h", "3d") right-aligned, then the source action as an icon-only
   button with an accessible label/tooltip ("Open in Gmail", "Open session",
   "View run"). The separate full-width "Open in Gmail" row is removed.
6. The account / source chip ("alice", "personal.inbox", "Session")
   is removed from the card. The Gmail deep link still targets the right
   account.

**C. Header**
7. The location chip shows only while `where.status` is `away` or
   `traveling_today` (not `home`, not `just_returned` — the opener covers
   those). The existing trip-badge fallback for old gateways is unchanged.
8. Callouts: at most 2 rendered; a `timezone` callout is not rendered when
   `where.status` is `home` or `just_returned`. Gateway side: the day brief
   drops `timezone` callouts in those states before persisting, and its
   prompt says not to produce them when the operator is home.

**D. Suggestions**
9. Curator post-filter (deterministic, after the LLM): drop a suggestion
   that is prep for an Up-next event — its label starts with
   prep/prepare/get ready/join (case-insensitive) AND names (substring
   match on the event summary, ≥ 2 words or ≥ 6 chars) a meaningful event
   starting within the next 18 hours — and drop a suggestion that names both
   events of a `schedule_context` conflict (the conflict callout covers it).
   If filtering leaves fewer than 3, keep the remainder (no backfill).
10. Desktop: suggestion rows are plain rows (no glyph bubble); the hover
    affordance (arrow/insert) stays.

**E. Tasks card**
11. With the kanban route fixed the board renders as before. In the
    sessions fallback: rows whose source is cron (or whose title matches a
    cron job) are hidden; the grey preview line is removed; the text tags
    ("telegram", "cron") become a small source icon with a tooltip.

**F. Smaller trims**
12. NTK and Suggestions headers show the freshness stamp only when the data
    is older than 60 minutes (then in the warning tone); the refresh button
    always shows.
13. The task composer is 2 rows tall when empty and unfocused, grows with
    content (to the current max), and shows the "Enter to start ·
    Shift+Enter…" hint only while focused or non-empty.

## Required tests

Gateway (`gateway/tests/test_u13_declutter.py`):
- `_kanban_connect` prefers `kanban_db_connect.connect_closing`, falls back
  to `kanban_db.connect_closing` (stub both modules via `sys.modules`); the
  `/kanban` handler uses it (stubbed list_tasks).
- Day brief: a `timezone` callout is dropped when where.status is home /
  just_returned and kept when away; conflict callouts kept.
- Suggestion filter: prep suggestion naming an event in 3h → dropped; same
  label for an event in 30h → kept; suggestion naming both events of a
  conflict → dropped; unrelated suggestions untouched; no backfill.

Desktop (`tests/desktop/u13-*.test.ts`, pure helpers):
- `meetingPeopleSummary`: colleagues-only (one org, mixed orgs, no org),
  mixed colleagues + externals (externals listed, max 3, "+N more"),
  declined excluded, singular/plural.
- `compactAge`: minutes/hours/days boundaries.
- `shouldShowLocationChip` per where.status; `visibleCallouts` (timezone
  dropped at home, cap 2).
- `isStale(curatedAt, now)` 60-minute threshold.
- Tasks fallback filter drops cron rows.
