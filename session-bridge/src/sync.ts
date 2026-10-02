import type { TopicInfo } from './state'

export interface SyncAction {
  kind: 'create' | 'close'
  session: string
  topicId?: number
}

export function planSync(
  live: string[],
  topics: Record<string, TopicInfo>,
  excludeRegexps: RegExp[],
): SyncAction[] {
  const watched = live.filter(s => !excludeRegexps.some(r => r.test(s)))
  const actions: SyncAction[] = []
  for (const session of watched) {
    const t = topics[session]
    if (!t || t.status === 'closed') actions.push({ kind: 'create', session })
  }
  for (const [session, t] of Object.entries(topics)) {
    if (t.status === 'open' && !watched.includes(session)) {
      actions.push({ kind: 'close', session, topicId: t.topicId })
    }
  }
  return actions
}

// A single failed `tmux ls` returns an empty session list, which would otherwise
// archive every tab at once. Require a session to be missing on two consecutive syncs
// before its topic is closed. `nextMissing` is the set to carry into the next sync.
export function debounceCloses(
  actions: SyncAction[],
  prevMissing: Set<string>,
): { execute: SyncAction[]; nextMissing: Set<string> } {
  const missingNow = new Set<string>()
  const execute: SyncAction[] = []
  for (const a of actions) {
    if (a.kind !== 'close') {
      execute.push(a)
      continue
    }
    missingNow.add(a.session)
    if (prevMissing.has(a.session)) execute.push(a)
  }
  return { execute, nextMissing: missingNow }
}
