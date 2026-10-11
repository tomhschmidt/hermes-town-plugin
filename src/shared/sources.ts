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
