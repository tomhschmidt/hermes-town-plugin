# G6R — Hermes-native restructure (master build plan)

*Approved-in-principle 2026-07-18 pending Tom's review of this document.
Supersedes the old G6 spec (deleted; see git history) and reshapes G3/G4.
Motivation and conformance audit: `docs/hermes-native-alignment.md`.*

## Design rule (settled after the blueprint discussion)

**Scripts for the sensing layer, agents for the judgment layer** — the same
split Hermes core makes. `no_agent` cron scripts do exact-cadence mechanics
with bounded, batched classification calls (`classify_items.py` precedent;
~300 runs/day can't be agent sessions; deterministic label writes; no
session exhaust polluting the curator). Agent-mode crons with
`context_from` + scoped toolsets are reserved for downstream judgment
products: memory writes, People-lite, weekly reviews, G5 drafts. The
script-fed-agent variant remains a documented config-level pivot per job if
per-email agent nuance is ever wanted (same fetch scripts, `no_agent`
flipped, prompt added).

## Components

### A. `~/.hermes/scripts/town-gmail-scan.py` (every 10m, `no_agent`)
G1's scan + G2's labeler moved out of the plugin, logic near-verbatim:
- Token discovery/refresh/account-dedup, watermarks (script-local state
  JSON beside nothing — under `~/.hermes/town-feed/` — NOT in the repo's
  plugin dir), full-body fetch, known-correspondent cache, starred skip,
  awaiting-reply lifecycle, Town/ label writes (envelope unchanged:
  labels.create + modify addLabelIds, removeLabelIds only for
  awaiting-reply), audit entries.
- Classification: ONE `call_llm(task="town_inbox", temperature=0,
  max_tokens=...)` per cycle — `agent.auxiliary_client` import, lazily,
  inside main() (classify_items conventions). Prompt-instructed JSON,
  fence-tolerant defensive parsing. Model configured via
  `auxiliary.town_inbox.{provider,model}` in config.yaml (default auto;
  Tom's intended value: a cheap cloud model). VERIFIED: the plugin
  registers the task via `ctx.register_auxiliary_task("town_inbox",
  display_name=..., description=..., defaults=...)` (plugins.py:1047) —
  gains model-picker visibility + defaults; arbitrary `auxiliary.<name>`
  config also works unregistered. Names `town_inbox`/`town_curator` are
  safe (builtin-reserved list excludes them; note builtin `curator` IS
  reserved). Scripts run with sanitized env (no provider keys, no
  TELEGRAM_BOT_TOKEN) — call_llm reads credentials from disk config, and
  the telegram script re-reads its token from ~/.hermes/.env (existing
  pattern).
- Output contract: structured JSON to stdout `{candidates: [...],
  labeling: {applied, skipped, errors}, watermarks_advanced: bool}` →
  saved by cron to its output dir. EMPTY stdout when no new mail (silent
  success). Exit ladder: 0 ok, 2 input/config, 3 import, 4 LLM, 5 Gmail
  API — non-zero alerts via cron error delivery.
- Delivery per component G (telegram alerts on failure, wake-gated
  silence on success).
- Scripts open with the sf-sublet-monitor re-exec shim (self re-exec into
  the venv python if invoked manually with another interpreter) — cron
  already uses the venv python (verified: editable-install .pth makes
  `agent.auxiliary_client` importable from any cwd; empirically tested).

### B. `~/.hermes/scripts/town-calendar-scan.py` (every 30m, `no_agent`)
Replaces the old G3 plugin-scanner spec:
- Per-token scope detection accepting `calendar` OR `calendar.readonly`
  (lights up per account as re-auths land; `secondary` currently lacks it).
- Primary calendar, next 48h, singleEvents, cap 20/account. Candidates
  (headline "<summary> — <relative start>", source
  `calendar:<account>:<event_id>`, created_at=start, hash on
  (account,id,start)) + a `next_event` object, all in the stdout JSON.
- No writes of any kind. Same exit ladder; empty stdout when scope absent
  everywhere or no events.

### C. `~/.hermes/scripts/town-telegram-scan.py` (every 10m, `no_agent`)
Replaces the old G4 spec:
- Also in this phase: migrate `telegram-action-items-v2.py`'s summary
  call OFF the direct Ollama daemon (:11434) onto
  `call_llm(task="monitor")` — Hermes-routed, config.yaml-tunable — with
  the daily cron's observable behavior unchanged.
- Reuse `telegram-action-items-v2.py`'s collection (import if a minimal
  refactor keeps the daily cron byte-identical; else mirror minimally with
  a source-of-truth comment). Last-48h messages grouped per thread; one
  candidate per active thread (cap 15), source `telegram:<chat_id>`,
  watermarks per chat in script-local state; agent-exhaust threads
  excluded. Stdout JSON; empty when quiet.

### D. Curator (plugin) — thinner, cron-triggered by direct import
- DELETE: both daemon threads, `inbox_candidates` lane + its normalizers,
  the labeler flock, all `TOWN_*` env parsing.
- `_scan_cron` extension: when a cron output file is valid town-scan JSON
  (recognized by structure), ingest its `candidates` directly (sources
  preserved) instead of headline-izing the raw text; `next_event` from the
  calendar scan flows to `/feed`.
- Trigger: `town-feed-curate` cron job (`no_agent`, every 30m) running
  `~/.hermes/scripts/town-curate.py`, which imports the installed plugin's
  `plugin_api` module (same `importlib` pattern `__init__.py` uses) and
  calls `run_curator(llm=<adapter>)` IN ITS OWN PROCESS — the feed-state
  flock was designed for exactly this cross-process case. No HTTP, no
  token plumbing, no new endpoint. (`context_from` was evaluated and
  rejected for this: it is context injection into agent prompts, not a
  completion trigger; Hermes has no on-completion chaining primitive.)
