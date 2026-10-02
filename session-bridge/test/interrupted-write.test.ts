import { expect, test } from 'bun:test'
import { handleUpdate, type ActionOutcome, type ApprovalBinding, type RouterDeps } from '../src/router'

function setup(failOn: 'attempting' | 'injected') {
  let approval: ApprovalBinding | undefined
  let outcome: ActionOutcome | undefined
  let armed = false
  const pressed: string[] = []
  const deps: RouterDeps = {
    config: { groupId: -1, allowedUserId: 2, excludePatterns: [], excludeRegexps: [], pollTimeoutSec: 1, topicSyncIntervalSec: 1 },
    topics: () => ({ demo: { topicId: 3, status: 'open' } }), classify: () => 'BLOCKED',
    capture: () => 'Do you want to proceed?\nEsc to cancel', generation: () => '$1:@2:3',
    inject: () => {}, pressKey: (_s, key) => pressed.push(key), setPending: () => {},
    reply: async () => ({ messageIds: [9] }), approval: () => approval,
    saveApproval: (_id, value) => { approval = value; armed = true },
    actionOutcome: () => outcome,
    recordAction: (_id, value) => {
      if (value.status === failOn) throw new Error('state write failed')
      outcome = value
    },
    now: () => 100,
  }
  const message = (text: string, id: number) => ({ update_id: id, message: { text, chat: { id: -1 }, from: { id: 2 }, message_thread_id: 3 } })
  return { deps, pressed, message, armed: () => armed, outcome: () => outcome }
}

test('failed attempting write stops before tmux invocation', async () => {
  const h = setup('attempting')
  await h.deps.reply(3, 'fixture')
  await handleUpdate(h.message('show', 1), h.deps)
  await expect(handleUpdate(h.message('approve', 2), h.deps)).rejects.toThrow('state write failed')
  expect(h.pressed).toEqual([])
})

test('failed terminal write leaves attempting so duplicate becomes uncertain without replay', async () => {
  const h = setup('injected')
  await handleUpdate(h.message('show', 1), h.deps)
  await expect(handleUpdate(h.message('approve', 2), h.deps)).rejects.toThrow('state write failed')
  expect(h.pressed).toEqual(['1'])
  expect(h.outcome()?.status).toBe('attempting')
  h.deps.recordAction = (_id, value) => { (h as any)._last = value }
  await handleUpdate(h.message('approve', 2), h.deps)
  expect(h.pressed).toEqual(['1'])
  expect((h as any)._last.status).toBe('uncertain_action')
})
