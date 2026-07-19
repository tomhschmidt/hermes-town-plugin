import {
  atom,
  Button,
  cn,
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
  PALETTE_AREA,
  relativeTime,
  ScrollArea,
  Skeleton,
  StatusDot,
  Switch,
  Textarea,
  Tip,
  useValue,
  type PluginContext
} from '@hermes/plugin-sdk'
import { useEffect, useRef, useState } from 'react'

const MIN_POLL_INTERVAL_MS = 15_000
const EVENT_REFRESH_DEBOUNCE_MS = 2_000
const CRON_LIST_PARAMS = { action: 'list' }
const NEW_ROUTINE_PROMPT = 'Create a routine that '
const $newRoutineDialogOpen = atom(false)
const $runHistoryDialogOpen = atom(false)

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

type UnknownRecord = Record<string, unknown>

interface RpcState<T> {
  data: T | null
  error: string | null
  loading: boolean
}

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

interface TownFeedBridge {
  getCronPrompt?: (jobId: string) => Promise<string>
  getCronRuns?: (jobId: string) => Promise<CronRunsResult>
  triggerCron?: (jobId: string) => Promise<CronTriggerResult>
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

// Keep this formatter in sync with its twin in town-home; desktop bundles cannot import each other.
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

function windowTownFeedBridge(): TownFeedBridge | null {
  const candidate = asRecord((window as Window & { __hermesTownFeed?: unknown }).__hermesTownFeed)
  return candidate ? (candidate as TownFeedBridge) : null
}

function bridgeCapabilities(): { cronPrompt: boolean; cronRuns: boolean; triggerCron: boolean } {
  const bridge = windowTownFeedBridge()
  return {
    cronPrompt: typeof bridge?.getCronPrompt === 'function',
    cronRuns: typeof bridge?.getCronRuns === 'function',
    triggerCron: typeof bridge?.triggerCron === 'function'
  }
}

function useTownFeedBridgeCapabilities(): ReturnType<typeof bridgeCapabilities> {
  const [available, setAvailable] = useState(bridgeCapabilities)

  useEffect(() => {
    const refresh = () => setAvailable(bridgeCapabilities())
    refresh()
    window.addEventListener('hermes-town-feed-ready', refresh)
    return () => window.removeEventListener('hermes-town-feed-ready', refresh)
  }, [])

  return available
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error || 'Unknown gateway error')
}

function payloadError(value: unknown): string | null {
  const record = asRecord(value)
  return record?.success === false ? stringValue(record.error) || 'The gateway rejected the request.' : null
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

function formatDayTime(timestamp: number): string {
  const formatter: unknown = fmtDayTime
  const format = asRecord(formatter)?.format

  if (typeof format === 'function') {
    return String(format.call(formatter, new Date(timestamp)))
  }

  return new Date(timestamp).toLocaleString()
}

function routineOutcome(row: CronRow): { label: string; tone: 'bad' | 'good' | 'muted' | 'warn' } {
  if (!row.enabled || row.state === 'paused') {
    return { label: 'Paused', tone: 'warn' }
  }

  if (row.lastStatus === 'error' || row.lastError || row.lastDeliveryError) {
    return { label: row.lastError || row.lastDeliveryError || 'Last run failed', tone: 'bad' }
  }

  if (row.lastRunAt !== null) {
    return {
      label: row.lastStatus === 'ok' ? `Completed ${relativeTime(row.lastRunAt)}` : `Last ran ${relativeTime(row.lastRunAt)}`,
      tone: row.lastStatus === 'ok' ? 'good' : 'muted'
    }
  }

  return { label: 'Not run yet', tone: 'muted' }
}

function CardSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="space-y-3 p-3" aria-label="Loading">
      {Array.from({ length: rows }, (_, index) => (
        <div className="space-y-2" key={index}>
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-3 w-1/3" />
        </div>
      ))}
    </div>
  )
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

function runTone(status: string): 'bad' | 'good' | 'muted' {
  if (status === 'ok') {
    return 'good'
  }
  if (status === 'error' || status === 'failed') {
    return 'bad'
  }
  return 'muted'
}

