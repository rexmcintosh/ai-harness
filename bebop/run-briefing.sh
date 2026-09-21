#!/usr/bin/env bash
# Bebop briefing runner — twice-daily Gmail+Calendar digest to Telegram, via headless Claude Code.
# Usage: run-briefing.sh [morning|evening]
#
# Design notes:
#  - Runs the cheap model (haiku) over DELTAS only (email since last successful run) to keep cost tiny.
#  - state.json tracks last successful run; it only advances on success, so a failed run never skips email.
#  - Tools are whitelisted via --allowedTools so the agent can't wander; non-listed tools are denied.
#  - On failure it still pings Telegram, so silence never hides a break.
set -uo pipefail

# Cron's PATH has no ~/.local/bin, where the claude CLI lives since the 2026-07-25
# installer migration. Without this every run dies rc=127 before doing anything —
# the 2026-08-05..08-08 silent outage (the failure ping needed claude too, then).
# Same fix loom's runner has carried since its 07-26..08-01 outage.
export PATH="$HOME/.local/bin:$PATH"

MODE="${1:-morning}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROMPT_FILE="$DIR/prompts/briefing-$MODE.md"
STATE_FILE="$DIR/state.json"
LOG_DIR="$DIR/logs"
LOG="$LOG_DIR/runs.log"
CHAT_ID="7735693897"
MODEL="haiku"
CLAUDE_BIN="$(command -v claude || echo /usr/bin/claude)"

mkdir -p "$LOG_DIR"
[ -f "$PROMPT_FILE" ] || { echo "missing prompt: $PROMPT_FILE" >&2; exit 1; }

# --- delta window: epoch seconds of last successful run (default: 24h ago) ---
NOW_EPOCH=$(date +%s)
if [ -f "$STATE_FILE" ]; then
  SINCE_EPOCH=$(python3 -c "import json;print(json.load(open('$STATE_FILE')).get('last_run_epoch', $NOW_EPOCH-86400))" 2>/dev/null || echo $((NOW_EPOCH-86400)))
else
  SINCE_EPOCH=$((NOW_EPOCH-86400))
fi
NOW_HUMAN=$(TZ=Europe/Lisbon date '+%A %Y-%m-%d %H:%M %Z')
SINCE_HUMAN=$(TZ=Europe/Lisbon date -d "@$SINCE_EPOCH" '+%A %Y-%m-%d %H:%M %Z' 2>/dev/null || echo "~24h ago")

# --- build prompt with substitutions ---
PROMPT=$(cat "$PROMPT_FILE")
PROMPT="${PROMPT//\{\{NOW\}\}/$NOW_HUMAN}"
PROMPT="${PROMPT//\{\{SINCE\}\}/$SINCE_HUMAN}"
PROMPT="${PROMPT//\{\{SINCE_EPOCH\}\}/$SINCE_EPOCH}"
PROMPT="${PROMPT//\{\{CHAT_ID\}\}/$CHAT_ID}"

# --- loom line -----------------------------------------------------------------
# Composed HERE, in code, not by the briefing model: these are counts Rex acts on,
# and a paraphrase ("a bunch of articles") would be worse than no line at all. The
# model receives finished text and is told to pass it through verbatim.
# Empty when nothing needs him — the silence is deliberate, and is what keeps the
# line meaningful on the mornings it does appear.
LOOM_LINE=""
LOOM_PENDING="/home/dev/projects/ai-harness/loom/pending.json"
LOOM_PY="/home/dev/projects/ai-harness/.venv/bin/python"
if [ -r "$LOOM_PENDING" ] && [ -x "$LOOM_PY" ]; then
  LOOM_LINE=$("$LOOM_PY" - "$LOOM_PENDING" <<'PY' 2>/dev/null || true
import json, sys
from loom.pending import briefing_line
try:
    print(briefing_line(json.load(open(sys.argv[1]))), end="")
except Exception:
    pass                      # a broken loom must never cost Rex his email briefing
PY
)
fi
PROMPT="${PROMPT//\{\{LOOM\}\}/$LOOM_LINE}"

ALLOWED=(
  mcp__claude_ai_Gmail__search_threads
  mcp__claude_ai_Gmail__get_thread
  mcp__claude_ai_Google_Calendar__list_events
  mcp__claude_ai_Google_Calendar__list_calendars
)

