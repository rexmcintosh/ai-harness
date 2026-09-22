#!/usr/bin/env bash
# Repeatable copy of /home/dev from the VPS into the Lima VM on the mini. Safe to re-run; never deletes on the target.
# Things that are CPU-specific (x86_64 -> arm64) or must not auto-start on the VM are left out on purpose.
set -euo pipefail
MINI=bebop_admin@100.65.130.64
KH=${KH:-$HOME/.ssh/known_hosts_bebop_vm}
PORT=$(ssh -o BatchMode=yes -i ~/.ssh/id_ed25519 "$MINI" "/opt/homebrew/bin/limactl list --format '{{.SSHLocalPort}}' vps")

rsync -aHx --partial --info=stats2 \
  -e "ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$KH -i $HOME/.ssh/id_ed25519 -J $MINI -p $PORT" \
  --exclude='/mnt/' --exclude='/snap/' \
  --exclude='/.cache/' --exclude='/.npm/' --exclude='/.nvm/' --exclude='/.bun/' --exclude='/.vscode-server/' --exclude='/.dotnet/' \
  --exclude='/.zcompdump*' \
  --exclude='/.local/share/claude/' --exclude='/.local/share/uv/' --exclude='/.local/share/pipx/' --exclude='/.local/share/Trash/' \
  --exclude='/.local/share/portfolio-cockpit/' --exclude='/.local/share/attain-work-queue/' \
  --exclude='/.local/bin/claude' --exclude='/.local/bin/python3.11' --exclude='/.local/bin/uv' --exclude='/.local/bin/uvx' \
  --exclude='/.config/systemd/' \
  --exclude='/.ssh/authorized_keys' \
  --exclude='/projects/santa-amaro-home-renovation/references-local/blender-home/.python/' \
  --exclude='/.claude/remote/' --exclude='/.local/bin/zoxide' --exclude='/.local/lib/python3.12/site-packages/' \
  --exclude='node_modules/' --exclude='.venv/' --exclude='venv/' --exclude='__pycache__/' --exclude='.next/' --exclude='.astro/' \
  /home/dev/ dev@127.0.0.1:/home/dev/

ssh -o BatchMode=yes -o UserKnownHostsFile="$KH" -i ~/.ssh/id_ed25519 -J "$MINI" -p "$PORT" dev@127.0.0.1 'mkdir -p ~/migration-holding/systemd'
# systemd user units go to a holding folder: with linger on, units placed in ~/.config/systemd would start on the VM
rsync -aH -e "ssh -o BatchMode=yes -o UserKnownHostsFile=$KH -i $HOME/.ssh/id_ed25519 -J $MINI -p $PORT" \
  /home/dev/.config/systemd/ dev@127.0.0.1:/home/dev/migration-holding/systemd/
crontab -l | ssh -o BatchMode=yes -o UserKnownHostsFile="$KH" -i ~/.ssh/id_ed25519 -J "$MINI" -p "$PORT" dev@127.0.0.1 'cat > ~/migration-holding/crontab.vps.txt'
echo "SYNC-OK $(date -u +%FT%TZ)"
