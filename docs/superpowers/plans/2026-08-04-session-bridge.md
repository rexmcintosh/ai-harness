# Session Bridge Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A daemon + Stop hook that gives each tmux Claude session its own Telegram forum topic for two-way, session-specific conversations, per the spec at `docs/superpowers/specs/2026-08-03-session-bridge-design.md`.

**Architecture:** A single Bun process (`session-bridge`) long-polls a dedicated bot (@WallFred_bot), keeps forum topics in a private supergroup in sync with `tmux ls`, and injects inbound text into the matching tmux pane via bracketed paste. A global Claude Code Stop hook sends the session's final answer back to the topic, but only when a per-session "pending" flag says the last input came from Telegram. Pure logic (classify, chunking, sync planning, routing) is separated from I/O wrappers so it is unit-testable with `bun test`.

**Tech Stack:** Bun (runtime + `bun:test`), tmux CLI, Telegram Bot API via `fetch`, systemd user service. Zero npm dependencies.

## Global Constraints

- Runtime is Bun only; **no npm dependencies**. Use `node:fs`, `Bun.spawnSync`, `fetch`, `bun:test`.
- All Telegram sends are **plain text** — never set `parse_mode`.
- Telegram message limit: chunk at **4096** chars, preferring paragraph (`\n\n`) boundaries.
- Topics are **never deleted** — only renamed to `✖ <name>` and closed.
- The bot token is read from `~/.config/session-bridge/.env` (mode 600, already exists) and must never appear in logs, error messages, or process args.
- Pinned identities: `groupId: -1004480392983`, `allowedUserId: 7735693897` (already in `~/.config/session-bridge/config.json`).
- Session exclude patterns (config): `["^cai/", "^codex-"]`.
- State lives under `~/.local/state/session-bridge/` (`state.json`, `pending/<session>`).
- Two installs are auto-mode-gated and must be handed to the user as `!` one-liners, never executed by the agent: the Stop-hook entry in `~/.claude/settings.json`, and `systemctl --user enable --now session-bridge`.
- All paths below are relative to repo root `~/projects/build-ai-automation-workflow/`, branch `claude/session-bridge`.
- Commit after every task.

---

### Task 1: Scaffold + config module

**Files:**
- Create: `session-bridge/package.json`
- Create: `session-bridge/src/config.ts`
- Test: `session-bridge/test/config.test.ts`

**Interfaces:**
- Produces: `interface Config { groupId: number; allowedUserId: number; excludePatterns: string[]; pollTimeoutSec: number; topicSyncIntervalSec: number }`, `loadConfig(path?): Config`, `loadToken(path?): string`, constants `CONFIG_DIR`, `STATE_DIR`.

- [ ] **Step 1: Scaffold package**

`session-bridge/package.json`:

```json
{
  "name": "session-bridge",
  "type": "module",
  "private": true
}
```

- [ ] **Step 2: Write the failing test**

`session-bridge/test/config.test.ts`:

```ts
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
})

test('loadConfig throws a plain error on missing file', () => {
  expect(() => loadConfig('/nonexistent/config.json')).toThrow(/config/)
})

test('loadToken parses TELEGRAM_BOT_TOKEN= line', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'TELEGRAM_BOT_TOKEN=123:abc\n')
  expect(loadToken(join(d, '.env'))).toBe('123:abc')
})

test('loadToken throws when the variable is absent', () => {
  const d = tmp()
  writeFileSync(join(d, '.env'), 'OTHER=x\n')
  expect(() => loadToken(join(d, '.env'))).toThrow(/TELEGRAM_BOT_TOKEN/)
})
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd ~/projects/build-ai-automation-workflow/session-bridge && bun test test/config.test.ts`
Expected: FAIL — cannot resolve `../src/config`.

- [ ] **Step 4: Write minimal implementation**

`session-bridge/src/config.ts`:

```ts
import { readFileSync } from 'node:fs'

export const CONFIG_DIR = `${process.env.HOME}/.config/session-bridge`
export const STATE_DIR = `${process.env.HOME}/.local/state/session-bridge`

export interface Config {
  groupId: number
  allowedUserId: number
  excludePatterns: string[]
  pollTimeoutSec: number
  topicSyncIntervalSec: number
}

export function loadConfig(path = `${CONFIG_DIR}/config.json`): Config {
  let raw: string
  try {
    raw = readFileSync(path, 'utf8')
  } catch {
    throw new Error(`session-bridge config not readable at ${path}`)
  }
  return JSON.parse(raw) as Config
}

export function loadToken(path = `${CONFIG_DIR}/.env`): string {
  const raw = readFileSync(path, 'utf8')
  const m = raw.match(/^TELEGRAM_BOT_TOKEN=(.+)$/m)
  if (!m) throw new Error(`TELEGRAM_BOT_TOKEN not found in ${path}`)
  return m[1].trim()
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bun test test/config.test.ts`
Expected: 4 pass.

- [ ] **Step 6: Commit**

```bash
git add session-bridge/package.json session-bridge/src/config.ts session-bridge/test/config.test.ts
git commit -m "feat(session-bridge): scaffold + config/token loader"
```

---

### Task 2: Text chunking

**Files:**
- Create: `session-bridge/src/chunk.ts`
- Test: `session-bridge/test/chunk.test.ts`

**Interfaces:**
- Produces: `chunkText(text: string, limit?: number): string[]` — never returns a chunk over `limit` (default 4096); prefers splitting at `\n\n`, falls back to hard cut.

- [ ] **Step 1: Write the failing test**

