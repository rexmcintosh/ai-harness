import { expect, test } from 'bun:test'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { compactActions, loadState, saveState } from '../src/state'

function tmp(): string { return mkdtempSync(join(tmpdir(), 'sb-action-state-')) }

test('versioned approval and inbound outcomes persist across restart', () => {
  const d = tmp()
  const state: any = {
    version: 2, offset: 5, topics: {},
    approvals: { '3': { version: 1, threadId: 3, session: 'demo', sessionGeneration: '$1:@2:100', promptHash: 'abc', displayedMessageIds: [9], displayedAt: 1, expiresAt: 2 } },
    actions: { '4': { version: 1, updateId: 4, kind: 'press_key', session: 'demo', sessionGeneration: '$1:@2:100', status: 'injected', recordedAt: 1 } },
  }
  saveState(state, d)
  expect(loadState(d)).toEqual(state)
})

test('restart converts an interrupted attempting action to uncertain before polling', () => {
  const d = tmp()
  writeFileSync(join(d, 'state.json'), JSON.stringify({
    version: 2, offset: 5, topics: {}, approvals: {},
    actions: { '6': { version: 1, updateId: 6, kind: 'inject_text', session: 'demo', sessionGeneration: '$1:@2:100', status: 'attempting', recordedAt: 1 } },
  }))
  const loaded: any = loadState(d)
  expect(loaded.actions['6'].status).toBe('uncertain_action')
  expect(loaded.offset).toBe(5)
})

test('old state gains empty identity records without losing topics', () => {
  const d = tmp()
  writeFileSync(join(d, 'state.json'), JSON.stringify({ offset: 2, topics: { demo: { topicId: 3, status: 'open' } } }))
  expect(loadState(d)).toEqual({ version: 2, offset: 2, topics: { demo: { topicId: 3, status: 'open' } }, approvals: {}, actions: {} })
})


test('confirmed history stays bounded while unresolved action evidence remains', () => {
  const state = loadState(mkdtempSync(join(tmpdir(), 'sb-compact-')))
  state.offset = 5000
  for (let i = 1; i <= 4096; i++) state.actions[String(i)] = {
    version: 1, updateId: i, kind: 'press_key', session: 'demo', sessionGeneration: 'g',
    status: i === 1 ? 'uncertain_action' : 'injected', recordedAt: 1,
  }
  compactActions(state)
  expect(Object.keys(state.actions).length).toBe(257)
  expect(state.actions['1'].status).toBe('uncertain_action')
  expect(state.actions['4096'].status).toBe('injected')
})
