# R1b — Telegram: stop fake and already-handled action items

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`. Consumes the
R1a daemon contract; must degrade gracefully when the new fields are
absent (old daemon).*

## Problem (observed in production, anonymized)

Two surfaces turn Telegram into action items: the daily digest
(`gateway/scripts/telegram-action-items-v2.py`, LLM summary → Telegram +
Reminders + an artifact the morning briefing reads) and the dashboard
scanner (`gateway/scripts/town-telegram-scan.py` → curator → Need to Know).
Both produce:

- **Misattribution.** In a group, Bob asks the group a question and Carol
  answers Bob with "you should resend *your* message" — the operator never
  posted, yet the dashboard says "ping falls to you". Another group's
  "we're so close" (Dave to Erin) became "Dave wants your call". A
  broadcast channel's market recap ranked as an action item.
- **Already handled.** The operator answered "Yes" 16 s after a question;
  the digest still flagged it 3 h later. "PRL", "11:35", "ok" replies
  vanished. A same-minute reply rendered ABOVE the question it answered.
  "Reply in the next hour" was flagged 14 h later.

Root causes:
1. Both scripts drop messages with ≤ 5 characters of text
   (`len(text) > 5`) and all non-text messages, so short answers and
   photo/voice replies disappear.
2. The digest sorts by a minute-resolution date STRING; the daemon returns
   newest-first, and a stable sort keeps same-minute messages reversed.
3. The prompts know only a display name: no user id/@username, no
   DM/group/channel distinction, no reply-to/mention signal, and no current
   time. Group chatter is indistinguishable from asks to the operator.
4. The digest groups chats by TITLE (same-title chats merge) and caps
   messages at 500 chars; the scanner compares display names to decide
   `user_spoke_last`, sorts message ids as strings, and advances watermarks
   for chats it never reports (only 15 candidates are reported).
5. The curator's rules ("write in second person… what the user must do")
   push every thread onto the operator; nothing says "not addressed to the
   user → not an item".

## Scope

`gateway/scripts/town-telegram-scan.py`, `gateway/scripts/telegram-action-items-v2.py`,
the telegram-specific ranking text in `gateway/town-feed/dashboard/plugin_api.py`
(`_RANKING_RULES` / telegram payload fields in `_llm_rank` only), a shared
pure helper module if useful (e.g. `gateway/scripts/town_telegram_common.py`
deployed alongside the scripts — document it in `gateway/scripts/README.md`),
tests. Not in scope: daemon (R1a), item retention/dedupe (R4), script
bootstrap headers (R2).

## Hard requirements

**Identity & chat typing**
1. Operator identity is resolved at runtime from the daemon `/me`
   (`user_id`, `first_name`, `last_name`, `username`), falling back to the
   existing constants/env (`TOWN_OWNER_NAME`) only if `/me` fails. No new
   hardcoded identity.
2. Chat type comes from `/chats` `type` when present; otherwise inferred
   from the id (`> 0` private; `<= -1000000000000` supergroup/channel —
   treat as group unless proven channel; other negatives basic group).
   Channels (`type == channel`, or messages carrying `sender_chat_id` with
   no `sender_user_id` in a non-private chat) and bot DMs (`is_bot`) are
   excluded from both surfaces.
3. A message is the operator's when `is_outgoing` is true or
   `sender_user_id == me.user_id` (never by display-name comparison).

**Message fidelity**
4. No length filter. Non-text messages render as `[photo]`, `[voice]`,
   `[sticker]`, … plus caption when present. Messages with neither text
   nor a known content type are skipped.
5. Within a chat, order is `(date, int(id))` ascending — exact
   timestamps, numeric ids. The digest groups by chat ID, labeling with
   the title.
6. Rendered lines carry addressing markers: the operator's lines are
   prefixed `[YOU]`; a reply shows `↩ <replied-to sender or "you">`; a
   message mentioning the operator (by user id or @username) or naming
   them by first name as a whole word shows `@you`. Times are rendered in
   the operator's local timezone (system tz until S1's profile exists).

**Addressing policy (deterministic, before any LLM)**
7. DM: always eligible.
8. Group: eligible only if, within the window, some message by someone
   else replies to the operator, mentions the operator (id/@username), or
   names the operator's first name as a whole word — OR the operator made a
   commitment there ("I'll", "I will", "will send/check/do", "let me") that
   nobody has since acknowledged. Groups with none of these are dropped
   from the digest input and emit no scanner candidate.
9. Resolution signal: for each eligible chat compute
   `last_addressed_at` (newest message by someone else that is a DM message
   or addresses the operator per 8) and `operator_last_spoke_at`. If the
   operator spoke after `last_addressed_at`, the chat is marked
   `operator_replied_after_ask: true` (digest header + scanner field). The
   digest prompt must instruct: such chats are NOT action items unless the
   operator's own later message makes a still-open promise.

**Digest prompt**
10. The prompt states: who the operator is (name, @username, user id),
    the current local date/time and timezone, the marker legend, and these
    rules: only asks/promises involving the operator; a message addressed
    to someone else is never the operator's item; resolved or superseded
    asks are not items; time-boxed asks whose window has passed are
    dropped or flagged as missed (never presented as current); quote the
    ask briefly. Chat headers: `=== <title> [DM with <name>] ===` or
    `=== <title> [group] ===`, plus `operator replied after last ask` when
    true.
11. Output format, the `🔵` bullet contract (Reminders parser), the
    `✅ No action items.` sentinel, artifact persistence, `--no-send`,
    Markdown fallback, timeouts, and exit behavior are unchanged.
    Delivery-related code paths are untouched.

**Scanner**
12. Candidates add `chat_type`, `addressed_to_user` (bool, per 8),
    `operator_replied_after_ask` (bool, per 9), and keep `chat_name`,
    `last_sender`, `user_spoke_last` (now id-based). The thread `body`
    uses the same markers as 6 and its header names the operator as
    "you" (`[chat: <title> (group); you are <name>]`).
13. Watermarks advance ONLY for chats whose candidate was reported in this
    run (or that produced no candidate because they were ineligible). A
    chat cut by `MAX_CANDIDATES` keeps its old watermark so it is reported
    next run.

**Curator rules (plugin_api.py, text + payload only)**
14. `_llm_rank` passes `chat_type`, `addressed_to_user`,
    `operator_replied_after_ask` for telegram rows. `_RANKING_RULES` adds:
    a group thread that is not `addressed_to_user` is never phrased as the
    user's task (score it below 20 or omit it); when
    `operator_replied_after_ask` is true, do not present the original ask
    as open — at most an open loop if the user's reply promised something.

## Required tests (`gateway/tests/test_r1b_telegram.py`)

Synthetic chats built as daemon-shaped dicts (with and without R1a fields).
- Group, Bob asks group, Carol replies to Bob, operator silent → not
  eligible; no candidate; not in digest input.
- Group, Bob replies to operator's message → eligible, `@you`/`↩ you`
  markers rendered.
- Group naming "Alice" (operator first name) as a word → eligible; "Alicex"
  → not.
- DM: question at 10:00:05, operator "Yes" at 10:00:21 (same minute,
  daemon newest-first) → rendered order is question then `[YOU] Yes`;
  `operator_replied_after_ask` true; "Yes" not dropped.
- Photo reply with caption → `[photo] caption`.
- Channel (type channel / sender_chat_id only) and bot DM → excluded.
- Old-daemon payload (no new fields) still works: identity via fallback,
  type via id sign, `user_spoke_last` via `sender_user_id == me.user_id`.
- Digest groups two chats with the same title separately.
- Digest prompt contains the current local time string, operator
  @username and user id, and the marker legend (assert on the built prompt
  text; inject `now` and `/me`; no LLM call).
- Scanner watermark: 20 eligible chats → 15 reported, the other 5 keep
  their previous watermarks.
- `_llm_rank` payload for a telegram candidate includes the three new
  fields; `_RANKING_RULES` mentions `addressed_to_user` and
  `operator_replied_after_ask`.
