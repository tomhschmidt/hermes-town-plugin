# GROUNDING spec pack (R1–R5, S1–S2) — reliability + situational awareness

*Drafted 2026-10-11 from a production audit of the live deployment (cron run
history, feed-state, scanner outputs, Telegram history replayed through the
prompt builders) plus a review of ~170 Town.com assistant emails. Shared
conventions in `docs/specs/README.md` bind, with the overrides below.*

| Spec | Title | Lane |
|---|---|---|
| [R1a](R1a-tdlib-daemon.md) | TDLib daemon: move into repo + richer message contract | gateway/tdlib-daemon |
| [R1b](R1b-telegram-attribution.md) | Telegram: stop fake + already-handled action items | scripts + curator rules |
| [R2](R2-script-runtime.md) | Scripts run on the managed Python (labeler outage) | scripts |
| [R3](R3-calendar-time.md) | Calendar: local time, cross-calendar dedupe, event types | calendar scanner |
| [R4](R4-ntk-curation.md) | Need-to-Know: consume every scan, one item per source, no junk | curator + gmail |
| [R5](R5-dashboard-plumbing.md) | Dashboard shows raw plumbing | desktop + routes |
| [S1](S1-where-and-when.md) | Where & when: operator profile, location, local time, Up next | all layers |
| [S2](S2-who-people-are.md) | Who people are: people directory + meeting prep | all layers |

## Overrides to README.md conventions

1. **Isolation.** Each lane works ONLY inside the git worktree path it is
   given. Never touch `~/.hermes`, `~/.openclaw`, `~/.tdlib_daemon`,
   LaunchAgents, or any live state. Never deploy, restart services, trigger
   cron jobs, call an LLM, call Google/Telegram APIs, or send messages.
   Reading live files to understand shapes is allowed; COPYING live data
   into the repo is not (see 3).
2. **Commits.** Do not commit. The orchestrator reviews the working tree in
   your worktree, then commits.
3. **Public repo — synthetic data only.** This repository is public (MIT).
   Specs, fixtures, tests, comments, and commit text must use invented
   names, addresses, chat titles, and message text (Alice/Bob/Carol,
   `alice@example.com`, "Weekend Crew"). Never write the operator's real
   name, email, phone, address, Telegram ids/handles, contacts, or message
   content into the repo. Operator identity is resolved at RUNTIME
   (daemon `/me`, `operator.json`, `USER.md`), never hardcoded in new code.
4. **Tests are acceptance criteria.** Every "Required tests" bullet becomes
   at least one named test. Gateway: stdlib `unittest` in
   `gateway/tests/test_<spec>_*.py`, importing via `gateway/tests/support.py`
   (`load_script`, `load_plugin_api(state_dir)`); run with
   `npm run test:gateway`. Desktop: pure logic lives in `.ts` modules (no
   JSX, no SDK/React imports, erasable TypeScript only — no enums,
   namespaces, or parameter properties) imported by `plugin.tsx` with an
   explicit `.ts` extension, tested by `tests/desktop/<spec>*.test.ts` with
   `node:test`; run with `npm run test:desktop`. No network, no live state,
   no wall-clock dependence (inject `now`).
5. **Definition of done** = README.md DoD plus: `npm test` green, every
   hard requirement mapped to code + test in your report, and the report
   lists every file touched. Flag spec errors instead of working around them.

## Shared contracts introduced by this pack

### Operator profile (`gateway/town-feed/dashboard/operator_profile.py`)

Single loader, importable from the plugin (same dir) and from cron scripts
(they already import sibling modules from the installed
`~/.hermes/plugins/town-feed/dashboard/` by path). Source of truth is
`$HERMES_HOME/town-feed/operator.json` (user-owned, never in the repo);
missing fields fall back to `$HERMES_HOME/memories/USER.md` structured
lines (`**Name:**`, `**What to call them:**`, `**Timezone:**`,
`**Telegram:**`), then to the system timezone. R4 creates the module with
the fields it needs; S1 extends it. Schema (all optional):

```json
{
  "name": "Alice Example",
  "preferred_name": "Alice",
  "home_timezone": "America/New_York",
  "home_city": "New York",
  "self_addresses": ["alias@example.com"],
  "assistant_senders": ["assistant@example.com"],
  "work_domains": {"example-capital.com": "Example Capital"},
  "telegram_user_id": 1000001,
  "telegram_username": "alice_example"
}
```

`load_operator_profile(hermes_home: Path | None = None) -> dict` never
raises; malformed files log one stderr line and yield defaults. Never
include free-text memory contents in the profile.

### Telegram daemon message contract (R1a → R1b)

Additive fields only; old consumers keep working. See R1a.
