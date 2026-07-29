import {
  atom,
  Button,
  Codicon,
  EmptyState,
  ErrorState,
  host,
  relativeTime,
  ROUTES_AREA,
  ScrollArea,
  Skeleton,
  Tip,
  useValue,
  type PluginContext
} from '@hermes/plugin-sdk'
import { useEffect } from 'react'

const FEED_POLL_INTERVAL_MS = 60_000
const EVENT_REFRESH_DEBOUNCE_MS = 2_000
const DISMISSED_IDS_KEY = 'dismissed-ids'
const PROMOTED_IDS_KEY = 'promoted-ids'
const UNDO_DISMISS_MS = 6_000
const STRIP_TRANSITION_MS = 150
const NOW_TICK_INTERVAL_MS = 30_000

let pluginContext: PluginContext | null = null

type UnknownRecord = Record<string, unknown>

interface FeedItem {
  id: string
  headline: string
  summary: string
  source: string
  score: number
  createdAt: number | null
  actionHint: string | null
}

interface NextEvent {
  account: string
  startAt: number
  summary: string
  location: string | null
}

interface ScheduleEvent {
  account: string
  allDay: boolean
  endAt: number
  startAt: number
  summary: string
  location: string | null
  conference: string | null
  attendees: string[]
  attendeeCount: number | null
  multiDay: boolean
}

interface DayBriefCallout {
  kind: string
  text: string
}

interface DayBriefTrip {
  label: string
  dayN: number | null
  totalDays: number | null
}

interface DayBrief {
  daySummary: string
  callouts: DayBriefCallout[]
  trip: DayBriefTrip | null
  generatedAt: number | null
}

type SuggestionKind = 'followup' | 'digest' | 'maintenance'

interface Suggestion {
  id: string
  kind: SuggestionKind
  label: string
  prompt: string
}

interface FeedState {
  curatedAt: number | null
  curating: boolean
  dayBrief: DayBrief | null
  error: string | null
  items: FeedItem[]
  loading: boolean
  nextEvent: NextEvent | null
  schedule: ScheduleEvent[]
  suggestions: Suggestion[]
  suggestionsGeneratedAt: number | null
  unavailable: boolean
}

interface FeedAtom {
  get(): FeedState
  set(value: FeedState): void
  subscribe(listener: (value: FeedState) => void): () => void
}

interface RecentlyDismissed {
  id: string
  headline: string
  expiresAt: number
  item: FeedItem
}

interface RecentlyDismissedAtom {
  get(): RecentlyDismissed[]
  set(value: RecentlyDismissed[]): void
  subscribe(listener: (value: RecentlyDismissed[]) => void): () => void
}

interface StringArrayAtom {
  get(): string[]
  set(value: string[]): void
  subscribe(listener: (value: string[]) => void): () => void
}

interface KanbanTask {
  id: string
  title: string
  status: string
  priority: number | null
  createdAt: number | null
  completedAt: number | null
  sessionId: string | null
  updatedAt: number | null
}

interface KanbanColumn {
  name: string
  tasks: KanbanTask[]
}

interface KanbanState {
  columns: KanbanColumn[]
  error: string | null
  loading: boolean
  unavailable: boolean
}

interface KanbanAtom {
  get(): KanbanState
  set(value: KanbanState): void
  subscribe(listener: (value: KanbanState) => void): () => void
}

interface CreateTaskInput {
  client_key: string
  prompt: string
  session_id?: string
  title: string
}

interface CreatedTask {
  id: string
  status: string
  title: string
}

interface CronRun {
  error: string
  output: string
  startedAt: number
  status: string
}

interface CronRuns {
  jobId: string
  lastError: string
  lastStatus: string
  nextRunAt: number | null
  runs: CronRun[]
}

interface CronTriggerResult {
  ok: boolean
  status: string
}

interface TownFeedBridge {
  $feed: typeof $feed
  $kanban: typeof $kanban
  acquire(): void
  release(): void
  completeTask(taskId: string): Promise<void>
  createTask(input: CreateTaskInput): Promise<CreatedTask>
  curate(): Promise<void>
  dismiss(item: FeedItem): Promise<void>
  undismiss(item: FeedItem): Promise<void>
  getCronPrompt(jobId: string): Promise<string>
  getCronRuns(jobId: string): Promise<CronRuns>
  promote(item: FeedItem): Promise<void>
  triggerCron(jobId: string): Promise<CronTriggerResult>
}

const $feed = atom({
  curatedAt: null,
  curating: false,
  dayBrief: null,
  error: null,
  items: [],
  loading: true,
  nextEvent: null,
  schedule: [],
  suggestions: [],
  suggestionsGeneratedAt: null,
  unavailable: false
}) as FeedAtom
const $kanban = atom({ columns: [], error: null, loading: true, unavailable: false }) as KanbanAtom
const $recentlyDismissed = atom([]) as RecentlyDismissedAtom
const $visibleDismissedStrips = atom([]) as StringArrayAtom
const $now = atom(Date.now())

