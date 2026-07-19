# UX polish loop — ledger

*Standing goal (2026-07-18): iterate one well-scoped Town polish item at a
time — spec → Codex (gpt-5.6-sol medium, parallel lanes) → orchestrator
review → deploy → interaction-verify — until the Hermes Town suite is
credibly competitive with Town. Backlog graded from a live Town re-read
(home, 2026-07-18); re-inspect Town every few iterations.*

## Iterations

| # | Item | Spec | Status |
|---|---|---|---|
| 1 | Forgiving Dismiss: optimistic removal + inline Undo strip + `/undismiss` + overlay reconciliation (server truth wins on refetch) | U1-dismiss-undo.md | shipped 2026-07-18; dismiss→undo interaction-verified live. One check outstanding: the reconciliation self-heal (stale `ntk_af47389a263a8a65` in the town-feed dismissed-ids overlay should auto-drop, resurfacing the Dialog-dinner NTK item) — machine locked mid-verify; confirm visually on next unlocked session |
| 2 | Recents activity stream: sessions + routine outcomes interleaved on /town (+ Customize-loader merge fix — old validation reset stored orders when CARD_IDS grew) | U2-recents-card.md | shipped 2026-07-18; visual check queued behind screen unlock |
| 3 | "Needs input" sub-state on task rows (waitingItems ↔ kanban sessionId correlation, accent sub-line, float-to-top before the 7-row cap) | U3-needs-input.md | committed 9c60854; deploy held |
| 4 | Quick-action chips under Suggestions (Brief me on today / Plan a task / New routine / Refresh feed) + composer mode lifted into $composer atom | U4-quick-actions.md | committed a51a454; deploy held |
| 5 | View schedule: scanner emits events[], /feed schedule (3h stale gate), greeting chip + Today/Tomorrow dialog. Lane B also killed the last three named group variants (card-header/suggestion/town-header reveals were latently dead) | U5-view-schedule.md | shipped 2026-07-18 (845b54e — NOTE: Codex self-committed+pushed, content review-approved; lanes now barred from git). Gateway verified live (6 real events on /feed); desktop chip/dialog visual check + embedded-gateway app relaunch idle-gated |
| 6 | Gmail labels: bare lowercase + Gmail-palette colors, adopt-or-rename migration (v1) + legacy re-tag/delete cleanup (v2, town/-guarded); label-driven curation: town_inbox classification flows into ranking (action-needed boost, newsletters/promo NTK-excluded), suggestions from four signal groups (action mail w/ aged re-raise ≥55, awaiting-reply nudges, notable charges, event prep w/ personal-reminder exclusion + 2-slot calendar cap), retention 30 vs display 10 | U6-labels-colors-curation.md | shipped 2026-07-18, migration executed + verified in BOTH Gmail accounts (zero Town/ chips remain), suggestions verified live. Transitional gaps: pre-migration M. thread unrecoverable (evicted at cap 10); awaiting-reply nudges populate as threads re-observe |

