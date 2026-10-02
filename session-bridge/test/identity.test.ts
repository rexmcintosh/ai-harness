import { beforeEach, expect, test } from 'bun:test'
import { handleUpdate, resetArmedForTests, type ApprovalBinding, type RouterDeps } from '../src/router'

const CFG = {
  groupId: -1001, allowedUserId: 7, excludePatterns: [], excludeRegexps: [],
  pollTimeoutSec: 50, topicSyncIntervalSec: 30,
}

function update(text: string, id = 41) {
  return { update_id: id, message: { message_id: 8, text, chat: { id: -1001 }, from: { id: 7 }, message_thread_id: 3 } }
}

function harness() {
  let pane = 'Do you want to proceed with action A?\nEsc to cancel'
  let generation = '$1:@2:100'
  let approval: ApprovalBinding | undefined
  const pressed: string[] = []
  const injected: string[] = []
  const outcomes: Record<string, any> = {}
  const replies: string[] = []
  let failReply = false
  let failInject = false
  const deps: RouterDeps = {
    config: CFG,
    topics: () => ({ demo: { topicId: 3, status: 'open' } }),
    classify: () => 'BLOCKED',
    capture: () => pane,
    generation: () => generation,
    inject: (_s, text) => { injected.push(text); if (failInject) throw new Error('after invocation') },
    pressKey: (_s, key) => { pressed.push(key) },
    reply: async (_t, text) => {
      replies.push(text)
      if (failReply) throw new Error('send rejected')
      return { messageIds: [90 + replies.length] }
    },
    setPending: () => {},
    approval: () => approval,
    saveApproval: (_threadId, value) => { approval = value },
    actionOutcome: id => outcomes[String(id)],
    recordAction: (id, value) => { outcomes[String(id)] = value },
    now: () => 1_000,
  }
  return {
    deps, pressed, injected, replies, outcomes,
    setPane: (v: string) => { pane = v },
    setGeneration: (v: string) => { generation = v },
    setReplyFailure: (v: boolean) => { failReply = v },
    setInjectFailure: (v: boolean) => { failInject = v },
    getApproval: () => approval,
  }
}

beforeEach(resetArmedForTests)

test('a failed prompt send never arms approval', async () => {
  const h = harness()
  h.setReplyFailure(true)
  await expect(handleUpdate(update('approve'), h.deps)).rejects.toThrow('send rejected')
  expect(h.getApproval()).toBeUndefined()
  h.setReplyFailure(false)
  await handleUpdate(update('approve', 42), h.deps)
  expect(h.pressed).toEqual([])
})

test('changed prompt invalidates an accepted approval display', async () => {
  const h = harness()
  await handleUpdate(update('show me', 1), h.deps)
  h.setPane('Do you want to proceed with action B?\nEsc to cancel')
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.pressed).toEqual([])
  expect(h.replies.at(-1)).toContain('fresh approval')
})

test('reused session name with a new tmux generation invalidates approval', async () => {
  const h = harness()
  await handleUpdate(update('show me', 1), h.deps)
  h.setGeneration('$3:@8:200')
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.pressed).toEqual([])
})

test('unchanged accepted prompt presses the selected key once', async () => {
  const h = harness()
  await handleUpdate(update('show me', 1), h.deps)
  await handleUpdate(update('approve', 2), h.deps)
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.pressed).toEqual(['1'])
  expect(h.outcomes['2'].status).toBe('injected')
})

test('an error after tmux invocation is uncertain and a duplicate is not replayed', async () => {
  const h = harness()
  await handleUpdate(update('show me', 1), h.deps)
  h.setInjectFailure(true)
  h.deps.pressKey = (_s, key) => { h.pressed.push(key); throw new Error('tmux result unknown') }
  await handleUpdate(update('approve', 2), h.deps)
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.pressed).toEqual(['1'])
  expect(h.outcomes['2'].status).toBe('uncertain_action')
})

test('an unavailable tmux generation cannot arm a prompt', async () => {
  const h = harness()
  h.deps.generation = () => null
  await handleUpdate(update('show me', 1), h.deps)
  expect(h.getApproval()).toBeUndefined()
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.pressed).toEqual([])
})

test('failed relay persistence cannot record a completed action or replay its keypress', async () => {
  const h = harness()
  await handleUpdate(update('show me', 1), h.deps)
  h.deps.setPending = () => { throw new Error('disk full') }
  await expect(handleUpdate(update('approve', 2), h.deps)).rejects.toThrow('disk full')
  expect(h.outcomes['2'].status).toBe('attempting')
  await handleUpdate(update('approve', 2), h.deps)
  expect(h.outcomes['2'].status).toBe('uncertain_action')
  expect(h.pressed).toEqual(['1'])
})
