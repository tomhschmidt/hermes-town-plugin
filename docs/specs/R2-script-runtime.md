# R2 — Scripts run on the managed Python (Gmail labeler outage)

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`.*

## Problem (observed in production)

- The Gmail LLM labeler has applied no LLM labels for 15 days. Every scan
  that needs classification exits 4 with
  `No module named 'pydantic_core._pydantic_core'`; earlier runs exited 3
  with `No module named 'ruamel'`. The curator also failed 59 runs in a row
  (≈30 h of frozen Need-to-Know).
- Root cause: `town-gmail-scan.py`, `town-calendar-scan.py`, and
  `town-curate.py` re-exec themselves (`os.execv`) into
  `$HERMES_HOME/hermes-agent/.venv/bin/python` — a legacy Python 3.11 venv
  frozen since the September `hermes update` moved Hermes onto
  package-manager (PM) environments (Python 3.14). Inside `call_llm`,
  `hermes_bootstrap` activates the PM's 3.14 site-packages into that 3.11
  process, so the first compiled import (pydantic_core) fails.
- Failed classification still advances the Gmail watermarks
  (`watermarks_advanced: true`, skip reason `classifier_error`), so those
  messages are never labeled.
- The curator's health item shows `last_error[:77]`, which is entirely
  plugin-loader noise ("Plugin x: unknown kind 'user'…"), hiding the real
  error.

`gateway/scripts/telegram-action-items-v2.py` (synced from the live copy)
already shows the correct pattern: put the hermes-agent checkout on
`sys.path`, then `import hermes_bootstrap` BEFORE agent imports, and never
force `.venv`.

## Scope

`gateway/scripts/town-gmail-scan.py`, `town-calendar-scan.py`,
`town-curate.py`, `town-telegram-scan.py` (audit only; change if it shares
the bug), the `_scan_cron` health-detail code in
`gateway/town-feed/dashboard/plugin_api.py`, `gateway/scripts/README.md`,
new tests. Do not touch other logic in these files (R1b/R3/R4 own it).

## Hard requirements

1. No script under `gateway/scripts/` references `.venv` or a hardcoded
   `hermes-agent/venv` interpreter, and none calls `os.execv` into one.
2. Each script that imports Hermes/agent modules bootstraps exactly like
   the digest: hermes-agent checkout on `sys.path`, `import
   hermes_bootstrap`, then agent imports. Cron (which already runs scripts
   on the PM interpreter) must execute them in-process with no re-exec.
3. A manual run under a foreign interpreter (e.g. a pyenv `python3`) either
   works or fails fast with ONE clear stderr line naming the command to use
   instead. If you choose to re-exec, resolve the PM interpreter through
   hermes-agent's own API (investigate `~/.hermes/hermes-agent` — e.g.
   `pm/environments.py`, `hermes_cli/_launchers.py`) — never a hardcoded
   path — and guard against loops.
4. Gmail scan: when the classifier fails for a batch, watermarks for the
   messages that were not classified do NOT advance, so the next run
   retries them; `watermarks_advanced` in stdout reports the truth.
   Successful classification advances them exactly as today. No retroactive
   backfill: on deploy, whatever the watermark is now stays the starting
   point. Label REMOVAL behavior is unchanged.
5. Exit codes and the stdout JSON contracts in `gateway/scripts/README.md`
   are unchanged (update the README only to document requirement 3 and the
   watermark-hold behavior).
6. Health detail: the `town-*` failure item's detail is the most specific
   error line — prefer the last stderr line starting with `town-`, else the
   last non-noise line — stripping known loader noise (`Plugin <name>:
   unknown kind …`, `providers.<x>: unknown config keys …`, `Script exited
   with code N`, `stderr:`/`stdout:` markers, and the stdout JSON blob).
   Keep the 80-char cap. Example: an error string shaped like the live one
   yields `labeler: classifier failed: … No module named
   'pydantic_core._pydantic_core'` (truncated to 80).

## Required tests (`gateway/tests/test_r2_runtime.py`)

- Static: no `gateway/scripts/*.py` contains `.venv` or `execv(` into a
  venv path (string scan is fine).
- Importing each changed script via `support.load_script` with `os.execv`
  patched to raise does not call it (run under the managed interpreter).
- Gmail watermark hold: with the classifier stubbed to fail, state
  watermarks after the run equal those before for unclassified messages and
  stdout has `watermarks_advanced: false`; with it succeeding they advance.
  Use the module's existing seams/fakes (no network); state paths → tmp.
- Health detail extraction: synthetic stderr shaped like the live failure
  (loader noise lines + `town-gmail-scan: labeler: classifier failed: …`)
  → the `town-gmail-scan: …` line wins; pure-noise input falls back to the
  status string; result ≤ 80 chars.
