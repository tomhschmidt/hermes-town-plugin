# U9 — Board identity rev: two-column layout, whitespace, type, color

*2026-07-18, from Tom's review: "whitespace and layout leaves much to be
desired, stock typefaces + colors, mimic Town's double-column layout
(stacked right column, not tabs)." Single in-scope file:
src/town-home/plugin.tsx. Conventions: docs/specs/README.md binds —
ESPECIALLY the Tailwind subset in hard rule 2 (NO bracket-arbitrary
utilities, NO named group variants; responsive prefixes sm:/lg: of
STANDARD utilities are proven live in this file's header). Inline
`style` is allowed and REQUIRED where the subset can't express a value
(fontFamily, color-mix tokens).*

## 1. Two-column board (Town's structure)

Rework the page shell (line ~3151: `ScrollArea > div.mx-auto.max-w-6xl
.flex-col.gap-4.p-6`):

- Shell: `mx-auto w-full max-w-7xl px-8 py-8`, then
  `flex flex-col gap-8 lg:flex-row lg:items-start`.
- MAIN column: `min-w-0 flex-1 flex flex-col gap-6` — greeting header,
  composer, then main-column cards.
- SIDE rail: `w-full lg:w-96 shrink-0 flex flex-col gap-5` — side cards
  stacked, always visible (this is Town's right column; the host tab
  sidebar is untouched and out of scope).
- Fixed column assignment: MAIN = tasks, recents, briefing; SIDE =
  need-to-know, suggestions, routines. The Customize dialog's single
  `cardOrder` is UNCHANGED (no pref schema change): render each column
  by filtering cardOrder to that column's members, preserving relative
  order. Visibility toggles keep working per card. The Customize dialog
  copy does not need column labels this round.

## 2. Whitespace

- Cards: `rounded-xl`, padding `p-5`; title block → content spacing
  `mt-4` consistently; side-rail cards may use `p-4`.
- Empty states: replace tall voids (`min-h-24`+) with quiet `py-6`
  single-line states; the Tasks "Board clear" empty state must not
  exceed ~96px total height.
- Composer: textarea min height via standard utility (e.g. `min-h-20`);
  bottom bar padding `pt-3`; container `rounded-xl`.
- Row rhythm stays `py-3`; section header → first row gets `mt-1`
  breathing room via the title-block rule above.

## 3. Typography

- Greeting h1: inline
  `style={{fontFamily: 'ui-serif, "Iowan Old Style", Georgia, serif'}}`,
  classes `text-3xl font-medium tracking-tight` (serif display — Town's
  warmth without font loading; system New York face on macOS).
- Date + context lines: unchanged sans, but group them `mt-2` under the
  h1 with `text-sm`.
- EVERY SectionCard title: `text-sm font-medium text-(--ui-text-secondary)`
  (quiet, consistent — audit all cards; some titles currently render
  primary-color).
- Meta/timestamps stay `text-xs text-(--ui-text-tertiary)`.
- No webfonts, no new deps, nothing else changes face.

## 4. Color (theme-derived, no hardcoded hues)

Define once, on the board root div, via inline style custom properties:

```
style={{
  ['--town-tint']: 'color-mix(in srgb, var(--ui-accent) 10%, transparent)',
  ['--town-surface']: 'color-mix(in srgb, var(--ui-text-primary) 4%, transparent)',
}}
```

(TypeScript: cast the object `as React.CSSProperties` — custom props
need the cast.)

- SIDE-rail cards: `style={{background: 'var(--town-surface)'}}` on the
  card container (soft panel contrast vs the main column, adapts to
  light/dark since it derives from theme text color).
- Leading glyph chips (Suggestions icons, Recents icons, NTK source
  pill icons where a circular glyph exists): circular container gets
  `style={{background: 'var(--town-tint)'}}` and the Codicon gets
  `text-(--ui-accent)` — restrained accent pops, Town-style.
- The greeting accent bar (profile color) stays. Nothing else gains
  color; buttons/badges/pills unchanged. If color-mix is unsupported it
  must degrade to no background (verify no crash path).

## DoD

Unpiped `npm run check` passes; `npm run build` passes; no files
changed outside src/town-home/plugin.tsx; NO git commits (orchestrator
owns them); report any spec assumption that turned out wrong (especially
which card titles were inconsistent and any subset utility you needed
but found missing). Do NOT deploy or restart anything.
