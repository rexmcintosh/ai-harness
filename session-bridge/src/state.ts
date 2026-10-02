import { closeSync, fsyncSync, openSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { STATE_DIR } from './config'
import type { ActionOutcome, ApprovalBinding } from './router'

export interface TopicInfo { topicId: number; status: 'open' | 'closed' }
export interface BridgeState {
  version: 2
  offset: number
  topics: Record<string, TopicInfo>
  approvals: Record<string, ApprovalBinding>
  actions: Record<string, ActionOutcome>
}

function fresh(): BridgeState { return { version: 2, offset: 0, topics: {}, approvals: {}, actions: {} } }

export function loadState(dir = STATE_DIR): BridgeState {
  const path = join(dir, 'state.json')
  if (!existsSync(path)) return fresh()
  let s: any
  try { s = JSON.parse(readFileSync(path, 'utf8')) }
  catch (e) { throw new Error(`session-bridge state file is corrupt at ${path} — fix or remove it: ${e}`) }
  if (s === null || typeof s !== 'object' || Array.isArray(s)) {
    throw new Error(`session-bridge state file is corrupt at ${path} — not a JSON object`)
  }
  const state: BridgeState = {
    version: 2,
    offset: Number.isInteger(s.offset) ? s.offset : 0,
    topics: s.topics && typeof s.topics === 'object' ? s.topics : {},
    approvals: s.approvals && typeof s.approvals === 'object' ? s.approvals : {},
    actions: s.actions && typeof s.actions === 'object' ? s.actions : {},
  }
  let recovered = false
  for (const action of Object.values(state.actions)) {
    if (action.status === 'attempting') {
      action.status = 'uncertain_action'
      action.recordedAt = Date.now()
      recovered = true
    }
  }
  if (recovered) saveState(state, dir)
  return state
}

export function compactActions(s: BridgeState, keep = 256): void {
  const confirmed = Object.values(s.actions)
    .filter(a => (a.status === 'injected' || a.status === 'not_injected') && a.updateId < s.offset)
    .sort((a, b) => b.updateId - a.updateId)
  for (const action of confirmed.slice(keep)) delete s.actions[String(action.updateId)]
  // Uncertain actions remain evidence for the owner; they never expire into permission to retry.
}

export function saveState(s: BridgeState, dir = STATE_DIR): void {
  compactActions(s)
  mkdirSync(dir, { recursive: true })
  const tmp = join(dir, 'state.json.tmp')
  const fd = openSync(tmp, 'w', 0o600)
  try { writeFileSync(fd, JSON.stringify(s, null, 2)); fsyncSync(fd) } finally { closeSync(fd) }
  renameSync(tmp, join(dir, 'state.json'))
  const directory = openSync(dir, 'r')
  try { fsyncSync(directory) } finally { closeSync(directory) }
}

export function sanitizeSession(name: string): string {
  const safe = name.replace(/[^a-zA-Z0-9._-]/g, '_')
  return /^\.+$/.test(safe) ? '_' : safe
}

export function pendingPath(session: string, dir = STATE_DIR): string {
  return join(dir, 'pending', sanitizeSession(session))
}

export function pendingIsFresh(session: string, now = Date.now(), dir = STATE_DIR, maxAgeMs = 60 * 60 * 1000): boolean {
  try {
    const flag = JSON.parse(readFileSync(pendingPath(session, dir), 'utf8'))
    return typeof flag.ts === 'number' && now >= flag.ts && now - flag.ts <= maxAgeMs
  } catch {
    return false
  }
}
export function setPending(session: string, topicId: number, dir = STATE_DIR, remaining = 1): void {
  mkdirSync(join(dir, 'pending'), { recursive: true })
  writeFileSync(pendingPath(session, dir), JSON.stringify({ topicId, ts: Date.now(), remaining }))
}
