export type SessionState = 'BLOCKED' | 'WORKING' | 'WAITING' | 'IDLE' | 'GONE'

const BLOCKED_RE = /Esc to cancel|Enter to select|Do you want to (proceed|create|make|run|apply)/
const WORKING_RE = /esc to interrupt/
const WAITING_RE =
  /^[^A-Za-z]*[A-Z][a-z]+ for \d+m \d+s$|^[^A-Za-z]*[A-Z][a-z]+ for \d+s$|How is Claude doing this session|new task\? \/clear|say "do it"/m

export function tailLines(pane: string, n = 15): string {
  return pane.split('\n').map(l => l.replace(/\s+$/, '')).filter(l => l !== '').slice(-n).join('\n')
}

export function classify(pane: string | null): SessionState {
  if (pane === null) return 'GONE'
  const tail = tailLines(pane, 15)
  if (BLOCKED_RE.test(tail)) return 'BLOCKED'
  if (WORKING_RE.test(tail)) return 'WORKING'
  if (WAITING_RE.test(tail)) return 'WAITING'
  return 'IDLE'
}

function run(cmd: string[]): { code: number; out: string; err: string } {
  const r = Bun.spawnSync(cmd, { stdout: 'pipe', stderr: 'pipe' })
  return { code: r.exitCode, out: r.stdout.toString(), err: r.stderr.toString() }
}

function target(session: string): string { return `=${session}:` }
function detail(err: string): string {
  const first = err.trim().slice(0, 200)
  return first === '' ? '' : `: ${first}`
}

export function listSessions(): string[] {
  const r = run(['tmux', 'ls', '-F', '#{session_name}'])
  return r.code === 0 ? r.out.split('\n').filter(s => s !== '') : []
}

export function capturePane(session: string): string | null {
  const r = run(['tmux', 'capture-pane', '-t', target(session), '-p', '-J'])
  return r.code === 0 ? r.out : null
}

// The tmux session ID changes when a name is reused. Pane ID and creation time
// change when the target pane is replaced. Daemon restarts leave all three stable.
export function sessionGeneration(session: string): string | null {
  const r = run([process.env.SESSION_BRIDGE_TMUX ?? 'tmux', 'display-message', '-p', '-t', target(session), '#{session_id}:#{pane_id}:#{pane_created}'])
  const value = r.out.trim()
  return r.code === 0 && value !== '' ? value : null
}

export function inject(session: string, text: string): void {
  const buf = `sb-${session.replace(/[^a-zA-Z0-9]/g, '_')}`
  for (const step of [
    ['tmux', 'set-buffer', '-b', buf, '--', text],
    ['tmux', 'paste-buffer', '-p', '-d', '-b', buf, '-t', target(session)],
    ['tmux', 'send-keys', '-t', target(session), 'Enter'],
  ]) {
    const r = run(step)
    if (r.code !== 0) throw new Error(`tmux ${step[1]} failed for ${session}${detail(r.err)}`)
  }
}

export function pressKey(session: string, key: string): void {
  const r = run(['tmux', 'send-keys', '-t', target(session), key])
  if (r.code !== 0) throw new Error(`tmux send-keys failed for ${session}${detail(r.err)}`)
}
