#!/usr/bin/env bash
# Memory guard and memory caps for the lima-vps VM on the Mac mini.
#
# Why: in November 2026 earlyoom stopped processes 73 times. Its old settings
# ranked small services (the nutrition tracker, the T3 server) above an 8 GB
# runaway test, and nothing capped what one session's builds and tests could
# use. Background and evidence: docs/memory-guard.md.
#
# What this sets:
#   1. earlyoom: prefer build and test helpers, avoid the T3 server and system
#      daemons. It no longer favours every "node", "claude" or "codex" process.
#   2. Always-on user services (nutrition-tracker, finance-tracker): a lower
#      kill priority and Restart=always, so a memory kill does not leave them down.
#   3. Each T3 Claude, Codex or terminal session: at most 4 GB of memory and
#      1 GB of swap.
#   4. Each login session (SSH shells, the Claude desktop-app server with all
#      its chats, a standalone Codex server): at most 6 GB of memory and 2 GB of
#      swap.
#   Past a cap the kernel's OOM killer stops a process inside that group only
#   (it picks by oom_score, which is mostly size when the group's processes
#   share one oom_score_adj), and OOMPolicy=continue keeps the rest running.
#
# Idempotent. Run on lima-vps as dev; it uses sudo for the /etc files.
#   setup/vps-to-mini/memory-guard.sh           apply, then check
#   setup/vps-to-mini/memory-guard.sh --check   check only
# Exit status 1 means a check failed; the FAIL lines say which.
set -euo pipefail

if [ "$(hostname)" != "lima-vps" ] || [ "$(id -un)" != "dev" ]; then
  echo "memory-guard: run this on lima-vps as dev (got $(hostname) as $(id -un))" >&2
  exit 1
fi

MODE=apply
case "${1:-}" in
  "") ;;
  --check) MODE=check ;;
  *) echo "usage: $0 [--check]" >&2; exit 2 ;;
esac

MARK="# Managed by ai-harness setup/vps-to-mini/memory-guard.sh"
USER_UNITS="$HOME/.config/systemd/user"
STATE_DIR="$HOME/.local/state/memory-guard"
SERVICES=(nutrition-tracker finance-tracker)
SERVICE_OOM_ADJ=-100
SESSION_CAP=6G
SESSION_SWAP_CAP=2G
AGENT_CAP=4G
AGENT_SWAP_CAP=1G

# Unanchored on purpose: a `$` breaks systemd's EnvironmentFile expansion.
EARLYOOM_PREFER='(vitest|esbuild|workerd|wrangler)'
EARLYOOM_AVOID='(systemd|sshd|dbus-daemon|tailscaled|tmux|earlyoom|lima-guestagent|dockerd|containerd|node-MainThread)'

earlyoom_conf() {
  cat <<EOF
$MARK
# earlyoom acts when free memory and free swap are both under 10% (defaults).
# It picks the process with the highest oom_score; --prefer adds 300 and
# --avoid subtracts 300. Names are matched against the process name (comm).
#   prefer: build and test helpers (vitest workers, esbuild, workerd, wrangler)
#   avoid:  system daemons, and the T3 server (node-MainThread); stopping T3
#           stops every T3 thread at once.
EARLYOOM_ARGS="$EARLYOOM_WANT_ARGS"
EOF
}

scope_cap_conf() { # $1 memory cap, $2 swap cap, $3 what it covers
  cat <<EOF
$MARK
# $3
# Past the cap the kernel's OOM killer stops a process in this group only,
# usually the largest; OOMPolicy=continue keeps the rest of the group running.
[Scope]
MemoryMax=$1
MemorySwapMax=$2
OOMPolicy=continue
EOF
}

service_conf() {
  cat <<EOF
$MARK
# A memory kill ends the process with SIGTERM, which Restart=on-failure treats
# as a clean stop. Restart=always brings the service back. The lower
# OOMScoreAdjust ranks it below agent sessions and their builds and tests
# (DefaultOOMScoreAdjust is 300 in /etc/systemd/user.conf.d/10-oom-leaves.conf).
[Service]
OOMScoreAdjust=$SERVICE_OOM_ADJ
Restart=always
EOF
}

