import { test } from 'node:test'
import assert from 'node:assert/strict'

import type { TownScheduleContext } from '../../src/shared/situation.ts'
import { eventHint, isMeaningfulEvent, pickUpNext, UP_NEXT_EMPTY_MESSAGE, type UpNextEvent } from '../../src/town-home/lib/upnext.ts'

const ET = 'America/New_York'
const SGT = 'Asia/Singapore'
const MINUTE = 60_000
const HOUR = 60 * MINUTE
// Sun 2026-10-11 10:42 AM ET.
const MORNING = Date.parse('2026-10-11T14:42:00Z')
// Sun 2026-10-11 7:00 PM ET.
const EVENING = Date.parse('2026-10-11T23:00:00Z')

function event(summary: string, startAt: number, minutes: number, extra: Partial<UpNextEvent> = {}): UpNextEvent {
  return { summary, startAt, endAt: startAt + minutes * MINUTE, allDay: false, eventType: 'default', attendees: [], ...extra }
}

const EMPTY_CONTEXT: TownScheduleContext = { conflicts: [], clusters: [] }

test('isMeaningfulEvent: reminders out, real commitments in', () => {
  assert.equal(isMeaningfulEvent(event('Vitamins', MORNING, 15)), false)
  assert.equal(isMeaningfulEvent(event('Deep work', MORNING, 30)), true)
  assert.equal(isMeaningfulEvent(event('Quick call', MORNING, 15, { attendees: ['Bob'] })), true)
  assert.equal(isMeaningfulEvent(event('Standup', MORNING, 15, { attendeeCount: 6 })), true)
  assert.equal(isMeaningfulEvent(event('Tea', MORNING, 15, { location: 'Example Cafe' })), true)
  assert.equal(isMeaningfulEvent(event('Sync', MORNING, 15, { conference: 'Zoom' })), true)
  assert.equal(isMeaningfulEvent(event('Focus', MORNING, 120, { eventType: 'focusTime' })), false)
  assert.equal(isMeaningfulEvent(event('Home', MORNING, 24 * 60, { allDay: true })), false)
  assert.equal(isMeaningfulEvent(event('Flight', MORNING, 400, { eventType: 'fromGmail' })), true)
  assert.equal(isMeaningfulEvent(event('Declined', MORNING, 60, { declined: true })), false)
})

test('pickUpNext: ordering, meaningful filter, and the three-row cap', () => {
  const events = [
    event('Planning', MORNING + 5 * HOUR, 60, { attendees: ['Bob'] }),
    event('Vitamins', MORNING + 20 * MINUTE, 15),
    event('Design review', MORNING + 40 * MINUTE, 30, { attendees: ['Bob'], location: 'Room 4' }),
    event('Ongoing', MORNING - 10 * MINUTE, 30, { conference: 'Zoom' }),
    event('Ended', MORNING - 2 * HOUR, 30, { attendees: ['Bob'] }),
    event('Gym', MORNING + 2 * HOUR, 60),
    event('Tomorrow sync', MORNING + 24 * HOUR, 30, { attendees: ['Bob'] })
  ]
  const result = pickUpNext(events, EMPTY_CONTEXT, MORNING, ET)
  assert.deepEqual(result.rows.map(row => row.title), ['Ongoing', 'Design review', 'Gym'])
  assert.deepEqual(result.rows.map(row => row.relative), ['now', 'in 40m', 'in 2h'])
  assert.equal(result.rows[1].time, '11:22 AM')
  assert.equal(result.rows[1].hint, 'Room 4')
  assert.equal(result.rows[0].hint, 'Zoom')
  assert.equal(result.rows[1].meeting, true)
  assert.equal(result.rows[2].meeting, false)
  assert.equal(result.emptyMessage, null)
})

test('pickUpNext: duplicates collapse and a bare call link hints "Video call"', () => {
  const copy = event('Design review', MORNING + HOUR, 30, { location: 'https://meet.example.com/abc' })
  const result = pickUpNext([copy, { ...copy }], EMPTY_CONTEXT, MORNING, ET)
  assert.equal(result.rows.length, 1)
  assert.equal(eventHint(copy), 'Video call')
})

