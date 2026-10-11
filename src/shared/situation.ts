// Who and where the operator is, plus the schedule facts the header needs
// (S1). /feed sends snake_case; the town-feed bridge republishes camelCase,
// so every normalizer accepts both. Shared by town-feed and town-home.

type UnknownRecord = Record<string, unknown>

export interface TownOperator {
  name: string
  preferredName: string
  homeCity: string
  homeTimezone: string
}

export type TownWhereStatus = 'home' | 'away' | 'traveling_today' | 'just_returned'

export interface TownWhere {
  status: TownWhereStatus
  city: string
  timezone: string
  label: string
  evidence: string[]
  source: string
  since: number | null
  until: number | null
}

export interface TownScheduleConflict {
  a: string
  b: string
  aStartAt: number | null
  bStartAt: number | null
}

export interface TownScheduleCluster {
  summaries: string[]
  startsAt: Array<number | null>
}

export interface TownScheduleContext {
  conflicts: TownScheduleConflict[]
  clusters: TownScheduleCluster[]
}

const WHERE_STATUSES: ReadonlySet<string> = new Set(['home', 'away', 'traveling_today', 'just_returned'])

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function text(value: unknown, limit = 120): string {
  return typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, limit) : ''
}

function instant(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 1_000_000_000_000 ? value * 1_000 : value
  }
  if (typeof value === 'string' && value.trim()) {
    const parsed = Date.parse(value)
    return Number.isNaN(parsed) ? null : parsed
  }
  return null
}

/** True when the runtime knows the IANA zone name. */
export function isValidTimeZone(value: unknown): value is string {
  if (typeof value !== 'string' || !value.trim()) {
    return false
  }
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: value.trim() })
    return true
  } catch {
    return false
  }
}

export function normalizeOperator(value: unknown): TownOperator | null {
  const record = asRecord(value)
  if (!record) {
    return null
  }
  const homeTimezone = text(record.homeTimezone ?? record.home_timezone, 64)
  return {
    name: text(record.name),
    preferredName: text(record.preferredName ?? record.preferred_name, 40),
    homeCity: text(record.homeCity ?? record.home_city, 80),
    homeTimezone: isValidTimeZone(homeTimezone) ? homeTimezone : ''
  }
}

export function normalizeWhere(value: unknown): TownWhere | null {
  const record = asRecord(value)
  const status = text(record?.status, 32)
  const label = text(record?.label, 80)
  if (!record || !WHERE_STATUSES.has(status) || !label) {
    return null
  }
  const timezone = text(record.timezone, 64)
  const evidence = Array.isArray(record.evidence)
    ? record.evidence.flatMap(entry => {
        const line = text(entry, 120)
        return line ? [line] : []
      }).slice(0, 3)
    : []
  return {
    status: status as TownWhereStatus,
    city: text(record.city, 80),
    timezone: isValidTimeZone(timezone) ? timezone : '',
    label,
    evidence,
    source: text(record.source, 32),
    since: instant(record.since),
    until: instant(record.until)
  }
}

export function normalizeScheduleContext(value: unknown): TownScheduleContext | null {
  const record = asRecord(value)
  if (!record) {
    return null
  }
  const conflictRows = Array.isArray(record.conflicts) ? record.conflicts : []
  const conflicts = conflictRows.slice(0, 6).flatMap(row => {
    const conflict = asRecord(row)
    const a = text(conflict?.a, 200)
    const b = text(conflict?.b, 200)
    if (!conflict || !a || !b) {
      return []
    }
    return [{
      a,
      b,
      aStartAt: instant(conflict.aStartAt ?? conflict.a_start_iso),
      bStartAt: instant(conflict.bStartAt ?? conflict.b_start_iso)
    }]
  })
  const clusterRows = Array.isArray(record.clusters)
    ? record.clusters
    : Array.isArray(record.start_clusters) ? record.start_clusters : []
  const clusters = clusterRows.slice(0, 3).flatMap(row => {
    const cluster = asRecord(row)
    const summaries = Array.isArray(cluster?.summaries) ? cluster.summaries.map(entry => text(entry, 200)) : []
    const rawStarts = cluster?.startsAt ?? cluster?.starts_iso
    const startsAt = Array.isArray(rawStarts) ? rawStarts.map(instant) : []
    return cluster && summaries.length > 0 ? [{ summaries, startsAt }] : []
  })
  return { conflicts, clusters }
}
