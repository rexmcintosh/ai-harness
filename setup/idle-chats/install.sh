#!/bin/sh
# Install idle-chats: copy it to ~/.local/bin and add its nightly cron line.
#   setup/idle-chats/install.sh          install or update (safe to run again)
#   setup/idle-chats/install.sh --undo   remove the cron line and the copy
# The old crontab is saved to ~/.local/state/idle-chats/ before each change.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
target="$HOME/.local/bin/idle-chats"
state="$HOME/.local/state/idle-chats"
marker="# idle-chats nightly"
line="0 4 * * *  /usr/bin/python3 -I $target --apply >> $state/cron.log 2>&1  $marker"

umask 077
mkdir -p "$state" "$HOME/.local/bin"
chmod 700 "$state"

old="$state/crontab.$(date -u +%Y%m%dT%H%M%SZ)"
crontab -l > "$old" 2>/dev/null || : > "$old"
new="$state/crontab.new"
grep -vF "$marker" "$old" > "$new" || true

if [ "${1:-}" = "--undo" ]; then
    crontab "$new"
    rm -f "$target" "$new"
    if crontab -l 2>/dev/null | grep -qF "$marker"; then
        echo "idle-chats: the cron line is still there" >&2
        exit 1
    fi
    echo "idle-chats removed (old crontab saved in $old)"
    exit 0
fi

install -m 755 "$here/idle_chats.py" "$target"
printf '%s\n' "$line" >> "$new"
crontab "$new"
rm -f "$new"

crontab -l | grep -qxF "$line" || { echo "idle-chats: cron line not found after install" >&2; exit 1; }
cmp -s "$here/idle_chats.py" "$target" || { echo "idle-chats: copy differs" >&2; exit 1; }
echo "idle-chats installed: $target, nightly at 04:00 (server time, Europe/Lisbon)"
echo "old crontab saved in $old"
