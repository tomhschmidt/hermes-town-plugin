// How Town shows people (S2): attendee rows for MeetingPeople and the
// Schedule dialog. Names first — never a full raw email as the primary text —
// with the relationship humanized and the true attendee total behind "+N".

import type { AttendeeDetail, MeetingPrep, Relationship } from '../../shared/people.ts'

const RELATIONSHIP_LABELS: Readonly<Record<Relationship, string>> = {
  colleague: 'colleague',
  contact: 'in your contacts',
  correspondent: "you've emailed",
  new: 'first meeting'
}

export type PersonLike = Pick<AttendeeDetail, 'email' | 'name'> & Partial<AttendeeDetail>

export function relationshipLabel(relationship: Relationship | null | undefined): string | null {
  return relationship ? RELATIONSHIP_LABELS[relationship] ?? null : null
}

/** "alice.smith+town@example.com" -> "Alice Smith". */
export function displayNameFromEmail(email: string): string {
  const local = (email.split('@', 1)[0] ?? '').split('+', 1)[0] ?? ''
  return local
    .split(/[._-]+/)
    .filter(Boolean)
    .map(word => (/^[a-z]/i.test(word) ? word[0].toUpperCase() + word.slice(1) : word))
    .join(' ')
}

export function attendeeDisplayName(person: PersonLike): string {
  const name = person.name?.trim() ?? ''
  if (name && !name.includes('@')) {
    return name
  }
  const email = person.email?.trim() || (name.includes('@') ? name : '')
  return email ? displayNameFromEmail(email) || 'Guest' : 'Guest'
}

/** "last email 3 days ago" from an epoch-ms timestamp; null when unknown. */
export function lastTouchText(lastContactAt: number | null | undefined, now: number): string | null {
  if (typeof lastContactAt !== 'number' || !Number.isFinite(lastContactAt) || lastContactAt <= 0) {
    return null
  }
  const seconds = Math.max(0, (now - lastContactAt) / 1_000)
  if (seconds < 3_600) {
    return 'last email within the hour'
  }
  if (seconds < 48 * 3_600) {
    const hours = Math.max(1, Math.round(seconds / 3_600))
    return `last email ${hours} hour${hours === 1 ? '' : 's'} ago`
  }
  const days = seconds / 86_400
  if (days < 14) {
    return `last email ${Math.round(days)} days ago`
  }
  if (days < 60) {
    return `last email ${Math.round(days / 7)} weeks ago`
  }
  if (days < 365) {
    return `last email ${Math.max(2, Math.round(days / 30))} months ago`
  }
  return 'last email over a year ago'
}

export interface PersonRowParts {
  name: string
  detail: string
}

export function personRowParts(person: PersonLike, now: number): PersonRowParts {
  const detail = [
    person.org?.trim() || null,
    relationshipLabel(person.relationship),
    lastTouchText(person.lastContactAt, now),
    person.response === 'declined' ? 'declined' : null
  ].filter((part): part is string => Boolean(part))
  return { name: attendeeDisplayName(person), detail: detail.join(' · ') }
}

/** "Name — org · relationship · last touch" (segments omitted when unknown). */
export function personRowText(person: PersonLike, now: number): string {
  const { name, detail } = personRowParts(person, now)
  return detail ? `${name} — ${detail}` : name
}

/** How many attendees "+N more" stands for (never negative). */
export function attendeeOverflow(total: number | null | undefined, shown: number): number {
  if (typeof total !== 'number' || !Number.isFinite(total)) {
    return 0
  }
  return Math.max(0, Math.floor(total) - Math.max(0, shown))
}

/** Attendees other than the operator, from the TRUE count (attendee_count includes self). */
export function otherAttendeeTotal(details: readonly PersonLike[], attendeeCount: number | null | undefined): number {
  const others = details.filter(person => person.self !== true).length
  if (typeof attendeeCount !== 'number' || !Number.isFinite(attendeeCount)) {
    return others
  }
  const selves = details.length - others
  return Math.max(others, Math.floor(attendeeCount) - selves)
}

/** Schedule dialog line: "with Bob Lee, Carol (declined), Dana +38", or null. */
export function scheduleAttendeeSummary(
  details: readonly PersonLike[],
  attendeeCount: number | null | undefined,
  limit = 3
): string | null {
  const others = details.filter(person => person.self !== true)
  if (others.length === 0) {
    return null
  }
  const shown = others.slice(0, limit)
  const names = shown.map(person => {
    const name = attendeeDisplayName(person)
    return person.response === 'declined' ? `${name} (declined)` : name
  })
  const overflow = attendeeOverflow(otherAttendeeTotal(details, attendeeCount), shown.length)
  return `with ${names.join(', ')}${overflow > 0 ? ` +${overflow}` : ''}`
}

export function prepForEvent(
  prep: readonly MeetingPrep[] | null | undefined,
  eventKey: string | null | undefined
): MeetingPrep | null {
  if (!eventKey || !prep) {
    return null
  }
  return prep.find(entry => entry.eventKey === eventKey) ?? null
}

export interface MeetingPersonRow {
  key: string
  name: string
  detail: string
  text: string
  prepLine: string | null
}

export interface MeetingPeopleView {
  rows: MeetingPersonRow[]
  overflow: number
  talkingPoint: string | null
}

/**
 * Up to ``limit`` non-self attendees — people with a prep line first,
 * declined last — plus "+N more" from the true total and the talking point.
 */
export function meetingPeopleView(
  details: readonly PersonLike[],
  attendeeCount: number | null | undefined,
  prep: MeetingPrep | null,
  now: number,
  limit = 5
): MeetingPeopleView {
  const lines = new Map((prep?.people ?? []).map(person => [person.email, person.line]))
  const lineFor = (person: PersonLike): string | null => (person.email ? lines.get(person.email) ?? null : null)
  const others = details.filter(person => person.self !== true)
  const declined = (person: PersonLike) => person.response === 'declined'
  const ordered = [
    ...others.filter(person => !declined(person) && lineFor(person)),
    ...others.filter(person => !declined(person) && !lineFor(person)),
    ...others.filter(declined)
  ]
  const rows = ordered.slice(0, limit).map((person, index) => {
    const { name, detail } = personRowParts(person, now)
    return {
      key: person.email ?? `${name}-${index}`,
      name,
      detail,
      text: detail ? `${name} — ${detail}` : name,
      prepLine: lineFor(person)
    }
  })
  return {
    rows,
    overflow: attendeeOverflow(otherAttendeeTotal(details, attendeeCount), rows.length),
    talkingPoint: prep?.talkingPoint ?? null
  }
}
