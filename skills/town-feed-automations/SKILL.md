---
name: town-feed-automations
description: "Install and schedule the four script-backed Town Feed scanners and curator."
version: 1.0.0
author: Hermes Town
license: MIT
platforms: [macos, linux]
required_credential_files:
  - path: google_token.json
    description: Primary Google OAuth2 token; additional accounts use google-accounts/<account>/google_token.json
metadata:
  hermes:
    tags: [Town Feed, Gmail, Calendar, Telegram, Automation]
---

# Town Feed automations

This single skill packages the complete Town Feed sensing and curation set, so the four scripts are installed and versioned as one unit. Install all four scripts together, then opt into the four ordinary Hermes cron jobs. The skill deliberately has no `metadata.hermes.blueprint`: blueprint frontmatter cannot carry script jobs, while the Town Feed gateway plugin registers script-backed suggestions through the native Suggested Cron Jobs surface.

## Jobs

| Job | Schedule | Installed script | Contract and safety envelope |
|---|---|---|---|
| `town-gmail-scan` | every 10m | `~/.hermes/scripts/town-gmail-scan.py` | Reads Gmail, batches classification through `auxiliary.town_inbox`, emits candidates, and may add configured `Town/` labels. It removes labels only for the awaiting-reply lifecycle, locks its own state, and refreshes Google credentials in memory without writing token files. |
| `town-telegram-scan` | every 10m | `~/.hermes/scripts/town-telegram-scan.py` | Reads the Telegram Bot API (token from `~/.hermes/.env`), emits at most 15 changed thread candidates from a 48-hour window, and advances per-chat watermarks under a file lock. It does not send Telegram messages or mutate chats. |
| `town-calendar-scan` | every 30m | `~/.hermes/scripts/town-calendar-scan.py` | Reads the next 48 hours from each primary calendar with a calendar-scoped token, capped at 20 events per account. It never writes calendars or credentials. This automation is dormant in the shipping configuration: without a token carrying `calendar` or `calendar.readonly` scope it exits successfully with empty stdout. |
| `town-feed-curate` | every 30m | `~/.hermes/scripts/town-curate.py` | Loads the installed Town Feed plugin, ranks scanner and host activity through `auxiliary.town_curator`, and persists the Need-to-Know feed under the plugin's state lock. If the LLM fails, the plugin may persist its heuristic fallback and the script exits 4. |

All jobs use `no_agent: true` and `deliver: local`. Scanner JSON is retained in native cron output for the curator, while failures are surfaced by the Town Feed cron-health ingestion path. Pausing a job with `hermes cron pause <job>` is the immediate kill switch.

## Configuration

Set model routing and Town Feed behavior in `~/.hermes/config.yaml`:

```yaml
auxiliary:
  town_inbox:
    provider: ""  # empty selects Hermes automatic/main-model routing
    model: ""
  town_curator:
    provider: ""
    model: ""

plugins:
  entries:
    town-feed:
      labeling_enabled: true
      label_confidence_min: 0.6
      taxonomy: {}
```

`auxiliary.town_inbox` controls Gmail classification; `auxiliary.town_curator` controls ranking. `plugins.entries.town-feed.labeling_enabled` is the Gmail-labeling kill switch. `label_confidence_min` must be between 0 and 1. `taxonomy` adds or overrides label descriptions; every key must begin with `Town/` and every description must be non-empty.

Google credentials are owned by the `google-workspace` skill. The scanners discover `~/.hermes/google_token.json` plus zero or more `~/.hermes/google-accounts/<account>/google_token.json` files. Gmail requires Gmail scopes; the dormant calendar scanner activates account by account only after those tokens gain Calendar scope. Telegram also requires the local Telegram daemon and `TELEGRAM_BOT_TOKEN` in the process environment or `~/.hermes/.env`.

## Install

From the root of the Hermes Town checkout, deploy the four scripts atomically before accepting or creating any job:

```bash
install -d -m 700 ~/.hermes/scripts
install -m 700 \
  gateway/scripts/town-gmail-scan.py \
  gateway/scripts/town-telegram-scan.py \
  gateway/scripts/town-calendar-scan.py \
  gateway/scripts/town-curate.py \
  ~/.hermes/scripts/
```

Enable the Town Feed gateway plugin. Its `register()` function offers any missing jobs as integration suggestions; review and accept them with `/suggestions`. Existing jobs with the exact names below suppress their matching suggestions, and accepting a suggestion creates the same ordinary cron entry as the CLI. Hermes caps the pending queue at five suggestions; this four-job set fits in an empty queue, but if fewer than four slots are free, resolve existing suggestions and reload the plugin so the remaining Town Feed jobs can be offered.

For a manual install, create the four jobs directly:

```bash
hermes cron create "every 10m" --name town-gmail-scan --script town-gmail-scan.py --no-agent --deliver local
hermes cron create "every 10m" --name town-telegram-scan --script town-telegram-scan.py --no-agent --deliver local
hermes cron create "every 30m" --name town-calendar-scan --script town-calendar-scan.py --no-agent --deliver local
hermes cron create "every 30m" --name town-feed-curate --script town-curate.py --no-agent --deliver local
```

Do not create both accepted suggestions and manual jobs. Confirm the installed set with `hermes cron list`; use `hermes cron run <job>` for an explicit test rather than waiting for a schedule.

## Output and state

The scripts write machine-readable JSON to stdout and diagnostics to stderr. Gmail always emits its envelope; Telegram and Calendar leave stdout empty on a successful quiet scan. The curator always emits its result object. Full schemas, exit codes, and state files are inventoried in `gateway/scripts/README.md` in the source checkout.
