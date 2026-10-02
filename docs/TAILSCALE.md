# Tailscale — the private wiring of the mesh

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> **Where this fits:** Tailscale is the "wiring" box in
> [ARCHITECTURE.md](ARCHITECTURE.md). It joins all four nodes — VPS, Mac Mini,
> MacBook, iPhone — into one private, encrypted network so they can reach each
> other *by name* from anywhere, without exposing anything to the public
> internet.
>
> **Prerequisite / order:** You can install Tailscale on the Mini, MacBook, and
> iPhone **today**. The **VPS doesn't exist yet** — provision and harden it first
> (`../setup/PLAYBOOK.md` Phases 1–3), then come back and do the VPS section.

---

## What Tailscale is, in one paragraph

Normally, for your laptop to reach a server you'd open a port on the server to
the whole internet and hope the firewall and passwords hold. Tailscale flips
that around: every device you own runs a small agent, logs into *your* account,
and they build direct, encrypted tunnels to each other (using WireGuard). The
result is a private network — your **tailnet** — that only your devices can join.
Your iPhone on cellular can reach your VPS as if they were on the same Wi-Fi,
and **nothing new is exposed to strangers.** No port-forwarding, no VPN server to
run, no firewall holes.

Two features make it feel magical:

- **MagicDNS** — once on, you type `ssh dev@vps` instead of `ssh dev@100.x.y.z`.
  Devices get stable names regardless of where they physically are.
- **Tailscale SSH** — Tailscale can *be* your SSH layer. It authenticates the
  connection using your tailnet identity, so you don't have to copy SSH keys
  around between devices. Your phone can SSH the VPS with no key setup at all.

---

## The plan (5 steps)

1. Create your tailnet (sign in once).
2. Install + sign in on the three devices you have now: **Mac Mini, MacBook, iPhone**.
3. Install on the **VPS** with a tagged auth key, and turn on Tailscale SSH.
4. Turn on **MagicDNS** and name your nodes.
5. Set the **access policy (ACL)** and **verify** the mesh end-to-end.

---

## Step 1 — Create your tailnet

1. Go to <https://login.tailscale.com/start> and sign in with an identity
   provider you control (Google/GitHub/Microsoft/Apple/email). Use the same
   account you'll use everywhere — **every device must log into this same
   account** to land in the same tailnet.
2. That's it — you now have an empty tailnet and the admin console at
   <https://login.tailscale.com/admin/machines>. The free **Personal** plan
   covers up to 100 devices; four is nothing.

Keep the admin console open in a browser tab; several steps below happen there.

---

## Step 2 — Install on the devices you have now

### Mac Mini (home) and MacBook (travel) — macOS

Easiest path (recommended for both Macs):

1. Install the **Tailscale** app from the **Mac App Store**, or:
   ```bash
   brew install --cask tailscale
   ```
2. Launch it, click **Log in**, complete sign-in in the browser, approve the
   VPN/network extension when macOS prompts.
3. The menu-bar icon shows you're connected and your tailnet name
   (`something.ts.net`).

