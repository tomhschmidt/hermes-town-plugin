# T17 — Need to Know: presentation parity + backlog hygiene

## Goal

Town's NTK is a compact stack of 2–3 high-signal items with freshness and
source links. Ours is a 1-of-30 carousel. Fix presentation (desktop) and
backlog quality (gateway).

## Current state

- Desktop: `src/town-feed/plugin.tsx` (`NeedToKnowPane`, full pane) and
  `src/town-home/plugin.tsx` (`NeedToKnowCard`, /town card) both consume the
  town-feed bridge (`useTownFeedBridge`). Items carry headline, summary,
  source (`session:<id>` / cron / memory refs), score, timestamps; actions
  Dismiss + "Turn into task" (promote) exist.
- Gateway: `gateway/town-feed/dashboard/plugin_api.py` — `run_curator`
  (heuristic + `_llm_rank`), `GET /feed`, `POST /dismiss`,
  `POST /promote-to-task`, `POST /curate` (heuristic-only by design — leave
  that), scheduler in `__init__.py` every 30m.

## Gateway changes

1. **Cap + expiry.** After ranking, keep at most 10 live (non-dismissed)
   items; items older than 14 days (by their source-activity timestamp) are
   expired (removed from `items`; their `decisions` entries follow the
   existing `_prune_decisions` behavior). Dismissal/promotion state for
   still-live items is preserved. Old `feed-state.json` files must load
   unchanged (backfill missing fields defensively).
2. **Curator prompt iteration.** In `_llm_rank`'s prompt: require headlines
   that lead with the concrete entity ("Kasa smart plug setup — unresolved"
   → entity first), call out deadlines/dates explicitly when present, and
   forbid generic scheduler/system phrasing (extend the existing
   self-exclusion rules). Summaries ≤ 2 sentences, concrete, no hedging.
3. **Freshness metadata.** `/feed` response gains `curated_at` (ISO, when the
   curator last completed) if not already exposed; keep the shape otherwise
   backward-compatible (`normalizeTownFeedState` consumers keep working).

## Desktop changes

4. **/town card (`NeedToKnowCard`):** replace the 1-of-N carousel with a
   stack of the top 3 items: headline (medium weight), 1-line summary
   (muted, clamped), relative source age, per-item Dismiss + "Turn into
   task" (existing actions), and — when the item's source is a session ref —
   the headline is clickable → `host.navigate` to that session (same route
   pattern the session rows already use). Footer: "N more in Need to Know"
   → navigates to the town-feed pane route if N > 3.
5. **Card header:** "NEED TO KNOW · 37 min ago" using `curated_at` (relative,
   omit when unknown) + a refresh icon `Button` that calls a new bridge
   method `curate()` → `POST /curate`, disabled while running, then
   refreshes the feed. Note POST /curate is heuristic-only (documented
   design); the toast on completion should say "Refreshed (heuristic pass)".
6. **Pane (`NeedToKnowPane`):** keep the full list but adopt the same item
   presentation (clickable session sources, freshness header + refresh).
   Carousel chrome (Prev/Next/"1 of 30") goes away in both places.

## In scope

`src/town-feed/plugin.tsx`, `src/town-home/plugin.tsx`,
`gateway/town-feed/dashboard/plugin_api.py`, `gateway/town-feed/__init__.py`
(only if the scheduler needs to pass anything new).

## Out of scope

Suggestions (T18), promote/dismiss semantics, scheduler cadence, the
`POST /curate` heuristic-only design.

## Acceptance checks

- Fresh state file: after one curator run, ≤10 live items, none older than
  14 days; an old-format state file loads without error and gets pruned on
  the next run.
- /town NTK card shows a 3-item stack with freshness header; refresh button
  triggers a heuristic curate and updates `curated_at`; session-sourced
  headlines navigate to the session; Dismiss removes the item and the next
  ranked item fills the slot.
- `npm run check` + `npm run build` + gateway py_compile pass.
