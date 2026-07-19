# Gateway JSON-RPC surface available to `host.request`

This inventory is grounded in the installed source at `~/.hermes/hermes-agent`. Re-verified against `e45d12642` (2026-07-18, hermes update): ONE method removed (`credits.view`) and SIX added for terminal billing (`usage.bars`, `subscription.state/preview/change/resume/upgrade`) — 123 total registrations; every method the Town suite calls is unchanged. Line anchors below otherwise date to `d9ee34241`. JSON-RPC object params are validated centrally and dispatched through `_methods`; `@method(name)` is the registration mechanism (`tui_gateway/server.py:1393`, `tui_gateway/server.py:1395`). The registry below lists the `d9ee34241`-era methods plus the 2026-07-18 billing delta noted above (123 total). Parameter columns record keys read by the handler or its shared session/response helper; `?` means optional and `*` means required by an explicit validation. Responses are the JSON-RPC `result` object, not the outer envelope (`tui_gateway/server.py:1386`). Where a concise shape was not safely inferable, the table says so.

## Dashboard requirements

| Dashboard need | `host.request` finding | Ground truth |
|---|---|---|
| Kanban list | **NOT FOUND.** No `kanban.*` JSON-RPC method is registered. The installed kanban backend instead exposes plugin REST `GET /board`. | `plugins/kanban/dashboard/plugin_api.py:378` |
| Cron list | **FOUND, but not the core desktop path.** `cron.manage({action: 'list'})` is registered. The desktop Cron pane uses REST `GET /api/cron/jobs`. | `tui_gateway/server.py:14987`; `apps/desktop/src/hermes.ts:871` |
| Cron toggle | **FOUND as actions.** `cron.manage({action: 'pause'|'resume', name: jobId})`. The desktop uses REST pause/resume endpoints. | `tui_gateway/server.py:14987`; `apps/desktop/src/hermes.ts:908`; `apps/desktop/src/hermes.ts:915` |
| Session list | **FOUND.** `session.list({limit?}) -> {sessions:[{id,title,preview,started_at,message_count,source}]}`. The desktop sidebar itself uses REST `/api/sessions`. | `tui_gateway/server.py:5351`; `apps/desktop/src/hermes.ts:309` |
| Session unread | **NOT FOUND.** No unread RPC field/method is registered. Desktop unread-finished ids are renderer-local atoms updated from lifecycle events. | `apps/desktop/src/store/session.ts:333`; `apps/desktop/src/store/session.ts:569` |
| Approval respond | **FOUND.** `approval.respond({session_id, choice?, all?}) -> {resolved:boolean}`. | `tui_gateway/server.py:10689`; client `apps/desktop/src/components/assistant-ui/tool/approval.tsx:145` |
| Pending approval list | **NOT FOUND.** The registry has the response method, not an approval-list method; pending requests arrive through gateway events. | response handler `tui_gateway/server.py:10689`; client event handling context `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts:556` |

The practical correction to SPEC section 1.2 is that `host.request` reaches the TUI JSON-RPC registry, but the desktop does not use JSON-RPC for everything. Session persistence/listing and Cron CRUD are REST-backed, and kanban is a namespaced plugin REST API.

## Confirmed desktop call sites

These are static method strings at production client call sites. `requestGateway` is the desktop's wrapper around the same live gateway request function.