> **Mac Mini specifics** (never-sleep, start-at-login, run-in-background) are
> configured in [MAC-MINI-SETUP.md](MAC-MINI-SETUP.md). The short version: set
> Tailscale to start at login and keep running, so the Mini is always a live
> node. Because the Mini never sleeps and stays logged in, the GUI app is
> sufficient — no headless daemon needed.
>
> *(Advanced: tailscale.com also offers a "standalone" macOS build that can run
> as a system daemon, useful for a truly headless Mac. You don't need it here.)*

### iPhone (travel) — iOS

1. Install **Tailscale** from the **App Store**.
2. Open it, **Sign in** with the same account, allow it to add a VPN
   configuration when iOS asks.
3. Leave the toggle **on**. iOS keeps it connected in the background; it sips
   battery.

After this step, open the admin console — you should see **three machines**
(mini, your MacBook, iPhone). Rename them now if you like (pencil icon): short
names like `mini`, `macbook`, `iphone` make MagicDNS pleasant.

---

## Step 3 — Install on the VPS (after it's provisioned)

> Do this **after** `../setup/PLAYBOOK.md` Phases 1–3. The VPS is a headless
> Linux box, so instead of a browser login we hand it a pre-made **auth key**.

### 3a. Generate a tagged auth key

In the admin console → **Settings → Keys → Generate auth key**:

- **Description:** `vps bootstrap`
- **Reusable:** off (one-time is fine; generate again if you rebuild)
- **Ephemeral:** **off** — we want the VPS to stay a permanent node
- **Tags:** add `tag:server` (this labels the VPS as infrastructure, which the
  access policy in Step 5 keys off of)

Copy the key — it looks like `tskey-auth-xxxxxxxxxxxx`. Treat it like a password.

> If tagging the key complains that the tag has no owner, do **Step 5 first**
> (it defines `tag:server`), then come back here.

### 3b. Install and bring it up with SSH enabled

SSH into the VPS the normal way once (`ssh dev@<vps-ip>`), then:

```bash
# install the Tailscale agent
curl -fsSL https://tailscale.com/install.sh | sh

# join the tailnet as a tagged server, name it "vps", and turn on Tailscale SSH
sudo tailscale up \
  --authkey=tskey-auth-xxxxxxxxxxxx \
  --advertise-tags=tag:server \
  --ssh \
  --hostname=vps
```

What the flags do:
- `--authkey=…` — logs in non-interactively using the key from 3a.
- `--advertise-tags=tag:server` — registers the VPS under `tag:server`.
- `--ssh` — runs Tailscale's built-in SSH server, so other devices can SSH in
  using tailnet identity (no key juggling).
- `--hostname=vps` — what it's called in MagicDNS (`ssh dev@vps`).

Verify locally:
```bash
tailscale status        # should list vps + your other devices
tailscale ip -4         # the VPS's 100.x.y.z tailnet address
```

### 3c. Stop the VPS node from expiring

By default a node's auth expires (~180 days) and it would drop off the tailnet —
bad for an always-on server. In the admin console → **Machines → vps → ⋯ menu →
Disable key expiry.** Do this once.

---

## Step 4 — Turn on MagicDNS

In the admin console → **DNS**:

1. Add a **nameserver** if prompted (Tailscale's default is fine), then
2. Toggle **MagicDNS = on**.

Now every device can reach every other by bare hostname: `vps`, `mini`,
`macbook`, `iphone`. No IP addresses to memorize.

---

## Step 5 — The access policy (ACL)

> **Where:** admin console → **Access controls → JSON editor**
> (`login.tailscale.com/admin/acls/file`). You can do this step **now**, even
> before the VPS exists — pre-defining `tag:server` here is what stops the VPS
> auth key in Step 3a from complaining about an unowned tag. The `tag:server`
> SSH rule simply matches nothing until the VPS joins.

The **access policy** is one JSON document that says who can reach what. The
editor **already contains a default file** — a `grants` "allow all my devices"
rule (keep it) and a default `ssh` rule whose `dst` is `autogroup:self`.

> **`grants` vs `acls`:** Tailscale's newer policy syntax is **`grants`**;
> `acls` is the legacy syntax. Newer tailnets (likely yours) ship a `grants`
> file by default. Both still work and can even coexist, but use whichever your
> file already has — don't mix them needlessly. The examples below use `grants`.
> Also note: most of the default file is **commented-out examples** (`groups`,
> `postures`, `tests`); only the live `grants` and `ssh` blocks matter.

That default SSH rule is the catch worth understanding:

> **`autogroup:self` only covers your *own, untagged* devices.** The VPS will be
> *tagged* (`tag:server`), and tagged devices have no user-owner — so the default
> rule will **not** let you SSH into the VPS. That's exactly why we add a second
> rule targeting `tag:server`.

Easiest path for a newcomer: **select all in the editor, delete, and paste this
complete known-good file**, then **Save**. It keeps the default self-SSH rule and
adds the two things the mesh needs (it's HuJSON — JSON with comments):

```jsonc
{
  // Tags that may be applied to devices, and who may apply them.
  "tagOwners": {
    "tag:server": ["autogroup:admin"],
  },

  // Network reachability (newer "grants" syntax): all your devices reach all.
  "grants": [
    { "src": ["*"], "dst": ["*"], "ip": ["*"] },
  ],

  // Who can use Tailscale SSH, into which devices, as which users.
  "ssh": [
    // Default: SSH into your own UNTAGGED devices (periodic browser check).
    {
      "action": "check",
      "src":    ["autogroup:member"],
      "dst":    ["autogroup:self"],
      "users":  ["autogroup:nonroot", "root"],
    },
    // The VPS is tagged tag:server, so "autogroup:self" above won't match it.
    // Allow SSH into it as the dev user, connecting immediately (no re-check).
    {
      "action": "accept",
      "src":    ["autogroup:member"],
      "dst":    ["tag:server"],
      "users":  ["dev", "autogroup:nonroot"],
    },
  ],
}
```

Line by line:
- **`tagOwners`** — declares `tag:server` and says admins (you) may apply it.
  This is what lets the VPS auth key in Step 3a carry `tag:server`.
- **`grants`** — the default "my devices can reach my devices" (the newer
  successor to `acls`; your tailnet ships with it). Fine for a single-person
  mesh.
- **`ssh`** — two rules. The first is Tailscale's default (SSH between your own
  untagged devices). The second is the one that matters for the mesh:
  `dst: tag:server` = the VPS, `users: ["dev", …]` = you may log in as `dev`,
  `action: "accept"` connects with no extra check.

Use **Preview rules** to sanity-check, then **Save** (the grey button activates
once you've made an edit). Policy changes apply within seconds.

> **More secure variant (optional):** change the SSH rule's `"action"` to
> `"check"` and add `"checkPeriod": "12h"`. Then the first SSH from a device each
> 12 hours pops a quick browser re-auth. Great on a laptop; mildly annoying from
> a phone on a train. Start with `"accept"`; switch to `"check"` once the basics
> work.

Click **Save**. Policy changes apply within seconds.

---

## Step 6 — Verify the mesh

The "is it actually working?" gate. Split into what you can check **now** (the
three devices you have today) and what waits for the **VPS**.

> **macOS CLI tip:** if you installed Tailscale from the **Mac App Store**, the
> `tailscale` command isn't on your PATH — it lives inside the app bundle. Use
> the full path, or add an alias:
> ```bash
> alias tailscale='/Applications/Tailscale.app/Contents/MacOS/Tailscale'
> ```
> (Homebrew `--cask` installs put `tailscale` on PATH already.) The menu-bar
> icon also shows status without any CLI.

### 6a — The three devices (do this now, no VPS needed)

**See them all** (from the Mini or MacBook Terminal):
```bash
tailscale status
# One line per device (mini, macbook, iphone). A "-" in the last column means
# idle, not offline — an actual outage prints the word "offline".
```

**Prove the tunnels carry traffic** — ping the other devices by their MagicDNS
name (use *your* device names from `tailscale status`):
```bash
tailscale ping rexmb              # your MacBook
# pong from rexmb (100.x.y.z) via 192.168.x.x:41641 in 2ms      <- direct, same LAN

tailscale ping iphone-se-gen-3    # your iPhone
# pong from iphone-se-gen-3 (100.x.y.z) via <public-ip>:port in 20ms  <- direct over the internet

ping -c 3 rexmb                   # plain ICMP — also proves MagicDNS resolves the name
```
Reading the result: `via <LAN-ip>` = a direct tunnel on your local network;
`via <public-ip>` = a direct tunnel across the internet (e.g. phone on
cellular); `via DERP` = relayed through Tailscale's servers (slower, still
fine). **Any `pong` = success.**

> `tailscale ping` is round-trip, so a `pong` proves the peer is reachable **and**
> replying — you don't have to repeat the test from every device. If a peer
> doesn't answer, wake it / open its Tailscale app and retry.

**Checklist for now:**
- [ ] `tailscale status` lists all three devices (none "offline").
- [ ] `tailscale ping` to each other device returns a `pong`.
- [ ] MagicDNS short names resolve (`ping mini`, `ping rexmb`).

### 6b — With the VPS (do this after PLAYBOOK Phases 1–3)

```bash
tailscale ping vps
# Expect "pong" — direct ideal, "via DERP" also works.

ssh dev@vps
# Logs you in with NO SSH key set up on this device — that's Tailscale SSH using
# your tailnet identity. Type `exit` to leave.
```
- In Termius (iPhone/Mac), set the host's hostname to **`vps`** (MagicDNS) and
  connect — you should land on the VPS over the private mesh.
- In the admin console, the VPS shows **Expiry: Disabled** and the
  **tag:server** badge.
- **The promise test:** turn the Mac Mini *off* — you can still `ssh dev@vps`
  from the MacBook/iPhone. Active work never depended on the Mini.

If 6a passes now and 6b passes once the VPS is up, the wiring is done.

---

## How this relates to the VPS firewall

The bootstrap script (`../setup/bootstrap-vps.sh`) opens **22 (SSH), 80, 443**,
and the Mosh UDP range. Tailscale does **not** require any inbound port — it
makes outbound connections and traverses NAT on its own, so adding Tailscale
changes nothing about your firewall.

You now have **two** ways to reach the VPS:
1. **Public** — `ssh dev@<vps-ip>` on port 22 (works from anywhere, even devices
   not on your tailnet; e.g. a borrowed machine).
2. **Private** — `ssh dev@vps` over Tailscale (no exposed port involved).

Both are fine and the design intentionally keeps public 22/80/443 open. Prefer
the Tailscale name day-to-day.

> **Optional hardening — ✅ DONE 2026-06-05.** Once you trust the mesh, you lock
> the VPS so it's reachable *only* over Tailscale: drop public 22/80/443 (+ the
> Mosh range) in UFW and allow everything on the `tailscale0` interface instead.
> That removes the box's entire public inbound surface (the public IP stays as the
> outbound pipe). This was done on the live VPS — the full runbook, rollback, the
> idempotent re-apply script (`../setup/harden-mesh-only.sh`), and the
> `tailscale serve` recipe for future private web services all live in
> **[MESH-ONLY-LOCKDOWN.md](MESH-ONLY-LOCKDOWN.md)**. End state: only `tailscale0`
> + `udp/41641` are allowed inbound. Reach the box with `ssh dev@vps` as always.

