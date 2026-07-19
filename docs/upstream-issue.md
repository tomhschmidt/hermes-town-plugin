# Desktop runtime plugins silently fail to load on remote-gateway topologies

**Filed as PR:** https://github.com/NousResearch/hermes-agent/pull/66387 (fix + full report in the PR body)

**Repo:** NousResearch/hermes-agent · **Area:** apps/desktop (runtime plugin loader)
**Verified at:** `0c1adb48` (main, 2026-07-15) — both halves of the bug are present on current main. Re-checked at `d9ee34241` (2026-07-17): still unfixed.

## Summary

On any remote-gateway connection whose dashboard bind is auth-gated (i.e. every
non-loopback bind, since `--insecure` no longer disables the gate), **no
runtime desktop plugin can ever load**, silently. The loader builds its scan
path from the gateway's `/api/status` response, but auth-gated binds
deliberately omit `hermes_home` from that public endpoint, so the loader scans
the literal path `undefined/desktop-plugins` and swallows the failure.

## Environment

- Hermes Desktop v0.18.2 (source build) on macOS, connected via
  **Remote gateway** to `hermes serve --host 0.0.0.0 --port 9119` (basic auth)
  on a second machine.
- Plugins placed in `~/.hermes/desktop-plugins/<id>/plugin.js` on the desktop
  machine, exactly per `skills/hermes-desktop-plugins/SKILL.md`.

## Symptom

- Settings → Plugins shows **"0 installed"** indefinitely; the fs-watch/5s
  poll never picks anything up. No error toast, no log line.
- The only user-visible evidence: Settings → Plugins → **Open plugins folder**
  toasts `ENOENT: no such file or directory, mkdir 'undefined'`.

## Root cause chain

1. `apps/desktop/src/contrib/runtime-loader.ts` (`scanDiskPlugins`):
   ```ts
   const { hermes_home } = await getStatus()
   const { entries } = await desktop.readDir(`${hermes_home}/desktop-plugins`)
   ```
   `desktop.readDir` is a **local** fs IPC (`electron/main.ts`,
   `hermes:fs:readDir`), but the path prefix comes from the **gateway's**
   status response.
2. `hermes_cli/web_server.py` (~line 2805): `/api/status` is in
   `PUBLIC_API_PATHS`, so on a gated bind it intentionally omits
   `hermes_home` / `config_path` / `env_path` ("deployment recon" — the
   loopback/gated split drawn by `should_require_auth`). This applies even to
   authenticated callers, because the endpoint itself is public.
3. The template string yields `undefined/desktop-plugins`, resolved against
   the app's cwd (`/` for a Finder-launched app) → ENOENT → swallowed by the
   loader's catch ("No desktop-plugins dir (or no gateway yet) — nothing to
   reconcile"). Result: permanent, silent no-op.
4. The same `${hermes_home}/desktop-plugins` template exists in
   `apps/desktop/src/app/settings/plugins-settings.tsx` (`revealPluginsDir`),
   which is what surfaces the one visible `mkdir 'undefined'` toast.

## Reproduction

1. On machine B: `hermes serve --host 0.0.0.0 --port 9119` (any auth-gated bind).
2. On machine A: connect Hermes Desktop → Settings → Gateway → Remote gateway.
3. Drop the official template plugin into `~/.hermes/desktop-plugins/hello/plugin.js` on machine A.
4. Observe: never loads; Rescan does nothing; "Open plugins folder" toasts the `mkdir 'undefined'` error.

## Proposed fix

Desktop runtime plugins are a client-side feature; the folder should resolve
from the **local** hermes home rather than trusting a field the gateway may
legitimately withhold:

- Electron main already computes `HERMES_HOME` (`resolveHermesHome()`,
  `electron/main.ts`). Expose it to the renderer (one `ipcMain.handle` +
  preload line, e.g. `hermesDesktop.getHermesHome()`).
- In `runtime-loader.ts` and `plugins-settings.tsx`:
  `const home = status.hermes_home ?? await desktop.getHermesHome()` —
  conservative fallback that cannot regress local-gateway setups.

Happy to submit this as a PR.

## Workaround we're running

Rebind the remote's dashboard to loopback (its default), reach it through a
persistent SSH tunnel, and pin `HERMES_DASHBOARD_SESSION_TOKEN` in the serve
LaunchAgent so the desktop's token-mode remote auth works against the
headless serve (which has no web UI to mint/serve a discoverable token).
This works in our case only because both machines share the same
`/Users/<user>` home path — the fix should not rely on that coincidence.

## Related observation (can file separately)

Plugin **render** errors escape isolation: a `ReferenceError` thrown during a
statusbar contribution's render took down the entire renderer ("Something
broke in the interface", recoverable via Retry). The loader's error isolation
covers load/registration, but contribution render sites appear to have no
per-plugin error boundary — one bad plugin build can crash the whole app UI.
