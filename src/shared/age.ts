// Compact ages and staleness for Town cards (U13): a footer says "27m", not
// "22 min. ago", and a header shows its freshness stamp only once it is stale.

const MINUTE_MS = 60_000
const HOUR_MS = 60 * MINUTE_MS
const DAY_MS = 24 * HOUR_MS

export const STALE_AFTER_MS = 60 * MINUTE_MS

/** "now", "27m", "5h", "3d" (whole units, floored); null when the time is unknown. */
export function compactAge(at: number | null | undefined, now: number): string | null {
  if (typeof at !== 'number' || !Number.isFinite(at)) {
    return null
  }
  const elapsed = Math.max(0, now - at)
  if (elapsed < MINUTE_MS) {
    return 'now'
  }
  if (elapsed < HOUR_MS) {
    return `${Math.floor(elapsed / MINUTE_MS)}m`
  }
  if (elapsed < DAY_MS) {
    return `${Math.floor(elapsed / HOUR_MS)}h`
  }
  return `${Math.floor(elapsed / DAY_MS)}d`
}

/** True once data is more than 60 minutes old; an unknown time is never stale. */
export function isStale(at: number | null | undefined, now: number): boolean {
  return typeof at === 'number' && Number.isFinite(at) && now - at > STALE_AFTER_MS
}
