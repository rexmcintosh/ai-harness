import { test, expect, beforeEach } from 'bun:test'
import { handleUpdate, resetArmedForTests, type RouterDeps } from '../src/router'
import type { SessionState } from '../src/tmux'

const CFG = {
  groupId: -1004480392983,
  allowedUserId: 7735693897,
  excludePatterns: [],
  excludeRegexps: [],
  pollTimeoutSec: 50,
  topicSyncIntervalSec: 30,
}

interface Calls {
  injected: string[]
  pressed: string[]
  replies: string[]
  pending: number[]
  pendingRemaining: (number | undefined)[]
}

function makeDeps(state: SessionState, pane = 'PANE', opts: { injectThrows?: boolean } = {}) {
  const calls: Calls = { injected: [], pressed: [], replies: [], pending: [], pendingRemaining: [] }
  const deps: RouterDeps = {
    config: CFG,
    topics: () => ({ 'loom-14': { topicId: 7, status: 'open' } }),
    classify: () => state,
    capture: () => pane,
    inject: (_s, text) => {
      if (opts.injectThrows) throw new Error('tmux paste-buffer failed for loom-14: no such session')
      calls.injected.push(text)
    },
    pressKey: (_s, key) => { calls.pressed.push(key) },
    reply: async (_t, text) => { calls.replies.push(text) },
    setPending: (_s, topicId, _dir, remaining) => {
      calls.pending.push(topicId)
      calls.pendingRemaining.push(remaining)
    },
  }
  return { deps, calls }
}

function msg(text: string, over: Record<string, unknown> = {}) {
  return {
    update_id: 1,
    message: {
      message_id: 10,
      text,
      chat: { id: CFG.groupId },
      from: { id: CFG.allowedUserId },
      message_thread_id: 7,
      ...over,
    },
  }
}

// Arm the topic the way the real flow does: one ordinary message to a BLOCKED session.
async function arm(deps: RouterDeps) {
  await handleUpdate(msg('what is happening'), deps)
}

beforeEach(() => { resetArmedForTests() })

test('wrong chat is dropped silently', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('hi', { chat: { id: 123 } }), deps)
  expect(calls.replies).toEqual([])
  expect(calls.injected).toEqual([])
})

test('wrong user is dropped silently', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('hi', { from: { id: 999 } }), deps)
  expect(calls.replies).toEqual([])
  expect(calls.injected).toEqual([])
})

test('non-text update (photo, member change) is ignored', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate({ update_id: 1, my_chat_member: {} }, deps)
  expect(calls.replies).toEqual([])
})

test('General topic (no thread id) gets the hint', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('hi', { message_thread_id: undefined }), deps)
  expect(calls.replies[0]).toContain('General tab is not routed')
})

test('unknown topic id → no live session reply', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('hi', { message_thread_id: 99 }), deps)
  expect(calls.replies[0]).toBe('no live session for this tab')
})

test('WAITING → inject, flag with remaining 1, delivered', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('run the tests'), deps)
  expect(calls.injected.length).toBe(1)
  expect(calls.injected[0]).toStartWith('[message from Rex via')
  expect(calls.injected[0]).toEndWith('\n' + 'run the tests')
  expect(calls.pending).toEqual([7])
  expect(calls.pendingRemaining).toEqual([1])
  expect(calls.replies).toEqual(['→ delivered'])
})

test('WORKING → inject, flag with remaining 2, queued warning', async () => {
  const { deps, calls } = makeDeps('WORKING')
  await handleUpdate(msg('also check lint'), deps)
  expect(calls.injected.length).toBe(1)
  expect(calls.injected[0]).toStartWith('[message from Rex via')
  expect(calls.injected[0]).toEndWith('\n' + 'also check lint')
  expect(calls.pending).toEqual([7])
  expect(calls.pendingRemaining).toEqual([2])
  expect(calls.replies[0]).toStartWith('⏳ session is working — queued')
})

