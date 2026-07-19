import {
  atom,
  Badge,
  Button,
  cn,
  EmptyState,
  host,
  relativeTime,
  ScrollArea,
  Tip,
  useValue,
  type PluginContext
} from '@hermes/plugin-sdk'
import { useEffect, useState } from 'react'

const HISTORY_STORAGE_KEY = 'recent-resolutions'
const MAX_PENDING_ITEMS = 20
const MAX_RECENT_ITEMS = 20
const MAX_ARGS_PREVIEW_LENGTH = 200
const SESSION_LIST_PARAMS = { limit: 100 }

type UnknownRecord = Record<string, unknown>
type ApprovalChoice = 'once' | 'deny'
type HistoryKind = 'approval' | 'clarify'

interface PluginStorage {
  get<T>(key: string, fallback: T): T
  set(key: string, value: unknown): void
  remove(key: string): void
}

interface PendingApproval {
  argsPreview: string
  description: string
  id: string
  sessionId: string
  sessionKey: string
  sessionTitle: string
  timestamp: number
  toolName: string
}

interface PendingClarify {
  id: string
  options: string[]
  question: string
  sessionId: string
  sessionKey: string
  sessionTitle: string
  timestamp: number
}

interface RecentResolution {
  choice: string
  id: string
  kind: HistoryKind
  timestamp: number
  title: string
}

interface ActiveSession {
  id: string
  sessionKey: string
  status: string
  title: string
}

interface LatestTool {
  argsPreview: string
  name: string
}

const $pendingApprovals: {
  get(): Record<string, PendingApproval>
  set(value: Record<string, PendingApproval>): void
} = atom({} as Record<string, PendingApproval>)
const $pendingClarifies: {
  get(): Record<string, PendingClarify>
  set(value: Record<string, PendingClarify>): void
} = atom({} as Record<string, PendingClarify>)
const $recentResolutions: {
  get(): RecentResolution[]
  set(value: RecentResolution[]): void
} = atom([] as RecentResolution[])
const latestTools = new Map<string, LatestTool>()
const submittingIds = new Set<string>()
let pluginStorage: PluginStorage | null = null
// Module-level so requests arriving while no pane is visible are kept.
// subscribe() is idempotent; unsubscribe() fully resets, so a transient
// unmount of the statusbar host component just re-subscribes on remount.
let eventDisposers: (() => void)[] | null = null

function subscribeApprovalEvents(): void {
  if (eventDisposers) {
    return
  }

  eventDisposers = [
    host.onEvent('tool.start', rememberLatestTool),
    host.onEvent('approval.request', setPendingApproval),
    host.onEvent('clarify.request', setPendingClarify),
    host.onEvent('message.complete', resolvePendingForSession),
    host.onEvent('error', discardPendingForSession)
  ]
}

function unsubscribeApprovalEvents(): void {
  eventDisposers?.forEach(offEvent => offEvent())
  eventDisposers = null
}

function asRecord(value: unknown): UnknownRecord | null {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as UnknownRecord) : null
}

function stringValue(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function sessionIdFrom(event: unknown): string {
  return stringValue(asRecord(event)?.session_id)
}

function truncatePreview(value: string): string {
  const compact = value.trim()

  return compact.length > MAX_ARGS_PREVIEW_LENGTH
    ? `${compact.slice(0, MAX_ARGS_PREVIEW_LENGTH - 1)}…`
    : compact
}

function stringOptions(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return []
  }

  return Array.from(
    new Set(
      value.flatMap(option => {
        const text = stringValue(option).trim()
        return text ? [text] : []
      })
    )
  )
}

function rememberLatestTool(event: unknown): void {
  const record = asRecord(event)
  const sessionId = stringValue(record?.session_id)
  const payload = asRecord(record?.payload)
  const name = stringValue(payload?.name).trim()

  if (!sessionId || !name) {
    return
  }

  latestTools.set(sessionId, {
    argsPreview: truncatePreview(stringValue(payload?.args_text) || stringValue(payload?.context)),
    name
  })
}

