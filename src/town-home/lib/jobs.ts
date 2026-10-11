// Town's own plumbing jobs (scanners + curator) are infrastructure, not the
// user's routines: hidden from Routines, the header next-routine line, and
// Recents (R5). Run history opened by job id still works for them.

const INTERNAL_JOB_NAMES: ReadonlySet<string> = new Set([
  'town-gmail-scan',
  'town-telegram-scan',
  'town-calendar-scan',
  'town-feed-curate',
  'town feed curator'
])

const INTERNAL_JOB_SCRIPTS: ReadonlySet<string> = new Set([
  'town-gmail-scan.py',
  'town-telegram-scan.py',
  'town-calendar-scan.py',
  'town-curate.py'
])

export interface TownJobLike {
  name?: unknown
  prompt?: unknown
  promptPreview?: unknown
  script?: unknown
}

function folded(value: unknown): string {
  return typeof value === 'string' ? value.trim().toLowerCase() : ''
}

export function isInternalTownJob(job: TownJobLike | null | undefined): boolean {
  if (!job) {
    return false
  }
  if (INTERNAL_JOB_NAMES.has(folded(job.name))) {
    return true
  }
  const script = folded(job.script).split(/[\\/]/).pop() ?? ''
  if (INTERNAL_JOB_SCRIPTS.has(script)) {
    return true
  }
  // Legacy agent-mode curator jobs only name the tool in their prompt.
  return [job.prompt, job.promptPreview].some(prompt => folded(prompt).includes('town_feed_curate'))
}