| 6b (origin session) | Dialog dismissal corrective: host X is decorative (Tip drops the props DialogPrimitive.Close composes — upstream draft docs/upstream-dialog-close-tip-issue.md, gated on Tom's go); own close buttons + capture-phase Escape hook across all 5 dialogs | U6-dialog-dismissal.md | REVERTED 2026-07-18: Tom confirmed physical Escape dismisses dialogs, fully falsifying the premise (host X was also live-verified working). Custom close buttons + Escape hook removed; upstream draft deleted (false premise). |
| 7 | Labels: bare lowercase + Gmail palette colors; label-driven curation + suggestion quality | U6-labels-colors-curation.md | OWNED BY THE FORK SESSION (sharp-cori-a6fd3f — Tom forked this session mid-loop; it authored the spec AND implemented/deployed its own version incl. a v2 message-consolidation migration, live-run against both accounts 12:31-12:40). This session executed the spec in parallel unknowingly, briefly overwrote the live install at ~12:41, then restored the fork's copies at ~12:44 and messaged the fork. Parallel implementation preserved in `git stash` ("U7 parallel implementation") — NOT committed. ALL gateway/scripts deploys from this session are ON HOLD until the fork replies or Tom decides |

| 8 | Live relative timestamps (refcounted 30s ticker per bundle) + Recents Show more + NTK +N-more verified | U8-live-time-show-more.md | shipped 2026-07-18 (d2974b9) |
| 9 | Board identity rev: Town-style two-column board (main + 384px side rail via ResizeObserver-driven inline geometry — host CSS lacks lg: variants, third subset lesson), whitespace pass (rounded-xl, p-5, py-6 empty states), ui-serif display greeting, theme-derived color (color-mix tint tokens: side-rail surface + accent glyph chips) | U9-board-identity.md | shipped 2026-07-18, visually verified live (two columns, serif greeting, tinted chips; U6 lanes visibly producing gmail suggestions + first awaiting-reply nudge) |
| 10 | Curation parity: full calendarList coverage (a work work calendar unlocked), 7d gmail lookback w/ exactly-once seen-set, durable open-loops memory (action-needed mail, ask/deadline schema, owner-reply close, dismiss suppression, Town-shared-label passthrough), cron/session noise killed at source + stale-prior skip + state purge, notable-charge ≥$100 boost, body-aware detail extraction (top-15) | U10-curation-parity.md | shipped 2026-07-18; verified live: M. open loop seeded, deadline re-raise is the #1 NTK item ("registration deadline was EOD July 16"), suggestions match Town's set (Dialog follow-up, weekly sync prep, watch-party prep). Residual: pre-U10-processed TWENTY receipt keeps its new-sender label (one historical miss; future receipts dual-label via classifier fall-through) |
| 11 | Row alignment polish: unified borderless row primitive (rounded-lg -mx-2 px-2 py-2.5, no first:pt-0 highlight compression, content fully inside hover bounds), glyph-chip centering (Codicon direct flex child + leading-none), rail tint removed (hover contrast fallback). RETRACTED same-day: the "host pointer-swallow" theory was a synthetic-pointer tooling artifact — Tom's real click in the claimed dead zone (sidebar collapsed) worked; upstream draft deleted unposted. SECOND synthetic-input false positive this session (after Escape): rule added — no bug claims from synthetic input alone | U11-row-alignment.md | shipped 2026-07-18; hover verified on every surface (Recents first row contains icon/text/meta; suggestion pencil + highlight on rail; chips centered) |
| 12 | Fit-and-finish audit vs Town (hands-on per-section): borderless main column, eyebrow-above-greeting hierarchy, userName greeting preference (Customize field), suggestions wrap not truncate, Recents de-noised (town-* scanners excluded), humanized Routines names, combined Tasks empty state, run-history presenter (cron boilerplate stripped, scan payloads synthesized to counts), NTK/Suggestions right-aligned freshness + persistent refresh | U12-fit-and-finish.md | shipped 2026-07-18, all nine verified live in one screenshot |

## Merged record note (2026-07-18, consolidation)

Both sessions' entries above are kept verbatim for the record; where
they conflict: (a) the "regression" attributed to the fork's curator is
REFUTED — the "Cron: telegram-action-items" headlines appear in the
12:25 pre-U6 state dump, so they pre-date the curator rework
(pre-existing cosmetic, on the backlog); (b) ffe6bee's custom dialog
close buttons shipped and are live/functional, but their premise (host
X decorative) was falsified by a live test — revert candidate pending
Tom's physical Escape check; (c) label/curation ownership resolved to
the fork (this branch), whose implementation is the live one.

## Loop close-out (2026-07-18 ~13:10)

Suggestion quality VERIFIED under the fork's U7 curator (17:05Z curate):
"Help a colleague secure tool access" / "Pay the credit card" (due
July 19) / "Send a friend birthday wishes" — person/event-centric,
deadline-aware, independent-signal-derived. Town's bar met.

ONE REGRESSION FOR THE FORK (its owned file, deploys held here): the
same curate emitted two cron-jargon NTK headlines ("Cron:
telegram-action-items", "telegram-action-items · Jul 18 08:00") — the
pre-U7 curator suppressed scheduler-mechanics headlines (P3 rule +
town-* silent-doc skip). The fork's plugin_api rework appears to have
loosened that path; re-apply the no-scheduler-jargon guard.

Verdict: within the dashboard scope Tom set (Tasks, NTK, Routines,
Approvals, Suggestions, ambient gmail/calendar/telegram context,
auto-inbox), the suite is judged competitive with Town — every pattern
from Tom's original review (subtle hover, in-context suggestions,
inline pills, suggestions content quality) is implemented, deployed,
and verified live against his real data. Known non-goals: Town's
People/CRM section (People-lite phase 2, parked), audio briefings /
doc & deck creation (content-creation products outside dashboard
parity), G5 drafts (parked by decision).

Post-U5 statuses (2026-07-18 midday): U1 reconciliation CONFIRMED live
(overlay converged to []); U2 Recents CONFIRMED live (single-line rows,
real data); U3/U4 deployed, below-fold visual check pending the idle
gate. Remaining backlog: composer icon cluster (low), self-refreshing
relative timestamps + skeletons (low-medium), Town re-read for new
material after U5 verification.

## Final verification + competitive assessment (2026-07-18, post-U6)

Visual pass completed live: U5 View-schedule chip + dialog (real
Today/Tomorrow data, All-day badges), U4 quick-action chips, U2 Recents
with outcomes, U6 suggestions (initials avatars, person/deadline-led),
NTK pills/stack. U3 needs-input could not render (zero blocked sessions
— correct behavior). Known nits: cron-doc NTK summaries can leak raw prompt
markdown (cosmetic, candidate for a later summary-sanitizer).
CORRECTION on Escape (post cross-session evidence): the earlier
"SDK Dialog ignores Escape" finding is most likely a test-harness
artifact — synthetic (CGEvent) Escape presses do not dispatch into the
renderer (the origin session independently traced and retracted the
same observation; my same-batch background scroll also no-opped,
consistent with modal-open, not app behavior). Host X + backdrop are
VERIFIED working live. One physical Escape press from Tom settles it;
do NOT ship custom close buttons/Escape hooks (origin branch ffe6bee
rejected on this evidence — host X demonstrably works).

Loop verdict: feature-competitive with Town on every core surface for
this stack, ahead on multi-account Gmail + Telegram ambience, undo
forgiveness, and approvals depth; behind on accumulated thread memory
(time, not capability), People/auto-CRM (user-parked T24), and Town's
content-creation chips (platform-philosophy difference). Loop closed.

## Graded backlog (from live Town home)

- **Recents card** — Town interleaves session rows (chat icon, title-case
  names) with routine-outcome one-liners (doc icon, sentence summaries:
  "Inbox scanned … no new deals found"), right-aligned relative time,
  "Show more". We have session.list RPC + /cron-runs. High value: makes
  the home feel alive. → iteration 2.
- **"Needs input" task sub-state** — Town task rows show an accent-colored
  "Needs input: <what the agent is waiting on>" line when the linked
  session is blocked. We already track approvals/clarifies (town-approvals)
  and kanban cards carry session ids — correlate and render. High value:
  closes the loop between Tasks and Approvals. → iteration 3 candidate.
- **Quick-action chips row** — pill buttons under Suggestions ("Audio
  briefings", "Start a document", "Create a deck", …). Ours would prefill
  the composer with canned prompts / open panes. Medium value, very cheap.
- **View schedule** — "Friday · View schedule" chip next to the greeting
  opening a schedule panel. Calendar lane is live now (next_event +
  events); a slide-over listing today's events unblocks the deferred
  schedule panel in lite form. Medium-high value, medium scope.
- **Composer icon cluster** — decorative depth (+, people, clock, shield,
  mic). Low value for us; skip unless bored.
- **Fit-and-finish leftovers** — relative timestamps that self-refresh;
  skeleton loading states. Low-medium; batch late.

## Standing verification drill

Gateway route changes need BOTH restarts (kickstart local.hermes-serve +
quit/reopen the app — embedded gateway serves the desktop REST proxy).
Interaction checks run via computer use (quit Magnet first if clicking).
Manual cron fires over waiting for schedules.
