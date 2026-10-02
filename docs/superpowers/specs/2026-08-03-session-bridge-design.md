# Session Bridge — per-tmux-session Telegram conversations

**Date:** 2026-08-03
**Status:** Approved design, pending implementation
**Location:** `~/projects/build-ai-automation-workflow/session-bridge/`

## Goal

Let Rex hold a conversation with any specific Claude Code tmux session from his phone.
The organizing unit is the tmux session: one Telegram forum topic ("tab") per tmux
session, named after it, inside a single private group. No generic always-listening
assistant session.

Decisions locked during brainstorm:

1. **Coverage:** all tmux sessions, automatically. No opt-in flag.
2. **Mirroring:** tabs are quiet by default. Only replies to phone-originated messages
   land in a tab. Terminal work never mirrors.
3. **Approvals:** permission prompts can be approved/denied from the tab, with an
   explicit confirm word.

## Non-goals

- Replacing the existing main bot. Bebop briefings and `agents` nudges continue on the
  current bot, untouched.
- Message history/search (Telegram Bot API has none).
- Rich media inbound (photos etc.) — v1 is text only in both directions.
- Moving `agents` nudges into tabs (possible future; nudges stay in the main-bot DM).

## Components

### 1. Bot: Wall-E (@WallFred_bot)

- Dedicated bot; token at `~/.config/session-bridge/.env` (`TELEGRAM_BOT_TOKEN=`, mode 600).
- Needed because a Telegram bot token allows exactly one long-poll consumer, and the
  main bot's slot is contested by the telegram channel plugin that Claude sessions spawn.
- The bot must be **admin** of the group (grants Manage Topics *and* bypasses privacy
  mode so it sees all group messages; `getMe` currently shows
  `can_read_all_group_messages: false`, which admin status overrides).

### 2. Group: "Claude Sessions"

- Private supergroup with **Topics enabled**. One forum topic per tmux session, named
  exactly like the session (e.g. `loom-14`).
- Group ID is pinned in config once at setup. The bridge hard-ignores every other chat.

### 3. Bridge daemon (`session-bridge`)

A single Bun script run as a systemd **user** service (`session-bridge.service`,
`Restart=always`). It is a dumb postman: no LLM, no policy decisions beyond this spec.

Responsibilities:

- **Long-poll** `getUpdates` with a persisted offset (nothing lost across restarts —
  Telegram queues messages while the daemon is down).
- **Topic sync** (every 30 s and on every update batch):
  - `tmux ls` → session list, minus exclude patterns (default: `^cai/`, `^codex-`).
  - New session → `createForumTopic` named after it; record `session ↔ topic_id` in
    state. Session gone → rename topic to `✖ <name>` + `closeForumTopic` (archived,
    reversible, never deleted).
  - Names are unique by construction (harness suffixes `-N`), so the map is 1:1. If a
    name ever repeats after its topic was closed, create a fresh topic.
- **Inbound routing** — for each message: accept only if `chat.id` = configured group,
  `from.id` = 7735693897, and `message_thread_id` maps to a live session. Everything
  else is dropped silently (no pairing replies, no error chatter to strangers).
- **State classification** before injecting, reusing the `agents` script's pane
  heuristics (BLOCKED / WORKING / WAITING / IDLE via `tmux capture-pane`).

Inbound behavior by state:

| Session state | Behavior |
| --- | --- |
| WAITING / IDLE | Inject the text (below), reply "→ delivered". Set the reply flag. |
| WORKING | Inject (Claude Code queues typed input mid-turn), reply "session is working — queued; the next answer may belong to its current task". Set the reply flag. |
| BLOCKED | Do **not** inject. Send the pane's prompt excerpt (last ~15 lines), which *arms* this topic for 10 minutes. |
| GONE | Reply "session ended", close the topic. |

Approval mode (per topic, while the session stays BLOCKED):

- The **first** message to a blocked session always returns the prompt excerpt and arms
  the topic for 10 minutes — never a keypress. You never approve something you have not
  been shown, so an `approve` typed blind is answered with the prompt instead.
