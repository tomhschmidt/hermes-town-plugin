# U8 — Live relative timestamps + Recents depth

*2026-07-18, polish-loop iteration 8. Town's relative times tick while
you look at the page; ours freeze until the next poll (a "1 min. ago"
Recents row can read stale for minutes). Town's Recents also has a
"Show more" affordance; ours hard-caps at 8. Conventions:
docs/specs/README.md binds (Tailwind subset rule especially). Desktop
files ONLY — gateway deploys are on hold (UX-LOOP.md), and none are
needed.*

## 1. Shared minute ticker (both bundles)

In src/town-home/plugin.tsx and src/town-feed/plugin.tsx (each bundle
is isolated — twin implementation, same pattern): a module-level
`$now`-style atom holding `Date.now()`, updated by ONE `setInterval`
every 30 seconds that starts when the first subscriber mounts and stops
when the last unmounts (reuse each bundle's existing acquire/release or
subscriber-count pattern if present; otherwise a small refcount in the
hook). A `useNow()` hook returns its value.

Every component that renders `relativeTime(...)` for an on-screen age
(Recents rows, NTK card + feed page items, routines "Completed Xh ago"
line in town-home, kanban subtitles, greeting context line if it shows
ages) reads `useNow()` so rows re-render each tick. `relativeTime`
itself keeps its signature (it already takes the timestamp; if it
internally calls Date.now(), add an optional `now` parameter rather
than changing call sites that don't need ticking). Do NOT add
per-row timers, and do NOT poll any data source faster — this is
purely presentational re-rendering.

town-routines/plugin.tsx: SKIP this round (it re-renders on its own
poll; leave it).

## 2. Recents "Show more" (src/town-home/plugin.tsx)

The Recents card caps merged rows at 8. Add Town's affordance: when
more rows exist, a slim muted "Show more" text Button centered below
the list expands the cap to 20 in place ("Show fewer" collapses back to
8). Ephemeral component state (useState is fine — it is render state,
not handler state), not persisted. Row rendering unchanged.

## 3. NTK "+N more" check (src/town-home/plugin.tsx)

NeedToKnowCard computes `moreCount`. VERIFY it renders an affordance
when moreCount > 0 (a muted "+N more in the feed" line that navigates
to the town-feed pane/route the same way existing links do). If it
already renders one, leave it and say so in your report; if it computes
the number but renders nothing, add the line.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; files changed
ONLY src/town-home/plugin.tsx and src/town-feed/plugin.tsx; NO git
commit or push; no deploy/restart. Report which components got the
ticker and what you found for item 3.
