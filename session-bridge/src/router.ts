import { createHash } from 'node:crypto'
import type { Config } from './config'
import type { SessionState } from './tmux'
import type { TopicInfo } from './state'
import { tailLines } from './tmux'

export interface SendReceipt { messageIds: number[] }
export interface ApprovalBinding {
  version: 1
  threadId: number
  session: string
  sessionGeneration: string
  promptHash: string
  displayedMessageIds: number[]
  displayedAt: number
  expiresAt: number
}
export type ActionStatus = 'attempting' | 'not_injected' | 'injected' | 'uncertain_action'
export interface ActionOutcome {
  version: 1
  updateId: number
  kind: 'press_key' | 'inject_text'
  session: string
  sessionGeneration: string
  status: ActionStatus
  recordedAt: number
}

export interface RouterDeps {
  config: Config
  topics(): Record<string, TopicInfo>
  classify(session: string): SessionState
  capture(session: string): string | null
  generation?(session: string): string | null
  inject(session: string, text: string): void
  pressKey(session: string, key: string): void
  reply(threadId: number | undefined, text: string): Promise<SendReceipt | void>
  setPending(session: string, topicId: number, dir?: string, remaining?: number): void
  approval?(threadId: number): ApprovalBinding | undefined
  saveApproval?(threadId: number, binding: ApprovalBinding | undefined): void
  actionOutcome?(updateId: number): ActionOutcome | undefined
  recordAction?(updateId: number, outcome: ActionOutcome): void
  now?(): number
}

const APPROVAL_HINT = 'say approve, deny, or an option number (1-9)'
const ARM_WINDOW_MS = 10 * 60 * 1000
const fallbackApprovals = new Map<number, ApprovalBinding>()

export function blockedPrompt(session: string, pane: string): string {
  return `🔴 ${session} is waiting on an approval:\n\n${tailLines(pane, 15)}\n\n${APPROVAL_HINT}`
}

