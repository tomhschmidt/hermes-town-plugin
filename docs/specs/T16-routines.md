# T16 — Routines humanization (desktop-only)

## Goal

Routines surfaces currently show raw cron ("0 8 * * *"). Make them read like
Town: human schedule text, a description, Run Now, and a last-run link.

## Current state

- `src/town-routines/plugin.tsx`: routines pane (pause/resume via
  `host.request('cron.manage', {action:'pause'|'resume', name})`), statusbar
  countdown chip, New-routine dialog (submits prose to the ACTIVE session via
  `prompt.submit` — leave that dialog's behavior alone).
- `src/town-home/plugin.tsx`: `RoutinesCard` + `normalizeCronRows` +
  `routineOutcome` render the same cron rows on /town.
- Cron data comes from `cron.manage {action:'list'}` — rows include at least
  name/schedule/next-run/last-status; inspect the live shape via the
  town-debug dump or the normalizers before assuming fields.

## Behavior changes

1. **Human schedule text.** Add a shared `describeCronSchedule(expr: string):
   string` formatter (pure function, duplicated or placed in whichever plugin
   each bundle builds from — bundles can't import from each other, so if both
   need it, duplicate the ~40-line helper in both files with a comment noting
   the twin). Cover the 5-field forms our real jobs use and the common cases:
   `0 8 * * *` → "Daily at 8:00 AM", `0 */6 * * *` → "Every 6 hours",
   `0 15 * * *` → "Daily at 3:00 PM", `*/5 * * * *` → "Every 5 minutes",
   day-of-week lists (`0 9 * * 1-5` → "Weekdays at 9:00 AM",
   `0 9 * * 1` → "Mondays at 9:00 AM"). Anything unrecognized falls back to
   the raw expression. 12-hour clock, no seconds. Show the human text where
   the raw expression is shown today (both surfaces), with the raw expression
   demoted to a `title` tooltip attribute.
2. **Description line.** One muted line per routine derived from the cron
   job's prompt if the list payload carries it: first sentence, truncated
   ~90 chars. If the payload has no prompt field, omit the line gracefully
   (and say so in your report).
3. **Run Now.** Per-routine action (icon `Button` in the pane; also on the
   /town RoutinesCard rows) that: `session.create {title: "Run now: <name>"}`
   → `prompt.submit {session_id, text: <the job's prompt>}` → success toast
   "Started <name> — see session …". Requires the prompt field; if absent,
   hide Run Now entirely. Disable while in flight; handlers read state
   imperatively. KNOWN LIMITATION (accepted): this does not update the cron
   job's own last-run bookkeeping — do not try to fake it.
4. **Last-run link.** If the cron list payload exposes anything resolvable to
   the last run's session/log (inspect the real payload; also check the REST
   the desktop Cron pane uses, `GET /api/cron/jobs`, documented in
   `docs/rpc-surface.md` — but remember the renderer can only use JSON-RPC
   `cron.manage` and its own namespace REST, so only use fields that
   actually arrive in the data the plugins can fetch). If resolvable: make
   the "Completed Xh ago" outcome text clickable → `host.navigate` to that
   session. If not resolvable, leave the text as-is and report the finding —
   do NOT invent a mapping.

## In scope

`src/town-routines/plugin.tsx`, `src/town-home/plugin.tsx`.

## Out of scope

Gateway changes, cron add/remove flows, the New-routine dialog, statusbar
chip behavior (it may reuse the formatter if trivial).

## Acceptance checks

- The four real jobs (`0 8 * * *`, `0 15 * * *`, `0 9 * * *`, `0 */6 * * *`)
  render as "Daily at 8:00 AM", "Daily at 3:00 PM", "Daily at 9:00 AM",
  "Every 6 hours" on both the pane and /town; tooltips show the raw expr.
- Run Now on a real job starts a session that begins executing the routine
  prompt; the button is hidden if prompts aren't in the payload.
- `npm run check` + `npm run build` pass.
