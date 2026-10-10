#!/usr/bin/env bash
# MeetTrack "expected but missing" monitor (watchdog/expect_run.py).
# Cron: every 5 minutes. Read-only against Supabase; alerts go straight to
# Telegram through bin/tg-send, never through a model.
#   ./watchdog/run-expect.sh             one run
#   ./watchdog/run-expect.sh --dry-run   print what it would send; writes nothing
set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "$DIR/.." && pwd)"
LOG_DIR="${WATCHDOG_LOG_DIR:-$DIR/logs}"
ENV_FILE="${WATCHDOG_ENV_FILE:-/home/dev/projects/splash_poller/.env}"
TG_SEND="${WATCHDOG_TG_SEND:-$BASE/bin/tg-send}"
CHAT_ID="7735693897"
mkdir -p "$LOG_DIR"

# The Supabase key is loaded inside this subshell only. The python run lock
# (logs/expect.lock) is taken inside expect_run.py; the AI narrative it may
# spawn runs detached, time-capped and without the key or the lock.
OUT="$( set -a; [ -f "$ENV_FILE" ] && . "$ENV_FILE"; set +a
        WATCHDOG_BASE="$BASE" WATCHDOG_LOG_DIR="$LOG_DIR" WATCHDOG_TG_SEND="$TG_SEND" \
        PYTHONPATH="$BASE" timeout --kill-after=10 240 python3 -m watchdog.expect_run "$@" \
        2>>"$LOG_DIR/expect.err" )"
RC=$?
printf '%s\n' "$OUT"

# A monitor that cannot run is a blind monitor: say so directly, at most once
# an hour (marker file), and never in a dry-run. rc 3 is "send failed", which
# is already logged and retried by the next run.
if [ "$RC" -ne 0 ] && [ "$RC" -ne 3 ] && [ "${1:-}" != "--dry-run" ]; then
  MARK="$LOG_DIR/expect-crash.marker"
  if [ ! -f "$MARK" ] || [ -n "$(find "$MARK" -mmin +60 2>/dev/null)" ]; then
    # The marker moves only after Telegram accepted the page, so a failed
    # send is retried by the next 5-minute run, not an hour later.
    if printf 'MeetTrack monitor could not run (rc=%s). Live meets are NOT being checked.\nSee %s\n' \
         "$RC" "$LOG_DIR/expect.err" \
       | timeout --kill-after=5 60 "$TG_SEND" "$CHAT_ID" - >/dev/null 2>>"$LOG_DIR/expect.err"; then
      touch "$MARK"
    fi
  fi
fi
exit "$RC"