function digest(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

function now(deps: RouterDeps): number { return deps.now?.() ?? Date.now() }
function generation(deps: RouterDeps, session: string): string | null {
  return deps.generation ? deps.generation(session) : session
}
function getApproval(deps: RouterDeps, threadId: number): ApprovalBinding | undefined {
  return deps.approval?.(threadId) ?? fallbackApprovals.get(threadId)
}
function saveApproval(deps: RouterDeps, threadId: number, value: ApprovalBinding | undefined): void {
  if (deps.saveApproval) deps.saveApproval(threadId, value)
  else if (value) fallbackApprovals.set(threadId, value)
  else fallbackApprovals.delete(threadId)
}

// Kept for source compatibility. A topic cannot be armed without a captured
// prompt, live tmux generation, and accepted send receipt.
export function armTopic(_threadId: number): void {}

export function resetArmedForTests(): void { fallbackApprovals.clear() }

export async function displayBlockedPrompt(
  session: string, threadId: number, deps: RouterDeps, prefix = '',
): Promise<ApprovalBinding | undefined> {
  const pane = deps.capture(session)
  const gen = generation(deps, session)
  if (pane === null || gen === null || deps.classify(session) !== 'BLOCKED') {
    saveApproval(deps, threadId, undefined)
    return undefined
  }
  const prompt = blockedPrompt(session, pane)
  const receipt = await deps.reply(threadId, prefix + prompt)
  const at = now(deps)
  const binding: ApprovalBinding = {
    version: 1, threadId, session, sessionGeneration: gen,
    promptHash: digest(prompt), displayedMessageIds: receipt?.messageIds ?? [],
    displayedAt: at, expiresAt: at + ARM_WINDOW_MS,
  }
  saveApproval(deps, threadId, binding)
  return binding
}

function currentBindingMatches(binding: ApprovalBinding, session: string, deps: RouterDeps): boolean {
  if (binding.session !== session || binding.expiresAt < now(deps)) return false
  const pane = deps.capture(session)
  const gen = generation(deps, session)
  if (pane === null || gen === null) return false
  return binding.sessionGeneration === gen && binding.promptHash === digest(blockedPrompt(session, pane))
}

function record(
  deps: RouterDeps, updateId: number, kind: ActionOutcome['kind'], session: string,
  sessionGeneration: string, status: ActionStatus,
): void {
  deps.recordAction?.(updateId, {
    version: 1, updateId, kind, session, sessionGeneration, status, recordedAt: now(deps),
  })
}

export async function handleUpdate(update: any, deps: RouterDeps): Promise<void> {
  const msg = update.message
  if (!msg || typeof msg.text !== 'string') return
  if (msg.chat?.id !== deps.config.groupId || msg.from?.id !== deps.config.allowedUserId) return

  const existing = deps.actionOutcome?.(update.update_id)
  if (existing) {
    if (existing.status === 'attempting') {
      record(deps, update.update_id, existing.kind, existing.session, existing.sessionGeneration, 'uncertain_action')
    }
    return
  }

  const threadId: number | undefined = msg.message_thread_id
  if (threadId === undefined) {
    await deps.reply(undefined, 'talk to a session in its own tab — this General tab is not routed')
    return
  }
  const entry = Object.entries(deps.topics()).find(([, t]) => t.topicId === threadId && t.status === 'open')
  if (!entry) { await deps.reply(threadId, 'no live session for this tab'); return }
  const [session] = entry
  const sessionState = deps.classify(session)
  if (sessionState === 'GONE') { await deps.reply(threadId, 'session ended — tab will be archived'); return }

  if (sessionState === 'BLOCKED') {
    const word = msg.text.trim().toLowerCase()
    const key = word === 'approve' ? '1' : word === 'deny' ? 'Escape' : /^[1-9]$/.test(word) ? word : null
    const binding = getApproval(deps, threadId)
    if (key === null || !binding || !currentBindingMatches(binding, session, deps)) {
      saveApproval(deps, threadId, undefined)
      await displayBlockedPrompt(session, threadId, deps, binding ? '⚠ The pending action changed. Review this fresh approval:\n\n' : '')
      return
    }
    const gen = generation(deps, session)
    if (gen === null) { saveApproval(deps, threadId, undefined); return }
    record(deps, update.update_id, 'press_key', session, gen, 'attempting')
    if (!currentBindingMatches(binding, session, deps)) {
      record(deps, update.update_id, 'press_key', session, gen, 'not_injected')
      saveApproval(deps, threadId, undefined)
      await displayBlockedPrompt(session, threadId, deps, '⚠ The pending action changed. Review this fresh approval:\n\n')
      return
    }
    try {
      deps.pressKey(session, key)
    } catch (e) {
      record(deps, update.update_id, 'press_key', session, gen, 'uncertain_action')
      await deps.reply(threadId, `⚠ keypress outcome is uncertain for ${session}; inspect the session and journalctl --user -u session-bridge before any retry`)
      return
    }
    deps.setPending(session, threadId, undefined, 1)
    record(deps, update.update_id, 'press_key', session, gen, 'injected')
    saveApproval(deps, threadId, undefined)
    await Bun.sleep(500)
    await deps.reply(threadId, `✅ pressed — current screen:\n\n${tailLines(deps.capture(session) ?? '', 8)}`)
    return
  }

  const gen = generation(deps, session)
  if (gen === null) {
    record(deps, update.update_id, 'inject_text', session, '', 'not_injected')
    await deps.reply(threadId, `⚠ delivery failed — ${session} was replaced or ended`)
    return
  }
  const text = '[message from Rex via this session\'s Telegram tab — answer normally in this chat; ' +
    'the bridge relays your reply back to the tab. Never send Telegram messages yourself.]\n' +
    msg.text.replace(/\x1b/g, '')
  record(deps, update.update_id, 'inject_text', session, gen, 'attempting')
  try {
    deps.inject(session, text)
  } catch (e) {
    record(deps, update.update_id, 'inject_text', session, gen, 'uncertain_action')
    console.error(`session-bridge: inject uncertain for ${session}: ${e}`)
    await deps.reply(threadId, `⚠ delivery failed with an uncertain outcome for ${session}; inspect the session and journalctl --user -u session-bridge before any retry`)
    return
  }
  deps.setPending(session, threadId, undefined, sessionState === 'WORKING' ? 2 : 1)
  record(deps, update.update_id, 'inject_text', session, gen, 'injected')
  await deps.reply(threadId, sessionState === 'WORKING'
    ? '⏳ session is working — queued. The next answer may belong to its current task; yours follows.'
    : '→ delivered')
}
