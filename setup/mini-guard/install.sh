#!/bin/sh
# Install or update mini-guard on the Mac mini, from the VM.
#   setup/mini-guard/install.sh            install / update (keeps the mini's config and state)
#   setup/mini-guard/install.sh --undo     stop and remove the launchd job and the script
# Also sets up a read-only SSH key so the VM's watchdog can read the guard's heartbeat.
set -eu
MINI=bebop_admin@mini
here=$(cd "$(dirname "$0")" && pwd)
key="$HOME/.ssh/mini_guard_read"
ssh_mini() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$MINI" "$@"; }

if [ "${1:-}" = "--undo" ]; then
  ssh_mini 'launchctl bootout gui/$(id -u)/io.bebop.mini-guard 2>/dev/null || true
            rm -f ~/Library/LaunchAgents/io.bebop.mini-guard.plist ~/.local/bin/mini-guard
            if [ -f ~/.ssh/authorized_keys ]; then
              tmp=$(mktemp ~/.ssh/authorized_keys.XXXXXX)
              grep -v " mini-guard-heartbeat\$" ~/.ssh/authorized_keys > "$tmp" || true
              chmod 600 "$tmp" && mv "$tmp" ~/.ssh/authorized_keys
            fi
            echo "mini-guard removed (config and state kept in ~/.config/mini-guard and ~/.local/state/mini-guard)"'
  exit 0
fi

python3 -m py_compile "$here/mini_guard.py"
ssh_mini 'mkdir -p ~/.local/bin ~/Library/LaunchAgents && mkdir -m 700 -p ~/.config/mini-guard ~/.local/state/mini-guard'
scp -q -o BatchMode=yes "$here/mini_guard.py" "$MINI:.local/bin/mini-guard.new"
scp -q -o BatchMode=yes "$here/io.bebop.mini-guard.plist" "$MINI:Library/LaunchAgents/io.bebop.mini-guard.plist.new"
ssh_mini 'set -e
  /usr/bin/python3 -m py_compile ~/.local/bin/mini-guard.new
  plutil -lint ~/Library/LaunchAgents/io.bebop.mini-guard.plist.new >/dev/null
  chmod 755 ~/.local/bin/mini-guard.new && mv ~/.local/bin/mini-guard.new ~/.local/bin/mini-guard
  mv ~/Library/LaunchAgents/io.bebop.mini-guard.plist.new ~/Library/LaunchAgents/io.bebop.mini-guard.plist
  if [ ! -f ~/.config/mini-guard/config.json ]; then
    umask 077
    printf "{\n \"mode\": \"observe\",\n \"chat_id\": \"7735693897\",\n \"telegram_token_file\": \"%s/.config/mini-guard/telegram-token\"\n}\n" "$HOME" > ~/.config/mini-guard/config.json
  fi
  chmod 600 ~/.config/mini-guard/config.json
  launchctl bootout gui/$(id -u)/io.bebop.mini-guard 2>/dev/null || true
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/io.bebop.mini-guard.plist
  launchctl print gui/$(id -u)/io.bebop.mini-guard >/dev/null
  echo "launchd job loaded"'

# Read-only heartbeat key for the VM's watchdog.
if [ ! -f "$key" ]; then
  ssh-keygen -q -t ed25519 -N "" -C mini-guard-heartbeat -f "$key"
fi
pub=$(cat "$key.pub")   # ends with the comment "mini-guard-heartbeat"
line="command=\"cat /Users/bebop_admin/.local/state/mini-guard/last-tick.json\",no-port-forwarding,no-pty,no-agent-forwarding,no-X11-forwarding $pub"
# Replace exactly the managed line (matched by its comment at the end), keep every other key.
printf '%s\n' "$line" | ssh_mini 'set -e; umask 077; mkdir -p ~/.ssh; chmod 700 ~/.ssh; touch ~/.ssh/authorized_keys
  tmp=$(mktemp ~/.ssh/authorized_keys.XXXXXX)
  { grep -v " mini-guard-heartbeat\$" ~/.ssh/authorized_keys || true; cat; } > "$tmp"
  grep -q " mini-guard-heartbeat\$" "$tmp"
  chmod 600 "$tmp"; mv "$tmp" ~/.ssh/authorized_keys'
# Prove the VM can read the heartbeat with the read-only key (the first tick may still be running).
for try in 1 2 3 4 5; do
  if beat=$(ssh -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=10 -i "$key" "$MINI" 2>/dev/null) \
     && printf '%s' "$beat" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert "time" in d'; then
    echo "mini-guard installed; the VM reads its heartbeat."
    exit 0
  fi
  sleep 5
done
echo "mini-guard installed, but the VM could NOT read its heartbeat with $key" >&2
exit 1