# The agent COMPOSES only; the send happens here via raw Bot API. This removes
# the telegram plugin/MCP dependency entirely (plugin can be disabled globally)
# and with it the historical failure modes: MCP still "connecting" at run time,
# MarkdownV2 escaping rejects, and the placeholder-send incident.
TG_SEND="$DIR/../bin/tg-send"

TS="$(date -Iseconds)"
# NOTE: headless MCP tool calls require --dangerously-skip-permissions; --allowedTools
# alone does not authorize them non-interactively. --allowedTools is kept to signal intent
# and narrow the surface. This box is Rex's own VPS and the prompt is fixed/benign.

# --- compose, with ONE retry ------------------------------------------------------
# The claude.ai Gmail/Calendar connectors are listed by name at session start, but on a
# slow start they are not callable for the first 30-45s. The agent looks them up ~10
# times, gives up and answers FAILED (2026-08-26 18:00, 2026-09-18 18:00; the 08-22
# 07:00 run got through on its 11th lookup). A fresh process a little later connects
# normally, and state.json has not advanced, so the retry covers the same window.
# Exactly one retry: a real outage must still reach the failure ping below.
MAX_ATTEMPTS="${BEBOP_MAX_ATTEMPTS:-2}"
RETRY_DELAY="${BEBOP_RETRY_DELAY:-90}"
case "$MAX_ATTEMPTS" in
  1|2) ;;
  ''|*[!0-9]*) echo "BEBOP_MAX_ATTEMPTS='$MAX_ATTEMPTS' is not a number; using 2" >&2; MAX_ATTEMPTS=2 ;;
  *) MAX_ATTEMPTS=2 ;;                      # "one retry" is the contract, whatever the env asks
esac
case "$RETRY_DELAY" in
  ''|*[!0-9]*) echo "BEBOP_RETRY_DELAY='$RETRY_DELAY' is not a number; using 90" >&2; RETRY_DELAY=90 ;;
esac
ATTEMPT=0
while :; do
  ATTEMPT=$((ATTEMPT+1))
  OUT=$("$CLAUDE_BIN" -p "$PROMPT" \
    --model "$MODEL" \
    --allowedTools "${ALLOWED[@]}" \
    --dangerously-skip-permissions \
    --output-format json 2>>"$LOG.err")
  RC=$?

  RESULT=$(printf '%s' "$OUT" | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('result','').strip())
except Exception as e: print('PARSE_ERROR:'+str(e))" 2>/dev/null)
  USAGE=$(printf '%s' "$OUT" | python3 -c "import json,sys
try:
 d=json.load(sys.stdin); u=d.get('usage',{})
 print('cost_usd=%s in=%s out=%s'%(d.get('total_cost_usd','?'),u.get('input_tokens','?'),u.get('output_tokens','?')))
except: print('usage=?')" 2>/dev/null)

  if [ $RC -eq 0 ] && [ -n "$RESULT" ] && ! printf '%s' "$RESULT" | grep -qE '^(FAILED|PARSE_ERROR)'; then
    break                                   # composed a briefing
  fi
  [ "$ATTEMPT" -ge "$MAX_ATTEMPTS" ] && break
  echo "attempt $ATTEMPT failed (rc=$RC): $(printf '%s' "$RESULT" | head -c 120 | tr '\n' ' ') - retrying in ${RETRY_DELAY}s" >&2
  sleep "$RETRY_DELAY"
done
[ "$ATTEMPT" -gt 1 ] && USAGE="$USAGE attempts=$ATTEMPT"

# --- backlog line, morning only ---------------------------------------------------
# backlog-run leaves finished work `in_review` and parks the rest `held`; nothing pinged
# Rex, so items sat for weeks. This is that reminder. Unlike the loom line above it is
# NOT handed to the model through the prompt — it is appended to the finished text below,
# after the agent has answered, so the briefing model can neither drop it nor reword it.
# Empty output means nothing is waiting, and then no line is added at all.
# Fail open: a missing helper, a missing backlog, a crash or a hang all cost the line and
# nothing else (the helper itself always exits 0; `timeout` and `|| true` cover the rest).
BACKLOG_LINE=""
BACKLOG_FLAG=0
BACKLOG_HELPER="$DIR/backlog_line.py"
BACKLOG_PY="$DIR/../.venv/bin/python"
[ -x "$BACKLOG_PY" ] || BACKLOG_PY="$(command -v python3 || true)"
# The helper sits on the send path, so its wait is bounded twice: a garbled value falls
# back to 10 seconds and nothing above the cap (30) is honoured. `-k 2` is for a helper
# that ignores TERM.
BACKLOG_TIMEOUT_CAP="${BEBOP_BACKLOG_TIMEOUT_CAP:-30}"
case "$BACKLOG_TIMEOUT_CAP" in ''|*[!0-9]*) BACKLOG_TIMEOUT_CAP=30 ;; esac
BACKLOG_TIMEOUT="${BEBOP_BACKLOG_TIMEOUT:-10}"
case "$BACKLOG_TIMEOUT" in ''|*[!0-9]*) BACKLOG_TIMEOUT=10 ;; esac
[ "$BACKLOG_TIMEOUT" -ge 1 ] || BACKLOG_TIMEOUT=10
[ "$BACKLOG_TIMEOUT" -le "$BACKLOG_TIMEOUT_CAP" ] || BACKLOG_TIMEOUT="$BACKLOG_TIMEOUT_CAP"
TG_MAX_CHARS=4000                     # Telegram refuses a message over 4096 characters

