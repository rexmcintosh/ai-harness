#!/usr/bin/env bash
# Phase E step 5 on the VM, run as dev AFTER the VPS crontab is removed and its services are stopped,
# and AFTER the final sync-home.sh delta. Installs the units, the crontab and the cockpit tailscale serve.
set -euo pipefail
H=~/migration-holding
CRON=$(ls -1 "$H"/crontab.vps*.txt | sort | tail -1)

# 1. systemd user units (the retired telegram-orphan-reaper stays out)
mkdir -p ~/.config/systemd/user
for u in session-bridge.service portfolio-cockpit.service attain-work-queue.service attain-work-queue.timer home-dev-mnt-mini.mount home-dev-mnt-mini.automount; do
  cp "$H/systemd/user/$u" ~/.config/systemd/user/
done
systemctl --user daemon-reload
systemctl --user enable --now session-bridge.service portfolio-cockpit.service attain-work-queue.timer
# the mini sshfs mount unit is only useful if the mini's disk is wanted from inside the VM; enable the automount lazily
systemctl --user enable home-dev-mnt-mini.automount >/dev/null 2>&1 || true

# 2. crontab (same lines as the VPS)
crontab "$CRON"

# 3. cockpit behind tailscale serve, as on the VPS
sudo tailscale serve --bg --https=443 http://127.0.0.1:8790 >/dev/null

sleep 3
echo "--- units"; systemctl --user --no-pager --no-legend list-units session-bridge.service portfolio-cockpit.service attain-work-queue.timer
echo "--- cron lines: $(crontab -l | grep -cvE '^\s*(#|$)')"
echo "--- serve"; tailscale serve status | head -3
echo "--- cockpit"; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/
echo "ACTIVATE-OK $(date -u +%FT%TZ)"
