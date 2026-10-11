import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  dateKeyIn,
  displayTimeZone,
  formatClock,
  formatEventTime,
  relativeUntil,
  safeTimeZone,
  shiftedDateKey,
  zoneAbbreviation
} from '../../src/town-home/lib/when.ts'

const ET = 'America/New_York'
const SGT = 'Asia/Singapore'
const PT = 'America/Los_Angeles'

test('formatEventTime: one zone renders a bare local time', () => {
  // Mon 2026-10-12 11:00 AM ET.
  const start = '2026-10-12T15:00:00Z'
  assert.equal(formatEventTime(start, { homeTz: ET, displayTz: ET }), '11:00 AM')
  assert.equal(formatEventTime(start, { homeTz: ET, displayTz: ET, eventTz: ET }), '11:00 AM')
  // A same-offset zone name is not a second zone.
  assert.equal(formatEventTime(start, { homeTz: ET, displayTz: ET, eventTz: 'America/Toronto' }), '11:00 AM')
  // An offset ISO from a Singapore-zoned calendar is the same instant.
  assert.equal(formatEventTime('2026-10-12T23:00:00+08:00', { homeTz: ET, displayTz: ET }), '11:00 AM')
})

test('formatEventTime: away in SGT shows home time then local time', () => {
  assert.equal(
    formatEventTime('2026-10-12T15:00:00Z', { homeTz: ET, displayTz: SGT, eventTz: ET }),
    '11:00 AM ET · 11:00 PM SGT'
  )
})

test('formatEventTime: names the weekday when the other zone is on another day', () => {
  // Sun 2026-10-11 9:00 PM ET is Mon 9:00 AM in Singapore.
  assert.equal(
    formatEventTime('2026-10-12T01:00:00Z', { homeTz: ET, displayTz: SGT }),
    '9:00 PM ET · Mon 9:00 AM SGT'
  )
})

test('formatEventTime: at home, an event set in another zone shows both', () => {
  assert.equal(
    formatEventTime('2026-10-12T16:00:00Z', { homeTz: ET, displayTz: ET, eventTz: PT }),
    '12:00 PM ET · 9:00 AM PT'
  )
  assert.equal(formatEventTime('not a date', { homeTz: ET, displayTz: ET }), '')
})

test('zoneAbbreviation: spoken names, DST-aware, offset fallback', () => {
  const summer = Date.UTC(2026, 6, 1, 12)
  const winter = Date.UTC(2026, 11, 1, 12)
  assert.equal(zoneAbbreviation(ET, summer), 'ET')
  assert.equal(zoneAbbreviation(SGT, summer), 'SGT')
  assert.equal(zoneAbbreviation('Europe/London', summer), 'BST')
  assert.equal(zoneAbbreviation('Europe/London', winter), 'GMT')
  assert.equal(zoneAbbreviation('Australia/Sydney', winter), 'AEDT')
  assert.equal(zoneAbbreviation('Asia/Taipei', summer), 'UTC+8')
  assert.equal(zoneAbbreviation('Asia/Kathmandu', summer), 'UTC+5:45')
  assert.equal(zoneAbbreviation('Not/AZone', summer), 'UTC')
})

test('formatClock and date keys use the explicit zone (US fall-back day)', () => {
  // 2026-11-01 is the US fall-back day: 1:30 AM happens twice.
  assert.equal(formatClock(Date.parse('2026-11-01T05:30:00Z'), ET), '1:30 AM')
  assert.equal(formatClock(Date.parse('2026-11-01T06:30:00Z'), ET), '1:30 AM')
  assert.equal(formatClock(Date.parse('2026-11-01T17:00:00Z'), ET), '12:00 PM')
  assert.equal(dateKeyIn(Date.parse('2026-10-12T01:00:00Z'), ET), '2026-10-11')
  assert.equal(dateKeyIn(Date.parse('2026-10-12T01:00:00Z'), SGT), '2026-10-12')
  assert.equal(shiftedDateKey(Date.parse('2026-10-31T16:00:00Z'), ET, 1), '2026-11-01')
})

test('relativeUntil', () => {
  const now = Date.parse('2026-10-11T14:42:00Z')
  const minute = 60_000
  assert.equal(relativeUntil(now - 5 * minute, now + 25 * minute, now), 'now')
  assert.equal(relativeUntil(now + 40 * minute, now + 70 * minute, now), 'in 40m')
  assert.equal(relativeUntil(now + 130 * minute, now + 160 * minute, now), 'in 2h 10m')
  assert.equal(relativeUntil(now + 120 * minute, now + 160 * minute, now), 'in 2h')
  assert.equal(relativeUntil(now + 7 * 60 * minute, now + 8 * 60 * minute, now), 'in 7h')
})

test('displayTimeZone prefers the away zone, then home, then the fallback', () => {
  const operator = { homeTimezone: ET }
  assert.equal(displayTimeZone(operator, { status: 'away', timezone: SGT }, 'UTC'), SGT)
  assert.equal(displayTimeZone(operator, { status: 'just_returned', timezone: ET }, 'UTC'), ET)
  assert.equal(displayTimeZone(operator, { status: 'traveling_today', timezone: SGT }, 'UTC'), ET)
  assert.equal(displayTimeZone(null, null, 'Europe/Lisbon'), 'Europe/Lisbon')
  assert.equal(safeTimeZone('Mars/Olympus', ET), ET)
})
