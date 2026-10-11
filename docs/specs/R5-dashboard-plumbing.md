# R5 — The dashboard shows raw plumbing

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md` (all desktop
hard rules apply: SDK-only imports, theme vars, Tailwind subset,
imperative handlers, ≥ few-second polling, bridge-only cross-plugin data).*

## Problem (observed in production)

1. **Briefing card shows the cron prompt.** `/cron-runs/{job_id}` returns
   the first 2000 chars of the run doc, which begin with the wrapper
   header and the 38k-char prompt (`# Cron Job: Morning briefing **Job ID:**
   … ## Prompt [IMPORTANT: The user has invoked …`); the response only
   starts at the `## Response` heading hundreds of lines later. The card
   (and the run-history dialog) render that header; status shows raw `ok`.
2. **Telegram source chips show raw chat ids** (`-5508869635`) on 4 of the
   top 10 items: `_public_item` drops `chat_name`, and the desktop label
   regexes expect a `Name:` headline prefix the LLM no longer writes.
3. **Every Refresh button degrades the feed.** `POST /curate` runs
   `run_curator(llm=None)` (heuristic): it overwrites LLM headlines,
   summaries, action hints, and scores until the next scheduled run; the
   toast literally says "Refreshed (heuristic pass)"; Suggestions refresh
   is a no-op (suggestions only regenerate in LLM mode). A synchronous LLM
   curation would also exceed the renderer's ~15 s IPC timeout.
4. **Routines card lists the 4 internal scanner jobs** ("Town gmail scan ·
   every 10m", …) and the header's next-routine line can name one;
   `TOWN_SCANNER_JOB_NAMES` is applied only in Recents. Routine failures
   are `opacity-0` until hover.
5. **Open in Gmail** ignores the account (`/mail/#inbox/<id>`), opening
   the wrong mailbox for the second account.
6. **Fake initials** on suggestion chips: `suggestionInitials` turns
   labels into "NP" (Nudge Patrick-style), "FM", "PT".
7. **Briefing card goes stale**: its effect depends on
   `[briefing?.id, cronRuns]`, so a new day's run is not fetched until
   remount.
8. **Refetch storm**: `host.onEvent('*', …)` (home + feed) refetches on
   every gateway event, including streaming deltas — several RPCs plus
   `/feed` and `/kanban` every ~2 s while a session streams.
9. **Wrong empty states flash on mount** ("Feed not connected",
   "Suggestions not connected", "kanban bridge offline") because bridge
   hooks start at `null` instead of reading the already-published
   `window` bridge.

## Scope

`src/town-home/plugin.tsx`, `src/town-feed/plugin.tsx`, new pure helper
modules under `src/town-home/lib/` / `src/town-feed/lib/` (or `src/shared/`),
routes `/cron-runs`, `/curate`, and `_public_item` in
`gateway/town-feed/dashboard/plugin_api.py`, tests. Not in scope: curator
internals (R4), header redesign / Up next (S1).

## Hard requirements

1. `/cron-runs/{job_id}` returns, per run, `output` = the text after the
   LAST `## Response` heading (trimmed, ≤ 2000 chars, reading the file
   tail efficiently — docs can be large), and `status` unchanged plus
   `delivered` if derivable. Docs without a Response section yield `""`.
   The desktop Briefing card and run-history dialog render that text with
   markdown syntax stripped to readable plain text (headings, bold/italic
   markers, bullet glyphs normalized; links shown as their text) and a
   humanized status ("Delivered", "Failed", "Ran"), never raw `ok`.
2. `_public_item` passes through `chat_name`, `last_sender`,
   `user_spoke_last`, and (if present) `chat_type`, `account`,
   `thread_id`. Telegram chip label = `chat_name` → existing regex
   fallbacks → `"Telegram"`; never a numeric id.
3. `POST /curate` never runs the heuristic curator. It finds the curate
   cron job (name `town-feed-curate` or script `town-curate.py`), starts
   it in a background thread via the same `_execute_job_now` path as
   `/cron-trigger`, and returns immediately
   `{"ok": true, "triggered": true, "since": <curated_at>}` (or
   `{"ok": false, "error": …}` when no job exists; `triggered: false` if a
   run is already in flight — guard with a module-level lock/flag).
   (`/feed` already returns `curated_at`; do not modify `get_feed` — R4 owns it.)
4. Desktop Refresh (NTK + Suggestions + feed pane): shows a "Refreshing…"
   state, polls `/feed` no faster than every 5 s until `curated_at`
   advances past `since` or 120 s elapse, then updates; toast copy is
   human ("Updated your feed" / "Couldn't refresh — showing the last
   update"). No "heuristic" wording anywhere in UI copy.
5. One predicate `isInternalTownJob(job)` (scanner names + curate job)
   hides internal jobs from the Routines card, the header next-routine
   line, and Recents (replacing ad-hoc checks). Run history for a job opened
   directly still works.
6. Routine last-outcome badge is always visible when its tone is bad or
   warn; neutral/good tones may stay hover-revealed.
7. Gmail deep link: `https://mail.google.com/mail/u/<account email>/#all/<id>`
   (URL-encode both); falls back to the current form when the account is
   unknown. Applies in town-home and town-feed.
8. Remove fake initials: suggestion chips show a kind glyph/icon (or
   nothing) unless a real person field exists in the data.
9. Briefing card refetches when the briefing job's `lastRunAt` changes.
10. Replace `onEvent('*')` with a whitelist of event types that can change
    the data shown (session created/ended/renamed, cron job
    run/updated, approvals, kanban/task events — derive the real names from
    `~/.hermes/hermes-agent` gateway source and `docs/rpc-surface.md`);
    keep the existing debounce. Streaming/delta events must not trigger
    refetches.
11. Bridge hooks initialize lazily from the published `window` bridge
    (`useState(() => window.__…)`) so connected states render on first
    paint; the "not connected" copy appears only if still missing after
    the ready event/timeout.

## Required tests

Gateway (`gateway/tests/test_r5_routes.py`):
- Run-doc extraction: synthetic doc with a long prompt and a
  `## Response` section → only the response; doc with two Response
  headings → the last; no Response → `""`; > 2000 chars → capped.
- `_public_item` passthrough of the telegram/gmail fields.
- `/curate` handler with stubbed `list_jobs` / `_execute_job_now`:
  returns immediately with `triggered: true` (assert the executor runs on
  a thread, not inline); second call while in flight → `triggered:
  false`; never calls `run_curator(llm=None)`.

Desktop (`tests/desktop/r5-*.test.ts`, pure helpers):
- `stripMarkdownToText` on a representative briefing (headings, bold,
  bullets, links).
- `humanRunStatus` mapping.
- `telegramChipLabel` (chat_name wins; regex fallback; never digits-only).
- `gmailUrl(account, id)` with and without account.
- `isInternalTownJob` for scanner names, curate job, and a normal routine.
- `shouldRefetchForEvent` whitelist (delta/stream events → false).