let sourceItems: FeedItem[] = []
let dismissedIds = new Set<string>()
let promotedIds = new Set<string>()
let subscriberCount = 0
let stopPolling: (() => void) | null = null
let nowSubscriberCount = 0
let nowInterval: ReturnType<typeof setInterval> | null = null
const actionIds = new Set<string>()
const inFlightDismissalIds = new Set<string>()
const inFlightUndismissalIds = new Set<string>()
const dismissedStripTimers = new Map<string, ReturnType<typeof setTimeout>[]>()
let curateOperation: Promise<void> | null = null

function acquireNow(): void {
  nowSubscriberCount += 1
  if (nowSubscriberCount === 1) {
    $now.set(Date.now())
    nowInterval = setInterval(() => $now.set(Date.now()), NOW_TICK_INTERVAL_MS)
  }
}

function releaseNow(): void {
  nowSubscriberCount = Math.max(0, nowSubscriberCount - 1)
  if (nowSubscriberCount === 0 && nowInterval !== null) {
    clearInterval(nowInterval)
    nowInterval = null
  }
}

function useNow(): number {
  const now = useValue($now) as number

  useEffect(() => {
    acquireNow()
    return releaseNow
  }, [])

  return now
}

function context(): PluginContext {
  if (!pluginContext) {
    throw new Error('Town Feed context is unavailable before plugin registration')
  }

  return pluginContext
}

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

function timestampValue(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 1_000_000_000_000 ? value * 1_000 : value
  }

  if (typeof value === 'string' && value.trim()) {
    const parsed = Date.parse(value)
    return Number.isNaN(parsed) ? null : parsed
  }

  return null
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown gateway error')
}

function payloadError(value: unknown): string | null {
  const record = asRecord(value)
  if (!record) {
    return 'The gateway returned an invalid response.'
  }

  if (record.ok === false || record.success === false) {
    return stringValue(record.error) || stringValue(record.detail) || 'The gateway rejected the request.'
  }

  return null
}

function isUnavailableError(error: unknown): boolean {
  return /\b404\b|not found|no such plugin|plugin.+(?:disabled|not installed|unavailable)/i.test(errorMessage(error))
}

function suggestionKind(value: unknown): SuggestionKind | null {
  return value === 'followup' || value === 'digest' || value === 'maintenance' ? value : null
}

function normalizeSchedule(value: unknown): ScheduleEvent[] {
  if (!Array.isArray(value)) {
    return []
  }

  const schedule: ScheduleEvent[] = []
  for (const row of value) {
    const event = asRecord(row)
    const summary = stringValue(event?.summary)
    const startAt = timestampValue(event?.start_iso)
    const endAt = timestampValue(event?.end_iso)
    const allDay = event?.all_day
    const account = stringValue(event?.account)
    if (
      !event
      || !summary
      || startAt === null
      || endAt === null
      || endAt < startAt
      || typeof allDay !== 'boolean'
      || !account
    ) {
      return []
    }

    if (schedule.length < 20) {
      const attendeeRows = Array.isArray(event.attendees) ? event.attendees : []
      const attendees = attendeeRows.flatMap(attendeeRow => {
        const attendee = asRecord(attendeeRow)
        if (!attendee || attendee.self === true) {
          return []
        }
        const display = stringValue(attendee.name).trim() || stringValue(attendee.email).trim()
        return display ? [display] : []
      }).slice(0, 8)
      const rawCount = event.attendee_count
      schedule.push({
        account,
        allDay,
        endAt,
        startAt,
        summary,
        location: stringValue(event.location).trim() || null,
        conference: stringValue(event.conference).trim() || null,
        attendees,
        attendeeCount: typeof rawCount === 'number' && Number.isFinite(rawCount) ? rawCount : null,
        multiDay: event.multi_day === true
      })
    }
  }

  return schedule
}

function normalizeDayBrief(value: unknown): DayBrief | null {
  const brief = asRecord(value)
  const daySummary = stringValue(brief?.day_summary ?? brief?.daySummary).trim()
  if (!brief || !daySummary) {
    return null
  }

  const calloutRows = Array.isArray(brief.callouts) ? brief.callouts : []
  const callouts = calloutRows.slice(0, 4).flatMap(row => {
    const callout = asRecord(row)
    const kind = stringValue(callout?.kind).trim()
    const text = stringValue(callout?.text).trim()
    return callout && kind && text ? [{ kind, text }] : []
  })
  const tripRecord = asRecord(brief.trip)
  const tripLabel = stringValue(tripRecord?.label).trim()
  const tripDayN = tripRecord?.day_n ?? tripRecord?.dayN
  const tripTotal = tripRecord?.total_days ?? tripRecord?.totalDays
  const trip = tripRecord && tripLabel
    ? {
        label: tripLabel,
        dayN: typeof tripDayN === 'number' && Number.isFinite(tripDayN) ? tripDayN : null,
        totalDays: typeof tripTotal === 'number' && Number.isFinite(tripTotal) ? tripTotal : null
      }
    : null

  return {
    daySummary,
    callouts,
    trip,
    generatedAt: timestampValue(brief.generated_at ?? brief.generatedAt)
  }
}

