# Hermes desktop plugin SDK surface

> **Official docs landed 2026-07-19** (upstream #67301): the SDK now has a
> first-party guide at `website/docs/developer-guide/desktop-plugin-sdk.md`
> (+ ctx.i18n in the authoring skill). Its pitfalls list matches this doc's
> hard rules 1:1; its export table cross-checks clean against ours EXCEPT
> `ComposerMiddleware` / `ComposerAttachmentProvider` (composer extension
> area — never used by this suite; backlog candidate). The official guide
> does NOT cover the precompiled-Tailwind-subset trap (bracket-arbitrary
> utilities and named group variants silently no-op) — that remains
> documented only here and in docs/specs/README.md hard rule 2; upstream
> doc-PR candidate. New in this update: `ctx.i18n` / `usePluginI18n`
> (plugin-scoped locale bundles, additive).

Ground truth is the installed source at `~/.hermes/hermes-agent`, not the prose in `SPEC.md`. The public barrel is `apps/desktop/src/sdk/index.ts`; the context contract is `apps/desktop/src/contrib/plugin.ts`. Re-verified against `d9ee34241` (2026-07-17): neither file changed since the `0c1adb48` baseline, so every citation and `types/hermes-plugin-sdk.d.ts` remain exact.

Every SDK name claimed in SPEC section 1.2 exists. There are no missing exports or renames. The meaningful contract drift is narrower: `HermesPlugin.name` and `defaultEnabled` are optional, `PluginContext` also exposes readonly `source`, and `storage.get` requires a fallback value. The SDK describes `host.state.gateway` as the gateway socket state, not a gateway URL. See `apps/desktop/src/contrib/plugin.ts:35`, `apps/desktop/src/contrib/plugin.ts:56`, and `apps/desktop/src/sdk/index.ts:58`.

| Surface | SPEC claim confirmed | Drift or useful exports omitted by SPEC | Evidence |
|---|---|---|---|
| Plugin/context | `HermesPlugin`; `register`, `registerMany`, `rest`, `socket`, `storage.get/set/remove` | `name?`, `defaultEnabled?`, readonly `ctx.source`; `storage.get(key, fallback)` | `apps/desktop/src/contrib/plugin.ts:29`, `apps/desktop/src/contrib/plugin.ts:35`, `apps/desktop/src/contrib/plugin.ts:56` |
| Host state/actions | `host.state.activeSessionId/cwd/gateway/model/profile/viewport`; `request`, `onEvent`, `notify`, `navigate`, `logs`, `status`, `restartGateway` | `host.notifyError`; `gateway` is connection state | `apps/desktop/src/sdk/index.ts:58`, `apps/desktop/src/sdk/index.ts:74`, `apps/desktop/src/sdk/index.ts:82`, `apps/desktop/src/sdk/index.ts:85`, `apps/desktop/src/sdk/index.ts:90`, `apps/desktop/src/sdk/index.ts:95`, `apps/desktop/src/sdk/index.ts:98`, `apps/desktop/src/sdk/index.ts:101` |
| Contribution areas | `COMPOSER_AREAS`, `PALETTE_AREA`, `ROUTES_AREA`, `SIDEBAR_NAV_AREA`, `KEYBINDS_AREA` | `PANES_AREA`, `STATUSBAR_AREAS`, `TITLEBAR_AREAS`, `THEMES_AREA` | `apps/desktop/src/sdk/index.ts:119`, `apps/desktop/src/sdk/index.ts:123`, `apps/desktop/src/sdk/index.ts:124`, `apps/desktop/src/sdk/index.ts:202`, `apps/desktop/src/sdk/index.ts:212`, `apps/desktop/src/sdk/index.ts:224` |
| UI kit | `Button`, `Input`, `Select*`, `Switch`, `Tabs*`, `Dialog*`, `ConfirmDialog`, `DropdownMenu*`, `Popover*`, `Badge`, `SearchField`, `ScrollArea`, `Skeleton`, `EmptyState`, `ErrorState`, `StatusDot`, `LogView`, `Codicon`, `Tip`/`Tooltip` | Also `Checkbox`, `ContextMenu*`, `CopyButton`, `DecodeText`, `GlyphSpinner`, `Kbd`/`KbdGroup`, `Loader`, `SegmentedControl`, `Separator`, `Textarea` | `apps/desktop/src/sdk/index.ts:128`, `apps/desktop/src/sdk/index.ts:129`, `apps/desktop/src/sdk/index.ts:130`, `apps/desktop/src/sdk/index.ts:131`, `apps/desktop/src/sdk/index.ts:132`, `apps/desktop/src/sdk/index.ts:133`, `apps/desktop/src/sdk/index.ts:134`, `apps/desktop/src/sdk/index.ts:141`, `apps/desktop/src/sdk/index.ts:143`, `apps/desktop/src/sdk/index.ts:152`, `apps/desktop/src/sdk/index.ts:159`, `apps/desktop/src/sdk/index.ts:166`, `apps/desktop/src/sdk/index.ts:178` |
| React/data | `useI18n`, `queryClient` | `useQuery`, `useMutation`, `useQueryClient`; `atom`, `computed`; `Contribute`; `useValue` | `apps/desktop/src/sdk/index.ts:195`, `apps/desktop/src/sdk/index.ts:198`, `apps/desktop/src/sdk/index.ts:210`, `apps/desktop/src/sdk/index.ts:227`, `apps/desktop/src/sdk/index.ts:231`, `apps/desktop/src/sdk/index.ts:234` |
| Helpers | `cn`, `haptic`, `icons`, `profileColor` | `profileColorSoft`; `coarseElapsed`, `fmtDateTime`, `fmtDayTime`, `relativeTime`; `evaluateRuntimeReadiness` | `apps/desktop/src/sdk/index.ts:199`, `apps/desktop/src/sdk/index.ts:201`, `apps/desktop/src/sdk/index.ts:206`, `apps/desktop/src/sdk/index.ts:219`, `apps/desktop/src/sdk/index.ts:222`, `apps/desktop/src/sdk/index.ts:223` |
| Public types | Contribution, plugin, event, status, and area-specific types are re-exported alongside runtime values | `RpcEvent`, `StatusResponse`, `RuntimeReadinessResult`, `LoaderType`, `ContributeProps` | `apps/desktop/src/sdk/index.ts:179`, `apps/desktop/src/sdk/index.ts:180`, `apps/desktop/src/sdk/index.ts:195`, `apps/desktop/src/sdk/index.ts:219`, `apps/desktop/src/sdk/index.ts:225` |

`types/hermes-plugin-sdk.d.ts` contains the same exported names as this installed barrel, so no name correction was warranted.