function setPendingApproval(event: unknown): void {
  const record = asRecord(event)
  const sessionId = stringValue(record?.session_id)
  const payload = asRecord(record?.payload)

  if (!sessionId || !payload) {
    return
  }

  const latestTool = latestTools.get(sessionId)
  const patternKey = stringValue(payload.pattern_key)
  // approval.request has no request_id, so the live runtime session id is the
  // narrowest stable key exposed by the gateway.
  const nextItem: PendingApproval = {
    argsPreview: truncatePreview(stringValue(payload.command) || latestTool?.argsPreview || ''),
    description: stringValue(payload.description) || 'Approval required',
    id: sessionId,
    sessionId,
    sessionKey: '',
    sessionTitle: '',
    timestamp: Date.now(),
    toolName: latestTool?.name || (patternKey === 'execute_code' ? 'execute_code' : 'Tool approval')
  }
  const next = { ...$pendingApprovals.get(), [nextItem.id]: nextItem }
  const retained = Object.values(next)
    .sort((left, right) => right.timestamp - left.timestamp)
    .slice(0, MAX_PENDING_ITEMS)

  $pendingApprovals.set(Object.fromEntries(retained.map(item => [item.id, item])))
}

function setPendingClarify(event: unknown): void {
  const record = asRecord(event)
  const sessionId = stringValue(record?.session_id)
  const payload = asRecord(record?.payload)
  const requestId = stringValue(payload?.request_id)
  const question = stringValue(payload?.question).trim()

  if (!sessionId || !requestId || !question) {
    return
  }

  const nextItem: PendingClarify = {
    id: requestId,
    options: stringOptions(payload?.choices),
    question,
    sessionId,
    sessionKey: '',
    sessionTitle: '',
    timestamp: Date.now()
  }
  const next = { ...$pendingClarifies.get(), [nextItem.id]: nextItem }
  const retained = Object.values(next)
    .sort((left, right) => right.timestamp - left.timestamp)
    .slice(0, MAX_PENDING_ITEMS)

  $pendingClarifies.set(Object.fromEntries(retained.map(item => [item.id, item])))
}

function normalizeRecentResolutions(value: unknown): RecentResolution[] {
  if (!Array.isArray(value)) {
    return []
  }

  return value
    .flatMap<RecentResolution>(row => {
      const record = asRecord(row)
      const choice = stringValue(record?.choice).trim()
      const id = stringValue(record?.id).trim()
      const kind: HistoryKind | null =
        record?.kind === 'approval' || record?.kind === 'clarify' ? record.kind : null
      const timestamp = typeof record?.timestamp === 'number' && Number.isFinite(record.timestamp) ? record.timestamp : 0
      const title = stringValue(record?.title).trim()

      return choice && id && kind && timestamp > 0 && title ? [{ choice, id, kind, timestamp, title }] : []
    })
    .sort((left, right) => right.timestamp - left.timestamp)
    .slice(0, MAX_RECENT_ITEMS)
}

function loadRecentResolutions(): void {
  try {
    $recentResolutions.set(normalizeRecentResolutions(pluginStorage?.get<unknown>(HISTORY_STORAGE_KEY, []) ?? []))
  } catch {
    $recentResolutions.set([])
  }
}

function addRecentResolution(kind: HistoryKind, sourceId: string, title: string, choice: string): void {
  const timestamp = Date.now()
  const next = normalizeRecentResolutions([
    {
      choice,
      id: `${kind}:${sourceId}:${timestamp}`,
      kind,
      timestamp,
      title: title.trim() || (kind === 'approval' ? 'Approval request' : 'Clarification request')
    },
    ...$recentResolutions.get()
  ])

  $recentResolutions.set(next)
  try {
    pluginStorage?.set(HISTORY_STORAGE_KEY, next)
  } catch (error) {
    host.notifyError(error, 'Could not save approval history')
  }
}

function approvalHistoryTitle(item: PendingApproval): string {
  return item.sessionTitle || item.toolName || item.description
}

function removePendingApproval(id: string, choice?: string): void {
  const current = $pendingApprovals.get()
  const item = current[id]

  if (!item) {
    return
  }

  if (choice) {
    addRecentResolution('approval', item.id, approvalHistoryTitle(item), choice)
  }

  const next = { ...current }
  delete next[id]
  $pendingApprovals.set(next)
}

function removePendingClarify(id: string, choice?: string): void {
  const current = $pendingClarifies.get()
  const item = current[id]

  if (!item) {
    return
  }

  if (choice) {
    addRecentResolution('clarify', item.id, item.question, choice)
  }

  const next = { ...current }
  delete next[id]
  $pendingClarifies.set(next)
}