function normalizeFeed(value: unknown): {
  curatedAt: number | null
  dayBrief: DayBrief | null
  items: FeedItem[]
  nextEvent: NextEvent | null
  schedule: ScheduleEvent[]
  suggestions: Suggestion[]
  suggestionsGeneratedAt: number | null
} {
  const response = asRecord(value)
  const rows = response?.items
  if (!Array.isArray(rows)) {
    throw new Error('The gateway returned an invalid feed response.')
  }

  const items = rows.flatMap(row => {
    const record = asRecord(row)
    if (!record) {
      return []
    }

    const id = stringValue(record.id)
    const headline = stringValue(record.headline)
    if (!id || !headline) {
      return []
    }

    return [
      {
        id,
        headline,
        summary: stringValue(record.summary),
        source: stringValue(record.source) || 'Hermes',
        score: typeof record.score === 'number' && Number.isFinite(record.score) ? record.score : 0,
        createdAt: timestampValue(record.created_at),
        actionHint: stringValue(record.action_hint).trim() || null
      }
    ]
  })

  const suggestionRows = Array.isArray(response?.suggestions) ? response.suggestions : []
  const suggestions = suggestionRows.slice(0, 5).flatMap(row => {
    const record = asRecord(row)
    const id = stringValue(record?.id)
    const kind = suggestionKind(record?.kind)
    const label = stringValue(record?.label).slice(0, 60)
    const prompt = stringValue(record?.prompt)
    if (!record || !id || !kind || !label || !prompt) {
      return []
    }

    return [{ id, kind, label, prompt }]
  })

  const nextEventRecord = asRecord(response?.next_event)
  const nextEventSummary = stringValue(nextEventRecord?.summary)
  const nextEventStart = timestampValue(nextEventRecord?.start_iso)
  const nextEventAccount = stringValue(nextEventRecord?.account)
  const nextEvent = nextEventRecord && nextEventSummary && nextEventStart !== null && nextEventAccount
    ? {
        account: nextEventAccount,
        startAt: nextEventStart,
        summary: nextEventSummary,
        location: stringValue(nextEventRecord.location).trim() || null
      }
    : null

  return {
    curatedAt: timestampValue(response?.curated_at),
    dayBrief: normalizeDayBrief(response?.day_brief),
    items,
    nextEvent,
    schedule: normalizeSchedule(response?.schedule),
    suggestions,
    suggestionsGeneratedAt: timestampValue(response?.suggestions_generated_at)
  }
}

function normalizeKanban(value: unknown): KanbanColumn[] {
  const rows = asRecord(value)?.columns
  if (!Array.isArray(rows)) {
    throw new Error('The gateway returned an invalid kanban response.')
  }

  return rows.flatMap(row => {
    const column = asRecord(row)
    const name = stringValue(column?.name)
    if (!column || !name || !Array.isArray(column.tasks)) {
      return []
    }

    const tasks = column.tasks.slice(0, 50).flatMap(task => {
      const record = asRecord(task)
      const rawId = record?.id
      const id = typeof rawId === 'number' && Number.isFinite(rawId) ? String(rawId) : stringValue(rawId)
      const title = stringValue(record?.title)
      if (!record || !id || !title) {
        return []
      }

      return [{
        id,
        title,
        status: stringValue(record.status),
        priority: typeof record.priority === 'number' && Number.isFinite(record.priority) ? record.priority : null,
        createdAt: timestampValue(record.created_at),
        completedAt: timestampValue(record.completed_at),
        sessionId: stringValue(record.session_id) || null,
        updatedAt: timestampValue(record.updated_at)
      }]
    })

    return [{ name, tasks }]
  })
}

function storedIds(key: string): Set<string> {
  const stored: unknown = context().storage.get(key, [])
  const ids = Array.isArray(stored) ? stored.filter((value): value is string => typeof value === 'string' && value.length > 0) : []

  return new Set(ids)
}

function writeIds(key: string, ids: Set<string>): void {
  context().storage.set(key, [...ids])
}

function visibleItems(): FeedItem[] {
  return sourceItems.filter(item => !dismissedIds.has(item.id) && !promotedIds.has(item.id))
}

function publishItems(): void {
  $feed.set({ ...$feed.get(), items: visibleItems() })
}

