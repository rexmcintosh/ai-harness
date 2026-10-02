#!/usr/bin/env bash
# security-sweep.sh — scheduled repo-wide security sweep -> Telegram summary.
#
# Runs `council sweep` over a repo and sends the chair's summary to Telegram. The
# autonomous-security-research idea, bounded: --max-chunks caps coverage and the
# dropped count is reported, so it never silently claims to have scanned everything.
#
# Usage: security-sweep.sh [path] [--max-chunks N]
#   path defaults to the repo this script lives in.
# Needs VENICE_API_KEY (sourced from /home/dev/.env).
set -uo pipefail

# Cron's PATH has no ~/.local/bin, where pipx's `council` and the claude CLI live.
# Without this the sweep silently falls back to the repo module. Same fix as loom.
export PATH="$HOME/.local/bin:$PATH"

[ -f /home/dev/.env ] && set -a && . /home/dev/.env && set +a
: "${VENICE_API_KEY:?VENICE_API_KEY not set}"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$DIR/../.." && pwd)"
TARGET="${1:-$REPO_ROOT}"
MAX_CHUNKS="${MAX_CHUNKS:-40}"
CHAT_ID="7735693897"
MODEL="haiku"
CLAUDE_BIN="$(command -v claude || echo /usr/bin/claude)"
LOG_DIR="$REPO_ROOT/council/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/sweep.log"
TS="$(date -Iseconds)"

# Run the sweep (the global `council` if installed, else the repo module).
if command -v council >/dev/null 2>&1; then
  REPORT="$(council sweep "$TARGET" --max-chunks "$MAX_CHUNKS" --format md 2>>"$LOG.err")"
else
  REPORT="$(cd "$REPO_ROOT" && python3 -m council.cli sweep "$TARGET" \
    --max-chunks "$MAX_CHUNKS" --format md 2>>"$LOG.err")"
fi
RC=$?
echo "[$TS] rc=$RC target=$TARGET $(printf '%s' "$REPORT" | grep -c '^- ') findings" >> "$LOG"

# Pull just the summary block for the phone alert; attach nothing huge.
SUMMARY="$(printf '%s\n' "$REPORT" | sed -n '/### Summary/,/### Findings/p' | sed '1d;$d')"
[ -n "$SUMMARY" ] || SUMMARY="Sweep finished (rc=$RC). See $LOG."

# Raw Bot API send (bin/tg-send) — no claude call, no telegram plugin/MCP needed.
"$REPO_ROOT/bin/tg-send" "$CHAT_ID" "🛡️ Weekly security sweep — $(basename "$TARGET")
$SUMMARY" >/dev/null 2>&1 || true

echo "$REPORT"
