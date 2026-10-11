# hermes-town — instructions for the coding agent

## What this project is

Build "Town on Hermes": replicate the Town.com dashboard experience (Tasks, Need to Know, Routines, Approvals) as **runtime plugins for the Hermes Desktop app**. Full architecture, research findings, and phased build plan are in `SPEC.md` — read it first, it is the source of truth. Do NOT fork the hermes-agent repo; the plugin route was an explicit decision (SPEC §3).

## Context you'd otherwise have to rediscover

- All facts in SPEC §1 were verified against `NousResearch/hermes-agent@main` source in July 2026, not docs — web documentation for desktop plugins barely existed until 2026-07-19, when an official SDK guide landed upstream (website/docs/developer-guide/desktop-plugin-sdk.md — pitfalls match ours; it omits the precompiled-Tailwind-subset trap). Source remains the tiebreaker.
- Reference material lives in the hermes-agent repo:
  - `skills/hermes-desktop-plugins/SKILL.md` — the official authoring guide (pitfalls section is gold)
  - `skills/hermes-desktop-plugins/templates/plugin.js` — canonical plugin template
  - `apps/desktop/src/contrib/` (plugin.ts, plugins.ts, runtime-loader.ts, types.ts) — the contract
  - `apps/desktop/src/sdk/index.ts` — everything importable from `@hermes/plugin-sdk`
  - `apps/desktop/src/app/contrib/controller.tsx` — core pane registrations to mimic
  - The full checkout lives locally at `~/.hermes/hermes-agent` — the EXACT source the installed app was built from. Use it as the reference; no clone needed.
  - This repo's `docs/sdk-surface.md` and `docs/rpc-surface.md` are verified ground truth (SDK exports; all 117 gateway RPC methods with evidence). Trust them over SPEC §1 where they differ. Headlines: kanban has NO JSON-RPC and no cross-namespace REST from the renderer; no pending-approvals list RPC (events only); cron is one `cron.manage` action method.
- No community desktop plugins exist yet (checked July 2026): no GitHub topic, nothing in the awesome list, demo plugins not yet landed in `hermes-example-plugins`. There is no prior art to copy — the template + app source are it.

## Deployment topologies (generic — machine specifics live in CLAUDE.local.md, untracked)

- macOS; Hermes Desktop installed (bundle `com.nousresearch.hermes` — the plain name "Hermes" resolves to the `.setup` helper in some tools). Note: `hermes update` stops BOTH dashboard/gateway processes — restart your gateway LaunchAgent and relaunch the app afterward.
- The Hermes agent/gateway runs on a **gateway host** (`hermes serve`, typically a LaunchAgent, loopback-bound `127.0.0.1:9119`, pinned `HERMES_DASHBOARD_SESSION_TOKEN` in its plist). **Two desktop topologies are supported — keep both configs and docs alive:**
  1. **Remote gateway:** the desktop app runs on a different machine and reaches the gateway host through a persistent SSH tunnel (`127.0.0.1:9119`, launchd KeepAlive, rides Tailscale/MagicDNS), with the pinned token shared between the gateway plist and the desktop machine's `connection.json` (`authMode: token`). Setup recipe + rollback: `ops/README.md`. The tunnel+token dance exists because auth-gated (non-loopback) binds strip `hermes_home` from `/api/status`, which silently breaks ALL desktop plugins — upstream bug, filed as NousResearch/hermes-agent#66387.
  2. **Local gateway (development):** the desktop app runs on the gateway host itself. No tunnel, no `connection.json`, no token plumbing — loopback binds skip the auth gate, so `hermes_home` is present and plugins just load. Deploys and gateway-plugin installs are plain local fs writes.
     - CRITICAL: with no `connection.json`, the app spawns its **own embedded gateway process** — a `hermes serve` LaunchAgent on :9119 is a SECOND, separate instance. Both load `~/.hermes/plugins/*` and share the same state files. Consequences: (a) deploying gateway-plugin changes needs BOTH restarted — kickstart the LaunchAgent AND quit/reopen the app; (b) curling :9119 verifies the LaunchAgent instance, NOT what the app runs; (c) a new plugin REST route 405s in the app until the app restarts; (d) an outdated instance can overwrite shared state written by the newer one (symptom: capped/migrated state "reverts").
