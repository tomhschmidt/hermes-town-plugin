# Town Feed gateway plugin

Town Feed is the server-side Need-to-Know engine for the Hermes Town desktop
plugins. It scans recent Hermes session outcomes, cron results, and changed
memory files; ranks and deduplicates the results; persists dismissal/promotion
state; and bridges the desktop plugin to Kanban without a cross-namespace REST
call.

## Install on the gateway machine

Run these commands on the machine that runs `hermes serve`, from a copy of
this repository — directly when you are working on that machine (local-gateway
topology), or over SSH when the desktop app lives on a different Mac
(remote-gateway topology; see `ops/README.md`):

```sh
mkdir -p ~/.hermes/plugins/town-feed
cp -R gateway/town-feed/. ~/.hermes/plugins/town-feed/
hermes plugins enable town-feed
launchctl kickstart -k "gui/$(id -u)/local.hermes-serve"
```

The launchd label is `local.hermes-serve`. If that LaunchAgent has not yet been
bootstrapped in the current login session, load its existing plist once and
then kick it:

```sh
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/local.hermes-serve.plist
launchctl kickstart -k "gui/$(id -u)/local.hermes-serve"
```

The required installed layout is:

```text
~/.hermes/plugins/town-feed/
|-- plugin.yaml
|-- __init__.py
`-- dashboard/
    |-- manifest.json
    |-- dist/index.js
    `-- plugin_api.py
```

`feed-state.json` and `.feed-state.lock` are created beside `plugin.yaml` on
first use. They survive gateway restarts. Do not copy an old state file over a
new install unless that history is intentional.

## Scheduling (G6R: Hermes-managed cron, since 2026-07-18)

The plugin schedules **nothing**. Four `no_agent` Hermes cron jobs own all
recurring work (visible in `hermes cron list`, the desktop Cron pane, and
the Town Routines card — pause/resume/Run Now included):

| Job | Schedule | Script | Does |
|---|---|---|---|
| `town-gmail-scan` | every 10m | `~/.hermes/scripts/town-gmail-scan.py` | scan both Gmail accounts, apply `Town/` labels (one `call_llm(task="town_inbox")` batch), emit candidates JSON |
| `town-telegram-scan` | every 10m | `~/.hermes/scripts/town-telegram-scan.py` | per-thread Telegram candidates (48h window, per-chat watermarks) |
| `town-calendar-scan` | every 30m | `~/.hermes/scripts/town-calendar-scan.py` | scope-aware events + next_event (silent no-op without calendar scope) |
| `town-feed-curate` | every 30m | `~/.hermes/scripts/town-curate.py` | loads this plugin's `run_curator` in-process with a `call_llm(task="town_curator")` adapter |

Scanner outputs land in the cron output dirs; the curator ingests any
output matching the town-scan JSON contract (`_town_scan_payload`
tolerates cron's markdown wrapper), skips our own silent-run docs, and
surfaces failing `town-*` jobs as Need-to-Know items (no delivery-based
alerting; jobs run `deliver: local`).

Model routing: `auxiliary.town_inbox` / `auxiliary.town_curator` blocks in
`config.yaml` (since the 2026-07-19 host update these blocks also accept
`key_env` to source an API key from an env var — useful if the lanes ever
split onto a separate provider) (registered for the model picker at plugin load; empty =
main model). Kill switches: `hermes cron pause <job>` and
`plugins.entries.town-feed.labeling_enabled: false`.

State files: `feed-state.json` (v5 — product state only: items,
decisions, suggestions, next_event) plus script-local
`~/.hermes/town-feed/{gmail,telegram}-state.json` (watermarks,
known-correspondent cache, labeling audit log). Google tokens are owned
by the google-workspace skill; scripts refresh in memory only.

## Verify locally on the gateway machine

Fastest full-chain check (the standing pattern — never wait for the
schedule): `hermes cron run town-gmail-scan && hermes cron run
town-feed-curate`, then `GET /feed` below.


Hermes protects non-public `/api/` routes with the dashboard's existing auth.
For a loopback `hermes serve`, copy the current session token printed in the
service log into `HERMES_SESSION_TOKEN`, then run:

```sh
export HERMES_SESSION_TOKEN='replace-with-current-dashboard-session-token'
export TOWN_FEED_URL='http://127.0.0.1:9119/api/plugins/town-feed'

curl -fsS \
  -H "X-Hermes-Session-Token: $HERMES_SESSION_TOKEN" \
  -X POST "$TOWN_FEED_URL/curate"

curl -fsS \
  -H "X-Hermes-Session-Token: $HERMES_SESSION_TOKEN" \
  "$TOWN_FEED_URL/feed"

curl -fsS \
  -H "X-Hermes-Session-Token: $HERMES_SESSION_TOKEN" \
  "$TOWN_FEED_URL/kanban"
```

Dismiss or promote an id returned by `/feed`:

```sh
curl -fsS \
  -H "X-Hermes-Session-Token: $HERMES_SESSION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"ntk_replace_me"}' \
  "$TOWN_FEED_URL/dismiss"

curl -fsS \
  -H "X-Hermes-Session-Token: $HERMES_SESSION_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"id":"ntk_replace_me"}' \
  "$TOWN_FEED_URL/promote-to-task"
```

On an OAuth-gated non-loopback deployment, use the authenticated desktop/browser
client rather than copying its cookie into shell history.

## Security

All routes inherit the dashboard server's existing auth posture and the user
plugin enabled/disabled gate. The plugin does not add a separate bearer token,
open an unauthenticated port, shell out, or call Kanban over loopback HTTP.
Promotion uses `hermes_cli.kanban_db` directly, including an idempotency key so
retries cannot create duplicate cards.
