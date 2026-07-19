# U1 — Forgiving Dismiss (optimistic removal + inline Undo)

*2026-07-18, polish-loop iteration 1. Town pattern: dismissing a feed item
removes it instantly and offers a short-lived Undo affordance. Ours is
instant but irreversible. Conventions: docs/specs/README.md binds (theme
vars only, imperative handler state reads, 150ms transitions, SDK
components). The SDK has NO toast API (`host.notifyError` only) — the Undo
affordance is an inline strip, not a toast.*

## Gateway — `POST /undismiss` (gateway/town-feed/dashboard/plugin_api.py)

Exact inverse of the existing `/dismiss` route (line ~1101), same
`ItemRequest` payload `{id}`:

- Under `_process_lock()`: read state; find the item by id in
  `state["items"]`.
- Always clear the decision: `decisions.setdefault(id, {})` with
  `dismissed = False`, `updated_at` = now ISO, and `source_activity_at`
  from the item when present (mirror the dismiss route). Run
  `_prune_decisions` and write state.
- If the item is present: set `item["dismissed"] = False`, respond
  `{"ok": True, "id": id, "dismissed": False, "restored": True}`.
- If the item is absent (a curate evicted it after dismissal): do NOT 404 —
  clearing the decision flag is still meaningful (the item returns on the
  next curate). Respond with `"restored": False`.

## Bridge — `undismiss` (src/town-feed/plugin.tsx)

- Add `undismiss(item: FeedItem): Promise<void>` to the window-bridge type
  and the exported bridge object, mirroring `dismiss` (line ~611):
  - Optimistically remove the id from `dismissedIds`, persist via
    `writeIds(DISMISSED_IDS_KEY, ...)`.
  - `POST /undismiss {id}` via `context().rest`.
  - On failure: re-add the id to `dismissedIds`, persist, and
    `host.notifyError(error, 'Could not restore Need-to-Know item')`.
  - On success: trigger an IMMEDIATE feed refetch (reuse the existing
    fetch/refresh path) — after a dismissal the next poll drops the item
    from `sourceItems`, so without a refetch an undo that lands after a
    poll would not visually restore until the next cycle.

## UI — inline "Dismissed · Undo" strip (both NTK surfaces)

Surfaces: the NTK card in src/town-home/plugin.tsx and the feed page in
src/town-feed/plugin.tsx. Each keeps its own ephemeral local store (atom)
`recentlyDismissed: Array<{id, headline, expiresAt}>` — NOT persisted.

- On Dismiss click: existing dismissal flow unchanged, plus push
  `{id, headline, expiresAt: now + 6000}` (cap the list at 3, drop
  oldest). A timer removes the entry at expiry; clear timers on unmount.
- Render: for each live entry, a slim strip row at the TOP of the item
  list (before the first item): muted single line
  `Dismissed "<headline, truncated ~48ch>"` + a text-variant SDK `Button`
  "Undo", right-aligned. Muted tertiary text, existing row padding, 150ms
  opacity transition on appear/expire. No layout jump beyond the strip's
  own slim height.
- Undo click (imperative reads only): remove the entry from
  `recentlyDismissed`, then call the bridge `undismiss` (town-home:
  `currentBridge.undismiss?.(item)` — if the bridge or method is absent,
  ignore the click silently; town-feed: call its own `undismiss`
  directly). The town-home card needs the original item object to hand
  back — keep it on the entry (`item` field) rather than re-looking it up.
- The strip must not suppress the live top-3 stack: entries render in
  addition to (not instead of) the normal item rows.

## Addendum (post-review corrective) — overlay reconciliation

Found during live verification: `refreshFeed` never reconciles the
persisted `dismissedIds` overlay against server truth. A gateway-side
restore (POST /undismiss from curl, another device, or a future surface)
stays hidden locally FOREVER, and the storage set grows unboundedly.

Rule (src/town-feed/plugin.tsx only): the server's `/feed` EXCLUDES
dismissed items, so any id the server still returns is, by definition,
not dismissed server-side. On each successful `refreshFeed`, set
`dismissedIds = (ids present in fetched items ∩ dismissedIds) ∪
in-flight dismissal ids`, and persist via `writeIds` only when the set
actually changed. Track in-flight dismissals with a module-level
`Set<string>`: add in `dismiss()` before the POST, remove in a
`finally`. The union keeps the optimistic hide alive when a poll lands
between the overlay-add and the POST completing; ids absent from the
server feed are unrenderable and stale, so dropping them is the
storage-bloat fix.

Explicitly do NOT reconcile `promotedIds` the same way — the server
intentionally keeps returning promoted items (the overlay is what hides
them); reconciling would re-show promoted items. Leave promote handling
untouched.

## Out of scope

No changes to promote/turn-into-task, no toast frameworks, no gateway
changes beyond the one route, no schema/state-version bump (the fields
touched already exist).

## Lanes (parallel, disjoint files)

- Lane A: gateway plugin_api.py + src/town-feed/plugin.tsx (route,
  bridge method, feed-page strip).
- Lane B: src/town-home/plugin.tsx (NTK card strip via the bridge
  contract above).

DoD per lane: unpiped `npm run check` passes; `npm run build` passes;
Lane A also `python3 -m py_compile gateway/town-feed/dashboard/plugin_api.py`;
no files changed outside the lane's scope; report any spec assumption that
turned out wrong instead of silently working around it. Do NOT run
`npm run deploy`, do not touch dist/, do not restart anything.
