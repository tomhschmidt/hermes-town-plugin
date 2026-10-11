// The Tasks card's sessions fallback (U13), shown only while the kanban board
// is unreachable: the user's own sessions, never routine runs or the curator.

export interface FallbackSessionLike {
  preview: string
  source: string
  title: string
}

export interface CronJobRef {
  id: string
  name: string
}

export interface SessionSource {
  icon: string
  label: string
}

// Hermes titles a cron run's session "<job name, first 60 chars> · Oct 11
// 08:20" (a "#2" suffix on a clash), or "cron <job id>" for a nameless job.
const CRON_TITLE_NAME_CHARS = 60

const KNOWN_SOURCES: ReadonlyMap<string, SessionSource> = new Map([
  ['telegram', { icon: 'send', label: 'Telegram' }],
  ['email', { icon: 'mail', label: 'Email' }],
  ['gmail', { icon: 'mail', label: 'Gmail' }],
  ['cli', { icon: 'terminal', label: 'CLI' }],
  ['tui', { icon: 'terminal', label: 'Terminal' }],
  ['desktop', { icon: 'device-desktop', label: 'Desktop' }],
  ['api', { icon: 'plug', label: 'API' }],
  ['api_server', { icon: 'plug', label: 'API' }],
  ['webhook', { icon: 'plug', label: 'Webhook' }],
  ['cron', { icon: 'clock', label: 'Routine' }]
])

function foldTitle(value: string): string {
  return value.toLowerCase().replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim()
}

function referencesTownFeedCurator(value: string): boolean {
  const folded = value.toLowerCase()
  return folded.includes('town feed curator') || folded.includes('town_feed_curate')
}

/** A routine's run: source ``cron``, or titled after a cron job. */
export function isCronSession(session: Pick<FallbackSessionLike, 'source' | 'title'>, jobs: readonly CronJobRef[]): boolean {
  if (session.source.trim().toLowerCase() === 'cron') {
    return true
  }
  const title = foldTitle(session.title)
  if (!title) {
    return false
  }
  return jobs.some(job => {
    const name = foldTitle(job.name).slice(0, CRON_TITLE_NAME_CHARS).trim()
    if (name && (title === name || title.startsWith(`${name} · `) || title.startsWith(`${name} #`))) {
      return true
    }
    const id = job.id.trim().toLowerCase()
    return Boolean(id) && (title === `cron ${id}` || title.startsWith(`cron ${id} `))
  })
}

/** Recent sessions for the fallback: no cron runs, no curator sessions. */
export function tasksFallbackSessions<T extends FallbackSessionLike>(rows: readonly T[], jobs: readonly CronJobRef[]): T[] {
  return rows.filter(row =>
    !referencesTownFeedCurator(row.title)
    && !referencesTownFeedCurator(row.preview)
    && !isCronSession(row, jobs)
  )
}

/** The small icon (and its tooltip) standing in for a session's source tag. */
export function sessionSource(source: string): SessionSource {
  const key = source.trim().toLowerCase()
  const known = KNOWN_SOURCES.get(key)
  if (known) {
    return known
  }
  const label = key ? key.replace(/[-_]+/g, ' ').replace(/^./, letter => letter.toUpperCase()) : 'Session'
  return { icon: 'comment-discussion', label }
}
