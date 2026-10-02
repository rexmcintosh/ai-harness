import { test, expect } from 'bun:test'
import { mkdtempSync, readFileSync, existsSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadState, saveState, setPending, pendingPath, sanitizeSession } from '../src/state'

function tmp(): string { return mkdtempSync(join(tmpdir(), 'sb-state-')) }

test('loadState returns empty state when file is absent', () => {
  const s = loadState(tmp())
  expect(s).toEqual({ version: 2, offset: 0, topics: {}, approvals: {}, actions: {} })
})

test('saveState/loadState round-trips', () => {
  const d = tmp()
  saveState({ version: 2, offset: 42, approvals: {}, actions: {}, topics: { 'loom-14': { topicId: 7, status: 'open' } } }, d)
  expect(loadState(d)).toEqual({ version: 2, offset: 42, approvals: {}, actions: {}, topics: { 'loom-14': { topicId: 7, status: 'open' } } })
})

test('setPending writes topicId, a timestamp and remaining=1 by default', () => {
  const d = tmp()
  setPending('loom-14', 7, d)
  const flag = JSON.parse(readFileSync(pendingPath('loom-14', d), 'utf8'))
  expect(flag.topicId).toBe(7)
  expect(typeof flag.ts).toBe('number')
  expect(flag.remaining).toBe(1)
})

test('setPending records an explicit remaining count', () => {
  const d = tmp()
  setPending('loom-14', 7, d, 2)
  expect(JSON.parse(readFileSync(pendingPath('loom-14', d), 'utf8')).remaining).toBe(2)
})

test('sanitizeSession replaces path separators', () => {
  expect(sanitizeSession('cai/foo')).toBe('cai_foo')
})

test('sanitizeSession never yields a dots-only name', () => {
  expect(sanitizeSession('.')).toBe('_')
  expect(sanitizeSession('..')).toBe('_')
  expect(sanitizeSession('...')).toBe('_')
  expect(sanitizeSession('.hidden')).toBe('.hidden')
})

test('saveState leaves no temp file behind', () => {
  const d = tmp()
  saveState({ version: 2, offset: 1, topics: {}, approvals: {}, actions: {} }, d)
  expect(existsSync(join(d, 'state.json'))).toBe(true)
  expect(existsSync(join(d, 'state.json.tmp'))).toBe(false)
  expect(loadState(d).offset).toBe(1)
})

test('loadState throws when an existing state file is corrupt', () => {
  const d = tmp()
  writeFileSync(join(d, 'state.json'), '{not json')
  expect(() => loadState(d)).toThrow(/corrupt/)
})

test('loadState throws when the state file is not an object', () => {
  const d = tmp()
  writeFileSync(join(d, 'state.json'), 'null')
  expect(() => loadState(d)).toThrow(/corrupt/)
})
