# G2 — Auto-inbox labeler (live, both accounts)

> **AS-BUILT RECORD.** This spec describes the shipped in-plugin
> implementation (live and verified 2026-07-17/18). The scheduling and
> scanner architecture it describes is being migrated to Hermes-native
> cron scripts by `G6R-native-restructure.md` (phases a/b); the behavioral
> decisions (taxonomy, envelopes, lifecycles, thresholds) carry over
> unchanged and this file remains their reference.

## Goal

Town's Auto-inbox: new mail gets classified and labeled automatically.
LIVE from day one (user decision) — so guardrails are structural, not
procedural.

## Design constraints (non-negotiable)

- **Labels are the ONLY write.** Never archive, delete, mark read, star,
  or move. The Gmail calls used must be exactly: ensure-label-exists
  (`users.labels.create`) and `users.messages.modify` with `addLabelIds`
  only (no `removeLabelIds` except when re-labeling supersedes a previous
  Town label on the same message).
- **Namespace everything** under a `Town/` label prefix so a bad run is
  one bulk filter away from cleanup: `Town/Action`, `Town/Waiting`,
  `Town/FYI`, `Town/Newsletter`, `Town/Receipts`, `Town/Calendar`.
  Default taxonomy lives in one Python constant with descriptions used in
  the classifier prompt; overridable via `TOWN_INBOX_LABELS` env (JSON) —
  the orchestrator will surface the taxonomy to Tom for tuning at review.
- **Kill switch**: `TOWN_FEED_DISABLE_LABELING=1` skips all writes while
  leaving the G1 scan running.
- **Audit trail**: every applied label appends `{ts, account, message_id,
  subject≤80, label, confidence}` to a capped (500) `labeling_log` list in
  the state file. A `label_stats` rollup {account: {label: count}} feeds a
  one-line dashboard mention later (not this spec).

## Behavior

1. Runs inside the G1 10-minute inbox tick, after the scan, on the same
   new-since-watermark messages (so nothing is classified twice).
2. Classification: ONE `ctx.llm.complete_structured` call per cycle
   batching all new messages (cap 25/account) with schema
   `{items: [{message_ref, label, confidence 0-1}]}`. Prompt includes the
   taxonomy with descriptions, from/subject/snippet per message, and the
   instruction to return NO entry for messages that fit no label (skipping
   is first-class; do not force-fit). Apply only entries with
   confidence ≥ 0.6.
3. The thread needs `ctx.llm` — thread the handle from `register(ctx)`
   into the inbox loop the same way the curator scheduler does.
4. Already-Town-labeled messages are never re-classified (watermark makes
   this near-moot; cheap guard anyway).
5. Failure isolation: classifier or API errors log and skip the cycle;
   the G1 scan's candidates still persist regardless.

## In scope

`gateway/town-feed/dashboard/google_scan.py`,
`gateway/town-feed/dashboard/plugin_api.py` (state fields),
`gateway/town-feed/__init__.py` (llm threading).

## Out of scope

Draft replies, scheduling actions, unlabeling sweeps, desktop UI.

## Acceptance checks

- A cycle against real inboxes applies `Town/*` labels visible in Gmail,
  with matching `labeling_log` entries; skipped messages logged as skipped
  in the cycle summary (state), not force-labeled.
- `TOWN_FEED_DISABLE_LABELING=1` stops writes; scan continues.
- No Gmail write calls besides labels.create / messages.modify(addLabelIds)
  appear anywhere in the diff (orchestrator will grep).
- Gates: py_compile all touched Python; `npm run check`/`build` if any TSX
  touched (should be none).

## Town-alignment addendum (from live product + docs read, 2026-07-17)

Post-initial-build corrective requirements, from Town's exported Inbox
Autopilot prompt and https://www.town.com/docs/routines/auto-inbox:

1. **Taxonomy → Town's 8 (keep our `Town/` prefix)**: `Town/New sender`,
   `Town/Awaiting reply`, `Town/Action needed`, `Town/Newsletters`,
   `Town/Promotional`, `Town/Orders`, `Town/Travel`, `Town/Updates`.
   Newsletter-vs-promotional split = opted-in content vs unsolicited
   marketing.
2. **Classify from the full body** (1–2k chars via the existing
   format=full fetch), never the snippet alone.
3. **Known-correspondent skip**: mail from people Tom regularly
   corresponds with is left UNlabeled (it belongs in the inbox).
   Known = a prior meaningful sent email to that address (approximate
   Town's "meaningful" as sent body > ~50 chars; their exclusions —
   unsubscribes, declines, one-word stop replies — are approximated by
   the length threshold). Cache per-address verdicts in state.
4. **`Town/New sender`** = real-person mail from an address failing the
   known-contact check (not bulk/no-reply senders).
5. **Never label starred mail** (STARRED in labelIds). Gmail's auto
   IMPORTANT is ambiguous (not necessarily user-marked) — do not skip on
   IMPORTANT alone; document this divergence in code comment.
6. **Awaiting-reply lifecycle**: when a thread's latest message is
   outbound from Tom → `Town/Awaiting reply`; when a new inbound reply
   arrives in a thread carrying it → REMOVE that label (this is the one
   sanctioned removeLabelIds use beyond same-message supersede).
7. Explicit non-adoptions (decided): no auto-archive ever (outside our
   write envelope; Town defaults it off anyway), no per-email push
   (10-min poll stands, per Tom), no reuse of organic user labels
   (namespace preserved), reply/scheduling drafts deferred to G5.
8. **Cheap-model knob** (agreed 2026-07-17): the classification call takes
   an optional model override (`TOWN_INBOX_LABEL_MODEL` env, default
   unset = host default). Passing it through ctx.llm requires the
   fail-closed trust-gate config in config.yaml
   (`plugins.entries.town-feed.llm.allow_model_override` + allowed_models
   list) — the orchestrator handles the config edit at deploy; the code
   must degrade gracefully (log + default model) if the override is
   rejected by the gate.