- LLM lane: the adapter wraps `call_llm(task="town_curator")` exposing the
  `complete_structured` surface `run_curator` expects — model/provider
  configured via `auxiliary.town_curator.*` in config.yaml (resolves audit
  divergence D4 for the curator as well). The in-plugin `register()` tool
  keeps working via `ctx.llm` for agent-invoked curation; `POST /curate`
  stays heuristic for the UI button.
- The `town_feed_curate` tool remains (agent-invocable); scheduler env
  vars retired.

### E. Config migration
`config.yaml`:
```yaml
auxiliary:
  town_inbox:   {provider: "", model: ""}  # empty = auto (main model)
  town_curator: {provider: "", model: ""}
plugins:
  entries:
    town-feed:
      labeling_enabled: true          # kill switch (was env)
      label_confidence_min: 0.6
      taxonomy: {}                    # optional Town/ label overrides
```
Env vars removed from code. Emergency stops: cron `pause` (native) +
`labeling_enabled`.

### F. Distribution & scheduling opt-in (REVISED after adversarial verify)
TO BE CLEAR: all four jobs are ordinary Hermes-managed cron entries
(created through Hermes's own API, visible/pausable in `hermes cron`,
the desktop Cron pane, and our Routines card, with native run history and
claims). Scripts in `~/.hermes/scripts/` is the scheduler's REQUIRED
location, same as Tom's existing four cron scripts. This section only
concerns how the jobs get CREATED at install time for third parties.
Blueprint frontmatter CANNOT carry `script:` — `BlueprintSpec` has no such
field and `create_job(no_agent=True, script=None)` raises (verified
blueprints.py:57-141, jobs.py:1167-1171). So:
- The four jobs are created DIRECTLY via `cron.manage`/CLI on Tom's
  machine (with his confirmation), scripts installed to
  `~/.hermes/scripts/`.
- For distribution: package as ordinary SKILLS carrying the scripts +
  install docs; on install, register scheduling opt-ins programmatically
  via `cron.suggestions.add_suggestion(source="integration",
  job_spec={..script, no_agent, schedule..}, dedup_key=...)` — verified:
  acceptance passes the stored job_spec straight to `create_job`, which
  DOES accept script+no_agent; suggestions never auto-create (MAX_PENDING
  cap 5 noted).
- File an upstream issue proposing `script:`/`workdir:` in blueprint
  frontmatter (would make script blueprints first-class; our #66387
  precedent).

### G. Failure visibility & delivery (NEW after adversarial verify)
`deliver: local` makes script failures INVISIBLE outside run history —
the error alert is built then discarded for local (scheduler.py:2729-3803
path verified). Design instead:
- Jobs use `deliver: telegram` (Tom's home chat) so non-zero exits deliver
  the one-line failure summary — real alerting.
- Success cycles must NOT spam: scripts append the native wake-gate line
  `{"wakeAgent": false}` as the last stdout line, which suppresses
  delivery while (build-time verification item) the output is still saved
  to the cron output dir for the curator. If the gate suppresses saving
  too, fall back to `deliver: local` + a tiny `town-cron-health` check in
  the curator that surfaces failed `last_status` jobs into NTK (the
  curator already scans cron state).

### H. Google token ownership (codified after auth-layer hunt)
Hermes-managed scripts read token FILES on disk — env sanitization strips
env vars only, so no auth plumbing is needed (parcel-gmail-tracker is the
months-in-production proof of this exact pattern). Refresh-token
revocation (the one residual failure mode; re-auth happens via the
google-workspace skill) surfaces through exit-code 5 → component G's
telegram alert instead of a silently dead scanner.
The google-workspace SKILL is the sole owner/writer of token files (it
created `~/.hermes/google_token.json`; the `google-accounts/*/` layout is
OUR convention, read by town-feed + parcel only). Scripts refresh
credentials IN MEMORY ONLY and never write tokens back (the shipped
G2-corrective google_scan.py already behaves this way — carry the
comment forward). No shared helper module exists upstream; our scripts
copy the proven `_gmail_token_paths()` + in-memory-refresh idiom.

## What changes for shipped surfaces
Desktop suite: zero changes. Routines card: gains the four town jobs
(visible, pausable, Run Now via T16). feed-state.json: keeps product state
(items, decisions, suggestions, label audit log moves to script state);
`inbox_candidates` and gmail watermark keys are dropped by a v3 migration
that preserves everything else. gateway/town-feed/README.md: rewritten at
execution (scheduler section replaced by the cron/blueprint story).

## Execution order (Codex-delegated, review-gated per phase)
1. **G6R-a**: gmail script + aux task + config migration + cron job;
   plugin labeler/inbox thread retired same commit (no dual-writer
   window); live-verify a labeling cycle end-to-end.
2. **G6R-b**: `town-curate.py` direct-import trigger job + aux-task LLM
   adapter; curate thread retired; `_scan_cron` JSON ingestion + state v3;
   live-verify curation.
3. **G6R-c**: calendar + telegram scripts + jobs; verify candidates and
   next_event greeting.
4. **G6R-d**: blueprint packaging + README rewrite + docs sweep.

## Verification & rollback
Per phase: py_compile + `npm run check`/`build` when TSX touched; deploy;
both-instance restart discipline; watch cron run history + state file +
Gmail labels + dashboard. Rollback: cron jobs are pausable/deletable in
one command; the retired threads exist in git history; feed-state v3
migration is additive-only so v2 readers keep working during a rollback.
