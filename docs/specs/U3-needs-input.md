# U3 — "Needs input" sub-state on task rows

*2026-07-18, polish-loop iteration 3. Town's task rows show an
accent-colored "Needs input: <what the agent is waiting on>" line when
the linked session is blocked, closing the loop between the task list
and pending approvals/questions. All ingredients exist in
src/town-home/plugin.tsx — single in-scope file, no gateway changes.
Conventions: docs/specs/README.md binds.*

## Data / correlation

`attentionItems(...)` (line ~1125) already computes waiting sessions as
`AttentionItem {id, title, detail}` with human-readable details
("Approval required before work can continue", "Waiting for your
response", "Finished while you were viewing another session"). Kanban
task rows carry `sessionId` (the STORED/persisted session id — T15),
while attention items key on active-row `id` and, for active-derived
rows, also `sessionKey` (`session_key` fallback id, line ~901).

- Extend `AttentionItem` with `sessionKey?: string`; in
  `attentionItems`, set it for items built from `activeRows`
  (`row.sessionKey`). Marker-derived items keep just `id`.
- Where TasksCard receives `waitingItems`, build an alias lookup
  `Map<string, AttentionItem>` inserting each item under `id` AND
  `sessionKey` (when present and different).
- EXCLUDE items whose detail is the 'finished' variant — "Needs input"
  is only for blocked sessions (approval / waiting-for-response), not
  finished notifications.

## Presentation

For each kanban task row whose `sessionId` hits the lookup:

- Replace the row's normal muted subtitle line with:
  `Needs input: <detail>` — the "Needs input:" prefix in
  `text-(--ui-accent)` (font-medium), the detail in the row's existing
  muted subtitle style, single line, truncate. 150ms color transition
  is NOT needed (static state), no new hover behavior.
- Sort: within the existing tasks-card ordering, tasks that need input
  float to the TOP of the list (stable — preserve the existing
  column-priority-then-recency order within both groups). The existing
  sort helper stays; wrap or pre-partition, do not rewrite it.
- Everything else about the row (checkbox gating, Open session chip,
  hover-reveal actions, click behavior) is unchanged. The Waiting on
  you card and its badge are unchanged.
- No new polling, events, or state atoms — this is pure derivation from
  data the page already has.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files
changed outside src/town-home/plugin.tsx; report any spec assumption
that turned out wrong rather than silently working around it. Do NOT
run npm run deploy, do not touch dist/, do not restart anything.
