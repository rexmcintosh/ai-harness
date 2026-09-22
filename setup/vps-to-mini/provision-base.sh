#!/usr/bin/env bash
# Base provisioning for the Lima VM that replaces the Hetzner VPS. Run as root inside the VM. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

timedatectl set-timezone Etc/UTC

# user `dev`, uid/gid 1000, zsh, passwordless sudo: same as the VPS so /home/dev paths and cron lines move unchanged
if ! id dev >/dev/null 2>&1; then
  if getent passwd 1000 >/dev/null; then echo "uid 1000 is taken by $(getent passwd 1000 | cut -d: -f1)" >&2; exit 1; fi
  # Lima gives its own login user gid 1000; move that group so `dev` can match the VPS (1000:1000)
  lima_grp=$(getent group 1000 | cut -d: -f1 || true)
  if [ -n "$lima_grp" ] && [ "$lima_grp" != dev ]; then
    groupmod -g 1001 "$lima_grp"
    lima_home=$(getent passwd "$lima_grp" | cut -d: -f6)
    [ -d "$lima_home" ] && chgrp -R "$lima_grp" "$lima_home"
  fi
  groupadd -g 1000 dev
  useradd -m -u 1000 -g 1000 -G sudo,users -s /bin/bash dev
fi
echo 'dev ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/90-dev
chmod 440 /etc/sudoers.d/90-dev

apt-get update -q
apt-get install -y -q \
  zsh tmux git curl wget jq unzip rsync sshfs cron at build-essential pipx python3 python3-dev python3-venv python3-numpy \
  ripgrep fzf bat eza htop ncdu lsof strace mosh screen time earlyoom fail2ban ufw unattended-upgrades \
  gnupg pandoc poppler-utils ffmpeg tesseract-ocr tesseract-ocr-eng tesseract-ocr-por xvfb \
  fonts-liberation fonts-noto-color-emoji bubblewrap net-tools netcat-openbsd bind9-dnsutils

chsh -s /usr/bin/zsh dev

# GitHub CLI from the official repo (Ubuntu's own package is old)
if ! command -v gh >/dev/null; then
  install -d -m 0755 /etc/apt/keyrings
  curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
  chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" > /etc/apt/sources.list.d/github-cli.list
  apt-get update -q && apt-get install -y -q gh
fi

# Tailscale (not yet signed in: `tailscale up` needs the owner)
command -v tailscale >/dev/null || curl -fsSL https://tailscale.com/install.sh | sh

# user services keep running with nobody logged in, as on the VPS
loginctl enable-linger dev

echo "BASE-OK $(lsb_release -ds) $(uname -m) $(id dev)"
