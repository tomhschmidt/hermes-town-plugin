// Desktop unit tests run on Node's built-in runner with native type
// stripping: test pure `.ts` helper modules (no JSX, no SDK/React imports,
// erasable TypeScript only), imported with an explicit `.ts` extension.
import { test } from 'node:test'
import assert from 'node:assert/strict'

test('desktop test harness runs', () => {
  assert.equal(typeof structuredClone, 'function')
})
