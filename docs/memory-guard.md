# Memory guard and caps on lima-vps

The Mac mini VM (`lima-vps`) has 11.65 GiB of RAM and a 4 GiB swapfile.
`setup/vps-to-mini/memory-guard.sh` sets how the machine behaves when memory
runs short. It is idempotent: run it again after a rebuild, or with `--check`
to see the live state.

## Why it exists

earlyoom stopped processes 73 times in November 2026.

- 58 of the stops happened before the swapfile existed (it was added on
  2026-11-05 at 08:51 UTC).
- The bursts came from heavy work in several agent sessions at the same time:
  - website builds of about 1 GB each, repeated after each stop;
  - Vitest runs with 4 to 5 workers of 0.5 to 1.2 GB each;
  - 466 `codex exec` runs in one evening;
  - on 2026-11-18, a test whose fake database ignored paging. The test grew to
    8 GB and ran three times.
- Idle sessions held the baseline. The median over 2026-11-19 to 11-25 was
  4.6 GiB of RAM plus 1.7 GiB of swap. It came from long-lived Claude
  desktop-app chats, T3 threads, a standalone Codex server and one Playwright
  MCP server per session.
- The old earlyoom settings gave +300 to every process named `node`, `claude`,
  `codex` or `MainThread`. That covered the T3 server (`node-MainThread`) and
  the nutrition tracker (`node`).
- Together with `DefaultOOMScoreAdjust=300` for user services, those settings
  ranked a 31 MB tracker and the T3 server above the 8 GB test on 2026-11-18.
- The tracker then stayed down for six days. earlyoom stops with SIGTERM, and
  `Restart=on-failure` treats SIGTERM as a clean exit.

earlyoom 1.7 scores processes mostly by `oom_score_adj`, not by size. A
process's score is roughly (1000 + adj + 1000 × memory ÷ (RAM + swap)) × 2/3.
`--prefer` adds 300 to that score and `--avoid` subtracts 300.

## What is set

| Layer | Setting | Effect |
|---|---|---|
| `/etc/default/earlyoom` | `--prefer (vitest\|esbuild\|workerd\|wrangler)` | Build and test helpers go first. |
| | `--avoid (systemd\|sshd\|dbus-daemon\|tailscaled\|tmux\|earlyoom\|lima-guestagent\|dockerd\|containerd\|node-MainThread)` | 300 points less likely to be picked: the user manager, the SSH and network daemons, Docker, and the T3 server. This lowers their priority but does not make them immune. Stopping T3 stops every T3 thread. |
| `nutrition-tracker`, `finance-tracker` | `OOMScoreAdjust=-100`, `Restart=always` | Ranked below agent work. Back within 5 s after any stop. |
| Each T3 session (`t3code-claude-*`, `t3code-codex-*`, `t3code-terminal-*` scopes) | `MemoryMax=4G`, `MemorySwapMax=1G`, `OOMPolicy=continue` | One session's builds and tests cannot take the machine. |
| Each login session (`session-*.scope`) | `MemoryMax=6G`, `MemorySwapMax=2G`, `OOMPolicy=continue` | Covers SSH shells, the Claude desktop-app server with all its chats, and a standalone Codex server. |

When a group reaches its cap, the kernel's OOM killer stops a process in that
group only. It picks by `oom_score`. Inside one group the processes share an
`oom_score_adj`, so the score mostly follows size, and the victim is usually
the largest process. `OOMPolicy=continue` keeps the rest of the group running,
so a runaway test dies and its session survives.

T3 sets its own limit for all T3 sessions together on `app-t3code-agents.slice`:
5.8 GiB soft (`MemoryHigh`) and 8.7 GiB hard (`MemoryMax`) as of 2026-11-25.
This script does not manage that limit. `--check` prints it.

These settings are unchanged:

- `user@1000.service` keeps `OOMScoreAdjust=-500`.
- `/etc/systemd/user.conf.d/10-oom-leaves.conf` keeps
  `DefaultOOMScoreAdjust=300`, so batch timers such as restic and the work
  queue are still easy to stop.
- earlyoom's own override keeps `OOMScoreAdjust=-100` and `Nice=-20`.

## How a run works

1. The script writes each managed file through a temporary file and a rename.
2. It reloads both systemd managers on every run. This does not restart any
   unit.
