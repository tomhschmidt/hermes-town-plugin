// Hermes Desktop loads plugin.js by rewriting its `@hermes/plugin-sdk` /
// `react*` specifiers with a lexer that is not syntax-aware. Shells before
// hermes-agent #120208 (e.g. client cda2374, Sep 2026) mistake a quote inside
// a regex literal for a string, so any import AFTER such a regex is left as a
// bare specifier and the plugin fails to load. esbuild emits an external's
// imports where each bundled source module begins, so a plugin split across
// several .tsx files gets mid-file imports. Hoisting them all to the top keeps
// every import ahead of any code: import declarations are hoisted by the
// engine anyway, and esbuild has already made the local bindings unique.

// A top-level import declaration as esbuild prints it: named (possibly
// multi-line), default, namespace, or side-effect.
const IMPORT_DECL = /^import\s+(?:[\w$*{}\s,]+?\s+from\s+)?(["'])([^"']+)\1;?[ \t]*(?:\r?\n|$)/gm

const COMMENT = /\/\/[^\n]*|\/\*[\s\S]*?\*\//g

/** The source with every top-level import declaration moved to the top. */
export function hoistImports(source) {
  const imports = []
  const body = source.replace(IMPORT_DECL, (declaration) => {
    imports.push(declaration.trimEnd())
    return ''
  })
  if (imports.length === 0) {
    return source
  }
  return `${imports.join('\n')}\n${body.replace(/^\s*\n/, '')}`
}

/** True when no import declaration follows code (comments/whitespace only). */
export function importsAreLeading(source) {
  let position = 0
  for (const match of source.matchAll(IMPORT_DECL)) {
    if (source.slice(position, match.index).replace(COMMENT, '').trim()) {
      return false
    }
    position = match.index + match[0].length
  }
  return true
}
