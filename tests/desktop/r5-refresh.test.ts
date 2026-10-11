import { test } from 'node:test'
import assert from 'node:assert/strict'

import { curationAdvanced, waitForCuration } from '../../src/town-feed/lib/refresh.ts'

function fakeClock() {
  let now = 1_000_000
  const sleeps: number[] = []
  return {
    now: () => now,
    sleep: async (ms: number) => {
      sleeps.push(ms)
      now += ms
    },
    sleeps
  }
}

test('curationAdvanced compares against since', () => {
  assert.equal(curationAdvanced(1000, 2000), true)
  assert.equal(curationAdvanced(1000, 1000), false)
  assert.equal(curationAdvanced(1000, null), false)
  assert.equal(curationAdvanced(1000, undefined), false)
  assert.equal(curationAdvanced(null, 1), true)
  assert.equal(curationAdvanced(null, null), false)
})

test('waitForCuration resolves once curated_at moves past since', async () => {
  const clock = fakeClock()
  const answers = [1000, undefined, 1000, 5000]
  const advanced = await waitForCuration({
    since: 1000,
    poll: async () => answers.shift(),
    sleep: clock.sleep,
    now: clock.now
  })
  assert.equal(advanced, true)
  assert.deepEqual(clock.sleeps, [5000, 5000, 5000, 5000])
})

test('waitForCuration gives up after two minutes and never polls faster than 5 s', async () => {
  const clock = fakeClock()
  let polls = 0
  const advanced = await waitForCuration({
    since: 1000,
    poll: async () => {
      polls += 1
      return 1000
    },
    sleep: clock.sleep,
    now: clock.now,
    intervalMs: 1,
    timeoutMs: 10 * 60_000
  })
  assert.equal(advanced, false)
  assert.equal(polls, 24)
  assert.ok(clock.sleeps.every(ms => ms >= 5000))
})

test('waitForCuration survives a failing poll', async () => {
  const clock = fakeClock()
  let calls = 0
  const advanced = await waitForCuration({
    since: null,
    poll: async () => {
      calls += 1
      if (calls === 1) {
        throw new Error('gateway restarting')
      }
      return 42
    },
    sleep: clock.sleep,
    now: clock.now
  })
  assert.equal(advanced, true)
  assert.equal(calls, 2)
})
