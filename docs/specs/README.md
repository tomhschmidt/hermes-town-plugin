# Tier 1 spec pack — shared conventions (read this first)

These specs implement Tier 1 of `docs/town-gap-analysis.md`. Each is executed
by a separate coding agent; the orchestrator reviews, deploys, and verifies.

## Repo shape

- Desktop plugins: authored TSX in `src/<id>/plugin.tsx` → `npm run build` →
  `dist/<id>/plugin.js`. Do NOT edit `dist/` by hand. Do NOT run
  `npm run deploy` (the orchestrator deploys).
- Gateway plugin (Python/FastAPI): `gateway/town-feed/` — `__init__.py`
  (register + scheduler) and `dashboard/plugin_api.py` (routes + curator +
  kanban bridge). Mounted at `/api/plugins/town-feed`. The orchestrator
  installs + restarts the gateway; you only edit the repo copy.
- Ground truth for APIs: `docs/rpc-surface.md` (117 JSON-RPC methods),
  `docs/sdk-surface.md` (SDK exports), `types/hermes-plugin-sdk.d.ts`.
  The exact hermes-agent source the installed app was built from is checked
  out at `~/.hermes/hermes-agent` (read-only reference — e.g.
  `hermes_cli/kanban_db.py` for the kanban DB API).

## Hard rules (violations = broken plugin)

1. Desktop bundles may import ONLY `@hermes/plugin-sdk`, `react`,
   `react/jsx-runtime`. esbuild fails the build otherwise.
2. No hardcoded colors/backgrounds — theme vars only
   (`var(--ui-text-secondary)`, `var(--ui-accent)`, `var(--ui-stroke-secondary)`, …).
   Prefer SDK components (`Button`, `Badge`, `ScrollArea`, `EmptyState`,
   `Dialog*`, …); verify every identifier you render is imported.
   TAILWIND SUBSET (host CSS is precompiled; unknown classes silently
   no-op): NO bracket-arbitrary utilities (`grid-cols-[…]`,
   `text-[0.6875rem]`, `transition-[…]` — proven dead, U2/2026-07-18)
   and NO named group variants (`group/x`, `group-hover/x:` — proven
   dead, P2). Allowed: standard utilities, generic `group`/
   `group-hover:`, and CSS-var paren utilities (`text-(--ui-…)`,
   `bg-(--ui-…)`, `border-(--ui-…)` — proven live). Layout rows use
   flex (`flex items-center gap-3` + `min-w-0 flex-1` + `shrink-0`),
   never arbitrary grid templates.
3. Event/click handlers read state imperatively (`host.state.x.get()` /
   `store.get()`), never from render closures.
4. Never poll faster than a few seconds. Reuse the existing `useRpc` /
   bridge-refresh machinery instead of adding new timers where possible.
5. `ctx.rest` (and the desktop `host.rest` equivalent) is locked to the
   plugin's OWN namespace. `town-home` cannot call town-feed REST directly —
   cross-plugin data flows through the window bridge that `src/town-feed`
   publishes (`windowTownFeedBridge()` / `windowTownKanbanBridge()` consumers
   in `src/town-home/plugin.tsx`, `hermes-town-feed-ready` event). Extend that
   bridge; do not add cross-namespace REST.
6. A render error can take down the whole renderer. Defensive-normalize all
   external data (see the `normalize*` helpers) and keep empty states.

## Definition of done (every spec)

- `npm run check` passes (run it un-piped; zero errors).
- `npm run build` passes.
- For gateway changes: `python3 -m py_compile gateway/town-feed/dashboard/plugin_api.py gateway/town-feed/__init__.py` passes; endpoints stay inside the
  `town-feed` namespace; state file schema changes are backward-compatible
  (old `feed-state.json` must load).
- No changes outside the files the spec puts in scope. Match the surrounding
  code style (no reformatting sweeps, no comment noise).
- Do NOT `git commit` or `git push` — the orchestrator reviews the working
  tree first and owns all commits (a lane self-committing bypasses review;
  happened once, U5/2026-07-18).
- Report: what you changed, why, and anything you discovered that the spec
  got wrong (do not silently work around spec errors — flag them).
