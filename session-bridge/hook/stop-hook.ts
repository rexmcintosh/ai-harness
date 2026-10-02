// Claude Code Stop hook: if this tmux session's last input came from Telegram
// (pending flag set by session-bridge), send the final assistant text to that
// forum topic. Must always exit 0 and cost ~nothing when the flag is absent.
import { appendFileSync, closeSync, existsSync, mkdirSync, openSync, readFileSync, readSync, statSync, unlinkSync } from 'node:fs'
import { STATE_DIR, loadConfig, loadToken } from '../src/config'
import { pendingPath, setPending } from '../src/state'
import { Telegram } from '../src/telegram'

const STALE_MS = 3_600_000

function hlog(line: string): void {
  try {
    mkdirSync(STATE_DIR, { recursive: true })
    appendFileSync(`${STATE_DIR}/hook.log`, `${new Date().toISOString()} ${line}\n`)
  } catch {}
}

// Transcripts grow without bound; a Stop hook must never slurp one. Read only the
// final maxBytes and discard the leading partial line so every line still parses.
export function readTail(path: string, maxBytes = 2 * 1024 * 1024): string {
  const size = statSync(path).size
  if (size <= maxBytes) return readFileSync(path, 'utf8')

  // One extra byte, so we can tell a tail that begins exactly at a line break.
  const length = maxBytes + 1
  const start = size - length
  const buf = Buffer.allocUnsafe(length)
  const fd = openSync(path, 'r')
  let read: number
  try {
    read = readSync(fd, buf, 0, length, start)
  } finally {
    closeSync(fd)
  }
  const text = buf.subarray(0, read).toString('utf8')
  if (text.startsWith('\n')) return text.slice(1)
  const nl = text.indexOf('\n')
  return nl === -1 ? '' : text.slice(nl + 1)
}

export interface AssistantEntry {
  text: string
  // Entry timestamp in epoch ms; entries without a parseable timestamp get
  // Infinity so they always count as fresh (never silently dropped).
  ts: number
}

export function lastAssistantEntry(jsonl: string): AssistantEntry | null {
  let out: AssistantEntry | null = null
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
    if (texts.length === 0) continue
    const parsed = Date.parse(entry.timestamp)
    out = { text: texts.join('\n\n'), ts: Number.isNaN(parsed) ? Infinity : parsed }
  }
  return out
}

export function lastAssistantText(jsonl: string): string | null {
  return lastAssistantEntry(jsonl)?.text ?? null
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

    // Claude Code flushes the final answer to the transcript a beat AFTER the
    // Stop event fires. Never forward text generated before the question was
    // injected (flag.ts): poll until a fresh entry appears or the budget runs
    // out. FRESH_SLACK_MS absorbs daemon/session clock skew within one host.
    const remainingBefore = typeof flag.remaining === 'number' ? flag.remaining : 1
    const FRESH_SLACK_MS = 2_000
    const deadline = Date.now() + 4_000
    let entry = lastAssistantEntry(readTail(input.transcript_path))
    const fresh = () => entry !== null && entry.ts >= flag.ts - FRESH_SLACK_MS
    while (!fresh() && Date.now() < deadline) {
      await Bun.sleep(500)
      entry = lastAssistantEntry(readTail(input.transcript_path))
    }
    if (!fresh()) {
      // A WORKING-session flag (remaining >= 2) legitimately forwards the
      // in-flight answer, which predates the question — allow that one.
      if (!(remainingBefore >= 2 && entry !== null)) {
        hlog(`${session} no fresh answer (flag ts ${flag.ts}, entry ts ${entry?.ts ?? 'none'}) — leaving flag for next Stop`)
        process.exit(0)
      }
    }
    const text = entry!.text

    const config = loadConfig()
    hlog(`${session} → topic ${flag.topicId} remaining=${remainingBefore} text="${text.slice(0, 60).replace(/\n/g, ' ')}"`)
    await new Telegram(loadToken()).send(config.groupId, flag.topicId, text)
    hlog(`${session} sent ok`)
    // A WORKING session owes two answers (its in-flight turn, then the queued
    // message). Old flags predate the counter and mean one. Re-arm with a fresh
    // timestamp so the second answer gets its own staleness window.
    const remaining = remainingBefore - 1
    if (remaining > 0) setPending(session, flag.topicId, undefined, remaining)
    else unlinkSync(flagPath)
  } catch (e) {
    // Leave the flag for a retry on the next Stop; never fail the session.
    hlog(`error: ${e}`)
    console.error(`session-bridge hook: ${e}`)
  }
  process.exit(0)
}
