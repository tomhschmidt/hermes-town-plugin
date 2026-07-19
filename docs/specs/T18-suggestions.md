# T18 — Suggestions widget (depends on T14's composer)

## Goal

Town shows an auto-refreshing "Suggestions" rail of contextual prompt chips.
Ours: the town-feed curator already scans sessions/cron/memory with LLM
ranking — have it also emit 3–5 suggested prompts, and render them on /town
as chips that prefill the T14 composer.

## Gateway changes

1. In `run_curator`'s LLM pass (`_llm_rank` cycle — NOT the heuristic
   `POST /curate` path), additionally generate `suggestions`: 3–5 objects
   `{id, label, prompt, kind}` where `label` is chip text ≤ 60 chars
   ("Follow up on the Kasa smart-plug failure"), `prompt` is the full
   ready-to-send task prompt, `kind` ∈ `followup | digest | maintenance`
   (best-effort classification). Ground them in the same scanned candidates
   the feed uses; forbid suggestions about the curator/scheduler itself
   (reuse the self-exclusion rules). Persist in the state file as a
   `suggestions` list with a `generated_at` ISO timestamp; overwrite wholesale
   each LLM run (no dismissal state for suggestions in this round). Heuristic
   runs leave the previous suggestions untouched.
2. `GET /feed` response gains `suggestions` + `suggestions_generated_at`.
   Backward compatibility: old state files without the key load fine and
   yield `[]`.

## Desktop changes

3. `src/town-feed/plugin.tsx`: bridge state exposes the new fields
   (normalize defensively).
4. `src/town-home/plugin.tsx`: new `SuggestionsCard` on /town rendering up to
   5 chips (SDK `Button`/`Badge` style, icon per `kind` if cheap — text-only
   is fine). Header "SUGGESTIONS · <relative generated_at>". Clicking a chip
   prefills the T14 composer with the suggestion's `prompt` (module-level
   store/atom shared within the town-home bundle; composer focuses, mode
   stays Task) — it must NOT auto-submit. Empty/missing suggestions → card
   hidden entirely (no empty shell).
5. Placement: directly below the Need to Know card on /town.

## In scope

`gateway/town-feed/dashboard/plugin_api.py`, `src/town-feed/plugin.tsx`,
`src/town-home/plugin.tsx`.

## Out of scope

Auto-refresh cadence changes (piggybacks the existing 30-min cycle), a
standalone suggestions pane, suggestion dismissal, Town's "Checking…" live
spinner.

## Acceptance checks

- After one scheduled (LLM) curator run, `/feed` carries 3–5 grounded
  suggestions with sane labels; heuristic refresh does not clobber them.
- Chips render on /town; clicking one puts the full prompt in the composer
  (focused, not submitted); with zero suggestions the card is absent.
- Old `feed-state.json` without `suggestions` loads cleanly.
- `npm run check` + `npm run build` + gateway py_compile pass.
