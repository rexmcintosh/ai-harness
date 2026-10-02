import { test, expect } from 'bun:test'
import { planSync, debounceCloses, type SyncAction } from '../src/sync'

const re = (...p: string[]) => p.map(s => new RegExp(s))

test('new live session → create', () => {
  expect(planSync(['loom-14'], {}, [])).toEqual([{ kind: 'create', session: 'loom-14' }])
})

test('excluded sessions are never created', () => {
  expect(planSync(['cai/x', 'codex-bank-20', 'loom-14'], {}, re('^cai/', '^codex-'))).toEqual([
    { kind: 'create', session: 'loom-14' },
  ])
})

test('open topic with dead session → close', () => {
  expect(planSync([], { 'loom-14': { topicId: 7, status: 'open' } }, [])).toEqual([
    { kind: 'close', session: 'loom-14', topicId: 7 },
  ])
})

test('open topic with live session → no action', () => {
  expect(planSync(['loom-14'], { 'loom-14': { topicId: 7, status: 'open' } }, [])).toEqual([])
})

test('closed topic whose name is live again → fresh create', () => {
  expect(planSync(['loom-14'], { 'loom-14': { topicId: 7, status: 'closed' } }, [])).toEqual([
    { kind: 'create', session: 'loom-14' },
  ])
})

test('closed topic with dead session stays untouched', () => {
  expect(planSync([], { 'loom-14': { topicId: 7, status: 'closed' } }, [])).toEqual([])
})

const close = (session: string): SyncAction => ({ kind: 'close', session, topicId: 7 })
const create = (session: string): SyncAction => ({ kind: 'create', session })

test('debounceCloses defers a close first seen this round', () => {
  const r = debounceCloses([close('loom-14')], new Set())
  expect(r.execute).toEqual([])
  expect([...r.nextMissing]).toEqual(['loom-14'])
})

test('debounceCloses executes a close missing on two consecutive syncs', () => {
  const r = debounceCloses([close('loom-14')], new Set(['loom-14']))
  expect(r.execute).toEqual([close('loom-14')])
  expect([...r.nextMissing]).toEqual(['loom-14'])
})

test('debounceCloses never defers creates', () => {
  const r = debounceCloses([create('a'), close('b')], new Set())
  expect(r.execute).toEqual([create('a')])
})

test('a session that reappears clears its missing mark', () => {
  const first = debounceCloses([close('loom-14')], new Set())
  // Next sync sees it live again → no close action at all.
  const second = debounceCloses([], first.nextMissing)
  expect(second.execute).toEqual([])
  expect(second.nextMissing.size).toBe(0)
})
