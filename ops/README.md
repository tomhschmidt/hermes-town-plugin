# ops — remote-gateway topology setup (as built, 2026-07-16)

## When this applies

Two topologies are supported (see the README "Topologies" section):

- **Local gateway** — desktop app and gateway on the same machine
  (development). Only step (1) below applies: it is simply how the gateway
  runs. No tunnel, no `connection.json`; skip steps (2)–(3).
- **Remote gateway** — desktop app on a different Mac than the gateway
  (primary daily use). All three steps below apply. Keep this runbook and the
  tunnel plist even while developing locally; the remote setup gets
  re-activated whenever the desktop Mac is back in play.

## The problem

Hermes desktop plugins resolve their folder as
`${status.hermes_home}/desktop-plugins` (a **local** fs read), but `/api/status`
is a public endpoint that deliberately omits `hermes_home` on any non-loopback
(auth-gated) bind. With `hermes serve --host 0.0.0.0` on <gateway-host>, the app
scanned `undefined/desktop-plugins` and no desktop plugin could ever load.
Bug exists on upstream main (verified at 0c1adb48; still present at
d9ee34241, 2026-07-17); filed as
[NousResearch/hermes-agent#66387](https://github.com/NousResearch/hermes-agent/issues/66387)
— the loader should fall back to the local hermes home.

## What was changed (all reversible)

1. **<gateway-host> — `~/Library/LaunchAgents/local.hermes-serve.plist`**
   (backup: `local.hermes-serve.plist.bak-20260716`)
   - `--host 0.0.0.0` → `--host 127.0.0.1` (loopback bind ⇒ auth gate off ⇒
     `hermes_home` present in status).
   - Added `EnvironmentVariables.HERMES_DASHBOARD_SESSION_TOKEN=<token>` —
     headless `hermes serve` otherwise mints a random per-start token that
     nothing can discover (no web UI to read it from), and loopback binds still
     require it on non-public `/api/*` routes.

2. **Desktop Mac (remote topology only) — `~/Library/LaunchAgents/hermes-gateway-tunnel.example.plist`**
   (source copy: `ops/hermes-gateway-tunnel.example.plist`)
   - Persistent `ssh -N -L 127.0.0.1:9119:127.0.0.1:9119 <user>@<gateway-host>`
     under launchd KeepAlive. Rides Tailscale (<gateway-host> = your Tailscale MagicDNS name), so it
     works off-LAN. Log: `~/Library/Logs/hermes-mini-tunnel.log`.

3. **Desktop Mac (remote topology only) — `~/Library/Application Support/Hermes/connection.json`**
   (absent in the local topology — the app talks to its local gateway directly)
   - `remote.url`: `http://<gateway-host>:9119` → `http://127.0.0.1:9119`
   - `remote.authMode`: `oauth` → `token`, with the same token as (1) stored as
     `{value, encoding: "plain"}` (the app also accepts safeStorage-encrypted).

## Invariants

- The token in (1) and (3) must match. Rotate by editing both and restarting
  the mini agent (`launchctl kickstart -k gui/$(id -u)/local.hermes-serve`).
- Anything else that used `http://<gateway-host>:9119` over the network must now
  tunnel the same way.
- If launchd `bootstrap` returns `Input/output error`, wait a few seconds and
  re-run; `launchctl load` is the working fallback.

## Rollback

Restore the `.bak-20260716` plist on the mini, bootout/bootstrap it, revert
`connection.json` to the old URL with `authMode: "oauth"`, sign in, and
`launchctl bootout gui/$(id -u)/com.example.hermes-gateway-tunnel` here.