3. It sets the caps again at runtime on the scopes that already exist.
4. It restarts earlyoom only when the earlyoom file changed, or when the
   running earlyoom has other arguments.
5. It then checks the live state and exits with status 1 if anything differs.
   A run that stopped halfway is therefore completed by the next run.

A lock in `~/.local/state/memory-guard/lock` allows one run at a time. A
second run started while the lock is held exits at once with status 1. Before
it changes anything, the script checks that its tools, sudo and dev's systemd
user manager are available. Any failed write stops the run, so a file is never
replaced without its backup.

The tracker drop-ins are optional. The script installs them only when the
service is installed, and it prints `WARN` when one is missing.

These tests passed on lima-vps on 2026-11-25:

- A test scope capped at 200 MB stopped a 500 MB program. Another process in
  the same scope kept running.
- `--check` failed after one cap was loosened by hand, and after one managed
  file was edited. A plain run repaired both.
- When the earlyoom file was correct but earlyoom ran with the old arguments,
  `--check` failed and a plain run restarted earlyoom.
- A second run started while the lock was held exited at once with status 1.
- With the target folder made immutable, a run stopped with status 1 and left
  the file unchanged. The next run repaired it.
- After a SIGTERM, the nutrition tracker came back within 8 s with
  `oom_score_adj` -100.

The process names were checked on 2026-11-25:

- Vitest workers are named `node (vitest N)`.
- esbuild and workerd are native programs with their own names.
- The `wrangler` CLI usually runs as `node`, so `--prefer` does not match it.
  That costs nothing.
- The T3 server and its launcher are named `node-MainThread`. `--check`
  fails if a T3 server process over 50 MB has a name that `--avoid` does not
  match.

## Known limits

- T3 starts its Claude sessions with `oom_score_adj` 800. In a machine-wide
  shortage, earlyoom therefore stops T3 Claude sessions before jobs in
  desktop-app chats. The per-session caps make that shortage rare.
- Desktop-app chats share one cap, because they all run in the session scope
  of the server that hosts them. Claude Code's built-in
  `CLAUDE_CODE_TOOL_MEMORY_LIMIT` cannot help there: the session scope is
  owned by root, so Claude cannot create its tool cgroup.
- A command stopped at a cap exits with status 137 ("Killed"). An agent can
  still re-run it unchanged.
- The login-session cap applies to every user's sessions, root included.
  `MemoryMax` does not slow a session down. It only stops a process once that
  one session holds 6 GB, and a recovery shell does not come near that.
- `OOMPolicy=continue` cannot be set at runtime. Existing scopes get it from
  the drop-in through `daemon-reload`, and `--check` fails for any scope that
  does not have it.
- Scopes that already exist get the caps in two ways. `daemon-reload` applies
  the drop-ins, and `systemctl set-property --runtime` sets them again. New
  scopes get the drop-ins when they start.

## Check and undo

- Check: `setup/vps-to-mini/memory-guard.sh --check`.
  - It prints the earlyoom arguments, every cap, the service settings and
    earlyoom's current top picks.
  - It exits with status 1 and prints `FAIL` lines when any of these differ
    from the managed values. A plain run applies the settings and then runs
    the same check.
- Backups: before the script replaces a file, it copies the old file to
  `~/.local/state/memory-guard/backups/`. The copy is named after the path,
  with a UTC time stamp.
- Undo:
  1. Restore the previous `/etc/default/earlyoom` from that folder. The
     newest `etc_default_earlyoom.*` file holds the version before the last
     change. On lima-vps the pre-2026-11-25 version is
     `etc_default_earlyoom.20261125-before-memory-guard`. The packaged default
     is `EARLYOOM_ARGS="-r 3600"`.
  2. Delete the `50-memory-cap.conf` and `50-memory-guard.conf` drop-ins named
     in the script.
  3. Run `sudo systemctl daemon-reload` and `systemctl --user daemon-reload`.
  4. Run `sudo systemctl restart earlyoom`.
  5. Runtime caps on existing scopes stay until each scope ends. To lift them
     sooner, run `systemctl set-property --runtime <scope> MemoryMax=infinity
     MemorySwapMax=infinity`, with `sudo` for `session-*` scopes and `--user`
     for `t3code-*` scopes.