function removeRecentlyDismissed(itemId: string): void {
  for (const timer of dismissedStripTimers.get(itemId) ?? []) {
    clearTimeout(timer)
  }
  dismissedStripTimers.delete(itemId)
  $visibleDismissedStrips.set($visibleDismissedStrips.get().filter(id => id !== itemId))
  $recentlyDismissed.set($recentlyDismissed.get().filter(entry => entry.id !== itemId))
}

function clearRecentlyDismissed(): void {
  for (const timers of dismissedStripTimers.values()) {
    for (const timer of timers) {
      clearTimeout(timer)
    }
  }
  dismissedStripTimers.clear()
  $visibleDismissedStrips.set([])
  $recentlyDismissed.set([])
}

function rememberDismissed(item: FeedItem): void {
  removeRecentlyDismissed(item.id)
  const current = $recentlyDismissed.get()
  for (const dropped of current.slice(0, Math.max(0, current.length - 2))) {
    removeRecentlyDismissed(dropped.id)
  }

  const expiresAt = Date.now() + UNDO_DISMISS_MS
  $recentlyDismissed.set([
    ...$recentlyDismissed.get(),
    { id: item.id, headline: item.headline, expiresAt, item }
  ])
  const appearTimer = setTimeout(() => {
    const visible = $visibleDismissedStrips.get()
    if ($recentlyDismissed.get().some(entry => entry.id === item.id) && !visible.includes(item.id)) {
      $visibleDismissedStrips.set([...visible, item.id])
    }
  }, 16)
  const fadeTimer = setTimeout(() => {
    $visibleDismissedStrips.set($visibleDismissedStrips.get().filter(id => id !== item.id))
  }, UNDO_DISMISS_MS - STRIP_TRANSITION_MS)
  const expireTimer = setTimeout(() => removeRecentlyDismissed(item.id), UNDO_DISMISS_MS)
  dismissedStripTimers.set(item.id, [appearTimer, fadeTimer, expireTimer])
}

async function refreshFeed(): Promise<void> {
  try {
    const payload: unknown = await context().rest('/feed')
    const normalized = normalizeFeed(payload)
    const fetchedIds = new Set(normalized.items.map(item => item.id))
    const reconciledDismissedIds = new Set([...dismissedIds].filter(id => fetchedIds.has(id)))
    for (const id of inFlightDismissalIds) {
      reconciledDismissedIds.add(id)
    }
    const dismissedIdsChanged = reconciledDismissedIds.size !== dismissedIds.size
      || [...reconciledDismissedIds].some(id => !dismissedIds.has(id))
    if (dismissedIdsChanged) {
      dismissedIds = reconciledDismissedIds
      writeIds(DISMISSED_IDS_KEY, dismissedIds)
    }

    if (subscriberCount > 0) {
      sourceItems = normalized.items
      $feed.set({
        ...$feed.get(),
        curatedAt: normalized.curatedAt,
        dayBrief: normalized.dayBrief,
        error: null,
        items: visibleItems(),
        loading: false,
        nextEvent: normalized.nextEvent,
        schedule: normalized.schedule,
        suggestions: normalized.suggestions,
        suggestionsGeneratedAt: normalized.suggestionsGeneratedAt,
        unavailable: false
      })
    }
  } catch (error) {
    if (subscriberCount > 0) {
      const unavailable = isUnavailableError(error)
      $feed.set({
        ...$feed.get(),
        error: unavailable ? null : errorMessage(error),
        loading: false,
        unavailable
      })
    }
  }
}

async function refreshKanban(): Promise<void> {
  try {
    const payload: unknown = await context().rest('/kanban')
    if (subscriberCount > 0) {
      $kanban.set({ columns: normalizeKanban(payload), error: null, loading: false, unavailable: false })
    }
  } catch (error) {
    if (subscriberCount > 0) {
      const unavailable = isUnavailableError(error)
      $kanban.set({
        ...$kanban.get(),
        error: unavailable ? null : errorMessage(error),
        loading: false,
        unavailable
      })
    }
  }
}

async function refreshTownData(): Promise<void> {
  await Promise.all([refreshFeed(), refreshKanban()])
}

function startFeedPolling(): void {
  let refreshTimer: ReturnType<typeof setTimeout> | null = null

  const scheduleEventRefresh = () => {
    if (host.state.gateway.get() !== 'open' || refreshTimer !== null) {
      return
    }

    refreshTimer = setTimeout(() => {
      refreshTimer = null
      void refreshTownData()
    }, EVENT_REFRESH_DEBOUNCE_MS)
  }

  $feed.set({ ...$feed.get(), loading: sourceItems.length === 0 })
  $kanban.set({ ...$kanban.get(), loading: $kanban.get().columns.length === 0 })
  void refreshTownData()
  const interval = setInterval(() => void refreshTownData(), FEED_POLL_INTERVAL_MS)
  const offEvent = host.onEvent('*', scheduleEventRefresh)

  stopPolling = () => {
    clearInterval(interval)
    offEvent()
    if (refreshTimer !== null) {
      clearTimeout(refreshTimer)
    }
    stopPolling = null
  }
}

