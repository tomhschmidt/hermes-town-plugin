# R1a — TDLib daemon: move into the repo + richer message contract

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`.*

## Problem

The Telegram user-session daemon (HTTP on `127.0.0.1:18784`, consumed by
`town-telegram-scan.py` and `telegram-action-items-v2.py`) lives outside
this project in a legacy OpenClaw workspace, under a LaunchAgent named for
OpenClaw. Its `/messages` payload keeps only `id`, `date`, `text` (plain
`messageText` only), `sender_user_id`, `sender_name`, and `/chats` only
`id`/`title`. Consumers therefore cannot tell who a message replies to,
whether the operator sent it, whether it @mentions anyone, whether a chat
is a DM, group, or channel, or that a photo/voice/sticker reply exists.
This is the data-level root cause of "a message addressed to someone else
became my action item" and "an item I'd already answered".

The verbatim current source is now at
`gateway/tdlib-daemon/telegram-tdlib-daemon.py` (the baseline for this
spec). Its TDLib session lives in `~/.tdlib_daemon/` (absolute path,
independent of the script location) — moving the script is safe.

## Scope

`gateway/tdlib-daemon/` (daemon source, README, LaunchAgent template
`local.hermes-tdlib-daemon.example.plist` using `__HOME__`-style
placeholders, no real paths), tests. Do not change consumers (R1b).

## Hard requirements

1. **Backward compatible.** Every existing endpoint, query parameter, and
   response field keeps its name, type, and semantics (`text` stays the
   `messageText` text only; empty for media). New data is additive fields.
   Port stays 18784, bind stays `127.0.0.1`.
2. **`/me`** adds `username` (first active username, or `""`) and
   `usernames` (list).
3. **`/chats`** entries add `type` ∈ {`private`, `group`, `supergroup`,
   `channel`, `secret`, `unknown`} (from TDLib `chat.type`; a supergroup
   with `is_channel` is `channel`), `member_count` when cheaply available
   (omit otherwise), and for private chats `user_id` and `is_bot` (via a
   cached `getUser`). Same for `/muted-chats` entries (additive).
4. **`/messages`** entries add:
   - `is_outgoing` (bool, TDLib `is_outgoing`);
   - `reply_to_message_id` (int or omitted) from `reply_to` of type
     `messageReplyToMessage` in the same chat; plus
     `reply_to_sender_user_id` / `reply_to_sender_name` when resolvable —
     first from messages in the same response, else one `getMessage` call
     (cached per request; never more than 20 extra calls per request);
   - `content_type`: short kind (`text`, `photo`, `video`, `voice`,
     `video_note`, `sticker`, `animation`, `document`, `audio`, `poll`,
     `location`, `contact`, `other`);
   - `caption` (str) for media with a caption;
   - `mention_user_ids` (ints, from `textEntityTypeMentionName`) and
     `mention_usernames` (lowercased, without `@`, from
     `textEntityTypeMention` spans) for both text and captions;
   - `sender_chat_id` when the sender is a chat (channel posts / anonymous
     admins), so consumers can tell channel posts from users.
5. **No hidden side effects added**: the existing sync-state update on
   `/messages` without `from_message_id` is preserved as-is (consumers pass
   `from_message_id=0`).
6. **Pure helpers.** Message/chat shaping lives in pure functions (input:
   TDLib dicts + a lookup callable; output: response dicts) so it is unit
   testable without TDLib. Importing the module for tests must not require
   `tdjson` or create `~/.tdlib_daemon` (guard the import and the
   directory/log setup behind `main()`/`cmd_*`).
7. **Runtime location contract** (documented in `gateway/tdlib-daemon/README.md`):
   deployed script path `$HERMES_HOME/scripts/telegram-tdlib-daemon.py`;
   LaunchAgent label `local.hermes-tdlib-daemon`; interpreter = the existing
   venv that has `tdjson` (path supplied at install time); logs to
   `$HERMES_HOME/logs/tdlib-daemon.log`; session dir unchanged
   (`~/.tdlib_daemon`). Include install + rollback steps (unload new agent,
   reload the old OpenClaw agent) — the orchestrator performs them.

## Required tests (`gateway/tests/test_r1a_daemon.py`)

Synthetic TDLib dicts only.
- private/basicGroup/supergroup/channel(`is_channel`)/secret → `type`.
- photo with caption + `textEntityTypeMention` + `textEntityTypeMentionName`
  → `content_type=photo`, `caption`, `mention_usernames`, `mention_user_ids`;
  `text == ""`.
- reply resolution: reply target present in the batch → sender filled
  without a lookup call; absent → exactly one lookup; lookup cap honored.
- outgoing flag passthrough; channel post with `messageSenderChat` →
  `sender_chat_id`, no `sender_user_id`.
- Back-compat snapshot: a plain text message from a user yields a dict
  whose original keys/values equal the old shape exactly.
- `/me` shaping includes `username`/`usernames`.
- Module imports without `tdjson` installed (stub absent) and without
  touching the real home directory.