function RunHistoryDialog() {
  const open = useValue($runHistoryDialogOpen)
  const target = useValue($runHistoryTarget)
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
                  <span>{relativeTime(run.startedAt)}</span>
                  <span className="text-(--ui-text-tertiary)">{run.status === 'unknown' ? '—' : run.status}</span>
                </div>
                <ScrollArea className="max-h-40 rounded border border-(--ui-stroke-secondary)">
                  <pre className="whitespace-pre-wrap break-words p-3 font-mono text-xs text-(--ui-text-secondary)">
                    {run.output || run.error || 'No output was saved.'}
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

function RoutinesPane() {
  const cron = useRpc<unknown>('cron.manage', CRON_LIST_PARAMS)
  const townFeedBridge = useTownFeedBridgeCapabilities()
  const [rows, setRows] = useState<CronRow[]>([])
  const [pendingIds, setPendingIds] = useState<Set<string>>(() => new Set())
  const [runningIds, setRunningIds] = useState<Set<string>>(() => new Set())
  const rowsRef = useRef(rows)
  const pendingRef = useRef(new Set<string>())
  const runningRef = useRef(new Set<string>())
  rowsRef.current = rows

  useEffect(() => {
    if (cron.data !== null && !payloadError(cron.data)) {
      const nextRows = normalizeCronRows(cron.data)
      rowsRef.current = nextRows
      setRows(nextRows)
    }
  }, [cron.data])

  const toggleRoutine = async (id: string, checked: boolean) => {
    const current = rowsRef.current.find(row => row.id === id)
    if (!current || pendingRef.current.has(id)) {
      return
    }

    const previous = current
    const optimistic = rowsRef.current.map(row =>
      row.id === id ? { ...row, enabled: checked, state: checked ? 'scheduled' : 'paused' } : row
    )
    pendingRef.current.add(id)
    rowsRef.current = optimistic
    setRows(optimistic)
    setPendingIds(new Set(pendingRef.current))

    try {
      const result = await host.request<unknown>('cron.manage', {
        action: checked ? 'resume' : 'pause',
        name: current.id
      })
      const actionError = payloadError(result)
      if (actionError) {
        throw new Error(actionError)
      }

      const refreshed = await host.request<unknown>('cron.manage', CRON_LIST_PARAMS)
      const refreshError = payloadError(refreshed)
      if (refreshError) {
        throw new Error(refreshError)
      }

      const refreshedRows = normalizeCronRows(refreshed)
      rowsRef.current = refreshedRows
      setRows(refreshedRows)
    } catch (error) {
      const reverted = rowsRef.current.map(row => (row.id === id ? previous : row))
      rowsRef.current = reverted
      setRows(reverted)
      host.notifyError(error, checked ? 'Could not resume routine' : 'Could not pause routine')
    } finally {
      pendingRef.current.delete(id)
      setPendingIds(new Set(pendingRef.current))
    }
  }

  const runRoutine = async (id: string) => {
    const current = rowsRef.current.find(row => row.id === id)
    if (!current || runningRef.current.has(id)) {
      return
    }

    runningRef.current.add(id)
    setRunningIds(new Set(runningRef.current))
    try {
      const bridge = windowTownFeedBridge()
      if (typeof bridge?.triggerCron === 'function') {
        const result = await bridge.triggerCron(current.id)
        if (!result.ok) {
          throw new Error(`Cron run finished with status ${result.status}.`)
        }
        host.notify({ kind: 'success', message: `Completed ${current.name}` })
        const refreshed = await host.request<unknown>('cron.manage', CRON_LIST_PARAMS)
        if (!payloadError(refreshed)) {
          const refreshedRows = normalizeCronRows(refreshed)
          rowsRef.current = refreshedRows
          setRows(refreshedRows)
        }
        return
      }

      let prompt = current.prompt
      if (!prompt.trim()) {
        if (typeof bridge?.getCronPrompt !== 'function') {
          throw new Error('The full routine prompt is unavailable.')
        }
        prompt = await bridge.getCronPrompt(current.id)
      }

      const created = await host.request<unknown>('session.create', { title: `Run now: ${current.name}` })
      const createdRecord = asRecord(created)
      const sessionId = stringValue(createdRecord?.session_id)
      if (!sessionId) {
        throw new Error('The gateway did not return a session ID.')
      }

      await host.request('prompt.submit', { session_id: sessionId, text: prompt })
      const storedSessionId = stringValue(createdRecord?.stored_session_id)
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

  const openRunHistory = (event: { currentTarget: HTMLButtonElement }) => {
    const id = event.currentTarget.dataset.jobId || ''
    const current = rowsRef.current.find(row => row.id === id)
    if (current) {
      $runHistoryTarget.set({ id: current.id, name: current.name })
      $runHistoryDialogOpen.set(true)
    }
  }

  const runRoutineFromEvent = (event: { currentTarget: HTMLButtonElement }) => {
    const id = event.currentTarget.dataset.jobId || ''
    if (id) {
      void runRoutine(id)
    }
  }

  const toggleRoutineFromEvent = (event: { currentTarget: HTMLButtonElement }) => {
    const id = event.currentTarget.dataset.jobId || ''
    const current = rowsRef.current.find(row => row.id === id)
    if (current) {
      void toggleRoutine(id, !(current.enabled && current.state !== 'paused'))
    }
  }

  const cronError = cron.error || payloadError(cron.data)

  return (
    <div className="flex h-full min-h-0 flex-col text-sm">
      <div className="border-b border-(--ui-stroke-secondary) px-3 py-2.5 font-medium">Routines</div>
      <ScrollArea className="min-h-0 flex-1">
        {cron.loading ? (
          <CardSkeleton rows={4} />
        ) : cronError ? (
          <ErrorState className="min-h-48 px-3" description={cronError} title="Could not load routines" />
        ) : rows.length === 0 ? (
          <EmptyState className="min-h-48 px-3" title="No routines yet" description="Create a routine to see its schedule here." />
        ) : (
          <div className="px-3">
            {rows.map(row => {
              const outcome = routineOutcome(row)
              const nextRun =
                row.nextRunAt === null ? (row.enabled ? 'Next run unavailable' : 'Paused') : relativeTime(row.nextRunAt)
              const checked = row.enabled && row.state !== 'paused'
              const description = routineDescription(row.promptPreview.replace(/\.\.\.$/, ''))

              return (
                <div
                  className="group border-t border-(--ui-stroke-secondary) py-3 transition-colors duration-150 first:border-t-0 hover:bg-(--chrome-action-hover)"
                  key={row.id}
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 font-medium" title={row.name}>
                      {row.name}
                    </div>
                    <div className="flex shrink-0 items-center gap-1">
                      {townFeedBridge.triggerCron || row.prompt.trim() || townFeedBridge.cronPrompt ? (
                        <Tip label={`Run ${row.name} now`}>
                          <Button
                            aria-label={`Run ${row.name} now`}
                            className="opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                            data-job-id={row.id}
                            disabled={runningIds.has(row.id)}
                            size="icon-xs"
                            type="button"
                            variant="ghost"
                            onClick={runRoutineFromEvent}
                          >
                            <Codicon name="triangle-right" size="0.75rem" />
                          </Button>
                        </Tip>
                      ) : null}
                      <Switch
                        aria-label={`${checked ? 'Pause' : 'Resume'} ${row.name}`}
                        checked={checked}
                        data-job-id={row.id}
                        disabled={pendingIds.has(row.id)}
                        onClick={toggleRoutineFromEvent}
                        size="xs"
                      />
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
                  <div className="mt-1 flex items-center justify-between gap-3 text-xs">
                    <div className="flex min-w-0 items-center gap-1.5 text-(--ui-text-secondary)">
                      <StatusDot tone={outcome.tone} />
                      {row.lastRunAt !== null && townFeedBridge.cronRuns ? (
                        <Button
                          className="min-w-0 truncate opacity-0 transition-opacity duration-150 group-hover:opacity-100 focus-visible:opacity-100"
                          data-job-id={row.id}
                          size="inline"
                          title={outcome.label}
                          type="button"
                          variant="text"
                          onClick={openRunHistory}
                        >
                          {outcome.label}
                        </Button>
                      ) : (
                        <span className="min-w-0 truncate" title={outcome.label}>{outcome.label}</span>
                      )}
                    </div>
                    <span
                      className="shrink-0 text-(--ui-text-tertiary)"
                      title={row.nextRunAt === null ? undefined : formatDayTime(row.nextRunAt)}
                    >
                      {nextRun}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </ScrollArea>
      <RunHistoryDialog />
      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-(--ui-stroke-secondary) px-3 py-2">
        <Button size="inline" type="button" variant="text" onClick={() => $newRoutineDialogOpen.set(true)}>
          + New routine
        </Button>
        <Button size="inline" type="button" variant="text" onClick={() => host.navigate('/cron')}>
          Open Cron
        </Button>
      </div>
    </div>
  )
}

function NextRoutineChip() {
  const gateway = useValue(host.state.gateway)
  const cron = useRpc<unknown>('cron.manage', CRON_LIST_PARAMS, { intervalMs: 30_000 })
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const interval = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(interval)
  }, [])

  if (gateway !== 'open' || cron.error || payloadError(cron.data)) {
    return null
  }

  const next = normalizeCronRows(cron.data)
    .filter(row => row.enabled && row.state !== 'paused' && row.nextRunAt !== null)
    .sort((a, b) => (a.nextRunAt ?? Number.POSITIVE_INFINITY) - (b.nextRunAt ?? Number.POSITIVE_INFINITY))[0]

  if (!next || next.nextRunAt === null) {
    return null
  }

  return (
    <Tip label={`${next.name} - ${formatDayTime(next.nextRunAt)}`}>
      <button
        className={cn(
          'inline-flex h-full items-center gap-1 px-1.5 text-xs transition-colors',
          'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
        )}
        onClick={() => host.navigate('/town')}
        type="button"
      >
        next: {relativeTime(next.nextRunAt, now)}
      </button>
    </Tip>
  )
}

export default {
  id: 'town-routines',
  name: 'Town Routines',
  register(ctx: PluginContext) {
    ctx.registerMany([
      {
        id: 'pane',
        area: 'panes',
        title: 'routines',
        data: { placement: 'right', width: '280px' },
        render: () => <RoutinesPane />
      },
      {
        // The dialog rides the statusbar contribution (mounted in every
        // layout) so the palette command works even when the routines pane
        // isn't part of the current layout.
        id: 'next-routine',
        area: 'statusBar.right',
        order: 131,
        render: () => (
          <>
            <NextRoutineChip />
            <NewRoutineDialog />
          </>
        )
      },
      {
        id: 'new-routine',
        area: PALETTE_AREA,
        data: {
          id: 'town-routines.new-routine',
          label: 'New routine…',
          keywords: ['town', 'routine', 'cron', 'schedule'],
          run: () => {
            host.navigate('/town')
            $newRoutineDialogOpen.set(true)
          }
        }
      }
    ])
  }
}