- Plugins folder: `~/.hermes/desktop-plugins/` on whichever machine runs the desktop app. Python gateway plugins (`gateway/town-feed/`) install under `~/.hermes/plugins/` on the gateway host.
- `ctx.socket` no-ops on OAuth remotes — poll via `host.request`/`ctx.rest` + `host.onEvent`; sockets are only an accelerator. (On a local or token connection sockets may actually work — unverified; don't rely on them.)
- `ctx.rest` is locked to the plugin's OWN namespace — a desktop plugin pairing with a gateway plugin must share its id (that's why the NTK pane plugin is `town-feed`).

## Hard rules (violations = broken plugin, from the official skill doc)

1. `plugin.js` loads **uncompiled**. No JSX syntax, no TypeScript — `jsx()`/`jsxs()` calls from `react/jsx-runtime`. (Preferred: author in TSX and bundle with esbuild: `--bundle --format=esm --external:@hermes/plugin-sdk --external:react --external:react/jsx-runtime`, output to the plugins folder.)
2. Only importable specifiers: `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. Anything else fails at load. All import declarations must sit at the TOP of `plugin.js`: Hermes Desktop clients before hermes-agent #120208 rewrite specifiers with a lexer that a quote inside a regex literal derails, so a later (mid-file) import stays bare and the plugin fails to load (shows its raw id on the Plugins page). `scripts/build.mjs` hoists imports and fails the build otherwise; verify against the CLIENT's loader, not the gateway host's.
3. Never hardcode colors/backgrounds. Theme vars only: `var(--ui-text-secondary)`, `var(--ui-accent)`, `var(--ui-stroke-secondary)`, etc. Leave pane backgrounds alone.
4. Prefer SDK components (`Button`, `Badge`, `ScrollArea`, `EmptyState`, `Dialog*`, …) over hand-rolled elements; verify every identifier you render is actually imported (ReferenceError otherwise).
5. Event/click handlers read state imperatively (`host.state.x.get()`), never from render closures.
6. Never poll faster than a few seconds.
7. Canvas panes need a `ResizeObserver` updating width/height attributes.
8. Respect enable/disable: if the user disabled a plugin in Settings, don't work around it.

## Dev loop

1. Write/update `<plugins folder>/<id>/plugin.js`.
2. The app fs-watches and hot-reloads within seconds; fallback ⌘K → "Reload desktop plugins" (or Settings → Plugins → Rescan).
3. Load failures show a toast naming the error; fix and save again. CAUTION: load isolation does not cover RENDER errors — a throw during render can take down the whole renderer (recoverable via Retry). Gate every deploy on an UNPIPED `npm run check`.
4. Verify in Settings → Plugins (status) and visually (pane appears, draggable).
5. You CAN see and drive the app yourself via computer use: request bundle id `com.nousresearch.hermes`; quit Magnet (`osascript -e 'quit app "Magnet"'`) before clicking — its invisible overlay blocks synthetic clicks — and relaunch it after; the Hermes window moves between displays. Prefer screenshots (no focus steal) when the user is active. CAUTION: synthetic input (CGEvent keys/pointer) is UNRELIABLE evidence — it has produced two false bug diagnoses (Escape 'not dismissing', a phantom 'pointer-dead zone'); never file or draft a bug from synthetic-input observations alone — confirm with a real user interaction or DOM/state inspection first.

## Resolved unknowns (formerly SPEC §7)

- The RPC surface is fully mapped in `docs/rpc-surface.md` (registry: `tui_gateway/server.py`, 117 `@method` registrations). The `town-debug` pane dumps `commands.catalog` + `host.status()` live.
- SDK surface verified in `docs/sdk-surface.md`; `types/hermes-plugin-sdk.d.ts` is name-accurate against the installed build.

## Architecture & roadmap

`docs/architecture-and-roadmap.md` (2026-07-17) is the consolidated current-state document: as-built system, the five LLM lanes, design principles (extend-don't-duplicate, lane-per-job, write envelopes), and the ordered remaining work (G2-G6, Tier 2 residue, Phase 5 publish). Read it before planning new work; it supersedes the roadmap half of town-gap-analysis.md.

## Build status

SPEC §6 Phases 0–4 are COMPLETE (commits c2f520d..HEAD): all six desktop plugins deployed, plus the `gateway/town-feed` Python plugin INSTALLED on the mini (2026-07-17) — plugin enabled, routes mounted, curator on the in-process scheduler. The `hermes_home` stripping bug is filed upstream (NousResearch/hermes-agent#66387). **Town-parity Tier 1 (T14–T18, from `docs/town-gap-analysis.md`) SHIPPED 2026-07-17**: /town task composer (Task=kanban card+session, Chat=session), Tasks card v2 (48h done auto-hide, checkbox complete, session click-through), humanized routines + Run Now (full prompt via `/cron-prompt/{job_id}`), NTK stacked top-3 with freshness/refresh + 10-item cap + 14-day expiry, curator-generated Suggestions chips prefilling the composer. Specs in `docs/specs/`; executor was Codex gpt-5.6-sol, orchestrator-reviewed. **G-track + G6R SHIPPED 2026-07-17/18** (ambient gmail/telegram/calendar scanners + live Town/ labeling as Hermes-managed no_agent cron jobs; curator cron-triggered; docs/specs/G6R-native-restructure.md). **Tier 2 (T19-T23) SHIPPED 2026-07-18** (run history + real Run Now via _execute_job_now wrap, greeting context line, Customize dialog, approvals depth w/ clarify.respond, empty-state sweep + Briefing wiring; docs/specs/TIER2.md). **CHARACTER pack (C1-C7) SHIPPED 2026-07-28** (docs/specs/CHARACTER.md): full event context through the pipeline (location/attendees/description/conference/event-tz/multi-day + ongoing events), deterministic `schedule_context` (conflicts, start clusters, tz skew, trips, venue changes), LLM day brief w/ hourly regeneration gating rendered as header narrative + Today-strip callouts + trip badge, Telegram thread bodies + last-speaker attribution + user_spoke_last open loops, NTK action_hint + 2-line summaries, day-aware suggestions w/ grounded first-contact check (gmail known_contacts export), TOWN_VOICE shared across LLM lanes; feed-state v6 (back-compat verified). **GROUNDING pack (R1a-R5, S1-S2) SHIPPED 2026-10-11** (docs/specs/GROUNDING.md): Telegram attribution (TDLib daemon moved into gateway/tdlib-daemon/ as LaunchAgent `local.hermes-tdlib-daemon`, reply/outgoing/chat-type contract, deterministic addressing), scripts on the PM-managed Python (Gmail LLM labeler restored), calendar in local time + cross-calendar dedupe + event types, curator consumes every scan / one item per source / LLM-vetted only / no self-exhaust, dashboard plumbing (Briefing card, chips, background Refresh, event whitelist), operator profile (`$HERMES_HOME/town-feed/operator.json`) + where-inference + Up next, people directory (`people-cache.json`) + meeting prep. Tests: `npm test` (desktop node:test + gateway unittest on the managed Python). Remaining: GROUNDING follow-ups (GROUNDING.md), G5 (parked), Town-voice pass (proposed, deferred), Phase 5 publish.

## Repo hygiene

- This folder is the project root. Suggested layout: `src/<plugin-id>/plugin.tsx` (authored) → build → `dist/<plugin-id>/plugin.js`, plus a `deploy` script that copies dist folders into the live plugins directory. Keep `gateway/town-feed/` (Python) separate with its own README for the segregated machine.
- git init early; the end goal includes publishing this as `hermes-town-plugins` (first community desktop-plugin suite).