| Method | Params at client | Response use | Client evidence | Server evidence |
|---|---|---|---|---|
| `session.create` | assembled session params (cwd/model/profile/etc.) | `SessionCreateResponse` | `apps/desktop/src/app/session/hooks/use-session-actions/index.ts:294` | `tui_gateway/server.py:5519` |
| `session.resume` | `{session_id, source?}` | `{session_id}` / resume payload | `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:349` | `tui_gateway/server.py:5901` |
| `session.close` | `{session_id}` | ignored | `apps/desktop/src/app/session/hooks/use-session-actions/index.ts:302` | `tui_gateway/server.py:8409` |
| `session.usage` | `{session_id}` | `UsageStats` | `apps/desktop/src/app/session/hooks/use-session-actions/index.ts:550` | `tui_gateway/server.py:6843` |
| `session.interrupt` | `{session_id}` | ignored | `apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts:571` | `tui_gateway/server.py:8505` |
| `session.steer` | `{session_id,text}` | status, notably `queued` | `apps/desktop/src/app/session/hooks/use-prompt-actions/index.ts:616` | `tui_gateway/server.py:8795` |
| `session.context_breakdown` | `{session_id}` | `ContextBreakdown` | `apps/desktop/src/app/shell/context-usage-panel.tsx:31` | `tui_gateway/server.py:6867` |
| `session.title` | `{session_id,title?}` | `SessionTitleResponse` | `apps/desktop/src/app/chat/sidebar/session-actions-menu.tsx:76` | `tui_gateway/server.py:6542` |
| `prompt.submit` | `{session_id,text,truncate_before_user_ordinal?}` | ignored/events follow | `apps/desktop/src/app/session/hooks/use-prompt-actions/submit.ts:460` | `tui_gateway/server.py:8832` |
| `approval.respond` | `{session_id,choice}` | `{resolved?}` | `apps/desktop/src/components/assistant-ui/tool/approval.tsx:145` | `tui_gateway/server.py:10689` |
| `clarify.respond` | `{request_id,answer}` | `{ok?}` | `apps/desktop/src/components/assistant-ui/clarify-tool.tsx:247` | `tui_gateway/server.py:10668` |
| `sudo.respond` | `{request_id,password}` | `{status?}` | `apps/desktop/src/components/prompt-overlays.tsx:67` | `tui_gateway/server.py:10679` |
| `secret.respond` | `{request_id,value}` | `{status?}` | `apps/desktop/src/components/prompt-overlays.tsx:170` | `tui_gateway/server.py:10684` |
| `terminal.read.respond` | `{request_id,text}` | ignored | `apps/desktop/src/app/session/hooks/use-message-stream/gateway-event.ts:648` | `tui_gateway/server.py:10673` |
| `config.set` | `{key:'reasoning'|'fast',session_id,value}` | ignored | `apps/desktop/src/store/model-presets.ts:77` | `tui_gateway/server.py:10714` |
| `model.options` | `{session_id?,refresh?,explicit_only?}` | `ModelOptionsResponse` | `apps/desktop/src/lib/model-options.ts:34` | `tui_gateway/server.py:13440` |
| `llm.oneshot` | `{input?,instructions?,max_tokens?,session_id?,task?,temperature?,template?,variables?}` | `{text?}` | `apps/desktop/src/lib/oneshot.ts:46` | `tui_gateway/server.py:6645` |
| `commands.catalog` | `{}` | command pairs/categories | `apps/desktop/src/app/chat/composer/hooks/use-slash-completions.ts:136` | `tui_gateway/server.py:12205` |
| `complete.slash` | `{text}` | `{items?,replace_from?}` | `apps/desktop/src/app/chat/composer/hooks/use-slash-completions.ts:155` | `tui_gateway/server.py:13361` |
| `complete.path` | `{word,session_id?,cwd?}` | `{items?}` | `apps/desktop/src/app/chat/composer/hooks/use-at-completions.ts:105` | `tui_gateway/server.py:13138` |
| `process.list` | `{session_id}` | `{processes?}` | `apps/desktop/src/store/composer-status.ts:323` | `tui_gateway/server.py:12005` |
| `process.kill` | `{session_id,process_id}` | ignored | `apps/desktop/src/store/composer-status.ts:353` | `tui_gateway/server.py:12017` |
| `reload.mcp` | `{confirm:true,session_id?}` | ignored | `apps/desktop/src/app/skills/mcp-tab.tsx:672` | `tui_gateway/server.py:12040` |
| `pet.generate.status` | `{}` | `{available,providers?}` | `apps/desktop/src/store/pet-generate.ts:135` | `tui_gateway/server.py:7636` |
| `pet.rename` | `{slug,name}` | `{ok,slug}` | `apps/desktop/src/store/pet-generate.ts:606` | `tui_gateway/server.py:7400` |
| `pet.select` | `{slug}` | `{ok,slug,displayName}` | `apps/desktop/src/store/pet-generate.ts:616` | `tui_gateway/server.py:7316` |

