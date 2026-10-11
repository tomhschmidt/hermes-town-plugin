import { normalizeAttendeeDetails, normalizeMeetingPrep } from '../shared/people.ts'
import { meetingPeopleView, prepForEvent } from './lib/people.ts'

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
  now?: number
}

function countValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

/**
 * Who is in a meeting (S2): up to five attendees as
 * "Name — org · relationship · last touch", each with its prep line when the
 * day brief has one, then "+N more" from the true attendee total.
 */
export function MeetingPeople({ event, prep, now }: MeetingPeopleProps) {
  const details = normalizeAttendeeDetails(event?.attendeeDetails ?? event?.attendees)
  const rawKey = event?.eventKey ?? event?.event_key
  const eventKey = typeof rawKey === 'string' ? rawKey : null
  const view = meetingPeopleView(
    details,
    countValue(event?.attendeeCount ?? event?.attendee_count),
    prepForEvent(normalizeMeetingPrep(prep), eventKey),
    now ?? Date.now()
  )
  if (view.rows.length === 0) {
    return null
  }

  return (
    <div className="min-w-0 space-y-1">
      {view.rows.map(row => (
        <div className="min-w-0" key={row.key}>
          <p className="truncate text-xs text-(--ui-text-secondary)" title={row.text}>
            <span className="text-(--ui-text-primary)">{row.name}</span>
            {row.detail ? ` — ${row.detail}` : null}
          </p>
          {row.prepLine ? (
            <p className="truncate text-xs text-(--ui-text-tertiary)" title={row.prepLine}>
              {row.prepLine}
            </p>
          ) : null}
        </div>
      ))}
      {view.overflow > 0 ? (
        <p className="text-xs text-(--ui-text-tertiary)">+{view.overflow} more</p>
      ) : null}
      {view.talkingPoint ? (
        <p className="truncate text-xs text-(--ui-text-secondary)" title={view.talkingPoint}>
          Talking point: {view.talkingPoint}
        </p>
      ) : null}
    </div>
  )
}
