#!/usr/bin/env bash
# Phase D on the VM, run as dev after sync-home.sh: firewall, then rebuild everything that is CPU-specific.
# Idempotent. Starts no jobs and no services.
set -euo pipefail
HERE=/home/dev/projects/ai-harness

# --- firewall: same shape as the VPS (tailnet-only inbound) plus Lima's own SSH from the mac host side
sudo ufw --force reset >/dev/null
sudo ufw default deny incoming >/dev/null
sudo ufw default allow outgoing >/dev/null
sudo ufw allow in on tailscale0 comment 'mesh: all inbound over tailnet' >/dev/null
sudo ufw allow 41641/udp comment 'tailscale direct path (NAT traversal)' >/dev/null
sudo ufw allow from 192.168.5.0/24 to any port 22 proto tcp comment 'lima host agent ssh' >/dev/null
sudo ufw --force enable >/dev/null
sudo ufw status | sed -n '1,12p'

# --- user site-packages for /usr/bin/python3 (the copied folder holds x86_64 wheels)
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q python3-pip >/dev/null
REQ="$1"
rm -rf "$HOME/.local/lib/python3.12/site-packages"
python3 -m pip install --user --break-system-packages --quiet -r "$REQ"
echo "user-site: $(python3 -m pip list --user --format=freeze 2>/dev/null | wc -l) packages"

# --- council + friends (backlog-run, diem, session-gc, jev, notion-work-queue, portfolio-cockpit, venice-usage)
export PATH="$HOME/.local/bin:$PATH"
pipx install --force "$HERE" >/dev/null
pipx list --short
ls -la "$HOME/.local/bin" | grep -c pipx/venvs || true
echo "REBUILD-BASE-OK"
