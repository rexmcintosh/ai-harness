# mcp-lazy: start the browser helper only when a session uses it

Claude Code and Codex start every configured stdio MCP server when a session
starts, and keep it running until the session ends. On lima-vps each session
started its own Playwright MCP server (`npx -y @playwright/mcp@latest`), about
120 MB each. On 2026-11-25, 15 copies were running and none had a browser open:
about 1.8 GB of memory for nothing. Claude Code has no setting that starts a
local MCP server on first use. The docs describe deferred start only for HTTP
servers.

`mcp_lazy.py` stands in for the real server. It is plain Python 3 with no
dependencies.

## How it works

- **Session start:** the stand-in answers `initialize` and `tools/list` from a
  cache. The session sees all 25 browser tools as usual. No real server runs.
- **First browser call:** the stand-in starts the real server, replays the
  client's own `initialize` parameters, and sends `notifications/initialized`
  itself. From then on it passes every message through unchanged, in both
  directions. Server-to-client requests such as `roots/list` pass through too.
- **Cache:** `~/.cache/mcp-lazy/playwright-<hash>.json`. The key is the full
  real command line plus the client's whole `initialize` parameters: protocol
  version, client name and version, and client capabilities. With
  `--key-env PLAYWRIGHT_`, any environment variable whose name starts with
  `PLAYWRIGHT_` is part of the key too, because Playwright MCP reads settings
  from such variables. Claude Code
  (`claude-code`, protocol `2025-11-25`) and Codex (`codex-mcp-client`,
  protocol `2025-06-18`) each get their own entry; their tool lists are the
  same. A client update gives a new key, so it costs one short probe. Cache
  files unused for 30 days are deleted.
- **Cache miss:** the stand-in starts the real server once, records its
  answers, stops it and stays lazy. This took 0.4 to 0.9 s. A lock file next
  to the cache makes sessions that start together share one probe. A session
  that waits more than 10 s for that lock does not start a second probe; it
  passes straight through to a real server instead, so its start stays inside
  the client's own limit (30 s for Codex here).
- **Own requests:** the stand-in's own request ids carry a random part, and it
  takes back only the replies to ids it has sent, so a client id can never
  be mistaken for one of its own.
- **Refresh:** after every real start, the stand-in asks for the tools again.
  If the list changed, it updates the cache and sends
  `notifications/tools/list_changed`.
- **Requests it cannot answer:** a request outside the cached capabilities
  (for example `resources/list`, when the server offers only tools) gets
  "method not found", which the real server would also send. Any other request
  starts the real server.
- **Failures:** if the cache cannot be built, the stand-in passes the session
  straight through to the real server. If the real server will not start for a
  call, that call gets an error and the stand-in stays up, so a later call can
  try again. If the real server exits, the stand-in exits with its code. If
  the real server stops reading its input, the stand-in ends the session
  (exit 1) instead of leaving requests unanswered.
- **Stopping:** at the end of input, or on SIGTERM, SIGINT or SIGHUP, the
  stand-in closes the server's input. The Playwright server then closes its
  browser and exits. The real server runs in its own process group. Anything
  still running after 5 s, in that group or below the server, gets SIGTERM,
  then SIGKILL after 3 s more. That includes a process the server started
  during shutdown and left behind. If the stand-in itself is killed with
  SIGKILL, the server sees the end of its input and exits.
- **A server that stops answering:** if the real server closes its output but
  keeps running, the stand-in stops it the same way and exits.

## Install, check, undo

```sh
setup/mcp-lazy/install.py            # install or repair, then check
setup/mcp-lazy/install.py --check    # check only; exit 1 if anything differs
setup/mcp-lazy/install.py --undo     # put the original entries back
```

The installer:

- copies `mcp_lazy.py` to `~/.local/bin/mcp-lazy`;
- sets the user-scope `playwright` entry in `~/.claude.json` with
  `claude mcp remove` and `claude mcp add-json`, because live sessions rewrite
  that file;
