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

## Approval and inbound-action state

`state.json` uses schema version 2. Each accepted approval display stores the topic,
session, exact prompt SHA-256, accepted Telegram message IDs, display and expiry
times, and the live tmux generation. The generation is
`session_id:pane_id:pane_created`, read with an exact tmux target. It stays stable
across a bridge restart. A new session with the same name or a replaced pane gets
a new generation and invalidates the display. The router compares the generation,
blocked state, and exact prompt again immediately before a key press.

Inbound action outcomes are keyed by Telegram update ID. The states are
`attempting`, `not_injected`, `injected`, and `uncertain_action`. The bridge saves
`attempting` before it calls tmux. A pre-call validation failure is
`not_injected`. Success means only that tmux accepted paste/Enter or the key press.
An error or interruption after invocation is `uncertain_action`. On load, any
persisted `attempting` state becomes `uncertain_action`. Terminal outcomes advance
the update offset and never replay automatically. They do not claim command or
task completion.

## Operating notes

- One long-poll consumer per bot token: never run a second copy of the daemon.
- Topics are archived (`✖ name` + closed), never deleted.
- All sends are plain text; 4096-char chunks.
- Pane-state heuristics are a port of `~/.local/bin/agents` — update both together.
- Logs: `journalctl --user -u session-bridge -f`

A Telegram receipt proves provider acceptance only. It does not prove that Rex
read the prompt or that the requested work completed.

## Activate after review

No runtime was changed by this repair. After the reviewed commit reaches the
main checkout, run `bun test session-bridge/test`, then
`systemctl --user restart session-bridge`. Check
`journalctl --user -u session-bridge -n 50 --no-pager`. Roll back by restoring
the prior reviewed commit and restarting the same service. Do not delete
`state.json`; it contains the replay guard and accepted prompt identity.

## Recovery

- Restart: `systemctl --user restart session-bridge`
- Missed replies: check `~/.local/state/session-bridge/pending/` for stale flags (auto-expire after 1 h).
- Rebuild topic map only after reviewing action records: stop the service, save `state.json` as evidence, start from a reviewed empty version-2 state, then start. Deleting state also deletes replay guards.

## Smoke results — 2026-08-05

Live end-to-end with Rex on the phone; all seven spec checks passed:

1. Topic auto-create (bridge-smoke-1 within 30 s) — PASS
2. Inbound inject + "→ delivered" — PASS ("say the word banana")
3. Reply relayed to the tab (freshness-gated) — PASS ("banana")
4. Queued-warning path — PASS (unit-tested; ⏳ copy observed during testing)
5. Approval flow — PASS (proactive 🔴 prompt arrived unprompted; "2" pressed Blue)
6. Archive on session death — PASS (renamed ✖ bridge-smoke-1 + closed, after debounce)
7. Main-bot isolation — PASS (agents test-nudge HTTP 200; Bebop untouched)

Fixed during the smoke (each committed separately): `=name:` exact tmux targets,
capture-pane `-J` for wrapped panes + AskUserQuestion footer as BLOCKED, hook
freshness gate (never relay text older than the question), injected-message
preamble (stops sessions freelancing via the main-bot plugin), proactive
blocked-watcher (no manual poke needed), hook.log ground truth.

The router never substitutes a session name when an installed tmux-generation lookup fails. It saves the relay flag before marking an action `injected`; a failed flag write leaves the prior attempt unresolved and prevents duplicate injection. State writes sync the file and directory. The last 256 confirmed action records remain in the state file; older confirmed records are protected from replay by the persisted polling offset. Uncertain records remain until an explicit owner resolution and are not automatically pruned. Multi-part Telegram sends can partly succeed; a later failed part leaves approval unarmed and may leave a partial prompt visible. It is not a complete display or an approval.