function resolvePendingForSession(event: unknown): void {
  const sessionId = sessionIdFrom(event)

  if (!sessionId) {
    return
  }

  removePendingApproval(sessionId, 'Resolved in session')
  for (const item of Object.values($pendingClarifies.get())) {
    if (item.sessionId === sessionId) {
      removePendingClarify(item.id, 'Answered in session')
    }
  }
  latestTools.delete(sessionId)
}

function discardPendingForSession(event: unknown): void {
  const sessionId = sessionIdFrom(event)

  if (!sessionId) {
    return
  }

  removePendingApproval(sessionId)
  for (const item of Object.values($pendingClarifies.get())) {
    if (item.sessionId === sessionId) {
      removePendingClarify(item.id)
    }
  }
  latestTools.delete(sessionId)
}

function normalizeActiveSessions(value: unknown): ActiveSession[] {
  const rows = asRecord(value)?.sessions

  if (!Array.isArray(rows)) {
    return []
  }

  return rows.flatMap(row => {
    const record = asRecord(row)
    const id = stringValue(record?.id)

    return id
      ? [
          {
            id,
            sessionKey: stringValue(record?.session_key),
            status: stringValue(record?.status),
            title: stringValue(record?.title)
          }
        ]
      : []
  })
}

function sessionTitles(value: unknown): Map<string, string> {
  const rows = asRecord(value)?.sessions
  const titles = new Map<string, string>()

  if (!Array.isArray(rows)) {
    return titles
  }

  for (const row of rows) {
    const record = asRecord(row)
    const id = stringValue(record?.id)
    const title = stringValue(record?.title)

    if (id && title) {
      titles.set(id, title)
    }
  }

  return titles
}

async function reconcilePendingItems(): Promise<void> {
  try {
    const [activeResult, sessionResult] = await Promise.all([
      host.request<unknown>('session.active_list', {}),
      host.request<unknown>('session.list', SESSION_LIST_PARAMS)
    ])
    const activeRows = normalizeActiveSessions(activeResult)
    const titles = sessionTitles(sessionResult)
    const activeByRuntimeId = new Map(activeRows.map(row => [row.id, row]))
    const nextApprovals: Record<string, PendingApproval> = {}
    const nextClarifies: Record<string, PendingClarify> = {}

    for (const item of Object.values($pendingApprovals.get())) {
      const active = activeByRuntimeId.get(item.sessionId)

      // Blocking prompts are represented as running live sessions, not as the
      // gateway's generic pending-prompt status. Existence is the safe signal.
      if (!active) {
        continue
      }

      const sessionKey = active.sessionKey || item.sessionKey
      nextApprovals[item.id] = {
        ...item,
        sessionKey,
        sessionTitle: active.title || titles.get(sessionKey) || titles.get(item.sessionId) || item.sessionTitle
      }
    }

    for (const item of Object.values($pendingClarifies.get())) {
      const active = activeByRuntimeId.get(item.sessionId)

      if (!active) {
        continue
      }

      const sessionKey = active.sessionKey || item.sessionKey
      nextClarifies[item.id] = {
        ...item,
        sessionKey,
        sessionTitle: active.title || titles.get(sessionKey) || titles.get(item.sessionId) || item.sessionTitle
      }
    }

    $pendingApprovals.set(nextApprovals)
    $pendingClarifies.set(nextClarifies)
  } catch (error) {
    host.notifyError(error, 'Could not reconcile pending requests')
  }
}

function SubscriptionLifecycle() {
  useEffect(() => {
    subscribeApprovalEvents()
    return unsubscribeApprovalEvents
  }, [])

  return null
}

async function respondToApproval(
  id: string,
  choice: ApprovalChoice,
  setSubmitting: (choice: ApprovalChoice | null) => void
): Promise<void> {
  if (submittingIds.has(id)) {
    return
  }

  const item = $pendingApprovals.get()[id]

  if (!item) {
    return
  }

  submittingIds.add(id)
  setSubmitting(choice)

  try {
    const result = await host.request<{ resolved?: boolean }>('approval.respond', {
      session_id: item.sessionId,
      choice
    })

    if (result.resolved === true) {
      removePendingApproval(id, choice === 'deny' ? 'Denied' : 'Approved once')
    } else {
      setSubmitting(null)
    }
  } catch (error) {
    host.notifyError(error, choice === 'deny' ? 'Could not deny approval' : 'Could not approve request')
    setSubmitting(null)
  } finally {
    submittingIds.delete(id)
  }
}

