// People facts shared by town-feed (the bridge) and town-home (S2): calendar
// attendees enriched from the people directory, and the day brief's
// per-meeting prep lines. Every field is optional on the wire, so these pure
// normalizers accept both the /feed (snake_case) and the bridge (camelCase)
// shapes and never hand render code a malformed record.

export type Relationship = 'colleague' | 'contact' | 'correspondent' | 'new'

export interface AttendeeDetail {
  email: string | null
  name: string | null
  org: string | null
  relationship: Relationship | null
  /** Last email exchanged, epoch milliseconds. */
  lastContactAt: number | null
  response: string | null
  self: boolean
}

export interface MeetingPrepPerson {
  email: string
  line: string
}

export interface MeetingPrep {
  eventKey: string
  people: MeetingPrepPerson[]
  talkingPoint: string | null
}

const RELATIONSHIPS: ReadonlySet<string> = new Set(['colleague', 'contact', 'correspondent', 'new'])
const MAX_ATTENDEES = 8
const MAX_PREP = 2
const MAX_PREP_PEOPLE = 6

function asRecord(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Record<string, unknown>) : null
}

function text(value: unknown, limit: number): string | null {
  if (typeof value !== 'string') {
    return null
  }
  const compact = value.replace(/\s+/g, ' ').trim()
  return compact ? compact.slice(0, limit) : null
}

/** Epoch seconds (the gateway's ``*_ts``), epoch ms, or an ISO string, as ms. */
function timestampMs(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value) && value > 0) {
    return value < 1_000_000_000_000 ? value * 1_000 : value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Date.parse(value)
    return Number.isNaN(parsed) ? null : parsed
  }
  return null
}

export function relationshipValue(value: unknown): Relationship | null {
  return typeof value === 'string' && RELATIONSHIPS.has(value) ? (value as Relationship) : null
}

function emailValue(value: unknown): string | null {
  const email = text(value, 254)?.toLowerCase() ?? null
  return email && /^[^\s@]+@[^\s@]+$/.test(email) ? email : null
}

export function normalizeAttendeeDetails(value: unknown): AttendeeDetail[] {
  if (!Array.isArray(value)) {
    return []
  }

  const details: AttendeeDetail[] = []
  for (const row of value) {
    const record = typeof row === 'string' ? { name: row } : asRecord(row)
    if (!record) {
      continue
    }
    const rawName = text(record.name, 80)
    const email = emailValue(record.email) ?? (rawName && emailValue(rawName)) ?? null
    const name = rawName && !rawName.includes('@') ? rawName : null
    if (!email && !name) {
      continue
    }
    details.push({
      email,
      name,
      org: text(record.org, 80),
      relationship: relationshipValue(record.relationship),
      lastContactAt: timestampMs(record.lastContactAt ?? record.last_contact_ts),
      response: text(record.response, 24),
      self: record.self === true
    })
    if (details.length >= MAX_ATTENDEES) {
      break
    }
  }
  return details
}

export function normalizeMeetingPrep(value: unknown): MeetingPrep[] {
  const rows = Array.isArray(value) ? value : asRecord(value) ? [value] : []
  const prep: MeetingPrep[] = []
  const seen = new Set<string>()
  for (const row of rows) {
    const record = asRecord(row)
    const eventKey = text(record?.eventKey ?? record?.event_key, 64)
    if (!record || !eventKey || seen.has(eventKey)) {
      continue
    }
    const peopleRows = Array.isArray(record.people) ? record.people : []
    const people = peopleRows.slice(0, MAX_PREP_PEOPLE).flatMap(personRow => {
      const person = asRecord(personRow)
      const email = emailValue(person?.email)
      const line = text(person?.line, 90)
      return email && line ? [{ email, line }] : []
    })
    const talkingPoint = text(record.talkingPoint ?? record.talking_point, 140)
    if (people.length === 0 && !talkingPoint) {
      continue
    }
    seen.add(eventKey)
    prep.push({ eventKey, people, talkingPoint })
    if (prep.length >= MAX_PREP) {
      break
    }
  }
  return prep
}