- changes only the `command` and `args` lines of `[mcp_servers.playwright]` in
  `~/.codex/config.toml`. `startup_timeout_sec` and every other line stay the
  same. It refuses the edit if anything else would change.

Before it replaces anything, the installer saves the old copy in
`~/.local/state/mcp-lazy/backups/`. The folder is owner-only and each copy is
mode 600, because an entry's `env` can hold secrets. The Claude entry keeps
every field except `command` and `args`, so an `env` or any other field stays.
The first time it replaces a helper entry that is not the stand-in, the
installer records that entry's command and args in
`~/.local/state/mcp-lazy/baseline.json`, for `--undo`.

The resulting command for both tools is:

```
~/.local/bin/mcp-lazy --name playwright --key-env PLAYWRIGHT_ -- npx -y @playwright/mcp@0.0.84 --browser chromium
```

`--undo` puts back the recorded command and args in both files. If a file has
no record, `--undo` leaves that file alone and says so; restore it from the
backups folder instead. On lima-vps the record holds
`npx -y @playwright/mcp@latest --browser chromium` for both. `--undo` leaves
`~/.local/bin/mcp-lazy` in place, unused.

Sessions that were already running keep their old helper until they restart.

### The Codex entry and the Claude mirror

The Codex `playwright` entry sits inside the `# BEGIN managed Claude mirror`
block that `setup/claude-codex-mirror/settings.py` writes. That tool copies
the entry from `~/.claude.json`. On 2026-11-25 it already refused to refresh,
because Codex had added other lines to the block. A later refresh would copy
the new `~/.claude.json` entry, which is the same stand-in, so the two stay in
agreement.

## The pinned version

The helper is pinned to `@playwright/mcp@0.0.84`. Before this change,
`@latest` resolved to 0.0.86. That version expects Chromium build 1255, which
is not installed, so every browser call failed with "Browser
chrome-for-testing is not installed". Version 0.0.84 uses build 1252, which is
installed in `~/.cache/ms-playwright`.

To move to a newer version:

1. Install its browser, for example
   `npx -y @playwright/mcp@<version> install-browser chrome-for-testing`.
2. Change `REAL` in `install.py`.
3. Run `install.py`.

The new command line gives a new cache key, so the first session of each
client builds a fresh cache.

## Tests

`test_mcp_lazy.py` drives the stand-in over real pipes against
`fake_mcp_server.py`. It covers:

- the cache miss, the cache hit and pass-through;
- the replayed handshake;
- a server-to-client round trip;
- a 3 MB message;
- tool lists split over several pages;
- list changes;
- start failures and corrupt caches;
- client ids that look like the stand-in's own;
- a server that stops reading its input, and one that closes its output;
- a process the server starts during shutdown;
- a failed Claude entry change, with and without a working rollback;
- a busy probe lock (pass-through, not a second probe);
- four sessions that start at once (one probe);
- repeated SIGTERM;
- cleanup on end of input, on SIGTERM (with a server that ignores end of input
  and has a child process), on SIGKILL of the stand-in, and on a server crash.

`test_mcp_lazy_install.py` covers the Codex config edit and the Claude entry
check.

```sh
.venv/bin/python -m pytest setup/mcp-lazy -q
```

These checks with the real helper passed on lima-vps on 2026-11-25:

- **Direct:** with the stand-in at the start of a session, no helper process
  ran and the stand-in used 16 to 17 MB. The first `browser_navigate` started
  the helper in 0.3 to 0.5 s and opened the page. With the browser open, all
  three ways of stopping the session (end of input, SIGTERM, SIGKILL) left no
  `npm exec`, `node` or Chrome process behind.
- **Claude Code 2.1.331:** two `claude -p` sessions opened a page through the
  stand-in. A third session that did not use the browser never started the
  helper.
- **Codex 0.172.0:** `codex exec` opened a page through the stand-in.