function acquire(): void {
  subscriberCount += 1
  if (subscriberCount === 1) {
    startFeedPolling()
  }
}

function release(): void {
  subscriberCount = Math.max(0, subscriberCount - 1)
  if (subscriberCount === 0) {
    stopPolling?.()
  }
}

function taskReference(value: unknown): string | null {
  const task = asRecord(asRecord(value)?.task)
  if (!task) {
    return null
  }

  const id = stringValue(task.id)
  const title = stringValue(task.title)
  if (!id && !title) {
    return null
  }

  return title && id ? `${title} (${id})` : title || id
}

function mutationConfirmed(value: unknown, id: string): boolean {
  const record = asRecord(value)

  return record?.ok === true && stringValue(record.id) === id
}

function createdTask(value: unknown): CreatedTask | null {
  const task = asRecord(asRecord(value)?.task)
  const rawId = task?.id
  const id = typeof rawId === 'number' && Number.isFinite(rawId) ? String(rawId) : stringValue(rawId)
  const status = stringValue(task?.status)
  const title = stringValue(task?.title)
  if (!task || !id || !status || !title) {
    return null
  }

  return { id, status, title }
}

async function createTask(input: CreateTaskInput): Promise<CreatedTask> {
  const result: unknown = await context().rest('/task', { method: 'POST', body: input })
  const failure = payloadError(result)
  const task = createdTask(result)
  if (failure || asRecord(result)?.ok !== true || !task) {
    throw new Error(failure || 'The gateway did not return a task reference.')
  }

  await refreshKanban()
  return task
}

async function completeTask(taskId: string): Promise<void> {
  const id = taskId.trim()
  if (!id) {
    throw new Error('A task ID is required.')
  }

  try {
    const result: unknown = await context().rest('/kanban/complete', { method: 'POST', body: { id } })
    const failure = payloadError(result)
    const response = asRecord(result)
    if (failure || response?.ok !== true || stringValue(response.id) !== id || stringValue(response.status) !== 'done') {
      throw new Error(failure || 'The gateway did not confirm task completion.')
    }
  } finally {
    await refreshKanban()
  }
}

async function getCronPrompt(jobId: string): Promise<string> {
  const id = jobId.trim()
  if (!id) {
    throw new Error('A cron job ID is required.')
  }

  const result: unknown = await context().rest(`/cron-prompt/${encodeURIComponent(id)}`)
  const failure = payloadError(result)
  const response = asRecord(result)
  const prompt = response?.prompt
  if (
    failure ||
    response?.ok !== true ||
    stringValue(response.job_id) !== id ||
    typeof prompt !== 'string' ||
    !prompt.trim()
  ) {
    throw new Error(failure || 'The gateway did not return a cron prompt.')
  }

  return prompt
}

async function getCronRuns(jobId: string): Promise<CronRuns> {
  const id = jobId.trim()
  if (!id) {
    throw new Error('A cron job ID is required.')
  }

  const result: unknown = await context().rest(`/cron-runs/${encodeURIComponent(id)}`)
  const failure = payloadError(result)
  const response = asRecord(result)
  if (failure || response?.ok !== true || stringValue(response.job_id) !== id || !Array.isArray(response.runs)) {
    throw new Error(failure || 'The gateway did not return cron run history.')
  }

  const runs = response.runs.slice(0, 5).flatMap(value => {
    const run = asRecord(value)
    const startedAt = timestampValue(run?.started_at)
    if (!run || startedAt === null) {
      return []
    }
    return [{
      error: stringValue(run.error),
      output: stringValue(run.output).slice(0, 2000),
      startedAt,
      status: stringValue(run.status) || 'unknown'
    }]
  })

  return {
    jobId: id,
    lastError: stringValue(response.last_error),
    lastStatus: stringValue(response.last_status),
    nextRunAt: timestampValue(response.next_run_at),
    runs
  }
}

async function triggerCron(jobId: string): Promise<CronTriggerResult> {
  const id = jobId.trim()
  if (!id) {
    throw new Error('A cron job ID is required.')
  }

  const result: unknown = await context().rest(`/cron-trigger/${encodeURIComponent(id)}`, { method: 'POST' })
  const failure = payloadError(result)
  const response = asRecord(result)
  const status = stringValue(response?.status)
  if (failure || !response || !status) {
    throw new Error(failure || 'The gateway did not return a cron trigger outcome.')
  }

  return { ok: response.ok === true, status }
}