---

## Point Termius at the mesh

In Termius (Mac + iPhone), edit your VPS host:
- **Hostname:** `vps` (MagicDNS) instead of the raw IP.
- Keep Mosh enabled for connection resilience (see PLAYBOOK Phase 8).

Now the same one-tap "land in my Claude session" snippet works identically at
home or on the road, because `vps` resolves everywhere via MagicDNS.

---

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| `ssh dev@vps` says *host not found* | MagicDNS off, or device not on the tailnet. Check `tailscale status`; enable MagicDNS (Step 4). |
| A device shows **offline** in the console | Its Tailscale agent isn't running. Re-open the app (Mac/iOS) or `sudo tailscale up` (VPS). |
| VPS dropped off after months | Key expiry — you forgot Step 3c. Re-auth and **Disable key expiry**. |
| Auth key rejected: *tag not owned* | `tag:server` isn't defined. Do Step 5 (the `tagOwners` block), then re-run `tailscale up`. |
| `ssh dev@vps` asks for a password / key | Tailscale SSH not enabled or ACL `ssh` block missing. Re-run with `--ssh`; confirm the `ssh` rule in the policy. |
| `tailscale ping` works but is slow ("via DERP") | It's relaying through Tailscale's servers because a direct path couldn't form. Still works; usually resolves itself. Not urgent. |

