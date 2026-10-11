// Refresh = trigger the curate cron job in the background, then poll /feed
// until curated_at moves past the `since` the trigger returned (R5). An LLM
// curation outlives the renderer's ~15 s IPC timeout, so nothing waits on it.

export const REFRESH_POLL_INTERVAL_MS = 5_000
export const REFRESH_TIMEOUT_MS = 120_000

export function curationAdvanced(since: number | null, curatedAt: number | null | undefined): boolean {
  return typeof curatedAt === 'number' && (since === null || curatedAt > since)
}

export interface WaitForCurationOptions {
  /** curated_at (ms) before the trigger; null when the feed was never curated. */
  since: number | null
  /** Fetch the feed; resolves its curated_at (ms), or undefined when the fetch failed. */
  poll: () => Promise<number | null | undefined>
  sleep: (ms: number) => Promise<void>
  now: () => number
  intervalMs?: number
  timeoutMs?: number
}

/** Resolves true once curated_at advances, false after the timeout. Never polls faster than 5 s. */
export async function waitForCuration(options: WaitForCurationOptions): Promise<boolean> {
  const intervalMs = Math.max(REFRESH_POLL_INTERVAL_MS, options.intervalMs ?? REFRESH_POLL_INTERVAL_MS)
  const timeoutMs = Math.min(REFRESH_TIMEOUT_MS, options.timeoutMs ?? REFRESH_TIMEOUT_MS)
  const startedAt = options.now()

  while (options.now() - startedAt < timeoutMs) {
    await options.sleep(intervalMs)
    let curatedAt: number | null | undefined
    try {
      curatedAt = await options.poll()
    } catch {
      curatedAt = undefined
    }
    if (curationAdvanced(options.since, curatedAt)) {
      return true
    }
  }
  return false
}