function curate(): Promise<void> {
  if (curateOperation) {
    return curateOperation
  }

  $feed.set({ ...$feed.get(), curating: true })
  curateOperation = (async () => {
    try {
      const result: unknown = await context().rest('/curate', { method: 'POST' })
      const failure = payloadError(result)
      if (failure || asRecord(result)?.ok !== true) {
        throw new Error(failure || 'The gateway did not confirm the refresh.')
      }

      await refreshFeed()
      host.notify({ kind: 'success', message: 'Refreshed (heuristic pass)' })
    } catch (error) {
      host.notifyError(error, 'Could not refresh Need to Know')
    } finally {
      $feed.set({ ...$feed.get(), curating: false })
      curateOperation = null
    }
  })()

  return curateOperation
}

async function dismiss(target: FeedItem): Promise<void> {
  if (actionIds.has(target.id)) {
    return
  }

  actionIds.add(target.id)
  dismissedIds.add(target.id)
  publishItems()
  writeIds(DISMISSED_IDS_KEY, dismissedIds)
  inFlightDismissalIds.add(target.id)

  try {
    const result: unknown = await context().rest('/dismiss', { method: 'POST', body: { id: target.id } })
    const failure = payloadError(result)
    if (failure || !mutationConfirmed(result, target.id)) {
      throw new Error(failure || 'The gateway did not confirm the dismissal.')
    }
  } catch (error) {
    dismissedIds.delete(target.id)
    publishItems()
    writeIds(DISMISSED_IDS_KEY, dismissedIds)
    host.notifyError(error, 'Could not dismiss Need-to-Know item')
  } finally {
    inFlightDismissalIds.delete(target.id)
    actionIds.delete(target.id)
  }
}

async function undismiss(target: FeedItem): Promise<void> {
  dismissedIds.delete(target.id)
  publishItems()
  writeIds(DISMISSED_IDS_KEY, dismissedIds)
  inFlightUndismissalIds.add(target.id)

  try {
    const result: unknown = await context().rest('/undismiss', { method: 'POST', body: { id: target.id } })
    const failure = payloadError(result)
    if (failure || !mutationConfirmed(result, target.id)) {
      throw new Error(failure || 'The gateway did not confirm the restoration.')
    }

    await refreshFeed()
  } catch (error) {
    dismissedIds.add(target.id)
    publishItems()
    writeIds(DISMISSED_IDS_KEY, dismissedIds)
    host.notifyError(error, 'Could not restore Need-to-Know item')
  } finally {
    inFlightUndismissalIds.delete(target.id)
  }
}

async function promote(target: FeedItem): Promise<void> {
  if (actionIds.has(target.id)) {
    return
  }

  actionIds.add(target.id)
  promotedIds.add(target.id)
  publishItems()
  writeIds(PROMOTED_IDS_KEY, promotedIds)

  try {
    const result: unknown = await context().rest('/promote-to-task', { method: 'POST', body: { id: target.id } })
    const failure = payloadError(result)
    const reference = taskReference(result)
    if (failure || !mutationConfirmed(result, target.id) || !reference) {
      throw new Error(failure || 'The gateway did not return a task reference.')
    }

    host.notify({ kind: 'success', message: `Task created: ${reference}` })
  } catch (error) {
    promotedIds.delete(target.id)
    publishItems()
    writeIds(PROMOTED_IDS_KEY, promotedIds)
    host.notifyError(error, 'Could not turn Need-to-Know item into a task')
  } finally {
    actionIds.delete(target.id)
  }
}

function FeedSkeleton() {
  return (
    <div className="space-y-3 px-3 py-4" aria-label="Loading Need to Know">
      <Skeleton className="h-4 w-5/6" />
      <Skeleton className="h-3 w-1/2" />
      <Skeleton className="h-14 w-full" />
    </div>
  )
}

function sessionIdFromSource(source: string): string | null {
  const match = source.trim().match(/^session:(.+)$/i)
  return match?.[1]?.trim() || null
}

function gmailSourceFromSource(source: string): { account: string; messageId: string } | null {
  const match = source.trim().match(/^gmail:([^:]+):(.+)$/i)
  const account = match?.[1]?.trim() || ''
  const messageId = match?.[2]?.trim() || ''
  return account && messageId ? { account, messageId } : null
}

interface FeedSourcePillDetails {
  icon: string
  label: string
  opensSource: boolean
}

function sourceSuffix(source: string, kind: string): string {
  const match = source.trim().match(new RegExp(`^${kind}:(.+)$`, 'i'))
  return match?.[1]?.trim() || ''
}

function telegramChatLabel(item: FeedItem, chatId: string): string {
  const headlineName = item.headline.match(/^([^:]+):\s/)?.[1]?.trim()
  const summaryName = item.summary.match(/^([^:\n]+):\s/)?.[1]?.trim()
  return headlineName || summaryName || chatId
}

