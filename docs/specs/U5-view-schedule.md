# U5 — View schedule (greeting chip + schedule dialog)

*2026-07-18, polish-loop iteration 5. Town: "Friday · View schedule"
chip beside the greeting opens a schedule panel. Our calendar lane is
live (both accounts; scanner every 30m) and ALREADY collects the full
upcoming-events list in `upcoming` — it just discards all but
`next_event` (gateway/scripts/town-calendar-scan.py line ~258-283).
Conventions: docs/specs/README.md binds. DO NOT start this iteration
until the U3/U4 deploy hold clears (UX-LOOP.md).*

## Lane A — scanner + curator + feed (gateway files)

1. `gateway/scripts/town-calendar-scan.py`: extend the `upcoming`
   tuple collection to also carry `end_iso` and `all_day` (both already
   in scope at the collection site), and emit in the payload, next to
   `next_event`: `"events": [...]` — ALL upcoming events already
   collected (they are bounded by the existing scan window and
   MAX_EVENTS_PER_ACCOUNT), sorted by start ascending, capped at 20,
   each `{summary, start_iso, end_iso, all_day, account}`. next_event
   stays exactly as-is. IMPORTANT: emit `events` even when `candidates`
   is empty — today the function returns `0, None` when there are no
   candidates, which would drop a quiet day's schedule; return the
   payload whenever events OR candidates exist.
2. `gateway/town-feed/dashboard/plugin_api.py`: where the curator
   ingests the calendar scan payload (the same place `next_event` is
   read), persist `state["schedule"] = {"events": <the list>,
   "generated_at": <now ISO>}` (defensive: list of dicts only, cap 20).
   Treat as stale and return [] from /feed when generated_at is older
   than 3 hours. Add `schedule` to the `/feed` response. State version
   stays (additive key, defensive read).

## Lane B — bridge + UI (desktop files; AFTER Lane A defines the shape)

3. `src/town-feed/plugin.tsx`: normalize `schedule` into $feed
   (`schedule: Array<{summary, startAt, endAt, allDay, account}>`,
   timestamps parsed like nextEvent's) and expose through the window
   bridge feed state.
4. `src/town-home/plugin.tsx`: beside the greeting's date line, when
   the bridge is present AND schedule is non-empty, a small text-chip
   "View schedule" (calendar Codicon — already used in this repo)
   opening an SDK Dialog (the run-history dialog is the wiring
   reference — boolean open atom whose setter receives EVERY
   onOpenChange value): events grouped under "Today" / "Tomorrow" /
   weekday headers (local time), each row `h:mm a – h:mm a` (or an
   "All day" pill), summary truncated, account short-name pill ONLY
   when more than one account contributes. Empty/dormant → no chip at
   all (calendar-less installs see nothing new).

## Deploy note (orchestrator)

Scanner change requires re-installing the script copy to
`~/.hermes/scripts/town-gmail-scan.py`-style location for
town-calendar-scan.py, the gateway plugin copy, BOTH gateway restarts,
then manual `hermes cron run town-calendar-scan && hermes cron run
town-feed-curate` to materialize the schedule before visual checks.

## DoD per lane

Unpiped `npm run check` + `npm run build` (Lane B), `python3 -m
py_compile` on both Python files (Lane A); no files outside the lane's
scope; report wrong spec assumptions instead of working around them.
Do NOT deploy or restart anything.
