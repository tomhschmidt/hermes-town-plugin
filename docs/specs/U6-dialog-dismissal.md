# U6 — Dialog dismissal corrective (host X is decorative; Escape eaten)

*2026-07-18, polish-loop iteration 6 (emergency corrective). Live
verification of U5's Schedule dialog: backdrop click dismisses, but the
X button and Escape do NOT — and this reproduces on the run-history
dialog, i.e. it affects EVERY dialog in the suite and predates P6 (the
P6 boolean-atom fix was correct but fixed a different layer).*

## Root cause (X) — HOST BUG, source-proven

`apps/desktop/src/components/ui/dialog.tsx` renders the close button as
`<DialogPrimitive.Close asChild><Tip label…><Button…/></Tip></…>`.
Radix `Close asChild` merges its `onClick` (and ref) onto the CHILD —
`Tip`. But `Tip` (`components/ui/tooltip.tsx:80`) destructures
`{ label, children, ...props }` and spreads `...props` onto
**TooltipContent**, not onto the child button. The composed onClick
lands on the floating tooltip label; the X never gets a click handler.
Hover/tooltip work (pointer events fine), click is a no-op — app-wide.
Upstream draft: docs/upstream-dialog-close-tip-issue.md (do NOT post
without Tom's explicit go).

Escape: eaten before Radix's handler sees it (exact host listener not
pinned down; composer esc-cancel provably defers to open dialogs, so it
is something else). The fix below does not depend on identifying it.

## Fix (both bundles; five dialogs)

town-home (Customize, NewRoutine, RunHistory, Schedule) and
town-routines (RunHistory):

1. Pass `showCloseButton={false}` to `DialogContent`.
2. Render OUR OWN close button as the last child of `DialogContent`:
   `<Button aria-label="Close" className="absolute right-2.5 top-2.5
   z-20 text-(--ui-text-tertiary)" size="icon-xs" variant="ghost"
   onClick={<setter false>}>` with `<Codicon name="close"
   size="0.875rem" />` (codicon `close` is the core VS Code chrome-close
   glyph; report if it renders empty — fallback is a text-variant
   "Close" Button). The onClick calls the dialog's boolean atom setter
   directly (imperative module-level atom — no Tip wrapper, no Radix
   Close).
3. Add ONE shared hook per bundle:
   `function useDialogEscape(open: boolean, close: () => void)` —
   `useEffect` gated on `open`; adds `document.addEventListener('keydown',
   handler, { capture: true })`; handler: `if (event.key === 'Escape') {
   event.preventDefault(); close() }`; cleanup removes with the same
   capture flag. Call it in each dialog component with that dialog's
   open value + a module-level `close` function (e.g.
   `closeScheduleDialog`). Capture phase is deliberate — it beats
   whichever host listener eats the bubble/late-capture path.
4. Backdrop dismissal and the `onOpenChange` boolean-atom wiring stay
   exactly as they are (proven working).

## DoD

Unpiped `npm run check` passes; `npm run build` passes; files changed
ONLY: src/town-home/plugin.tsx, src/town-routines/plugin.tsx. Do NOT
git commit or push (specs/README rule). Do NOT deploy or restart.
Report which dialogs you touched and any spec assumption that turned
out wrong.