function cronJobLabel(item: FeedItem, jobId: string): string {
  const cronName = item.headline.match(/^Cron:\s*(.+)$/i)?.[1]?.trim()
  const failingName = item.headline.match(/^(.+?)\s+failing\s+[\u2014-]/i)?.[1]?.trim()
  return cronName || failingName || jobId
}

function genericSourceLabel(source: string): string {
  const family = source.trim().split(':', 1)[0] || 'Hermes'
  return family.charAt(0).toUpperCase() + family.slice(1)
}

function feedSourcePillDetails(item: FeedItem): FeedSourcePillDetails {
  if (sessionIdFromSource(item.source)) {
    return { icon: 'comment-discussion', label: 'Session', opensSource: true }
  }

  const gmail = gmailSourceFromSource(item.source)
  if (gmail) {
    return { icon: 'mail', label: gmail.account.split('@', 1)[0] || gmail.account, opensSource: true }
  }

  const telegramChatId = sourceSuffix(item.source, 'telegram')
  if (telegramChatId) {
    return { icon: 'send', label: telegramChatLabel(item, telegramChatId), opensSource: false }
  }

  const cronJobId = sourceSuffix(item.source, 'cron')
  if (cronJobId) {
    return { icon: 'clock', label: cronJobLabel(item, cronJobId), opensSource: false }
  }

  if (sourceSuffix(item.source, 'calendar')) {
    return { icon: 'calendar', label: 'Calendar', opensSource: false }
  }

  return { icon: 'symbol-misc', label: genericSourceLabel(item.source), opensSource: false }
}

function currentFeedItem(itemId: string): FeedItem | null {
  return $feed.get().items.find(item => item.id === itemId) ?? null
}

function openFeedSource(itemId: string): void {
  const source = currentFeedItem(itemId)?.source || ''
  const sessionId = sessionIdFromSource(source)
  if (sessionId) {
    host.navigate(`/${encodeURIComponent(sessionId)}`)
    return
  }
  const gmail = gmailSourceFromSource(source)
  if (gmail) {
    window.open(`https://mail.google.com/mail/#inbox/${encodeURIComponent(gmail.messageId)}`, '_blank', 'noopener,noreferrer')
  }
}

function runFeedAction(itemId: string, action: 'dismiss' | 'promote'): void {
  const item = currentFeedItem(itemId)
  if (item) {
    if (action === 'dismiss') {
      rememberDismissed(item)
    }
    void (action === 'dismiss' ? dismiss(item) : promote(item))
  }
}

function undoFeedDismissal(itemId: string): void {
  const entry = $recentlyDismissed.get().find(candidate => candidate.id === itemId)
  if (!entry) {
    return
  }

  removeRecentlyDismissed(itemId)
  void undismiss(entry.item)
}

function truncatedHeadline(headline: string): string {
  return headline.length > 48 ? `${headline.slice(0, 47).trimEnd()}…` : headline
}

function FeedSourcePill({ item }: { item: FeedItem }) {
  const source = feedSourcePillDetails(item)
  const className = 'inline-flex items-center gap-1 rounded-full border border-(--ui-stroke-secondary) px-2 py-0.5 text-xs text-(--ui-text-tertiary) transition-colors duration-150 hover:border-(--ui-stroke-primary) hover:bg-(--ui-control-hover-background) hover:text-(--ui-text-secondary)'
  const content = (
    <>
      <Codicon name={source.icon} size="0.6875rem" />
      <span>{source.label}</span>
    </>
  )

  return source.opensSource ? (
    <button
      aria-label={`${source.icon === 'mail' ? 'Open in Gmail' : 'Open session'} for ${item.headline}`}
      className={`${className} cursor-pointer outline-none focus-visible:border-(--ui-stroke-primary) focus-visible:bg-(--ui-control-hover-background)`}
      onClick={() => openFeedSource(item.id)}
      type="button"
    >
      {content}
    </button>
  ) : (
    <span className={className}>{content}</span>
  )
}

function FeedHeader({ feed }: { feed: FeedState }) {
  const now = useNow()

  return (
    <div className="group flex shrink-0 items-center justify-between gap-3 border-b border-(--ui-stroke-secondary) px-3 py-2">
      <h2 className="text-xs font-medium uppercase tracking-wide text-(--ui-text-tertiary)">
        Need to Know
        {feed.curatedAt === null ? null : (
          <span className="font-normal normal-case"> · {relativeTime(feed.curatedAt, now)}</span>
        )}
      </h2>
      <Tip label={feed.curating ? 'Refreshing Need to Know' : 'Refresh Need to Know'}>
        <Button
          aria-label="Refresh Need to Know"
          disabled={feed.curating || feed.unavailable}
          className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
          size="icon-xs"
          type="button"
          variant="ghost"
          onClick={() => void curate()}
        >
          <Codicon name="refresh" size="0.75rem" />
        </Button>
      </Tip>
    </div>
  )
}

