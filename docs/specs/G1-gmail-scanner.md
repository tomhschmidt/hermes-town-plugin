# G1 — Gmail ambient scanner (both accounts, 10-minute cycle)

> **AS-BUILT RECORD.** This spec describes the shipped in-plugin
> implementation (live and verified 2026-07-17/18). The scheduling and
> scanner architecture it describes is being migrated to Hermes-native
> cron scripts by `G6R-native-restructure.md` (phases a/b); the behavioral
> decisions (taxonomy, envelopes, lifecycles, thresholds) carry over
> unchanged and this file remains their reference.

## Goal

Give the curator standing Gmail context (Town-style Need-to-Know material)
without an agent in the loop: a fast inbox scan every 10 minutes feeding
candidates into the existing curate/rank pipeline.

## Ground truth to follow

`~/.hermes/plugins/parcel-gmail-tracker/parcel_gmail_tracker.py` is the
proven reference for EVERYTHING auth/fetch related: authorized-user token
discovery (`~/.hermes/google_token.json` + `~/.hermes/google-accounts/*/
google_token.json`), refresh handling, and Gmail REST calls from gateway/
cron context. Follow its patterns; do not invent a second auth stack. The
two live accounts are `tom` and `secondary`; the root token duplicates one
of them — dedup by account email (fetch via Gmail profile or token
metadata) so the same inbox isn't scanned twice.

## Behavior

1. New module `gateway/town-feed/dashboard/google_scan.py` (keep
   plugin_api.py from ballooning; import from it):
   - `scan_gmail(now) -> (candidates, errors)`: for each account, query
     messages newer than a per-account watermark (Gmail `internalDate`),
     query `in:inbox -in:chats newer_than:2d`, cap ~25 messages/account/
     cycle. Extract: from, subject, date, snippet (first ~300 chars of
     body text), message id, thread id, unread/important flags.
   - Candidates in the curator's shape: headline `"<From name>: <subject>"`,
     summary = snippet, `source = "gmail:<account>:<message_id>"`,
     created_at from internalDate, content hash on (account, thread id,
     subject) so follow-ups in one thread dedup to the freshest message.
   - Skip: messages the curator itself has already surfaced (existing
     hash dedup covers this), anything matching `_references_curator`.
2. **10-minute inbox thread**: a second daemon thread in
   `gateway/town-feed/__init__.py` (pattern-copy the curator scheduler;
   same guard style, `TOWN_FEED_INBOX_INTERVAL_SECONDS` env, min 300,
   `TOWN_FEED_DISABLE_INBOX=1` kill switch). Each tick runs the gmail scan
   (and G2 labeling when it lands) and persists fresh candidates into a new
   `inbox_candidates` list in the state file (locked read-modify-write,
   capped 100, pruned by the same 14-day window). It does NOT run the LLM.
3. `run_curator` consumes `inbox_candidates` alongside its own scanners
   (sessions/cron/memory) every 30 minutes — same dedup/heuristic/LLM path.
   `_heuristic_score` gains a small bump for unread+important mail.
4. Watermarks live in `source_state.gmail` per account (like
   `memory_hashes`); backward-compatible state loading as always.
5. Desktop (small): NTK items whose source is `gmail:<account>:<id>` render
   the account as part of the source line and the headline click opens
   `https://mail.google.com/mail/#inbox/<message_id>` in the system browser
   — investigate the right SDK affordance for external links (check
   `types/hermes-plugin-sdk.d.ts` / sdk-surface for an opener; if none
   exists, `window.open` in the renderer is acceptable if it works in
   Electron here — verify, do not assume; if neither works, render the
   account badge and skip the link, reporting so).

## In scope

`gateway/town-feed/dashboard/google_scan.py` (new),
`gateway/town-feed/dashboard/plugin_api.py`, `gateway/town-feed/__init__.py`,
`src/town-feed/plugin.tsx`, `src/town-home/plugin.tsx` (source-line render
only).

## Out of scope

Labeling (G2), calendar (G3), Telegram (G4), any change to curate cadence.

## Acceptance checks

- With both tokens present: one cycle populates `inbox_candidates` from
  both accounts, deduped, watermarked (second run adds only new mail).
- A revoked/expired token degrades to an error entry in
  `last_curate_errors`-style reporting, never a crash.
- After the next LLM curate, real emails appear in NTK ranked against other
  sources; gmail-sourced items show the account and open Gmail on click
  (or the documented fallback).
- Gates: unpiped `npm run check`, `npm run build`,
  `python3 -m py_compile` on all three Python files.
