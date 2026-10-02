import { test, expect } from 'bun:test'
import { chunkText } from '../src/chunk'

test('short text is a single chunk', () => {
  expect(chunkText('hello', 4096)).toEqual(['hello'])
})

test('splits at paragraph boundary under the limit', () => {
  const a = 'a'.repeat(60)
  const b = 'b'.repeat(60)
  const chunks = chunkText(`${a}\n\n${b}`, 100)
  expect(chunks).toEqual([a, b])
})

test('hard-cuts a single oversized paragraph', () => {
  const chunks = chunkText('x'.repeat(250), 100)
  expect(chunks.length).toBe(3)
  expect(chunks.every(c => c.length <= 100)).toBe(true)
  expect(chunks.join('')).toBe('x'.repeat(250))
})

test('empty text yields no chunks', () => {
  expect(chunkText('', 100)).toEqual([])
})

test('hard cut never splits a surrogate pair', () => {
  // 'x' * 9 then 🔥 (2 units): a limit-10 cut would land inside the emoji.
  const text = `${'x'.repeat(9)}🔥${'y'.repeat(20)}`
  const chunks = chunkText(text, 10)
  expect(chunks[0]).toBe('x'.repeat(9))
  expect(chunks.join('')).toBe(text)
  for (const c of chunks) {
    expect(c.length).toBeLessThanOrEqual(10)
    expect([...c].every(ch => ch.charCodeAt(0) < 0xd800 || ch.length === 2)).toBe(true)
  }
})

test('a run of emoji chunks cleanly', () => {
  const text = '🔥'.repeat(50)
  const chunks = chunkText(text, 9)
  expect(chunks.join('')).toBe(text)
  expect(chunks.every(c => c.length % 2 === 0)).toBe(true)
})
