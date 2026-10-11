// Up next (S1): the next few meaningful events, in the operator's zone.
// The meaningful rule mirrors gateway where.is_meaningful: timed, a
// commitment type, not declined, and with another person, a place or call
// link, or at least 30 minutes. A 15-minute solo reminder never shows.

import type { TownScheduleContext } from '../../shared/situation.ts'
import { dateKeyIn, formatClock, formatEventTime, relativeUntil, safeTimeZone, shiftedDateKey, zonedParts } from './when.ts'

const COMMITMENT_EVENT_TYPES: ReadonlySet<string> = new Set(['default', 'fromGmail'])
const MEANINGFUL_MIN_MS = 30 * 60 * 1000
const UP_NEXT_LIMIT = 3
const EVENING_HOUR = 18
const MORNING_CUTOFF_HOUR = 12
const URL_RE = /^https?:\/\//i

export interface UpNextEvent {
  summary: string
  startAt: number
  endAt: number
  allDay: boolean
  eventType?: string | null
  eventTz?: string | null
  /** Display names of attendees other than the operator. */
  attendees?: readonly string[]
  /** True attendee total, the operator included. */
  attendeeCount?: number | null
  location?: string | null
  conference?: string | null
  declined?: boolean
}

export interface UpNextZones {
  displayTz: string
  homeTz?: string
}

export interface UpNextRow<E extends UpNextEvent = UpNextEvent> {
  event: E
  key: string
  day: 'today' | 'tomorrow'
  time: string
  relative: string
  title: string
  hint: string | null
  conflict: boolean
  dualTime: string | null
  meeting: boolean
}

export interface UpNextResult<E extends UpNextEvent = UpNextEvent> {
  rows: UpNextRow<E>[]
  emptyMessage: string | null
  tomorrowFirst: { time: string; title: string } | null
}

export const UP_NEXT_EMPTY_MESSAGE = 'Nothing else on the calendar today'

export function hasOtherAttendee(event: UpNextEvent): boolean {
  return (event.attendees?.length ?? 0) > 0 || (event.attendeeCount ?? 0) >= 2
}

export function isMeaningfulEvent(event: UpNextEvent): boolean {
  if (event.allDay || event.declined) {
    return false
  }
  if (!COMMITMENT_EVENT_TYPES.has(event.eventType || 'default')) {
    return false
  }
  if (!Number.isFinite(event.startAt) || !Number.isFinite(event.endAt) || event.endAt < event.startAt) {
    return false
  }
  return hasOtherAttendee(event)
    || Boolean(event.location?.trim())
    || Boolean(event.conference?.trim())
    || event.endAt - event.startAt >= MEANINGFUL_MIN_MS
}

/** A place, else the call service, else a generic hint for a bare link. */
export function eventHint(event: UpNextEvent): string | null {
  const location = event.location?.trim() ?? ''
  if (location && !URL_RE.test(location)) {
    return location
  }
  const conference = event.conference?.trim() ?? ''
  if (conference) {
    return conference
  }
  return location ? 'Video call' : null
}

function inConflict(event: UpNextEvent, context: TownScheduleContext | null | undefined): boolean {
  if (!context) {
    return false
  }
  const matches = (summary: string, startAt: number | null | undefined) =>
    summary === event.summary && startAt === event.startAt
  return context.conflicts.some(conflict => matches(conflict.a, conflict.aStartAt) || matches(conflict.b, conflict.bStartAt))
    || context.clusters.some(cluster => cluster.summaries.some((summary, index) => matches(summary, cluster.startsAt[index])))
}

/**
 * The next 2-3 meaningful events: under way or later today, plus (after 6 PM
 * local) tomorrow before noon. Empty -> "Nothing else on the calendar today"
 * with tomorrow's first meaningful event when known.
 */
export function pickUpNext<E extends UpNextEvent>(
  events: readonly E[],
  context: TownScheduleContext | null | undefined,
  now: number,
  tz: string | UpNextZones
): UpNextResult<E> {
  const displayTz = safeTimeZone(typeof tz === 'string' ? tz : tz.displayTz)
  const homeTz = safeTimeZone(typeof tz === 'string' ? tz : tz.homeTz ?? displayTz, displayTz)
  const todayKey = dateKeyIn(now, displayTz)
  const tomorrowKey = shiftedDateKey(now, displayTz, 1)
  const evening = zonedParts(now, displayTz).hour >= EVENING_HOUR

  const seen = new Set<string>()
  const upcoming = events
    .filter(event => isMeaningfulEvent(event) && event.endAt > now)
    .filter(event => {
      const key = `${event.summary}\u0000${event.startAt}`
      if (seen.has(key)) {
        return false
      }
      seen.add(key)
      return true
    })
    .sort((a, b) => a.startAt - b.startAt || a.endAt - b.endAt)

  const dayOf = (event: E): 'today' | 'tomorrow' | null => {
    const key = dateKeyIn(event.startAt, displayTz)
    if (event.startAt <= now || key === todayKey) {
      return 'today'
    }
    return key === tomorrowKey ? 'tomorrow' : null
  }

  const rows = upcoming.flatMap(event => {
    const day = dayOf(event)
    if (day === null) {
      return []
    }
    if (day === 'tomorrow' && !(evening && zonedParts(event.startAt, displayTz).hour < MORNING_CUTOFF_HOUR)) {
      return []
    }
    const dual = formatEventTime(event.startAt, { homeTz, displayTz, eventTz: event.eventTz })
    return [{
      event,
      key: `${event.startAt}-${event.endAt}-${event.summary}`,
      day,
      time: formatClock(event.startAt, displayTz),
      relative: day === 'tomorrow' ? 'tomorrow' : relativeUntil(event.startAt, event.endAt, now),
      title: event.summary,
      hint: eventHint(event),
      conflict: inConflict(event, context),
      dualTime: dual.includes(' · ') ? dual : null,
      meeting: hasOtherAttendee(event)
    }]
  }).slice(0, UP_NEXT_LIMIT)

  if (rows.length > 0) {
    return { rows, emptyMessage: null, tomorrowFirst: null }
  }
  const first = upcoming.find(event => dateKeyIn(event.startAt, displayTz) === tomorrowKey)
  return {
    rows: [],
    emptyMessage: UP_NEXT_EMPTY_MESSAGE,
    tomorrowFirst: first ? { time: formatClock(first.startAt, displayTz), title: first.summary } : null
  }
}
