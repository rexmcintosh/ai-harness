# Orphaned `codex` processes

*Written 2026-09-17 after two leaked Codex reviews were found and killed by hand.*

## What happened

`ps` on mesh-vps showed two `codex exec review` processes that no session owned:

```
    PID    PPID     ELAPSED COMMAND
2430396       1  47-20:29:37 .../@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex exec review -c sandbox_mode="read-only" --model gpt-5.6-sol -c model_reasoning_effort=high --skip-git-repo-check -- Review the full diff of branch claude/writing-engine-2 against main...
1413812       1  18-20:22:43 .../bin/codex exec review -c sandbox_mode="read-only" --model gpt-5.6-sol -c model_reasoning_effort=xhigh --skip-git-repo-check -- You are reviewing one task's implementation...
```

47 days and 18 days of elapsed time, `PPID 1` (reparented to init), each holding a
`codex-code-mode` child, about 18 MB RSS apiece. Both had `cwd` in
`~/projects/ultimate-portugal`. Nobody was waiting for either answer. On a box with
7.6 GiB of RAM and a documented OOM history, stranded processes are not free.

## Why it happens

`codex` on `PATH` is a Node wrapper. It spawns the native musl binary as a child and
waits on it. The wrapper and the worker are two processes, in the same process group
but with no supervision between them.

The delegate plugin's Codex helper —
`~/.claude/plugins/cache/authority-hacker-plugins/delegate/<version>/skills/codex/scripts/codex_chat.py`
(`installed_plugins.json` lists 0.4.0; the cache also carries 0.5.0) — runs:

```python
result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout, ...)
```

with `DEFAULT_TIMEOUT` of 2400 seconds. On `TimeoutExpired`, `subprocess.run` kills the
process it started — the wrapper — and nothing else. The worker keeps running and is
reparented to init. The same thing happens when the calling Claude session is killed
(OOM, a reaped tmux pane, a closed background session): the wrapper dies with the
session, the worker does not.

Reproduced locally with a stub wrapper that spawns a `sleep` child, called through
`subprocess.run(..., timeout=2)`:

```
subprocess.run timeout fired: wrapper killed
worker pid 1657316 survived, ppid is now 1
    PID    PPID     ELAPSED COMMAND
1657316       1       00:02 sleep 300
```

That is the whole mechanism. No Codex API call is needed to show it.

## What this repo does about it

Detection only. `watchdog/triage.py::check_orphan_processes` reads
`ps -eo pid,ppid,etime,args` (collected in `watchdog/run.py::collect`) and fires
`proc:orphans` at **warn** when a `codex` process has `PPID 1` and at least 6 hours of
elapsed time. It reports pid, age and the head of the command line.

The watchdog never kills anything, by design (`watchdog/README.md`). Killing a leaked
review is a one-line operator action once the alert names the pid:

```bash
kill -TERM <pid>          # the worker; its codex-code-mode child goes with it
```

## Proposed fix at the source

The plugin is third-party and its cache directory is overwritten on update, so nothing
here edits it. The fix belongs upstream, or in a local fork the owner maintains
deliberately. Put the worker in its own process group and kill the group:

```python
# codex_chat.py — run_codex()
proc = subprocess.Popen(
    cmd, cwd=cwd, stdin=subprocess.DEVNULL,
    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    start_new_session=True,                 # own process group, so we can signal the tree
)
try:
    stdout, stderr = proc.communicate(timeout=timeout)
except subprocess.TimeoutExpired:
    os.killpg(proc.pid, signal.SIGTERM)      # wrapper AND worker
    try:
        proc.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGKILL)
    raise
```

That covers the timeout path. It does not cover the killed-session path: if the parent
Python process is `SIGKILL`ed, no handler runs. For that, either the wrapper has to
watch its parent (`PR_SET_PDEATHSIG` on Linux), or something outside has to sweep —
which is what the watchdog check is for.

## Checking by hand

```bash
ps -eo pid,ppid,etime,args | awk '$2 == 1' | grep -E '(^|/)codex' 
```
