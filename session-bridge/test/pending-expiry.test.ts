import { expect, test } from 'bun:test'
import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { pendingIsFresh, pendingPath } from '../src/state'

test('pending flags older than the hook limit are not watchable', () => {
  const d = mkdtempSync(join(tmpdir(), 'sb-pending-'))
  mkdirSync(join(d, 'pending'))
  writeFileSync(pendingPath('demo', d), JSON.stringify({ topicId: 3, ts: 1_000, remaining: 1 }))
  expect(pendingIsFresh('demo', 1_000 + 60 * 60 * 1000 + 1, d)).toBe(false)
  expect(pendingIsFresh('demo', 1_000 + 30 * 60 * 1000, d)).toBe(true)
})
