# U12 — Fit-and-finish: per-section audit vs Town (low-hanging fruit)

*2026-07-18, from the hands-on section-by-section comparison against
Town's live home. Single in-scope file: src/town-home/plugin.tsx.
Conventions: docs/specs/README.md binds (Tailwind subset rule; no new
paren-var class strings; inline style for anything the subset can't
express; NO git commits).*

## 1. Borderless main column (Town's airiness)

Town's main column has NO card outlines — quiet section headers +
whitespace; only the right rail uses soft cards. Mirror it: SectionCard
gains this behavior from the existing `sideRail` prop — sideRail=false
cards drop `rounded-xl border border-(--ui-stroke-secondary)` and their
padding shrinks to `px-0 py-0` (the column's gap-6 provides rhythm;
keep the title block + mt-4). Rail cards keep border + p-4. The
composer keeps its bordered container (it's an input, like Town's).

## 2. Header hierarchy (eyebrow above the greeting)

Reorder: the date + View-schedule chip line becomes a small EYEBROW
ABOVE the serif h1 (`text-sm text-(--ui-text-secondary)`, chip
unchanged); the h1 follows; the context line ("Next: …") stays below
the h1 unchanged. (Town: "Saturday · View schedule" over "Good
afternoon, Tom.")

## 3. Greeting personalization

"Good afternoon, Tom." — add an optional display name: new `userName`
string field in TownHomePreferences (defensive load, default "").
Customize dialog gains a small text input ("Your name — shown in the
greeting"; SDK input or plain input styled with theme vars, imperative
save via the existing preferences update path). Greeting renders
`Good afternoon, {name}.` when set, else `Good afternoon.` (with the
period — matches Town's sentence style).

## 4. Suggestions wrap instead of truncate

Suggestion labels: remove `truncate` + title tooltip; allow natural
wrap (labels are ≤70 chars → max two lines). Keep the row's flex
alignment `items-start` so the chip tops align on wrapped rows.

## 5. Recents de-noise

Exclude our own scanner jobs from Recents routine rows: skip cron rows
whose name matches `town-gmail-scan`, `town-telegram-scan`,
`town-calendar-scan`, `town-feed-curate` (exact names). User routines
(sf-sublet-monitor, parcel-gmail-tracker, …) and sessions remain.

## 6. Humanize routine names in RoutinesCard

RoutinesCard currently renders raw kebab names ("telegram-action-items");
reuse the existing `humanizeJobName` helper for the display name
(id/name data attributes unchanged).

## 7. Tasks card combined empty state

When the board is clear AND nothing is waiting: render ONE compact
empty state ("Board clear — nothing needs your attention" +
description line + Add a task action, ≤96px) instead of the current two
stacked empty sections; the "Waiting on you" sub-header with its zero
badge is hidden in that case. When either has content, current layout
unchanged.

## 8. Run-history dialog output cleanup

The dialog currently dumps raw cron internals ("# Cron Job: …",
"**Job ID:** …", raw payload JSON). Add a presenter: strip the leading
"# Cron Job:" header block (up to the first blank line) and the
"**Key:** value" boilerplate lines; if the remaining output parses as a
town-scan JSON payload (reuse/port the tolerant extraction pattern —
line-scan then brace-slice), render a one-line synthesis instead:
"Scan payload · N candidates · N events · N open loops" (only counts
for keys present). Otherwise show the cleaned text as today (2000-char
cap unchanged).

## 9. NTK header meta parity

The NTK card title currently reads "Need to Know · 18 min. ago" inline.
Move freshness to the RIGHT side of the header row (text-xs tertiary)
with a small PERSISTENT refresh icon button beside it (existing refresh
affordance/handler, no hover-gating). Same treatment for the
Suggestions card header freshness.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files
changed outside src/town-home/plugin.tsx; NO git commits; report
each section you changed and anything that did not fit cleanly (flag,
don't force). Do NOT deploy or restart anything.
