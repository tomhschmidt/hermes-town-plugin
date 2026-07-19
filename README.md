# hermes-town-plugin

[Town.com](https://www.town.com)'s "dashboard for your life" — Tasks, Need to
Know, Suggestions, Routines, Approvals, an auto-labeled inbox — rebuilt as
**runtime plugins for [Hermes](https://github.com/NousResearch/hermes-agent)
Desktop**, running entirely against your own Hermes gateway and your own
accounts. No third-party service: the ambient intelligence (Gmail/Calendar/
Telegram scanning, curation, inbox labeling) runs as Hermes-managed `no_agent`
cron scripts on your gateway machine, and the dashboard renders in Hermes
Desktop. First community desktop-plugin suite for Hermes (SPEC.md is the
original design document; `docs/specs/` is the running engineering record).

Status: working end-to-end for its authors; opened up for friends to test and
contribute. Expect rough edges — file issues liberally.

## The suite

| Plugin (id) | What it adds |
|---|---|
| `town-home` | The `/town` board (+ sidebar nav, `⌘⇧H`, palette): serif greeting w/ schedule eyebrow + View-schedule dialog, task/chat composer, kanban-backed Tasks with "Needs input" states, Recents (routine outcomes + sessions), and a rail of Need to Know, Suggestions, and Routines cards — all customizable (card order/visibility, done-task window, greeting name) |
| `town-routines` | Routines pane (pause/resume/Run Now via real cron triggers, run history with cleaned output), next-routine statusbar countdown, "New routine…" dialog |
| `town-approvals` | Event-driven approval cards (Approve/Deny), clarify.request option cards, pending-count statusbar badge, recent-decision history |
| `town-feed` | Full Need-to-Know feed page with undo-able dismiss; owns the window bridge that pairs the desktop suite with the gateway engine |
| `town-layout` | "Town" preset in the layout picker |
| `town-debug` | Dev pane dumping `commands.catalog` + `host.status()` |
| `hello` | Smoke-test plugin (safe to disable) |
| `gateway/town-feed` | **Python gateway plugin**: NTK curator (label-aware ranking, open-loops memory, notable-charge detection, event prep), feed/dismiss/undismiss/kanban/cron REST under its namespace. See [gateway/town-feed/README.md](gateway/town-feed/README.md) |
| `gateway/scripts/` | The four `no_agent` cron scanners (gmail w/ auto-labeling + lookback, telegram, calendar w/ full calendarList, curator trigger) — visible and controllable as normal Hermes cron jobs |

## Prerequisites

- **Hermes** v0.18.2+ (recent upstream `main` recommended — this suite tracks
  it closely): Desktop app + `hermes serve` gateway.
- **macOS** is what the authors run; nothing is intentionally macOS-only
  except the launchd recipes in `ops/`.
- Gmail/Calendar lanes: Google account(s) connected via Hermes's
  `google-workspace` skill (its token files are the source of truth; the
  scanners refresh in memory and never write them).
- Optional: the Hermes Telegram integration (the Telegram scanner reuses its
  collection), and `auxiliary.town_inbox` / `auxiliary.town_curator` blocks
  in `config.yaml` to route the two LLM lanes (defaults to your main model;
  `key_env` supported for split providers).

## Dev loop

```sh
npm install        # once
npm run check      # tsc gate — run it UNPIPED before shipping
npm run build      # src/<id>/plugin.tsx → dist/<id>/plugin.js (esbuild; verifies imports)
npm run deploy     # dist → $HERMES_HOME/desktop-plugins/<id>/ (default ~/.hermes)
```

The app fs-watches the plugins folder: deploys hot-reload within seconds
(fallback: ⌘K → "Reload desktop plugins"). `docs/sdk-surface.md` and
`docs/rpc-surface.md` are the verified ground truth for the SDK exports and
gateway RPC methods — trust them over SPEC.md §1 where they differ.

## Topologies: local vs remote gateway

Both are supported; pick per session.

- **Local gateway (simplest — best for development).** Desktop app and
  `hermes serve` on the same machine, gateway loopback-bound. Nothing to
  configure: loopback binds skip the auth gate, `/api/status` includes
  `hermes_home`, and both `npm run deploy` (desktop half) and the
  `gateway/town-feed` install (Python half) are plain local file copies.
- **Remote gateway (daily-driver setup).** Desktop app on one machine, gateway
  on another. This needs the setup in [ops/README.md](ops/README.md) —
  loopback-bind the gateway, reach it through a persistent SSH tunnel, and pin
  a shared dashboard session token in the gateway's LaunchAgent env and the
  desktop's `connection.json` (`authMode: token`). Required because auth-gated
  (non-loopback) binds strip `hermes_home` from `/api/status`, which silently
  breaks all desktop-plugin loading (upstream bug, filed as
  [NousResearch/hermes-agent#66387](https://github.com/NousResearch/hermes-agent/issues/66387)).
  The Python gateway plugin installs on the gateway machine (e.g. over SSH).

In both topologies `ctx.socket` may be unavailable (it no-ops on OAuth
remotes) — the suite is designed around polling + `host.onEvent`, with sockets
as an optional accelerator.

## Hard-won constraints (violating these breaks plugins)

- Bundles may import only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`
  (the build fails otherwise). Theme vars only, never hardcoded colors.
- The host app's CSS is a **precompiled Tailwind subset**: bracket-arbitrary
  utilities (`grid-cols-[…]`, `text-[11px]`) and named group variants
  (`group/x`) silently no-op. Proven utility strings, theme variables, and
  inline styles only — full rule in [docs/specs/README.md](docs/specs/README.md).
- `ctx.rest` is locked to `/api/plugins/<own-plugin-id>` — that's why the
  desktop NTK plugin is named `town-feed`, pairing it with the gateway plugin.
  Kanban has **no JSON-RPC** and no cross-namespace REST; the renderer reaches
  it only through the `town-feed` gateway bridge.
- There is no pending-approvals list RPC; `town-approvals` reconstructs state
  from `approval.request` / `message.complete` / `error` events.
- A plugin render error can take down the whole renderer (error isolation
  covers load, not render). Never deploy an unverified bundle.
- Remote-gateway topology needs `hermes_home` in `/api/status`, which
  auth-gated binds strip — see "Topologies" above and
  [ops/README.md](ops/README.md) for the loopback-bind + tunnel + pinned-token
  setup that fixes it (upstream:
  [NousResearch/hermes-agent#66387](https://github.com/NousResearch/hermes-agent/issues/66387)).

## Repo layout

`src/<id>/plugin.tsx` (authored TSX) → `dist/<id>/plugin.js` (built) →
deployed. `gateway/town-feed/` is the Python half, installed manually on the
gateway machine (locally or over SSH — see Topologies). `ops/` documents the
remote-gateway connection setup.

## License

[MIT](LICENSE).