---

## Verification checklist

- [ ] Tailnet created; all four devices logged into the **same** account.
- [ ] Mini, MacBook, iPhone installed and showing **online**.
- [ ] VPS joined with `--ssh`, tagged `tag:server`, **key expiry disabled**.
- [ ] MagicDNS **on**; nodes named `vps` / `mini` / `macbook` / `iphone`.
- [ ] Access policy saved with `tagOwners` + `ssh` blocks.
- [ ] `ssh dev@vps` works from the MacBook with **no manual SSH key**.
- [ ] `ssh dev@vps` works from the iPhone (Termius, hostname `vps`).
- [ ] Mini powered off → VPS still reachable. ✅ design promise proven.

---

## Glossary (additions)

- **Tailnet** — your private Tailscale network; the set of devices on your
  account.
- **WireGuard** — the fast, modern encryption protocol Tailscale builds on.
- **MagicDNS** — Tailscale's feature that gives each device a memorable name and
  resolves it on every other device.
- **Tailscale SSH** — Tailscale acting as the SSH authenticator, so you don't
  manage SSH keys per device; access is governed by the policy file.
- **ACL / access policy** — the single JSON document defining who can reach what
  (and who can SSH as which user).
- **Tag (`tag:server`)** — a label you attach to a device (instead of tying it to
  a person) so policies can refer to "the servers" as a group.
- **Auth key** — a pre-generated token that lets a headless machine (the VPS)
  join the tailnet without an interactive browser login.
- **DERP** — Tailscale's relay servers, used as a fallback when two devices
  can't form a direct tunnel. Slower but keeps things working.
