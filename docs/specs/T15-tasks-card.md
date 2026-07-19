# T15 — Tasks card overhaul

## Goal

The Tasks card on `/town` currently shows kanban items like "Done 60 days
ago" — stale noise. Make it Town-like: active work first, completed work
auto-hidden after 48h, checkbox completion, status subtitles, click-through.

## Current state

`src/town-home/plugin.tsx`: `TasksCard()` renders (a) recent sessions with
attention markers and (b) kanban tasks from `windowTownKanbanBridge()`;
helpers `activeKanbanTasks`, `completedKanbanTasks`, `kanbanBadgeVariant`.
Gateway `GET /kanban` (`get_kanban_summary` in
`gateway/town-feed/dashboard/plugin_api.py`) returns tasks grouped from
`kanban_db.list_tasks(conn, include_archived=False)`.

## Behavior changes

1. **Auto-hide stale Done:** kanban items with a done/completed status older
   than 48h (by their most recent status-change/updated timestamp) are
   excluded from the card. If the gateway summary doesn't currently expose an
   updated-at timestamp per task, add it to the `/kanban` response (read it
   from the task row / events in `kanban_db` — inspect
   `~/.hermes/hermes-agent/hermes_cli/kanban_db.py` for the available
   fields). Sort: active first (triage/ready/running/blocked), then recent
   Done (<48h), newest activity first.
2. **Checkbox complete:** each active kanban row gets a leading checkbox
   (SDK `Checkbox` if exported — verify in `docs/sdk-surface.md` /
   `types/hermes-plugin-sdk.d.ts` — else an accessible button styled with
   theme vars). Checking it calls a new bridge method `completeTask(taskId)` →
   new gateway endpoint `POST /kanban/complete` `{id}`:
   - Use `kanban_db.complete_task(conn, task_id, result="Completed from Town dashboard")`
     — it handles `running|ready → done`. It returns False/raises for other
     states (e.g. `triage`); inspect `kanban_db.py` and use the correct
     supported transition for triage cards (e.g. move/update to a completable
     state first, or the direct status-update API if one exists) — the
     acceptance check below must pass for BOTH a triage card and a ready
     card. Respond `{ok, id, status}`; 404 unknown id; 409 if the task is
     already done/archived (idempotent success is also acceptable — pick one
     and say which).
   - Optimistic UI: strike-through/fade the row immediately, reconcile on the
     next bridge refresh (refresh after the POST resolves).
3. **Status subtitles:** under each kanban title, one muted line: column/
   status name + relative age ("In progress · 2h ago", "Done · yesterday").
   For session rows keep the existing subtitle behavior.
4. **Click-through:** clicking a kanban row's text opens its linked session
   when the card body contains a `session:<id>` reference (T14 cards will;
   promoted NTK cards contain "Town Feed item" refs — for those, and cards
   with no session ref, clicking is a no-op title row, not a broken link).
   Parse the ref out of the body server-side and expose it as
   `session_id` in the `/kanban` summary payload rather than shipping the
   whole body to the renderer.
5. **Empty state:** if nothing survives filtering, keep the existing
   "Board clear" empty state.

## In scope

`src/town-home/plugin.tsx`, `src/town-feed/plugin.tsx` (bridge method),
`gateway/town-feed/dashboard/plugin_api.py`.

## Out of scope

The sessions half of the card beyond what's stated, kanban board
reordering/drag, the composer (T14), archived tasks.

## Acceptance checks

- The three "phone latency … Done 60 days ago" cards no longer render;
  "Board clear" shows when only stale-done items exist.
- Completing a triage card AND a ready card both flip to done and stay done
  after a bridge refresh; the row fades/strikes immediately on click.
- A T14-created card click navigates to its session.
- `npm run check` + `npm run build` + gateway py_compile pass; `/kanban`
  response remains backward-compatible for the existing consumers
  (`normalizeKanbanState` keeps working — extend, don't break).
