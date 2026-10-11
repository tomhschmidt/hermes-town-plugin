// Source chips and deep links shared by town-home and town-feed (R5).

const NUMERIC_ID_RE = /^[-+]?\d+$/
const EMAIL_RE = /^[^\s@/]+@[^\s@/]+$/

export interface TelegramChipInput {
  chatName?: string | null
  headline?: string | null
  summary?: string | null
}

function usableName(value: unknown): string | null {
  const name = typeof value === 'string' ? value.trim() : ''
  return name && !NUMERIC_ID_RE.test(name) ? name : null
}

/** Chip label for a Telegram item: chat title, then headline/summary prefixes, then "Telegram" — never a raw id. */
export function telegramChipLabel(item: TelegramChipInput): string {
  const chatName = usableName(item.chatName)
  if (chatName) {
    return chatName
  }

  const summary = typeof item.summary === 'string' ? item.summary : ''
  const headline = typeof item.headline === 'string' ? item.headline : ''
  const candidates = [
    summary.match(/^(?:telegram(?: chat)?|chat)\s*[:·—-]\s*([^:\n·—]{1,60})/i)?.[1],
    headline.match(/^([^:\n]{1,60}):\s/)?.[1],
    summary.match(/^([^:\n]{1,60}):\s/)?.[1]
  ]
  for (const candidate of candidates) {
    const name = usableName(candidate)
    if (name) {
      return name
    }
  }
  return 'Telegram'
}

/** Gmail web link for a message, opened in the right mailbox when the account is a known address. */
export function gmailUrl(account: string | null | undefined, messageId: string): string {
  const id = encodeURIComponent(messageId.trim())
  const email = typeof account === 'string' ? account.trim() : ''
  return EMAIL_RE.test(email)
    ? `https://mail.google.com/mail/u/${encodeURIComponent(email)}/#all/${id}`
    : `https://mail.google.com/mail/#inbox/${id}`
}

export interface SourceAction {
  kind: 'gmail' | 'session' | 'run'
  /** Accessible name and tooltip of the icon-only button. */
  label: string
  icon: string
}

/**
 * The one source action a Need-to-Know card offers, as an icon-only button
 * (U13): open the Gmail message or the session, or (where the surface has a
 * run-history dialog) view the routine's run. Null when nothing opens.
 */
export function sourceAction(source: string, options: { runs?: boolean } = {}): SourceAction | null {
  const value = source.trim()
  if (/^session:\s*\S/i.test(value)) {
    return { kind: 'session', label: 'Open session', icon: 'comment-discussion' }
  }
  if (/^gmail:[^:]*\S[^:]*:\s*\S/i.test(value)) {
    return { kind: 'gmail', label: 'Open in Gmail', icon: 'mail' }
  }
  if (options.runs && /^cron:\s*\S/i.test(value)) {
    return { kind: 'run', label: 'View run', icon: 'clock' }
  }
  return null
}
