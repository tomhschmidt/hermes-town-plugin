# Polish round (P1–P6) — product experience & UI fit-and-finish vs Town

*2026-07-18, from a live side-by-side (Town dark-mode home + hover
captures vs our /town). Conventions: docs/specs/README.md binds; theme
vars only; imperative handlers; 150ms `transition-*` as the standard
motion. Executors: Codex gpt-5.6-sol medium, parallel by file lanes.*

Observed Town patterns to match (described for executors who can't see
the screenshots):
- Rows are quiet until hover: hovering a task row tints its background
  and fades in trailing icon actions (expand, ⋯) beside the persistent
  contextual chip ("Review document" with a doc icon); hovering a
  section header fades in its header actions (filter, +).
- Suggestions are icon-led rows, not flat chips: leading circular glyph
  (person initials for people-follow-ups, magnifier for research,
  envelope for email, star for plans), verb-first natural text that
  chains off the current Need-to-Know items ("Register for the an event
  dinner on September 14", "Respond to Noahpinion's 'Power and
  Progress' book review").
- Entity references render as compact pills/links, never raw IDs.
- The composer is a soft rounded container with an integrated bottom
  bar: icon cluster lower-left, mode pill + primary action lower-right.

## P1 — Reference pills (kill the raw IDs)
NTK card, full-feed page: source lines currently print raw strings
("session:20260717_154117_d9ddf290 · 2 hr. ago", "Gmail ·
<work-account>"). Replace with a compact pill component (twin per
bundle): [Codicon + label] — session → comment-discussion + "Session";
gmail → mail + account short-name (before @); telegram → send + chat id
(name when the summary carries it); cron → clock + job name; calendar →
calendar + "Calendar". Pills are the click target (same navigation as
today), hover state, relative time stays as muted text after the pill.

## P2 — Hover-reveal interaction model
All list rows (NTK items, kanban tasks, routines, feed page): row hover
background (`hover:bg-(--ui-bg-hover)` — verify the actual theme var
names available; pick the established muted-surface var used by SDK
components) + 150ms transition. Per-item text actions (Dismiss / Turn
into task; routines Run Now + history) become opacity-0 →
group-hover:opacity-100 (still focusable: focus-visible forces
visible). Section headers: card action icons (refresh, gear) get the
same reveal treatment. Checkbox and persistent context chips stay
always-visible.

## P3 — Suggestions parity (content + presentation)
Gateway: rewrite the suggestions instruction inside _llm_rank: verb-led
imperatives ≤70 chars, person/event-centric, explicitly derived from
the top-ranked feed items when possible (follow-up on the person, next
concrete step on the event), mix kinds, natural language, zero
ops-jargon (no "verify auth state mismatch" phrasing). Desktop
(town-home): SuggestionsCard becomes icon-led rows: leading circular
glyph by kind (followup → person, digest → mail-read, maintenance →
tools; use initials chip when the label starts with a capitalized name
pair), single-line clamp, hover reveal of a subtle "prefill" affordance;
click behavior unchanged (prefill composer, focus).

## P4 — In-context action chips (persistent, like "Review document")
Kanban rows with sessionId → chip "Open session" (comment icon). NTK
gmail-sourced → chip "Open in Gmail". NTK cron-sourced → chip "View
run" opening the run-history dialog for that job. Chips are small
bordered buttons with icon+label, persistent (not hover-gated),
right-aligned in the row.

## P5 — Composer fit-and-finish
Town-frame the existing composer: one rounded-xl bordered container;
textarea borderless inside; bottom bar inside the container — left: a
muted hint ("Enter to start · Shift+Enter for a new line"), right:
SegmentedControl + primary Button; focus-within ring on the container
(accent var); gateway-offline state dims the container with an inline
notice instead of just a disabled button. No functional changes.

## P6 — Dialog dismissal BUG + micro-polish sweep
CONFIRMED BUG: the run-history dialog cannot be dismissed — X clicked
twice and Escape pressed, dialog stays while content re-renders. Audit
EVERY Dialog usage (town-home: history/customize; town-routines:
history; any others) for correct `open`/`onOpenChange` wiring — the SDK
Dialog's dismiss events (X, Escape, backdrop) must all drive the same
state setter. Fix all instances. Sweep: history dialog "unknown" status
for legacy runs renders as muted "—" not the word "unknown"; consistent
card padding (p-4) and type ramp (title text-sm font-medium, meta
text-xs tertiary); replace any remaining instant show/hide with 150ms
transitions.

## Lanes
- Lane G (gateway plugin_api.py): P3-content. 
- Lane F (src/town-feed): P1 + P2 for the feed page.
- Lane R (src/town-routines): P2 + P6-dialog-fix for the pane.
- Lane H (src/town-home, sequential): H1 = P1+P2+P4; H2 = P3-presentation
  + P5; H3 = P6 (dialog audit/fix + sweep).
Per-lane review gate; single combined deploy; UI verification with
hover/dismissal interaction tests.
