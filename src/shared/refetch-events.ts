// Which gateway notifications can change data a Town surface shows (R5).
// Event names are the backend's GATEWAY_EVENT_TYPES
// (hermes-agent apps/shared/src/gateway-contract.generated.ts), fanned out to
// plugins by apps/desktop/src/contrib/events.ts. Streaming chatter
// (message.delta, reasoning/thinking deltas, tool.*, status.update,
// session.usage, subagent.*, moa.*) must never trigger a refetch. Approval,
// clarify, sudo and secret prompts are server->client requests, not events,
// so the active-session poll covers them; request.cancel / approval.cancelled
// are their event-side withdrawals. Kanban has no gateway events at all.

export type RefetchTopic = 'sessions' | 'activeSessions' | 'cron' | 'feed'

const TOPIC_EVENT_TYPES: Record<RefetchTopic, readonly string[]> = {
  // session.list: created/ended/renamed sessions (other processes included).
  sessions: ['gateway.ready', 'sessions.changed', 'session.title', 'message.complete'],
  // session.active_list: live turns starting/ending and withdrawn prompts.
  activeSessions: [
    'gateway.ready',
    'message.start',
    'message.complete',
    'request.cancel',
    'approval.cancelled',
    'session.reclaimed'
  ],
  // cron.manage list: jobs.json moved (edits, runs, scheduler ticks).
  cron: ['gateway.ready', 'cron.changed'],
  // /feed + /kanban: curator/scanner runs land via cron; turns may move cards.
  feed: ['gateway.ready', 'cron.changed', 'message.complete']
}

const ALL_REFETCH_EVENT_TYPES: ReadonlySet<string> = new Set(Object.values(TOPIC_EVENT_TYPES).flat())
const TOPIC_SETS = new Map<RefetchTopic, ReadonlySet<string>>(
  (Object.keys(TOPIC_EVENT_TYPES) as RefetchTopic[]).map(topic => [topic, new Set(TOPIC_EVENT_TYPES[topic])])
)

function eventTypeOf(event: unknown): string {
  if (typeof event === 'string') {
    return event
  }
  if (event !== null && typeof event === 'object') {
    const type = (event as { type?: unknown }).type
    return typeof type === 'string' ? type : ''
  }
  return ''
}

/** Whether a gateway event (or bare event type) should refetch `topic` (any topic when omitted). */
export function shouldRefetchForEvent(event: unknown, topic?: RefetchTopic): boolean {
  const type = eventTypeOf(event)
  if (!type) {
    return false
  }
  const allowed = topic ? TOPIC_SETS.get(topic) : ALL_REFETCH_EVENT_TYPES
  return allowed?.has(type) ?? false
}

/** The event types to subscribe to for `topic` — never `'*'`. */
export function refetchEventTypes(topic: RefetchTopic): string[] {
  return [...TOPIC_EVENT_TYPES[topic]]
}
