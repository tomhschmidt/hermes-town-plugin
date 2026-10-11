import { test } from 'node:test'
import assert from 'node:assert/strict'

import { normalizeAttendeeDetails, normalizeMeetingPrep, type AttendeeDetail } from '../../src/shared/people.ts'
import {
  attendeeDisplayName,
  attendeeOverflow,
  displayNameFromEmail,
  lastTouchText,
  otherAttendeeTotal,
  personRowText,
  prepForEvent,
  relationshipLabel,
  scheduleAttendeeSummary
} from '../../src/town-home/lib/people.ts'

const NOW = Date.UTC(2026, 9, 11, 14, 0)
const HOUR = 3_600_000
const DAY = 24 * HOUR

function person(fields: Partial<AttendeeDetail>): AttendeeDetail {
  return {
    email: null,
    name: null,
    org: null,
    relationship: null,
    lastContactAt: null,
    response: null,
    self: false,
    ...fields
  }
}

test('personRowText covers every relationship variant', () => {
  assert.equal(
    personRowText(person({ name: 'Bob Lee', org: 'Example Fund', relationship: 'colleague', lastContactAt: NOW - 3 * DAY }), NOW),
    'Bob Lee — Example Fund · colleague · last email 3 days ago'
  )
  assert.equal(
    personRowText(person({ name: 'Carol Example', org: 'Example Co', relationship: 'contact' }), NOW),
    'Carol Example — Example Co · in your contacts'
  )
  assert.equal(
    personRowText(person({ name: 'Erin Example', relationship: 'correspondent', lastContactAt: NOW - 5 * HOUR }), NOW),
    "Erin Example — you've emailed · last email 5 hours ago"
  )
  assert.equal(personRowText(person({ name: 'Dana Example', relationship: 'new' }), NOW), 'Dana Example — first meeting')
  assert.deepEqual(
    (['colleague', 'contact', 'correspondent', 'new'] as const).map(relationshipLabel),
    ['colleague', 'in your contacts', "you've emailed", 'first meeting']
  )
  assert.equal(relationshipLabel(null), null)
})

test('personRowText drops a missing org and unknown segments', () => {
  assert.equal(
    personRowText(person({ name: 'Bob Lee', relationship: 'colleague', lastContactAt: NOW - 2 * DAY }), NOW),
    'Bob Lee — colleague · last email 2 days ago'
  )
  assert.equal(personRowText(person({ name: 'Bob Lee' }), NOW), 'Bob Lee')
  assert.equal(
    personRowText(person({ name: 'Carol Example', relationship: 'contact', response: 'declined' }), NOW),
    'Carol Example — in your contacts · declined'
  )
})

test('lastTouchText humanizes the last email', () => {
  assert.equal(lastTouchText(NOW - 10 * 60_000, NOW), 'last email within the hour')
  assert.equal(lastTouchText(NOW - HOUR, NOW), 'last email 1 hour ago')
  assert.equal(lastTouchText(NOW - 30 * HOUR, NOW), 'last email 30 hours ago')
  assert.equal(lastTouchText(NOW - 3 * DAY, NOW), 'last email 3 days ago')
  assert.equal(lastTouchText(NOW - 21 * DAY, NOW), 'last email 3 weeks ago')
  assert.equal(lastTouchText(NOW - 120 * DAY, NOW), 'last email 4 months ago')
  assert.equal(lastTouchText(NOW - 400 * DAY, NOW), 'last email over a year ago')
  assert.equal(lastTouchText(null, NOW), null)
  assert.equal(lastTouchText(Number.NaN, NOW), null)
})

test('attendeeOverflow counts what "+N" stands for', () => {
  assert.equal(attendeeOverflow(41, 5), 36)
  assert.equal(attendeeOverflow(5, 5), 0)
  assert.equal(attendeeOverflow(3, 5), 0)
  assert.equal(attendeeOverflow(null, 2), 0)
  assert.equal(attendeeOverflow(Number.POSITIVE_INFINITY, 2), 0)
  // attendee_count includes the operator; the overflow is about everyone else.
  const details = [person({ email: 'alice@example.com', self: true }), person({ name: 'Bob Lee' }), person({ name: 'Carol' })]
  assert.equal(otherAttendeeTotal(details, 41), 40)
  assert.equal(otherAttendeeTotal(details, null), 2)
  assert.equal(attendeeOverflow(otherAttendeeTotal(details, 41), 2), 38)
})

