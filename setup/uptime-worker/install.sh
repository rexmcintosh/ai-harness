#!/bin/sh
# Connect the VM and the Mac mini to the bebop-uptime Worker (run on the VM).
#   setup/uptime-worker/install.sh          install or update
#   setup/uptime-worker/install.sh --undo   remove the VM cron line and the mini's ping settings
# Deploying the Worker itself: see README.md (npm install && npm run deploy).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
MINI=bebop_admin@mini
BASE_URL=https://bebop-uptime.rex-651.workers.dev
secret_file="$HOME/.config/bebop-uptime/secret"
bin="$HOME/.local/bin/bebop-uptime-ping"
cron_line="*/5 * * * * $bin 2>> \$HOME/.local/state/bebop-uptime-ping.err"
ssh_mini() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$MINI" "$@"; }

# Each step is safe on its own and safe to repeat. If one fails, say what is done.
done_steps="nothing yet"
report() {
  rc=$?
  [ "$rc" -eq 0 ] && return
  echo "install.sh stopped (exit $rc). Done so far: $done_steps." >&2
  echo "Fix the cause and run it again, or run install.sh --undo; both are safe to repeat." >&2
}
trap report EXIT

# Set or clear the mini-guard ping settings. The secret arrives on stdin (empty = clear),
# so it never shows in a process list.
set_mini_ping() {
  ssh_mini "/usr/bin/python3 -c '
import json, os, sys
p = os.path.expanduser(\"~/.config/mini-guard/config.json\")
cfg = json.load(open(p))
token = sys.stdin.read().strip()
if token:
    cfg[\"healthcheck_url\"] = \"$BASE_URL/ping/mini-guard\"
    cfg[\"healthcheck_token\"] = token
else:
    cfg.pop(\"healthcheck_url\", None); cfg.pop(\"healthcheck_token\", None)
tmp = p + \".tmp\"
with open(tmp, \"w\") as f: json.dump(cfg, f, indent=1)
os.chmod(tmp, 0o600); os.replace(tmp, p)
'"
}

if [ "${1:-}" = "--undo" ]; then
  { crontab -l 2>/dev/null | grep -vF "$bin" || true; } | crontab -
  rm -f "$bin"
  done_steps="VM cron line and ping script removed"
  printf '' | set_mini_ping
  echo "bebop-uptime pings removed (the Worker itself is still deployed)"
  exit 0
fi

[ -s "$secret_file" ] || { echo "no $secret_file; deploy the Worker first (README.md)" >&2; exit 1; }
tr -d '\n' < "$secret_file" | grep -Eqx '[0-9a-f]{64}' \
  || { echo "$secret_file must be 64 lowercase hex characters (openssl rand -hex 32)" >&2; exit 1; }
mkdir -p "$HOME/.local/bin" "$HOME/.local/state"
install -m 755 "$here/vm-ping.sh" "$bin"
{ crontab -l 2>/dev/null | grep -vF "$bin" || true; echo "$cron_line"; } | crontab -
done_steps="VM cron line set"
start=$(date +%s)
"$bin"
echo "VM ping installed (every 5 min)"
done_steps="VM ping installed and sent"

tr -d '\n' < "$secret_file" | set_mini_ping
echo "mini-guard ping settings set"
done_steps="VM ping installed and sent; mini-guard ping settings set"

# Prove both reach the Worker. The mini ticks every 2 minutes; poll for up to about 4.
status() { printf 'header = "Authorization: Bearer %s"\n' "$(tr -d '\n' < "$secret_file")" \
  | curl -fsS -m 15 -K - "$BASE_URL/status"; }
tries=13
while :; do
  if status | python3 -c "
import json, sys
c = json.load(sys.stdin).get('checks', {})
sys.exit(0 if all(c.get(n, {}).get('lastSeen', 0) >= $start for n in ('bebop-vm', 'mini-guard')) else 1)"; then
    echo "both machines reach the Worker"
    exit 0
  fi
  tries=$((tries - 1))
  [ "$tries" -gt 0 ] || break
  sleep 20
done
echo "the Worker has not heard from both machines; on the mini, check ~/.local/state/mini-guard/guard.log" >&2
exit 1
