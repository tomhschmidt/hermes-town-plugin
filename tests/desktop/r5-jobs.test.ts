import { test } from 'node:test'
import assert from 'node:assert/strict'

import { isInternalTownJob } from '../../src/town-home/lib/jobs.ts'

test('isInternalTownJob hides the scanner jobs', () => {
  for (const name of ['town-gmail-scan', 'town-telegram-scan', 'town-calendar-scan', 'Town-Gmail-Scan ']) {
    assert.equal(isInternalTownJob({ name }), true, name)
  }
  assert.equal(isInternalTownJob({ name: 'Inbox sweep', script: 'town-telegram-scan.py' }), true)
  assert.equal(isInternalTownJob({ name: 'Inbox sweep', script: '/opt/example/scripts/town-calendar-scan.py' }), true)
})

test('isInternalTownJob hides the curate job', () => {
  assert.equal(isInternalTownJob({ name: 'town-feed-curate' }), true)
  assert.equal(isInternalTownJob({ name: 'Town Feed curator' }), true)
  assert.equal(isInternalTownJob({ name: 'Feed', script: 'town-curate.py' }), true)
  assert.equal(isInternalTownJob({ name: 'Feed', prompt: 'Call the town_feed_curate tool and stop.' }), true)
  assert.equal(isInternalTownJob({ name: 'Feed', prompt: '', promptPreview: 'Run town_feed_curate now...' }), true)
})

test('isInternalTownJob keeps normal routines', () => {
  assert.equal(isInternalTownJob({ name: 'Morning briefing', prompt: 'Brief Alice on her day.' }), false)
  assert.equal(isInternalTownJob({ name: 'town-hall-notes', script: 'notes.py' }), false)
  assert.equal(isInternalTownJob({ name: 'Weekly review', script: '' }), false)
  assert.equal(isInternalTownJob({}), false)
  assert.equal(isInternalTownJob(null), false)
})
