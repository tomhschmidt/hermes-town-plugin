import { test } from 'node:test'
import assert from 'node:assert/strict'

import {
  humanRunStatus,
  runOutputPreview,
  runStatusTone,
  stripMarkdownToText
} from '../../src/town-home/lib/run-output.ts'

const BRIEFING = [
  '## Morning briefing',
  '',
  '🌅 **Good morning, Alice**',
  '*Saturday, Oct 10 — 3 events · 2 emails*',
  '',
  '### Calendar',
  '- **9:00 AM** - Standup with Bob ([Join](https://meet.example.com/abc-(1)))',
  '* 1:00 PM - Lunch with __Carol__',
  '+ [Weekend Crew](https://t.me/example) wants a reply',
  '•   Already a bullet',
  '  - Nested follow-up for _Dave_',
  '',
  '> Quote from Dave',
  '',
  '---',
  '',
  'Visit <https://example.com/docs> and read `notes.md` ~~today~~.',
  'Escaped \\*not italic\\* and snake_case_name stay; 2 * 3 * 4 = 24.',
  '![Chart](https://example.com/chart.png)'
].join('\n')

test('stripMarkdownToText renders a briefing as readable plain text', () => {
  assert.equal(
    stripMarkdownToText(BRIEFING),
    [
      'Morning briefing',
      '',
      '🌅 Good morning, Alice',
      'Saturday, Oct 10 — 3 events · 2 emails',
      '',
      'Calendar',
      '• 9:00 AM - Standup with Bob (Join)',
      '• 1:00 PM - Lunch with Carol',
      '• Weekend Crew wants a reply',
      '• Already a bullet',
      '  • Nested follow-up for Dave',
      '',
      'Quote from Dave',
      '',
      'Visit https://example.com/docs and read notes.md today.',
      'Escaped *not italic* and snake_case_name stay; 2 * 3 * 4 = 24.',
      'Chart'
    ].join('\n')
  )
})

test('stripMarkdownToText leaves no markdown syntax behind', () => {
  const text = stripMarkdownToText(BRIEFING)
  assert.doesNotMatch(text, /\*\*|__|\]\(|^#{1,6} |^\s*[-+] /m)
  assert.doesNotMatch(text, /https:\/\/meet\.example\.com/)
})

test('stripMarkdownToText normalizes CRLF and collapses blank runs', () => {
  assert.equal(stripMarkdownToText('# Title\r\n\r\n\r\n\r\nBody **bold**\r\n'), 'Title\n\nBody bold')
  assert.equal(stripMarkdownToText(''), '')
})

test('runOutputPreview drops blank lines and caps length', () => {
  assert.equal(runOutputPreview('## Hi\n\n- **one**\n\n- two'), 'Hi\n• one\n• two')
  const long = runOutputPreview('word '.repeat(200), 50)
  assert.equal(long.length, 50)
  assert.ok(long.endsWith('…'))
})

test('humanRunStatus never shows a raw status', () => {
  assert.equal(humanRunStatus('ok', true), 'Delivered')
  assert.equal(humanRunStatus('ok'), 'Ran')
  assert.equal(humanRunStatus('ok', null), 'Ran')
  assert.equal(humanRunStatus('completed'), 'Ran')
  assert.equal(humanRunStatus('unknown'), 'Ran')
  assert.equal(humanRunStatus(''), 'Ran')
  assert.equal(humanRunStatus(undefined), 'Ran')
  assert.equal(humanRunStatus('error'), 'Failed')
  assert.equal(humanRunStatus('failed', true), 'Failed')
  assert.equal(humanRunStatus('blocked_config'), 'Failed')
  assert.equal(humanRunStatus('interrupted'), 'Failed')
  assert.equal(humanRunStatus('delivery_failed'), 'Delivery failed')
  assert.equal(humanRunStatus('ok', false), 'Delivery failed')
  assert.equal(humanRunStatus('running'), 'Running')
  assert.equal(humanRunStatus('skipped'), 'Skipped')
  for (const status of ['ok', 'error', 'unknown', 'completed', 'delivery_failed']) {
    assert.notEqual(humanRunStatus(status), status)
  }
})

test('runStatusTone follows the humanized status', () => {
  assert.equal(runStatusTone('ok', true), 'good')
  assert.equal(runStatusTone('ok'), 'good')
  assert.equal(runStatusTone('unknown'), 'muted')
  assert.equal(runStatusTone('error'), 'bad')
  assert.equal(runStatusTone('delivery_failed'), 'warn')
  assert.equal(runStatusTone('ok', false), 'warn')
})