test('display names fall back to the email local part, never the raw email', () => {
  assert.equal(displayNameFromEmail('alice.smith@example.com'), 'Alice Smith')
  assert.equal(displayNameFromEmail('bob_lee+town@example.org'), 'Bob Lee')
  assert.equal(displayNameFromEmail('jdoe42@example.com'), 'Jdoe42')
  assert.equal(attendeeDisplayName(person({ email: 'carol-example@example.com' })), 'Carol Example')
  assert.equal(attendeeDisplayName(person({ name: 'dana@example.org' })), 'Dana')
  assert.equal(attendeeDisplayName(person({ name: 'Erin Example', email: 'erin@example.org' })), 'Erin Example')
  assert.equal(attendeeDisplayName(person({})), 'Guest')
})

test('scheduleAttendeeSummary uses names, the true total, and marks declined', () => {
  const details = normalizeAttendeeDetails([
    { email: 'alice@example.com', name: 'Alice', self: true },
    { email: 'bob.lee@example-fund.com' },
    { email: 'carol@example.com', name: 'Carol Example', response: 'declined' },
    { email: 'dana@example.org', name: 'Dana Example', relationship: 'new' },
    { email: 'erin@example.org', name: 'Erin Example' }
  ])
  assert.equal(scheduleAttendeeSummary(details, 41), 'with Bob Lee, Carol Example (declined), Dana Example +37')
  assert.equal(scheduleAttendeeSummary(details.slice(0, 3), 3), 'with Bob Lee, Carol Example (declined)')
  assert.equal(scheduleAttendeeSummary([details[0]], 1), null)
  assert.ok(!scheduleAttendeeSummary(details, 41)?.includes('@'))
})

test('normalizeAttendeeDetails accepts feed and bridge shapes defensively', () => {
  const details = normalizeAttendeeDetails([
    { email: ' Bob@Example-Fund.com ', name: 'Bob Lee', org: 'Example Fund', relationship: 'colleague', last_contact_ts: 1_760_000_000 },
    { email: 'carol@example.com', relationship: 'stranger', lastContactAt: 1_760_000_000_000 },
    'Dana Example',
    { name: '' },
    null,
    42
  ])
  assert.deepEqual(details, [
    { email: 'bob@example-fund.com', name: 'Bob Lee', org: 'Example Fund', relationship: 'colleague', lastContactAt: 1_760_000_000_000, response: null, self: false },
    { email: 'carol@example.com', name: null, org: null, relationship: null, lastContactAt: 1_760_000_000_000, response: null, self: false },
    { email: null, name: 'Dana Example', org: null, relationship: null, lastContactAt: null, response: null, self: false }
  ])
  assert.deepEqual(normalizeAttendeeDetails('nope'), [])
})

test('normalizeMeetingPrep keeps two grounded entries in either shape', () => {
  const prep = normalizeMeetingPrep([
    { event_key: 'ev_a', people: [{ email: 'Bob@Example.com', line: 'Colleague; you emailed 3 days ago' }, { email: 'bad', line: 'x' }], talking_point: 'Open with the memo.' },
    { eventKey: 'ev_a', people: [{ email: 'c@example.com', line: 'duplicate' }] },
    { eventKey: 'ev_b', people: [], talkingPoint: null },
    { eventKey: 'ev_c', people: [{ email: 'd@example.com', line: 'kept' }] },
    { eventKey: 'ev_d', people: [{ email: 'e@example.com', line: 'over the cap' }] }
  ])
  assert.deepEqual(prep, [
    { eventKey: 'ev_a', people: [{ email: 'bob@example.com', line: 'Colleague; you emailed 3 days ago' }], talkingPoint: 'Open with the memo.' },
    { eventKey: 'ev_c', people: [{ email: 'd@example.com', line: 'kept' }], talkingPoint: null }
  ])
  assert.equal(prepForEvent(prep, 'ev_c')?.people[0].line, 'kept')
  assert.equal(prepForEvent(prep, 'ev_x'), null)
  assert.equal(prepForEvent(prep, null), null)
})
