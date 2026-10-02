# Mac Mini Setup — the home anchor

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> **Where this fits:** In [ARCHITECTURE.md](ARCHITECTURE.md) the Mini has two
> jobs: **workstation when you're home** and **always-on backup anchor** for the
> whole mesh. This doc configures both. Everything here is also the **shared
> foundation** that would let the Mini double as your session host later — so
> it's worth doing regardless of the VPS-vs-Mini decision.

This is the one setup doc you can fully execute **today**, with the Mini in front
of you. Work top to bottom; each section has a concept, the steps (GUI + CLI),
and a verify command.

> **Paste tip (macOS zsh):** some blocks below carry `# comments`. macOS's
> interactive zsh does **not** treat `#` as a comment when pasted, so the comment
> becomes stray arguments (you'll see errors like
> `grep: #: No such file or directory`). Fix it once with
> `setopt interactive_comments` (add that line to `~/.zshrc` to make it
> permanent) — or simply don't paste the `# ...` part.

> **Check current state first** (read-only, no `sudo`; this block is comment-free
> and paste-safe):
> ```bash
> pmset -g | grep -E 'sleep|womp|autorestart|disksleep'
> fdesetup status
> nc -z localhost 22 && echo "Remote Login ON" || echo "Remote Login OFF"
> tmutil destinationinfo
> ls -d /Applications/Backblaze.app 2>/dev/null && echo "Backblaze installed"
> ```
> These report, in order: **power/sleep** settings · **FileVault** on/off ·
> whether **Remote Login** is on · the **Time Machine** destination · whether
> **Backblaze** is installed.

---

## 1. Never sleep

**Concept:** A backup target and a Tailscale node are useless asleep. A
"never-sleep" desktop stays reachable and keeps its jobs running. (The *display*
can still sleep — that just turns the monitor off and saves power.)

The durable, native way is `pmset` (power management settings). These persist
across reboots and don't depend on any app staying open:

```bash
sudo pmset -a sleep 0          # never sleep the system itself
sudo pmset -a disksleep 0      # keep disks spun up (matters for a backup target)
sudo pmset -a womp 1           # wake for network access
sudo pmset -a autorestart 1    # power-cut → reboot automatically when power returns
```

GUI equivalent: **System Settings → Energy** — turn on *"Prevent automatic
sleeping when the display is off,"* *"Start up automatically after a power
failure,"* and *"Wake for network access."*

- **`caffeinate`** is the tool for *temporary* wakefulness — keep the Mac awake
  for one long task without changing settings:
  ```bash
  caffeinate -dimsu        # awake until you Ctrl-C
  caffeinate -dimsu make   # awake only while `make` runs
  ```
- A keep-awake app like **Amphetamine** does the same via a GUI. Fine to use,
  but it only works while the app is running — set the `pmset` baseline above so
  the Mini stays awake even if the app quits or you log out.

**Verify:**
```bash
pmset -g | grep -E 'sleep|womp|autorestart'
# sleep should read 0; autorestart 1; womp 1
```

---

## 2. Enable Remote Login (SSH)

**Concept:** "Remote Login" turns on macOS's built-in SSH server, so you can
`ssh` into the Mini from your MacBook or iPhone — including over Tailscale, by
name, from anywhere. This is what makes "drive the home machine from my phone"
work.

**Turn it on** — GUI: **System Settings → General → Sharing → Remote Login =
On.** Click the ⓘ to limit it to your user account. Or CLI:
```bash
sudo systemsetup -setremotelogin on
```

**Reach it over the mesh.** Your login name on the Mini is your **macOS account
name** (not `dev` like the VPS). With Tailscale + MagicDNS:
```bash
ssh <your-mac-username>@mini
```

**Use a key, not a password** (especially for the iPhone):
1. In **Termius** (iPhone/Mac), generate or pick an SSH key; copy its **public**
   key.
2. On the Mini, add it:
   ```bash
   mkdir -p ~/.ssh && chmod 700 ~/.ssh
   printf '%s\n' "ssh-ed25519 AAAA...your-public-key... termius" >> ~/.ssh/authorized_keys
   chmod 600 ~/.ssh/authorized_keys
   ```
3. Connect from Termius to host **`mini`** as your Mac username → you land in a
   shell on the Mini, over the private mesh. **That's your first "phone → home
   machine" win — before the VPS even exists.**

**Security note:** because you reach the Mini over Tailscale, you do **not** need
to forward port 22 on your home router. Keep the router closed; let Tailscale be
the private door. (Local-network SSH stays available too.)

**Verify:**
```bash
# from the MacBook:
ssh <your-mac-username>@mini 'hostname && echo SSH-OK'
```

---

## 3. Time Machine (local backup)

**Concept:** Time Machine is macOS's automatic, hourly, *versioned* backup to an
external drive. It's the Mini's job as the mesh's **local backup anchor** — fast
full-history restores of anything not in Git (configs, documents, app data).

**You need a backup disk:** an external USB/Thunderbolt drive (rule of thumb:
≥ 2× the data you want backed up), or a NAS. Plug it in, then:

- GUI: **System Settings → General → Time Machine → Add Backup Disk** → pick the
  drive → **encrypt it** (recommended — the backup leaves nothing to chance if
  the drive is lost/stolen) → Done. It begins hourly backups automatically.
- CLI equivalent:
  ```bash
  sudo tmutil setdestination -a /Volumes/<YourBackupDrive>
  tmutil startbackup --auto
  ```

