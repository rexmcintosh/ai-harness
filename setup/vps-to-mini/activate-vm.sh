#!/usr/bin/env bash
# Phase E step 5 on the VM, run as dev AFTER the VPS crontab is removed and its services are stopped,
# and AFTER the final sync-home.sh delta. Installs the units, the crontab and the cockpit tailscale serve.
set -euo pipefail
H=~/migration-holding
CRON=$(ls -1 "$H"/crontab.vps*.txt | sort | tail -1)

# 1. systemd user units (the retired telegram-orphan-reaper and session-bridge stay out)
mkdir -p ~/.config/systemd/user
# session-bridge (Wall-E) was retired 2026-10-07. Converge hosts that still have it: stop, disable
# and drop the unit file. Every step tolerates a unit that is already gone.
systemctl --user disable --now session-bridge.service >/dev/null 2>&1 || true
rm -f ~/.config/systemd/user/session-bridge.service
for u in portfolio-cockpit.service attain-work-queue.service attain-work-queue.timer home-dev-mnt-mini.mount home-dev-mnt-mini.automount; do
  cp "$H/systemd/user/$u" ~/.config/systemd/user/
done
systemctl --user daemon-reload
systemctl --user enable --now portfolio-cockpit.service attain-work-queue.timer
# the mini sshfs mount unit is only useful if the mini's disk is wanted from inside the VM; enable the automount lazily
systemctl --user enable home-dev-mnt-mini.automount >/dev/null 2>&1 || true

# 2. crontab (same lines as the VPS)
crontab "$CRON"

# 3. cockpit behind tailscale serve, as on the VPS
sudo tailscale serve --bg --https=443 http://127.0.0.1:8790 >/dev/null

sleep 3
echo "--- units"; systemctl --user --no-pager --no-legend list-units portfolio-cockpit.service attain-work-queue.timer
# Retired-unit check: read the exact states; anything other than gone/off (including a query error) fails.
sb_active=$(systemctl --user is-active session-bridge.service 2>&1 || true)
sb_enabled=$(systemctl --user is-enabled session-bridge.service 2>&1 || true)
case "$sb_active" in inactive|failed|unknown) ;; *) echo "FAIL: session-bridge.service is-active=$sb_active" >&2; exit 1 ;; esac
case "$sb_enabled" in disabled|not-found|"Failed to get unit file state for session-bridge.service: No such file or directory") ;; *) echo "FAIL: session-bridge.service is-enabled=$sb_enabled" >&2; exit 1 ;; esac
echo "--- session-bridge: retired (is-active=$sb_active, is-enabled=$sb_enabled)"
echo "--- cron lines: $(crontab -l | grep -cvE '^\s*(#|$)')"
echo "--- serve"; tailscale serve status | head -3
echo "--- cockpit"; curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8790/
echo "ACTIVATE-OK $(date -u +%FT%TZ)"
