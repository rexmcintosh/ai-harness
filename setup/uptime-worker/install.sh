#!/bin/sh
# Connect the VM and the Mac mini to the bebop-uptime Worker (run on the VM).
#   setup/uptime-worker/install.sh          install or update
#   setup/uptime-worker/install.sh --undo   remove the VM cron line and the mini's ping URL
# Deploying the Worker itself: see README.md (npm install && npm run deploy).
set -eu
here=$(cd "$(dirname "$0")" && pwd)
MINI=bebop_admin@mini
secret_file="$HOME/.config/bebop-uptime/secret"
bin="$HOME/.local/bin/bebop-uptime-ping"
cron_line="*/5 * * * * $bin 2>> \$HOME/.local/state/bebop-uptime-ping.err"
ssh_mini() { ssh -o BatchMode=yes -o ConnectTimeout=10 "$MINI" "$@"; }

set_mini_url() {   # $1 = URL or empty; read from stdin so it never shows in process lists
  ssh_mini '/usr/bin/python3 -c "
import json, os, sys
p = os.path.expanduser(\"~/.config/mini-guard/config.json\")
cfg = json.load(open(p))
url = sys.stdin.read().strip()
if url: cfg[\"healthcheck_url\"] = url
else: cfg.pop(\"healthcheck_url\", None)
tmp = p + \".tmp\"
with open(tmp, \"w\") as f: json.dump(cfg, f, indent=1)
os.chmod(tmp, 0o600); os.replace(tmp, p)
"'
}

if [ "${1:-}" = "--undo" ]; then
  crontab -l 2>/dev/null | grep -vF "$bin" | crontab -
  rm -f "$bin"
  printf '' | set_mini_url
  echo "bebop-uptime pings removed (the Worker itself is still deployed)"
  exit 0
fi

[ -s "$secret_file" ] || { echo "no $secret_file; deploy the Worker first (README.md)" >&2; exit 1; }
mkdir -p "$HOME/.local/bin" "$HOME/.local/state"
install -m 755 "$here/vm-ping.sh" "$bin"
{ crontab -l 2>/dev/null | grep -vF "$bin" || true; echo "$cron_line"; } | crontab -
"$bin"
echo "VM ping installed (every 5 min)"

tr -d '\n' < "$secret_file" | sed 's#^#https://bebop-uptime.rex-651.workers.dev/ping/mini-guard/#' | set_mini_url
echo "mini-guard ping URL set"

# Prove both reach the Worker: the mini ticks every 2 minutes.
status() { printf 'url = "https://bebop-uptime.rex-651.workers.dev/status/%s"\n' "$(tr -d '\n' < "$secret_file")" \
  | curl -fsS -m 15 -K -; }
start=$(date +%s)
for try in 1 2 3 4 5 6 7 8; do
  if status | python3 -c "
import json, sys
d = json.load(sys.stdin); c = d.get('checks', {})
ok = all(c.get(n, {}).get('lastSeen', 0) >= $start - 300 for n in ('bebop-vm',)) and c.get('mini-guard', {}).get('lastSeen', 0) >= $start
sys.exit(0 if ok else 1)"; then
    echo "both machines reach the Worker"
    exit 0
  fi
  sleep 20
done
echo "the Worker has not heard from both machines yet; check: mini-guard status on the mini" >&2
exit 1
