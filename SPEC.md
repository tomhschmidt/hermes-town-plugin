# Spec: Replicating the Town Experience on Hermes Desktop

**Goal.** Recreate Town.com's "dashboard for your life" — Tasks, Need to Know, Routines, Approvals — as a set of Hermes Desktop plugins running against your own Hermes instance, on your own hardware, with your own model choice and data retention.

**Recommendation up front:** build this as **runtime desktop plugins** (Option A), not a fork. The desktop app's contribution system is expressly designed for exactly this, plugins hot-reload with zero build step, and upstream is moving too fast to fork comfortably (your install has a 223-change update pending). Fall back to bundled-plugin mode (Option B) only if you outgrow the no-build constraints.

---

## 1. The desktop plugin system (verified from source, `NousResearch/hermes-agent@main`)

### 1.1 Delivery modes

| Mode | Location | Language | Loading |
|---|---|---|---|
| **Runtime** ("the disk door") | `$HERMES_HOME/desktop-plugins/<name>/plugin.js` (`~/.hermes/...` by default; `~/.hermes/profiles/<profile>/...` under a named profile) | Plain ESM JS, **uncompiled** — `jsx()` calls, not JSX syntax | fs-watched: loads seconds after the file lands, **hot-reloads on every save**. Fallback: ⌘K → "Reload desktop plugins" |
| **Bundled** | `apps/desktop/src/plugins/<name>/plugin.{ts,tsx}` in a source checkout | TypeScript/JSX (vite glob) | Discovered at build time; requires running/building the app from source |

Both modes use the identical contract and appear in **Settings → Plugins** with live enable/disable (a disabled plugin survives restarts).

### 1.2 The contract

A plugin default-exports a `HermesPlugin`:

```js
export default {
  id: 'town-home',        // must equal the folder name
  name: 'Town Home',
  defaultEnabled: true,
  register(ctx) { /* wire contributions */ }
}
```

`ctx` (scoped per plugin; ids auto-namespaced, collisions impossible):

- `ctx.register({ id, area, title?, order?, when?, enabled?, render?, data? })` → disposer
- `ctx.registerMany([...])`
- `ctx.rest(path, opts)` — REST to the plugin's own backend namespace `/api/plugins/<id>` (for plugins that ship a Python `plugin_api.py` on the gateway side)
- `ctx.socket(path, onMessage)` — WebSocket twin of `rest`, auto-reconnect
- `ctx.storage.get/set/remove` — JSON persistence namespaced under `hermes.plugin.<id>.*`

`host` (from `@hermes/plugin-sdk`, the **only** importable module besides `react`/`react/jsx-runtime`):

- `host.state.*` — readonly reactive atoms: `activeSessionId`, `cwd`, `gateway`, `model`, `profile`, `viewport` (`.get()` in handlers, `useValue()` in components)
- `host.request(method, params)` — **gateway JSON-RPC: sessions, config, skills, cron, kanban — everything the app itself uses**
- `host.onEvent(type, fn)` — live gateway stream (message deltas, session lifecycle, tool activity; `'*'` for all)
- `host.notify({kind, message})`, `host.navigate(path)`, `host.logs(...)`, `host.status()`, `host.restartGateway()`
- UI kit re-exported from the SDK: `Button, Input, Select*, Switch, Tabs*, Dialog*, ConfirmDialog, DropdownMenu*, Popover*, Badge, SearchField, ScrollArea, Skeleton, EmptyState, ErrorState, StatusDot, LogView, Codicon, icons.*, cn, Tip/Tooltip`, plus `useI18n`, `queryClient`, `profileColor`

### 1.3 Contribution areas (the surfaces you can occupy)

| Area | What it gives you |
|---|---|
| `'panes'` | A **layout pane** — a first-class tile in the app's tiling grid. `data: { placement: 'left'\|'right'\|'bottom'\|'main', dock?: {pane, pos}, width?, height?, collapsible? }`. Users drag it anywhere; it stacks/tabs with other panes |
| `ROUTES_AREA` | A **full page** (`data: { path: '/town' }` + `render`) mounted in the workspace pane like built-in views |
| `SIDEBAR_NAV_AREA` | A nav row in the left sidebar (`data: { path, label, codicon }`) that lights up at the route |
| `'layouts'` | A **layout preset** (`data: LayoutNode`) — appears in the app's layout picker next to Default/Focus/Quad |
| `STATUSBAR_AREAS` (`statusBar.left/right`) | Persistent chips in the status bar |
| `TITLEBAR_AREAS` | Title bar controls |
| `PALETTE_AREA` | ⌘K commands |
| `KEYBINDS_AREA` | Rebindable keyboard actions |
| `COMPOSER_AREAS` | Chat composer middleware/attachment providers |