**Optional exclusions** (save space — these are reproducible): System Settings →
Time Machine → **Options** → add `node_modules`, build caches, etc. Your code is
in Git anyway.

**Verify:**
```bash
tmutil destinationinfo     # shows the configured drive
tmutil latestbackup        # shows the most recent snapshot once one completes
```

---

## 4. Backblaze (offsite backup)

**Concept:** Time Machine protects you from disk failure and fat-fingers.
**Backblaze** protects you from fire/theft/flood — a continuous, unlimited
*offsite* copy in the cloud. Together they satisfy the classic **3-2-1 rule**
(3 copies, 2 media, 1 offsite). GitHub already covers your code offsite; this
covers everything else on the Mini.

**Install:**
1. Download **Backblaze Personal Backup** from <https://www.backblaze.com>.
2. Install, create/sign-in to an account, let it run. It continuously backs up
   the internal drive **and attached external drives**.
3. Cost: roughly **$9/mo or $99/yr, unlimited** (verify current pricing). Default
   version history is 30 days; extendable.

**Know the caveats:**
- The **first** backup can take days for large datasets — that's normal; it
  throttles in the background. Settings let you cap/uncap bandwidth.
- External drives must be **reconnected ~every 30 days** or Backblaze purges
  their backup. Network/NAS volumes aren't covered by Personal Backup.

**Verify:** the Backblaze menu-bar app shows *"You are backed up"* / a remaining
count once the initial pass completes.

---

## 5. Tailscale at boot

**Concept:** After a reboot the Mini must rejoin the tailnet **on its own**, with
no one typing anything — otherwise "always reachable" breaks every power blip.

In the **Tailscale** menu-bar app → **Settings**, confirm:
- **"Launch Tailscale at login"** — **on** (starts when you log in).
- **"Allow incoming connections"** — **on**. This is what lets your other devices
  reach the Mini; if it's off, your phone/MacBook can't SSH in.
- **"Use Tailscale DNS settings"** — **on** (MagicDNS).

> **Mac App Store limitation:** the sandboxed App Store build offers **only**
> "Launch at login" — there is **no "run unattended"** option, so Tailscale starts
> only *after a user logs in*. True unattended reconnection (at the login screen,
> nobody signed in) needs either the **standalone** Tailscale build from
> tailscale.com (it installs a background system service) or macOS **automatic
> login** — and **FileVault gates both** at the unlock screen anyway (see the
> caveat below). For an always-on Mini that's rarely rebooted and usually logged
> in, **"Launch at login" is enough** — don't over-engineer it unless the Mini
> becomes your always-on host.

So: a power cut → `pmset autorestart 1` reboots the Mini → it reconnects as soon
as your user session logs back in (instantly, if you stay logged in).

> **CLI on PATH (optional):** that **"CLI integration → Show me how"** button
> tells you how to symlink the `tailscale` command into your PATH, so you can run
> `tailscale status` directly instead of the long `/Applications/Tailscale.app/...`
> path.

> ### ⚠️ The FileVault reboot caveat (read this)
> **FileVault encrypts your disk — keep it on.** But there's an unavoidable
> trade-off for an always-on box: after an **unexpected reboot** (power loss),
> macOS boots to the **FileVault unlock screen** and runs *nothing* — no
> Tailscale, no SSH, no login items — until someone **physically types the
> password.** So "reachable after a home power cut while I'm away" is **not**
> guaranteed while FileVault is on. This is by design and can't be safely
> automated away.
>
> Your options:
> - **Keep FileVault on** (recommended) and accept that a power-loss reboot needs
>   a manual unlock. Fine if you're usually home and power is stable. For a
>   *planned* reboot, `sudo fdesetup authrestart` reboots straight past the lock
>   once.
> - **Disable FileVault** to allow fully unattended reconnection — but the disk
>   is then unencrypted at rest. A real security downgrade; only consider it if
>   the Mini holds nothing sensitive.
>
> This exact friction is what a **headless Linux VPS avoids** (no disk-unlock
> gate; it reconnects itself). If "always reachable even after a power cut while
> traveling" becomes a hard requirement, that's a strong vote for the VPS as the
> always-on host. For now: FileVault on + know the caveat.

---

## Verification checklist

- [ ] `pmset -g` shows `sleep 0`, `autorestart 1`, `womp 1`.
- [ ] `ssh <user>@mini` works from the MacBook (and from the iPhone via Termius).
- [ ] Router port 22 is **not** forwarded (Tailscale is the only remote door).
- [ ] Time Machine has an **encrypted** destination and a completed backup.
- [ ] Backblaze shows *"You are backed up"* (after the initial pass).
- [ ] Tailscale set to start at login / run unattended; auto-update on.
- [ ] You understand the FileVault reboot caveat and made a deliberate choice.

---

## Glossary (additions)

- **`pmset`** — macOS power-management CLI; sets durable sleep/wake/restart
  behavior.
- **`caffeinate`** — macOS tool to keep the Mac awake temporarily (one command or
  one task), without changing settings.
- **Remote Login** — macOS's name for "enable the SSH server."
- **Time Machine** — macOS's built-in hourly, versioned local backup.
- **Backblaze (Personal Backup)** — continuous, unlimited offsite cloud backup
  for a Mac and its attached drives.
- **FileVault** — macOS full-disk encryption. Protects data at rest; requires a
  manual unlock at boot.
- **3-2-1 backup** — 3 copies of your data, on 2 kinds of media, 1 of them
  offsite. Time Machine + Backblaze + GitHub satisfy it.
