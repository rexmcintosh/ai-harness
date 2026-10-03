# Daily Loop — the operating manual

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> **Where this fits:** This is the day-to-day of *living* in the mesh from
> [ARCHITECTURE.md](ARCHITECTURE.md): reach the always-on host from whatever
> device is in your hand, work inside a `tmux` session that never dies, push to
> GitHub, and pick up from the exact same place on any other device.

## The mental model (one more time, because it's everything)

- **The device in your hand is a window.** Closing it changes nothing.
- **Your session lives on the host** (the VPS) in `tmux`. It keeps running while
  you sleep, travel, or switch devices.
- **Your code lives in GitHub.** Pushed daily, it survives any machine dying.

So you can never "lose your place" by closing a laptop, and never "lose code"
because it's already on GitHub.

> **Until the VPS is provisioned:** the loop is identical against the Mini —
> substitute `bebop_admin@mini` for `dev@vps`, and install tmux once with
> `brew install tmux`. Everything below works the same. Once the VPS is up
> ([`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md) Phases 1–3 +
> [MIGRATION.md](MIGRATION.md) Phase B), switch to `dev@vps`.

---

## A day in the loop

### 1 · Start (or resume) work — from any device

One tap in **Termius** (save this as a per-project snippet):
```bash
cd ~/projects/<repo> && tmux new -As <repo> && claude
```
Or by hand:
```bash
ssh dev@vps
tmux new -As <repo>     # attaches if it exists, else creates it
claude
```
`new -As <repo>` is the magic: **the same command resumes an existing session or
starts a fresh one.** You never have to remember which.

### 2 · Work

- Claude works on a `feat/<slug>` branch, commits small and often, pushes, and
  opens a **draft PR** — per the project conventions in
  [`../setup/templates/CLAUDE.md`](../setup/templates/CLAUDE.md).
- The **Venice review council** critiques the PR; you read it, tell Claude what
  to fix, then merge. CI deploys. (Full pipeline:
  [`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md) Phases 6–7.)
- Handy `tmux` layout: window 0 = Claude, window 1 = a shell, window 2 = logs.

### 3 · Suspend — just walk away

Detach and leave everything running:
```
Ctrl-b  then  d
```
Or simply close Termius / shut the laptop lid. **The session keeps running on the
host.** There is no local state to save, because there is no local state.

### 4 · Before you stop for the day — the one discipline

**Everything committed and pushed to GitHub.** That's the whole rule.

```bash
git -C ~/projects/<repo> status     # anything uncommitted?
git -C ~/projects/<repo> push       # send it up
```
Or, on the Mini, sweep everything at once:
```bash
scripts/audit-projects.sh           # every project should show unpushed 0
```
Why bother, if the host is always on? Because daily push caps your worst case.
If the host ever died mid-week, you'd lose at most *today*, not the week. Code
never lives only on the host.

### 5 · Resume on another device

Pick up your phone, run the same snippet (or):
```bash
ssh dev@vps -t "tmux attach -t <repo>"
```
You land in the exact same session — Claude mid-thought, your shell history,
everything. The work never paused; it ran on the host while you moved.

---

## tmux in 60 seconds (your survival kit)

`tmux` is what keeps your work alive on the host. `Ctrl-b` is the "prefix": press
and release it, *then* press the next key.

| I want to… | Do this |
|---|---|
| attach to my project (or start it) | `tmux new -As <name>` |
| see what sessions are running | `tmux ls` |
| leave a session running (detach) | `Ctrl-b` then `d` |
| open a new window | `Ctrl-b` then `c` |
| next / previous window | `Ctrl-b` then `n` / `p` |
| jump to window number N | `Ctrl-b` then `<N>` |
| scroll back / read logs | `Ctrl-b` then `[`  (press `q` to exit) |
| rename the current window | `Ctrl-b` then `,` |
| kill a session for good | `tmux kill-session -t <name>` |

That's enough to live in it. Everything else is bonus.

---

## Switching devices mid-task

Detach on the Mac (`Ctrl-b d`), attach on the phone — same session. You're not
copying anything; you're just pointing a different window at the same running
work on the host.

## After a host reboot

`tmux` sessions do **not** survive a reboot — the running Claude process is gone.
But your repos on disk and everything pushed to GitHub are intact. Just re-create
the session and carry on from your last commit:
```bash
ssh dev@vps
cd ~/projects/<repo> && tmux new -As <repo> && claude
```
(One more reason commits should be small and frequent.)

---

## End-of-day checklist

- [ ] All work committed on its feature branch.
- [ ] Pushed to GitHub (`git push`); draft PRs opened where ready.
- [ ] `tmux ls` shows your sessions (leaving them running is fine — that's the point).
- [ ] Optional: `scripts/audit-projects.sh` shows **0 unpushed** everywhere.

If those are green, you can close every device and walk away. Tomorrow, from any
device, one tap puts you right back.

---

## See also

- [ARCHITECTURE.md](ARCHITECTURE.md) — the rule and the *why*.
- [TAILSCALE.md](TAILSCALE.md) — how you reach the host privately, from anywhere.
- [MIGRATION.md](MIGRATION.md) — how projects got onto the host (Phase B = `--to-host dev@vps`).
- [`../setup/PLAYBOOK.md`](../setup/PLAYBOOK.md) — the VPS + Venice review pipeline and the screenshot-from-phone workaround (Phase 8).
