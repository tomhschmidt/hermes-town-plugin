# DRAFT upstream issue — blueprints cannot declare no_agent script jobs

**Do not post without Tom's explicit go.** Target: NousResearch/hermes-agent.

## Title
Blueprint frontmatter cannot express `no_agent` script jobs — `create_job`
rejects the spec blueprints produce

## Body
`metadata.hermes.blueprint` supports `schedule/deliver/prompt/no_agent/
model/provider/enabled_toolsets` (tools/blueprints.py BlueprintSpec), but
not `script`. Since `create_job(no_agent=True, script=None)` raises
("no_agent=True requires a script"), any blueprint declaring
`no_agent: true` is dead on arrival at acceptance time — the combination
is expressible in frontmatter but unconstructible.

Script-backed `no_agent` jobs are otherwise first-class (`cronjob` tool,
`hermes cron create --script --no-agent`), and skills already ship script
files, so the gap appears to be just plumbing: `script` (and probably
`workdir`) in BlueprintSpec/parse_blueprint/blueprint_to_job_spec, plus
resolving the packaged script into `~/.hermes/scripts/` at acceptance.

Encountered building a community suite (hermes-town-plugins) whose
scanner jobs are the fetch→classify shape `cron/scripts/classify_items.py`
models; we currently fall back to `add_suggestion(source="integration")`
with a hand-built job_spec, which works but loses the Blueprints-tab
surface. Happy to PR if the shape above is acceptable.

Repro: any SKILL.md with `blueprint: {schedule: "every 10m", no_agent:
true}` → accept its suggestion → ValueError from cron/jobs.py:1167.