Existing core pane ids you can dock against: `workspace` (main chat), `sessions`, `terminal`, `files`, `preview`, `review`, `logs`.

### 1.4 Constraints & security (from `runtime-loader.ts`)

- Runtime plugins may import **only** `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`. Any other bare specifier fails at load with a clear error.
- No JSX/TS compilation — write `jsx('div', {...})` calls (or precompile yourself; the loader only sees the final `plugin.js`).
- Error isolation only, **not** a sandbox: a loaded plugin runs with full renderer authority (gateway RPC, storage, navigation). Fine for files on your own disk; don't install plugin.js files you haven't read.
- Optional `sha256` integrity check exists at the loader seam.
- Never hardcode colors — use theme vars (`--ui-text-secondary`, `--ui-accent`, etc.) so plugins track the active theme.
- Broken plugins toast an error and never take the app down; re-saving hot-reloads cleanly (loader disposes previous registrations first).

### 1.5 Ecosystem status (as of July 2026)

There are **no published community desktop plugins yet** — the feature is weeks old; the official reference material is the `hermes-desktop-plugins` skill (in-repo: `skills/hermes-desktop-plugins/SKILL.md` + `templates/plugin.js`). The demo plugins mentioned in the repo README (counter, gateway-pill rebuild, runtime hello-world) haven't landed in `hermes-example-plugins` yet. Design patterns worth borrowing from the adjacent web-dashboard plugin ecosystem: `strike-freedom-cockpit` (theme + slot HUD composition) and `agent-analytics-hermes-plugin` (theme-aware data widgets). You'd effectively be first — this spec doubles as a publishable community project.

A key phrase from the Settings panel: plugins include "**ones Hermes writes**." The intended workflow is that your agent authors and iterates on these panes itself via the `hermes-desktop-plugins` skill — you can bootstrap the whole dashboard by feeding this spec to Hermes.

---

## 2. Town surface inventory → Hermes mapping

Town's web app (per its docs): lands on **Tasks** by default; sidebar navigation; the surfaces that matter are Tasks, Need to Know, Routines, Approvals, plus Library/People/Memory.