async function respondToClarify(
  id: string,
  answer: string,
  setSubmitting: (answer: string | null) => void
): Promise<void> {
  if (submittingIds.has(id)) {
    return
  }

  const item = $pendingClarifies.get()[id]

  if (!item || !item.options.includes(answer)) {
    return
  }

  submittingIds.add(id)
  setSubmitting(answer)

  try {
    await host.request<unknown>('clarify.respond', {
      request_id: item.id,
      answer
    })
    removePendingClarify(id, answer)
  } catch (error) {
    host.notifyError(error, 'Could not send clarification')
    setSubmitting(null)
  } finally {
    submittingIds.delete(id)
  }
}

function openPendingSession(kind: HistoryKind, id: string): void {
  const item = kind === 'approval' ? $pendingApprovals.get()[id] : $pendingClarifies.get()[id]

  if (!item) {
    return
  }

  host.navigate(`/${encodeURIComponent(item.sessionKey || item.sessionId)}`)
}

function ApprovalCard({ item }: { item: PendingApproval }) {
  const [submitting, setSubmitting] = useState<ApprovalChoice | null>(null)
  const title = item.sessionTitle || 'Untitled session'

  return (
    <article className="border-t border-(--ui-stroke-secondary) py-3 first:border-t-0">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0 truncate text-sm font-medium" title={title}>
          {title}
        </div>
        <span className="shrink-0 text-xs text-(--ui-text-tertiary)">{relativeTime(item.timestamp)}</span>
      </div>

      <div className="mt-1 font-mono text-xs font-medium text-(--ui-text-primary)">{item.toolName}</div>
      <p className="mt-1 text-xs leading-5 text-(--ui-text-secondary)">{item.description}</p>
      {item.argsPreview && (
        <code className="mt-2 block rounded-md bg-(--ui-bg-quaternary) px-2 py-1.5 font-mono text-xs leading-4 break-all text-(--ui-text-primary)">
          {item.argsPreview}
        </code>
      )}

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button
          disabled={submitting !== null}
          size="xs"
          type="button"
          onClick={() => void respondToApproval(item.id, 'once', setSubmitting)}
        >
          {submitting === 'once' ? 'Approving...' : 'Approve'}
        </Button>
        <Button
          disabled={submitting !== null}
          size="xs"
          type="button"
          variant="outline"
          onClick={() => void respondToApproval(item.id, 'deny', setSubmitting)}
        >
          {submitting === 'deny' ? 'Denying...' : 'Deny'}
        </Button>
        <Button
          className="ml-auto"
          size="inline"
          type="button"
          variant="text"
          onClick={() => openPendingSession('approval', item.id)}
        >
          Open session
        </Button>
      </div>
    </article>
  )
}

function ClarifyCard({ item }: { item: PendingClarify }) {
  const [submitting, setSubmitting] = useState<string | null>(null)
  const title = item.sessionTitle || 'Untitled session'

  return (
    <article className="border-t border-(--ui-stroke-secondary) py-3 first:border-t-0">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <div className="min-w-0 truncate text-sm font-medium" title={title}>
          {title}
        </div>
        <span className="shrink-0 text-xs text-(--ui-text-tertiary)">{relativeTime(item.timestamp)}</span>
      </div>

      <div className="mt-1 text-xs font-medium tracking-wide text-(--ui-text-tertiary) uppercase">
        Clarification
      </div>
      <p className="mt-1 text-xs leading-5 text-(--ui-text-primary)">{item.question}</p>

      {item.options.length > 0 ? (
        <div className="mt-3 flex flex-wrap gap-2">
          {item.options.map(option => (
            <Button
              disabled={submitting !== null}
              key={option}
              size="xs"
              type="button"
              variant="outline"
              onClick={() => void respondToClarify(item.id, option, setSubmitting)}
            >
              {submitting === option ? 'Sending...' : option}
            </Button>
          ))}
        </div>
      ) : (
        <p className="mt-2 text-xs text-(--ui-text-secondary)">Respond in the session with a text answer.</p>
      )}

      <div className="mt-2 flex justify-end">
        <Button size="inline" type="button" variant="text" onClick={() => openPendingSession('clarify', item.id)}>
          Open session
        </Button>
      </div>
    </article>
  )
}

