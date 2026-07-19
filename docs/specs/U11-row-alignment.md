# U11 — Row alignment & padding polish (hover geometry, chip centering)

*2026-07-18, from Tom's review + live audit. Confirmed artifacts:
(a) Recents/NTK first-row hover highlight is SMALLER than the row —
`py-3 first:pt-0` makes spacing and highlight share padding, so the
first row's hover box is compressed and clips its icon; right-aligned
meta can sit at the highlight's edge; (b) the Suggestions icon-variant
chip (e.g. Weekly Leadership Sync row) renders its Codicon off-center —
U9 wrapped the icon in an extra inline span inside the flex circle;
(c) NTK side-rail rows show no visible hover at all on the tinted
surface. Conventions: docs/specs/README.md binds — Tailwind subset rule
is critical; `-mx-2`, `px-2`, `py-2.5`, `rounded-lg` and
`hover:bg-(--ui-row-hover-background)` are all PROVEN live in these
bundles; do NOT invent new paren-var class strings (a new var name in a
class is an uncompiled class — silently dead; new vars go through
inline `style` only).*

## In scope

`src/town-home/plugin.tsx` and `src/town-feed/plugin.tsx` only.

## 1. Unified list-row geometry (Town's borderless model)

For EVERY list row in: town-home NTK card, Recents, Suggestions,
Routines, sessions-fallback rows, and the town-feed feed-page item
rows:

- Row container: `rounded-lg -mx-2 px-2 py-2.5` + the row's existing
  flex classes + `transition-colors duration-150
  hover:bg-(--ui-row-hover-background)` (interactive rows only; static
  rows get the same geometry without hover). The `-mx-2` bleed makes
  the highlight extend past the content into the card gutter so icons,
  text, and right-aligned meta all sit INSIDE the highlight with
  breathing room.
- REMOVE all inter-row `border-t border-(--ui-stroke-secondary)` and
  every `first:border-t-0` / `first:pt-0` in these lists — separation
  is spacing-only (rows are self-padded; if a list needs slightly more
  rhythm, the list container may use `space-y-0.5`). Do NOT touch
  borders that are not row separators (card borders, the greeting
  header's border-b, the composer, dialogs, the undo strip).
- Every row must fully contain its content: icon chip, text, and
  right-aligned timestamp/meta inside the hover bounds at all row
  positions (first, middle, last).
- The NTK hover-reveal actions row (Dismiss / Turn into task) and any
  sub-rows keep working — they live INSIDE the row container so the
  highlight covers them too.

## 2. Glyph-chip centering

Every circular glyph chip (Suggestions initials/icon chips size-6,
Recents chips size-6, NTK source-pill chips size-4):

- The Codicon must be a DIRECT child of the flex circle — remove U9's
  inner wrapper spans. Icon color moves onto the circle element's
  className (`text-(--ui-accent)` for icon variants; initials variants
  keep their existing text classes on the circle).
- Add `leading-none` to the circle so the glyph centers optically.
- Initials chips: ensure the two-letter text is centered
  (`leading-none` + existing text-xs font-medium).

## 3. Side-rail hover visibility

After (1), verify the hover class actually reads on the tinted
side-rail cards (`--town-surface` background from U9). Since
`hover:bg-(--ui-row-hover-background)` is the only proven hover class:
if the orchestrator's post-deploy visual check finds it invisible on
the tint, the FALLBACK (decided now, do not invent alternatives) is to
remove the `--town-surface` inline background from SectionCard's
sideRail variant entirely — rail cards keep their border like main
cards, and hover contrast is restored. Implement the primary change
only; the orchestrator applies the fallback after visual verification
if needed.

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files
changed outside the two in-scope files; NO git commits; report every
row/chip site you changed (file + component) and any site that did not
fit the primitive (do not force dialogs/strips/pills into it — flag
them instead). Do NOT deploy or restart anything.