| Town surface | Behavior to replicate | Hermes data source | Plugin surface |
|---|---|---|---|
| **Tasks** (default landing) | Single place for assistant work, unread activity, follow-ups, items waiting on your response | Kanban RPC (Hermes's multi-agent kanban), session list + unread state via `host.request` / `host.onEvent` session lifecycle | Route `/town` (landing page) + `panes` widget |
| **Need to Know** | Paginated, click-to-expand carousel of timely updates; remembers dismissed and task-created items | A Hermes **cron routine** that curates updates into a store (file/SQLite via a small Python plugin exposing REST); dismissal state in `ctx.storage` | Carousel card on `/town` + optional right-rail pane |
| **Waiting on you / Approvals** | Context-rich approval cards; approve from anywhere; work resumes on approval | Gateway approval flow — `host.onEvent` approval requests, `approval.respond` RPC (same door the core app uses) | Pane docked right + statusbar badge counter |
| **Routines** | Stock + custom automations; schedules and triggers; plain-language creation | Hermes **cron jobs** (`host.request` cron methods — the app's Cron pane already manages them); "create a routine that…" = prompt to the agent, who writes the cron job | Routines widget on `/town`; deep-link `host.navigate` to the core cron surface for editing |
| **Morning Briefing / Daily Summary** | Scheduled digest delivered to the dashboard | Cron job writing markdown to a known path or kanban card; widget renders latest | Briefing card on `/town` |
| **Library / Content Organizer** | Centralized docs the assistant references, auto-organized daily | Workspace files (`files` core pane already exists) + a cron "organizer" routine; memory browser for MEMORY.md | Reuse core `files` pane in the layout preset; optional memory widget |
| **People** | Contact/relationship context | Hermes memory (USER.md / memory graph) | Optional later-phase widget |
| **Chat/Threads** | Talk to the assistant | Core `workspace` + `sessions` panes — already native | No work needed |
| **Approval modes / per-tool controls** | Safety posture | Hermes toolset/approval config (Settings) | Out of scope — already native |

The insight: **Hermes already has every backend primitive** (sessions, cron, kanban, approvals, memory, skills). Town's value is the *presentation layer* — a calm, widgetized "what needs me / what happened / what's scheduled" home. That's precisely what the contribution system can build.

---

## 3. Architecture options

### Option A — Runtime plugin suite (recommended)

A small family of plugins under `~/.hermes/desktop-plugins/`, each independently toggleable in Settings → Plugins:

```
~/.hermes/desktop-plugins/
├── town-home/plugin.js        # the dashboard: route /town + sidebar nav + widget grid
├── town-approvals/plugin.js   # approvals pane (right dock) + statusbar badge
├── town-need-to-know/plugin.js# NTK carousel pane (consumes curated feed)
├── town-routines/plugin.js    # cron/routine summary widget + palette commands
└── town-layout/plugin.js      # 'Town' layout preset in the layout picker
```

Plus one **gateway-side Python plugin** (standard `~/.hermes/plugins/town-feed/`) that:

- exposes REST endpoints the panes consume via `ctx.rest` (`/feed`, `/dismiss`, `/promote-to-task`)
- registers the cron-run curation logic (the "Need to Know engine") — scans recent sessions, cron outputs, memory diffs, and (if connected) messaging-platform events, and writes a ranked feed
- optionally pushes live updates over `ctx.socket`

Pros: zero fork, hot-reload iteration, survives app auto-updates untouched, agent-authorable ("Hermes, add a weather widget to my dashboard"), each widget independently disableable.
Cons: uncompiled `jsx()` authoring ergonomics; single-file per plugin (keep widgets small); no imports beyond SDK.

**Mitigation for ergonomics:** author in TS/JSX in a repo of your own and add a 5-line esbuild step (`--bundle --format=esm --external:@hermes/plugin-sdk --external:react --external:react/jsx-runtime`) whose output lands in `~/.hermes/desktop-plugins/<name>/plugin.js`. You get real JSX with none of the fork costs — the loader can't tell the difference.

### Option B — Bundled-plugin soft fork

Clone `hermes-agent`, add `apps/desktop/src/plugins/town-*/plugin.tsx`, run `hermes desktop --source` or build installers. Same contract, full TS/JSX + repo tooling.

Use only if: you need multiple files per plugin without a bundler, want type-checking against real SDK types in-tree, or intend to upstream a PR. Cost: you now rebase against a repo shipping hundreds of changes per release, and lose one-click app updates.

### Option C — Full fork (new chrome, new areas)

Only justified if the contribution areas can't express something (e.g., replacing the entire shell with a widget canvas). Everything in §2 fits areas that already exist. **Not recommended** — the pace of upstream (223 changes in your pending update alone) makes fork maintenance a part-time job.

**Decision rule:** A now; promote a plugin to B only when it demonstrably can't be expressed through the SDK; C never, unless the project becomes "your own product."

### Remote-connection note (your topology)

You run the desktop app on your main machine connected remotely to Hermes on the segregated box. Desktop plugins load from `$HERMES_HOME/desktop-plugins/` **on the machine running the desktop app** — data flows through `host.request` to the remote gateway. Two implications: (1) the Python `town-feed` plugin installs on the **segregated machine** (`~/.hermes/plugins/`), while the `plugin.js` files live on your laptop; (2) note the loader comment — `ctx.socket` resolves to a no-op on OAuth remotes, so poll via `ctx.rest`/`host.request` as the baseline and treat sockets as an accelerator. If the agent should self-author UI ("ones Hermes writes"), give it a synced path or write plugins via a local session.

---

## 4. The Town layout (what it looks like)

Register a `'Town'` layout preset (`area: 'layouts'`) so one click in the layout picker produces:

```
┌────────────┬──────────────────────────────┬───────────────┐
│ sessions   │  workspace (main)            │ town-approvals│
│ (core)     │  → /town route = dashboard   │ (plugin pane) │
│            │    landing page              ├───────────────┤
│            │                              │ files (core)  │
├────────────┴──────────────────────────────┴───────────────┤
│ terminal (core, collapsed by default)                      │
└────────────────────────────────────────────────────────────┘
```

The `/town` route (workspace main view) is the Town-style home:

- **Header strip** — greeting, date, gateway/model status (`host.state`), profile color accent
- **Tasks** — kanban cards + sessions awaiting response, click → `host.navigate` to the session
- **Need to Know** — paginated click-to-expand carousel; Dismiss / Turn into task actions (state → `ctx.storage` + REST)
- **Routines** — next scheduled runs + last outcomes from cron RPC; toggle/pause inline; "＋ New routine" opens a composer prompt pre-filled with "Create a routine that…"
- **Briefing** — latest Morning Briefing markdown rendered inline

Statusbar chips (always visible in any layout): pending-approvals badge, next-routine countdown. ⌘K commands: "Go to Town home", "Reload desktop plugins", "New routine…".

---

## 5. Skeleton: the dashboard plugin

`~/.hermes/desktop-plugins/town-home/plugin.js` (abridged; follows the official template conventions):

```js
import { Badge, EmptyState, host, icons, ScrollArea, Tip, useValue,
         ROUTES_AREA, SIDEBAR_NAV_AREA } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useEffect, useState } from 'react'

function useRpc(method, params, intervalMs = 15000) {
  const [data, setData] = useState(null)
  useEffect(() => {
    let live = true
    const pull = () => host.request(method, params)
      .then(d => live && setData(d)).catch(() => {})
    pull()
    const t = setInterval(pull, intervalMs)      // never poll faster than a few seconds
    const off = host.onEvent('*', pull)          // event-driven refresh accelerator
    return () => { live = false; clearInterval(t); off() }
  }, [method])
  return data
}

function TownHome() {
  const model = useValue(host.state.model)
  // Card sections: Tasks (kanban RPC), Need to Know (ctx.rest feed),
  // Routines (cron RPC), Briefing (latest digest) — each its own component.
  return jsxs('div', { className: 'flex h-full flex-col gap-4 p-6', children: [ /* … */ ] })
}

export default {
  id: 'town-home',
  name: 'Town Home',
  register(ctx) {
    ctx.registerMany([
      { id: 'page', area: ROUTES_AREA, data: { path: '/town' }, render: () => jsx(TownHome, {}) },
      { id: 'nav',  area: SIDEBAR_NAV_AREA,
        data: { path: '/town', label: 'Town', codicon: 'home' } }
    ])
  }
}
```

(Real implementation: verify exact kanban/cron/approval RPC method names against your build — `host.request('commands.catalog')` and the app's own Cron/Command-Center panes are the reference. The `approval.respond`, `session.title`, `config.set`, `llm.oneshot`, `model.options` methods are confirmed in-source.)

---

## 6. Build plan

**Phase 0 — Prep (½ day).** Update the app (pending update). Confirm `$HERMES_HOME`, profile, and Settings → Plugins folder path. Drop the official `templates/plugin.js` in as `hello/` to validate the hot-reload loop end to end. Set up the esbuild repo if you want JSX.

**Phase 1 — Static dashboard (1–2 days).** `town-home` with route + nav + layout preset; hardcoded cards; get the visual language right using SDK components and theme vars only.

**Phase 2 — Live data (2–3 days).** Wire Tasks to kanban/session RPC; Routines to cron RPC; Briefing to a Morning Briefing cron job you define (Hermes ships routine-like cron already). Add statusbar chips. Everything read-only.

**Phase 3 — Actions (2–3 days).** `town-approvals` pane: subscribe to approval events, render context-rich cards, respond via RPC. Task actions (open session, mark done). Routine pause/resume. Palette commands + keybinds.

**Phase 4 — Need to Know engine (3–5 days, the only real new backend).** Python plugin `town-feed` on the Hermes box: a cron-driven curator (uses `ctx.llm.complete` for ranking/summarizing — plugin LLM access is a supported API) + REST endpoints; desktop carousel with dismiss/promote semantics persisted server-side.

**Phase 5 — Polish & publish (ongoing).** Theme-follow audit, narrow-viewport behavior (`host.state.viewport.narrow`), i18n via `useI18n`, then publish the suite as `hermes-town-plugins` — first mover in an empty niche the awesome-list will happily take.

Total: roughly two focused weeks to Town-parity on the surfaces that matter, with Phases 1–3 delivering daily-usable value in under a week.

---

## 7. Risks & open questions

- **API churn.** The plugin SDK is weeks old; expect breaking changes across releases. Mitigation: plugins are tiny and hot-reloadable; pin expectations to the `hermes-desktop-plugins` skill doc, which ships in-repo and updates with the app.
- **RPC surface discovery.** Gateway method names for kanban/cron aren't formally documented; derive them from the app source (`apps/desktop/src`) or `commands.catalog` at runtime. Worth a small `town-home` debug pane that dumps available methods during development.
- **Remote socket limitation.** `ctx.socket` no-ops on OAuth remotes (per loader source) — design for polling + `host.onEvent`, treat sockets as an accelerator (see §3).
- **Trust.** Runtime plugins have full renderer authority. Keep the suite in your own repo, read any third-party plugin.js before installing, and consider the `integrity` hash once you distribute.
- **Need to Know quality.** Town's NTK is their secret sauce (signal ranking). Yours will start cruder; the curator prompt is where iteration pays off — and it's exactly the kind of thing Hermes's self-improving skill loop is good at refining.

---

## Appendix: primary sources

- Authoring guide: `skills/hermes-desktop-plugins/SKILL.md` + `templates/plugin.js` (in-repo, ships with your install)
- Contract & loader: `apps/desktop/src/contrib/{plugin,plugins,runtime-loader,types,registry}.ts`
- SDK surface: `apps/desktop/src/sdk/index.ts`
- Areas & core panes: `apps/desktop/src/app/contrib/controller.tsx`, `components/pane-shell/tree/presets.ts`
- Repos: `github.com/NousResearch/hermes-agent`, `github.com/NousResearch/hermes-example-plugins`
- Town surfaces: `town.com/docs` (Tasks, Need to Know, Routines, Approvals, integrations, safety modes)