## Complete server registry

### Sessions and live conversation

| Method | Params shape | Brief result shape | Evidence |
|---|---|---|---|
| `session.create` | `{cols?,messages?,title?,parent_session_id?,cwd?,source?,profile?,model?,provider?,reasoning_effort?,fast?,close_on_disconnect?}` | `{session_id,stored_session_id,message_count,messages,info}` | `tui_gateway/server.py:5519` |
| `session.list` | `{limit?}` | `{sessions:[{id,title,preview,started_at,message_count,source}]}` | `tui_gateway/server.py:5665` |
| `session.most_recent` | `{}` | `{session_id,title?,started_at?,source?}`; id may be null | `tui_gateway/server.py:5711` |
| `project.facts` | `{cwd?}` | `{facts}`; facts may be null | `tui_gateway/server.py:5755` |
| `verification.status` | `{session_id?|session_key?,cwd?}` | `{verification}` | `tui_gateway/server.py:5773` |
| `session.resume` | `{session_id*,cols?,profile?,source?,lazy?,eager_build?,close_on_disconnect?}` | resumed runtime session payload | `tui_gateway/server.py:5901` |
| `session.cwd.set` | `{session_id*,cwd*}` | updated cwd/branch payload | `tui_gateway/server.py:6281` |
| `session.active_list` | `{current_session_id?}` | active session list | `tui_gateway/server.py:6438` |
| `session.activate` | `{session_id*}` | activation status | `tui_gateway/server.py:6476` |
| `session.delete` | `{session_id*}` | `{deleted}` | `tui_gateway/server.py:6500` |
| `session.title` | `{session_id*,title?}` | `{title,session_key}` | `tui_gateway/server.py:6542` |
| `llm.oneshot` | `{template?|variables?,instructions?|input?,task?,max_tokens?,temperature?,session_id?}` | `{text}` | `tui_gateway/server.py:6645` |
| `handoff.request` | `{platform?}` | handoff request payload | `tui_gateway/server.py:6704` |
| `handoff.state` | `{}` | current handoff state | `tui_gateway/server.py:6792` |
| `handoff.fail` | `{error?}` | failure status | `tui_gateway/server.py:6819` |
| `session.usage` | `{session_id*}` | usage statistics | `tui_gateway/server.py:6843` |
| `session.context_breakdown` | `{session_id*}` | context-window breakdown | `tui_gateway/server.py:6867` |
| `session.status` | `{session_id*}` | live session status | `tui_gateway/server.py:8125` |
| `session.history` | `{session_id*}` | message history payload | `tui_gateway/server.py:8183` |
| `session.undo` | `{session_id*}` | undo status/history | `tui_gateway/server.py:8206` |
| `session.compress` | `{session_id*,focus_topic?}` | compression status | `tui_gateway/server.py:8234` |
| `session.save` | `{session_id*}` | save status | `tui_gateway/server.py:8354` |
| `session.close` | `{session_id*}` | close status | `tui_gateway/server.py:8409` |
| `session.branch` | `{session_id*,name?}` | branched session payload | `tui_gateway/server.py:8421` |
| `session.interrupt` | `{session_id*}` | interrupt status | `tui_gateway/server.py:8505` |
| `delegation.status` | `{session_id*}` | delegation status | `tui_gateway/server.py:8576` |
| `delegation.pause` | `{session_id*,paused?}` | pause status | `tui_gateway/server.py:8596` |
| `subagent.interrupt` | `{session_id*,subagent_id*}` | interrupt status | `tui_gateway/server.py:8604` |
| `spawn_tree.save` | `{session_id?,label?,started_at?,finished_at?,subagents?}` | saved tree metadata | `tui_gateway/server.py:8677` |
| `spawn_tree.list` | `{session_id?,cross_session?,limit?}` | tree list | `tui_gateway/server.py:8720` |
| `spawn_tree.load` | `{path*}` | stored tree payload | `tui_gateway/server.py:8771` |
| `session.steer` | `{session_id*,text*}` | status such as `{status:'queued'}` | `tui_gateway/server.py:8795` |
| `terminal.resize` | `{session_id*,cols?}` | resize status | `tui_gateway/server.py:8820` |
| `prompt.submit` | `{session_id*,text*,truncate_before_user_ordinal?}` | acceptance status; output streams as events | `tui_gateway/server.py:8832` |
| `prompt.background` | `{session_id*,text*}` | background-submit status | `tui_gateway/server.py:10492` |

