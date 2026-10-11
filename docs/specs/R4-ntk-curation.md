# R4 — Need to Know: consume every scan, one item per source, no junk

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`. Creates the
shared `operator_profile.py` (minimal fields; S1 extends it).*

## Problem (observed in production, anonymized)

- **Lost updates.** `_scan_cron` reads only the NEWEST output doc per cron
  job (`_latest_cron_output`), but the Gmail and Telegram scanners are
  watermarked and run every 10 min while the curator runs every 30 min.
  Candidates in the two older docs are never seen: over ~10 h, 15 of 23
  non-empty Telegram scans were never curated (a DM the morning briefing
  flagged never reached Need to Know).
- **Stale and duplicate items.** Items are keyed by content hash
  (per-message for Telegram, per-summary for sessions) and retained 14
  days, so a DM where the other person replied "That works!" still shows
  the older "3 days silent" item (which also drives a "Nudge …"
  suggestion); one session yields three items, one of them the stub
  "Operation interrupted: waiting for model response (9.6s elapsed)"; one
  group chat yields two items.
- **Junk outranks real items.** Candidates the LLM declines only lose 20
  points, so two birthdays and a working-location "Home — tomorrow" take 3
  of the 10 visible slots and push a real follow-up to #11.
- **Self-exhaust.** The #1 item (score 90) is the operator's own Morning
  Briefing email (sent by their assistant address) re-summarized. Of 62
  tracked "awaiting reply" threads, 37 are junk: 15 unsubscribe mailtos,
  14 self-addressed briefings, 8 FYI forwards — feeding "Nudge or archive
  three stale threads (73–83 days)" suggestions. Awaiting-reply and
  open-loop signals are sorted OLDEST first, so month-old noise wins.
- **Dismissed candidates** still feed suggestions' fallback and the day
  brief's top headlines.

## Scope

`gateway/town-feed/dashboard/plugin_api.py` (`_scan_cron`,
`_scan_sessions`, `run_curator`, `_llm_rank` call sites, signal sorting),
`gateway/town-feed/dashboard/google_scan.py` (candidate + awaiting-reply
filtering), new `gateway/town-feed/dashboard/operator_profile.py`, tests.
Not in scope: Telegram addressing (R1b), calendar candidate policy (R3),
routes/desktop (R5), bootstrap headers (R2).

## Hard requirements

**Operator profile (new module, per `GROUNDING.md`)**
1. `load_operator_profile()` returns at least `name`, `preferred_name`,
   `home_timezone`, `self_addresses` (lowercased; always includes the
   connected Google account addresses passed in by callers that know them),
   `assistant_senders` (lowercased). Missing file → defaults; malformed →
   one stderr line + defaults. Pure, never raises, no network.

**Consume every scan**
2. For candidate extraction, `_scan_cron` reads every output doc of a job
   newer than that job's consumption watermark (`source_state.cron_consumed
   [job_id]` = newest consumed doc mtime_ns), oldest→newest, capped at the
   12 newest docs and 6 h of age when no watermark exists. Snapshot fields
   (`awaiting_reply`, `open_loops`, `known_contacts`, `next_event`,
   `events`/schedule) still come from the newest doc that contains them.
3. The watermark is persisted inside the existing locked merge, only when
   the curation result is written; a failed curation re-reads the same
   docs next time. State stays backward-compatible (old files load; the
   new key is optional).

**One live item per source**
4. Identity keys: `telegram:<chat_id>`, `session:<session_id>`,
   `gmail:<account>:<thread_id>` (fallback message id), `calendar:<account>:
   <event_id>`, others by `content_hash`. Across fresh candidates AND
   retained prior items, only the newest (by source activity time) per
   identity survives; older ones are dropped, not shown. A newer candidate
   for an identity whose older item was dismissed is shown (new activity);
   the dismissal decision for the old hash remains.
5. Sessions: skip interrupt/error stub summaries (e.g. starting
   "Operation interrupted", "Error:", "Request timed out") — fall back to
   the previous substantive assistant message, else no item.

**Junk out**
6. In LLM ranking mode, only candidates the LLM returned are visible in
   `/feed`: candidates sent but not returned (declined) and candidates
   beyond `MAX_CANDIDATES_FOR_LLM` (never vetted) are dropped from items.
   *(Amended 2026-10-11 after the first live run: 51 candidates, the LLM
   kept 4 of 40, and the original "beyond the cap keeps heuristic
   treatment" rule filled 6 of 10 slots with unvetted items.)* Retention
   also drops legacy telegram GROUP items that predate R1b's
   `addressed_to_user`, and gmail items whose persisted `from_address` is
   an assistant sender. Heuristic/fallback mode is unchanged.
7. At most 3 `calendar:` items appear in the served top `MAX_FEED_ITEMS`
   (in both modes); the rest are not served. Implement at serve time in
   `get_feed` (a small pure `_select_served(visible)` helper), not by
   deleting items from state.
8. Gmail candidates whose sender is in `assistant_senders`, or whose sender
   AND every recipient are self addresses, are not emitted as candidates
   (scanner side, `google_scan.py`) and never get labels.
9. Awaiting-reply tracking (outbound latest message): do not ADD the
   awaiting-reply label or a tracked entry when every recipient is a self
   address, the recipient looks like an unsubscribe/list address (local
   part or address containing `unsubscribe`, or a `mailto:` from
   List-Unsubscribe), or the subject starts with `Fwd:`/`Fw:`. Existing
   Gmail labels are NEVER removed by this change (no cleanup, no backfill);
   existing junk entries are filtered only at READ time (signals), not
   mutated in Gmail.
10. Signals: awaiting-reply and open-loop lists are filtered by the same
    junk rules, drop `bulk_sender` open loops, restrict to the last 21
    days, and sort NEWEST first before capping
    (`MAX_AWAITING_REPLY_SIGNALS`, `MAX_OPEN_LOOP_SIGNALS`).
11. Dismissed candidates are excluded from suggestion inputs AND from the
    day brief's `top_headlines`.

## Required tests (`gateway/tests/test_r4_curation.py`)

Stub `cron.jobs` (`get_cron_output_dir`, `list_jobs`) via `sys.modules`
with a temp output dir of synthetic docs (use the real wrapped format:
`# Cron Job: …\n---\n<json>`); stub the LLM with an object exposing
`complete_structured` returning chosen rows; feed-state in a temp dir.
- Three scanner docs since the watermark (each with a different telegram
  candidate) → all three candidates reach ranking; second run with no new
  docs re-reads nothing; watermark persists; failed write → re-read.
- Old state file without `cron_consumed` loads and curates.
- Same `telegram:<id>` with an older retained item and a newer candidate →
  only the newer is served. Session with 3 summaries → 1 item; interrupt
  stub skipped.
- LLM returns 2 of 5 sent keys → only those 2 served (LLM mode); in
  heuristic mode all 5 eligible.
- 6 calendar candidates scoring highest → only 3 served.
- Gmail: assistant-sender message → no candidate; self→self → no
  candidate; outbound to `unsubscribe@list.example.com` / `Fwd:` /
  self-only → no awaiting-reply label call and no tracked entry
  (assert the label-writer seam is not called); normal outbound → label
  added as today.
- Signals: mixed old/new/junk awaiting entries → newest-first, ≤21 days,
  junk excluded.
- Dismissed candidate absent from `top_headlines` passed to the day brief
  generator and from suggestion candidates.
- `operator_profile`: missing file, malformed JSON, USER.md fallback
  fields, lowercase normalization.