FAILURES=0

die() { echo "memory-guard: $*" >&2; exit 1; }
fail() { echo "memory-guard: FAIL: $*" >&2; FAILURES=$((FAILURES + 1)); }
readf() { cat "$1" 2>/dev/null || echo "?"; }
bytes() { numfmt --from=iec "$1"; }

# Everything the script needs, checked before it changes anything.
for tool in systemctl python3 numfmt choom flock awk find; do
  command -v "$tool" >/dev/null || die "needs $tool"
done
systemctl --user show-environment >/dev/null 2>&1 || die "cannot reach dev's systemd user manager"

# One run at a time: a second run started meanwhile exits at once.
install -d -m 0700 "$STATE_DIR"
exec 9>"$STATE_DIR/lock"
flock -n 9 || die "another run is in progress"

# install_file PATH CONTENT sudo|user
# Sets WROTE=1 when it wrote the file, WROTE=0 when the file already had this
# content. Any failed step stops the script, so a file is never replaced
# without its backup. A replaced file is first copied to $STATE_DIR/backups/
# with a UTC time stamp.
WROTE=0
install_file() {
  local path=$1 content=$2 who=$3 dir backup tmp
  WROTE=0
  if [ -f "$path" ] && [ "$(cat "$path")" = "$content" ]; then
    return 0
  fi
  dir=$(dirname "$path")
  if [ -f "$path" ]; then
    backup="$STATE_DIR/backups/$(echo "${path#/}" | tr / _).$(date -u +%Y%m%dT%H%M%S.%NZ)"
    install -d -m 0700 "$STATE_DIR/backups" || die "cannot create $STATE_DIR/backups"
    if [ "$who" = sudo ]; then
      sudo -n cat "$path" > "$backup" || die "cannot back up $path"
    else
      cat "$path" > "$backup" || die "cannot back up $path"
    fi
    echo "backed up $path to $backup"
  fi
  # Write a temporary file in the same folder, then rename it over the old
  # one, so an interruption never leaves a half-written file. Temporary files
  # left by an earlier, killed run are removed first.
  tmp="$dir/.$(basename "$path").memory-guard.$$"
  if [ "$who" = sudo ]; then
    sudo -n install -d -m 0755 "$dir" || die "cannot create $dir"
    sudo -n find "$dir" -maxdepth 1 -name ".$(basename "$path").memory-guard.*" -delete || die "cannot clean $dir"
    printf '%s\n' "$content" | sudo -n tee "$tmp" >/dev/null || die "cannot write $tmp"
    sudo -n chmod 0644 "$tmp" || die "cannot chmod $tmp"
    sudo -n mv -f "$tmp" "$path" || die "cannot move $tmp to $path"
  else
    install -d -m 0755 "$dir" || die "cannot create $dir"
    find "$dir" -maxdepth 1 -name ".$(basename "$path").memory-guard.*" -delete || die "cannot clean $dir"
    printf '%s\n' "$content" > "$tmp" || die "cannot write $tmp"
    chmod 0644 "$tmp" || die "cannot chmod $tmp"
    mv -f "$tmp" "$path" || die "cannot move $tmp to $path"
  fi
  WROTE=1
  echo "wrote $path"
}

# scopes system|user PATTERN -> names of active scopes. "active" includes
# abandoned scopes: the Claude desktop-app server keeps running in one after
# the SSH login that started it has ended.
scopes() {
  local flag=()
  [ "$1" = user ] && flag=(--user)
  systemctl "${flag[@]}" list-units --type=scope --state=active --no-legend --plain "$2" | awk '{print $1}'
}

unit_loaded() { [ "$(systemctl --user show "$1" -p LoadState --value 2>/dev/null)" = loaded ]; }

# earlyoom_live_args -> the arguments the running earlyoom was started with
earlyoom_live_args() {
  local pid
  pid=$(systemctl show earlyoom -p MainPID --value 2>/dev/null || echo 0)
  if [ "${pid:-0}" = 0 ]; then
    echo "?"
    return 0
  fi
  tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | cut -d' ' -f2- || echo "?"
}

EARLYOOM_WANT_ARGS="-r 3600 --prefer $EARLYOOM_PREFER --avoid $EARLYOOM_AVOID"

# True only when earlyoom runs with exactly the managed arguments.
earlyoom_args_ok() {
  local live
  live=$(earlyoom_live_args)
  [ "${live% }" = "$EARLYOOM_WANT_ARGS" ]
}

# set_service_adj SVC -> lower the kill priority of every live process of SVC.
# Two passes, so a child forked during the first pass is not missed.
set_service_adj() {
  local cg pid pass
  cg=$(systemctl --user show "$1.service" -p ControlGroup --value 2>/dev/null || true)
  [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/cgroup.procs" ] || return 0
  for pass in 1 2; do
    while read -r pid; do
      [ -n "$pid" ] || continue
      [ "$(readf "/proc/$pid/oom_score_adj")" = "$SERVICE_OOM_ADJ" ] && continue
      if ! sudo -n choom -p "$pid" -n "$SERVICE_OOM_ADJ" >/dev/null 2>&1; then
        if [ -d "/proc/$pid" ] && [ "$pass" = 2 ]; then
          fail "could not set oom_score_adj of $1 pid $pid"
        fi
      fi
    done < "/sys/fs/cgroup$cg/cgroup.procs"
  done
}

# managed_files -> "PATH<TAB>sudo|user" for every file this script owns.
managed_files() {
  printf '%s\tsudo\n' /etc/default/earlyoom /etc/systemd/system/session-.scope.d/50-memory-cap.conf
  local kind svc
  for kind in claude codex terminal; do
    printf '%s\tuser\n' "$USER_UNITS/t3code-$kind-.scope.d/50-memory-cap.conf"
  done
  for svc in "${SERVICES[@]}"; do
    if unit_loaded "$svc.service"; then
      printf '%s\tuser\n' "$USER_UNITS/$svc.service.d/50-memory-guard.conf"
    fi
  done
}

# want PATH -> the content this script manages at PATH
want() {
  local kind
  case "$1" in
    /etc/default/earlyoom) earlyoom_conf ;;
    /etc/systemd/system/session-.scope.d/*)
      scope_cap_conf "$SESSION_CAP" "$SESSION_SWAP_CAP" \
        "Each login session: SSH shells, the Claude desktop-app server and its chats, a standalone Codex server." ;;
    */t3code-*-.scope.d/*)
      kind=${1##*/t3code-}
      kind=${kind%%-.scope.d/*}
      scope_cap_conf "$AGENT_CAP" "$AGENT_SWAP_CAP" "Each T3 $kind session, with its tools, builds and tests." ;;
    *.service.d/50-memory-guard.conf) service_conf ;;
    *) echo "memory-guard: no managed content for $1" >&2; return 1 ;;
  esac
}

apply() {
  sudo -n true 2>/dev/null || die "needs passwordless sudo for /etc files, earlyoom and choom"
  local svc path who earlyoom_changed=0
  # Reload first, so a service unit installed since the last reload is seen.
  systemctl --user daemon-reload
  for svc in "${SERVICES[@]}"; do
    unit_loaded "$svc.service" || echo "memory-guard: WARN: $svc.service is not installed; its protection is not set"
  done
  while IFS=$'\t' read -r path who; do
    install_file "$path" "$(want "$path")" "$who"
    if [ "$WROTE" = 1 ] && [ "$path" = /etc/default/earlyoom ]; then
      earlyoom_changed=1
    fi
  done < <(managed_files)

  # Reload both managers on every run, so a run that stopped after writing
  # files is completed by the next one. daemon-reload re-applies the drop-ins
  # to scopes that are already running (checked on 2026-11-25 for both the
  # system and the user manager). It does not restart any unit.
  sudo -n systemctl daemon-reload
  systemctl --user daemon-reload

  # Belt and braces for scopes that existed before the drop-ins: set the same
  # caps at runtime. A scope that ends meanwhile is not an error.
  local unit
  for unit in $(scopes system 'session-*.scope'); do
    if ! sudo -n systemctl set-property --runtime "$unit" \
        MemoryMax="$SESSION_CAP" MemorySwapMax="$SESSION_SWAP_CAP" 2>/dev/null; then
      if systemctl is-active -q "$unit"; then fail "could not cap $unit"; fi
    fi
  done
  for unit in $(scopes user 't3code-*.scope'); do
    if ! systemctl --user set-property --runtime "$unit" \
        MemoryMax="$AGENT_CAP" MemorySwapMax="$AGENT_SWAP_CAP" 2>/dev/null; then
      if systemctl --user is-active -q "$unit"; then fail "could not cap $unit"; fi
    fi
  done

  # The services keep running; lower the kill priority of their live processes.
  for svc in "${SERVICES[@]}"; do
    if unit_loaded "$svc.service"; then set_service_adj "$svc"; fi
  done

  # Restart earlyoom when its file changed or it runs with other arguments.
  if [ "$earlyoom_changed" = 1 ] || ! earlyoom_args_ok; then
    sudo -n systemctl restart earlyoom
  fi
}

# check_scope system|user UNIT CAP SWAP_CAP -> prints one line, counts misses
check_scope() {
  local flag=() cg mem swap policy
  [ "$1" = user ] && flag=(--user)
  cg=$(systemctl "${flag[@]}" show "$2" -p ControlGroup --value 2>/dev/null || true)
  mem="?"
  swap="?"
  if [ -n "$cg" ]; then
    mem=$(readf "/sys/fs/cgroup$cg/memory.max")
    swap=$(readf "/sys/fs/cgroup$cg/memory.swap.max")
  fi
  policy=$(systemctl "${flag[@]}" show "$2" -p OOMPolicy --value 2>/dev/null || echo "?")
  printf '  %-32s %s / %s / %s\n' "$2" "$mem" "$swap" "$policy"
  if [ "$mem" = "?" ] && ! systemctl "${flag[@]}" is-active -q "$2" 2>/dev/null; then
    return 0 # the scope ended while we looked
  fi
  if [ "$mem" != "$(bytes "$3")" ] || [ "$swap" != "$(bytes "$4")" ] || [ "$policy" != continue ]; then
    fail "$2 is not capped as expected"
  fi
}

report() {
  echo "== managed files"
  local path who
  while IFS=$'\t' read -r path who; do
    if [ -f "$path" ] && [ "$(cat "$path")" = "$(want "$path")" ]; then
      echo "  ok       $path"
    else
      echo "  differs  $path"
      fail "$path is missing or differs from the managed content"
    fi
  done < <(managed_files)

  echo "== T3's own limit for all T3 sessions together (set by T3, not by this script)"
  local slice
  for slice in $(systemctl --user list-units --type=slice --state=active --no-legend --plain 'app-t3code-agents.slice' | awk '{print $1}'); do
    echo "  $slice MemoryHigh=$(systemctl --user show "$slice" -p MemoryHigh --value) MemoryMax=$(systemctl --user show "$slice" -p MemoryMax --value)"
  done

  echo "== earlyoom"
  echo "  active=$(systemctl is-active earlyoom || true) args: $(earlyoom_live_args)"
  earlyoom_args_ok || fail "earlyoom is not running with the managed arguments"

  # --avoid matches process names, so check that the T3 server still has the
  # name it protects. Small helpers (under 50 MB) are not checked.
  echo "== T3 server processes covered by --avoid"
  local cg pid comm rss
  cg=$(systemctl --user show t3code.service -p ControlGroup --value 2>/dev/null || true)
  if [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/cgroup.procs" ]; then
    while read -r pid; do
      comm=$(readf "/proc/$pid/comm")
      rss=$(awk '/^VmRSS:/ {print int($2 / 1024)}' "/proc/$pid/status" 2>/dev/null || echo 0)
      [ "$comm" = "?" ] && continue # exited while we looked
      if [[ $comm =~ $EARLYOOM_AVOID ]]; then
        echo "  avoided  $pid $comm (${rss:-0} MB)"
      elif [ "${rss:-0}" -ge 50 ]; then
        echo "  EXPOSED  $pid $comm (${rss:-0} MB)"
        fail "T3 process $pid is named '$comm', which --avoid does not match"
      fi
    done < "/sys/fs/cgroup$cg/cgroup.procs"
  else
    echo "  t3code.service is not running"
  fi
  # The same name outside T3 would get T3's protection; flag it.
  local p
  for p in /proc/[0-9]*; do
    [ "$(readf "$p/comm")" = node-MainThread ] || continue
    case "$(readf "$p/cgroup")" in
      *"/t3code.service"*) ;;
      "?") ;; # exited while we looked
      *) fail "process ${p#/proc/} is named node-MainThread outside t3code.service; --avoid protects it" ;;
    esac
  done

  echo "== caps on running groups (memory.max / memory.swap.max / OOMPolicy)"
  local unit
  for unit in $(scopes system 'session-*.scope'); do
    check_scope system "$unit" "$SESSION_CAP" "$SESSION_SWAP_CAP"
  done
  for unit in $(scopes user 't3code-*.scope'); do
    check_scope user "$unit" "$AGENT_CAP" "$AGENT_SWAP_CAP"
  done

  echo "== services (Restart / OOMScoreAdjust / live processes)"
  local svc restart adj live n load
  for svc in "${SERVICES[@]}"; do
    load=$(systemctl --user show "$svc.service" -p LoadState --value 2>/dev/null || echo "?")
    if [ "$load" = not-found ]; then
      echo "  WARN $svc is not installed; its protection is not set"
      continue
    elif [ "$load" != loaded ]; then
      fail "$svc.service has LoadState=$load, so its protection cannot be checked"
      continue
    fi
    restart=$(systemctl --user show "$svc.service" -p Restart --value 2>/dev/null || echo "?")
    adj=$(systemctl --user show "$svc.service" -p OOMScoreAdjust --value 2>/dev/null || echo "?")
    cg=$(systemctl --user show "$svc.service" -p ControlGroup --value 2>/dev/null || true)
    n=0
    if [ -n "$cg" ] && [ -r "/sys/fs/cgroup$cg/cgroup.procs" ]; then
      while read -r pid; do
        live=$(readf "/proc/$pid/oom_score_adj")
        [ "$live" = "?" ] && continue # exited while we looked
        n=$((n + 1))
        if [ "$live" != "$SERVICE_OOM_ADJ" ]; then
          fail "$svc pid $pid runs with oom_score_adj $live"
        fi
      done < "/sys/fs/cgroup$cg/cgroup.procs"
    fi
    printf '  %-20s %s / %s / %s live process(es) checked\n' "$svc" "$restart" "$adj" "$n"
    if [ "$restart" != always ] || [ "$adj" != "$SERVICE_OOM_ADJ" ]; then
      fail "$svc does not have the managed Restart and OOMScoreAdjust"
    fi
  done

  echo "== earlyoom's first picks right now (score = oom_score, +300 prefer, -300 avoid)"
  python3 -I - "$EARLYOOM_PREFER" "$EARLYOOM_AVOID" <<'PY'
import os, re, sys
prefer, avoid = re.compile(sys.argv[1]), re.compile(sys.argv[2])
rows = []
for p in os.listdir("/proc"):
    if not p.isdigit():
        continue
    try:
        comm = open(f"/proc/{p}/comm").read().strip()
        score = int(open(f"/proc/{p}/oom_score").read())
        rss = 0
        for line in open(f"/proc/{p}/status"):
            if line.startswith("VmRSS:"):
                rss = int(line.split()[1]) // 1024
    except (OSError, ValueError):
        continue
    if rss == 0:
        continue
    if prefer.search(comm):
        score += 300
    if avoid.search(comm):
        score -= 300
    rows.append((score, rss, p, comm))
rows.sort(reverse=True)
for score, rss, p, comm in rows[:8]:
    print(f"  {score:5d}  {rss:6d} MB  {p:>8}  {comm}")
PY
}

if [ "$MODE" = apply ]; then
  apply
fi
report
if [ "$FAILURES" -gt 0 ]; then
  echo "memory-guard: $FAILURES check(s) failed" >&2
  exit 1
fi
echo "memory-guard: all checks passed"