### Kanban

There are no registered `kanban.*` JSON-RPC methods. Kanban is a dashboard plugin REST surface; its list endpoint is `GET /board` (`plugins/kanban/dashboard/plugin_api.py:378`).

### Cron

| Method | Params shape | Brief result shape | Evidence |
|---|---|---|---|
| `cron.manage` | `{action?:'list'|'add'|'remove'|'pause'|'resume',name?,schedule?,prompt?}` | parsed result from `cronjob`; exact shape varies by action | `tui_gateway/server.py:14987` |

### Approvals and prompts

| Method | Params shape | Brief result shape | Evidence |
|---|---|---|---|
| `clarify.respond` | `{request_id*,answer?}` | `{status:'ok'}` | `tui_gateway/server.py:10668` |
| `terminal.read.respond` | `{request_id*,text?}` | `{status:'ok'}` | `tui_gateway/server.py:10673` |
| `sudo.respond` | `{request_id*,password?}` | `{status:'ok'}` | `tui_gateway/server.py:10679` |
| `secret.respond` | `{request_id*,value?}` | `{status:'ok'}` | `tui_gateway/server.py:10684` |
| `approval.respond` | `{session_id*,choice?,all?}` | `{resolved:boolean}` | `tui_gateway/server.py:10689` |

### Config, models, tools, skills, and plugins

| Method | Params shape | Brief result shape | Evidence |
|---|---|---|---|
| `config.set` | `{key*,value?,session_id?,...key-specific fields}` | key-specific update result | `tui_gateway/server.py:10714` |
| `config.get` | `{key*,session_id?,cwd?}` | key-specific config object | `tui_gateway/server.py:11734` |
| `setup.status` | `{}` | `{provider_configured:boolean}` | `tui_gateway/server.py:11895` |
| `setup.runtime_check` | `{provider?}` | `{ok,provider?,model?,source?,error?}` | `tui_gateway/server.py:11905` |
| `reload.mcp` | `{session_id?,confirm?,always?}` | reload or confirmation-required status | `tui_gateway/server.py:12040` |
| `reload.env` | `{}` | environment reload status | `tui_gateway/server.py:12139` |
| `commands.catalog` | `{}` | `{pairs,sub,canon,categories,skill_count,warning}` | `tui_gateway/server.py:12205` |
| `model.options` | `{session_id?,explicit_only?,include_unconfigured?,refresh?}` | model/provider inventory payload | `tui_gateway/server.py:13440` |
| `model.save_key` | `{slug*,api_key*,session_id?}` | `{provider}` | `tui_gateway/server.py:13483` |
| `model.disconnect` | `{slug*}` | disconnect status and provider slug | `tui_gateway/server.py:13565` |
| `plugins.list` | `{}` | plugin inventory | `tui_gateway/server.py:14732` |
| `config.show` | `{}` | displayable config payload | `tui_gateway/server.py:14754` |
| `tools.list` | `{session_id?}` | tool list | `tui_gateway/server.py:14793` |
| `tools.show` | `{session_id?}` | tool detail/selection payload | `tui_gateway/server.py:14824` |
| `tools.configure` | `{session_id?,action?,names?}` | configuration status | `tui_gateway/server.py:14864` |
| `toolsets.list` | `{session_id?}` | toolset list | `tui_gateway/server.py:14933` |
| `agents.list` | `{}` | agent/process summary | `tui_gateway/server.py:14963` |
| `skills.manage` | `{action?,query?,page?,page_size?}` | action-specific skills result | `tui_gateway/server.py:15071` |
| `skills.reload` | `{}` | reload status | `tui_gateway/server.py:15130` |
| `plugins.manage` | `{action?,name?,enable?}` | action-specific plugin result | `tui_gateway/server.py:15155` |

