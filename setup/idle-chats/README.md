# idle-chats

Stops Claude desktop chat programs on this server that have been idle for a day.

## Why

The Claude desktop app keeps one CLI program (`~/.claude/remote/ccd-cli/<version>`)
running on the server for every chat in its sidebar. It never stops them. Each one
holds about 300 MB, plus its MCP helpers. Old chats pile up and fill the memory.

A stopped chat is not lost. When you type in it again, the app starts a new program
that resumes the same chat record, with its full history. This was tested on
2026-11-25: a chat stopped on the server came back with its history when a message
was typed in it on the Mac.

## The rule

A chat program is stopped when both of these are more than 24 hours ago:

- the last write to its chat record: `~/.claude/projects/*/<chat id>.jsonl`, and any
  file or folder in the `<chat id>/` folder next to it (subagents, tool output);
- the time the program started (so a chat you just reopened is left alone).

It is never stopped when it has no chat id (`--resume` / `--session-id`) or no chat
record, or when it is the program running the job.

Just before a stop, the record is checked again. The program gets SIGTERM, then
SIGKILL after 20 seconds. Helpers it leaves behind get the same treatment.

Only desktop chat programs are touched. Terminal, tmux, T3 Code and Codex sessions
are not.

## Use

```sh
idle-chats                 # report only: what would be stopped
idle-chats --apply         # stop idle chats
idle-chats --hours 48      # another idle time
```

Each run adds one JSON line to `~/.local/state/idle-chats/runs.jsonl`.
The nightly run writes its report to `~/.local/state/idle-chats/cron.log`.
Exit codes: 0 ok, 1 a program would not stop, 2 another run is in progress.

## Install

```sh
setup/idle-chats/install.sh          # copy to ~/.local/bin/idle-chats, add the cron line
setup/idle-chats/install.sh --undo   # remove both
```

The cron line runs at 04:00 server time (Europe/Lisbon). The installer saves the
old crontab in `~/.local/state/idle-chats/` before every change.

## Limits

- A chat that runs a background task for more than 24 hours without writing to its
  record would be stopped. None do this today.
- A schedule made inside a chat session (a session loop or cron) ends with the program.

## Tests

`python -m pytest setup/idle-chats` covers the chat id parsing, the idle rule, the
record folder, and real stops of fake chat programs: idle stopped with its helper,
active kept, report-only, a program that ignores SIGTERM, a recent start, and a busy lock.