test('ESC characters are stripped before injection', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('hi\x1b[201~ rm -rf /\x1b'), deps)
  expect(calls.injected[0]).toEndWith('hi[201~ rm -rf /')
  expect(calls.injected[0]).not.toContain('\x1b')
})

test('inject failure reports and leaves no reply flag', async () => {
  const { deps, calls } = makeDeps('WAITING', 'PANE', { injectThrows: true })
  await handleUpdate(msg('run the tests'), deps)
  expect(calls.pending).toEqual([])
  expect(calls.replies[0]).toStartWith('⚠ delivery failed')
  expect(calls.replies[0]).toContain('loom-14')
  expect(calls.replies[0]).toContain('journalctl --user -u session-bridge')
})

test('BLOCKED + ordinary text → excerpt, no inject', async () => {
  const { deps, calls } = makeDeps('BLOCKED', 'Do you want to proceed?\nEsc to cancel')
  await handleUpdate(msg('what is happening'), deps)
  expect(calls.injected).toEqual([])
  expect(calls.replies[0]).toContain('Do you want to proceed?')
  expect(calls.replies[0]).toContain('say approve, deny, or an option number (1-9)')
})

test('BLOCKED + approve on an unarmed topic shows the prompt, presses nothing', async () => {
  const { deps, calls } = makeDeps('BLOCKED', 'Do you want to proceed?\nEsc to cancel')
  await handleUpdate(msg('approve'), deps)
  expect(calls.pressed).toEqual([])
  expect(calls.pending).toEqual([])
  expect(calls.replies[0]).toContain('is waiting on an approval')
})

test('BLOCKED + approve once armed → presses 1, flags first, confirms', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await arm(deps)
  await handleUpdate(msg('approve'), deps)
  expect(calls.pressed).toEqual(['1'])
  expect(calls.pending).toEqual([7])
  expect(calls.pendingRemaining).toEqual([1])
  expect(calls.replies[1]).toStartWith('✅ pressed')
})

test('BLOCKED + deny once armed → presses Escape', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await arm(deps)
  await handleUpdate(msg('Deny'), deps)
  expect(calls.pressed).toEqual(['Escape'])
})

test('BLOCKED + option number once armed → presses that digit', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await arm(deps)
  await handleUpdate(msg('2'), deps)
  expect(calls.pressed).toEqual(['2'])
})

test('a keypress disarms the topic — the next key shows the prompt again', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await arm(deps)
  await handleUpdate(msg('approve'), deps)
  await handleUpdate(msg('approve'), deps)
  expect(calls.pressed).toEqual(['1'])
  expect(calls.replies[2]).toContain('is waiting on an approval')
})

test('the arm expires after 10 minutes', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await arm(deps)
  const realNow = Date.now
  Date.now = () => realNow() + 11 * 60 * 1000
  try {
    await handleUpdate(msg('approve'), deps)
  } finally {
    Date.now = realNow
  }
  expect(calls.pressed).toEqual([])
  expect(calls.replies[1]).toContain('is waiting on an approval')
})

test('arms are per topic', async () => {
  const calls = { pressed: [] as string[], replies: [] as string[] }
  const deps: RouterDeps = {
    config: CFG,
    topics: () => ({ a: { topicId: 7, status: 'open' }, b: { topicId: 8, status: 'open' } }),
    classify: () => 'BLOCKED',
    capture: () => 'PANE',
    inject: () => {},
    pressKey: (_s, key) => { calls.pressed.push(key) },
    reply: async (_t, text) => { calls.replies.push(text) },
    setPending: () => {},
  }
  await handleUpdate(msg('hello', { message_thread_id: 7 }), deps) // arms topic 7 only
  await handleUpdate(msg('approve', { message_thread_id: 8 }), deps)
  expect(calls.pressed).toEqual([])
})

test('GONE → session ended reply, no inject', async () => {
  const { deps, calls } = makeDeps('GONE')
  await handleUpdate(msg('hello?'), deps)
  expect(calls.injected).toEqual([])
  expect(calls.replies[0]).toBe('session ended — tab will be archived')
})