### Miscellaneous registered methods

| Method | Params shape | Brief result shape | Evidence |
|---|---|---|---|
| `pet.info` | `{profile?}` | pet information | `tui_gateway/server.py:7079` |
| `pet.info.meta` | `{profile?}` | pet metadata | `tui_gateway/server.py:7105` |
| `pet.cells` | `{cols?,graphics?,state?,profile?}` | rendered pet cells | `tui_gateway/server.py:7128` |
| `pet.gallery` | `{localOnly?,profile?}` | gallery payload | `tui_gateway/server.py:7233` |
| `pet.select` | `{slug*,profile?}` | selection status and pet identity | `tui_gateway/server.py:7316` |
| `pet.remove` | `{slug*,profile?}` | removal status | `tui_gateway/server.py:7343` |
| `pet.export` | `{slug*,profile?}` | export payload | `tui_gateway/server.py:7373` |
| `pet.rename` | `{slug*,name*,profile?}` | rename status and slug | `tui_gateway/server.py:7400` |
| `pet.thumb` | `{slug?|url?,profile?}` | thumbnail payload | `tui_gateway/server.py:7438` |
| `pet.disable` | `{profile?}` | disable status | `tui_gateway/server.py:7473` |
| `pet.scale` | `{scale?,profile?}` | scale status | `tui_gateway/server.py:7487` |
| `pet.cancel` | `{token?}` | cancellation status | `tui_gateway/server.py:7622` |
| `pet.generate.status` | `{}` | `{available,providers?}` | `tui_gateway/server.py:7636` |
| `pet.generate` | `{prompt?,style?,count?,provider?,referenceImage?}` | generation job/result payload | `tui_gateway/server.py:7667` |
| `pet.hatch` | `{token?,cancelToken?,index?,name?,description?,prompt?,style?,provider?}` | hatched pet payload | `tui_gateway/server.py:7780` |
| `usage.bars` | `{}` | usage bars payload (replaced `credits.view`, removed 2026-07-18) | `tui_gateway/server.py:8178` |
| `subscription.state` / `.preview` / `.change` / `.resume` / `.upgrade` | (billing) | terminal-billing surface, added 2026-07-18 | `tui_gateway/server.py:8253-8353` |
| `billing.state` | `{}` | billing state | `tui_gateway/server.py:7999` |
| `billing.charge` | `{amount_usd*,idempotency_key?}` | charge result | `tui_gateway/server.py:8015` |
| `billing.charge_status` | `{charge_id*}` | charge status | `tui_gateway/server.py:8041` |
| `billing.auto_reload` | `{enabled?,threshold?,top_up_amount?}` | auto-reload settings/result | `tui_gateway/server.py:8070` |
| `billing.step_up` | `{session_id?}` | step-up authentication result | `tui_gateway/server.py:8092` |
| `clipboard.paste` | `{session_id*}` | pasted clipboard payload | `tui_gateway/server.py:9878` |
| `image.attach` | `{session_id*,path*}` | attachment payload | `tui_gateway/server.py:9918` |
| `image.attach_bytes` | `{session_id*,content_base64?|data?,filename?,ext?}` | attachment payload | `tui_gateway/server.py:10050` |
| `pdf.attach` | `{session_id*,path?|content_base64?|data?,filename?,first_page?,last_page?}` | PDF attachment payload | `tui_gateway/server.py:10111` |
| `file.attach` | `{session_id*,path?|data_url?,name?}` | file attachment payload | `tui_gateway/server.py:10378` |
| `image.detach` | `{session_id*,path*}` | detach status | `tui_gateway/server.py:10425` |
| `input.detect_drop` | `{text?}` | detected dropped-input payload | `tui_gateway/server.py:10445` |
| `preview.restart` | `{session_id?,request_id?,url?,cwd?,context?}` | preview restart result | `tui_gateway/server.py:10538` |
| `projects.discover_repos` | `{}` | discovered repository list | `tui_gateway/server.py:11548` |
| `projects.record_repos` | `{repos?}` | record status | `tui_gateway/server.py:11560` |
| `projects.tree` | `{preview_limit?,session_limit?}` | project tree | `tui_gateway/server.py:11681` |
| `projects.project_sessions` | `{project_id*,session_limit?}` | `{project}` | `tui_gateway/server.py:11708` |
| `process.stop` | `{}` | `{killed}` | `tui_gateway/server.py:11978` |
| `process.list` | `{session_id*}` | `{processes}` | `tui_gateway/server.py:12005` |
| `process.kill` | `{session_id*,process_id*}` | process kill result | `tui_gateway/server.py:12017` |
| `cli.exec` | `{argv*,timeout?}` | CLI execution result | `tui_gateway/server.py:12320` |
| `command.resolve` | `{name*}` | command resolution | `tui_gateway/server.py:12352` |
| `command.dispatch` | `{name*,arg?,session_id?}` | dispatch result | `tui_gateway/server.py:12382` |
| `paste.collapse` | `{text?}` | collapsed-paste payload | `tui_gateway/server.py:12934` |
| `complete.path` | `{word?,session_id?,cwd?}` | `{items}` | `tui_gateway/server.py:13138` |
| `complete.slash` | `{text?}` | `{items,replace_from}` | `tui_gateway/server.py:13361` |
| `slash.exec` | `{session_id*,command*}` | slash-command result | `tui_gateway/server.py:13977` |
| `voice.toggle` | `{action?}` | voice toggle status | `tui_gateway/server.py:14165` |
| `voice.record` | `{action?,session_id?}` | recording result | `tui_gateway/server.py:14262` |
| `voice.tts` | `{text?}` | speech result | `tui_gateway/server.py:14339` |
| `insights.get` | `{days?}` | insights payload | `tui_gateway/server.py:14358` |
| `rollback.list` | `{}` | rollback entries | `tui_gateway/server.py:14386` |
| `rollback.restore` | `{hash*,file_path?}` | restore result | `tui_gateway/server.py:14416` |
| `rollback.diff` | `{hash*}` | diff payload | `tui_gateway/server.py:14463` |
| `browser.manage` | `{action?,url?,session_id?}` | browser action result | `tui_gateway/server.py:14588` |
| `learning.frames` | `{cols?,rows?,frames?}` | `{frames,...timeline metadata}` | `tui_gateway/server.py:15014` |
| `learning.detail` | `{id*}` | learning-frame detail | `tui_gateway/server.py:15038` |
| `learning.delete` | `{id*}` | deletion status | `tui_gateway/server.py:15049` |
| `learning.edit` | `{id*,content?}` | edit status | `tui_gateway/server.py:15060` |
| `shell.exec` | `{command*}` | subprocess result | `tui_gateway/server.py:15234` |

## NOT FOUND

- No JSON-RPC method for kanban board/task listing or mutation. Use the kanban plugin REST namespace; the board route is `plugins/kanban/dashboard/plugin_api.py:378`.
- No JSON-RPC method or session-list field for unread state. The desktop derives unread-finished sessions locally (`apps/desktop/src/store/session.ts:333`).
- No JSON-RPC method to enumerate pending approvals. Only `approval.respond` is registered (`tui_gateway/server.py:10689`); requests arrive as events.
- The desktop Cron REST surface now also exposes `POST /api/cron/jobs/{id}/trigger` and `GET /api/cron/jobs/{id}/runs` (`apps/desktop/src/hermes.ts:922`, `apps/desktop/src/hermes.ts:884`) — still renderer-inaccessible cross-namespace, but a town-feed gateway bridge could proxy them (Tier 2 candidates: server-side Run Now, last-run links).
- No separate `cron.list`, `cron.pause`, `cron.resume`, or `cron.toggle` names. Those operations are actions of `cron.manage` (`tui_gateway/server.py:14987`), while the desktop's own Cron UI uses REST (`apps/desktop/src/hermes.ts:871`).