function NeedToKnowPane() {
  const feed = useValue($feed)
  const recentlyDismissed = useValue($recentlyDismissed)
  const visibleDismissedStrips = useValue($visibleDismissedStrips)
  const now = useNow()

  useEffect(() => {
    acquire()
    return () => {
      clearRecentlyDismissed()
      release()
    }
  }, [])

  const dismissedStrips = recentlyDismissed.map(entry => (
    <div
      className={`-mx-2 flex items-center gap-3 border-t border-(--ui-stroke-secondary) px-2 py-2 text-xs text-(--ui-text-tertiary) transition-opacity duration-150 first:border-t-0 ${visibleDismissedStrips.includes(entry.id) ? 'opacity-100' : 'opacity-0'}`}
      key={entry.id}
    >
      <span className="min-w-0 flex-1 truncate">Dismissed &quot;{truncatedHeadline(entry.headline)}&quot;</span>
      <span aria-hidden="true" className="shrink-0">·</span>
      <Button size="inline" type="button" variant="text" onClick={() => undoFeedDismissal(entry.id)}>
        Undo
      </Button>
    </div>
  ))

  return (
    <div className="flex h-full min-h-0 flex-col text-sm">
      <FeedHeader feed={feed} />
      {feed.loading ? (
        <FeedSkeleton />
      ) : feed.unavailable ? (
        <EmptyState
          className="min-h-48 flex-1 px-4"
          title="Need-to-Know engine not installed"
          description="Install the gateway plugin using gateway/town-feed/README.md."
        />
      ) : feed.error ? (
        <ErrorState className="min-h-48 flex-1 px-4" title="Could not load Need to Know" description={feed.error} />
      ) : feed.items.length === 0 && recentlyDismissed.length === 0 ? (
        <EmptyState className="min-h-48 flex-1 px-4" title="You're caught up" description="New updates will appear after the next curator run." />
      ) : (
        <ScrollArea className="min-h-0 flex-1">
          <div className="px-3">
            {dismissedStrips}
            {feed.items.length === 0 ? (
              <EmptyState className="min-h-48 px-4" title="You're caught up" description="New updates will appear after the next curator run." />
            ) : null}
            {feed.items.map(item => {
              const summary = item.summary || 'No additional summary is available.'
              const created = item.createdAt === null ? 'Time unavailable' : relativeTime(item.createdAt, now)

              return (
                <article className="group -mx-2 rounded-lg px-2 py-2.5 transition-colors duration-150 hover:bg-(--ui-row-hover-background)" key={item.id}>
                  <h3 className="text-sm font-medium leading-5 text-(--ui-text-primary)">{item.headline}</h3>
                  <p
                    className="mt-1 text-xs leading-4 text-(--ui-text-secondary)"
                    style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
                    title={summary}
                  >
                    {summary}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {item.actionHint ? (
                      <span className="text-xs font-medium text-(--ui-accent)">{item.actionHint}</span>
                    ) : null}
                    <FeedSourcePill item={item} />
                    <span className="text-xs text-(--ui-text-tertiary)">{created}</span>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2">
                    <Button className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100" size="inline" type="button" variant="text" onClick={() => runFeedAction(item.id, 'dismiss')}>
                      Dismiss
                    </Button>
                    <Button className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100" size="inline" type="button" variant="text" onClick={() => runFeedAction(item.id, 'promote')}>
                      Turn into task
                    </Button>
                  </div>
                </article>
              )
            })}
          </div>
        </ScrollArea>
      )}
    </div>
  )
}

export default {
  id: 'town-feed',
  name: 'Town Need to Know',
  register(ctx: PluginContext) {
    pluginContext = ctx
    dismissedIds = storedIds(DISMISSED_IDS_KEY)
    promotedIds = storedIds(PROMOTED_IDS_KEY)
    publishItems()
    const bridge: TownFeedBridge = {
      $feed,
      $kanban,
      acquire,
      release,
      completeTask,
      createTask,
      curate,
      dismiss,
      undismiss,
      getCronPrompt,
      getCronRuns,
      promote,
      triggerCron
    }
    ;(window as Window & { __hermesTownFeed?: TownFeedBridge }).__hermesTownFeed = bridge
    window.dispatchEvent(new CustomEvent('hermes-town-feed-ready'))
    ctx.registerMany([
      {
        id: 'pane',
        area: 'panes',
        title: 'need to know',
        data: { placement: 'right', width: '300px' },
        render: () => <NeedToKnowPane />
      },
      {
        id: 'page',
        area: ROUTES_AREA,
        title: 'Need to Know',
        data: { path: '/town-feed' },
        render: () => <NeedToKnowPane />
      }
    ])
  }
}