- While armed: `approve` → `tmux send-keys '1'` (selects the modal's "Yes"); `deny` →
  `tmux send-keys Escape` (cancels); a bare number `1`–`9` → presses that option (covers
  multi-option modals like AskUserQuestion).
- Any keypress disarms the topic, so a second key needs a fresh excerpt first. A
  keypress also sets the reply flag, so the answer produced after the approval is
  forwarded back to the tab.
- Anything else → re-send the prompt excerpt with "say approve, deny, or an option
  number" (and re-arm). After any keypress, capture the pane again and confirm the new
  state in the tab. If the session is no longer BLOCKED on re-check, fall through to
  normal handling.

Text injection mechanics:

- `tmux set-buffer` + `paste-buffer -p -t <session>` (bracketed paste, so multi-line
  messages don't submit early), then `send-keys Enter`.
- Text is passed literally — never through a shell, no interpolation.

Reply flag: `~/.local/state/session-bridge/pending/<session>` containing
`{topicId, ts, remaining}`. Written on every successful injection and on every approval
keypress; consumed by the Stop hook. `remaining` is how many Stop events should still
forward an answer to this topic — 2 for an injection into a WORKING session, 1
otherwise. The hook decrements it and rewrites the flag (with a fresh `ts`) while it
stays above zero, deleting the flag only on the last one. A flag with no `remaining`
field predates the counter and counts as 1.

### 4. Reply hook (Stop hook, global)

Registered in `~/.claude/settings.json` → `hooks.Stop`. A small script that:

1. Exits instantly (cost ≈ 0) if `$TMUX` is unset or no reply flag exists for
   `tmux display-message -p '#S'`.
2. Otherwise: reads the hook's stdin JSON (`transcript_path`), extracts the last
   assistant message's text blocks from the transcript JSONL, POSTs to the flagged
   `topic_id` via `sendMessage`, and deletes the flag.
3. Sends **plain text** (no `parse_mode` — avoids MarkdownV2 escaping failures), split
   into ≤4096-char chunks on paragraph boundaries.
4. On send failure: log to stderr and leave the flag in place so the next Stop
   retries; a flag older than 1 h is stale and gets deleted instead of retried.

## Data & config layout

| Path | Contents |
| --- | --- |
| `~/.config/session-bridge/.env` | `TELEGRAM_BOT_TOKEN` (600) — DONE at design time |
| `~/.config/session-bridge/config.json` | `groupId`, `allowedUserId` (7735693897), `excludePatterns`, poll/sync intervals |
| `~/.local/state/session-bridge/state.json` | update offset, `session ↔ topic_id` map, topic status |
| `~/.local/state/session-bridge/pending/<session>` | reply flag: `{topicId, ts, remaining}` |
| `~/.config/systemd/user/session-bridge.service` | unit file |

## Security model

- Exactly one allowed user ID, one allowed group ID; all else dropped with no response.
- Token file mode 600; token never appears in logs or process args (read from env file).
- Inbound text is injected via tmux buffers only — no shell evaluation anywhere.
- The bridge can only do what a person at the keyboard could do in tmux; it adds no new
  capability surface beyond remote keyboard access for one user.
- House rule respected: topic close/rename fires on observed session disappearance
  (edge-triggered from polling, reversible, non-destructive). No destructive mutation
  ever keys off fuzzy session-end events.

## Failure modes

- **Daemon down:** messages queue server-side at Telegram; offset resume on restart.
  systemd restarts on crash.
- **Hook fails:** one missed tab reply; retried on next Stop via surviving flag; session
  unaffected; `agents` nudges unaffected.
- **Queued-message quirk (known, accepted):** messaging a WORKING session means the
  next Stop may answer its in-flight task, not you; your answer follows on the Stop
  after. The flag therefore carries `remaining: 2` for WORKING injections, so **both**
  the in-flight answer and the answer to your message are forwarded to the tab. The
  bridge still warns in-tab whenever it queues, because the first reply may look like a
  non-sequitur.
- **Modal drift:** if Claude Code's modal strings change, BLOCKED detection degrades to
  WAITING/IDLE and the bridge would paste text at a frozen prompt (expected to be
  benign, but verified during live smoke testing). Heuristics live in one function
  shared conceptually with `agents`; update both together.

## Setup checklist (user steps)

1. ~~Create bot via BotFather~~ — DONE (@WallFred_bot).
2. Create private group "Claude Sessions"; enable Topics in group settings.
3. Add @WallFred_bot as **admin** with Manage Topics.
4. Send any message in the group; the implementer pins the group ID into `config.json`.
5. Approve the two gated installs (auto-mode blocks agent writes here — each is handed
   to Rex as a `!` one-liner if prompted): the Stop-hook entry in
   `~/.claude/settings.json`, and `systemctl --user enable --now session-bridge`.

## Testing

- Unit-ish: classification + transcript-extraction functions get fixture tests (pane
  dumps, sample JSONL) run via `bun test`.
- Live smoke, in order: topic auto-created for a scratch tmux session → inbound text
  appears in its Claude prompt → reply lands in the right tab → WORKING queue warning →
  BLOCKED excerpt + approve presses "Yes" → session kill → topic archived with ✖.

## Future (explicitly deferred)

- Nudges (`agents`) posting into tabs instead of the main-bot DM.
- Inbound photo forwarding to the session.
- Starting a *new* session from Telegram ("open a tab for project X").
