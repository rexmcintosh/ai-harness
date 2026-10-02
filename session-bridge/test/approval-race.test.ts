import { expect, test } from 'bun:test'
import { handleUpdate, type ApprovalBinding, type RouterDeps } from '../src/router'

test('prompt is rechecked after attempting is durable and before keypress', async () => {
  let pane = 'Do you want to proceed with action A?\nEsc to cancel'
  let approval: ApprovalBinding | undefined
  const pressed: string[] = []
  const outcomes: string[] = []
  const deps: RouterDeps = {
    config: { groupId: -1, allowedUserId: 2, excludePatterns: [], excludeRegexps: [], pollTimeoutSec: 1, topicSyncIntervalSec: 1 },
    topics: () => ({ demo: { topicId: 3, status: 'open' } }),
    classify: () => 'BLOCKED', capture: () => pane, generation: () => '$1:@2:3',
    inject: () => {}, pressKey: (_s, key) => pressed.push(key),
    reply: async () => ({ messageIds: [9] }), setPending: () => {},
    approval: () => approval, saveApproval: (_id, value) => { approval = value },
    actionOutcome: () => undefined,
    recordAction: (_id, value) => {
      outcomes.push(value.status)
      if (value.status === 'attempting') pane = 'Do you want to proceed with action B?\nEsc to cancel'
    },
    now: () => 100,
  }
  const message = (text: string, id: number) => ({ update_id: id, message: { text, chat: { id: -1 }, from: { id: 2 }, message_thread_id: 3 } })
  await handleUpdate(message('show', 1), deps)
  await handleUpdate(message('approve', 2), deps)
  expect(pressed).toEqual([])
  expect(outcomes).toEqual(['attempting', 'not_injected'])
})
