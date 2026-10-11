import { test } from 'node:test'
import assert from 'node:assert/strict'

import { normalizeOperator, normalizeScheduleContext, normalizeWhere } from '../../src/shared/situation.ts'
import { contextLineSource, greetingBase, greetingName, locationChipLabel } from '../../src/town-home/lib/header.ts'

const relative = (at: number) => `rel(${at})`

test('contextLineSource priority: opener > day_summary > fallbacks', () => {
  const base = {
    opener: 'Back in Boston after Lisbon — two meetings left today.',
    daySummary: 'Light day — 2 meetings; tomorrow has 8.',
    nextMeaningful: { summary: 'Design review', startAt: 5 },
    legacyNextEvent: { summary: 'Vitamins', startAt: 3 },
    scheduleLoaded: true,
    waitingCount: 2,
    nextRoutine: { name: 'Morning brief', nextRunAt: 9 },
    relative
  }
  assert.deepEqual(contextLineSource(base), { kind: 'opener', text: base.opener })
  assert.deepEqual(contextLineSource({ ...base, opener: '  ' }), { kind: 'day_summary', text: base.daySummary })
  assert.deepEqual(contextLineSource({ ...base, opener: null, daySummary: null }), {
    kind: 'next_event',
    text: 'Next: Design review · rel(5)'
  })
  // With a schedule loaded, the raw scanner next_event (maybe a reminder) is never used.
  assert.deepEqual(contextLineSource({ ...base, opener: null, daySummary: null, nextMeaningful: null }), {
    kind: 'waiting',
    text: '2 items waiting on you'
  })
  // Without a schedule (older gateway), it still is.
  assert.deepEqual(
    contextLineSource({ ...base, opener: null, daySummary: null, nextMeaningful: null, scheduleLoaded: false }),
    { kind: 'next_event', text: 'Next: Vitamins · rel(3)' }
  )
  assert.deepEqual(
    contextLineSource({ ...base, opener: null, daySummary: null, nextMeaningful: null, waitingCount: 0 }),
    { kind: 'routine', text: 'Next routine: Morning brief · rel(9)' }
  )
  assert.equal(contextLineSource({ relative }), null)
})

test('locationChipLabel: where label, trip day while away, legacy trip badge', () => {
  const away = normalizeWhere({ status: 'away', city: 'Lisbon', timezone: 'Europe/Lisbon', label: 'In Lisbon · +5h from home', evidence: [] })
  const trip = { label: 'Lisbon trip', dayN: 2, totalDays: 4 }
  assert.equal(locationChipLabel(away, trip), 'In Lisbon · +5h from home · day 2 of 4')
  const home = normalizeWhere({ status: 'home', label: 'Home in Boston' })
  assert.equal(locationChipLabel(home, trip), 'Home in Boston')
  assert.equal(locationChipLabel(null, trip), 'Lisbon trip · day 2 of 4')
  assert.equal(locationChipLabel(null, { label: 'Lisbon trip', dayN: null, totalDays: null }), 'Lisbon trip')
  assert.equal(locationChipLabel(null, null), null)
})

test('greeting uses the explicit zone and falls back to the profile name', () => {
  // 10:42 AM Sun in Boston is 10:42 PM in Singapore.
  const now = Date.parse('2026-10-11T14:42:00Z')
  assert.equal(greetingBase(now, 'America/New_York'), 'Good morning')
  assert.equal(greetingBase(now, 'Asia/Singapore'), 'Good evening')
  assert.equal(greetingBase(Date.parse('2026-10-12T13:00:00Z'), 'America/New_York'), 'Happy Monday')
  assert.equal(greetingName('Al', 'Alice'), 'Al')
  assert.equal(greetingName('  ', 'Alice'), 'Alice')
  assert.equal(greetingName('', ''), '')
})

test('situation normalizers accept the gateway and bridge shapes defensively', () => {
  assert.deepEqual(normalizeOperator({ name: 'Alice Example', preferred_name: 'Alice', home_city: 'Boston', home_timezone: 'America/New_York' }), {
    name: 'Alice Example',
    preferredName: 'Alice',
    homeCity: 'Boston',
    homeTimezone: 'America/New_York'
  })
  assert.equal(normalizeOperator({ home_timezone: 'Mars/Olympus' })?.homeTimezone, '')
  assert.equal(normalizeOperator('nope'), null)
  const where = normalizeWhere({
    status: 'just_returned', city: 'Boston', timezone: 'America/New_York', label: 'Back in Boston after Lisbon',
    evidence: ['TP 217 landed 6h ago', 'From Lisbon', 'x', 'y'], since: '2026-10-11T08:42:00Z', source: 'flight'
  })
  assert.equal(where?.status, 'just_returned')
  assert.equal(where?.evidence.length, 3)
  assert.equal(where?.since, Date.parse('2026-10-11T08:42:00Z'))
  assert.equal(normalizeWhere({ status: 'teleported', label: 'x' }), null)
  assert.equal(normalizeWhere({ status: 'home' }), null)
  assert.deepEqual(normalizeWhere(where), where)
  const context = normalizeScheduleContext({
    conflicts: [{ a: 'A', b: 'B', a_start_iso: '2026-10-11T15:00:00Z', b_start_iso: '2026-10-11T15:30:00Z' }, { a: 'only' }],
    start_clusters: [{ summaries: ['C', 'D'], starts_iso: ['2026-10-11T17:00:00Z', 'bad'] }]
  })
  assert.equal(context?.conflicts.length, 1)
  assert.equal(context?.conflicts[0].aStartAt, Date.parse('2026-10-11T15:00:00Z'))
  assert.deepEqual(context?.clusters[0].startsAt, [Date.parse('2026-10-11T17:00:00Z'), null])
  assert.deepEqual(normalizeScheduleContext(context), context)
  assert.equal(normalizeScheduleContext(null), null)
})
