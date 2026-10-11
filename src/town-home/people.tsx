import type { ReactNode } from 'react'

import { normalizeAttendeeDetails, normalizeMeetingPrep } from '../shared/people.ts'
import { meetingPeopleSummary, prepForEvent } from './lib/people.ts'

/**
 * The slice of a schedule event MeetingPeople reads. Either the town-home
 * shape (``attendeeDetails``/``attendeeCount``/``eventKey``) or the raw /feed
 * row (``attendees``/``attendee_count``/``event_key``) works; everything is
 * normalized defensively before render.
 */
export interface MeetingPeopleEvent {
  attendeeDetails?: unknown
  attendees?: unknown
  attendeeCount?: unknown
  attendee_count?: unknown
  eventKey?: unknown
  event_key?: unknown
}

export interface MeetingPeopleProps {
  event: MeetingPeopleEvent | null | undefined
  /** The day brief's ``prep`` list (or one entry); matched by event key. */
  prep?: unknown
}

function countValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * Who is in a meeting (S2, reshaped by U13), as one muted line: up to three
 * non-colleagues by name, colleagues grouped per org ("8 Example Co
 * colleagues"), then "+N more" — plus prep lines for the people named.
 */
export function MeetingPeople({ event, prep }: MeetingPeopleProps) {
  const details = normalizeAttendeeDetails(event?.attendeeDetails ?? event?.attendees)
  const rawKey = event?.eventKey ?? event?.event_key
  const eventKey = typeof rawKey === 'string' ? rawKey : null
  const summary = meetingPeopleSummary(
    details,
    countValue(event?.attendeeCount ?? event?.attendee_count),
    prepForEvent(normalizeMeetingPrep(prep), eventKey)
  )
  if (!summary.text) {
    return null
  }

  const parts: Array<{ key: string; node: ReactNode }> = [
    ...summary.people.map(person => ({
      key: `person-${person.key}`,
      node: (
        <>
          <span className="text-(--ui-text-secondary)">{person.name}</span>
          {person.detail ? ` ${person.detail}` : null}
        </>
      )
    })),
    ...summary.colleagues.map(phrase => ({ key: `colleagues-${phrase}`, node: phrase })),
    ...(summary.overflow > 0 ? [{ key: 'overflow', node: `+${summary.overflow} more` }] : [])
  ]

  return (
    <div className="min-w-0 space-y-0.5 text-xs text-(--ui-text-tertiary)">
      <p className="min-w-0" title={summary.text}>
        {parts.map((part, index) => (
          <span key={part.key}>
            {index > 0 ? ' · ' : null}
            {part.node}
          </span>
        ))}
      </p>
      {summary.people.map(person => person.prepLine ? (
        <p className="truncate" key={`prep-${person.key}`} title={`${person.name}: ${person.prepLine}`}>
          {person.name}: {person.prepLine}
        </p>
      ) : null)}
      {summary.talkingPoint ? (
        <p className="truncate text-(--ui-text-secondary)" title={summary.talkingPoint}>
          Talking point: {summary.talkingPoint}
        </p>
      ) : null}
    </div>
  )
}
