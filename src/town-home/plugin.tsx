import {
  atom,
  Badge,
  Button,
  Checkbox,
  Codicon,
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  EmptyState,
  ErrorState,
  fmtDayTime,
  host,
  Input,
  KEYBINDS_AREA,
  PALETTE_AREA,
  profileColor,
  relativeTime,
  ROUTES_AREA,
  ScrollArea,
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  SegmentedControl,
  SIDEBAR_NAV_AREA,
  Skeleton,
  StatusDot,
  Switch,
  Textarea,
  Tip,
  useValue,
  type KeybindContribution,
  type PaletteContribution,
  type PluginContext
} from '@hermes/plugin-sdk'
import type * as React from 'react'
import { useEffect, useRef, useState, type ReactNode } from 'react'

const MIN_POLL_INTERVAL_MS = 15_000
const EVENT_REFRESH_DEBOUNCE_MS = 2_000
const SESSION_LIST_PARAMS = { limit: 12 }
const ACTIVE_SESSION_PARAMS = {}
const CRON_LIST_PARAMS = { action: 'list' }
const TOWN_HOME_PREFERENCES_KEY = 'preferences'
const MORNING_BRIEF_COMMAND = '/blueprint morning-brief time=08:00 deliver=origin'
const TODAY_BRIEF_PROMPT = 'Brief me on the rest of today: calendar, waiting items, and anything new I should know.'
const NEW_ROUTINE_PROMPT = 'Create a routine that '
const RECENTLY_DISMISSED_LIMIT = 3
const RECENTLY_DISMISSED_TTL_MS = 6_000
const OPACITY_TRANSITION_MS = 150
const NOW_TICK_INTERVAL_MS = 30_000

const CARD_IDS = ['tasks', 'recents', 'need-to-know', 'suggestions', 'routines', 'briefing'] as const
const MAIN_CARD_IDS = new Set<CardId>(['tasks', 'recents', 'briefing'])
const SIDE_CARD_IDS = new Set<CardId>(['need-to-know', 'suggestions', 'routines'])
const DONE_TASK_WINDOW_HOURS = [12, 24, 48, 168] as const
const TOWN_SCANNER_JOB_NAMES = new Set([
  'town-gmail-scan',
  'town-telegram-scan',
  'town-calendar-scan',
  'town-feed-curate'
])

type CardId = (typeof CARD_IDS)[number]
type DoneTaskWindowHours = (typeof DONE_TASK_WINDOW_HOURS)[number]

interface TownHomePreferences {
  cardOrder: CardId[]
  cardVisibility: Record<CardId, boolean>
  doneTaskWindowHours: DoneTaskWindowHours
  userName: string
}

interface PreferencesStore {
  get(): TownHomePreferences
  set(value: TownHomePreferences): void
  subscribe(listener: (value: TownHomePreferences) => void): () => void
}

const DEFAULT_TOWN_HOME_PREFERENCES: TownHomePreferences = {
  cardOrder: [...CARD_IDS],
  cardVisibility: {
    tasks: true,
    recents: true,
    'need-to-know': true,
    suggestions: true,
    routines: true,
    briefing: true
  },
  doneTaskWindowHours: 48,
  userName: ''
}

const $townHomePreferences = atom(DEFAULT_TOWN_HOME_PREFERENCES) as PreferencesStore
const $customizeDialogOpen = atom(false)
const $newRoutineDialogOpen = atom(false)
const $runHistoryDialogOpen = atom(false)
const $scheduleDialogOpen = atom(false)
const $now = atom(Date.now())
let nowSubscriberCount = 0
let nowInterval: ReturnType<typeof setInterval> | null = null
let pluginContext: PluginContext | null = null

type UnknownRecord = Record<string, unknown>

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

const TWO_COLUMN_MIN_WIDTH = 980

function useBoardWide(): [boolean, (node: HTMLDivElement | null) => void] {
  const [wide, setWide] = useState(false)
  const observerRef = useRef<ResizeObserver | null>(null)
  const attach = (node: HTMLDivElement | null) => {
    observerRef.current?.disconnect()
    observerRef.current = null
    if (!node) {
      return
    }
    const observer = new ResizeObserver(entries => {
      const width = entries[0]?.contentRect.width ?? 0
      setWide(width >= TWO_COLUMN_MIN_WIDTH)
    })
    observer.observe(node)
    observerRef.current = observer
  }
  return [wide, attach]
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
    throw new Error('Town Home context is unavailable before plugin registration')
  }

  return pluginContext
}

function defaultTownHomePreferences(): TownHomePreferences {
  return {
    cardOrder: [...DEFAULT_TOWN_HOME_PREFERENCES.cardOrder],
    cardVisibility: { ...DEFAULT_TOWN_HOME_PREFERENCES.cardVisibility },
    doneTaskWindowHours: DEFAULT_TOWN_HOME_PREFERENCES.doneTaskWindowHours,
    userName: DEFAULT_TOWN_HOME_PREFERENCES.userName
  }
}

function isCardId(value: unknown): value is CardId {
  return typeof value === 'string' && (CARD_IDS as readonly string[]).includes(value)
}

function isDoneTaskWindowHours(value: unknown): value is DoneTaskWindowHours {
  return typeof value === 'number' && (DONE_TASK_WINDOW_HOURS as readonly number[]).includes(value)
}

function normalizeUserName(value: unknown): string {
  return typeof value === 'string' ? value.trim().slice(0, 40) : ''
}

function normalizeTownHomePreferences(value: unknown): TownHomePreferences {
  const defaults = defaultTownHomePreferences()
  const record = asRecord(value)
  if (!record) {
    return defaults
  }

  const storedOrder = Array.isArray(record.cardOrder) ? record.cardOrder : []
  const cardOrder = storedOrder.reduce<CardId[]>((order, cardId) => {
    if (isCardId(cardId) && !order.includes(cardId)) {
      order.push(cardId)
    }
    return order
  }, [])

  for (const cardId of CARD_IDS) {
    if (cardOrder.includes(cardId)) {
      continue
    }

    const defaultIndex = CARD_IDS.indexOf(cardId)
    let insertionIndex = 0
    for (let index = defaultIndex - 1; index >= 0; index -= 1) {
      const precedingIndex = cardOrder.indexOf(CARD_IDS[index])
      if (precedingIndex >= 0) {
        insertionIndex = precedingIndex + 1
        break
      }
    }
    cardOrder.splice(insertionIndex, 0, cardId)
  }

  const storedVisibility = asRecord(record.cardVisibility)
  const cardVisibility = Object.fromEntries(CARD_IDS.map(cardId => [
    cardId,
    typeof storedVisibility?.[cardId] === 'boolean' ? storedVisibility[cardId] : defaults.cardVisibility[cardId]
  ])) as Record<CardId, boolean>

  return {
    cardOrder,
    cardVisibility,
    doneTaskWindowHours: isDoneTaskWindowHours(record.doneTaskWindowHours)
      ? record.doneTaskWindowHours
      : defaults.doneTaskWindowHours,
    userName: normalizeUserName(record.userName)
  }
}

function updateTownHomePreferences(update: (current: TownHomePreferences) => TownHomePreferences): void {
  const next = update($townHomePreferences.get())
  $townHomePreferences.set(next)
  try {
    context().storage.set(TOWN_HOME_PREFERENCES_KEY, next)
  } catch (error) {
    host.notifyError(error, 'Could not save Town customization')
  }
}

interface RpcState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

interface SessionRow {
  id: string
  preview: string
  source: string
  startedAt: number | null
  title: string
}

interface ActiveSessionRow {
  id: string
  lastActive: number | null
  preview: string
  sessionKey: string
  status: string
  title: string
}

type AttentionKind = 'approval' | 'finished' | 'waiting'

interface AttentionMarker {
  kind: AttentionKind
  timestamp: number
}

interface AttentionItem {
  detail: string
  id: string
  sessionKey?: string
  title: string
}

const FINISHED_ATTENTION_DETAIL = 'Finished while you were viewing another session'

interface CronRow {
  enabled: boolean
  id: string
  lastDeliveryError: string
  lastError: string
  lastRunAt: number | null
  lastStatus: string
  name: string
  nextRunAt: number | null
  prompt: string
  promptPreview: string
  schedule: string
  state: string
}

interface RecentSessionActivity {
  id: string
  kind: 'session'
  timestamp: number | null
  title: string
}

interface RecentRoutineActivity {
  id: string
  jobName: string
  kind: 'routine'
  timestamp: number
  title: string
}

type RecentActivity = RecentSessionActivity | RecentRoutineActivity

interface CronRun {
  error: string
  output: string
  startedAt: number
  status: string
}

interface CronRunsResult {
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

interface RunHistoryTarget {
  id: string
  name: string
}

interface RunHistoryAtom {
  get(): RunHistoryTarget | null
  set(value: RunHistoryTarget | null): void
  subscribe(listener: (value: RunHistoryTarget | null) => void): () => void
}

const $runHistoryTarget = atom(null) as RunHistoryAtom

interface TownFeedItem {
  id: string
  headline: string
  summary: string
  source: string
  createdAt: number | null
  actionHint: string | null
}

interface RecentlyDismissedEntry {
  id: string
  headline: string
  expiresAt: number
  item: TownFeedItem
}

interface RecentlyDismissedStore {
  get(): RecentlyDismissedEntry[]
  set(value: RecentlyDismissedEntry[]): void
  subscribe(listener: (value: RecentlyDismissedEntry[]) => void): () => void
}

const $recentlyDismissed = atom([]) as RecentlyDismissedStore

interface TownNextEvent {
  account: string
  startAt: number
  summary: string
}

interface TownScheduleEvent {
  account: string
  allDay: boolean
  endAt: number
  startAt: number
  summary: string
  location: string | null
  conference: string | null
  attendees: string[]
  multiDay: boolean
}

interface TownDayBriefCallout {
  kind: string
  text: string
}

interface TownDayBriefTrip {
  label: string
  dayN: number | null
  totalDays: number | null
}

interface TownDayBrief {
  daySummary: string
  callouts: TownDayBriefCallout[]
  trip: TownDayBriefTrip | null
}

type TownSuggestionKind = 'followup' | 'digest' | 'maintenance'

interface TownSuggestion {
  id: string
  kind: TownSuggestionKind
  label: string
  prompt: string
}

interface TownFeedState {
  curatedAt: number | null
  curating: boolean
  dayBrief: TownDayBrief | null
  error: string | null
  items: TownFeedItem[]
  loading: boolean
  nextEvent: TownNextEvent | null
  schedule: TownScheduleEvent[]
  suggestions: TownSuggestion[]
  suggestionsGeneratedAt: number | null
  unavailable: boolean
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

interface TownFeedStore {
  get?: () => unknown
  subscribe?: (listener: (value: unknown) => void) => (() => void) | void
}

interface CreateTaskInput {
  client_key: string
  prompt: string
  session_id?: string
  title: string
}

interface TownFeedBridge {
  $feed?: TownFeedStore
  $kanban?: TownFeedStore
  acquire?: () => void
  release?: () => void
  completeTask?: (taskId: string) => Promise<void>
  createTask?: (input: CreateTaskInput) => Promise<unknown>
  curate?: () => Promise<void>
  dismiss?: (item: TownFeedItem) => Promise<void> | void
  undismiss?: (item: TownFeedItem) => Promise<void> | void
  getCronPrompt?: (jobId: string) => Promise<string>
  getCronRuns?: (jobId: string) => Promise<CronRunsResult>
  promote?: (item: TownFeedItem) => Promise<void> | void
  triggerCron?: (jobId: string) => Promise<CronTriggerResult>
}

function openTownHome(): void {
  host.navigate('/town')
}

function openTownFeed(): void {
  host.navigate('/town-feed')
}

function explainTownFeedPane(): void {
  host.notify({ kind: 'info', message: 'Open the "need to know" pane from the layout editor.' })
}

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
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

// Keep this formatter in sync with its twin in town-routines; desktop bundles cannot import each other.
function describeCronSchedule(expr: string): string {
  const fields = expr.trim().split(/\s+/)
  if (fields.length !== 5) {
    return expr
  }

  const [minute, hour, dayOfMonth, month, dayOfWeek] = fields
  const minuteStep = minute.match(/^\*\/([1-9]\d*)$/)
  if (minuteStep && hour === '*' && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    const interval = Number(minuteStep[1])
    return interval === 1 ? 'Every minute' : `Every ${interval} minutes`
  }

  const hourStep = hour.match(/^\*\/([1-9]\d*)$/)
  if (minute === '0' && hourStep && dayOfMonth === '*' && month === '*' && dayOfWeek === '*') {
    const interval = Number(hourStep[1])
    return interval === 1 ? 'Every hour' : `Every ${interval} hours`
  }

  if (!/^\d{1,2}$/.test(minute) || !/^\d{1,2}$/.test(hour) || dayOfMonth !== '*' || month !== '*') {
    return expr
  }

  const minuteNumber = Number(minute)
  const hourNumber = Number(hour)
  if (minuteNumber > 59 || hourNumber > 23) {
    return expr
  }

  const period = hourNumber >= 12 ? 'PM' : 'AM'
  const clockHour = hourNumber % 12 || 12
  const time = `${clockHour}:${String(minuteNumber).padStart(2, '0')} ${period}`
  if (dayOfWeek === '*') {
    return `Daily at ${time}`
  }
  if (dayOfWeek === '1-5' || dayOfWeek === '1,2,3,4,5') {
    return `Weekdays at ${time}`
  }

  const dayNames = ['Sundays', 'Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays']
  const dayIndexes = dayOfWeek.split(',').map(value => Number(value === '7' ? '0' : value))
  if (dayIndexes.length === 0 || dayIndexes.some(value => !Number.isInteger(value) || value < 0 || value > 6)) {
    return expr
  }

  const days = [...new Set(dayIndexes)].map(value => dayNames[value])
  const dayLabel = days.length === 1
    ? days[0]
    : days.length === 2
      ? `${days[0]} and ${days[1]}`
      : `${days.slice(0, -1).join(', ')}, and ${days.at(-1)}`
  return `${dayLabel} at ${time}`
}

function routineDescription(prompt: string): string {
  const normalized = prompt.replace(/\s+/g, ' ').trim()
  const sentence = normalized.match(/^.*?[.!?](?=\s|$)/)?.[0] || normalized
  return sentence.length <= 90 ? sentence : `${sentence.slice(0, 89).trimEnd()}…`
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown gateway error')
}

function townScanPayload(output: string): UnknownRecord | null {
  const text = output.trim()
  if (!text) {
    return null
  }

  for (const rawLine of text.split('\n')) {
    const line = rawLine.trim()
    if (!line.startsWith('{')) {
      continue
    }
    try {
      const payload = asRecord(JSON.parse(line))
      if (payload) {
        return payload
      }
    } catch {
      // Fall through to the next line, then the whole-document brace slice.
    }
  }

  const start = text.indexOf('{')
  const end = text.lastIndexOf('}')
  if (start < 0 || end <= start) {
    return null
  }
  try {
    return asRecord(JSON.parse(text.slice(start, end + 1)))
  } catch {
    return null
  }
}

function presentRunOutput(output: string): string {
  const lines = output.replace(/\r\n?/g, '\n').split('\n')
  if (lines[0]?.trim().startsWith('# Cron Job:')) {
    const blankLineIndex = lines.findIndex(line => line.trim() === '')
    lines.splice(0, blankLineIndex >= 0 ? blankLineIndex + 1 : lines.length)
  }

  const cleaned = lines
    .filter(line => !/^\s*\*\*[^*]+:\*\*\s*.*$/.test(line))
    .join('\n')
    .trim()
  const payload = townScanPayload(cleaned)
  const countLabels: string[] = []
  if (payload && 'candidates' in payload) {
    countLabels.push(`${Array.isArray(payload.candidates) ? payload.candidates.length : 0} candidates`)
  }
  if (payload && 'events' in payload) {
    countLabels.push(`${Array.isArray(payload.events) ? payload.events.length : 0} events`)
  }
  if (payload && 'open_loops' in payload) {
    countLabels.push(`${Array.isArray(payload.open_loops) ? payload.open_loops.length : 0} open loops`)
  }

  return countLabels.length > 0 ? `Scan payload · ${countLabels.join(' · ')}` : cleaned
}

function payloadError(value: unknown): string | null {
  const record = asRecord(value)
  return record?.success === false ? stringValue(record.error) || 'The gateway rejected the request.' : null
}

function windowTownFeedBridge(): TownFeedBridge | null {
  const candidate = asRecord((window as Window & { __hermesTownFeed?: unknown }).__hermesTownFeed)
  const store = asRecord(candidate?.$feed)
  if (!candidate || !store || typeof store.get !== 'function' || typeof store.subscribe !== 'function') {
    return null
  }

  return candidate as TownFeedBridge
}

function cronBridgeCapabilities(): { cronPrompt: boolean; cronRuns: boolean; triggerCron: boolean } {
  const bridge = windowTownFeedBridge()
  return {
    cronPrompt: typeof bridge?.getCronPrompt === 'function',
    cronRuns: typeof bridge?.getCronRuns === 'function',
    triggerCron: typeof bridge?.triggerCron === 'function'
  }
}

function useCronBridgeCapabilities(): ReturnType<typeof cronBridgeCapabilities> {
  const [available, setAvailable] = useState(cronBridgeCapabilities)

  useEffect(() => {
    const refresh = () => setAvailable(cronBridgeCapabilities())
    refresh()
    window.addEventListener('hermes-town-feed-ready', refresh)
    return () => window.removeEventListener('hermes-town-feed-ready', refresh)
  }, [])

  return available
}

function townSuggestionKind(value: unknown): TownSuggestionKind | null {
  return value === 'followup' || value === 'digest' || value === 'maintenance' ? value : null
}

function normalizeTownSchedule(value: unknown): TownScheduleEvent[] {
  if (!Array.isArray(value)) {
    return []
  }

  const schedule: TownScheduleEvent[] = []
  for (const row of value) {
    const event = asRecord(row)
    const summary = stringValue(event?.summary).trim()
    const startAt = timestampValue(event?.startAt ?? event?.start_iso)
    const endAt = timestampValue(event?.endAt ?? event?.end_iso)
    const allDay = event?.allDay ?? event?.all_day
    const account = stringValue(event?.account).trim()
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
      const attendees = attendeeRows.flatMap(row => {
        if (typeof row === 'string') {
          return row.trim() ? [row.trim()] : []
        }
        const attendee = asRecord(row)
        if (!attendee || attendee.self === true) {
          return []
        }
        const display = stringValue(attendee.name).trim() || stringValue(attendee.email).trim()
        return display ? [display] : []
      }).slice(0, 8)
      schedule.push({
        account,
        allDay,
        endAt,
        startAt,
        summary,
        location: stringValue(event.location).trim() || null,
        conference: stringValue(event.conference).trim() || null,
        attendees,
        multiDay: event.multiDay === true || event.multi_day === true
      })
    }
  }

