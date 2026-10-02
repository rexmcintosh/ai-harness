import { test, expect } from 'bun:test'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { lastAssistantText, readTail } from '../hook/stop-hook'

const L = (o: unknown) => JSON.stringify(o)

function fixture(content: string): string {
  const p = join(mkdtempSync(join(tmpdir(), 'sb-tail-')), 'transcript.jsonl')
  writeFileSync(p, content)
  return p
}

test('extracts text of the last assistant message with text blocks', () => {
  const jsonl = [
    L({ type: 'user', message: { content: [{ type: 'text', text: 'hi' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'text', text: 'first' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'text', text: 'final answer' }] } }),
  ].join('\n')
  expect(lastAssistantText(jsonl)).toBe('final answer')
})

test('joins multiple text blocks with blank lines', () => {
  const jsonl = L({
    type: 'assistant',
    message: { content: [{ type: 'text', text: 'part 1' }, { type: 'text', text: 'part 2' }] },
  })
  expect(lastAssistantText(jsonl)).toBe('part 1\n\npart 2')
})

test('returns null when no assistant text exists', () => {
  const jsonl = L({ type: 'user', message: { content: [] } })
  expect(lastAssistantText(jsonl)).toBeNull()
})

test('skips malformed lines without throwing', () => {
  const jsonl = ['not json{{{', L({ type: 'assistant', message: { content: [{ type: 'text', text: 'ok' }] } })].join('\n')
  expect(lastAssistantText(jsonl)).toBe('ok')
})

test('readTail returns a small file whole', () => {
  const p = fixture('line one\nline two\n')
  expect(readTail(p, 200)).toBe('line one\nline two\n')
})

test('readTail reads only the tail and drops the partial first line', () => {
  const filler = `${'F'.repeat(300)}\n`
  const last = L({ type: 'assistant', message: { content: [{ type: 'text', text: 'final answer' }] } })
  const p = fixture(filler + last + '\n')
  const tail = readTail(p, 200)
  expect(tail.length).toBeLessThanOrEqual(200)
  // The truncated filler line is gone entirely — no partial line survives.
  expect(tail).not.toContain('F')
  expect(tail.split('\n').filter(l => l !== '').every(l => JSON.parse(l) !== null)).toBe(true)
  expect(lastAssistantText(tail)).toBe('final answer')
})

test('readTail keeps a complete line that starts exactly at the cut', () => {
  const a = L({ type: 'assistant', message: { content: [{ type: 'text', text: 'kept' }] } })
  // Pad so that the tail window begins right after the newline before `a`.
  const pad = 'P'.repeat(400 - a.length - 1)
  const p = fixture(`${pad}\n${a}\n`)
  const tail = readTail(p, a.length + 1)
  expect(lastAssistantText(tail)).toBe('kept')
})

test('readTail yields nothing usable when the tail has no line break', () => {
  const p = fixture('X'.repeat(1000))
  expect(readTail(p, 200)).toBe('')
  expect(lastAssistantText(readTail(p, 200))).toBeNull()
})

test('lastAssistantEntry carries the entry timestamp', () => {
  const { lastAssistantEntry } = require('../hook/stop-hook')
  const jsonl = JSON.stringify({
    type: 'assistant',
    timestamp: '2026-08-04T23:37:37.374Z',
    message: { content: [{ type: 'text', text: 'watermelon' }] },
  })
  const e = lastAssistantEntry(jsonl)
  expect(e.text).toBe('watermelon')
  expect(e.ts).toBe(Date.parse('2026-08-04T23:37:37.374Z'))
})

test('missing timestamp counts as fresh (Infinity)', () => {
  const { lastAssistantEntry } = require('../hook/stop-hook')
  const jsonl = JSON.stringify({
    type: 'assistant',
    message: { content: [{ type: 'text', text: 'ok' }] },
  })
  expect(lastAssistantEntry(jsonl).ts).toBe(Infinity)
})
