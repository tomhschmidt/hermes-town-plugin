// Explicit-zone time formatting for the header and Up next (S1). Every
// function takes the zone it renders in; nothing reads the renderer's
// implicit zone. Abbreviations mirror operator_profile.zone_abbreviation.

const GENERIC_ZONE_NAMES: Readonly<Record<string, string>> = {
  'America/New_York': 'ET',
  'America/Detroit': 'ET',
  'America/Toronto': 'ET',
  'America/Indiana/Indianapolis': 'ET',
  'America/Chicago': 'CT',
  'America/Winnipeg': 'CT',
  'America/Denver': 'MT',
  'America/Edmonton': 'MT',
  'America/Boise': 'MT',
  'America/Phoenix': 'MT',
  'America/Los_Angeles': 'PT',
  'America/Vancouver': 'PT',
  'America/Anchorage': 'AKT',
  'Pacific/Honolulu': 'HT',
  'Asia/Singapore': 'SGT',
  'Asia/Hong_Kong': 'HKT',
  'Asia/Tokyo': 'JST',
  'Asia/Seoul': 'KST',
  'Asia/Shanghai': 'CST',
  'Asia/Kolkata': 'IST',
  'Asia/Dubai': 'GST',
  'Asia/Bangkok': 'ICT',
  'Asia/Ho_Chi_Minh': 'ICT',
  'Asia/Jakarta': 'WIB',
  'Asia/Manila': 'PHT',
  'Asia/Kuala_Lumpur': 'MYT',
  'Atlantic/Reykjavik': 'GMT'
}

const CET_ZONES = [
  'Europe/Paris', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Rome', 'Europe/Amsterdam',
  'Europe/Brussels', 'Europe/Zurich', 'Europe/Vienna', 'Europe/Stockholm',
  'Europe/Copenhagen', 'Europe/Oslo', 'Europe/Prague', 'Europe/Warsaw'
]

const SEASONAL_ZONE_NAMES: Readonly<Record<string, readonly [string, string]>> = {
  'Europe/London': ['GMT', 'BST'],
  'Europe/Dublin': ['GMT', 'IST'],
  'Europe/Lisbon': ['WET', 'WEST'],
  'Europe/Athens': ['EET', 'EEST'],
  'Europe/Helsinki': ['EET', 'EEST'],
  'Australia/Sydney': ['AEST', 'AEDT'],
  'Australia/Melbourne': ['AEST', 'AEDT'],
  'Pacific/Auckland': ['NZST', 'NZDT'],
  ...Object.fromEntries(CET_ZONES.map(zone => [zone, ['CET', 'CEST'] as const]))
}

const HOUR_MS = 60 * 60 * 1000
const MINUTE_MS = 60 * 1000

export interface ZonedParts {
  year: number
  month: number
  day: number
  hour: number
  minute: number
  weekday: string
}

export interface EventTimeZones {
  homeTz: string
  displayTz: string
  eventTz?: string | null
}

const partFormatters = new Map<string, Intl.DateTimeFormat>()

function partFormatter(timeZone: string): Intl.DateTimeFormat {
  let formatter = partFormatters.get(timeZone)
  if (!formatter) {
    formatter = new Intl.DateTimeFormat('en-US', {
      timeZone,
      year: 'numeric',
      month: 'numeric',
      day: 'numeric',
      hour: 'numeric',
      minute: 'numeric',
      weekday: 'short',
      hourCycle: 'h23'
    })
    partFormatters.set(timeZone, formatter)
  }
  return formatter
}

/** A usable IANA zone: ``value`` when valid, else ``fallback``. */
export function safeTimeZone(value: string | null | undefined, fallback = 'UTC'): string {
  const zone = typeof value === 'string' ? value.trim() : ''
  if (!zone) {
    return fallback
  }
  try {
    partFormatter(zone)
    return zone
  } catch {
    return fallback
  }
}

export function zonedParts(at: number, timeZone: string): ZonedParts {
  const parts = Object.fromEntries(
    partFormatter(safeTimeZone(timeZone)).formatToParts(new Date(at)).map(part => [part.type, part.value])
  )
  return {
    year: Number(parts.year),
    month: Number(parts.month),
    day: Number(parts.day),
    hour: Number(parts.hour) % 24,
    minute: Number(parts.minute),
    weekday: String(parts.weekday ?? '')
  }
}

/** Minutes east of UTC for ``timeZone`` at instant ``at``. */
export function zoneOffsetMinutes(at: number, timeZone: string): number {
  const parts = zonedParts(at, timeZone)
  const asUtc = Date.UTC(parts.year, parts.month - 1, parts.day, parts.hour, parts.minute)
  const truncated = Math.floor(at / MINUTE_MS) * MINUTE_MS
  return Math.round((asUtc - truncated) / MINUTE_MS)
}

