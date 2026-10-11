// Cron run output is the agent's markdown reply (the gateway already cut the
// prompt off at the last "## Response" heading). Render it as readable plain
// text and humanize the run status (R5).

export type RunTone = 'bad' | 'good' | 'muted' | 'warn'

const BULLET_RE = /^(\s*)(?:[-*+•●▪◦‣])\s+/
const HEADING_RE = /^\s{0,3}#{1,6}\s+/
const CLOSING_HASHES_RE = /\s+#+\s*$/
const BLOCKQUOTE_RE = /^\s{0,3}>\s?/
const FENCE_RE = /^\s{0,3}(?:```|~~~)/
const RULE_RE = /^\s{0,3}([-*_])(?:\s*\1){2,}\s*$/

function stripInline(text: string): string {
  return text
    .replace(/!\[([^\]]*)\]\((?:[^()]|\([^)]*\))*\)/g, '$1')
    .replace(/\[([^\]]+)\]\((?:[^()]|\([^)]*\))*\)/g, '$1')
    .replace(/\[([^\]]+)\]\[[^\]]*\]/g, '$1')
    .replace(/<((?:https?|mailto):[^>\s]+)>/gi, '$1')
    .replace(/<\/?(?:b|i|u|s|em|strong|code|del|ins)>/gi, '')
    .replace(/`([^`\n]+)`/g, '$1')
    .replace(/(?<!\\)(\*\*|__)(?=\S)(.*?\S)\1/g, '$2')
    .replace(/(?<!\\)~~(?=\S)(.*?\S)~~/g, '$1')
    .replace(/(?<![\\\w*])\*(?=[^\s*])([^*\n]*?[^\s*\\])\*(?![\w*])/g, '$1')
    .replace(/(?<![\\\w])_(?=[^\s_])([^_\n]*?[^\s_\\])_(?!\w)/g, '$1')
    .replace(/\\([\\`*_{}[\]()#+\-.!>~|])/g, '$1')
}

/** Markdown -> readable plain text: headings, emphasis and code markers dropped,
 *  links shown as their text, bullets normalized to "• ". */
export function stripMarkdownToText(markdown: string): string {
  const lines: string[] = []
  for (const raw of markdown.replace(/\r\n?/g, '\n').split('\n')) {
    if (FENCE_RE.test(raw) || RULE_RE.test(raw)) {
      lines.push('')
      continue
    }
    let line = raw
    while (BLOCKQUOTE_RE.test(line)) {
      line = line.replace(BLOCKQUOTE_RE, '')
    }
    if (HEADING_RE.test(line)) {
      line = line.replace(HEADING_RE, '').replace(CLOSING_HASHES_RE, '')
    }
    line = line.replace(BULLET_RE, '$1• ')
    lines.push(stripInline(line).replace(/[ \t]+$/, ''))
  }
  return lines.join('\n').replace(/\n{3,}/g, '\n\n').trim()
}

/** A short multi-line preview for a card: plain text, blank lines dropped, capped. */
export function runOutputPreview(markdown: string, maxChars = 400): string {
  const text = stripMarkdownToText(markdown)
    .split('\n')
    .map(line => line.trim())
    .filter(Boolean)
    .join('\n')
  return text.length <= maxChars ? text : `${text.slice(0, maxChars - 1).trimEnd()}…`
}

const FAILED_STATUS_RE = /\b(?:error|fail(?:ed|ure)?|blocked\w*|interrupted|timeout|timed[ _]out|crash(?:ed)?)\b/

function foldStatus(status: unknown): string {
  return typeof status === 'string' ? status.trim().toLowerCase().replace(/[-\s]+/g, '_') : ''
}

/** Human run status — never a raw "ok": Delivered, Ran, Failed, Delivery failed, Running, Skipped. */
export function humanRunStatus(status: unknown, delivered?: boolean | null): string {
  const normalized = foldStatus(status)
  if (normalized === 'delivery_failed') {
    return 'Delivery failed'
  }
  if (FAILED_STATUS_RE.test(normalized.replace(/_/g, ' '))) {
    return 'Failed'
  }
  if (delivered === false) {
    return 'Delivery failed'
  }
  if (normalized === 'running' || normalized === 'claimed') {
    return 'Running'
  }
  if (normalized === 'skipped' || normalized === 'silent') {
    return 'Skipped'
  }
  return delivered === true ? 'Delivered' : 'Ran'
}

export function runStatusTone(status: unknown, delivered?: boolean | null): RunTone {
  const label = humanRunStatus(status, delivered)
  if (label === 'Failed') {
    return 'bad'
  }
  if (label === 'Delivery failed') {
    return 'warn'
  }
  if (label === 'Delivered') {
    return 'good'
  }
  const normalized = foldStatus(status)
  return label === 'Ran' && (normalized === 'ok' || normalized === 'completed' || normalized === 'success')
    ? 'good'
    : 'muted'
}
