# Mesh-only lockdown — close the VPS to everything but the tailnet

> **Historical deployment:** The Hetzner VPS was deleted on 2026-10-02 without a retained snapshot.
> Work now runs on `bebop-vm`, inside the Mac mini. Use the
> [current runtime record](vps-decommission-2026-10-02.md) for addresses, recovery and backup limits.
> The old VPS setup, public-IP fallback and mini-independent uptime claims below are superseded.


> **Status: DONE on the live VPS — 2026-06-05.** This runbook records the change,
> why it's safe, how to reproduce it on a rebuild, and how to roll it back.
>
> **Where this fits:** This is the "Optional hardening (later)" step flagged at the
> end of [TAILSCALE.md](TAILSCALE.md#how-this-relates-to-the-vps-firewall). The mesh
> ([ARCHITECTURE.md](ARCHITECTURE.md)) is solid, so we removed the VPS's public
> inbound surface. The public IP stays — it's still the box's **outbound** pipe
> (apt, the Venice API, Tailscale coordination) — but nothing on the internet can
> *initiate* a connection to it anymore. The only way in is the tailnet.

---

## The one-sentence version

We did **not** delete the public IP. We told the firewall to **deny all public
inbound** and **allow everything over the `tailscale0` interface**, so the box is
reachable only from your devices on the tailnet — `ssh dev@vps` still works
everywhere, a stranger on the public IP gets nothing.

---

## Why this is the right shape (and removing the IP is not)

A Hetzner Cloud server with **no** public IP and no NAT gateway loses *outbound*
internet too — no `apt`, no Docker pulls, **no Venice API calls** for the council.
You'd have to stand up a second server as a NAT gateway just to get updates back.
That's cost and complexity for no security benefit over simply **firewalling the
inbound side**. Tailscale is outbound-only and does its own NAT traversal, so you
can slam the inbound door shut and lose nothing. See the first half of this
conversation's reasoning, preserved in [TAILSCALE.md](TAILSCALE.md).

---

## Before → after (live box: `mesh-vps`, Hetzner Cloud, public IP `188.245.85.237`)

| Public port (pre) | What was on it | After lockdown |
|---|---|---|
| `22/tcp` | OpenSSH | **Closed publicly.** Reach it as `ssh dev@vps` over the mesh (Tailscale SSH, `RunSSH: true`) or system sshd on the tailnet IP `100.64.43.80:22`. |
| `80/tcp` | **Caddy** — stock *"Caddy works!"* placeholder only (no real service) | **Retired.** Caddy stopped + disabled; `:80` freed. |
| `443/tcp` | *nothing listening* | **Closed.** Nothing lost. |
| `60000–61000/udp` | Mosh (per-session) | **Closed publicly.** Mosh to `vps` over the tailnet still works (point Termius at the `vps` MagicDNS name). |
| `41641/udp` | `tailscaled` | **Kept open** — Tailscale's own WireGuard port; encrypted tunnel, no service behind it. Keeps your Macs on the fast *direct* path instead of relayed (DERP). |
| `4321/tcp` | Astro dev server | Was already UFW-blocked from the public; still reachable over the mesh at `vps:4321`. |

**End-state UFW policy:** `default deny incoming`, `default allow outgoing`, plus
exactly two allow rules — `allow in on tailscale0` and `allow 41641/udp` (v4+v6).

---

## What we ran (the safe sequence)

The ordering matters: **open the mesh side first, verify, *then* close the public
side.** Additive rules can't lock you out; deletions can — so they go last, after
the mesh path is proven.

```bash
# 1. ADDITIVE — open the mesh side (cannot lock you out)
sudo ufw allow in on tailscale0 comment 'mesh: all inbound over tailnet'
sudo ufw allow 41641/udp        comment 'tailscale direct path (NAT traversal)'

# 2. VERIFY the mesh path BEFORE closing anything
tailscale debug prefs | grep RunSSH        # expect: "RunSSH": true
tailscale ping -c1 rexmb                   # expect: pong (direct ideal)
# from a *peer* (your Mac), the authoritative check:
#   ssh dev@vps        ← must land you on the box

# 3. Retire Caddy (it served only the stock placeholder)
sudo systemctl disable --now caddy

# 4. CLOSE the public doors (v4+v6 removed together per spec)
sudo ufw delete allow 22/tcp
sudo ufw delete allow 80/tcp
sudo ufw delete allow 443/tcp
sudo ufw delete allow 60000:61000/udp

# 5. CONFIRM
sudo ufw status verbose                    # only tailscale0 + 41641 remain
```

`setup/harden-mesh-only.sh` performs steps 1–5 idempotently with a safety guard
(it refuses to run unless Tailscale is up with a reachable peer). Use it on a
rebuild **after** the box has rejoined the tailnet.

---

## Your safety net (why this can't brick you)

- **UFW is stateful.** Removing a rule does **not** drop *established* connections —
  only new ones are refused. The session you run this from survives.
- **Hetzner Cloud Console (VNC)** is out-of-band — it works even if UFW denied
  literally everything. If you ever lock yourself out, open the Hetzner console,
  log in, and re-open a port. You cannot permanently brick remote access.
- **Two independent mesh paths in:** Tailscale SSH (keyless, `RunSSH: true`) *and*
  system sshd reachable on the tailnet IP via `allow in on tailscale0`.

### Rollback (re-open public SSH in a hurry)

From the Hetzner VNC console (or any working session):
```bash
sudo ufw allow 22/tcp        # public SSH back
# re-add others only if you actually need them:
#   sudo ufw allow 80/tcp 443/tcp
#   sudo ufw allow 60000:61000/udp   # mosh
#   sudo systemctl enable --now caddy
```

---

## Tradeoff to remember

A machine **not** on your tailnet (a borrowed laptop, a colleague's box) can no
longer SSH in. To use a new device, add it to the tailnet first
([TAILSCALE.md](TAILSCALE.md) Step 2). This is the intended behaviour — it's the
whole point — just don't be surprised on a trip with a borrowed computer.

---

## Future: a *real* private web service (the `tailscale serve` recipe)

We retired Caddy because it served nothing. When you genuinely need a web
endpoint reachable **only** from the tailnet (e.g. a real upload service, a
dashboard), don't reopen public 80/443 — use **`tailscale serve`**, which gives
you HTTPS on your tailnet name with an auto-managed cert and **no public port**:

```bash
# one-time, in the Tailscale admin console:
#   DNS → "Enable HTTPS…"  (turns on tailnet TLS certs; required for serve --https)

# then, to expose a local app (say it listens on 127.0.0.1:8080) at
# https://vps.taila64e8f.ts.net :
tailscale serve --bg --https=443 http://127.0.0.1:8080

tailscale serve status        # show what's published
tailscale serve reset         # tear it all down
```

This is strictly better than public Caddy + Let's Encrypt for anything you only
reach yourself: no public 80/443, no HTTP-01 challenge, mesh-only by construction.
(`tailscale funnel` would expose it to the public internet — **don't**, that's the
opposite of what we want here.)

---

## Optional: belt-and-suspenders at the Hetzner edge

UFW (on the host) fully achieves the goal. For defence in depth you can *also* add
a **Hetzner Cloud Firewall** that mirrors this — inbound: allow only `41641/udp`,
deny the rest — so hostile packets are dropped at Hetzner's network edge before
they ever reach the VM. Done in the Hetzner Cloud console (Firewalls) or via the
API/`hcloud` CLI; it is **not** configured by `setup/bootstrap-vps.sh`. Nice to
have, not required.

> **Further host hardening (optional):** `sshd` and the Astro dev server currently
> bind `0.0.0.0` and are merely *firewalled* off the public side. To remove even
> the bind, set `ListenAddress 100.64.43.80` (the tailnet IP) in
> `/etc/ssh/sshd_config.d/` and bind dev servers to the tailnet IP. UFW already
> makes this belt-and-suspenders, not load-bearing.

---

## Verification checklist (post-change)

- [x] `sudo ufw status verbose` → `deny incoming`, only `tailscale0` + `41641/udp` allowed.
- [x] `tailscale debug prefs | grep RunSSH` → `true`.
- [x] `100.64.43.80:22` accepts connections (mesh SSH reachable).
- [x] `tailscale ping rexmb` / `mini` → `pong` (direct).
- [x] Nothing listening on `:80`; Caddy `inactive` + `disabled`.
- [x] `tailscaled` and `ufw` both `enabled` (survive reboot).
- [ ] **You** confirm `ssh dev@vps` from your MacBook/iPhone (the authoritative
      external check — do this once now while the VNC fallback is handy).