/** "2026-10-11" — the calendar date at ``at`` in ``timeZone``. */
export function dateKeyIn(at: number, timeZone: string): string {
  const { year, month, day } = zonedParts(at, timeZone)
  return `${year}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`
}

/** The date key ``days`` calendar days after ``at``'s date in ``timeZone``. */
export function shiftedDateKey(at: number, timeZone: string, days: number): string {
  const { year, month, day } = zonedParts(at, timeZone)
  const shifted = new Date(Date.UTC(year, month - 1, day + days))
  return `${shifted.getUTCFullYear()}-${String(shifted.getUTCMonth() + 1).padStart(2, '0')}-${String(shifted.getUTCDate()).padStart(2, '0')}`
}

/** "ET", "SGT", "BST", else "UTC+8" — the zone as people say it at ``at``. */
export function zoneAbbreviation(timeZone: string, at: number): string {
  const zone = safeTimeZone(timeZone, '')
  if (!zone) {
    return 'UTC'
  }
  const generic = GENERIC_ZONE_NAMES[zone]
  if (generic) {
    return generic
  }
  const offset = zoneOffsetMinutes(at, zone)
  const seasonal = SEASONAL_ZONE_NAMES[zone]
  if (seasonal) {
    const year = zonedParts(at, zone).year
    const standard = Math.min(
      zoneOffsetMinutes(Date.UTC(year, 0, 1), zone),
      zoneOffsetMinutes(Date.UTC(year, 6, 1), zone)
    )
    return offset > standard ? seasonal[1] : seasonal[0]
  }
  if (offset === 0) {
    return 'UTC'
  }
  const sign = offset > 0 ? '+' : '-'
  const hours = Math.floor(Math.abs(offset) / 60)
  const minutes = Math.abs(offset) % 60
  return `UTC${sign}${hours}${minutes ? `:${String(minutes).padStart(2, '0')}` : ''}`
}

/** "11:00 AM" in ``timeZone``. */
export function formatClock(at: number, timeZone: string): string {
  const { hour, minute } = zonedParts(at, timeZone)
  const suffix = hour < 12 ? 'AM' : 'PM'
  const hour12 = hour % 12 === 0 ? 12 : hour % 12
  return `${hour12}:${String(minute).padStart(2, '0')} ${suffix}`
}

function instantOf(value: string | number): number {
  return typeof value === 'number' ? value : Date.parse(value)
}

/**
 * An event's start: "11:00 AM" when every relevant zone agrees, else the
 * home-zone time plus the other zone ("11:00 AM ET · 11:00 PM SGT"), naming
 * the weekday on the second when its date differs ("9:00 PM ET · Mon 9:00 AM SGT").
 * The other zone is where the operator is (``displayTz``) when away, else the
 * event's own zone.
 */
export function formatEventTime(startIso: string | number, zones: EventTimeZones): string {
  const at = instantOf(startIso)
  if (!Number.isFinite(at)) {
    return ''
  }
  const home = safeTimeZone(zones.homeTz)
  const display = safeTimeZone(zones.displayTz, home)
  const event = safeTimeZone(zones.eventTz ?? '', '')
  const homeOffset = zoneOffsetMinutes(at, home)
  const other = zoneOffsetMinutes(at, display) !== homeOffset
    ? display
    : event && zoneOffsetMinutes(at, event) !== homeOffset ? event : ''
  if (!other) {
    return formatClock(at, display)
  }
  const weekday = dateKeyIn(at, other) !== dateKeyIn(at, home) ? `${zonedParts(at, other).weekday} ` : ''
  return `${formatClock(at, home)} ${zoneAbbreviation(home, at)} · ${weekday}${formatClock(at, other)} ${zoneAbbreviation(other, at)}`
}

/** "now" while under way, else "in 40m", "in 2h 10m", "in 7h". */
export function relativeUntil(startAt: number, endAt: number, now: number): string {
  if (startAt <= now && now < endAt) {
    return 'now'
  }
  const minutes = Math.round((startAt - now) / MINUTE_MS)
  if (minutes <= 0) {
    return 'now'
  }
  if (minutes < 60) {
    return `in ${minutes}m`
  }
  if (startAt - now >= 6 * HOUR_MS) {
    return `in ${Math.round(minutes / 60)}h`
  }
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  return rest ? `in ${hours}h ${rest}m` : `in ${hours}h`
}

/** The zone the dashboard speaks in: away zone while away, else home, else ``fallback``. */
export function displayTimeZone(
  operator: { homeTimezone: string } | null | undefined,
  where: { status: string; timezone: string } | null | undefined,
  fallback: string
): string {
  if (where?.status === 'away' && where.timezone) {
    return safeTimeZone(where.timezone, fallback)
  }
  return safeTimeZone(operator?.homeTimezone, fallback)
}
