# Hermes-native alignment review

*2026-07-17/18. Four-agent survey of the hermes-agent codebase + both docs
trees (11 files in `docs/`, ~330 under `website/docs/`), triggered by Tom
pointing at `cron/scripts/classify_items.py`. Conformance audit of what we
shipped, and the native target architecture for what remains.*

## The headline discovery

Hermes already ships the *pattern* we've been building: the
**poll → classify(cheap aux model) → surface-only-above-threshold → silent
otherwise** pipeline. `cron/scripts/classify_items.py` is the reference
classifier (`call_llm(task="monitor")`, temp 0, one batched call,
prompt-instructed JSON with defensive parsing, empty-stdout = suppressed
delivery, non-zero exit = alert). The docs' "important-mail monitor"
suggested-job is literally our G1/G2 shape. And `config.yaml` has **eleven
named auxiliary task slots — including `monitor` and `curator`** — for
routing background calls to cheap models, with plugin-registered tasks
layering defaults under user config.

## Conformance audit of shipped work

**Conforms** (no action):
- Plugin shape: `plugin.yaml` + `register(ctx)` + FastAPI router mounted at
  `/api/plugins/town-feed` with inherited dashboard auth — matches the
  kanban house pattern exactly.
- `ctx.llm` for in-plugin LLM calls — `plugin-llm-access.md` names it "the
  supported way for a plugin"; our trust-gated model-override degrade is
  the documented posture.
- Kanban writes through the shared `kanban_db` module — the "one DB module
  across surfaces" rule.
- JSON state file with flock + atomic tmp-fsync-replace writes — the
  sanctioned lightweight pattern (first-party plugins do the same).
- Write envelopes, audit log, kill switches — consistent with the security
  posture docs.
- Desktop suite — separate (JS) contract, independently conformant; the
  SKILL.md explicitly disclaims the Python surface.

**Diverges** (ranked):

| # | Divergence | House rule | Fix |
|---|---|---|---|
| D1 | Bespoke daemon-thread schedulers (30m curate, 10m inbox) | "Prefer not to own a loop" — use the cron engine; providers/CAS/claims handle multi-process | G6R migration (below) |
| D2 | Scanners live inside the plugin, feeding a custom `inbox_candidates` lane with custom watermarks | Native pipeline = `no_agent` cron scripts writing to cron output dirs; job chaining reads "latest output .md"; **our curator already scans cron output dirs** | Rebuild scanners as cron scripts; retire `inbox_candidates` |
| D3 | `TOWN_*` env vars for non-secret config (label model, intervals, kill switches) | AGENTS.md: "No new env vars for non-secret config — settings belong in config.yaml"; model routing belongs in `auxiliary.<task>.*` | Move to `plugins.entries.town-feed.*` + auxiliary task blocks; keep env only as emergency override |
| D4 | Labeler classification via `ctx.llm.complete_structured` inside the plugin thread | Scripts use `call_llm(task=...)` so the model is user-configurable per task in config.yaml with shared retry/402-fallback | When the labeler moves into the scan script (D2), switch lanes; `task="monitor"`-style with plugin-registered task name |
| D5 | G6 plan auto-creates cron jobs at install | "Installing never silently creates a recurring job" — blueprint schedules route through the /suggestions acceptance flow | G6R uses `register_blueprint_suggestion`; acceptance is explicit |
| D6 | No use of delivery/silence conventions | `[SILENT]` / empty-stdout / `deliver` targets are the native surface for "ping me only when it matters" | Free win in G6R: scanners can optionally deliver urgent finds natively |

## Native target architecture (G6R — replaces G6, reshapes G3/G4)

```
no_agent cron scripts (HERMES_HOME/scripts, blueprint-packaged skills)
  town-gmail-scan.py    every 10m  — fetch both accounts (own tokens; env is
                                     sanitized, Google tokens are ours) →
                                     classify via call_llm(task=town_inbox) →
                                     apply Town/ labels (envelope unchanged) →
                                     structured JSON to stdout → cron output
  town-calendar-scan.py every 30m  — scope-aware events → cron output
  town-telegram-scan.py every 10m  — thread digests → cron output
        │ (cron output dirs — native retention, atomic writes, profile-scoped)
        ▼
town-feed plugin (unchanged surfaces, thinner internals)
  curator: cron-triggered via POST /curate-full (stashed ctx.llm, purpose=),
  consumes scanner outputs through its EXISTING cron-output scanning;
  feed-state.json stays (product state: dismissals, decisions, suggestions);
  REST/dashboard/kanban bridge unchanged
        │
        ▼ (future) agent-mode cron with context_from: [scanner job ids] +
          enabled_toolsets — memory writes, People-lite, weekly review
```

Also inherited for free: per-profile correctness (`get_cron_output_dir`),
at-most-once firing (store CAS), Chronos scale-to-zero compatibility,
`/runs` + `/trigger` in our Routines UI, `last_delivery_error` tracking.

Conventions adopted in the scripts: `main() -> int` exit-code ladder
(2=input, 3=import, 4=LLM — non-silent failures alert), `_eprint` stderr,
stdin/stdout contracts, temperature 0 + fence-tolerant JSON parsing,
empty-stdout silence.

## What deliberately stays custom (justified)

- **feed-state.json** — dismissal/promotion/suggestion state is *product*
  state with REST semantics, not job output; JSON-under-plugin-dir is a
  sanctioned pattern. (Sqlite migration only if it grows queries.)
- **Curator in the plugin** — it needs the state file, REST, and kanban
  bridge; cron only *triggers* it (native "provider controls trigger,
  never execution" philosophy).
- **Desktop plugins** — separate contract, already conformant.
- **`ctx.llm` for the curator** — the documented plugin lane; add
  `purpose=` strings per the audit recommendation.

## Migration order (pending Tom's review)

1. **G6R-a**: gmail scan+label → cron script (absorbs G2's logic; plugin
   labeler thread retired); auxiliary task config; config.yaml settings
   migration.
2. **G6R-b**: curator trigger → cron job on `/curate-full`; retire both
   threads; `inbox_candidates` → cron-output consumption.
3. **G3/G4 rebuilt to spec as scripts** (calendar, telegram) — smaller than
   their original specs.
4. **Blueprint packaging** with suggestion-flow scheduling.
5. G5 unchanged (delegation pattern), People-lite as chained agent cron.
