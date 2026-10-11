# S2 — Who people are: people directory + meeting prep

*GROUNDING pack. Conventions: `GROUNDING.md` + `README.md`. Builds on R3
(attendee union, true `attendee_count`) and R4 (`operator_profile.py`).
Runs in parallel with S1 — see "Seams".*

## Why (what Town does that we don't)

Town's meeting briefings introduce each attendee with a role, a background
line, and quick context — e.g. "<Name> — <role> at <org>; referred to you
by <Name>"; "Long-time friend; you attended their wedding party" — and end
with a concrete talking point. Its daily briefings tie people to your
world ("now COO at <fund> — relevant given your LP position").

Ours (anonymized, production):
- Calendar attendees reach both LLM calls as bare emails, so names are
  guessed.
- `known_contacts` is a lazy cache of only ~32 addresses. Every colleague
  on the operator's own work domain therefore counts as a "first contact",
  which licenses the false prep line "no prior email history with <your
  partner>".
- Both Google tokens already carry `contacts.readonly`, but nothing reads
  the People API.
- The Schedule dialog shows raw attendee emails and "+4" for a 41-person
  call. (R3 fixes the count; S2 renders it.)

## Seams (parallel with S1)

S2 owns:
- a new pure-ish `gateway/town-feed/dashboard/people.py`;
- the people refresh inside the Gmail scan;
- attendee enrichment in the calendar scan;
- first-contact logic;
- the `people` field on ranking-payload rows and on day-brief input;
- the day-brief `prep` output;
- desktop `src/town-home/people.tsx` (`MeetingPeople` component) + pure
  `lib/` helpers;
- attendee rendering in the existing Schedule dialog.

S2 does NOT edit the header or Up next (S1). The orchestrator wires
`MeetingPeople` into S1's Up next slot. In shared functions
(`_llm_rank`, `_generate_day_brief`, their schemas), add only S2's fields,
in a clearly separated block, so the merge stays mechanical.

## Hard requirements

**Directory**

1. `people.py` provides:
   - `build_directory(contacts, sent_recipients, inbound_senders,
     known_contacts, profile) -> dict[email, Person]`;
   - `load_directory(hermes_home)`;
   - `lookup(directory, email)`.

   `Person` fields: `email`, `name`, `org`, `title`, `relationship` ∈
   {`colleague`, `contact`, `correspondent`, `new`}, `last_sent_ts`,
   `last_received_ts`, `interactions` (int).

   Relationship precedence:
   1. `colleague` — domain is in `profile.work_domains` or is a non-consumer
      domain of the operator's own self addresses (exclude gmail.com and
      similar);
   2. `contact` — present in Google Contacts;
   3. `correspondent` — any sent or received history;
   4. else `new`.

   `name`/`org` precedence: Contacts → message display names → domain-derived
   org for colleagues (via `work_domains`).

   Emails are compared lowercased; the operator's own addresses are excluded.
2. **Refresh** (inside `town-gmail-scan.py` / `google_scan.py`, read-only
   Google calls only):
   - People API `people.connections.list` (personFields
     `names,emailAddresses,organizations`, paginated) for each account
     whose token has `contacts.readonly`;
   - recipients (To/Cc, with display names) from SENT messages in the last
     180 days, using `format=metadata`, capped at 500 messages per account;
   - inbound senders seen by the normal scan (merged incrementally).

   Contacts and sent-recipient refresh at most once per 24 h per account
   (timestamps in the cache). Failures log one stderr line and never fail
   the scan or change its exit code. A missing scope is skipped silently
   (logged once per day).

   The cache is `$HERMES_HOME/town-feed/people-cache.json`, written
   atomically with mode 0600, capped at 5000 people. Never print people
   data to stdout or the logs.
3. Calendar scan: each attendee entry in `events` gains `name` (directory
   name, else Google `displayName`), `org`, `relationship`, and
   `last_contact_ts` when known. `self` attendees are flagged. Read the
   cache via `people.load_directory`; a missing cache means no enrichment
   and no failure.
4. `first_contact_attendees` (curator) = attendees whose relationship is
   `new` only. Colleagues, contacts and correspondents are never "first
   contact".

**LLM context**

5. Ranking payload: Gmail rows gain `people` (sender plus up to 4
   recipients); calendar rows gain attendees (up to 5). Each entry is
   `{name, org, relationship, last_contact}`, where `last_contact` is human
   relative text ("emailed 3 days ago", "no prior email").

   Rules text:
   - introduce people by name with org/relationship when supplied;
   - never invent roles or relationships;
   - "no prior email history" only for `relationship == new`.
6. Day brief: input gains `meeting_people` for meetings in the next 24 h
   that have at least one non-self attendee (≤ 4 meetings × ≤ 6 people).

   Output adds `prep`: `[{event_key, people: [{email, line}],
   talking_point?}]` for at most the next 2 meetings. Each `line` is ≤ 90
   chars, grounded only in the supplied people facts and the event
   description. `_normalize_day_brief` keeps old briefs valid.

**Desktop**

7. `MeetingPeople({ event, prep })` renders up to 5 attendees as rows:
   `Name — org · relationship · last touch`. The relationship is humanized
   ("colleague", "in your contacts", "you've emailed", "first meeting"),
   and the prep `line` is shown when present. It ends with "+N more" using
   the true total (`attendee_count`).

   Theme tokens only, SDK components only. Export it from `people.tsx` for
   the orchestrator to wire into Up next.
8. The Schedule dialog attendee list uses names (falling back to the email
   local part, never a full raw email as the primary text), shows "+N"
   from the true total, and marks declined attendees.

## Required tests

Gateway (`gateway/tests/test_s2_people.py`):
- Directory precedence:
  - a colleague via `work_domains`;
  - a colleague via the operator's own custom domain;
  - a gmail.com self domain does NOT make every gmail user a colleague;
  - Contacts name beats message display name;
  - self addresses are excluded.
- Synthetic People API responses (multiple pages; entries without email
  are skipped) and synthetic sent-message metadata (To/Cc with quoted names
  and multiple recipients) parse correctly.
- Refresh gating: a second run within 24 h makes no People/sent calls
  (assert fake-service call counts); a missing scope is skipped with no
  exception.
- Cache is written with mode 0600 and capped.
- Calendar attendee enrichment from a cache fixture; a missing cache is a
  no-op.
- First-contact excludes colleagues, contacts and correspondents.
- Captured ranking/day-brief inputs include `people` / `meeting_people`
  with human `last_contact`. `prep` is normalized; old briefs without
  `prep` still load.

Desktop (`tests/desktop/s2-*.test.ts`):
- `personRowText`: all relationship variants, a missing org, and a
  last-touch humanization.
- `attendeeOverflow(total, shown)`.
- Display-name fallback from the email local part.
