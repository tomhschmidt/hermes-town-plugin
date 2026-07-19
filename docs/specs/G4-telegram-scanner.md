# G4 — Telegram scanner (REWRITTEN 2026-07-18: script shape)

*Original plugin-scanner spec replaced after the Hermes-native alignment
review. G4 is built as component **C** of
`docs/specs/G6R-native-restructure.md` (`town-telegram-scan.py`, a
`no_agent` cron script) during phase G6R-c.*

Retained decisions from the original spec that still bind:
- Source the collection from `~/.hermes/scripts/telegram-action-items-v2.py`
  — import it if a minimal refactor keeps the daily cron's behavior
  byte-identical; otherwise mirror minimally with a source-of-truth
  comment. Never shell out per cycle; never parse its prose summary.
- One candidate per active thread (last 48h, cap 15): headline
  "<chat name>: <last message gist ≤60>", summary = up to 3 recent gists,
  source `telegram:<chat_id>`, created_at = last message ts, hash on
  (chat_id, last message id) so new activity resurfaces a thread.
- Per-chat watermarks (script-local state), agent-exhaust threads excluded
  (mirror the script's own distinction).
- The daily telegram-action-items cron must keep running unchanged —
  verified after deploy.
- Non-goals unchanged: no sending/replying, no archiving, no per-message
  candidates, no desktop changes.
