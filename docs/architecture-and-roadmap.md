# Town on Hermes — consolidated architecture & roadmap

*Written 2026-07-17 after Tier 1 (T14–T18), G1, and the Hermes-primitives
deep-dive (blueprints, cron chaining, delegation, LLM lanes). Supersedes the
roadmap portions of `town-gap-analysis.md`; SPEC.md remains the origin
document.*

## The system as built

**Desktop half** (7 runtime plugins, TSX→esbuild, SDK-only imports):
`/town` dashboard — composer (Task = kanban card + session, Chat = session),
Tasks card (kanban via bridge, 48h done auto-hide, checkbox complete,
session click-through), Need-to-Know top-3 stack (freshness, refresh,
dismiss/promote, Gmail/session deep links), Suggestions chips (prefill
composer), humanized Routines (schedule text, descriptions, Run Now via
full-prompt fetch), Briefing placeholder; `/town-feed` full list; approvals
pane + statusbar badge; Town layout preset; debug pane. Cross-plugin rule:
`ctx.rest` is namespace-locked, so all kanban/feed REST flows through the
town-feed window bridge.

**Gateway half** (`gateway/town-feed`, Python, mounted at
`/api/plugins/town-feed`): the curator (sessions/cron/memory scanners +
G1's Gmail scanner feeding a 10-min `inbox_candidates` lane), LLM ranking +
suggestions via `ctx.llm`, state file v2 (locks, caps, 14-day expiry,
watermarks); kanban bridge (idempotent create, dependency-safe complete,
structured session ids); `/cron-prompt/{id}`; G2's labeler (in review).

**Topology facts that bite** (all documented in CLAUDE.md):
two gateway processes share `~/.hermes/plugins` + state (the app's embedded
gateway AND the :9119 LaunchAgent — deploys restart BOTH); the desktop is
also installable remotely via the ops/ tunnel; `hermes update` stops both.

**LLM lanes** (choose per task):
| Lane | Auth/routing | Use for |
|---|---|---|
| `ctx.llm` (PluginLlm→call_llm) | host-owned, trust-gated overrides | curation ranking, labeling (cheap model via gated override) |
| Agent session (`session.create`+`prompt.submit`) | full Hermes | judgment needing memories/tools |
| `call_llm(task=...)` aux-task config | host-routed, config.yaml per task | script-side classification/ranking (house pattern; replaces direct Ollama use) |
| Agent-mode cron + `context_from` + `enabled_toolsets` | full Hermes, scoped | scheduled judgment over scanner output, memory writes (context_from = context injection, NOT a completion trigger) |
| Subagent delegation | inherits parent, scoped | parallel fan-out (per-email drafts) |

**Design principles distilled:**
1. *Extend, don't duplicate* (Hermes's own rule): blueprints over hand-rolled
   schedulers, `context_from` over curl-into-sessions, skills-hub over
   custom distribution.
2. Right lane per job: mechanics = `no_agent` cron; bounded judgment =
   `ctx.llm` structured; open judgment/memory = scoped agent cron; fan-out =
   delegation. (The T12 agent-cron failure was a task-shape problem —
   "must call one tool" — not an indictment of agent crons generally.)
3. Write envelopes, always: Gmail = `Town/`-namespaced labels only; G5 =
   drafts only; anything further = approval-gated. Audit log + kill switch
   per writer.
4. Everything visible: scheduling in the Routines/Blueprints UI, run
   history, dashboard freshness stamps.

## Remaining work, in order

*(Restructured 2026-07-18 by `specs/G6R-native-restructure.md` after the
Hermes-native alignment review — `hermes-native-alignment.md`. Design rule:
scripts for the sensing layer, agents for the judgment layer.)*

**G6R-a** ✅ SHIPPED 2026-07-18 — gmail scan+label → `no_agent` cron script
(`town-gmail-scan.py`), auxiliary-task model config, config.yaml
migration; plugin inbox/labeler thread retired.

**G6R-b** ✅ SHIPPED 2026-07-18 — curator cron-triggered via `town-curate.py` (direct module
import + `auxiliary.town_curator` call_llm adapter — no new endpoint);
curate thread retired; `_scan_cron` ingests scanner JSON; feed-state v3
(drops `inbox_candidates`).

**G6R-c** ✅ SHIPPED 2026-07-18 (incl. wrapped-output parser fix + self-exhaust skip) — calendar + telegram scanners as scripts (see rewritten G3/G4
specs); next_event greeting line; telegram-action-items script migrates
off direct Ollama onto `call_llm(task="monitor")`.

**G6R-d** (in flight) — skills packaging (suggestion-flow scheduling), README
rewrite, docs sweep.

**CHARACTER C1–C7** ✅ SHIPPED 2026-07-28 — context &
character pack (`specs/CHARACTER.md`): C1 event-context plumbing, C2
deterministic `schedule_context`, C3 LLM day brief (header narrative +
callouts + trip badge, hash/date/hourly regeneration gating), C4 telegram
thread bodies + attribution + user_spoke_last, C5 NTK action_hint +
takeaway summaries, C6 day-aware suggestions + grounded first-contact via
gmail `known_contacts`, C7 TOWN_VOICE. Feed-state v6. The day brief is a
second `town_curator`-lane call, ~1/hour steady-state.

**GROUNDING R1a–R5, S1–S2** ✅ SHIPPED 2026-10-11 — reliability + situational
awareness (`specs/GROUNDING.md`): Telegram attribution end to end (daemon
contract → deterministic addressing → curator rules), managed-Python
scripts, local-time calendar, every-scan curation with one item per source,
dashboard plumbing, operator profile + where inference + Up next, people
directory + meeting prep.

**G5** *(deferred)* — reply + scheduling drafts: agent-mode, delegation
fan-out, drafts-only envelope.

**Tier 2 residue** — last-run links + server-side Run Now via `/runs` +
`/trigger` gateway proxy; People-lite as an agent-mode cron with
`context_from` on the scanner jobs; approvals depth, home customization,
empty-state sweep, schedule panel (post-G6R-c data).

**Phase 5 — publish** (the end goal): `hermes-town-plugins` =
desktop plugins + gateway plugin + blueprint skills, distributed via the
skills hub with scan/provenance; upstream issue #66387 already filed;
docs (`sdk-surface`, `rpc-surface`) maintained as the community reference.
