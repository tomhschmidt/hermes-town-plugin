// Header copy for Town home (S1): greeting, context line, location chip.

import type { TownWhere } from '../../shared/situation.ts'
import { zonedParts } from './when.ts'

export type ContextLineKind = 'opener' | 'day_summary' | 'next_event' | 'waiting' | 'routine'

export interface ContextLineInput {
  opener?: string | null
  daySummary?: string | null
  /** The next meaningful event (Up next's first row), when known. */
  nextMeaningful?: { summary: string; startAt: number } | null
  /** The scanner's raw next_event — only used when no schedule is loaded. */
  legacyNextEvent?: { summary: string; startAt: number } | null
  scheduleLoaded?: boolean
  waitingCount?: number
  nextRoutine?: { name: string; nextRunAt: number } | null
  relative: (at: number) => string
}

export interface ContextLine {
  kind: ContextLineKind
  text: string
}

/** opener > day_summary > next meaningful event > waiting items > next routine. */
export function contextLineSource(input: ContextLineInput): ContextLine | null {
  const opener = input.opener?.trim()
  if (opener) {
    return { kind: 'opener', text: opener }
  }
  const daySummary = input.daySummary?.trim()
  if (daySummary) {
    return { kind: 'day_summary', text: daySummary }
  }
  const next = input.nextMeaningful ?? (input.scheduleLoaded ? null : input.legacyNextEvent)
  if (next?.summary.trim()) {
    return { kind: 'next_event', text: `Next: ${next.summary.trim()} · ${input.relative(next.startAt)}` }
  }
  const waiting = input.waitingCount ?? 0
  if (waiting > 0) {
    return { kind: 'waiting', text: `${waiting} ${waiting === 1 ? 'item' : 'items'} waiting on you` }
  }
  if (input.nextRoutine?.name.trim()) {
    return { kind: 'routine', text: `Next routine: ${input.nextRoutine.name.trim()} · ${input.relative(input.nextRoutine.nextRunAt)}` }
  }
  return null
}

export interface TripBadge {
  label: string
  dayN: number | null
  totalDays: number | null
}

function tripDays(trip: TripBadge | null | undefined): string {
  if (!trip || trip.dayN === null) {
    return ''
  }
  return ` · day ${trip.dayN}${trip.totalDays !== null ? ` of ${trip.totalDays}` : ''}`
}

/**
 * The one header chip: ``where.label`` (plus the trip day while away), else
 * — for a gateway that predates S1 — the day brief's trip badge.
 */
export function locationChipLabel(where: TownWhere | null | undefined, trip: TripBadge | null | undefined): string | null {
  if (where) {
    return `${where.label}${where.status === 'away' ? tripDays(trip) : ''}`
  }
  return trip?.label ? `${trip.label}${tripDays(trip)}` : null
}

// U13: home and just-back are what the opener already says, so the chip is
// only for being somewhere else; home-zone conversions are noise at home.
const LOCATION_CHIP_STATUSES: ReadonlySet<string> = new Set(['away', 'traveling_today'])
const HOME_STATUSES: ReadonlySet<string> = new Set(['home', 'just_returned'])
export const MAX_VISIBLE_CALLOUTS = 2

/**
 * Whether the header shows a location chip: only while away or traveling
 * today. Without ``where`` (a gateway that predates S1) the trip-badge
 * fallback in ``locationChipLabel`` decides.
 */
export function shouldShowLocationChip(where: Pick<TownWhere, 'status'> | null | undefined): boolean {
  return !where || LOCATION_CHIP_STATUSES.has(where.status)
}

/** The day-brief callouts the header renders: no time-zone callout at home, at most two. */
export function visibleCallouts<C extends { kind: string }>(
  callouts: readonly C[],
  where: Pick<TownWhere, 'status'> | null | undefined,
  limit = MAX_VISIBLE_CALLOUTS
): C[] {
  const home = Boolean(where && HOME_STATUSES.has(where.status))
  return callouts.filter(callout => !(home && callout.kind === 'timezone')).slice(0, limit)
}

/** "Good morning" / "Happy Monday" / "Good afternoon" / "Good evening" in ``timeZone``. */
export function greetingBase(now: number, timeZone: string): string {
  const { hour, weekday } = zonedParts(now, timeZone)
  if (hour < 12) {
    return weekday === 'Mon' ? 'Happy Monday' : 'Good morning'
  }
  return hour < 18 ? 'Good afternoon' : 'Good evening'
}

/** The Customize name wins; the operator profile's preferred name fills in. */
export function greetingName(customName: string | null | undefined, preferredName: string | null | undefined): string {
  return customName?.trim() || preferredName?.trim() || ''
}
