import { test } from 'node:test'
import assert from 'node:assert/strict'

import { normalizeAttendeeDetails, type AttendeeDetail } from '../../src/shared/people.ts'
import { colleaguePhrase, meetingPeopleSummary } from '../../src/town-home/lib/people.ts'

const SELF = { email: 'alice@example.com', name: 'Alice', self: true }

function colleague(index: number, org: string | null = 'Example Co', extra: Record<string, unknown> = {}) {
  return { email: `c${index}@example.com`, name: `Colleague ${index}`, org, relationship: 'colleague', ...extra }
}

function details(rows: unknown[]): AttendeeDetail[] {
  return normalizeAttendeeDetails(rows)
}

test('colleaguePhrase: singular, plural, and no org', () => {
  assert.equal(colleaguePhrase(8, 'Example Co'), '8 Example Co colleagues')
  assert.equal(colleaguePhrase(1, 'Example Co'), '1 Example Co colleague')
  assert.equal(colleaguePhrase(3, null), '3 colleagues')
  assert.equal(colleaguePhrase(1, '  '), '1 colleague')
})

test('meetingPeopleSummary: colleagues only, one org, is a single phrase', () => {
  const summary = meetingPeopleSummary(details([SELF, colleague(1), colleague(2), colleague(3), colleague(4), colleague(5)]), 6, null)
  assert.equal(summary.text, '5 Example Co colleagues')
  assert.deepEqual(summary.people, [])
  assert.deepEqual(summary.colleagues, ['5 Example Co colleagues'])
  assert.equal(summary.overflow, 0)
})

test('meetingPeopleSummary: colleagues from mixed orgs group per org, largest first', () => {
  const rows = [SELF, colleague(1, 'Acme'), colleague(2), colleague(3), colleague(4, 'acme')]
  const summary = meetingPeopleSummary(details(rows), 5, null)
  assert.deepEqual(summary.colleagues, ['2 Acme colleagues', '2 Example Co colleagues'])
  assert.equal(summary.text, '2 Acme colleagues · 2 Example Co colleagues')
  const single = meetingPeopleSummary(details([SELF, colleague(1, 'Acme'), colleague(2), colleague(3)]), 4, null)
  assert.equal(single.text, '2 Example Co colleagues · 1 Acme colleague')
})

test('meetingPeopleSummary: colleagues without an org are just "colleagues"', () => {
  assert.equal(meetingPeopleSummary(details([SELF, colleague(1, null), colleague(2, null)]), 3, null).text, '2 colleagues')
  assert.equal(meetingPeopleSummary(details([SELF, colleague(1, null)]), 2, null).text, '1 colleague')
})

test('meetingPeopleSummary: externals listed (max 3) before colleagues, then "+N more"', () => {
  const rows = [
    SELF,
    colleague(1),
    colleague(2),
    { email: 'dana@example.org', name: 'Dana Example', relationship: 'new' },
    { email: 'erin@acme.example', name: 'Erin Example', org: 'Acme Capital', relationship: 'contact' },
    { email: 'frank@example.org', name: 'Frank Example', relationship: 'correspondent' },
    { email: 'gina@example.net', name: 'Gina Example', org: 'Example Fund', relationship: 'new' }
  ]
  const prep = { eventKey: 'ev_a', people: [{ email: 'gina@example.net', line: 'Partner at Example Fund' }], talkingPoint: 'Ask about Q4.' }
  const summary = meetingPeopleSummary(details(rows), 7, prep)
  assert.deepEqual(summary.people.map(person => person.text), [
    'Gina Example — Example Fund (first meeting)',
    'Dana Example (first meeting)',
    'Erin Example — Acme Capital'
  ])
  assert.equal(summary.people[0].prepLine, 'Partner at Example Fund')
  assert.equal(summary.people[1].prepLine, null)
  assert.equal(summary.overflow, 1)
  assert.equal(
    summary.text,
    'Gina Example — Example Fund (first meeting) · Dana Example (first meeting) · Erin Example — Acme Capital · 2 Example Co colleagues · +1 more'
  )
  assert.equal(summary.talkingPoint, 'Ask about Q4.')
})

test('meetingPeopleSummary: prep lines only for the people named, never colleagues', () => {
  const rows = [SELF, colleague(1), { email: 'dana@example.org', name: 'Dana Example', relationship: 'new' }]
  const prep = {
    eventKey: 'ev_a',
    people: [{ email: 'c1@example.com', line: 'Runs the platform team' }, { email: 'dana@example.org', line: 'Intro from Bob' }],
    talkingPoint: null
  }
  const summary = meetingPeopleSummary(details(rows), 3, prep)
  assert.deepEqual(summary.people.map(person => [person.name, person.prepLine]), [['Dana Example', 'Intro from Bob']])
  assert.equal(summary.text, 'Dana Example (first meeting) · 1 Example Co colleague')
})

test('meetingPeopleSummary: declined attendees are excluded from names and counts', () => {
  const rows = [
    SELF,
    colleague(1),
    colleague(2, 'Example Co', { response: 'declined' }),
    { email: 'dana@example.org', name: 'Dana Example', relationship: 'new', response: 'declined' },
    { email: 'erin@example.org', name: 'Erin Example', relationship: 'contact' }
  ]
  const summary = meetingPeopleSummary(details(rows), 5, null)
  assert.equal(summary.text, 'Erin Example · 1 Example Co colleague')
  assert.equal(summary.overflow, 0)
})

test('meetingPeopleSummary: attendees past the scanner cap only count toward "+N more"', () => {
  const rows = [SELF, colleague(1), colleague(2), colleague(3), colleague(4), colleague(5), colleague(6), colleague(7)]
  const summary = meetingPeopleSummary(details(rows), 41, null)
  assert.equal(summary.text, '7 Example Co colleagues · +33 more')
})

test('meetingPeopleSummary: nobody to show renders nothing', () => {
  assert.equal(meetingPeopleSummary([], 0, null).text, '')
  assert.equal(meetingPeopleSummary(details([SELF]), 1, null).text, '')
  const onlyDeclined = meetingPeopleSummary(details([SELF, colleague(1, 'Example Co', { response: 'declined' })]), 2, null)
  assert.deepEqual(onlyDeclined, { people: [], colleagues: [], overflow: 0, talkingPoint: null, text: '' })
})