function RecentSection() {
  const items = useValue($recentResolutions)

  return (
    <details className="border-t border-(--ui-stroke-secondary)">
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-3 py-2.5 text-xs font-medium text-(--ui-text-secondary)">
        <span>Recent</span>
        {items.length > 0 && (
          <Badge size="xs" variant="muted">
            {items.length}
          </Badge>
        )}
      </summary>
      {items.length === 0 ? (
        <p className="px-3 pb-3 text-xs text-(--ui-text-tertiary)">No resolved requests yet.</p>
      ) : (
        <div className="px-3 pb-2">
          {items.map(item => (
            <div className="border-t border-(--ui-stroke-secondary) py-2 first:border-t-0" key={item.id}>
              <div className="flex min-w-0 items-center justify-between gap-2">
                <span className="text-xs font-medium tracking-wide text-(--ui-text-tertiary) uppercase">
                  {item.kind}
                </span>
                <span className="shrink-0 text-xs text-(--ui-text-tertiary)">
                  {relativeTime(item.timestamp)}
                </span>
              </div>
              <div className="mt-0.5 truncate text-xs text-(--ui-text-primary)" title={item.title}>
                {item.title}
              </div>
              <div className="mt-0.5 text-xs text-(--ui-text-secondary)">{item.choice}</div>
            </div>
          ))}
        </div>
      )}
    </details>
  )
}

function ApprovalsPane() {
  const approvals = Object.values(useValue($pendingApprovals))
  const clarifies = Object.values(useValue($pendingClarifies))
  const items = [
    ...approvals.map(item => ({ item, kind: 'approval' as const, timestamp: item.timestamp })),
    ...clarifies.map(item => ({ item, kind: 'clarify' as const, timestamp: item.timestamp }))
  ].sort((left, right) => right.timestamp - left.timestamp)

  useEffect(() => {
    void reconcilePendingItems()
  }, [])

  return (
    <div className="flex h-full min-h-0 flex-col text-sm">
      <div className="flex items-center justify-between gap-3 border-b border-(--ui-stroke-secondary) px-3 py-2.5">
        <span className="font-medium">Approvals</span>
        {items.length > 0 && (
          <Badge size="xs" variant="warn">
            {items.length}
          </Badge>
        )}
      </div>
      <ScrollArea className="min-h-0 flex-1">
        {items.length === 0 ? (
          <EmptyState className="min-h-40 px-3" title="Nothing needs your attention" />
        ) : (
          <div className="px-3">
            {items.map(row =>
              row.kind === 'approval' ? (
                <ApprovalCard item={row.item} key={`approval:${row.item.id}`} />
              ) : (
                <ClarifyCard item={row.item} key={`clarify:${row.item.id}`} />
              )
            )}
          </div>
        )}
        <RecentSection />
      </ScrollArea>
    </div>
  )
}

function ApprovalsStatusbar() {
  const approvalCount = Object.keys(useValue($pendingApprovals)).length
  const clarifyCount = Object.keys(useValue($pendingClarifies)).length
  const count = approvalCount + clarifyCount

  return (
    <>
      <SubscriptionLifecycle />
      {count > 0 && (
        <Tip label="Open pending requests in Town">
          <button
            aria-label={`Open ${count} pending request${count === 1 ? '' : 's'}`}
            className={cn(
              'inline-flex h-full items-center gap-1.5 px-1.5 text-xs transition-colors',
              'text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground'
            )}
            onClick={() => host.navigate('/town')}
            type="button"
          >
            approvals
            <Badge size="xs" variant="warn">
              {count}
            </Badge>
          </button>
        </Tip>
      )}
    </>
  )
}

export default {
  id: 'town-approvals',
  name: 'Town Approvals',
  register(ctx: PluginContext) {
    pluginStorage = ctx.storage as PluginStorage
    loadRecentResolutions()
    // Catch requests raised before the statusbar host mounts; the host's
    // unmount cleanup handles disposal on plugin unload/reload.
    subscribeApprovalEvents()

    ctx.registerMany([
      {
        id: 'pane',
        area: 'panes',
        title: 'approvals',
        data: { placement: 'right', width: '300px' },
        render: () => <ApprovalsPane />
      },
      {
        id: 'badge',
        area: 'statusBar.right',
        order: 132,
        render: () => <ApprovalsStatusbar />
      }
    ])
  }
}
