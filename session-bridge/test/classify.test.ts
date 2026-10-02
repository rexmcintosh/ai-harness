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

// Fixtures below are trimmed captures of live Claude Code panes (2026-08-04).
// In the current UI the "esc to interrupt" signal lives in the footer line, not
// in the spinner line.
const WORKING_PANE = `
● Reading the file now…

✶ Sketching… (3m 43s · thinking with high effort)

────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · esc to interrupt · ← …
`

const WAITING_PANE = `
● Done — the tests pass and the branch is ready.

✻ Baked for 3m 12s

────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle)
`

const IDLE_PANE = `
───────────── Review and systematize Mia's editorial feedback ──
❯
────────────────────────────────────────────────────────────────
  ⏵⏵ auto mode on (shift+tab to cycle) · ← 1 agent
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

test('AskUserQuestion menu footer → BLOCKED', () => {
  const pane = 'Which color do you pick?\n❯ 1. Red\n  2. Blue\nEnter to select · ↑/↓ to navigate · Esc to cancel'
  expect(classify(pane)).toBe('BLOCKED')
})
