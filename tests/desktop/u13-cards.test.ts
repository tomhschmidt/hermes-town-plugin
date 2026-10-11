import { test } from 'node:test'
import assert from 'node:assert/strict'

import { compactAge, isStale } from '../../src/shared/age.ts'
import { sourceAction } from '../../src/shared/sources.ts'
import { isCronSession, sessionSource, tasksFallbackSessions } from '../../src/town-home/lib/tasks.ts'

const NOW = Date.UTC(2026, 9, 11, 14, 0)
const MINUTE = 60_000
const HOUR = 60 * MINUTE
const DAY = 24 * HOUR

test('compactAge: minute, hour, and day boundaries', () => {
  assert.equal(compactAge(NOW, NOW), 'now')
  assert.equal(compactAge(NOW - 59_999, NOW), 'now')
  assert.equal(compactAge(NOW - MINUTE, NOW), '1m')
  assert.equal(compactAge(NOW - 27 * MINUTE, NOW), '27m')
  assert.equal(compactAge(NOW - (HOUR - 1), NOW), '59m')
  assert.equal(compactAge(NOW - HOUR, NOW), '1h')
  assert.equal(compactAge(NOW - 5 * HOUR - 59 * MINUTE, NOW), '5h')
  assert.equal(compactAge(NOW - (DAY - 1), NOW), '23h')
  assert.equal(compactAge(NOW - DAY, NOW), '1d')
  assert.equal(compactAge(NOW - 3 * DAY - 2 * HOUR, NOW), '3d')
  // Clock skew never reads as the future; unknown times render nothing.
  assert.equal(compactAge(NOW + 5 * MINUTE, NOW), 'now')
  assert.equal(compactAge(null, NOW), null)
  assert.equal(compactAge(Number.NaN, NOW), null)
})

test('isStale: older than 60 minutes', () => {
  assert.equal(isStale(NOW - 22 * MINUTE, NOW), false)
  assert.equal(isStale(NOW - 60 * MINUTE, NOW), false)
  assert.equal(isStale(NOW - 60 * MINUTE - 1, NOW), true)
  assert.equal(isStale(NOW - 5 * HOUR, NOW), true)
  assert.equal(isStale(null, NOW), false)
})

test('sourceAction: one icon-only action per openable source', () => {
  assert.deepEqual(sourceAction('gmail:alice@example.com:18f2a'), { kind: 'gmail', label: 'Open in Gmail', icon: 'mail' })
  assert.deepEqual(sourceAction('session:abc123'), { kind: 'session', label: 'Open session', icon: 'comment-discussion' })
  assert.equal(sourceAction('cron:job-1'), null)
  assert.deepEqual(sourceAction('cron:job-1', { runs: true }), { kind: 'run', label: 'View run', icon: 'clock' })
  assert.equal(sourceAction('telegram:-1001'), null)
  assert.equal(sourceAction('calendar:alice@example.com:ev1'), null)
  assert.equal(sourceAction('gmail:alice@example.com:'), null)
  assert.equal(sourceAction('session:'), null)
})

const JOBS = [
  { id: 'job0000briefing', name: 'Morning briefing' },
  { id: 'job0000telegram', name: 'telegram-action-items' },
  { id: 'job0000nameless', name: '' }
]

function session(title: string, source = 'desktop', preview = '') {
  return { id: title, title, source, preview, startedAt: null }
}

test('isCronSession: cron source or a title derived from a cron job', () => {
  assert.equal(isCronSession(session('Plan the offsite', 'cron'), JOBS), true)
  assert.equal(isCronSession(session('Morning briefing · Oct 11 08:20', 'telegram'), JOBS), true)
  assert.equal(isCronSession(session('Morning briefing · Oct 11 08:20 #2'), JOBS), true)
  assert.equal(isCronSession(session('telegram-action-items', 'telegram'), JOBS), true)
  assert.equal(isCronSession(session('Telegram action items'), JOBS), true)
  assert.equal(isCronSession(session('cron job0000nameless'), JOBS), true)
  assert.equal(isCronSession(session('Morning briefing ideas for the team'), JOBS), false)
  assert.equal(isCronSession(session('Draft the Q4 memo'), JOBS), false)
  assert.equal(isCronSession(session(''), JOBS), false)
})

test('tasksFallbackSessions drops cron runs and curator sessions, keeps the rest in order', () => {
  const rows = [
    session('Morning briefing · Oct 11 08:20', 'cron', 'Summarize the inbox…'),
    session('Draft the Q4 memo', 'desktop', 'Draft a memo'),
    session('telegram-action-items', 'telegram'),
    session('Curation', 'desktop', 'Run the Town feed curator now'),
    session('Reply to Bob', 'telegram', 'Bob asked about dinner')
  ]
  assert.deepEqual(tasksFallbackSessions(rows, JOBS).map(row => row.title), ['Draft the Q4 memo', 'Reply to Bob'])
  assert.deepEqual(tasksFallbackSessions(rows.slice(1, 2), []).map(row => row.title), ['Draft the Q4 memo'])
})

test('sessionSource: a small icon with a tooltip label instead of a text tag', () => {
  assert.deepEqual(sessionSource('telegram'), { icon: 'send', label: 'Telegram' })
  assert.deepEqual(sessionSource('cli'), { icon: 'terminal', label: 'CLI' })
  assert.deepEqual(sessionSource(' API_SERVER '), { icon: 'plug', label: 'API' })
  assert.deepEqual(sessionSource('whatsapp_bridge'), { icon: 'comment-discussion', label: 'Whatsapp bridge' })
  assert.deepEqual(sessionSource(''), { icon: 'comment-discussion', label: 'Session' })
  assert.deepEqual(sessionSource('Discord'), { icon: 'comment-discussion', label: 'Discord' })
  assert.deepEqual(sessionSource('constructor'), { icon: 'comment-discussion', label: 'Constructor' })
})
