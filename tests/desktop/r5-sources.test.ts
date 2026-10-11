import { test } from 'node:test'
import assert from 'node:assert/strict'

import { gmailUrl, telegramChipLabel } from '../../src/shared/sources.ts'

test('telegramChipLabel prefers chat_name', () => {
  assert.equal(
    telegramChipLabel({ chatName: 'Weekend Crew', headline: 'Bob: are we still on?', summary: 'Chat: Other' }),
    'Weekend Crew'
  )
  assert.equal(telegramChipLabel({ chatName: '  Book Club  ' }), 'Book Club')
})

test('telegramChipLabel falls back to the headline/summary prefixes', () => {
  assert.equal(telegramChipLabel({ headline: 'Weekend Crew: Saturday plan', summary: '' }), 'Weekend Crew')
  assert.equal(telegramChipLabel({ headline: 'Saturday plan', summary: 'Telegram chat: Book Club — new message' }), 'Book Club')
  assert.equal(telegramChipLabel({ headline: 'Saturday plan', summary: 'Carol Example: can you bring snacks?' }), 'Carol Example')
  assert.equal(telegramChipLabel({ chatName: '', headline: 'Bob: hi' }), 'Bob')
})

test('telegramChipLabel never returns a numeric chat id', () => {
  assert.equal(telegramChipLabel({ chatName: '-100500123', headline: 'Saturday plan', summary: 'No prefix here' }), 'Telegram')
  assert.equal(telegramChipLabel({ headline: '-100500123: new messages', summary: '42: hi' }), 'Telegram')
  assert.equal(telegramChipLabel({ chatName: '1000001', headline: 'Weekend Crew: hi' }), 'Weekend Crew')
  assert.equal(telegramChipLabel({}), 'Telegram')
  for (const label of [
    telegramChipLabel({ chatName: '-5', headline: '-5: x' }),
    telegramChipLabel({ summary: 'Telegram: 12345' })
  ]) {
    assert.doesNotMatch(label, /^[-+]?\d+$/)
  }
})

test('gmailUrl opens the right mailbox when the account is known', () => {
  assert.equal(
    gmailUrl('alice@example.com', '18c0ffee'),
    'https://mail.google.com/mail/u/alice%40example.com/#all/18c0ffee'
  )
  assert.equal(
    gmailUrl(' bob+work@example.com ', 'id/with?chars'),
    'https://mail.google.com/mail/u/bob%2Bwork%40example.com/#all/id%2Fwith%3Fchars'
  )
})

test('gmailUrl falls back to the inbox form without an account', () => {
  assert.equal(gmailUrl(null, '18c0ffee'), 'https://mail.google.com/mail/#inbox/18c0ffee')
  assert.equal(gmailUrl('', '18c0ffee'), 'https://mail.google.com/mail/#inbox/18c0ffee')
  assert.equal(gmailUrl('work', '18c0ffee'), 'https://mail.google.com/mail/#inbox/18c0ffee')
})
