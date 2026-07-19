# U4 — Quick-action chips under Suggestions

*2026-07-18, polish-loop iteration 4. Town's home shows a row of pill
buttons under the Suggestions box ("Audio briefings", "Start a
document", "Create a deck", …) — cheap entry points into common flows.
Ours map to capabilities that already exist on /town. Single in-scope
file: src/town-home/plugin.tsx. Conventions: docs/specs/README.md
binds.*

## Chips (exactly these four, in order)

Rendered as a wrap row at the BOTTOM of the SuggestionsCard content
(below the suggestion rows / empty state, separated by the same border
treatment sibling cards use), small outline pill buttons
(SDK `Button` variant that matches the existing chip look in this file
— the persistent context chips from P4 are the reference), each with a
leading Codicon ALREADY IMPORTED/RENDERED in this file (report which
you picked; do not invent names):

1. "Brief me on today" → prefill the composer in CHAT mode with
   "Brief me on the rest of today: calendar, waiting items, and
   anything new I should know." and focus it (the `$composer` atom's
   draft + focusRequest mechanism the Suggestions prefill already
   uses).
2. "Plan a task" → clear-prefill (empty draft) the composer in TASK
   mode and focus it.
3. "New routine" → call the existing `openNewRoutineDialog()`.
4. "Refresh feed" → call the bridge `curate()` the same way the
   existing NTK refresh affordance does (respect its in-flight/curating
   state — disable the chip while curating; graceful no-op when the
   bridge is absent).

## Rules

- Handlers read state imperatively (the existing helpers already do);
  chips must not capture render-time state.
- Theme vars only; 150ms transitions consistent with siblings; chips are
  always visible (not hover-gated).
- If the composer's mode control uses a specific setter/atom, reuse it —
  do not duplicate mode state.
- No new polling, no gateway changes, no new atoms beyond what
  composer/dialog/bridge already expose.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files changed
outside src/town-home/plugin.tsx; report any spec assumption that turned
out wrong. Do NOT run npm run deploy, do not touch dist/, do not restart
anything.
