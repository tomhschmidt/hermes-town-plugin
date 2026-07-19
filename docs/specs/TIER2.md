# Tier 2 spec pack (T19–T24)

*Drafted 2026-07-18 after G6R. Sources: town-gap-analysis Tier 2 + the
re-scopings in architecture-and-roadmap.md. G5 (drafts) is parked by
decision. Shared conventions: docs/specs/README.md still binds; executor
= Codex gpt-5.6-sol medium, parallel where files are disjoint.*

## T19 — Real run history + server-side Run Now for routines

The T16 gaps, closed properly. The gateway plugin runs inside the
dashboard server process, so it wraps cron internals DIRECTLY (no
proxying): new namespace endpoints `GET /cron-runs/{job_id}` (last N runs
from `cron.jobs` state + output dir: status, started, duration, first
~2000 chars of output) and `POST /cron-trigger/{job_id}` (the same
run-now path `hermes cron run` uses — real bookkeeping, unlike T16's
session.create simulation). Bridge methods + desktop: Routines pane and
/town card get (a) "Completed 2h ago" linkified → run-history popover
(last 3 runs, output preview), (b) Run Now switched to the trigger
endpoint for cron-backed rows (the session.create path stays only as
fallback when the bridge is absent). The four town-* jobs get the same
treatment — replacing the retired plugin-thread visibility.
Files: gateway plugin_api.py, src/town-feed (bridge), src/town-routines,
src/town-home.

## T20 — Greeting personalization + context line

"Good evening, Tom" (display name from config — investigate
`hermes_cli.config` for an owner/name key; fall back to the gateway
profile or omit). Second line composes what exists: next_event (shipped,
dormant), waiting-on-you count, next routine countdown — priority order,
one line, muted. Files: src/town-home only.

## T21 — Home customization (Town's Customize, lite)

Per-card show/hide + card order for /town (Tasks, NTK, Suggestions,
Routines, Briefing) + the done-task auto-hide window (12h/24h/48h/1w),
persisted via the SDK plugin `ctx.storage` (verified surface:
storage.get/set with fallback). UI: small Customize dialog from a header
gear (SDK Dialog components), imperative handlers, defensive load.
Files: src/town-home only.

## T22 — Approvals depth

Three upgrades to town-approvals: (1) richer cards — parse
approval.request payloads for tool name + args preview (truncated,
theme-safe); (2) clarify support — INVESTIGATE the clarify.request event
payload + response RPC (registry has the request side; confirm respond
method name in rpc-surface/live catalog): render option buttons
Town-Present-Options-style and respond; (3) history — last 20 resolved
approvals/clarifies (client-side ring buffer via ctx.storage) in a
collapsed "Recent" section. Files: src/town-approvals only (bridge not
needed — events + RPC are namespace-free).

## T23 — Empty-state + Briefing wiring sweep

Every /town card gets a designed empty state (SDK EmptyState, one line +
one action). Briefing card stops being a stub: if a briefing-shaped cron
exists (name match morning/brief), show its last run outcome (via T19's
/cron-runs) with a link; else the empty state offers Hermes's NATIVE
morning-brief blueprint ("/blueprint morning-brief" — copy button +
composer prefill). Files: src/town-home (+ bridge reuse from T19).

## T24 — People-lite (phase 1: memory profiles, no new UI)

The first agent-mode job in the suite, in the T12-safe shape: cron
`town-people` (agent mode, daily, `context_from: [town-gmail-scan job id,
town-telegram-scan job id]`, `enabled_toolsets` scoped to memory tools)
with a prompt that maintains per-person memory entries (who, role, last
interaction, open threads) from the injected scanner output — Town's
auto-CRM built entirely from Hermes primitives. Deliverable includes the
prompt file (versioned in repo), the job creation, and a verification
recipe (inspect memory entries after a manual `hermes cron run`).
Desktop UI deferred to phase 2 by design.
Files: gateway/scripts/town-people-prompt.md (or cron prompt inline),
job creation at deploy; no plugin/TSX changes.

## Explicitly deferred / gated

- Schedule slide-over panel: blocked on calendar scope re-auth (data
  exists the moment a token carries calendar.*; revisit then).
- Routine detail page: superseded by Blueprints tab + T19 popover unless
  gaps remain after T19 ships.
- G5 drafts: parked by decision.

## Execution plan

Three parallel lanes (disjoint files): Lane A = T19 (gateway+bridge+
routines). Lane B = T20→T21→T23 sequential (all town-home). Lane C = T22
(approvals). T24 follows T19 (needs job ids stable) — or runs anytime as
it touches no shared files. Per-task review gate, manual-fire
verification, single combined deploy per lane.
