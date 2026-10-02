#!/usr/bin/env bash
# Report-only watchdog. Provider acceptance starts suppression; detection alone does not.
set -uo pipefail

export PATH="$HOME/.local/bin:$PATH"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "$DIR/.." && pwd)"
PROMPT_FILE="$DIR/prompts/investigate.md"
LOG_DIR="${WATCHDOG_LOG_DIR:-$DIR/logs}"
LOG="$LOG_DIR/runs.log"
CHAT_ID="7735693897"
MODEL="haiku"
CLAUDE_BIN="${WATCHDOG_CLAUDE_BIN:-$(command -v claude || echo /usr/bin/claude)}"
TG_SEND="${WATCHDOG_TG_SEND:-$BASE/bin/tg-send}"
ENV_FILE="${WATCHDOG_ENV_FILE:-/home/dev/projects/splash_poller/.env}"
DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

mkdir -p "$LOG_DIR"
# One existing watchdog pass owns detection, attempt and receipt persistence together.
exec 9>"$LOG_DIR/run.lock"
flock -n 9 || exit 0
PRECHECK_ARGS=()
[ "$DRY_RUN" = 1 ] && PRECHECK_ARGS=(--dry-run)
TS="$(date -Iseconds)"
NOW_HUMAN="$(TZ=Europe/Lisbon date '+%A %Y-%m-%d %H:%M %Z')"

if [ -n "${WATCHDOG_PRECHECK_CMD:-}" ]; then
  OUT="$($WATCHDOG_PRECHECK_CMD "${PRECHECK_ARGS[@]}" 2>>"$LOG.err")"
else
  OUT="$( set -a; [ -f "$ENV_FILE" ] && . "$ENV_FILE"; set +a
          WATCHDOG_BASE="$BASE" PYTHONPATH="$BASE" python3 -m watchdog.run "${PRECHECK_ARGS[@]}" 2>>"$LOG.err" )"
fi
JSON_LINE="$(printf '%s\n' "$OUT" | grep '^WATCHDOG_JSON:' | head -1 | sed 's/^WATCHDOG_JSON://')"
ATTEMPT_ID="$(printf '%s' "$JSON_LINE" | python3 -c "import json,sys; print(json.load(sys.stdin).get('attempt_id') or '')" 2>/dev/null)"
METRICS_LINE="$(printf '%s\n' "$OUT" | grep '^WATCHDOG_METRICS:' | head -1 | sed 's/^WATCHDOG_METRICS://')"
REPORT="$(printf '%s\n' "$OUT" | grep -vE '^WATCHDOG_(JSON|METRICS):')"
[ -n "$METRICS_LINE" ] && echo "[$TS] metrics:$METRICS_LINE" >> "$LOG"

if [ -z "$JSON_LINE" ]; then
  REPORT="[CRIT] watchdog: pre-check failed to run (no result emitted). See $LOG.err."
  ESCALATE=1
else
  ESCALATE="$(printf '%s' "$JSON_LINE" | python3 -c "import json,sys
try: print('1' if json.load(sys.stdin).get('escalate') else '0')
except Exception: print('0')" 2>/dev/null)"
fi

if [ "$ESCALATE" != 1 ]; then
  echo "[$TS] rc=0 escalate=0 :: $(printf '%s' "$REPORT" | head -1)" >> "$LOG"
  echo "ok (no escalation): $(printf '%s' "$REPORT" | head -1)"
  exit 0
fi

ALL_FILES="$(find "$BASE/bebop/logs" "$BASE/loom/logs" "$BASE/watchdog/logs" \
                  /home/dev/projects/splash_poller/logs -maxdepth 1 -type f \
                  -printf '%T@ %TY-%Tm-%Td %TH:%TM  %9s  %p\n' 2>/dev/null | sort -rn)"
FILES="$(printf '%s\n' "$ALL_FILES" | head -40 | cut -d' ' -f2-)"
N_FILES="$(printf '%s\n' "$ALL_FILES" | grep -c .)"
[ "$N_FILES" -gt 40 ] && FILES="$FILES
(+ $((N_FILES - 40)) older files omitted)"
PROMPT="$(cat "$PROMPT_FILE")"
PROMPT="${PROMPT//\{\{NOW\}\}/$NOW_HUMAN}"
PROMPT="${PROMPT//\{\{REPORT\}\}/$REPORT}"
PROMPT="${PROMPT//\{\{FILES\}\}/$FILES}"
PROMPT="${PROMPT//\{\{BASE\}\}/$BASE}"
PROMPT="${PROMPT//\{\{CHAT_ID\}\}/$CHAT_ID}"

if [ "$DRY_RUN" = 1 ]; then
  echo "[$TS] DRY-RUN escalate=1" >> "$LOG"
  echo "WOULD ESCALATE — investigator briefing:"
  printf '%s\n' "$PROMPT"
  exit 0
fi

record_delivery() {
  local status="$1" receipt="${2:-}"
  [ -n "$ATTEMPT_ID" ] || return 0
  WATCHDOG_STATE="${WATCHDOG_STATE:-$BASE/watchdog/state.json}" \
  WATCHDOG_PENDING="${WATCHDOG_PENDING:-$BASE/watchdog/delivery-pending.json}" \
  WATCHDOG_DELIVERY_LAST="${WATCHDOG_DELIVERY_LAST:-$BASE/watchdog/delivery-last.json}" \
  WATCHDOG_METRICS="${WATCHDOG_METRICS:-$BASE/watchdog/metrics-history.json}" \
  PYTHONPATH="$BASE" python3 -m watchdog.run --record-delivery "$status" "$ATTEMPT_ID" ${receipt:+"$receipt"}
}

RESULT=$("$CLAUDE_BIN" -p "$PROMPT" --model "$MODEL" --allowedTools Read \
  --dangerously-skip-permissions --output-format json 2>>"$LOG.err" \
  | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('result','').strip())
except Exception as e: print('PARSE_ERROR:'+str(e))" 2>/dev/null)
RC=$?
RESULT_1LINE="$(printf '%s' "$RESULT" | tr '\n' ' ')"

if [ "$RC" -eq 0 ] && [ -n "$RESULT" ] && ! printf '%s' "$RESULT" | grep -q '^FAILED'; then
  # Persist possible delivery before contacting Telegram. A killed sender is never replayed.
  record_delivery attempting 2>>"$LOG.err" || exit 1
  RECEIPT="$(printf '%s' "$RESULT" | TG_SEND_RECEIPT_OUTPUT=1 "$TG_SEND" "$CHAT_ID" - 2>>"$LOG.err")"
  SEND_RC=$?
  if [ "$SEND_RC" -eq 0 ]; then
    if record_delivery accepted "$RECEIPT" 2>>"$LOG.err"; then
      echo "[$TS] rc=0 escalate=1 result=\"SENT ${RESULT_1LINE:0:70}\"" >> "$LOG"
      echo "escalated + provider accepted notification."
      exit 0
    fi
  else
    [ "$SEND_RC" -eq 3 ] && DELIVERY_STATUS=uncertain || DELIVERY_STATUS=failed
    record_delivery "$DELIVERY_STATUS" 2>>"$LOG.err" || true
  fi
  RC=1
fi

[ "$RC" -eq 0 ] && RC=1
echo "[$TS] rc=$RC escalate=1 result=\"${RESULT_1LINE:0:80}\"" >> "$LOG"
echo "FAILED to deliver watchdog alert (rc=$RC): $RESULT" >&2
exit 1
