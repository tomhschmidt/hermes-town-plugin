import { test } from 'node:test'
import assert from 'node:assert/strict'

import { refetchEventTypes, shouldRefetchForEvent } from '../../src/shared/refetch-events.ts'

const STREAMING_EVENTS = [
  'message.delta',
  'message.interim',
  'reasoning.delta',
  'reasoning.available',
  'thinking.delta',
  'tool.start',
  'tool.generating',
  'tool.complete',
  'status.update',
  'session.usage',
  'subagent.progress',
  'subagent.thinking',
  'subagent.tool',
  'moa.progress',
  'agent.terminal.output',
  'browser.progress',
  'voice.status'
]

test('streaming and delta events never trigger a refetch', () => {
  for (const type of STREAMING_EVENTS) {
    assert.equal(shouldRefetchForEvent(type), false, type)
    assert.equal(shouldRefetchForEvent({ type, session_id: 's1' }), false, type)
    for (const topic of ['sessions', 'activeSessions', 'cron', 'feed'] as const) {
      assert.equal(shouldRefetchForEvent({ type }, topic), false, `${topic}:${type}`)
    }
  }
})

test('data-changing events trigger a refetch', () => {
  for (const type of [
    'sessions.changed',
    'session.title',
    'message.start',
    'message.complete',
    'cron.changed',
    'request.cancel',
    'approval.cancelled',
    'gateway.ready'
  ]) {
    assert.equal(shouldRefetchForEvent({ type }), true, type)
  }
})

test('topics only refetch on their own events', () => {
  assert.equal(shouldRefetchForEvent({ type: 'cron.changed' }, 'cron'), true)
  assert.equal(shouldRefetchForEvent({ type: 'sessions.changed' }, 'cron'), false)
  assert.equal(shouldRefetchForEvent({ type: 'sessions.changed' }, 'sessions'), true)
  assert.equal(shouldRefetchForEvent({ type: 'sessions.changed' }, 'feed'), false)
  assert.equal(shouldRefetchForEvent({ type: 'cron.changed' }, 'feed'), true)
  assert.equal(shouldRefetchForEvent({ type: 'message.start' }, 'activeSessions'), true)
  assert.equal(shouldRefetchForEvent({ type: 'message.start' }, 'sessions'), false)
  assert.equal(shouldRefetchForEvent({ type: 'request.cancel' }, 'activeSessions'), true)
})

test('malformed events and the wildcard are ignored', () => {
  for (const event of ['*', '', null, undefined, 42, {}, { type: 7 }, { type: 'nope.unknown' }]) {
    assert.equal(shouldRefetchForEvent(event), false, String(event))
  }
})

test('subscription lists never include the wildcard and match the predicate', () => {
  for (const topic of ['sessions', 'activeSessions', 'cron', 'feed'] as const) {
    const types = refetchEventTypes(topic)
    assert.ok(types.length > 0)
    assert.ok(!types.includes('*'))
    for (const type of types) {
      assert.equal(shouldRefetchForEvent(type, topic), true, `${topic}:${type}`)
    }
  }
})