  return schedule
}

function normalizeTownDayBrief(value: unknown): TownDayBrief | null {
  const brief = asRecord(value)
  const daySummary = stringValue(brief?.daySummary ?? brief?.day_summary).trim()
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
  const tripDayN = tripRecord?.dayN ?? tripRecord?.day_n
  const tripTotal = tripRecord?.totalDays ?? tripRecord?.total_days
  const trip = tripRecord && tripLabel
    ? {
        label: tripLabel,
        dayN: typeof tripDayN === 'number' && Number.isFinite(tripDayN) ? tripDayN : null,
        totalDays: typeof tripTotal === 'number' && Number.isFinite(tripTotal) ? tripTotal : null
      }
    : null

  return { daySummary, callouts, trip }
}

function normalizeTownFeedState(value: unknown): TownFeedState {
  const state = asRecord(value)
  const rows = Array.isArray(state?.items) ? state.items : []
  const items = rows.flatMap(row => {
    const item = asRecord(row)
    const id = stringValue(item?.id)
    const headline = stringValue(item?.headline)
    if (!item || !id || !headline) {
      return []
    }

    return [{
      id,
      headline,
      summary: stringValue(item.summary),
      source: stringValue(item.source) || 'Hermes',
      createdAt: timestampValue(item.createdAt),
      actionHint: stringValue(item.actionHint ?? item.action_hint).trim() || null
    }]
  })
  const suggestionRows = Array.isArray(state?.suggestions) ? state.suggestions : []
  const suggestions = suggestionRows.slice(0, 5).flatMap(row => {
    const suggestion = asRecord(row)
    const id = stringValue(suggestion?.id).trim()
    const kind = townSuggestionKind(suggestion?.kind)
    const label = stringValue(suggestion?.label).trim().slice(0, 60)
    const prompt = stringValue(suggestion?.prompt).trim()
    if (!suggestion || !id || !kind || !label || !prompt) {
      return []
    }

    return [{ id, kind, label, prompt }]
  })
  const nextEventRecord = asRecord(state?.nextEvent ?? state?.next_event)
  const nextEventSummary = stringValue(nextEventRecord?.summary).trim()
  const nextEventStart = timestampValue(nextEventRecord?.startAt ?? nextEventRecord?.start_iso)
  const nextEventAccount = stringValue(nextEventRecord?.account).trim()
  const nextEvent = nextEventRecord && nextEventSummary && nextEventStart !== null && nextEventAccount
    ? { account: nextEventAccount, startAt: nextEventStart, summary: nextEventSummary }
    : null

  return {
    curatedAt: timestampValue(state?.curatedAt ?? state?.curated_at),
    curating: state?.curating === true,
    dayBrief: normalizeTownDayBrief(state?.dayBrief ?? state?.day_brief),
    error: typeof state?.error === 'string' ? state.error : null,
    items,
    loading: state?.loading === true,
    nextEvent,
    schedule: normalizeTownSchedule(state?.schedule),
    suggestions,
    suggestionsGeneratedAt: timestampValue(state?.suggestionsGeneratedAt ?? state?.suggestions_generated_at),
    unavailable: state?.unavailable === true
  }
}

function windowTownKanbanBridge(): TownFeedBridge | null {
  const candidate = asRecord((window as Window & { __hermesTownFeed?: unknown }).__hermesTownFeed)
  const store = asRecord(candidate?.$kanban)
  if (!candidate || !store || typeof store.get !== 'function' || typeof store.subscribe !== 'function') {
    return null
  }

  return candidate as TownFeedBridge
}

function normalizeKanbanState(value: unknown): KanbanState {
  const state = asRecord(value)
  const rawColumns = state?.columns
  const hasColumns = Array.isArray(rawColumns)
  const rows: unknown[] = Array.isArray(rawColumns) ? rawColumns : []
  const columns = rows.flatMap(row => {
    const column = asRecord(row)
    const name = stringValue(column?.name).trim()
    if (!column || !name || !Array.isArray(column.tasks)) {
      return []
    }

    const tasks = column.tasks.slice(0, 50).flatMap(rowTask => {
      const task = asRecord(rowTask)
      const rawId = task?.id
      const id = typeof rawId === 'number' && Number.isFinite(rawId) ? String(rawId) : stringValue(rawId).trim()
      const title = stringValue(task?.title).trim()
      if (!task || !id || !title) {
        return []
      }

      return [{
        id,
        title,
        status: stringValue(task.status).trim(),
        priority: typeof task.priority === 'number' && Number.isFinite(task.priority) ? task.priority : null,
        createdAt: timestampValue(task.createdAt),
        completedAt: timestampValue(task.completedAt),
        sessionId: stringValue(task.sessionId).trim() || null,
        updatedAt: timestampValue(task.updatedAt)
      }]
    })

    return [{ name, tasks }]
  })

  return {
    columns,
    error: typeof state?.error === 'string' ? state.error : hasColumns ? null : 'Invalid kanban bridge state',
    loading: state?.loading === true,
    unavailable: state?.unavailable === true
  }
}

function useTownFeedBridge(): { bridge: TownFeedBridge | null; feed: TownFeedState | null } {
  const [bridge, setBridge] = useState<TownFeedBridge | null>(null)
  const [feed, setFeed] = useState<TownFeedState | null>(null)

  useEffect(() => {
    let activeBridge: TownFeedBridge | null = null
    let unsubscribe: (() => void) | null = null

    const attach = () => {
      if (activeBridge) {
        return
      }

      const candidate = windowTownFeedBridge()
      if (!candidate) {
        return
      }

      try {
        candidate.acquire?.()
        activeBridge = candidate
        setBridge(candidate)
        setFeed(normalizeTownFeedState(candidate.$feed?.get?.()))
        const cleanup = candidate.$feed?.subscribe?.((value: unknown) => setFeed(normalizeTownFeedState(value)))
        unsubscribe = typeof cleanup === 'function' ? cleanup : null
      } catch {
        try {
          candidate.release?.()
        } catch {
          // A malformed optional bridge should degrade to the pointer card.
        }
        activeBridge = null
        setBridge(null)
        setFeed(null)
      }
    }

    attach()
    if (!activeBridge) {
      window.addEventListener('hermes-town-feed-ready', attach, { once: true })
    }

    return () => {
      window.removeEventListener('hermes-town-feed-ready', attach)
      try {
        unsubscribe?.()
      } catch {
        // Bridge cleanup is best-effort because the provider may have reloaded.
      }
      try {
        activeBridge?.release?.()
      } catch {
        // Never let an untyped cross-plugin global take down Town home.
      }
    }
  }, [])

  return { bridge, feed }
}

function useTownKanbanBridge(): { bridge: TownFeedBridge | null; kanban: KanbanState | null } {
  const [bridge, setBridge] = useState<TownFeedBridge | null>(null)
  const [kanban, setKanban] = useState<KanbanState | null>(null)

  useEffect(() => {
    let activeBridge: TownFeedBridge | null = null
    let unsubscribe: (() => void) | null = null

    const attach = () => {
      if (activeBridge) {
        return
      }

      const candidate = windowTownKanbanBridge()
      if (!candidate) {
        return
      }

      try {
        candidate.acquire?.()
        activeBridge = candidate
        setBridge(candidate)
        setKanban(normalizeKanbanState(candidate.$kanban?.get?.()))
        const cleanup = candidate.$kanban?.subscribe?.((value: unknown) => setKanban(normalizeKanbanState(value)))
        unsubscribe = typeof cleanup === 'function' ? cleanup : null
      } catch {
        try {
          candidate.release?.()
        } catch {
          // A malformed optional bridge should degrade to recent sessions.
        }
        activeBridge = null
        setBridge(null)
        setKanban(null)
      }
    }

    attach()
    if (!activeBridge) {
      window.addEventListener('hermes-town-feed-ready', attach, { once: true })
    }

    return () => {
      window.removeEventListener('hermes-town-feed-ready', attach)
      try {
        unsubscribe?.()
      } catch {
        // Bridge cleanup is best-effort because the provider may have reloaded.
      }
      try {
        activeBridge?.release?.()
      } catch {
        // Never let an untyped cross-plugin global take down Town home.
      }
    }
  }, [])

  return { bridge, kanban }
}

function useRpc<T>(
  method: string,
  params: Record<string, unknown>,
  { intervalMs = MIN_POLL_INTERVAL_MS }: { intervalMs?: number } = {}
): RpcState<T> {
  const requestRef = useRef({ method, params })
  const [state, setState] = useState<RpcState<T>>({ data: null, error: null, loading: true })
  requestRef.current = { method, params }

  useEffect(() => {
    let live = true
    let refreshTimer: ReturnType<typeof setTimeout> | null = null
    const pollIntervalMs = Math.max(MIN_POLL_INTERVAL_MS, intervalMs)

    const refresh = async () => {
      const request = requestRef.current

      try {
        const data = await host.request<T>(request.method, request.params)
        if (live) {
          setState({ data, error: null, loading: false })
        }
      } catch (error) {
        if (live) {
          setState(current => ({ ...current, error: errorMessage(error), loading: false }))
        }
      }
    }

    const scheduleEventRefresh = () => {
      if (host.state.gateway.get() !== 'open' || refreshTimer !== null) {
        return
      }

      refreshTimer = setTimeout(() => {
        refreshTimer = null
        void refresh()
      }, EVENT_REFRESH_DEBOUNCE_MS)
    }

    void refresh()
    const interval = setInterval(() => void refresh(), pollIntervalMs)
    const offEvent = host.onEvent('*', scheduleEventRefresh)

    return () => {
      live = false
      clearInterval(interval)
      offEvent()
      if (refreshTimer !== null) {
        clearTimeout(refreshTimer)
      }
    }
  }, [intervalMs])

  return state
}

function useAttentionMarkers(activeSessionId: null | string): Record<string, AttentionMarker> {
  const [markers, setMarkers] = useState<Record<string, AttentionMarker>>({})

  useEffect(() => {
    if (!activeSessionId) {
      return
    }

    setMarkers(current => {
      if (current[activeSessionId]?.kind !== 'finished') {
        return current
      }

      const next = { ...current }
      delete next[activeSessionId]
      return next
    })
  }, [activeSessionId])

  useEffect(() => {
    const sessionIdFrom = (event: unknown) => stringValue(asRecord(event)?.session_id)

    const clearMarker = (event: unknown) => {
      const sessionId = sessionIdFrom(event)
      if (!sessionId) {
        return
      }

      setMarkers(current => {
        if (!current[sessionId]) {
          return current
        }

        const next = { ...current }
        delete next[sessionId]
        return next
      })
    }

    const mark = (event: unknown, kind: AttentionKind) => {
      const sessionId = sessionIdFrom(event)
      if (!sessionId || (kind === 'finished' && sessionId === host.state.activeSessionId.get())) {
        return
      }

      setMarkers(current => ({ ...current, [sessionId]: { kind, timestamp: Date.now() } }))
    }

    const disposers = [
      host.onEvent('message.start', clearMarker),
      host.onEvent('message.complete', (event: unknown) => mark(event, 'finished')),
      host.onEvent('clarify.request', (event: unknown) => mark(event, 'waiting')),
      host.onEvent('approval.request', (event: unknown) => mark(event, 'approval')),
      host.onEvent('sudo.request', (event: unknown) => mark(event, 'approval')),
      host.onEvent('secret.request', (event: unknown) => mark(event, 'waiting'))
    ]

    return () => disposers.forEach(dispose => dispose())
  }, [])

  return markers
}

function normalizeSessions(value: unknown): SessionRow[] {
  const record = asRecord(value)
  const rows = Array.isArray(record?.sessions) ? record.sessions : []

  return rows.flatMap(row => {
    const item = asRecord(row)
    const id = stringValue(item?.id)
    if (!item || !id) {
      return []
    }

    return [
      {
        id,
        preview: stringValue(item.preview),
        source: stringValue(item.source),
        startedAt: timestampValue(item.started_at),
        title: stringValue(item.title) || 'Untitled session'
      }
    ]
  })
}

function normalizeActiveSessions(value: unknown): ActiveSessionRow[] {
  const record = asRecord(value)
  const rows = Array.isArray(record?.sessions) ? record.sessions : []

  return rows.flatMap(row => {
    const item = asRecord(row)
    const id = stringValue(item?.id || item?.session_id)
    if (!item || !id) {
      return []
    }

    return [
      {
        id,
        lastActive: timestampValue(item.last_active || item.started_at),
        preview: stringValue(item.preview),
        sessionKey: stringValue(item.session_key) || id,
        status: stringValue(item.status),
        title: stringValue(item.title) || 'Active session'
      }
    ]
  })
}

function normalizeCronRows(value: unknown): CronRow[] {
  const record = asRecord(value)
  const rows = Array.isArray(value) ? value : Array.isArray(record?.jobs) ? record.jobs : []

  return rows.flatMap(row => {
    const item = asRecord(row)
    const id = stringValue(item?.job_id || item?.id)
    if (!item || !id) {
      return []
    }

    const scheduleRecord = asRecord(item.schedule)
    const schedule =
      stringValue(item.schedule) ||
      stringValue(item.schedule_display) ||
      stringValue(scheduleRecord?.display || scheduleRecord?.value || scheduleRecord?.expr)
    const promptPreview = stringValue(item.prompt_preview)

    return [
      {
        enabled: item.enabled !== false,
        id,
        lastDeliveryError: stringValue(item.last_delivery_error),
        lastError: stringValue(item.last_error),
        lastRunAt: timestampValue(item.last_run_at),
        lastStatus: stringValue(item.last_status),
        name: stringValue(item.name) || 'Unnamed routine',
        nextRunAt: timestampValue(item.next_run_at),
        prompt: promptPreview.endsWith('...') ? '' : promptPreview,
        promptPreview,
        schedule: schedule || 'Schedule unavailable',
        state: stringValue(item.state)
      }
    ]
  })
}

function normalizeRecentSessions(value: unknown): RecentSessionActivity[] {
  const record = asRecord(value)
  const rows = Array.isArray(record?.sessions) ? record.sessions : []

  return rows.flatMap(row => {
    const item = asRecord(row)
    const id = stringValue(item?.id)
    // Cron runs already appear via the cron lane (humanized, run-history
    // click-through); their sessions would double-count them. Mirrors the
    // host sidebar's own non-interactive source exclusions.
    const source = stringValue(item?.source).trim().toLowerCase()
    if (source === 'cron' || source === 'subagent' || source === 'tool') {
      return []
    }
    const title = stringValue(item?.title).trim()
    const preview = stringValue(item?.preview).replace(/\s+/g, ' ').trim()
    if (!item || !id || (!title && !preview)) {
      return []
    }

    const previewTitle = preview.length > 60 ? `${preview.slice(0, 59).trimEnd()}…` : preview
    return [{ id, kind: 'session' as const, timestamp: timestampValue(item.started_at), title: title || previewTitle || 'Untitled session' }]
  })
}

function humanizeJobName(name: string): string {
  const normalized = name.replace(/[-_]+/g, ' ').replace(/\s+/g, ' ').trim()
  return normalized ? normalized[0].toUpperCase() + normalized.slice(1) : 'Unnamed routine'
}

function recentRoutineOutcome(row: CronRow): 'completed' | 'failed' | null {
  const status = row.lastStatus.trim().toLowerCase()
  if (status === 'ok' || status === 'completed') {
    return 'completed'
  }
  if (status.includes('error') || status.includes('fail') || row.lastError.trim()) {
    return 'failed'
  }
  return null
}

function formatSessionAge(timestamp: number | null, now = Date.now()): string {
  return timestamp === null ? 'Time unavailable' : relativeTime(timestamp, now)
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

function telegramChatIdFromSource(source: string): string | null {
  const match = source.trim().match(/^telegram:(.+)$/i)
  return match?.[1]?.trim() || null
}

function cronJobIdFromSource(source: string): string | null {
  const match = source.trim().match(/^cron:(.+)$/i)
  return match?.[1]?.trim() || null
}

function isCalendarSource(source: string): boolean {
  return /^calendar:[^:]+:.+$/i.test(source.trim())
}

function telegramSourceLabel(item: TownFeedItem, chatId: string): string {
  const summaryName = item.summary.match(/^(?:telegram(?: chat)?|chat)\s*[:\u00b7\u2014-]\s*([^:\n\u00b7\u2014]{1,60})/i)?.[1]?.trim()
  const headlineName = item.headline.match(/^([^:\n]{1,60}):\s/)?.[1]?.trim()
  return summaryName || headlineName || chatId
}

function townFeedSourceReference(item: TownFeedItem, cronRows: CronRow[]): { icon: string; label: string } {
  if (sessionIdFromSource(item.source)) {
    return { icon: 'comment-discussion', label: 'Session' }
  }

  const gmail = gmailSourceFromSource(item.source)
  if (gmail) {
    return { icon: 'mail', label: gmail.account.split('@', 1)[0] || 'Gmail' }
  }

  const telegramChatId = telegramChatIdFromSource(item.source)
  if (telegramChatId) {
    return { icon: 'send', label: telegramSourceLabel(item, telegramChatId) }
  }

  const cronJobId = cronJobIdFromSource(item.source)
  if (cronJobId) {
    return { icon: 'clock', label: cronRows.find(row => row.id === cronJobId)?.name || 'Routine' }
  }

  if (isCalendarSource(item.source)) {
    return { icon: 'calendar', label: 'Calendar' }
  }

  return { icon: 'symbol-misc', label: item.source || 'Hermes' }
}

function referencesTownFeedCurator(value: string): boolean {
  const folded = value.toLowerCase()
  return folded.includes('town feed curator') || folded.includes('town_feed_curate')
}

function stripImportantPreviewBlock(value: string): string {
  return value.replace(/^\s*\[IMPORTANT:[\s\S]*?\]\s*/i, '')
}

function sessionFallbackRows(rows: SessionRow[]): SessionRow[] {
  return rows.flatMap(row => {
    if (referencesTownFeedCurator(row.title) || referencesTownFeedCurator(row.preview)) {
      return []
    }

    return [{ ...row, preview: stripImportantPreviewBlock(row.preview) }]
  })
}

const ACTIVE_KANBAN_COLUMNS = ['triage', 'todo', 'scheduled', 'ready', 'running', 'blocked', 'review'] as const
// Statuses POST /kanban/complete can actually transition to done; the checkbox
// is disabled for the rest so the UI never advertises an impossible action.
const COMPLETABLE_KANBAN_COLUMNS = new Set<string>(['triage', 'todo', 'ready', 'running', 'blocked'])

function activeKanbanTasks(columns: KanbanColumn[]): Array<{ column: KanbanColumn; task: KanbanTask }> {
  const columnRank = new Map<string, number>(ACTIVE_KANBAN_COLUMNS.map((name, index) => [name, index]))

  return columns
    .filter(column => columnRank.has(column.name.toLowerCase()))
    .flatMap(column => column.tasks.map(task => ({ column, task })))
    .sort((a, b) => {
      // Column priority first (triage outranks running) so stale triage work
      // cannot be pushed off the card by chattier tasks; recency within column.
      const rankDelta =
        (columnRank.get(a.column.name.toLowerCase()) ?? 99) - (columnRank.get(b.column.name.toLowerCase()) ?? 99)
      return rankDelta !== 0 ? rankDelta : kanbanTaskActivity(b.task) - kanbanTaskActivity(a.task)
    })
}

function kanbanTaskActivity(task: KanbanTask): number {
  return task.updatedAt ?? task.completedAt ?? task.createdAt ?? 0
}

function completedKanbanTasks(
  columns: KanbanColumn[],
  doneTaskWindowHours: DoneTaskWindowHours
): Array<{ column: KanbanColumn; task: KanbanTask }> {
  const cutoff = Date.now() - doneTaskWindowHours * 60 * 60 * 1_000

  return columns
    .filter(column => column.name.toLowerCase() === 'done')
    .flatMap(column => column.tasks.map(task => ({ column, task })))
    .filter(({ task }) => kanbanTaskActivity(task) >= cutoff)
    .sort((a, b) => kanbanTaskActivity(b.task) - kanbanTaskActivity(a.task))
    .slice(0, 3)
}

function kanbanStatusLabel(status: string): string {
  const normalized = status.trim().toLowerCase()
  if (normalized === 'running') {
    return 'In progress'
  }
  if (normalized === 'todo') {
    return 'To do'
  }
  if (!normalized) {
    return 'Task'
  }
  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

function formatDayTime(timestamp: number): string {
  const formatter: unknown = fmtDayTime
  const format = asRecord(formatter)?.format

  if (typeof format === 'function') {
    return String(format.call(formatter, new Date(timestamp)))
  }

  return new Date(timestamp).toLocaleString()
}

function attentionItems(
  activeRows: ActiveSessionRow[],
  sessionRows: SessionRow[],
  markers: Record<string, AttentionMarker>,
  activeSessionId: null | string
): AttentionItem[] {
  const items: AttentionItem[] = []
  const seen = new Set<string>()

  for (const row of activeRows) {
    const marker = markers[row.id]
    if (
      (row.id === activeSessionId && marker?.kind === 'finished') ||
      (row.status !== 'waiting' && !marker)
    ) {
      continue
    }

    const detail =
      marker?.kind === 'approval'
        ? 'Approval required before work can continue'
        : marker?.kind === 'finished'
          ? FINISHED_ATTENTION_DETAIL
          : 'Waiting for your response'

    items.push({ detail, id: row.id, sessionKey: row.sessionKey, title: row.title })
    seen.add(row.id)
    seen.add(row.sessionKey)
  }

  for (const [id, marker] of Object.entries(markers).sort((a, b) => b[1].timestamp - a[1].timestamp)) {
    if ((id === activeSessionId && marker.kind === 'finished') || seen.has(id)) {
      continue
    }

    const session = sessionRows.find(row => row.id === id)
    const detail =
      marker.kind === 'approval'
        ? 'Approval required before work can continue'
        : marker.kind === 'finished'
          ? FINISHED_ATTENTION_DETAIL
          : 'Waiting for your response'

    items.push({ detail, id, title: session?.title || 'Session needs attention' })
  }

  return items
}

function routineOutcome(row: CronRow, now = Date.now()): { label: string; tone: 'bad' | 'good' | 'muted' | 'warn' } {
  if (!row.enabled || row.state === 'paused') {
    return { label: 'Paused', tone: 'warn' }
  }

  if (row.lastStatus === 'error' || row.lastError || row.lastDeliveryError) {
    return { label: row.lastError || row.lastDeliveryError || 'Last run failed', tone: 'bad' }
  }

  if (row.lastRunAt !== null) {
    return {
      label: row.lastStatus === 'ok' ? `Completed ${relativeTime(row.lastRunAt, now)}` : `Last ran ${relativeTime(row.lastRunAt, now)}`,
      tone: row.lastStatus === 'ok' ? 'good' : 'muted'
    }
  }

  return { label: 'Not run yet', tone: 'muted' }
}

function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div className="space-y-2" key={index}>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  )
}

function runTone(status: string): 'bad' | 'good' | 'muted' {
  if (status === 'ok') {
    return 'good'
  }
  if (status === 'error' || status === 'failed') {
    return 'bad'
  }
  return 'muted'
}

function openRunHistory(event: { currentTarget: { dataset: { jobId?: string; jobName?: string } } }): void {
  const id = event.currentTarget.dataset.jobId?.trim()
  if (!id) {
    return
  }

  $runHistoryTarget.set({ id, name: event.currentTarget.dataset.jobName?.trim() || 'Routine' })
  $runHistoryDialogOpen.set(true)
}

function openSession(sessionId: string | null | undefined): void {
  if (sessionId) {
    host.navigate(`/${encodeURIComponent(sessionId)}`)
  }
}

function openRecentSession(event: { currentTarget: { dataset: { sessionId?: string } } }): void {
  openSession(event.currentTarget.dataset.sessionId)
}

function openNewRoutineDialog(): void {
  $newRoutineDialogOpen.set(true)
}

function openScheduleDialog(): void {
  const bridge = windowTownFeedBridge()
  const schedule = normalizeTownFeedState(bridge?.$feed?.get?.()).schedule
  if (schedule.some(event => event.endAt >= Date.now())) {
    $scheduleDialogOpen.set(true)
  }
}

function NewRoutineDialog() {
  const open = useValue($newRoutineDialogOpen)
  const [draft, setDraft] = useState(NEW_ROUTINE_PROMPT)
  const [sending, setSending] = useState(false)
  const draftRef = useRef(draft)
  const sendingRef = useRef(sending)
  draftRef.current = draft
  sendingRef.current = sending

  useEffect(() => {
    if (open) {
      setDraft(NEW_ROUTINE_PROMPT)
    }
  }, [open])

  const sendToAgent = async () => {
    if (sendingRef.current) {
      return
    }

    const sessionId = host.state.activeSessionId.get()
    if (!sessionId) {
      host.notify({ kind: 'info', message: 'Open a session first, then try again.' })
      return
    }

    sendingRef.current = true
    setSending(true)
    try {
      await host.request('prompt.submit', { session_id: sessionId, text: draftRef.current })
      $newRoutineDialogOpen.set(false)
      host.notify({ kind: 'success', message: 'Routine request sent to the agent.' })
    } catch (error) {
      host.notifyError(error, 'Could not send routine request')
    } finally {
      sendingRef.current = false
      setSending(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen: boolean) => $newRoutineDialogOpen.set(nextOpen)}>
      <DialogContent className="max-w-md gap-4">
        <DialogHeader>
          <DialogTitle>New routine</DialogTitle>
          <DialogDescription>Describe the recurring work you want the agent to set up.</DialogDescription>
        </DialogHeader>
        <Textarea
          autoFocus
          className="min-h-28 resize-y"
          onChange={(event: { currentTarget: { value: string } }) => setDraft(event.currentTarget.value)}
          value={draft}
        />
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => $newRoutineDialogOpen.set(false)}>
            Cancel
          </Button>
          <Button disabled={sending} type="button" onClick={() => void sendToAgent()}>
            {sending ? 'Sending...' : 'Send to agent'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

// Keep this dialog in sync with its twin in town-routines/plugin.tsx; desktop bundles cannot import each other.
function RunHistoryDialog() {
  const open = useValue($runHistoryDialogOpen)
  const target = useValue($runHistoryTarget)
  const now = useNow()
  const [runs, setRuns] = useState<CronRun[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const requestIdRef = useRef(0)

  const loadRuns = async (jobId: string) => {
    const requestId = ++requestIdRef.current
    setLoading(true)
    setError(null)
    try {
      const bridge = windowTownFeedBridge()
      if (typeof bridge?.getCronRuns !== 'function') {
        throw new Error('Run history is unavailable until Town Feed is loaded.')
      }
      const result = await bridge.getCronRuns(jobId)
      if (requestId === requestIdRef.current) {
        setRuns(result.runs.slice(0, 3))
      }
    } catch (loadError) {
      if (requestId === requestIdRef.current) {
        setRuns([])
        setError(errorMessage(loadError))
      }
    } finally {
      if (requestId === requestIdRef.current) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    if (open && target) {
      void loadRuns(target.id)
    } else {
      requestIdRef.current += 1
      setRuns([])
      setError(null)
      setLoading(false)
    }
  }, [open, target?.id])

  const retry = () => {
    const current = $runHistoryTarget.get()
    if (current) {
      void loadRuns(current.id)
    }
  }

  return (
    <Dialog open={open} onOpenChange={(nextOpen: boolean) => $runHistoryDialogOpen.set(nextOpen)}>
      <DialogContent className="max-w-xl gap-4">
        <DialogHeader>
          <DialogTitle>{target?.name || 'Routine'} run history</DialogTitle>
          <DialogDescription>The three most recent saved cron outputs.</DialogDescription>
        </DialogHeader>
        {loading ? (
          <CardSkeleton rows={3} />
        ) : error ? (
          <ErrorState className="min-h-32" description={error} title="Could not load run history" />
        ) : runs.length === 0 ? (
          <EmptyState className="min-h-32" description="Run this routine to create its first saved output." title="No runs yet" />
        ) : (
          <div className="space-y-4">
            {runs.map(run => (
              <div className="space-y-2" key={`${run.startedAt}-${run.status}`}>
                <div className="flex items-center gap-2 text-xs text-(--ui-text-secondary)">
                  <StatusDot tone={runTone(run.status)} />
                  <span>{relativeTime(run.startedAt, now)}</span>
                  <span className="text-(--ui-text-tertiary)">
                    {run.status && run.status.trim().toLowerCase() !== 'unknown' ? run.status : '—'}
                  </span>
                </div>
                <ScrollArea className="max-h-40 rounded border border-(--ui-stroke-secondary)">
                  <pre className="whitespace-pre-wrap break-words p-3 font-mono text-xs text-(--ui-text-secondary)">
                    {presentRunOutput(run.output || run.error) || 'No output was saved.'}
                  </pre>
                </ScrollArea>
              </div>
            ))}
          </div>
        )}
        {error ? (
          <DialogFooter>
            <Button type="button" variant="outline" onClick={retry}>Retry</Button>
          </DialogFooter>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

function localDateKey(timestamp: number): string {
  const date = new Date(timestamp)
  return `${date.getFullYear()}-${date.getMonth()}-${date.getDate()}`
}

function scheduleDayLabel(timestamp: number, now: number): string {
  const today = new Date(now)
  const tomorrow = new Date(today.getFullYear(), today.getMonth(), today.getDate() + 1)
  const dateKey = localDateKey(timestamp)
  if (dateKey === localDateKey(now)) {
    return 'Today'
  }
  if (dateKey === localDateKey(tomorrow.getTime())) {
    return 'Tomorrow'
  }
  return new Intl.DateTimeFormat(undefined, { weekday: 'long' }).format(new Date(timestamp))
}

function scheduleTime(timestamp: number): string {
  return new Intl.DateTimeFormat('en-US', { hour: 'numeric', minute: '2-digit' })
    .format(new Date(timestamp))
    .toLowerCase()
}

function scheduleAccountName(account: string): string {
  return account.split('@', 1)[0] || account
}

function ScheduleDialog({ schedule }: { schedule: TownScheduleEvent[] }) {
  const open = useValue($scheduleDialogOpen)
  const now = Date.now()
  const upcoming = schedule.filter(event => event.endAt >= now)
  const showAccounts = new Set(upcoming.map(event => event.account)).size > 1
  const groups = upcoming.reduce<Array<{ dateKey: string; events: TownScheduleEvent[] }>>((result, event) => {
    const dateKey = localDateKey(event.startAt)
    const current = result.at(-1)
    if (current?.dateKey === dateKey) {
      current.events.push(event)
    } else {
      result.push({ dateKey, events: [event] })
    }
    return result
  }, [])

  return (
    <Dialog open={open} onOpenChange={(nextOpen: boolean) => $scheduleDialogOpen.set(nextOpen)}>
      <DialogContent className="max-w-xl gap-4">
        <DialogHeader>
          <DialogTitle>Schedule</DialogTitle>
          <DialogDescription>Your upcoming calendar events.</DialogDescription>
        </DialogHeader>
        <ScrollArea className="max-h-96">
          <div className="space-y-5 pr-3">
            {groups.map(group => (
              <section className="space-y-2" key={group.dateKey}>
                <h3 className="text-xs font-medium text-(--ui-text-secondary)">
                  {scheduleDayLabel(group.events[0].startAt, now)}
                </h3>
                <div className="space-y-1">
                  {group.events.map((event, index) => {
                    const detailParts = [
                      event.conference,
                      event.location,
                      event.attendees.length > 0 ? `with ${event.attendees.slice(0, 3).join(', ')}${event.attendees.length > 3 ? ` +${event.attendees.length - 3}` : ''}` : null
                    ].filter((part): part is string => Boolean(part))
                    const details = detailParts.join(' · ')

                    return (
                      <div className="flex items-start gap-3 rounded-md px-2 py-1.5" key={`${event.startAt}-${event.endAt}-${event.account}-${index}`}>
                        <div className="w-28 shrink-0 pt-0.5 text-xs text-(--ui-text-tertiary)">
                          {event.allDay ? (
                            <Badge size="xs" variant="muted">All day</Badge>
                          ) : (
                            <span>{scheduleTime(event.startAt)} – {scheduleTime(event.endAt)}</span>
                          )}
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm text-(--ui-text-primary)" title={event.summary}>
                            {event.summary}
                          </p>
                          {details ? (
                            <p className="truncate text-xs text-(--ui-text-tertiary)" title={details}>{details}</p>
                          ) : null}
                        </div>
                        {showAccounts ? (
                          <Badge size="xs" variant="muted">{scheduleAccountName(event.account)}</Badge>
                        ) : null}
                      </div>
                    )
                  })}
                </div>
              </section>
            ))}
          </div>
        </ScrollArea>
      </DialogContent>
    </Dialog>
  )
}

function SectionCard({
  action,
  children,
  className = '',
  persistentAction = false,
  sideRail = false,
  title
}: {
  action?: ReactNode
  children: ReactNode
  className?: string
  persistentAction?: boolean
  sideRail?: boolean
  title: ReactNode
}) {
  return (
    <section
      className={`min-w-0 ${sideRail ? 'rounded-xl border border-(--ui-stroke-secondary) p-4' : 'px-0 py-0'} ${className}`}
    >
      <div className="group flex items-center justify-between gap-3">
        <h2 className="text-sm font-medium text-(--ui-text-secondary)">{title}</h2>
        {action ? (
          <div className={persistentAction ? '' : 'opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-within:opacity-100'}>
            {action}
          </div>
        ) : null}
      </div>
      <div className="mt-4">{children}</div>
    </section>
  )
}

function CardEmptyState({
  action,
  description,
  title
}: {
  action?: ReactNode
  description: string
  title: string
}) {
  return (
    <div className="py-6">
      <EmptyState className="min-h-0" description={description} title={title} />
      {action ? <div className="mt-1 flex justify-center">{action}</div> : null}
    </div>
  )
}

type ComposerMode = 'task' | 'chat'

interface ComposerState {
  draft: string
  focusRequest: number
  mode: ComposerMode
}

interface ComposerStore {
  get(): ComposerState
  set(value: ComposerState): void
  subscribe(listener: (value: ComposerState) => void): () => void
}

const $composer = atom({ draft: '', focusRequest: 0, mode: 'task' as ComposerMode }) as ComposerStore

function setComposerDraftAndFocus(draft: string, mode: ComposerMode): void {
  const current = $composer.get()
  $composer.set({ draft, focusRequest: current.focusRequest + 1, mode })
}

function focusTaskComposer(): void {
  const current = $composer.get()
  $composer.set({ ...current, focusRequest: current.focusRequest + 1, mode: 'task' })
}

function prefillMorningBriefComposer(): void {
  setComposerDraftAndFocus(MORNING_BRIEF_COMMAND, 'task')
}

function prefillTodayBriefComposer(): void {
  setComposerDraftAndFocus(TODAY_BRIEF_PROMPT, 'chat')
}

function planTask(): void {
  setComposerDraftAndFocus('', 'task')
}

function clipboardWriteAvailable(): boolean {
  return typeof navigator !== 'undefined' && typeof navigator.clipboard?.writeText === 'function'
}

async function copyMorningBriefCommand(): Promise<void> {
  if (!clipboardWriteAvailable()) {
    host.notify({ kind: 'info', message: 'Clipboard access is unavailable; select and copy the command instead.' })
    return
  }

  try {
    await navigator.clipboard.writeText(MORNING_BRIEF_COMMAND)
    host.notify({ kind: 'success', message: 'Morning briefing command copied.' })
  } catch (error) {
    host.notifyError(error, 'Could not copy the morning briefing command')
  }
}

const COMPOSER_MODES: ReadonlyArray<{ id: ComposerMode; label: string }> = [
  { id: 'task', label: 'Task' },
  { id: 'chat', label: 'Chat' }
]

function composerTitle(prompt: string): string {
  return prompt.trim().split(/\r?\n/, 1)[0].trim().slice(0, 60)
}

function composerClientKey(): string {
  return crypto.randomUUID()
}

// Shared session-start handshake. storedSessionId is the routable id (may be
// empty on older gateways); the live sessionId is only valid for prompt.submit.
async function startSessionWithPrompt(title: string, prompt: string): Promise<{ sessionId: string; storedSessionId: string }> {
  const created = await host.request<unknown>('session.create', { title })
  const record = asRecord(created)
  const sessionId = stringValue(record?.session_id)
  if (!sessionId) {
    throw new Error('The gateway did not return a session ID.')
  }

  await host.request('prompt.submit', { session_id: sessionId, text: prompt })
  return { sessionId, storedSessionId: stringValue(record?.stored_session_id) }
}

function TaskComposer() {
  const gateway = useValue(host.state.gateway)
  const composer = useValue($composer)
  const [sending, setSending] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement | null>(null)
  const handledFocusRequestRef = useRef(composer.focusRequest)
  const sendingRef = useRef(sending)
  sendingRef.current = sending

  useEffect(() => {
    if (composer.focusRequest === handledFocusRequestRef.current) {
      return
    }

    handledFocusRequestRef.current = composer.focusRequest
    textareaRef.current?.focus()
  }, [composer.focusRequest])

  const updateDraft = (draft: string) => {
    const current = $composer.get()
    $composer.set({ ...current, draft })
  }

  const selectMode = (nextMode: ComposerMode) => {
    const current = $composer.get()
    $composer.set({ ...current, mode: nextMode })
  }

  const submit = async () => {
    const { draft: prompt, mode: submitMode } = $composer.get()
    if (sendingRef.current || host.state.gateway.get() !== 'open' || !prompt.trim()) {
      return
    }

    const title = composerTitle(prompt)
    const clientKey = submitMode === 'task' ? composerClientKey() : null
    sendingRef.current = true
    setSending(true)

    try {
      const started = await startSessionWithPrompt(title, prompt)
      // The card link must use the stored id: live session_id is not routable.
      const storedSessionId = started.storedSessionId || started.sessionId

      let cardNotice: string | null = null
      if (submitMode === 'task' && clientKey) {
        const bridge = windowTownKanbanBridge()
        if (typeof bridge?.createTask !== 'function') {
          cardNotice = 'Session started; kanban card skipped (town-feed bridge unavailable)'
        } else {
          try {
            await bridge.createTask({ client_key: clientKey, prompt, session_id: storedSessionId, title })
          } catch (error) {
            cardNotice = `Session started; kanban card could not be filed: ${errorMessage(error)}`
          }
        }
      }

      updateDraft('')
      host.notify({ kind: 'success', message: `Session started: ${title}` })
      if (cardNotice) {
        host.notify({ kind: 'info', message: cardNotice })
      }
    } catch (error) {
      host.notifyError(error, 'Could not start task')
    } finally {
      sendingRef.current = false
      setSending(false)
    }
  }

  const handleKeyDown = (event: { key: string; preventDefault(): void; shiftKey: boolean }) => {
    if (event.key !== 'Enter' || event.shiftKey) {
      return
    }

    event.preventDefault()
    void submit()
  }

  const disabled = sending || !composer.draft.trim() || gateway !== 'open'
  const gatewayOpen = gateway === 'open'

  return (
    <section
      className={`rounded-xl border border-(--ui-stroke-secondary) transition-all duration-150 focus-within:ring-1 focus-within:ring-(--ui-accent) ${gatewayOpen ? '' : 'opacity-60'}`}
    >
      <Textarea
        aria-label="Task or chat prompt"
        className="min-h-20 resize-y !rounded-none !border-0 !bg-transparent !shadow-none focus:!border-transparent focus:!shadow-none"
        onChange={(event: { currentTarget: { value: string } }) => updateDraft(event.currentTarget.value)}
        onKeyDown={handleKeyDown}
        placeholder="Give Hermes a task…"
        ref={textareaRef}
        value={composer.draft}
      />
      <div className="flex flex-wrap items-center justify-between gap-3 px-3 pb-3 pt-3">
        <span className="text-xs text-(--ui-text-tertiary)">
          {gatewayOpen ? 'Enter to start · Shift+Enter for a new line' : 'Gateway offline — reconnecting…'}
        </span>
        <div className="flex shrink-0 items-center gap-3">
          <SegmentedControl className="shrink-0" onChange={selectMode} options={COMPOSER_MODES} value={composer.mode} />
          <Button disabled={disabled} type="button" onClick={() => void submit()}>
            {sending ? 'Starting…' : composer.mode === 'task' ? 'Start task' : 'Start chat'}
          </Button>
        </div>
      </div>
    </section>
  )
}

interface KanbanTaskRowProps {
  bridgeAvailable: boolean
  column: KanbanColumn
  completed: boolean
  needsInputItem?: AttentionItem
  onComplete: (taskId: string) => void
  optimistic: boolean
  task: KanbanTask
}

function KanbanTaskRow({ bridgeAvailable, column, completed, needsInputItem, onComplete, optimistic, task }: KanbanTaskRowProps) {
  const now = useNow()
  const completable = COMPLETABLE_KANBAN_COLUMNS.has(column.name.toLowerCase())
  const taskRef = useRef(task)
  const onCompleteRef = useRef(onComplete)
  const rowStateRef = useRef({ completed, optimistic })
  taskRef.current = task
  onCompleteRef.current = onComplete
  rowStateRef.current = { completed, optimistic }

  const handleCheckedChange = (checked: boolean) => {
    const currentTask = taskRef.current
    const currentState = rowStateRef.current
    if (checked && !currentState.completed && !currentState.optimistic) {
      onCompleteRef.current(currentTask.id)
    }
  }

  const handleOpenSession = () => openSession(taskRef.current.sessionId)

  const activity = kanbanTaskActivity(task)
  const subtitle = `${kanbanStatusLabel(column.name)} · ${activity > 0 ? relativeTime(activity, now) : 'time unavailable'}`
  const textClassName = 'min-w-0 flex-1 text-left'

  return (
    <div
      className={`flex min-w-0 items-start gap-3 border-t border-(--ui-stroke-secondary) py-3 transition-colors duration-150 first:border-t-0 first:pt-0 hover:bg-(--ui-row-hover-background) ${optimistic ? 'opacity-60' : ''}`}
    >
      <Checkbox
        aria-label={completed ? `${task.title} completed` : `Complete ${task.title}`}
        checked={completed || optimistic}
        disabled={completed || optimistic || !bridgeAvailable || !completable}
        title={completable || completed ? undefined : `${kanbanStatusLabel(column.name)} tasks finish on their own schedule`}
        onCheckedChange={handleCheckedChange}
      />
      <div className={textClassName}>
        <div className={`truncate text-sm font-medium ${optimistic ? 'line-through' : ''}`} title={task.title}>
          {task.title}
        </div>
        {needsInputItem ? (
          <div className="mt-0.5 flex min-w-0 text-xs" title={`Needs input: ${needsInputItem.detail}`}>
            <span className="shrink-0 font-medium text-(--ui-accent)">Needs input:</span>
            <span className="min-w-0 truncate text-(--ui-text-tertiary)">&nbsp;{needsInputItem.detail}</span>
          </div>
        ) : (
          <div className="mt-0.5 text-xs text-(--ui-text-tertiary)">{subtitle}</div>
        )}
      </div>
      {task.sessionId ? (
        <Button
          aria-label={`Open session for ${task.title}`}
          className="h-6 shrink-0 gap-1.5 px-2 text-xs"
          size="inline"
          type="button"
          variant="outline"
          onClick={handleOpenSession}
        >
          <Codicon name="comment-discussion" size="0.75rem" />
          <span>Open session</span>
        </Button>
      ) : (
        <div />
      )}
    </div>
  )
}

function TasksCard({
  activeSessions,
  doneTaskWindowHours,
  sessions,
  waitingItems
}: {
  activeSessions: RpcState<unknown>
  doneTaskWindowHours: DoneTaskWindowHours
  sessions: RpcState<unknown>
  waitingItems: AttentionItem[]
}) {
  const { bridge, kanban } = useTownKanbanBridge()
  const now = useNow()
  const [optimisticTaskIds, setOptimisticTaskIds] = useState<Set<string>>(new Set())
  const bridgeRef = useRef(bridge)
  const optimisticTaskIdsRef = useRef(optimisticTaskIds)
  bridgeRef.current = bridge
  optimisticTaskIdsRef.current = optimisticTaskIds
  const sessionRows = normalizeSessions(sessions.data)
  const fallbackRows = sessionFallbackRows(sessionRows)
  const showSessionsFallback = !bridge || !kanban || kanban.unavailable || Boolean(kanban.error)
  const waitingItemAliases = new Map<string, AttentionItem>()
  for (const item of waitingItems) {
    if (item.detail === FINISHED_ATTENTION_DETAIL) {
      continue
    }
    waitingItemAliases.set(item.id, item)
    if (item.sessionKey && item.sessionKey !== item.id) {
      waitingItemAliases.set(item.sessionKey, item)
    }
  }
  const activeTasks = kanban ? activeKanbanTasks(kanban.columns) : []
  const completedTasks = kanban ? completedKanbanTasks(kanban.columns, doneTaskWindowHours) : []
  const orderedTasks = [
    ...activeTasks.map(row => ({ ...row, completed: false })),
    ...completedTasks.map(row => ({ ...row, completed: true }))
  ]
  const visibleTasks = [
    ...orderedTasks.filter(({ task }) => task.sessionId && waitingItemAliases.has(task.sessionId)),
    ...orderedTasks.filter(({ task }) => !task.sessionId || !waitingItemAliases.has(task.sessionId))
  ].slice(0, 7)
  const boardAndAttentionClear = !showSessionsFallback
    && !kanban?.loading
    && visibleTasks.length === 0
    && !activeSessions.loading
    && !activeSessions.error
    && waitingItems.length === 0

  useEffect(() => {
    if (!kanban || optimisticTaskIdsRef.current.size === 0) {
      return
    }

    const currentStatuses = new Map(
      kanban.columns.flatMap(column => column.tasks.map(task => [task.id, task.status.toLowerCase()] as const))
    )
    const next = new Set(optimisticTaskIdsRef.current)
    for (const taskId of next) {
      const status = currentStatuses.get(taskId)
      if (!status || status === 'done') {
        next.delete(taskId)
      }
    }
    if (next.size !== optimisticTaskIdsRef.current.size) {
      optimisticTaskIdsRef.current = next
      setOptimisticTaskIds(next)
    }
  }, [kanban])

  const setTaskOptimistic = (taskId: string, optimistic: boolean) => {
    const next = new Set(optimisticTaskIdsRef.current)
    if (optimistic) {
      next.add(taskId)
    } else {
      next.delete(taskId)
    }
    optimisticTaskIdsRef.current = next
    setOptimisticTaskIds(next)
  }

  const completeTask = (taskId: string) => {
    if (optimisticTaskIdsRef.current.has(taskId)) {
      return
    }

    const currentBridge = bridgeRef.current
    if (typeof currentBridge?.completeTask !== 'function') {
      host.notify({ kind: 'info', message: 'Task completion is unavailable while the town-feed bridge reloads.' })
      return
    }

    setTaskOptimistic(taskId, true)
    void currentBridge.completeTask(taskId).catch(error => {
      setTaskOptimistic(taskId, false)
      host.notifyError(error, 'Could not complete task')
    })
  }

  return (
    <SectionCard title="Tasks">
      {boardAndAttentionClear ? (
        <div className="flex flex-col items-center justify-center text-center" style={{ height: 96 }}>
          <p className="text-sm font-medium">Board clear — nothing needs your attention</p>
          <p className="mt-1 text-xs text-(--ui-text-tertiary)">Add a task whenever you're ready to start something new.</p>
          <Button className="mt-1" size="inline" type="button" variant="text" onClick={focusTaskComposer}>Add a task</Button>
        </div>
      ) : (
        <>
          {showSessionsFallback ? (
            <div>
              <p className="mb-3 text-xs leading-5 text-(--ui-text-tertiary)">
                Showing recent sessions (kanban bridge offline)
              </p>
              {sessions.loading ? (
                <CardSkeleton rows={4} />
              ) : sessions.error ? (
                <ErrorState className="py-6" description={sessions.error} title="Could not load recent sessions" />
              ) : fallbackRows.length === 0 ? (
                <CardEmptyState
                  action={<Button size="inline" type="button" variant="text" onClick={focusTaskComposer}>Start a task</Button>}
                  description="Start a task or chat and its session will appear here."
                  title="No recent sessions"
                />
              ) : (
                <div>
                  {fallbackRows.slice(0, 5).map(session => (
                    <button
                      aria-label={`Open session ${session.title}`}
                      className="-mx-2 block w-full min-w-0 cursor-pointer rounded-lg px-2 py-2.5 text-left transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
                      key={session.id}
                      onClick={() => host.navigate(`/${encodeURIComponent(session.id)}`)}
                      type="button"
                    >
                      <div className="flex min-w-0 items-center gap-3">
                        <div className="min-w-0 flex-1 truncate text-sm font-medium" title={session.title}>
                          {session.title}
                        </div>
                        <Badge variant="muted">{session.source || 'Session'}</Badge>
                      </div>
                      <div className="mt-1 min-w-0 truncate text-xs text-(--ui-text-tertiary)">
                        {formatSessionAge(session.startedAt, now)}{session.preview ? ` · ${session.preview}` : ''}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          ) : kanban.loading ? (
            <CardSkeleton rows={4} />
          ) : visibleTasks.length === 0 ? (
            <CardEmptyState
              action={<Button size="inline" type="button" variant="text" onClick={focusTaskComposer}>Add a task</Button>}
              description="Your active board has no tasks waiting for work."
              title="Board clear"
            />
          ) : (
            <div>
              {visibleTasks.map(({ column, completed, task }) => (
                <KanbanTaskRow
                  bridgeAvailable={typeof bridge?.completeTask === 'function'}
                  column={column}
                  completed={completed}
                  key={`${column.name}:${task.id}`}
                  needsInputItem={task.sessionId ? waitingItemAliases.get(task.sessionId) : undefined}
                  onComplete={completeTask}
                  optimistic={optimisticTaskIds.has(task.id)}
                  task={task}
                />
              ))}
            </div>
          )}

          <div className="mt-2 border-t border-(--ui-stroke-secondary) pt-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-sm font-medium">Waiting on you</h3>
              <Badge size="xs" variant={waitingItems.length > 0 ? 'warn' : 'muted'}>
                {waitingItems.length}
              </Badge>
            </div>
            <div className="mt-2">
              {activeSessions.loading ? (
                <CardSkeleton rows={2} />
              ) : activeSessions.error ? (
                <p className="py-3 text-xs text-destructive">Could not load active sessions: {activeSessions.error}</p>
              ) : waitingItems.length === 0 ? (
                <EmptyState
                  className="min-h-0 py-6"
                  description="Hermes has no sessions waiting for a response or approval."
                  title="Nothing needs your attention"
                />
              ) : (
                waitingItems.map(item => (
                  <button
                    aria-label={`Open session ${item.title}`}
                    className="w-full cursor-pointer border-t border-(--ui-stroke-secondary) py-2.5 text-left transition-colors duration-150 first:border-t-0 hover:bg-(--chrome-action-hover)"
                    key={item.id}
                    onClick={() => host.navigate(`/${encodeURIComponent(item.id)}`)}
                    type="button"
                  >
                    <div className="text-sm font-medium">{item.title}</div>
                    <div className="mt-0.5 text-xs text-(--ui-text-tertiary)">{item.detail}</div>
                  </button>
                ))
              )}
            </div>
          </div>
        </>
      )}
    </SectionCard>
  )
}

function RecentsCard({ cron, sessions }: { cron: RpcState<unknown>; sessions: RpcState<unknown> }) {
  const now = useNow()
  const [expanded, setExpanded] = useState(false)
  const sessionRows = normalizeRecentSessions(sessions.data)
  const routineRows = normalizeCronRows(cron.data).flatMap(row => {
    if (!row.enabled || row.lastRunAt === null || TOWN_SCANNER_JOB_NAMES.has(row.name)) {
      return []
    }

    const outcome = recentRoutineOutcome(row)
    return [{
      id: row.id,
      jobName: row.name,
      kind: 'routine' as const,
      timestamp: row.lastRunAt,
      title: `${humanizeJobName(row.name)} ran${outcome ? ` — ${outcome}` : ''}`
    }]
  })
  const allRows: RecentActivity[] = [...sessionRows, ...routineRows]
    .sort((a, b) => (b.timestamp ?? 0) - (a.timestamp ?? 0))
  const rows = allRows.slice(0, expanded ? 20 : 8)
  const hasMore = allRows.length > 8

  return (
    <SectionCard title="Recents">
      {rows.length === 0 ? (
        <EmptyState className="min-h-0 py-6" title="No recent activity yet" />
      ) : (
        <div>
          {rows.map(row => row.kind === 'session' ? (
            <button
              aria-label={`Open session ${row.title}`}
              className="group -mx-2 flex w-full min-w-0 cursor-pointer items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
              data-session-id={row.id}
              key={`session:${row.id}`}
              type="button"
              onClick={openRecentSession}
            >
              <span
                className="flex size-6 shrink-0 items-center justify-center rounded-full text-(--ui-accent) leading-none"
                style={{ background: 'var(--town-tint)' }}
              >
                <Codicon name="comment-discussion" size="0.75rem" />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-(--ui-text-secondary)" title={row.title}>{row.title}</span>
              <span className="shrink-0 text-right text-xs text-(--ui-text-tertiary)">
                {row.timestamp === null ? 'Time unavailable' : relativeTime(row.timestamp, now)}
              </span>
            </button>
          ) : (
            <button
              aria-label={`Open run history for ${row.jobName}`}
              className="group -mx-2 flex w-full min-w-0 cursor-pointer items-center gap-3 rounded-lg px-2 py-2.5 text-left transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
              data-job-id={row.id}
              data-job-name={row.jobName}
              key={`routine:${row.id}`}
              type="button"
              onClick={openRunHistory}
            >
              <span
                className="flex size-6 shrink-0 items-center justify-center rounded-full text-(--ui-accent) leading-none"
                style={{ background: 'var(--town-tint)' }}
              >
                <Codicon name="clock" size="0.75rem" />
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-(--ui-text-secondary)" title={row.title}>{row.title}</span>
              <span className="shrink-0 text-right text-xs text-(--ui-text-tertiary)">{relativeTime(row.timestamp, now)}</span>
            </button>
          ))}
          {hasMore ? (
            <div className="flex justify-center border-t border-(--ui-stroke-secondary) pt-2">
              <Button
                className="text-(--ui-text-tertiary)"
                size="inline"
                type="button"
                variant="text"
                onClick={() => setExpanded(current => !current)}
              >
                {expanded ? 'Show fewer' : 'Show more'}
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </SectionCard>
  )
}

function RecentlyDismissedStrip({
  entry,
  onUndo
}: {
  entry: RecentlyDismissedEntry
  onUndo: (itemId: string) => void
}) {
  const [visible, setVisible] = useState(false)

  useEffect(() => {
    const frame = requestAnimationFrame(() => setVisible(true))
    const fadeTimer = setTimeout(
      () => setVisible(false),
      Math.max(0, entry.expiresAt - Date.now() - OPACITY_TRANSITION_MS)
    )

    return () => {
      cancelAnimationFrame(frame)
      clearTimeout(fadeTimer)
    }
  }, [entry.expiresAt])

  const headline = entry.headline.length <= 48
    ? entry.headline
    : `${entry.headline.slice(0, 47).trimEnd()}…`

  return (
    <div
      className={`flex min-w-0 items-center gap-2 border-b border-(--ui-stroke-secondary) py-2 text-xs text-(--ui-text-tertiary) transition-opacity duration-150 ${visible ? 'opacity-100' : 'opacity-0'}`}
    >
      <span className="min-w-0 flex-1 truncate">Dismissed &quot;{headline}&quot;</span>
      <span aria-hidden="true" className="shrink-0">·</span>
      <Button className="h-auto shrink-0 p-0" size="inline" type="button" variant="text" onClick={() => onUndo(entry.id)}>
        Undo
      </Button>
    </div>
  )
}

function NeedToKnowCard({ cron }: { cron: RpcState<unknown> }) {
  const { bridge, feed } = useTownFeedBridge()
  const now = useNow()
  const recentlyDismissed = useValue($recentlyDismissed)
  const bridgeRef = useRef(bridge)
  const feedRef = useRef(feed)
  const dismissalTimersRef = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  bridgeRef.current = bridge
  feedRef.current = feed

  useEffect(() => () => {
    for (const timer of dismissalTimersRef.current.values()) {
      clearTimeout(timer)
    }
    dismissalTimersRef.current.clear()
    $recentlyDismissed.set([])
  }, [])

  if (!bridge || !feed) {
    return (
      <SectionCard sideRail title="Need to Know">
        <CardEmptyState
          action={<Button size="inline" type="button" variant="text" onClick={explainTownFeedPane}>How to open it</Button>}
          description="Load the Need to Know pane once to connect its live feed here."
          title="Feed not connected"
        />
      </SectionCard>
    )
  }

  const runAction = (itemId: string, action: 'dismiss' | 'promote') => {
    const currentBridge = bridgeRef.current
    const item = feedRef.current?.items.find(candidate => candidate.id === itemId)
    if (!currentBridge || !item) {
      return
    }

    if (action === 'dismiss') {
      const now = Date.now()
      const entry = { id: item.id, headline: item.headline, expiresAt: now + RECENTLY_DISMISSED_TTL_MS, item }
      const current = $recentlyDismissed.get().filter(candidate => candidate.id !== item.id && candidate.expiresAt > now)
      const next = [...current, entry].slice(-RECENTLY_DISMISSED_LIMIT)
      const retainedIds = new Set(next.map(candidate => candidate.id))

      for (const [dismissedId, timer] of dismissalTimersRef.current) {
        if (!retainedIds.has(dismissedId)) {
          clearTimeout(timer)
          dismissalTimersRef.current.delete(dismissedId)
        }
      }

      $recentlyDismissed.set(next)
      const existingTimer = dismissalTimersRef.current.get(item.id)
      if (existingTimer) {
        clearTimeout(existingTimer)
      }
      dismissalTimersRef.current.set(item.id, setTimeout(() => {
        dismissalTimersRef.current.delete(item.id)
        const latest = $recentlyDismissed.get()
        $recentlyDismissed.set(latest.filter(candidate => candidate.id !== item.id))
      }, Math.max(0, entry.expiresAt - Date.now())))
    }

    try {
      const result = action === 'dismiss' ? currentBridge.dismiss?.(item) : currentBridge.promote?.(item)
      void Promise.resolve(result).catch(error => host.notifyError(error, 'Could not update Need-to-Know item'))
    } catch (error) {
      host.notifyError(error, 'Could not update Need-to-Know item')
    }
  }

  const undoDismiss = (itemId: string) => {
    const current = $recentlyDismissed.get()
    const entry = current.find(candidate => candidate.id === itemId)
    if (!entry) {
      return
    }

    $recentlyDismissed.set(current.filter(candidate => candidate.id !== itemId))
    const timer = dismissalTimersRef.current.get(itemId)
    if (timer) {
      clearTimeout(timer)
      dismissalTimersRef.current.delete(itemId)
    }

    const currentBridge = bridgeRef.current
    if (typeof currentBridge?.undismiss !== 'function') {
      return
    }

    try {
      void Promise.resolve(currentBridge.undismiss(entry.item)).catch(() => {})
    } catch {
      // The town-feed bridge owns restore error reporting.
    }
  }

  const openSource = (itemId: string) => {
    const item = feedRef.current?.items.find(candidate => candidate.id === itemId)
    const source = item?.source || ''
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

  const refresh = () => {
    const currentBridge = bridgeRef.current
    if (typeof currentBridge?.curate !== 'function') {
      return
    }

    void currentBridge.curate().catch(error => host.notifyError(error, 'Could not refresh Need to Know'))
  }

  const visibleItems = feed.items.slice(0, 3)
  const liveDismissals = recentlyDismissed.filter(entry => entry.expiresAt > now)
  const moreCount = Math.max(0, feed.items.length - visibleItems.length)
  const cronRows = normalizeCronRows(cron.data)

  return (
    <SectionCard
      action={(
        <div className="flex items-center gap-1 text-xs text-(--ui-text-tertiary)">
          {feed.curatedAt === null ? null : <span>{relativeTime(feed.curatedAt, now)}</span>}
          <Tip label={feed.curating ? 'Refreshing Need to Know' : 'Refresh Need to Know'}>
            <Button
              aria-label="Refresh Need to Know"
              disabled={feed.curating || feed.unavailable || typeof bridge.curate !== 'function'}
              size="icon-xs"
              type="button"
              variant="ghost"
              onClick={refresh}
            >
              <Codicon name="refresh" size="0.75rem" />
            </Button>
          </Tip>
        </div>
      )}
      persistentAction
      sideRail
      title="Need to Know"
    >
      {feed.loading ? (
        <CardSkeleton rows={3} />
      ) : feed.unavailable ? (
        <CardEmptyState
          title="Need-to-Know engine not installed"
          description="Install the town-feed gateway plugin to enable this card."
        />
      ) : feed.error ? (
        <ErrorState className="py-6" title="Could not load Need to Know" description={feed.error} />
      ) : visibleItems.length === 0 && liveDismissals.length === 0 ? (
        <CardEmptyState
          action={(
            <Button
              disabled={feed.curating || typeof bridge.curate !== 'function'}
              size="inline"
              type="button"
              variant="text"
              onClick={refresh}
            >
              {feed.curating ? 'Refreshing…' : 'Refresh now'}
            </Button>
          )}
          description="Refresh to check for new messages and session updates."
          title="You're caught up"
        />
      ) : (
        <div className="text-sm">
          {liveDismissals.map(entry => (
            <RecentlyDismissedStrip entry={entry} key={entry.id} onUndo={undoDismiss} />
          ))}
          {visibleItems.map(item => {
            const sessionId = sessionIdFromSource(item.source)
            const gmail = gmailSourceFromSource(item.source)
            const cronJobId = cronJobIdFromSource(item.source)
            const cronJob = cronJobId ? cronRows.find(row => row.id === cronJobId) : null
            const sourceReference = townFeedSourceReference(item, cronRows)
            const summary = item.summary || 'No additional summary is available.'
            const created = item.createdAt === null ? 'Time unavailable' : relativeTime(item.createdAt, now)

            return (
              <article
                className="group -mx-2 rounded-lg px-2 py-2.5 transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
                key={item.id}
              >
                <h3 className="text-sm font-medium leading-5 text-(--ui-text-primary)">{item.headline}</h3>
                <p
                  className="mt-1 text-xs leading-4 text-(--ui-text-secondary)"
                  style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' }}
                  title={summary}
                >
                  {summary}
                </p>
                {item.actionHint ? (
                  <p className="mt-1 truncate text-xs font-medium text-(--ui-accent)" title={item.actionHint}>{item.actionHint}</p>
                ) : null}
                <div className="mt-1 flex min-w-0 items-center gap-2 text-xs text-(--ui-text-tertiary)">
                  {sessionId || gmail ? (
                    <button
                      aria-label={`${gmail ? 'Open in Gmail' : 'Open session'} for ${item.headline}`}
                      className="inline-flex h-5 min-w-0 cursor-pointer items-center gap-1 rounded-full border border-(--ui-stroke-secondary) px-2 text-(--ui-text-secondary) transition-colors duration-150 hover:bg-(--chrome-action-hover) focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-(--ui-accent)"
                      onClick={() => openSource(item.id)}
                      type="button"
                    >
                      <span
                        className="flex size-4 shrink-0 items-center justify-center rounded-full text-(--ui-accent) leading-none"
                        style={{ background: 'var(--town-tint)' }}
                      >
                        <Codicon name={sourceReference.icon} size="0.6875rem" />
                      </span>
                      <span className="truncate">{sourceReference.label}</span>
                    </button>
                  ) : (
                    <span className="inline-flex h-5 min-w-0 items-center gap-1 rounded-full border border-(--ui-stroke-secondary) px-2 text-(--ui-text-secondary)">
                      <span
                        className="flex size-4 shrink-0 items-center justify-center rounded-full text-(--ui-accent) leading-none"
                        style={{ background: 'var(--town-tint)' }}
                      >
                        <Codicon name={sourceReference.icon} size="0.6875rem" />
                      </span>
                      <span className="truncate">{sourceReference.label}</span>
                    </span>
                  )}
                  <span className="shrink-0">{created}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                  <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
                    <Button
                      className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                      size="inline"
                      type="button"
                      variant="text"
                      onClick={() => runAction(item.id, 'dismiss')}
                    >
                      Dismiss
                    </Button>
                    <Button
                      className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                      size="inline"
                      type="button"
                      variant="text"
                      onClick={() => runAction(item.id, 'promote')}
                    >
                      Turn into task
                    </Button>
                  </div>
                  {gmail ? (
                    <Button
                      aria-label={`Open in Gmail for ${item.headline}`}
                      className="h-6 shrink-0 gap-1.5 px-2 text-xs"
                      size="inline"
                      type="button"
                      variant="outline"
                      onClick={() => openSource(item.id)}
                    >
                      <Codicon name="mail" size="0.75rem" />
                      <span>Open in Gmail</span>
                    </Button>
                  ) : cronJobId ? (
                    <Button
                      aria-label={`View run for ${item.headline}`}
                      className="h-6 shrink-0 gap-1.5 px-2 text-xs"
                      data-job-id={cronJobId}
                      data-job-name={cronJob?.name || sourceReference.label}
                      size="inline"
                      type="button"
                      variant="outline"
                      onClick={openRunHistory}
                    >
                      <Codicon name="clock" size="0.75rem" />
                      <span>View run</span>
                    </Button>
                  ) : null}
                </div>
              </article>
            )
          })}
          {moreCount > 0 ? (
            <div className="border-t border-(--ui-stroke-secondary) pt-3">
              <Button size="inline" type="button" variant="text" onClick={openTownFeed}>
                {moreCount} more in Need to Know
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </SectionCard>
  )
}

function dayBriefCalloutIcon(kind: string): string {
  switch (kind) {
    case 'conflict':
      return 'warning'
    case 'travel':
      return 'location'
    case 'timezone':
      return 'globe'
    case 'prep':
      return 'person'
    case 'deadline':
      return 'clock'
    case 'trip':
      return 'calendar'
    default:
      return 'info'
  }
}

function suggestionInitials(label: string): string | null {
  const match = label.trim().match(/^(\p{Lu}\p{L}*)\s+(\p{Lu}\p{L}*)\b/u)
  return match ? `${match[1][0]}${match[2][0]}`.toUpperCase() : null
}

function suggestionIcon(kind: TownSuggestionKind): string {
  if (kind === 'followup') {
    return 'person'
  }
  if (kind === 'digest') {
    return 'mail-read'
  }
  return 'tools'
}

function SuggestionsCard() {
  const { bridge, feed } = useTownFeedBridge()
  const now = useNow()
  const bridgeRef = useRef(bridge)
  const feedRef = useRef(feed)
  bridgeRef.current = bridge
  feedRef.current = feed

  const prefillComposer = (suggestionId: string) => {
    const suggestion = feedRef.current?.suggestions.find(item => item.id === suggestionId)
    if (!suggestion) {
      return
    }

    setComposerDraftAndFocus(suggestion.prompt, 'task')
  }

  const selectSuggestion = (event: { currentTarget: { dataset: { suggestionId?: string } } }) => {
    const suggestionId = event.currentTarget.dataset.suggestionId
    if (suggestionId) {
      prefillComposer(suggestionId)
    }
  }

  const suggestions = feed?.suggestions.slice(0, 5) ?? []
  const refresh = () => {
    const currentBridge = bridgeRef.current
    if (typeof currentBridge?.curate !== 'function') {
      return
    }

    void currentBridge.curate().catch(error => host.notifyError(error, 'Could not refresh suggestions'))
  }

  const suggestionsGeneratedAt = feed?.suggestionsGeneratedAt ?? null
  return (
    <SectionCard
      action={(
        <div className="flex items-center gap-1 text-xs text-(--ui-text-tertiary)">
          {suggestionsGeneratedAt === null ? null : <span>{relativeTime(suggestionsGeneratedAt, now)}</span>}
          <Tip label={feed?.curating ? 'Refreshing suggestions' : 'Refresh suggestions'}>
            <Button
              aria-label="Refresh suggestions"
              disabled={!feed || feed.curating || feed.unavailable || typeof bridge?.curate !== 'function'}
              size="icon-xs"
              type="button"
              variant="ghost"
              onClick={refresh}
            >
              <Codicon name="refresh" size="0.75rem" />
            </Button>
          </Tip>
        </div>
      )}
      persistentAction
      sideRail
      title="Suggestions"
    >
      {!bridge || !feed ? (
        <CardEmptyState
          action={<Button size="inline" type="button" variant="text" onClick={openTownFeed}>Open Need to Know</Button>}
          description="Load the Need to Know pane to connect curated next-step suggestions."
          title="Suggestions not connected"
        />
      ) : feed.loading ? (
        <CardSkeleton rows={2} />
      ) : feed.unavailable ? (
        <CardEmptyState
          description="Install the town-feed gateway plugin to generate curated next steps."
          title="Suggestions engine not installed"
        />
      ) : feed.error ? (
        <ErrorState className="py-6" description={feed.error} title="Could not load suggestions" />
      ) : suggestions.length === 0 ? (
        <CardEmptyState
          action={(
            <Button
              disabled={feed.curating || feed.unavailable || typeof bridge.curate !== 'function'}
              size="inline"
              type="button"
              variant="text"
              onClick={refresh}
            >
              {feed.curating ? 'Refreshing…' : 'Refresh suggestions'}
            </Button>
          )}
          description="Refresh the feed to generate a few concrete next steps."
          title="No suggestions yet"
        />
      ) : (
        <div>
          {suggestions.map(suggestion => {
            const initials = suggestionInitials(suggestion.label)

            return (
              <button
                aria-label={`Prefill composer with ${suggestion.label}`}
                className="group -mx-2 flex w-full cursor-pointer items-start gap-3 rounded-lg px-2 py-2.5 text-left transition-colors duration-150 hover:bg-(--ui-row-hover-background) focus-visible:bg-(--ui-row-hover-background) focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-(--ui-accent)"
                data-suggestion-id={suggestion.id}
                key={suggestion.id}
                type="button"
                onClick={selectSuggestion}
              >
                <span
                  className={`flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-medium leading-none ${initials ? 'text-(--ui-text-secondary)' : 'text-(--ui-accent)'}`}
                  style={{ background: 'var(--town-tint)' }}
                >
                  {initials || <Codicon name={suggestionIcon(suggestion.kind)} size="0.75rem" />}
                </span>
                <span className="min-w-0 flex-1 text-sm text-(--ui-text-primary)">
                  {suggestion.label}
                </span>
                <span
                  aria-hidden="true"
                  className="flex size-6 shrink-0 items-center justify-center rounded-md text-(--ui-text-tertiary) opacity-0 transition-all duration-150 group-hover:bg-(--ui-control-hover-background) group-hover:opacity-100 group-focus-visible:opacity-100"
                  title="Prefill"
                >
                  <Codicon name="edit" size="0.75rem" />
                </span>
              </button>
            )
          })}
        </div>
      )}
      <div className="mt-3 flex flex-wrap gap-2 border-t border-(--ui-stroke-secondary) pt-3">
        <Button
          className="h-6 gap-1.5 rounded-full px-2 text-xs transition-colors duration-150"
          size="inline"
          type="button"
          variant="outline"
          onClick={prefillTodayBriefComposer}
        >
          <Codicon name="comment-discussion" size="0.75rem" />
          <span>Brief me on today</span>
        </Button>
        <Button
          className="h-6 gap-1.5 rounded-full px-2 text-xs transition-colors duration-150"
          size="inline"
          type="button"
          variant="outline"
          onClick={planTask}
        >
          <Codicon name="edit" size="0.75rem" />
          <span>Plan a task</span>
        </Button>
        <Button
          className="h-6 gap-1.5 rounded-full px-2 text-xs transition-colors duration-150"
          size="inline"
          type="button"
          variant="outline"
          onClick={openNewRoutineDialog}
        >
          <Codicon name="clock" size="0.75rem" />
          <span>New routine</span>
        </Button>
        <Button
          className="h-6 gap-1.5 rounded-full px-2 text-xs transition-colors duration-150"
          disabled={feed?.curating === true}
          size="inline"
          type="button"
          variant="outline"
          onClick={refresh}
        >
          <Codicon name="refresh" size="0.75rem" />
          <span>Refresh feed</span>
        </Button>
      </div>
    </SectionCard>
  )
}

function RoutinesCard({ cron }: { cron: RpcState<unknown> }) {
  const rows = normalizeCronRows(cron.data)
  const cronError = cron.error || payloadError(cron.data)
  const cronBridge = useCronBridgeCapabilities()
  const now = useNow()
  const [runningIds, setRunningIds] = useState<Set<string>>(() => new Set())
  const rowsRef = useRef(rows)
  const runningRef = useRef(new Set<string>())
  rowsRef.current = rows

  const runRoutine = async (event: { currentTarget: { dataset: { jobId?: string } } }) => {
    const id = event.currentTarget.dataset.jobId
    if (!id) {
      return
    }
    const current = rowsRef.current.find(row => row.id === id)
    if (!current || runningRef.current.has(id)) {
      return
    }

    runningRef.current.add(id)
    setRunningIds(new Set(runningRef.current))
    try {
      const bridge = windowTownFeedBridge()
      let triggerError: unknown = null
      if (typeof bridge?.triggerCron === 'function') {
        try {
          const result = await bridge.triggerCron(current.id)
          if (!result.ok) {
            throw new Error(`Cron run finished with status ${result.status}.`)
          }
          host.notify({ kind: 'success', message: `Completed ${current.name}` })
          return
        } catch (error) {
          triggerError = error
        }
      }

      let prompt = current.prompt
      if (!prompt.trim()) {
        if (typeof bridge?.getCronPrompt !== 'function') {
          if (triggerError) {
            throw triggerError
          }
          throw new Error('The full routine prompt is unavailable.')
        }
        prompt = await bridge.getCronPrompt(current.id)
      }

      const { storedSessionId } = await startSessionWithPrompt(`Run now: ${current.name}`, prompt)
      host.notify({
        kind: 'success',
        message: storedSessionId
          ? `Started ${current.name} — see session ${storedSessionId}`
          : `Started ${current.name}`
      })
    } catch (error) {
      host.notifyError(error, `Could not run ${current.name}`)
    } finally {
      runningRef.current.delete(id)
      setRunningIds(new Set(runningRef.current))
    }
  }

  return (
    <SectionCard sideRail title="Routines">
      {cron.loading ? (
        <CardSkeleton rows={3} />
      ) : cronError ? (
        <ErrorState className="py-6" description={cronError} title="Could not load routines" />
      ) : rows.length === 0 ? (
        <CardEmptyState
          action={<Button size="inline" type="button" variant="text" onClick={openNewRoutineDialog}>New routine</Button>}
          description="Create recurring work to see its schedule and latest outcome here."
          title="No routines yet"
        />
      ) : (
        <div>
          {rows.map(row => {
            const outcome = routineOutcome(row, now)
            const nextRun = row.nextRunAt === null ? (row.enabled ? 'Next run unavailable' : 'Paused') : relativeTime(row.nextRunAt, now)
            const description = routineDescription(row.promptPreview.replace(/\.\.\.$/, ''))

            return (
              <div
                className="group -mx-2 rounded-lg px-2 py-2.5 transition-colors duration-150 hover:bg-(--ui-row-hover-background)"
                key={row.id}
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0 text-sm font-medium">{humanizeJobName(row.name)}</div>
                  <div className="flex shrink-0 items-center gap-2">
                    <div
                      className="text-xs text-(--ui-text-tertiary)"
                      title={row.nextRunAt === null ? undefined : formatDayTime(row.nextRunAt)}
                    >
                      {nextRun}
                    </div>
                    {cronBridge.triggerCron || row.prompt.trim() || cronBridge.cronPrompt ? (
                      <Tip label={`Run ${row.name} now`}>
                        <Button
                          aria-label={`Run ${row.name} now`}
                          className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                          data-job-id={row.id}
                          disabled={runningIds.has(row.id)}
                          size="icon-xs"
                          type="button"
                          variant="ghost"
                          onClick={runRoutine}
                        >
                          <Codicon name="triangle-right" size="0.75rem" />
                        </Button>
                      </Tip>
                    ) : null}
                  </div>
                </div>
                <div className="mt-1 text-xs text-(--ui-text-tertiary)" title={row.schedule}>
                  {describeCronSchedule(row.schedule)}
                </div>
                {description ? (
                  <div className="mt-1 truncate text-xs text-(--ui-text-secondary)" title={description}>
                    {description}
                  </div>
                ) : null}
                <Button
                  className="mt-1.5 h-auto min-w-0 justify-start gap-1.5 p-0 text-xs text-(--ui-text-secondary) opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                  data-job-id={row.id}
                  data-job-name={row.name}
                  size="inline"
                  title={`Open run history: ${outcome.label}`}
                  type="button"
                  variant="text"
                  onClick={openRunHistory}
                >
                  <StatusDot tone={outcome.tone} />
                  <span className="min-w-0 truncate">
                    {outcome.label}
                  </span>
                </Button>
              </div>
            )
          })}
        </div>
      )}
      {rows.length > 0 ? (
        <Button className="mt-2" size="inline" type="button" variant="text" onClick={openNewRoutineDialog}>
          + New routine
        </Button>
      ) : null}
    </SectionCard>
  )
}

function BriefingCard({ cron }: { cron: RpcState<unknown> }) {
  const now = useNow()
  const cronError = cron.error || payloadError(cron.data)
  const briefing = normalizeCronRows(cron.data).find(row => /brief|briefing|morning/i.test(row.name)) ?? null
  const cronBridge = useCronBridgeCapabilities()
  const [latestRun, setLatestRun] = useState<CronRun | null>(null)
  const [runError, setRunError] = useState<string | null>(null)
  const [loadingRun, setLoadingRun] = useState(false)
  const requestIdRef = useRef(0)

  useEffect(() => {
    const requestId = ++requestIdRef.current
    if (!briefing) {
      setLatestRun(null)
      setRunError(null)
      setLoadingRun(false)
      return
    }
    if (!cronBridge.cronRuns) {
      setLatestRun(null)
      setRunError('Saved output is unavailable until Town Feed is loaded.')
      setLoadingRun(false)
      return
    }

    setLoadingRun(true)
    setRunError(null)
    const bridge = windowTownFeedBridge()
    if (typeof bridge?.getCronRuns !== 'function') {
      setLoadingRun(false)
      return
    }

    void bridge.getCronRuns(briefing.id).then(result => {
      if (requestId === requestIdRef.current) {
        setLatestRun(result.runs[0] ?? null)
      }
    }).catch(error => {
      if (requestId === requestIdRef.current) {
        setLatestRun(null)
        setRunError(errorMessage(error))
      }
    }).finally(() => {
      if (requestId === requestIdRef.current) {
        setLoadingRun(false)
      }
    })
  }, [briefing?.id, cronBridge.cronRuns])

  const fallbackOutcome = briefing ? routineOutcome(briefing, now) : null
  const status = latestRun?.status || briefing?.lastStatus || 'unknown'
  const timestamp = latestRun?.startedAt ?? briefing?.lastRunAt ?? null
  const output = (latestRun?.output || latestRun?.error || '').replace(/\s+/g, ' ').trim()
  const outputPreview = output.length > 200 ? `${output.slice(0, 199).trimEnd()}…` : output
  const clipboardAvailable = clipboardWriteAvailable()

  return (
    <SectionCard title="Briefing">
      {cron.loading ? (
        <CardSkeleton rows={2} />
      ) : cronError ? (
        <ErrorState className="py-6" description={cronError} title="Could not check briefing routines" />
      ) : briefing ? (
        loadingRun ? (
          <CardSkeleton rows={2} />
        ) : (
          <Button
            className="h-auto w-full flex-col items-start gap-2 whitespace-normal p-3 text-left"
            data-job-id={briefing.id}
            data-job-name={briefing.name}
            type="button"
            variant="ghost"
            onClick={openRunHistory}
          >
            <span className="flex items-center gap-2 text-xs text-(--ui-text-secondary)">
              <StatusDot tone={latestRun ? runTone(latestRun.status) : fallbackOutcome?.tone || 'muted'} />
              <span>{timestamp === null ? 'Not run yet' : relativeTime(timestamp, now)}</span>
              <span className="text-(--ui-text-tertiary)">{status}</span>
            </span>
            <span className="line-clamp-3 text-xs leading-5 text-(--ui-text-secondary)">
              {outputPreview || runError || 'No output was saved for the latest run.'}
            </span>
          </Button>
        )
      ) : (
        <CardEmptyState
          action={(
            <div className="flex flex-col items-center gap-2">
              <div className="flex flex-wrap justify-center gap-2">
                {clipboardAvailable ? (
                  <Button size="xs" type="button" variant="outline" onClick={() => void copyMorningBriefCommand()}>
                    Copy command
                  </Button>
                ) : null}
                <Button size="xs" type="button" variant="outline" onClick={prefillMorningBriefComposer}>
                  Prefill composer
                </Button>
              </div>
              {!clipboardAvailable ? (
                <code className="max-w-full select-all break-all rounded border border-(--ui-stroke-secondary) px-2 py-1 text-xs text-(--ui-text-secondary)">
                  {MORNING_BRIEF_COMMAND}
                </code>
              ) : null}
            </div>
          )}
          description="Use Hermes's native morning-brief blueprint to deliver a daily summary here."
          title="No briefing routine yet"
        />
      )}
    </SectionCard>
  )
}

const CARD_LABELS: Record<CardId, string> = {
  tasks: 'Tasks',
  recents: 'Recents',
  'need-to-know': 'Need to Know',
  suggestions: 'Suggestions',
  routines: 'Routines',
  briefing: 'Briefing'
}

function cardIdFromEvent(event: { currentTarget: { dataset: { cardId?: string } } }): CardId | null {
  const cardId = event.currentTarget.dataset.cardId
  return isCardId(cardId) ? cardId : null
}

function toggleCardVisibility(event: { currentTarget: { dataset: { cardId?: string } } }): void {
  const cardId = cardIdFromEvent(event)
  if (!cardId) {
    return
  }

  updateTownHomePreferences(current => ({
    ...current,
    cardVisibility: { ...current.cardVisibility, [cardId]: !current.cardVisibility[cardId] }
  }))
}

function moveCard(event: { currentTarget: { dataset: { cardId?: string; direction?: string } } }): void {
  const cardId = cardIdFromEvent(event)
  const direction = event.currentTarget.dataset.direction === 'up'
    ? -1
    : event.currentTarget.dataset.direction === 'down'
      ? 1
      : 0
  if (!cardId || direction === 0) {
    return
  }

  updateTownHomePreferences(current => {
    const currentIndex = current.cardOrder.indexOf(cardId)
    const nextIndex = currentIndex + direction
    if (currentIndex < 0 || nextIndex < 0 || nextIndex >= current.cardOrder.length) {
      return current
    }

    const cardOrder = [...current.cardOrder]
    ;[cardOrder[currentIndex], cardOrder[nextIndex]] = [cardOrder[nextIndex], cardOrder[currentIndex]]
    return { ...current, cardOrder }
  })
}

function selectDoneTaskWindow(value: string): void {
  const hours = Number(value)
  if (!isDoneTaskWindowHours(hours)) {
    return
  }

  updateTownHomePreferences(current => ({ ...current, doneTaskWindowHours: hours }))
}

function saveUserName(event: { currentTarget: { value: string } }): void {
  const userName = normalizeUserName(event.currentTarget.value)
  updateTownHomePreferences(current => ({ ...current, userName }))
}

function setCustomizeDialogOpen(open: boolean): void {
  if ($customizeDialogOpen.get() !== open) {
    $customizeDialogOpen.set(open)
  }
}

function openCustomizeDialog(): void {
  setCustomizeDialogOpen(true)
}

function closeCustomizeDialog(): void {
  setCustomizeDialogOpen(false)
}

function CustomizeDialog() {
  const open = useValue($customizeDialogOpen)
  const preferences = useValue($townHomePreferences)

  return (
    <Dialog open={open} onOpenChange={setCustomizeDialogOpen}>
      <DialogContent className="max-w-lg gap-4">
        <DialogHeader>
          <DialogTitle>Customize Town</DialogTitle>
          <DialogDescription>Choose which cards appear, arrange them, and control how long done tasks stay visible.</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <label className="text-sm font-medium" htmlFor="town-user-name">Your name — shown in the greeting</label>
          <Input
            defaultValue={preferences.userName}
            id="town-user-name"
            maxLength={40}
            placeholder="Your name"
            onBlur={saveUserName}
          />
        </div>

        <div className="space-y-1">
          {preferences.cardOrder.map((cardId, index) => (
            <div
              className="flex items-center gap-3 border-t border-(--ui-stroke-secondary) py-2.5 first:border-t-0"
              key={cardId}
            >
              <Switch
                aria-label={`${preferences.cardVisibility[cardId] ? 'Hide' : 'Show'} ${CARD_LABELS[cardId]}`}
                checked={preferences.cardVisibility[cardId]}
                data-card-id={cardId}
                onClick={toggleCardVisibility}
                size="xs"
              />
              <span className="min-w-0 flex-1 text-sm font-medium">{CARD_LABELS[cardId]}</span>
              <div className="flex shrink-0 items-center gap-1">
                <Button
                  aria-label={`Move ${CARD_LABELS[cardId]} up`}
                  data-card-id={cardId}
                  data-direction="up"
                  disabled={index === 0}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                  onClick={moveCard}
                >
                  <Codicon name="arrow-up" size="0.75rem" />
                </Button>
                <Button
                  aria-label={`Move ${CARD_LABELS[cardId]} down`}
                  data-card-id={cardId}
                  data-direction="down"
                  disabled={index === preferences.cardOrder.length - 1}
                  size="icon-xs"
                  type="button"
                  variant="ghost"
                  onClick={moveCard}
                >
                  <Codicon name="arrow-down" size="0.75rem" />
                </Button>
              </div>
            </div>
          ))}
        </div>

        <div className="flex flex-wrap items-center justify-between gap-3 border-t border-(--ui-stroke-secondary) pt-4">
          <label className="text-sm font-medium" htmlFor="town-done-task-window">Hide done tasks after</label>
          <Select value={String(preferences.doneTaskWindowHours)} onValueChange={selectDoneTaskWindow}>
            <SelectTrigger className="w-36" id="town-done-task-window">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="12">12 hours</SelectItem>
              <SelectItem value="24">24 hours</SelectItem>
              <SelectItem value="48">48 hours</SelectItem>
              <SelectItem value="168">1 week</SelectItem>
            </SelectContent>
          </Select>
        </div>

        <DialogFooter>
          <Button type="button" onClick={closeCustomizeDialog}>Done</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function TownHomeCard({
  activeSessions,
  cardId,
  cron,
  doneTaskWindowHours,
  sessions,
  waitingItems
}: {
  activeSessions: RpcState<unknown>
  cardId: CardId
  cron: RpcState<unknown>
  doneTaskWindowHours: DoneTaskWindowHours
  sessions: RpcState<unknown>
  waitingItems: AttentionItem[]
}) {
  switch (cardId) {
    case 'tasks':
      return (
        <TasksCard
          activeSessions={activeSessions}
          doneTaskWindowHours={doneTaskWindowHours}
          sessions={sessions}
          waitingItems={waitingItems}
        />
      )
    case 'recents':
      return <RecentsCard cron={cron} sessions={sessions} />
    case 'need-to-know':
      return <NeedToKnowCard cron={cron} />
    case 'suggestions':
      return <SuggestionsCard />
    case 'routines':
      return <RoutinesCard cron={cron} />
    case 'briefing':
      return <BriefingCard cron={cron} />
  }
}

function TownHome() {
  const nowTimestamp = useNow()
  const gateway = useValue(host.state.gateway)
  const model = useValue(host.state.model)
  const profile = useValue(host.state.profile)
  const activeSessionId = useValue(host.state.activeSessionId)
  const preferences = useValue($townHomePreferences)
  const sessions = useRpc<unknown>('session.list', SESSION_LIST_PARAMS)
  const activeSessions = useRpc<unknown>('session.active_list', ACTIVE_SESSION_PARAMS)
  const cron = useRpc<unknown>('cron.manage', CRON_LIST_PARAMS)
  const { bridge: feedBridge, feed } = useTownFeedBridge()
  const markers = useAttentionMarkers(activeSessionId)
  const sessionRows = normalizeSessions(sessions.data)
  const activeRows = normalizeActiveSessions(activeSessions.data)
  const waitingItems = attentionItems(activeRows, sessionRows, markers, activeSessionId)
  const now = new Date(nowTimestamp)
  const hour = now.getHours()
  const greetingBase = hour < 12
    ? now.getDay() === 1 ? 'Happy Monday' : 'Good morning'
    : hour < 18 ? 'Good afternoon' : 'Good evening'
  const greeting = preferences.userName ? `${greetingBase}, ${preferences.userName}.` : `${greetingBase}.`
  const today = new Intl.DateTimeFormat(undefined, { day: 'numeric', month: 'long', weekday: 'long' }).format(now)
  const cronAvailable = !cron.error && !payloadError(cron.data)
  const nextRoutine = cronAvailable
    ? normalizeCronRows(cron.data)
        .filter(row => row.enabled && row.state !== 'paused' && row.nextRunAt !== null && row.nextRunAt >= now.getTime())
        .sort((a, b) => (a.nextRunAt ?? Number.POSITIVE_INFINITY) - (b.nextRunAt ?? Number.POSITIVE_INFINITY))[0]
    : undefined
  const dayBrief = feed?.dayBrief ?? null
  const contextLine = dayBrief?.daySummary
    ? dayBrief.daySummary
    : feed?.nextEvent
      ? `Next: ${feed.nextEvent.summary} · ${relativeTime(feed.nextEvent.startAt, nowTimestamp)}`
      : waitingItems.length > 0
        ? `${waitingItems.length} ${waitingItems.length === 1 ? 'item' : 'items'} waiting on you`
        : nextRoutine?.nextRunAt !== null && nextRoutine?.nextRunAt !== undefined
          ? `Next routine: ${nextRoutine.name} · ${relativeTime(nextRoutine.nextRunAt, nowTimestamp)}`
          : null
  const accentColor = profileColor(profile)
  const gatewayTone = gateway === 'open' ? 'good' : gateway === 'connecting' ? 'warn' : 'muted'
  const gatewayLabel = gateway === 'open' ? 'Gateway online' : `Gateway ${gateway || 'idle'}`
  const schedule = feedBridge && feed ? feed.schedule.filter(event => event.endAt >= now.getTime()) : []
  const [boardWide, attachBoard] = useBoardWide()

  return (
    <ScrollArea className="h-full">
      <div
        className="mx-auto w-full"
        ref={attachBoard}
        style={{
          maxWidth: 1280,
          padding: 32,
          ['--town-tint']: 'color-mix(in srgb, var(--ui-accent) 10%, transparent)',
          ['--town-surface']: 'color-mix(in srgb, var(--ui-text-primary) 4%, transparent)'
        } as React.CSSProperties}
      >
        <div style={{ display: 'flex', flexDirection: boardWide ? 'row' : 'column', gap: 32, alignItems: boardWide ? 'flex-start' : 'stretch' }}>
          <main className="min-w-0" style={{ display: 'flex', flex: '1 1 0%', flexDirection: 'column', gap: 24 }}>
            <header className="group flex flex-col gap-4 border-b border-(--ui-stroke-secondary) pb-5 sm:flex-row sm:items-end sm:justify-between">
              <div className="min-w-0">
                <span
                  className="mb-3 block h-1 w-12 rounded-full bg-(--ui-stroke-secondary)"
                  style={accentColor ? { backgroundColor: accentColor } : undefined}
                />
                <div className="mb-2 flex items-center gap-2 text-sm text-(--ui-text-secondary)">
                  <p>{today}</p>
                  {dayBrief?.trip ? (
                    <Badge size="xs" variant="muted">
                      {dayBrief.trip.label}
                      {dayBrief.trip.dayN !== null
                        ? ` · day ${dayBrief.trip.dayN}${dayBrief.trip.totalDays !== null ? ` of ${dayBrief.trip.totalDays}` : ''}`
                        : ''}
                    </Badge>
                  ) : null}
                  {schedule.length > 0 ? (
                    <Button
                      className="h-6 gap-1.5 rounded-full px-2 text-xs transition-colors duration-150"
                      size="inline"
                      type="button"
                      variant="text"
                      onClick={openScheduleDialog}
                    >
                      <Codicon name="calendar" size="0.75rem" />
                      <span>View schedule</span>
                    </Button>
                  ) : null}
                </div>
                <h1
                  className="text-3xl font-medium tracking-tight"
                  style={{ fontFamily: 'ui-serif, "Iowan Old Style", Georgia, serif' }}
                >
                  {greeting}
                </h1>
                {contextLine ? <p className="mt-2 truncate text-sm text-(--ui-text-tertiary)" title={contextLine}>{contextLine}</p> : null}
                {dayBrief && dayBrief.callouts.length > 0 ? (
                  <div className="mt-3 flex flex-col gap-1.5">
                    {dayBrief.callouts.slice(0, 3).map((callout, index) => (
                      <button
                        className="flex min-w-0 cursor-pointer items-start gap-2 text-left text-xs text-(--ui-text-secondary) transition-colors duration-150 hover:text-(--ui-text-primary)"
                        key={`${callout.kind}-${index}`}
                        type="button"
                        onClick={openScheduleDialog}
                      >
                        <span className="mt-0.5 flex size-4 shrink-0 items-center justify-center rounded-full text-(--ui-accent)" style={{ background: 'var(--town-tint)' }}>
                          <Codicon name={dayBriefCalloutIcon(callout.kind)} size="0.6875rem" />
                        </span>
                        <span className="min-w-0">{callout.text}</span>
                      </button>
                    ))}
                  </div>
                ) : null}
              </div>
              <div className="flex min-w-0 flex-wrap items-center gap-2">
                <div className="inline-flex items-center gap-2 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-xs text-(--ui-text-secondary)">
                  <StatusDot tone={gatewayTone} />
                  <span>{gatewayLabel}</span>
                </div>
                <div
                  className="inline-flex min-w-0 items-center gap-2 rounded-md border border-(--ui-stroke-secondary) px-2.5 py-1.5 text-xs text-(--ui-text-secondary)"
                  title={model || 'No model selected'}
                >
                  <span className="text-(--ui-text-tertiary)">Model</span>
                  <span className="max-w-52 truncate font-medium text-(--ui-text-primary)">{model || 'Not selected'}</span>
                </div>
                <Tip label="Customize Town">
                  <Button
                    aria-label="Customize Town"
                    className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                    size="icon-sm"
                    type="button"
                    variant="ghost"
                    onClick={openCustomizeDialog}
                  >
                    <Codicon name="gear" size="0.875rem" />
                  </Button>
                </Tip>
              </div>
            </header>

            <TaskComposer />

            {preferences.cardOrder
              .filter(cardId => preferences.cardVisibility[cardId] && MAIN_CARD_IDS.has(cardId))
              .map(cardId => (
                <TownHomeCard
                  activeSessions={activeSessions}
                  cardId={cardId}
                  cron={cron}
                  doneTaskWindowHours={preferences.doneTaskWindowHours}
                  key={cardId}
                  sessions={sessions}
                  waitingItems={waitingItems}
                />
              ))}
          </main>

          <aside style={{ display: 'flex', flexDirection: 'column', flexShrink: 0, gap: 20, width: boardWide ? 384 : '100%' }}>
            {preferences.cardOrder
              .filter(cardId => preferences.cardVisibility[cardId] && SIDE_CARD_IDS.has(cardId))
              .map(cardId => (
                <TownHomeCard
                  activeSessions={activeSessions}
                  cardId={cardId}
                  cron={cron}
                  doneTaskWindowHours={preferences.doneTaskWindowHours}
                  key={cardId}
                  sessions={sessions}
                  waitingItems={waitingItems}
                />
              ))}
          </aside>
        </div>
        <CustomizeDialog />
        <NewRoutineDialog />
        <RunHistoryDialog />
        <ScheduleDialog schedule={feed?.schedule ?? []} />
      </div>
    </ScrollArea>
  )
}

export default {
  id: 'town-home',
  name: 'Town Home',
  register(ctx: PluginContext) {
    pluginContext = ctx
    try {
      $townHomePreferences.set(normalizeTownHomePreferences(ctx.storage.get(TOWN_HOME_PREFERENCES_KEY, defaultTownHomePreferences())))
    } catch {
      $townHomePreferences.set(defaultTownHomePreferences())
    }

    ctx.registerMany([
      {
        id: 'page',
        area: ROUTES_AREA,
        data: { path: '/town' },
        render: () => <TownHome />
      },
      {
        id: 'nav',
        area: SIDEBAR_NAV_AREA,
        data: { path: '/town', label: 'Town', codicon: 'home' }
      },
      {
        id: 'open',
        area: KEYBINDS_AREA,
        data: {
          id: 'town-home.open',
          category: 'navigation',
          defaults: ['mod+shift+h'],
          label: 'Open Town home',
          run: openTownHome
        } satisfies KeybindContribution
      },
      {
        id: 'go-home',
        area: PALETTE_AREA,
        data: {
          id: 'town-home.go-home',
          label: 'Go to Town home',
          action: 'town-home.open',
          keywords: ['town', 'home', 'dashboard'],
          run: openTownHome
        } satisfies PaletteContribution
      }
    ])
  }
}
