import { test } from 'node:test'
import assert from 'node:assert/strict'

import { normalizeWhere, type TownWhereStatus } from '../../src/shared/situation.ts'
import { locationChipLabel, shouldShowLocationChip, visibleCallouts } from '../../src/town-home/lib/header.ts'
import { pickUpNext, upNextRowMeta, type UpNextEvent } from '../../src/town-home/lib/upnext.ts'

const ET = 'America/New_York'
const MINUTE = 60_000
const HOUR = 60 * MINUTE
// Sun 2026-10-11 10:42 AM ET.
const MORNING = Date.parse('2026-10-11T14:42:00Z')
// Sun 2026-10-11 7:00 PM ET.
const EVENING = Date.parse('2026-10-11T23:00:00Z')

function where(status: TownWhereStatus) {
  const normalized = normalizeWhere({ status, city: 'Lisbon', timezone: 'Europe/Lisbon', label: `Where: ${status}`, evidence: [] })
  assert.ok(normalized)
  return normalized
}

test('shouldShowLocationChip: only while away or traveling today', () => {
  assert.equal(shouldShowLocationChip(where('home')), false)
  assert.equal(shouldShowLocationChip(where('just_returned')), false)
  assert.equal(shouldShowLocationChip(where('away')), true)
  assert.equal(shouldShowLocationChip(where('traveling_today')), true)
})

test('shouldShowLocationChip: a gateway without where keeps the trip-badge fallback', () => {
  const trip = { label: 'Lisbon trip', dayN: 2, totalDays: 4 }
  assert.equal(shouldShowLocationChip(null), true)
  assert.equal(shouldShowLocationChip(undefined), true)
  assert.equal(locationChipLabel(null, trip), 'Lisbon trip · day 2 of 4')
})

test('visibleCallouts: no time-zone callout at home or just back, at most two', () => {
  const callouts = [
    { kind: 'timezone', text: 'Gala is 9:00 PM ET, which is 6:00 PM Pacific.' },
    { kind: 'conflict', text: 'Design review overlaps Lunch.' },
    { kind: 'prep', text: 'Read the memo before the board call.' },
    { kind: 'deadline', text: 'Expenses due by 5 PM.' }
  ]
  for (const status of ['home', 'just_returned'] as const) {
    assert.deepEqual(visibleCallouts(callouts, where(status)).map(callout => callout.kind), ['conflict', 'prep'])
  }
  for (const status of ['away', 'traveling_today'] as const) {
    assert.deepEqual(visibleCallouts(callouts, where(status)).map(callout => callout.kind), ['timezone', 'conflict'])
  }
  // Without where the zone question is unknown: nothing dropped, still capped.
  assert.deepEqual(visibleCallouts(callouts, null).map(callout => callout.kind), ['timezone', 'conflict'])
  assert.deepEqual(visibleCallouts([], where('home')), [])
})

function event(summary: string, startAt: number, minutes: number, extra: Partial<UpNextEvent> = {}): UpNextEvent {
  return { summary, startAt, endAt: startAt + minutes * MINUTE, allDay: false, eventType: 'default', attendees: ['Bob'], ...extra }
}

test('upNextRowMeta: relative time and place inline after the title', () => {
  const result = pickUpNext([
    event('Weekly Sync', MORNING + 8 * HOUR, 30, { conference: 'Zoom' }),
    event('Design review', MORNING + 40 * MINUTE, 30, { location: 'Room 4' }),
    event('Planning', MORNING + 2 * HOUR, 60)
  ], null, MORNING, ET)
  assert.deepEqual(result.rows.map(row => [row.time, upNextRowMeta(row)]), [
    ['11:22 AM', 'in 40m · Room 4'],
    ['12:42 PM', 'in 2h'],
    ['6:42 PM', 'in 8h · Zoom']
  ])
  assert.equal(upNextRowMeta({ relative: '', hint: null }), '')
})

test('upNextRowMeta: tomorrow rows say "tomorrow" inline; the clock column is just the time', () => {
  const result = pickUpNext([event('Breakfast', EVENING + 14 * HOUR, 60, { location: 'Example Cafe' })], null, EVENING, ET)
  assert.equal(result.rows[0].time, '9:00 AM')
  assert.equal(upNextRowMeta(result.rows[0]), 'tomorrow · Example Cafe')
})