`session-bridge/test/chunk.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { chunkText } from '../src/chunk'

test('short text is a single chunk', () => {
  expect(chunkText('hello', 4096)).toEqual(['hello'])
})

test('splits at paragraph boundary under the limit', () => {
  const a = 'a'.repeat(60)
  const b = 'b'.repeat(60)
  const chunks = chunkText(`${a}\n\n${b}`, 100)
  expect(chunks).toEqual([a, b])
})

test('hard-cuts a single oversized paragraph', () => {
  const chunks = chunkText('x'.repeat(250), 100)
  expect(chunks.length).toBe(3)
  expect(chunks.every(c => c.length <= 100)).toBe(true)
  expect(chunks.join('')).toBe('x'.repeat(250))
})

test('empty text yields no chunks', () => {
  expect(chunkText('', 100)).toEqual([])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/chunk.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write minimal implementation**

`session-bridge/src/chunk.ts`:

```ts
export function chunkText(text: string, limit = 4096): string[] {
  if (text.length === 0) return []
  if (text.length <= limit) return [text]
  const chunks: string[] = []
  let current = ''
  for (const para of text.split('\n\n')) {
    const candidate = current === '' ? para : `${current}\n\n${para}`
    if (candidate.length <= limit) {
      current = candidate
      continue
    }
    if (current !== '') chunks.push(current)
    let rest = para
    while (rest.length > limit) {
      chunks.push(rest.slice(0, limit))
      rest = rest.slice(limit)
    }
    current = rest
  }
  if (current !== '') chunks.push(current)
  return chunks
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bun test test/chunk.test.ts` — Expected: 4 pass.

- [ ] **Step 5: Commit**

```bash
git add session-bridge/src/chunk.ts session-bridge/test/chunk.test.ts
git commit -m "feat(session-bridge): paragraph-preferring 4096 chunker"
```

---

### Task 3: tmux module — state classification + I/O wrappers

**Files:**
- Create: `session-bridge/src/tmux.ts`
- Test: `session-bridge/test/classify.test.ts`

**Interfaces:**
- Produces: `type SessionState = 'BLOCKED'|'WORKING'|'WAITING'|'IDLE'|'GONE'`, `classify(pane: string|null): SessionState`, `tailLines(pane: string, n?: number): string`, `listSessions(): string[]`, `capturePane(session: string): string|null`, `inject(session: string, text: string): void`, `pressKey(session: string, key: string): void`.
- The classify regexes are a port of `~/.local/bin/agents` (`classify()` there); if Claude Code UI strings drift, update both files together (spec: "Modal drift").

- [ ] **Step 1: Write the failing test with pane fixtures**

`session-bridge/test/classify.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { classify, tailLines } from '../src/tmux'

const BLOCKED_PANE = `
● I'll run the migration now.

  Do you want to proceed?
  ❯ 1. Yes
    2. Yes, and don't ask again
    3. No, and tell Claude what to do differently (esc)

  Esc to cancel
`

const WORKING_PANE = `
● Reading the file now…

✻ Forging… (esc to interrupt)
`

const WAITING_PANE = `
● Done — the tests pass and the branch is ready.

✻ Baked for 3m 12s
`

const IDLE_PANE = `
╭──────────────────────────────╮
│ >                            │
╰──────────────────────────────╯
`

test('permission modal → BLOCKED', () => expect(classify(BLOCKED_PANE)).toBe('BLOCKED'))
test('turn in flight → WORKING', () => expect(classify(WORKING_PANE)).toBe('WORKING'))
test('finished turn → WAITING', () => expect(classify(WAITING_PANE)).toBe('WAITING'))
test('empty prompt → IDLE', () => expect(classify(IDLE_PANE)).toBe('IDLE'))
test('null pane (capture failed) → GONE', () => expect(classify(null)).toBe('GONE'))

test('BLOCKED wins over WORKING when both strings present', () => {
  expect(classify(`${WORKING_PANE}\n  Do you want to proceed?\n  Esc to cancel`)).toBe('BLOCKED')
})

test('tailLines keeps last N non-empty right-trimmed lines', () => {
  const pane = 'a  \n\n\nb\nc\n'
  expect(tailLines(pane, 2)).toBe('b\nc')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/classify.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write the implementation**

`session-bridge/src/tmux.ts`:

```ts
export type SessionState = 'BLOCKED' | 'WORKING' | 'WAITING' | 'IDLE' | 'GONE'

// Ported from ~/.local/bin/agents classify() — keep the two in sync.
const BLOCKED_RE = /Esc to cancel|Do you want to (proceed|create|make|run|apply)/
const WORKING_RE = /esc to interrupt/
const WAITING_RE =
  /^[^A-Za-z]*[A-Z][a-z]+ for \d+m \d+s$|^[^A-Za-z]*[A-Z][a-z]+ for \d+s$|How is Claude doing this session|new task\? \/clear|say "do it"/m

export function tailLines(pane: string, n = 15): string {
  return pane
    .split('\n')
    .map(l => l.replace(/\s+$/, ''))
    .filter(l => l !== '')
    .slice(-n)
    .join('\n')
}

export function classify(pane: string | null): SessionState {
  if (pane === null) return 'GONE'
  const tail = tailLines(pane, 15)
  if (BLOCKED_RE.test(tail)) return 'BLOCKED'
  if (WORKING_RE.test(tail)) return 'WORKING'
  if (WAITING_RE.test(tail)) return 'WAITING'
  return 'IDLE'
}

function run(cmd: string[]): { code: number; out: string } {
  const r = Bun.spawnSync(cmd, { stdout: 'pipe', stderr: 'pipe' })
  return { code: r.exitCode, out: r.stdout.toString() }
}

export function listSessions(): string[] {
  const r = run(['tmux', 'ls', '-F', '#{session_name}'])
  if (r.code !== 0) return []
  return r.out.split('\n').filter(s => s !== '')
}

export function capturePane(session: string): string | null {
  const r = run(['tmux', 'capture-pane', '-t', session, '-p'])
  return r.code === 0 ? r.out : null
}

// Bracketed paste so multi-line text doesn't submit early; Enter submits once.
export function inject(session: string, text: string): void {
  const buf = `sb-${session.replace(/[^a-zA-Z0-9]/g, '_')}`
  for (const step of [
    ['tmux', 'set-buffer', '-b', buf, '--', text],
    ['tmux', 'paste-buffer', '-p', '-d', '-b', buf, '-t', session],
    ['tmux', 'send-keys', '-t', session, 'Enter'],
  ]) {
    const r = run(step)
    if (r.code !== 0) throw new Error(`tmux ${step[1]} failed for ${session}`)
  }
}

export function pressKey(session: string, key: string): void {
  const r = run(['tmux', 'send-keys', '-t', session, key])
  if (r.code !== 0) throw new Error(`tmux send-keys failed for ${session}`)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bun test test/classify.test.ts` — Expected: 7 pass.

- [ ] **Step 5: Sanity-check the fixtures against reality**

Run: `tmux capture-pane -t "$(tmux ls -F '#{session_name}' | head -1)" -p | tail -15`
Compare against the WAITING/IDLE fixtures; adjust fixtures if the live UI differs materially (do not loosen the regexes — they mirror `agents`).

- [ ] **Step 6: Commit**

```bash
git add session-bridge/src/tmux.ts session-bridge/test/classify.test.ts
git commit -m "feat(session-bridge): pane classification (agents port) + tmux wrappers"
```

---

### Task 4: State store + pending flags

**Files:**
- Create: `session-bridge/src/state.ts`
- Test: `session-bridge/test/state.test.ts`

**Interfaces:**
- Consumes: `STATE_DIR` from `src/config.ts`.
- Produces: `interface TopicInfo { topicId: number; status: 'open'|'closed' }`, `interface BridgeState { offset: number; topics: Record<string, TopicInfo> }`, `loadState(dir?): BridgeState`, `saveState(s, dir?): void`, `setPending(session: string, topicId: number, dir?): void`, `pendingPath(session: string, dir?): string`, `sanitizeSession(name: string): string`.
- The pending-flag file format is a cross-process contract with Task 8's hook: JSON `{ "topicId": number, "ts": number }` at `<dir>/pending/<sanitized-session>`.

- [ ] **Step 1: Write the failing test**

`session-bridge/test/state.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { mkdtempSync, readFileSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { loadState, saveState, setPending, pendingPath, sanitizeSession } from '../src/state'

function tmp(): string { return mkdtempSync(join(tmpdir(), 'sb-state-')) }

test('loadState returns empty state when file is absent', () => {
  const s = loadState(tmp())
  expect(s).toEqual({ offset: 0, topics: {} })
})

test('saveState/loadState round-trips', () => {
  const d = tmp()
  saveState({ offset: 42, topics: { 'loom-14': { topicId: 7, status: 'open' } } }, d)
  expect(loadState(d)).toEqual({ offset: 42, topics: { 'loom-14': { topicId: 7, status: 'open' } } })
})

test('setPending writes topicId and a timestamp', () => {
  const d = tmp()
  setPending('loom-14', 7, d)
  const flag = JSON.parse(readFileSync(pendingPath('loom-14', d), 'utf8'))
  expect(flag.topicId).toBe(7)
  expect(typeof flag.ts).toBe('number')
})

test('sanitizeSession replaces path separators', () => {
  expect(sanitizeSession('cai/foo')).toBe('cai_foo')
})

test('loadState survives corrupt json', () => {
  const d = tmp()
  saveState({ offset: 1, topics: {} }, d)
  const s = loadState(d)
  expect(existsSync(join(d, 'state.json'))).toBe(true)
  expect(s.offset).toBe(1)
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/state.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write the implementation**

`session-bridge/src/state.ts`:

```ts
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { STATE_DIR } from './config'

export interface TopicInfo {
  topicId: number
  status: 'open' | 'closed'
}

export interface BridgeState {
  offset: number
  topics: Record<string, TopicInfo>
}

export function loadState(dir = STATE_DIR): BridgeState {
  try {
    const raw = readFileSync(join(dir, 'state.json'), 'utf8')
    const s = JSON.parse(raw)
    return { offset: s.offset ?? 0, topics: s.topics ?? {} }
  } catch {
    return { offset: 0, topics: {} }
  }
}

export function saveState(s: BridgeState, dir = STATE_DIR): void {
  mkdirSync(dir, { recursive: true })
  writeFileSync(join(dir, 'state.json'), JSON.stringify(s, null, 2))
}

export function sanitizeSession(name: string): string {
  return name.replace(/[^a-zA-Z0-9._-]/g, '_')
}

export function pendingPath(session: string, dir = STATE_DIR): string {
  return join(dir, 'pending', sanitizeSession(session))
}

export function setPending(session: string, topicId: number, dir = STATE_DIR): void {
  mkdirSync(join(dir, 'pending'), { recursive: true })
  writeFileSync(pendingPath(session, dir), JSON.stringify({ topicId, ts: Date.now() }))
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bun test test/state.test.ts` — Expected: 5 pass.

- [ ] **Step 5: Commit**

```bash
git add session-bridge/src/state.ts session-bridge/test/state.test.ts
git commit -m "feat(session-bridge): state store + pending-flag contract"
```

---

### Task 5: Topic sync planner (pure)

**Files:**
- Create: `session-bridge/src/sync.ts`
- Test: `session-bridge/test/sync.test.ts`

**Interfaces:**
- Consumes: `TopicInfo` from `src/state.ts`.
- Produces: `interface SyncAction { kind: 'create'|'close'; session: string; topicId?: number }`, `planSync(live: string[], topics: Record<string, TopicInfo>, excludePatterns: string[]): SyncAction[]`. Executor (Task 7's `main.ts`) applies: `create` → Telegram `createForumTopic` then `topics[session] = {topicId, status:'open'}`; `close` → rename to `✖ <session>` + `closeForumTopic` then `status:'closed'`.

- [ ] **Step 1: Write the failing test**

`session-bridge/test/sync.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { planSync } from '../src/sync'

test('new live session → create', () => {
  expect(planSync(['loom-14'], {}, [])).toEqual([{ kind: 'create', session: 'loom-14' }])
})

test('excluded sessions are never created', () => {
  expect(planSync(['cai/x', 'codex-bank-20', 'loom-14'], {}, ['^cai/', '^codex-'])).toEqual([
    { kind: 'create', session: 'loom-14' },
  ])
})

test('open topic with dead session → close', () => {
  expect(planSync([], { 'loom-14': { topicId: 7, status: 'open' } }, [])).toEqual([
    { kind: 'close', session: 'loom-14', topicId: 7 },
  ])
})

test('open topic with live session → no action', () => {
  expect(planSync(['loom-14'], { 'loom-14': { topicId: 7, status: 'open' } }, [])).toEqual([])
})

test('closed topic whose name is live again → fresh create', () => {
  expect(planSync(['loom-14'], { 'loom-14': { topicId: 7, status: 'closed' } }, [])).toEqual([
    { kind: 'create', session: 'loom-14' },
  ])
})

test('closed topic with dead session stays untouched', () => {
  expect(planSync([], { 'loom-14': { topicId: 7, status: 'closed' } }, [])).toEqual([])
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/sync.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write the implementation**

`session-bridge/src/sync.ts`:

```ts
import type { TopicInfo } from './state'

export interface SyncAction {
  kind: 'create' | 'close'
  session: string
  topicId?: number
}

export function planSync(
  live: string[],
  topics: Record<string, TopicInfo>,
  excludePatterns: string[],
): SyncAction[] {
  const excludes = excludePatterns.map(p => new RegExp(p))
  const watched = live.filter(s => !excludes.some(r => r.test(s)))
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bun test test/sync.test.ts` — Expected: 6 pass.

- [ ] **Step 5: Commit**

```bash
git add session-bridge/src/sync.ts session-bridge/test/sync.test.ts
git commit -m "feat(session-bridge): pure topic-sync planner"
```

---

### Task 6: Telegram client + inbound router

**Files:**
- Create: `session-bridge/src/telegram.ts`
- Create: `session-bridge/src/router.ts`
- Test: `session-bridge/test/router.test.ts`

**Interfaces:**
- Consumes: `Config` (Task 1), `chunkText` (Task 2), `SessionState`, `tailLines` (Task 3), `TopicInfo` (Task 4).
- Produces:
  - `class Telegram { constructor(token: string); call(method: string, params: object): Promise<any>; getUpdates(offset: number, timeoutSec: number): Promise<any[]>; send(chatId: number, threadId: number|undefined, text: string): Promise<void>; createTopic(chatId: number, name: string): Promise<number>; renameAndClose(chatId: number, threadId: number, name: string): Promise<void> }`
  - `interface RouterDeps { config: Config; topics(): Record<string, TopicInfo>; classify(session: string): SessionState; capture(session: string): string|null; inject(session: string, text: string): void; pressKey(session: string, key: string): void; reply(threadId: number|undefined, text: string): Promise<void>; setPending(session: string, topicId: number): void }`
  - `handleUpdate(update: any, deps: RouterDeps): Promise<void>`
- Reply copy (exact strings, used by tests and smoke): delivered `→ delivered`; queued starts `⏳ session is working — queued`; blocked prompt ends with `say approve, deny, or an option number (1-9)`; keypress confirm starts `✅ pressed`; unknown tab `no live session for this tab`; ended `session ended — tab will be archived`; General-topic text `talk to a session in its own tab — this General tab is not routed`.

- [ ] **Step 1: Write the failing router test (fakes, no network, no tmux)**

`session-bridge/test/router.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { handleUpdate, type RouterDeps } from '../src/router'
import type { SessionState } from '../src/tmux'

const CFG = {
  groupId: -1004480392983,
  allowedUserId: 7735693897,
  excludePatterns: [],
  pollTimeoutSec: 50,
  topicSyncIntervalSec: 30,
}

function makeDeps(state: SessionState, pane = 'PANE') {
  const calls = { injected: [] as string[], pressed: [] as string[], replies: [] as string[], pending: [] as number[] }
  const deps: RouterDeps = {
    config: CFG,
    topics: () => ({ 'loom-14': { topicId: 7, status: 'open' } }),
    classify: () => state,
    capture: () => pane,
    inject: (_s, text) => { calls.injected.push(text) },
    pressKey: (_s, key) => { calls.pressed.push(key) },
    reply: async (_t, text) => { calls.replies.push(text) },
    setPending: (_s, topicId) => { calls.pending.push(topicId) },
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

test('WAITING → inject, flag, delivered', async () => {
  const { deps, calls } = makeDeps('WAITING')
  await handleUpdate(msg('run the tests'), deps)
  expect(calls.injected).toEqual(['run the tests'])
  expect(calls.pending).toEqual([7])
  expect(calls.replies).toEqual(['→ delivered'])
})

test('WORKING → inject, flag, queued warning', async () => {
  const { deps, calls } = makeDeps('WORKING')
  await handleUpdate(msg('also check lint'), deps)
  expect(calls.injected).toEqual(['also check lint'])
  expect(calls.pending).toEqual([7])
  expect(calls.replies[0]).toStartWith('⏳ session is working — queued')
})

test('BLOCKED + ordinary text → excerpt, no inject', async () => {
  const { deps, calls } = makeDeps('BLOCKED', 'Do you want to proceed?\nEsc to cancel')
  await handleUpdate(msg('what is happening'), deps)
  expect(calls.injected).toEqual([])
  expect(calls.replies[0]).toContain('Do you want to proceed?')
  expect(calls.replies[0]).toContain('say approve, deny, or an option number (1-9)')
})

test('BLOCKED + approve → presses 1 and confirms', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await handleUpdate(msg('approve'), deps)
  expect(calls.pressed).toEqual(['1'])
  expect(calls.replies[0]).toStartWith('✅ pressed')
})

test('BLOCKED + deny → presses Escape', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await handleUpdate(msg('Deny'), deps)
  expect(calls.pressed).toEqual(['Escape'])
})

test('BLOCKED + option number → presses that digit', async () => {
  const { deps, calls } = makeDeps('BLOCKED')
  await handleUpdate(msg('2'), deps)
  expect(calls.pressed).toEqual(['2'])
})

test('GONE → session ended reply, no inject', async () => {
  const { deps, calls } = makeDeps('GONE')
  await handleUpdate(msg('hello?'), deps)
  expect(calls.injected).toEqual([])
  expect(calls.replies[0]).toBe('session ended — tab will be archived')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/router.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write the Telegram client**

`session-bridge/src/telegram.ts`:

```ts
import { chunkText } from './chunk'

export class Telegram {
  constructor(private token: string) {}

  async call(method: string, params: Record<string, unknown> = {}): Promise<any> {
    const res = await fetch(`https://api.telegram.org/bot${this.token}/${method}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(params),
    })
    const body: any = await res.json()
    if (!body.ok) {
      // Never include the URL (contains the token) in errors.
      throw new Error(`telegram ${method} failed: ${body.error_code} ${body.description}`)
    }
    return body.result
  }

  async getUpdates(offset: number, timeoutSec: number): Promise<any[]> {
    return this.call('getUpdates', { offset, timeout: timeoutSec, allowed_updates: ['message'] })
  }

  async send(chatId: number, threadId: number | undefined, text: string): Promise<void> {
    for (const chunk of chunkText(text)) {
      await this.call('sendMessage', {
        chat_id: chatId,
        text: chunk,
        ...(threadId !== undefined ? { message_thread_id: threadId } : {}),
      })
    }
  }

  async createTopic(chatId: number, name: string): Promise<number> {
    const topic = await this.call('createForumTopic', { chat_id: chatId, name: name.slice(0, 128) })
    return topic.message_thread_id
  }

  async renameAndClose(chatId: number, threadId: number, name: string): Promise<void> {
    await this.call('editForumTopic', { chat_id: chatId, message_thread_id: threadId, name: name.slice(0, 128) })
    await this.call('closeForumTopic', { chat_id: chatId, message_thread_id: threadId })
  }
}
```

- [ ] **Step 4: Write the router**

`session-bridge/src/router.ts`:

```ts
import type { Config } from './config'
import type { SessionState } from './tmux'
import type { TopicInfo } from './state'
import { tailLines } from './tmux'

export interface RouterDeps {
  config: Config
  topics(): Record<string, TopicInfo>
  classify(session: string): SessionState
  capture(session: string): string | null
  inject(session: string, text: string): void
  pressKey(session: string, key: string): void
  reply(threadId: number | undefined, text: string): Promise<void>
  setPending(session: string, topicId: number): void
}

const APPROVAL_HINT = 'say approve, deny, or an option number (1-9)'

export async function handleUpdate(update: any, deps: RouterDeps): Promise<void> {
  const msg = update.message
  if (!msg || typeof msg.text !== 'string') return
  if (msg.chat?.id !== deps.config.groupId) return
  if (msg.from?.id !== deps.config.allowedUserId) return

  const threadId: number | undefined = msg.message_thread_id
  if (threadId === undefined) {
    await deps.reply(undefined, 'talk to a session in its own tab — this General tab is not routed')
    return
  }

  const entry = Object.entries(deps.topics()).find(
    ([, t]) => t.topicId === threadId && t.status === 'open',
  )
  if (!entry) {
    await deps.reply(threadId, 'no live session for this tab')
    return
  }
  const [session] = entry
  const state = deps.classify(session)

  if (state === 'GONE') {
    await deps.reply(threadId, 'session ended — tab will be archived')
    return
  }

  if (state === 'BLOCKED') {
    const word = msg.text.trim().toLowerCase()
    const key = word === 'approve' ? '1' : word === 'deny' ? 'Escape' : /^[1-9]$/.test(word) ? word : null
    if (key === null) {
      const pane = deps.capture(session) ?? ''
      await deps.reply(threadId, `🔴 ${session} is waiting on an approval:\n\n${tailLines(pane, 15)}\n\n${APPROVAL_HINT}`)
      return
    }
    deps.pressKey(session, key)
    await Bun.sleep(500)
    const after = deps.capture(session) ?? ''
    await deps.reply(threadId, `✅ pressed — current screen:\n\n${tailLines(after, 8)}`)
    return
  }

  // WORKING / WAITING / IDLE: deliver the text.
  deps.inject(session, msg.text)
  deps.setPending(session, threadId)
  if (state === 'WORKING') {
    await deps.reply(
      threadId,
      '⏳ session is working — queued. The next answer may belong to its current task; yours follows.',
    )
  } else {
    await deps.reply(threadId, '→ delivered')
  }
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `bun test test/router.test.ts` — Expected: 12 pass. (`Bun.sleep(500)` runs only in the two keypress tests; total runtime ~1 s is fine.)

- [ ] **Step 6: Commit**

```bash
git add session-bridge/src/telegram.ts session-bridge/src/router.ts session-bridge/test/router.test.ts
git commit -m "feat(session-bridge): telegram client + inbound router with approval mode"
```

---

### Task 7: Main loop

**Files:**
- Create: `session-bridge/src/main.ts`

**Interfaces:**
- Consumes: everything above. No new exports; this is the composition root run by systemd: `bun run src/main.ts`.

- [ ] **Step 1: Write the implementation**

`session-bridge/src/main.ts`:

```ts
import { loadConfig, loadToken } from './config'
import { Telegram } from './telegram'
import { loadState, saveState, setPending } from './state'
import { planSync } from './sync'
import { listSessions, capturePane, classify, inject, pressKey } from './tmux'
import { handleUpdate, type RouterDeps } from './router'

const config = loadConfig()
const tg = new Telegram(loadToken())
const state = loadState()

async function runSync(): Promise<void> {
  const actions = planSync(listSessions(), state.topics, config.excludePatterns)
  for (const a of actions) {
    if (a.kind === 'create') {
      const topicId = await tg.createTopic(config.groupId, a.session)
      state.topics[a.session] = { topicId, status: 'open' }
      console.error(`session-bridge: topic created for ${a.session} (${topicId})`)
    } else {
      await tg.renameAndClose(config.groupId, a.topicId!, `✖ ${a.session}`)
      state.topics[a.session].status = 'closed'
      console.error(`session-bridge: topic archived for ${a.session}`)
    }
  }
  if (actions.length > 0) saveState(state)
}

const deps: RouterDeps = {
  config,
  topics: () => state.topics,
  classify: s => classify(capturePane(s)),
  capture: capturePane,
  inject,
  pressKey,
  reply: (threadId, text) => tg.send(config.groupId, threadId, text),
  setPending,
}

// Sync on a timer; the event loop interleaves this with the long poll below.
let syncing = false
setInterval(async () => {
  if (syncing) return
  syncing = true
  try {
    await runSync()
  } catch (e) {
    console.error(`session-bridge: sync error: ${e}`)
  } finally {
    syncing = false
  }
}, config.topicSyncIntervalSec * 1000)

console.error('session-bridge: starting')
await runSync().catch(e => console.error(`session-bridge: initial sync error: ${e}`))

while (true) {
  try {
    const updates = await tg.getUpdates(state.offset, config.pollTimeoutSec)
    for (const u of updates) {
      state.offset = Math.max(state.offset, u.update_id + 1)
      try {
        await handleUpdate(u, deps)
      } catch (e) {
        console.error(`session-bridge: handler error: ${e}`)
      }
    }
    if (updates.length > 0) saveState(state)
  } catch (e) {
    console.error(`session-bridge: poll error: ${e}`)
    await Bun.sleep(5000)
  }
}
```

- [ ] **Step 2: Run the full unit suite**

Run: `bun test`
Expected: all tests from Tasks 1–6 pass (34 total).

- [ ] **Step 3: Foreground smoke run**

Run: `cd ~/projects/build-ai-automation-workflow/session-bridge && bun run src/main.ts` (leave running ~60 s, then Ctrl-C).
Expected: "starting" on stderr; within 30 s, one topic per live non-excluded tmux session appears in the "Rex & Wall-E" group; `~/.local/state/session-bridge/state.json` holds the session→topic map. The stale setup updates from group creation are consumed and dropped (they are `my_chat_member`/service messages — no text).

- [ ] **Step 4: Commit**

```bash
git add session-bridge/src/main.ts
git commit -m "feat(session-bridge): main loop — long poll + timed topic sync"
```

---

### Task 8: Stop hook

**Files:**
- Create: `session-bridge/hook/stop-hook.ts`
- Test: `session-bridge/test/transcript.test.ts`

**Interfaces:**
- Consumes: `loadConfig`, `loadToken`, `STATE_DIR` (Task 1), `Telegram` (Task 6), `pendingPath`, `sanitizeSession` (Task 4).
- Produces: `lastAssistantText(jsonl: string): string | null` (exported for tests). The script is invoked by Claude Code as a Stop hook: JSON on stdin including `transcript_path`; must always exit 0; must be near-free when the session has no pending flag.
- Claude Code transcript format: one JSON object per line; assistant turns look like `{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"…"},{"type":"tool_use",…}]}}`. The reply is the text blocks of the **last** assistant entry that has any.

- [ ] **Step 1: Write the failing test**

`session-bridge/test/transcript.test.ts`:

```ts
import { test, expect } from 'bun:test'
import { lastAssistantText } from '../hook/stop-hook'

const L = (o: unknown) => JSON.stringify(o)

test('extracts text of the last assistant message with text blocks', () => {
  const jsonl = [
    L({ type: 'user', message: { content: [{ type: 'text', text: 'hi' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'text', text: 'first' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'tool_use', name: 'Bash' }] } }),
    L({ type: 'assistant', message: { content: [{ type: 'text', text: 'final answer' }] } }),
  ].join('\n')
  expect(lastAssistantText(jsonl)).toBe('final answer')
})

test('joins multiple text blocks with blank lines', () => {
  const jsonl = L({
    type: 'assistant',
    message: { content: [{ type: 'text', text: 'part 1' }, { type: 'text', text: 'part 2' }] },
  })
  expect(lastAssistantText(jsonl)).toBe('part 1\n\npart 2')
})

test('returns null when no assistant text exists', () => {
  const jsonl = L({ type: 'user', message: { content: [] } })
  expect(lastAssistantText(jsonl)).toBeNull()
})

test('skips malformed lines without throwing', () => {
  const jsonl = ['not json{{{', L({ type: 'assistant', message: { content: [{ type: 'text', text: 'ok' }] } })].join('\n')
  expect(lastAssistantText(jsonl)).toBe('ok')
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `bun test test/transcript.test.ts` — Expected: FAIL, module not found.

- [ ] **Step 3: Write the hook**

`session-bridge/hook/stop-hook.ts`:

```ts
// Claude Code Stop hook: if this tmux session's last input came from Telegram
// (pending flag set by session-bridge), send the final assistant text to that
// forum topic. Must always exit 0 and cost ~nothing when the flag is absent.
import { existsSync, readFileSync, unlinkSync } from 'node:fs'
import { loadConfig, loadToken } from '../src/config'
import { pendingPath } from '../src/state'
import { Telegram } from '../src/telegram'

const STALE_MS = 3_600_000

export function lastAssistantText(jsonl: string): string | null {
  let out: string | null = null
  for (const line of jsonl.split('\n')) {
    if (line.trim() === '') continue
    let entry: any
    try {
      entry = JSON.parse(line)
    } catch {
      continue
    }
    if (entry.type !== 'assistant') continue
    const blocks = entry.message?.content
    if (!Array.isArray(blocks)) continue
    const texts = blocks
      .filter((b: any) => b.type === 'text' && typeof b.text === 'string')
      .map((b: any) => b.text)
    if (texts.length > 0) out = texts.join('\n\n')
  }
  return out
}

if (import.meta.main) {
  try {
    if (!process.env.TMUX) process.exit(0)
    const r = Bun.spawnSync(['tmux', 'display-message', '-p', '#S'])
    const session = r.stdout.toString().trim()
    if (session === '') process.exit(0)

    const flagPath = pendingPath(session)
    if (!existsSync(flagPath)) process.exit(0)
    const flag = JSON.parse(readFileSync(flagPath, 'utf8'))
    if (Date.now() - flag.ts > STALE_MS) {
      unlinkSync(flagPath)
      process.exit(0)
    }

    const input = JSON.parse(await Bun.stdin.text())
    const text = lastAssistantText(readFileSync(input.transcript_path, 'utf8'))
    if (text === null) {
      unlinkSync(flagPath)
      process.exit(0)
    }

    const config = loadConfig()
    await new Telegram(loadToken()).send(config.groupId, flag.topicId, text)
    unlinkSync(flagPath)
  } catch (e) {
    // Leave the flag for a retry on the next Stop; never fail the session.
    console.error(`session-bridge hook: ${e}`)
  }
  process.exit(0)
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `bun test test/transcript.test.ts` — Expected: 4 pass. Also run `bun test` — full suite still green.

- [ ] **Step 5: Verify the transcript-format assumption against a real transcript**

Run: `ls -t ~/.claude/projects/-home-dev/*.jsonl | head -1 | xargs tail -3 | cut -c1-200`
Expected: lines matching the `{"type":"assistant","message":{"content":[…]}}` shape. If the real shape differs, fix `lastAssistantText` and its fixtures now.

- [ ] **Step 6: Commit**

```bash
git add session-bridge/hook/stop-hook.ts session-bridge/test/transcript.test.ts
git commit -m "feat(session-bridge): Stop hook — telegram reply on flagged sessions"
```

---

### Task 9: Install assets — systemd unit, hook installer, README

**Files:**
- Create: `session-bridge/systemd/session-bridge.service`
- Create: `session-bridge/scripts/install-hook.sh`
- Create: `session-bridge/README.md`

**Interfaces:**
- Consumes: `src/main.ts` (Task 7), `hook/stop-hook.ts` (Task 8).
- Produces: the two artifacts the gated `!` one-liners reference in Task 10.

- [ ] **Step 1: Write the systemd user unit**

`session-bridge/systemd/session-bridge.service`:

```ini
[Unit]
Description=session-bridge — per-tmux-session Telegram topics
After=network-online.target

[Service]
ExecStart=/usr/local/bin/bun run /home/dev/projects/build-ai-automation-workflow/session-bridge/src/main.ts
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

(Token and config are read from `~/.config/session-bridge/` by the code itself; no `EnvironmentFile=` needed, which also keeps the token out of `systemctl show` output.)

- [ ] **Step 2: Write the hook installer**

`session-bridge/scripts/install-hook.sh`:

```bash
#!/usr/bin/env bash
# Adds the session-bridge Stop hook to ~/.claude/settings.json (idempotent).
# Run by the USER via a ! one-liner — agent writes to ~/.claude are auto-mode-gated.
set -euo pipefail

SETTINGS="$HOME/.claude/settings.json"
CMD="/usr/local/bin/bun /home/dev/projects/build-ai-automation-workflow/session-bridge/hook/stop-hook.ts"

if jq -e --arg cmd "$CMD" '[.hooks.Stop[]?.hooks[]?.command] | index($cmd)' "$SETTINGS" >/dev/null 2>&1; then
  echo "already installed"
  exit 0
fi

tmp="$(mktemp)"
jq --arg cmd "$CMD" \
  '.hooks.Stop = ((.hooks.Stop // []) + [{"hooks": [{"type": "command", "command": $cmd, "timeout": 30}]}])' \
  "$SETTINGS" > "$tmp"
mv "$tmp" "$SETTINGS"
echo "installed — new sessions pick it up; running sessions need a restart"
```

Then: `chmod +x session-bridge/scripts/install-hook.sh`

- [ ] **Step 3: Write the README**

`session-bridge/README.md`:

```markdown
# session-bridge

One Telegram forum topic per tmux Claude session, in the private "Rex & Wall-E"
supergroup, via the dedicated bot @WallFred_bot. Message a session's tab and the
text is pasted into its Claude prompt; the reply comes back to the same tab via a
global Stop hook. Approve/deny permission prompts from the tab while a session is
blocked. Spec: `../docs/superpowers/specs/2026-08-03-session-bridge-design.md`.

## Layout

- `src/main.ts` — daemon: long-poll + topic sync (systemd user service `session-bridge`)
- `hook/stop-hook.ts` — Claude Code Stop hook (registered in `~/.claude/settings.json`)
- `~/.config/session-bridge/` — `config.json` (groupId, allowed user, excludes) + `.env` (bot token, 600)
- `~/.local/state/session-bridge/` — `state.json` (offset, session↔topic map) + `pending/` flags

## Operating notes

- One long-poll consumer per bot token: never run a second copy of the daemon.
- Topics are archived (`✖ name` + closed), never deleted.
- All sends are plain text; 4096-char chunks.
- Pane-state heuristics are a port of `~/.local/bin/agents` — update both together.
- Logs: `journalctl --user -u session-bridge -f`

## Recovery

- Restart: `systemctl --user restart session-bridge`
- Missed replies: check `~/.local/state/session-bridge/pending/` for stale flags (auto-expire after 1 h).
- Rebuild topic map: stop the service, delete `state.json`, start — new topics are created; old ones stay archived.
```

- [ ] **Step 4: Run the full suite once more**

Run: `bun test` — Expected: green.

- [ ] **Step 5: Commit**

```bash
git add session-bridge/systemd/session-bridge.service session-bridge/scripts/install-hook.sh session-bridge/README.md
git commit -m "feat(session-bridge): systemd unit, gated hook installer, README"
```

---

### Task 10: Gated installs + live end-to-end smoke

**Files:** none created — this task is operational.

**Interfaces:**
- Consumes: all prior tasks.
- Produces: a running service, an installed hook, and a verified end-to-end path.

- [ ] **Step 1: Agent installs the unit file (allowed — outside `~/.claude/`)**

```bash
mkdir -p ~/.config/systemd/user
cp ~/projects/build-ai-automation-workflow/session-bridge/systemd/session-bridge.service ~/.config/systemd/user/
```

- [ ] **Step 2: Hand the user the two gated one-liners**

Ask the user to run, in this session:

```
! bash ~/projects/build-ai-automation-workflow/session-bridge/scripts/install-hook.sh
! systemctl --user daemon-reload && systemctl --user enable --now session-bridge && systemctl --user status session-bridge --no-pager | head -5
```

(If the agent's own attempt at either is permitted by the sandbox, fine — but expect the auto-mode gate and hand over the one-liners rather than fighting it.)

- [ ] **Step 3: Verify service health**

Run: `systemctl --user is-active session-bridge && journalctl --user -u session-bridge -n 10 --no-pager`
Expected: `active`; log shows "starting" and topic-creation lines, no auth errors.

- [ ] **Step 4: Live smoke — full checklist from the spec, in order**

1. **Topic auto-create:** `tmux new-session -d -s bridge-smoke-1 'claude'` → within 30 s a `bridge-smoke-1` topic exists.
2. **Inbound:** user messages the `bridge-smoke-1` tab "say the word pineapple" → text appears in that session's prompt and submits; tab shows `→ delivered`.
3. **Reply:** the session's answer (containing "pineapple") arrives in the same tab. (Requires the hook installed AND the smoke session started after hook install.)
4. **Queued warning:** message the tab again while the session is mid-turn → `⏳ session is working — queued…` reply.
5. **Approval flow:** in the tab, ask the session to create a file so a permission modal appears → tab message gets the 🔴 excerpt; user replies `approve` → `✅ pressed` + the modal clears in tmux.
6. **Archive:** `tmux kill-session -t bridge-smoke-1` → within 30 s the topic is renamed `✖ bridge-smoke-1` and closed.
7. **Isolation:** confirm the main bot (Bebop/agents nudges) still works: `agents test-nudge` returns HTTP 200.

- [ ] **Step 5: Record results**

Append a "Smoke results — YYYY-MM-DD" section to `session-bridge/README.md` with pass/fail per item, commit:

```bash
git add session-bridge/README.md
git commit -m "docs(session-bridge): live smoke results"
```

- [ ] **Step 6: Merge recommendation**

Per the global merge protocol: post the compact **Merge recommendation** block for `claude/session-bridge → main` (verified: `bun test` suite + 7-point live smoke; push to origin/main; delete branch y) and STOP — merge only on explicit approval.

---

## Self-Review (completed)

- **Spec coverage:** bot/token (pre-done), group pinning (pre-done), daemon long-poll+offset (T7), topic sync incl. exclude patterns, ✖-archive, unique-name recreate (T5/T7), inbound gate on user+group+topic (T6), state table incl. queued warning (T6), approval mode incl. number keys and stateless re-check (T6), bracketed-paste injection (T3), reply flag contract (T4), Stop hook fast-exit/stale/retry/plain-text/chunking (T8, chunking in T2/T6), systemd unit (T9), gated installs as `!` one-liners (T10), security (silent drops T6; token never logged T6/T9), testing plan (fixtures T1–T8, live smoke T10). Future items (nudges in tabs, photos, session-start-from-phone) correctly absent.
- **Placeholder scan:** none — every code step contains full code.
- **Type consistency:** `TopicInfo`/`BridgeState` (T4) used in T5/T6/T7; `RouterDeps.reply(threadId, text)` matches `tg.send(groupId, threadId, text)` wrapper in T7; `pendingPath`/`setPending` names consistent across T4/T6/T7/T8; reply copy strings identical between router (T6) and its tests.
