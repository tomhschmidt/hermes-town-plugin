# Town vs. Hermes Town — gap analysis (2026-07-17)

Method: drove Tom's live logged-in Town.com account (web) side-by-side with the
deployed Hermes Town suite (local-gateway topology, all 7 plugins loaded).
Feasibility notes reference `docs/rpc-surface.md`.

## What Town showed (observed, not from docs)

- **Home**: calendar-aware greeting ("Good afternoon, Tom. You have no
  upcoming meetings"), "View schedule" slide-over (day calendar). Centerpiece
  is a **task composer** ("Give Scribedash a task") with attachment, people,
  schedule, **permission shield** (Ask before writes / Always allow safe /
  Allow all), **Task|Chat mode** (⌘.), and voice. Tasks list: checkbox
  completion, one-line status subtitles ("DocSend deck attached…; follow-up
  task created."), inline action chips ("Review document"), "**Needs input:**"
  links into the thread. Recents below (typed icons, timestamps). Right rail:
  Townie mascot, **Need to know** (2 items stacked, "1h ago" + manual refresh,
  bold entity names, "Read here" source links), **Suggestions** (contextual
  prompt chips, auto-refreshing "Checking…"). **Customize** dialog: reorder/
  hide right-rail widgets, Townie toggle, task list behavior (keep-at-top,
  auto-hide after 12h–1wk).
- **Task detail** (`/assistant/<id>`): transcript with date separators,
  collapsed tool-call chips ("Search Emails", "Read Email"), prose updates,
  and a structured **"Present Options" card (status: Waiting)** — question,
  context, option buttons. Follow-up composer, Share.
- **Powers → Routines**: card gallery (icon, description, ⋯ menu),
  "Suggested for you" carousel with one-click install, Create routine, Trusted
  contacts. **Routine detail**: cover art, enable toggle, Run Now, Edit with
  Assistant, Copy to Team Routines, Create Installable Routine, Share; tabs =
  Recent sessions (outcome one-liners + "Ask assistant") / **Settings**
  (structured per-routine preferences — briefing time/days, calendars,
  delivery Email+Push, audio version, topics; ADVANCED: schedule trigger,
  **per-tool permissions** (read-only / requires approval / always allowed) by
  tool group, execution mode, outbound tool risk, MCP servers, account
  scoping) / Memories / Info / Webhook.
- **Powers → Skills**: SKILL.md directories, upload/create, personal + team.
- **Powers → Integrations**: 49-connector catalog, connected badges, "Add MCP
  server".
- **Library**: auto-grouped Collections (incl. per-routine), All Files with
  type filters, Suggested Content, provenance notes ("Moved to 'People'
  collection").
- **People**: auto-built CRM — "your assistant builds a working profile of the
  people you interact with" (email/phone, role description, Invite).
- **Team**: teammates w/ Townies, team routines, activity log, billing.
- **Notifications**: "team invitations and routine approvals appear here" +
  History. **Recents page**: filters All | Threads | Routine Results | Drafts
  + status.
- Identity: the agent is a persona ("Scribedash") with its own email address.

## What Hermes Town shows today

Dashboard (`/town`): date greeting, Gateway online + model chips; **Tasks**
(kanban via town-feed bridge; currently shows 60-day-old Done items),
**Waiting on you** (0/empty state), **Need to Know** (single-item carousel
"1 of 30", Dismiss / Turn into task, session ref + age), **Routines** (cron
list: raw expression "0 8 * * *", next-run countdown, last status, pause via
town-routines pane, + New routine prompt dialog), **Briefing** (empty-state
pointer). Right sidebar: Approvals pane (event-driven approve/deny, badge),
town-debug. Statusbar: next-routine chip. Session rows clickable (T8).

## Gaps → next round

### Tier 1 — core functionality (all verified feasible)

1. **Task composer on town-home** — "Give Hermes a task" input → `session.create`
   with `{messages:[prompt], title}` (JSON-RPC, verified registered). Options:
   Task (also files a kanban card via bridge) vs Chat (session only). This is
   Town's centerpiece interaction and we have zero of it.
2. **Tasks card overhaul** — hide stale Done items (Town auto-hides after a
   time-frame; ours shows "Done 60 days ago" ×3), active-first ordering,
   checkbox-complete (new bridge endpoint: kanban status update via
   `hermes_cli.kanban_db`, same idempotent path as promote-to-task), click →
   open linked session, status subtitle from the latest linked-session
   outcome.
3. **Routines humanization** — render "0 8 * * *" as "Daily at 8:00 AM";
   description line (derive from cron prompt); **Run Now** (= `session.create`
   with the cron job's prompt — `cron.manage` has no run action); last-result
   line links to the run's session.
4. **NTK presentation parity** — stack 2–3 items instead of 1-of-30 carousel;
   card header with freshness ("37 min ago") + manual refresh (POST /curate
   exists); "Read here" → open source session; curator-prompt iteration for
   bold-entity, deadline-aware summaries; cap/expire the backlog (30 live
   items is too many).
5. **Suggestions widget** — curator generates 3–5 contextual prompt chips
   (town-feed already has `ctx.llm` + session/cron/memory scanning); click →
   prefill the new composer. New `/suggestions` REST on the gateway plugin.

### Tier 2 — polish

6. **Greeting upgrade** — "Good afternoon, Tom" + one context line (next
   routine countdown, waiting-on-you count) in place of Town's calendar line.
7. **Home customization** — show/hide/reorder cards + task auto-hide
   time-frame, persisted (plugin storage/host.state).
8. **Approvals depth** — richer context on cards (tool args preview), approval
   history section (Town keeps History), multi-option rendering when an
   approval carries choices (Present-Options analogue; check `approval.request`
   payload for options).
9. **Empty/stale-state sweep** — every card gets a designed empty state
   (Briefing has one; Tasks shows stale noise instead).

### Tier 3 — new surfaces (bigger; sequence after 1–2 rounds)

10. **Briefing one-click** — "Create Morning Briefing" button that
    `cron.manage add`s a prefab briefing routine (prompt template with
    schedule/email/Telegram delivery choices), replacing the empty-state text.
11. **Routine detail view** — per-routine recent runs (filter sessions by cron
    parentage if the data allows), settings subset (schedule edit via
    `cron.manage add/remove`), enable toggle = pause/resume.
12. **People-lite** — gateway-plugin pass over recent sessions extracting
    contacts into a `/people` REST + simple pane. Town's auto-CRM is a
    flagship feature; ours would start crude.
13. **Suggested-routines gallery** — static catalog of prefab routines
    (briefing, inbox triage, parcel tracking — we already run 4 real crons to
    seed from) with one-click install.

### Explicitly out of scope (Hermes already covers, or N/A)

- Task-detail transcript view → native Hermes session panes already do this
  (our rows open them). Present-Options lives in approvals (item 8).
- Library/Files → Artifacts pane; Skills/Integrations galleries → Capabilities
  + Settings→Plugins; Search → native session search; Team/billing → N/A
  single-user; voice input, Townie mascot → cosmetic, skip for now.
- Per-tool permission matrices and safety modes → gateway-level config, not a
  desktop-plugin surface today; revisit if Hermes exposes approval policy RPC.
