import { test } from 'node:test'
import assert from 'node:assert/strict'

// @ts-ignore -- plain .mjs build helper
import { hoistImports, importsAreLeading } from '../../scripts/hoist-imports.mjs'

// The shape esbuild emits for a plugin split across several .tsx modules.
const BUNDLE = [
  '// src/town-home/plugin.tsx',
  'import {',
  '  Badge,',
  '  Button',
  '} from "@hermes/plugin-sdk";',
  'import { useState } from "react";',
  '',
  '// src/town-home/lib/text.ts',
  "var QUOTES = /[\"'`]/g;",
  '',
  '// src/town-home/people.tsx',
  'import { jsx, jsxs } from "react/jsx-runtime";',
  'function People() { return jsx("div", {}); }',
  '',
  '// src/town-home/upnext.tsx',
  'import { Badge as Badge2 } from "@hermes/plugin-sdk";',
  'import { jsx as jsx2 } from "react/jsx-runtime";',
  'var label = "imports from \\"react\\" stay in strings";',
  'export default { id: "x" };',
  ''
].join('\n')

test('hoistImports moves every import declaration ahead of code', () => {
  assert.equal(importsAreLeading(BUNDLE), false)
  const hoisted = hoistImports(BUNDLE)
  assert.equal(importsAreLeading(hoisted), true)
  const lines = hoisted.split('\n')
  assert.deepEqual(
    lines.slice(0, 9),
    [
      'import {',
      '  Badge,',
      '  Button',
      '} from "@hermes/plugin-sdk";',
      'import { useState } from "react";',
      'import { jsx, jsxs } from "react/jsx-runtime";',
      'import { Badge as Badge2 } from "@hermes/plugin-sdk";',
      'import { jsx as jsx2 } from "react/jsx-runtime";',
      '// src/town-home/plugin.tsx'
    ]
  )
  // Code and strings are untouched; only declarations moved.
  assert.ok(hoisted.includes("var QUOTES = /[\"'`]/g;"))
  assert.ok(hoisted.includes('var label = "imports from \\"react\\" stay in strings";'))
  assert.equal(hoisted.match(/^import /gm)?.length, 5)
})

test('hoistImports leaves an import-free or already-leading bundle alone', () => {
  const plain = 'var x = 1;\nexport default { id: "x" };\n'
  assert.equal(hoistImports(plain), plain)
  const leading = 'import { jsx } from "react/jsx-runtime";\nvar x = 1;\n'
  assert.equal(importsAreLeading(leading), true)
  assert.equal(importsAreLeading(hoistImports(leading)), true)
})
