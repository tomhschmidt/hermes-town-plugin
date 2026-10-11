// How Town shows people (S2, reshaped by U13): the people line under an Up
// next meeting and the Schedule dialog. Names first — never a full raw email
// as the primary text — with the true attendee total behind "+N".

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

export interface MeetingPersonEntry {
  key: string
  name: string
  /** "— Acme Capital", "(first meeting)", or both; null when nothing is known. */
  detail: string | null
  text: string
  prepLine: string | null
}

export interface MeetingPeopleSummary {
  /** Non-colleagues listed by name (at most three), people with prep first. */
  people: MeetingPersonEntry[]
  /** One muted phrase per org: "8 Example Co colleagues". */
  colleagues: string[]
  /** Unlisted non-colleagues plus attendees beyond the scanner's cap. */
  overflow: number
  talkingPoint: string | null
  /** The whole people line; empty when there is nobody to show. */
  text: string
}

const MAX_LISTED_PEOPLE = 3

/** "8 Example Co colleagues" / "1 Example Co colleague" / "3 colleagues". */
export function colleaguePhrase(count: number, org: string | null | undefined): string {
  const noun = count === 1 ? 'colleague' : 'colleagues'
  const name = org?.trim()
  return name ? `${count} ${name} ${noun}` : `${count} ${noun}`
}

function personEntry(person: PersonLike, index: number, prepLine: string | null): MeetingPersonEntry {
  const name = attendeeDisplayName(person)
  const org = person.org?.trim()
  const parts = [org ? `— ${org}` : null, person.relationship === 'new' ? '(first meeting)' : null]
  const detail = parts.filter((part): part is string => Boolean(part)).join(' ') || null
  return {
    key: person.email ?? `${name}-${index}`,
    name,
    detail,
    text: detail ? `${name} ${detail}` : name,
    prepLine
  }
}

/**
 * Who is in a meeting, in one line (U13): colleagues collapse into one phrase
 * per org, up to three other people are named ("Name (first meeting)",
 * "Name — Org"), then "+N more". Declined attendees are left out entirely;
 * prep lines belong only to the people named.
 */
export function meetingPeopleSummary(
  details: readonly PersonLike[],
  attendeeCount: number | null | undefined,
  prep: MeetingPrep | null,
  limit = MAX_LISTED_PEOPLE
): MeetingPeopleSummary {
  const lines = new Map((prep?.people ?? []).map(person => [person.email, person.line]))
  const lineFor = (person: PersonLike): string | null => (person.email ? lines.get(person.email) ?? null : null)
  const others = details.filter(person => person.self !== true)
  const attending = others.filter(person => person.response !== 'declined')
  const individuals = attending.filter(person => person.relationship !== 'colleague')
  const listed = [
    ...individuals.filter(person => lineFor(person)),
    ...individuals.filter(person => !lineFor(person))
  ].slice(0, limit)

  const groups = new Map<string, { org: string | null; count: number }>()
  for (const person of attending) {
    if (person.relationship !== 'colleague') {
      continue
    }
    const org = person.org?.trim() || null
    const key = org?.toLowerCase() ?? ''
    const group = groups.get(key) ?? { org, count: 0 }
    group.count += 1
    groups.set(key, group)
  }
  const colleagues = [...groups.values()]
    .sort((a, b) => b.count - a.count)
    .map(group => colleaguePhrase(group.count, group.org))

  const people = listed.map((person, index) => personEntry(person, index, lineFor(person)))
  if (people.length === 0 && colleagues.length === 0) {
    return { people, colleagues, overflow: 0, talkingPoint: null, text: '' }
  }
  // Attendees past the scanner's cap are unknown (maybe colleagues, maybe
  // not), so they only ever count toward "+N more".
  const beyondCap = Math.max(0, otherAttendeeTotal(details, attendeeCount) - others.length)
  const overflow = individuals.length - listed.length + beyondCap
  const text = [...people.map(person => person.text), ...colleagues, overflow > 0 ? `+${overflow} more` : null]
    .filter((part): part is string => Boolean(part))
    .join(' · ')
  return { people, colleagues, overflow, talkingPoint: prep?.talkingPoint ?? null, text }
}
