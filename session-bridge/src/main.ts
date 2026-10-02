import { loadConfig, loadToken } from './config'
import { Telegram } from './telegram'
import { loadState, saveState, setPending, pendingIsFresh } from './state'
import { planSync, debounceCloses } from './sync'
import { listSessions, capturePane, classify, inject, pressKey, sessionGeneration } from './tmux'
import { handleUpdate, displayBlockedPrompt, type RouterDeps } from './router'

const config = loadConfig()
const tg = new Telegram(loadToken())
const state = loadState()
let prevMissing = new Set<string>()

async function runSync(): Promise<void> {
  const actions = planSync(listSessions(), state.topics, config.excludeRegexps)
  const { execute, nextMissing } = debounceCloses(actions, prevMissing)
  prevMissing = nextMissing
  for (const a of execute) {
    if (a.kind === 'create') {
      const topicId = await tg.createTopic(config.groupId, a.session)
      state.topics[a.session] = { topicId, status: 'open' }
      saveState(state)
      console.error(`session-bridge: topic created for ${a.session} (${topicId})`)
    } else {
      await tg.renameAndClose(config.groupId, a.topicId!, `✖ ${a.session}`)
      state.topics[a.session].status = 'closed'
      saveState(state)
      console.error(`session-bridge: topic archived for ${a.session}`)
    }
  }
}

let syncing = false
async function syncNow(label: string): Promise<void> {
  if (syncing) return
  syncing = true
  try { await runSync() }
  catch (e) { console.error(`session-bridge: ${label} error: ${e}`) }
  finally { syncing = false }
}

const deps: RouterDeps = {
  config,
  topics: () => state.topics,
  classify: s => classify(capturePane(s)),
  capture: capturePane,
  generation: sessionGeneration,
  inject,
  pressKey,
  reply: (threadId, text) => tg.send(config.groupId, threadId, text),
  setPending,
  approval: threadId => state.approvals[String(threadId)],
  saveApproval: (threadId, binding) => {
    if (binding) state.approvals[String(threadId)] = binding
    else delete state.approvals[String(threadId)]
    saveState(state)
  },
  actionOutcome: updateId => state.actions[String(updateId)],
  recordAction: (updateId, outcome) => {
    state.actions[String(updateId)] = outcome
    saveState(state)
  },
}

setInterval(() => { void syncNow('sync') }, config.topicSyncIntervalSec * 1000)

// Add suppression only after Telegram accepts the exact prompt. Failed sends
// remain eligible on the next existing tick. Old pending flags do not arm.
const blockedNotified = new Set<string>()
let watching = false
setInterval(() => {
  if (watching) return
  watching = true
  void (async () => {
    try {
      for (const [session, topic] of Object.entries(state.topics)) {
        if (topic.status !== 'open' || !pendingIsFresh(session)) continue
        const pane = capturePane(session)
        if (classify(pane) !== 'BLOCKED') {
          blockedNotified.delete(session)
          continue
        }
        if (blockedNotified.has(session)) continue
        const binding = await displayBlockedPrompt(session, topic.topicId, deps)
        if (binding) {
          blockedNotified.add(session)
          console.error(`session-bridge: posted blocked prompt for ${session}`)
        }
      }
    } catch (e) {
      console.error(`session-bridge: blocked-watch error: ${e}`)
    } finally {
      watching = false
    }
  })()
}, 5000)

console.error('session-bridge: starting')
await syncNow('initial sync')

while (true) {
  try {
    const updates = await tg.getUpdates(state.offset, config.pollTimeoutSec)
    for (const update of updates) {
      if (update.update_id < state.offset) continue
      // Router writes attempting before tmux. Only a fully handled update may
      // advance the offset. A failed state write stops later updates.
      await handleUpdate(update, deps)
      state.offset = Math.max(state.offset, update.update_id + 1)
      saveState(state)
    }
    if (updates.length > 0) await syncNow('post-update sync')
  } catch (e) {
    console.error(`session-bridge: poll/handler error: ${e}`)
    await Bun.sleep(5000)
  }
}
