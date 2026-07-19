# T14 — Task composer on town-home

## Goal

Town.com's centerpiece is a composer ("Give Scribedash a task"). Add the
equivalent to the `/town` page: a prompt input that starts real Hermes work
without leaving the dashboard.

## UX

- New composer block on the `/town` page (`src/town-home/plugin.tsx`),
  rendered between the greeting header and the Tasks card, full content width.
- Multiline text input (Enter submits, Shift+Enter newline), placeholder
  "Give Hermes a task…", plus a mode control with two options:
  **Task** (default) and **Chat**. Use SDK components; a segmented control can
  be two toggle `Button`s if the SDK has nothing better.
- Submit affordance: primary `Button` ("Start task" / "Start chat" per mode),
  disabled while empty or while a submit is in flight, and while
  `host.state.gateway.get() !== 'open'`.
- After success: clear the input, `host.notify({ kind: 'success', … })` with
  the created session title, STAY on the Town page (no navigation). The new
  work should surface in the Tasks card (Task mode) / sessions list on next
  refresh — trigger the existing refresh paths (bridge refresh + the
  event-driven `useRpc` refresh already reacts to gateway events).
- Errors: `host.notifyError(error, 'Could not start task')`; keep the draft.

## Behavior

Both modes:
1. Derive a title: first line of the prompt, trimmed, truncated to ~60 chars.
2. `host.request('session.create', { title })` → response includes
   `session_id` (see `docs/rpc-surface.md`: params
   `{cols?,messages?,title?,parent_session_id?,cwd?,source?,profile?,model?,…}`,
   result `{session_id, stored_session_id, …}`). Do not pass model/profile —
   gateway defaults apply.
3. `host.request('prompt.submit', { session_id, text: <full prompt> })` — the
   proven pattern from `src/town-routines/plugin.tsx` (`sendToAgent`).

Task mode additionally files a kanban card, linked to the session:
4. New gateway endpoint `POST /task` in
   `gateway/town-feed/dashboard/plugin_api.py`, request
   `{title, prompt, session_id?, client_key}`:
   - Reuses the `_create_kanban_task` machinery style: `kanban_db.create_task`
     with `title`, `body` = prompt + `\n\nSource: town-composer\nSession:
     session:<session_id>`, `created_by="town-feed"`, `triage=True`,
     `idempotency_key=f"town-composer:{client_key}"` (client generates a
     random key per submit so retries are idempotent but distinct submits are
     not deduped).
   - Response `{ok, task: {id, title, status}}`.
5. Desktop reaches that endpoint via the town-feed window bridge (`ctx.rest`
   is namespace-locked): add a `createTask(input)` method to the bridge object
   `src/town-feed/plugin.tsx` publishes, following how the existing kanban
   summary fetch works, and consume it from town-home via the existing
   `windowTownFeedBridge()` / `windowTownKanbanBridge()` pattern. If the
   bridge is unavailable (town-feed plugin disabled), Task mode should still
   create the session and prompt, then notify
   `{kind:'info', message:'Session started; kanban card skipped (town-feed bridge unavailable)'}`.
6. On card success, refresh the kanban bridge state so the Tasks card shows
   the new card without a manual reload.

## In scope

`src/town-home/plugin.tsx`, `src/town-feed/plugin.tsx`,
`gateway/town-feed/dashboard/plugin_api.py`.

## Out of scope

Voice input, attachments, permission-mode shield, people mentions, composer
on other pages, any change to the NTK carousel or Routines card.

## Acceptance checks (orchestrator runs these after deploy)

- Chat mode: submit "Reply with exactly: pong" → a new session appears with
  the reply; Town page never navigates away; success toast shows.
- Task mode: same, plus a kanban card in triage titled from the first line,
  body containing `Session: session:<id>`, visible in the Tasks card after
  refresh; double-submit of the same text creates two cards (distinct
  client_key), a retried failed submit does not duplicate.
- With the town-feed desktop plugin disabled, Task mode degrades to
  session-only with the info toast.
- `npm run check` and `npm run build` pass; gateway py_compile passes.