# --- send: agent output IS the briefing text (or FAILED:<reason>) ---
# Success contract for the log stays `rc=0 ... result="SENT..."` — the watchdog's
# check_bebop_runs parses `[ts] ... rc=N` from these lines; keep that shape.
SEND_OK=0
if [ $RC -eq 0 ] && [ -n "$RESULT" ] && ! printf '%s' "$RESULT" | grep -q '^FAILED'; then
  # The backlog line rides on a briefing that actually composed. It is deliberately NOT
  # stapled to the failure ping below: that ping is an alarm, and an ordinary-looking
  # backlog nag under it would make a broken morning read like a normal one. The items
  # are still there tomorrow; the alarm has to stay loud today.
  # The helper runs here and not earlier, so a slow backlog read can never delay the
  # failure ping. And the line is optional: if it would push the message past Telegram's
  # limit, the briefing goes without it rather than not at all.
  if [ "$MODE" = "morning" ] && [ -r "$BACKLOG_HELPER" ] && [ -n "$BACKLOG_PY" ]; then
    BACKLOG_LINE=$(timeout -k 2 "$BACKLOG_TIMEOUT" \
      "$BACKLOG_PY" "$BACKLOG_HELPER" 2>/dev/null || true)
  fi
  MESSAGE="$RESULT"
  if [ -n "$BACKLOG_LINE" ] && [ $(( ${#RESULT} + ${#BACKLOG_LINE} + 2 )) -le "$TG_MAX_CHARS" ]; then
    MESSAGE="$RESULT

$BACKLOG_LINE"
    BACKLOG_FLAG=1
  fi
  if printf '%s' "$MESSAGE" | "$TG_SEND" "$CHAT_ID" -; then
    SEND_OK=1
  else
    RC=1
    BACKLOG_FLAG=0                      # nothing reached Rex, so nothing was added
    RESULT="FAILED:tg-send (Bot API) send failed"
  fi
else
  [ $RC -eq 0 ] && RC=1
fi

# runs.log entries must stay ONE line ([ts] ... rc=N — parsed by the watchdog),
# and RESULT is now multiline briefing text, so flatten before logging.
RESULT_1LINE="$(printf '%s' "$RESULT" | tr '\n' ' ')"

if [ $SEND_OK -eq 1 ]; then
  echo "[$TS] mode=$MODE rc=0 result=\"SENT ${RESULT_1LINE:0:80}\" $USAGE backlog_line=$BACKLOG_FLAG" >> "$LOG"
  python3 -c "import json;open('$STATE_FILE','w').write(json.dumps({'last_run_epoch':$NOW_EPOCH,'last_run_iso':'$TS','last_mode':'$MODE'},indent=2)+'\n')"
  echo "ok: sent"
  exit 0
else
  echo "[$TS] mode=$MODE rc=$RC result=\"${RESULT_1LINE:0:90}\" $USAGE backlog_line=$BACKLOG_FLAG" >> "$LOG"
  "$TG_SEND" "$CHAT_ID" "⚠️ Bebop $MODE briefing failed (rc=$RC). Check ~/projects/ai-harness/bebop/logs/." || true
  echo "FAILED rc=$RC result=$RESULT" >&2
  exit 1
fi