test('pickUpNext: after 6 PM local, tomorrow before noon is included', () => {
  const events = [
    event('Dinner', EVENING + 30 * MINUTE, 90, { location: 'Example Bistro' }),
    event('Breakfast meeting', Date.parse('2026-10-12T12:30:00Z'), 60, { attendees: ['Carol'] }),
    event('Afternoon review', Date.parse('2026-10-12T18:00:00Z'), 60, { attendees: ['Carol'] })
  ]
  const result = pickUpNext(events, EMPTY_CONTEXT, EVENING, ET)
  assert.deepEqual(result.rows.map(row => [row.title, row.day, row.relative]), [
    ['Dinner', 'today', 'in 30m'],
    ['Breakfast meeting', 'tomorrow', 'tomorrow']
  ])
  assert.equal(result.rows[1].time, '8:30 AM')
  const morning = pickUpNext(events.slice(1), EMPTY_CONTEXT, MORNING, ET)
  assert.deepEqual(morning.rows, [])
})

test('pickUpNext: conflicts and start clusters flag their rows', () => {
  const a = event('Board prep', MORNING + HOUR, 60, { attendees: ['Bob'] })
  const b = event('Vendor call', MORNING + HOUR + 30 * MINUTE, 30, { attendees: ['Dave'] })
  const c = event('Lunch', MORNING + 3 * HOUR, 60)
  const d = event('Walk', MORNING + 3 * HOUR + 15 * MINUTE, 45)
  const context: TownScheduleContext = {
    conflicts: [{ a: 'Board prep', b: 'Vendor call', aStartAt: a.startAt, bStartAt: b.startAt }],
    clusters: [{ summaries: ['Lunch', 'Walk'], startsAt: [c.startAt, d.startAt + MINUTE] }]
  }
  const result = pickUpNext([a, b, c, d], context, MORNING, ET)
  assert.deepEqual(result.rows.map(row => [row.title, row.conflict]), [
    ['Board prep', true],
    ['Vendor call', true],
    ['Lunch', true]
  ])
  assert.equal(pickUpNext([a, b], null, MORNING, ET).rows.every(row => !row.conflict), true)
})

test('pickUpNext: empty state names tomorrow\'s first event', () => {
  const events = [
    event('Vitamins', MORNING + HOUR, 15),
    event('Weekly sync', Date.parse('2026-10-12T13:00:00Z'), 60, { attendees: ['Bob'] }),
    event('Later sync', Date.parse('2026-10-12T15:00:00Z'), 60, { attendees: ['Bob'] })
  ]
  const result = pickUpNext(events, EMPTY_CONTEXT, MORNING, ET)
  assert.deepEqual(result.rows, [])
  assert.equal(result.emptyMessage, UP_NEXT_EMPTY_MESSAGE)
  assert.deepEqual(result.tomorrowFirst, { time: '9:00 AM', title: 'Weekly sync' })
  const nothing = pickUpNext([], EMPTY_CONTEXT, MORNING, ET)
  assert.equal(nothing.emptyMessage, 'Nothing else on the calendar today')
  assert.equal(nothing.tomorrowFirst, null)
})

test('pickUpNext: away, "today" and the time are local, with home time beside', () => {
  // 11 PM Sun in Singapore is 11 AM Sun ET: the row reads in local time.
  const now = Date.parse('2026-10-11T15:00:00Z')
  const call = event('Partner call', Date.parse('2026-10-11T15:30:00Z'), 30, { attendees: ['Bob'], eventTz: 'America/New_York' })
  const result = pickUpNext([call], EMPTY_CONTEXT, now, { displayTz: SGT, homeTz: ET })
  assert.equal(result.rows[0].time, '11:30 PM')
  assert.equal(result.rows[0].dualTime, '11:30 AM ET · 11:30 PM SGT')
  const home = pickUpNext([call], EMPTY_CONTEXT, now, { displayTz: ET, homeTz: ET })
  assert.equal(home.rows[0].dualTime, null)
})
