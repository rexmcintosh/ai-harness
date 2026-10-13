#!/bin/sh
# Install only; enable after the first successful remote backup and restore check.
set -eu
backup_source=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
command -v restic >/dev/null
install -d -m 700 "$HOME/.local/bin" "$HOME/.config/systemd/user"
install -m 700 "$backup_source/backup.py" "$HOME/.local/bin/restic-offsite"
install -m 600 "$backup_source/restic-offsite.service" "$HOME/.config/systemd/user/restic-offsite.service"
install -m 600 "$backup_source/restic-offsite.timer" "$HOME/.config/systemd/user/restic-offsite.timer"
systemctl --user daemon-reload
printf '%s\n' 'Installed. Timer remains disabled until B2 setup and recovery checks pass.'
