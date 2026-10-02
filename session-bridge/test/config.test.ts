import { test, expect } from 'bun:test'
import { mkdtempSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadConfig, loadToken } from '../src/config'

function tmp(): string { return mkdtempSync(join(tmpdir(), 'sb-')) }

test('loadConfig parses config.json', () => {
  const d = tmp()
  writeFileSync(join(d, 'config.json'), JSON.stringify({
    groupId: -1004480392983, allowedUserId: 7735693897,
    excludePatterns: ['^cai/'], pollTimeoutSec: 50, topicSyncIntervalSec: 30,
  }))
  const c = loadConfig(join(d, 'config.json'))
  expect(c.groupId).toBe(-1004480392983)
  expect(c.excludePatterns).toEqual(['^cai/'])
  expect(c.excludeRegexps.length).toBe(1)
  expect(c.excludeRegexps[0].test('cai/x')).toBe(true)
  expect(c.excludeRegexps[0].test('loom-14')).toBe(false)
})

test('loadConfig throws a plain error on missing file', () => {
  expect(() => loadConfig('/nonexistent/config.json')).toThrow(/config/)
})

function writeConfig(over: Record<string, unknown>): string {
  const d = tmp()
  const p = join(d, 'config.json')
  writeFileSync(p, JSON.stringify({
    groupId: -1, allowedUserId: 2, excludePatterns: [], ...over,
  }))
  return p
}

test('loadConfig rejects a non-numeric groupId, naming the field', () => {
  expect(() => loadConfig(writeConfig({ groupId: '-1004480392983' }))).toThrow(/groupId/)
})

test('loadConfig rejects a non-numeric allowedUserId, naming the field', () => {
  expect(() => loadConfig(writeConfig({ allowedUserId: null }))).toThrow(/allowedUserId/)
})

test('loadConfig rejects excludePatterns that is not an array', () => {
  expect(() => loadConfig(writeConfig({ excludePatterns: '^cai/' }))).toThrow(/excludePatterns/)
})

test('loadConfig rejects a non-string exclude pattern', () => {
  expect(() => loadConfig(writeConfig({ excludePatterns: [7] }))).toThrow(/excludePatterns/)
})

test('loadConfig rejects an uncompilable exclude pattern', () => {
  expect(() => loadConfig(writeConfig({ excludePatterns: ['^cai/('] }))).toThrow(/excludePatterns/)
})

test('loadConfig rejects invalid JSON', () => {
  const d = tmp()
  writeFileSync(join(d, 'config.json'), '{oops')
  expect(() => loadConfig(join(d, 'config.json'))).toThrow(/valid JSON/)
})

test('loadConfig defaults the poll and sync intervals', () => {
  const c = loadConfig(writeConfig({}))
  expect(c.pollTimeoutSec).toBe(50)
  expect(c.topicSyncIntervalSec).toBe(30)
})

test('loadToken parses TELEGRAM_BOT_TOKEN= line', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'TELEGRAM_BOT_TOKEN=123:abc\n')
  expect(loadToken(join(d, '.env'))).toBe('123:abc')
})

test('loadToken tolerates an export prefix', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'export TELEGRAM_BOT_TOKEN=123:abc\n')
  expect(loadToken(join(d, '.env'))).toBe('123:abc')
})

test('loadToken strips matching quotes', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'TELEGRAM_BOT_TOKEN="123:abc"\n')
  expect(loadToken(join(d, '.env'))).toBe('123:abc')
  const e = tmp()
  writeFileSync(join(e, '.env'), "export TELEGRAM_BOT_TOKEN='123:abc'\n")
  expect(loadToken(join(e, '.env'))).toBe('123:abc')
})

test('loadToken leaves unmatched quotes alone', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'TELEGRAM_BOT_TOKEN="123:abc\n')
  expect(loadToken(join(d, '.env'))).toBe('"123:abc')
})

test('loadToken throws when the variable is absent', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'OTHER=x\n')
  expect(() => loadToken(join(d, '.env'))).toThrow(/TELEGRAM_BOT_TOKEN/)
})

test('loadToken throws when the value is empty', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'TELEGRAM_BOT_TOKEN=\n')
  expect(() => loadToken(join(d, '.env'))).toThrow(/empty/)
})
