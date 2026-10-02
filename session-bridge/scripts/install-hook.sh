#!/usr/bin/env bash
# Adds the session-bridge Stop hook to ~/.claude/settings.json (idempotent).
# Run by the USER via a ! one-liner — agent writes to ~/.claude are auto-mode-gated.
set -euo pipefail

SETTINGS="$HOME/.claude/settings.json"
CMD="/usr/local/bin/bun /home/dev/projects/ai-harness/session-bridge/hook/stop-hook.ts"

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || printf '{}' > "$SETTINGS"

if ! jq -e . "$SETTINGS" >/dev/null 2>&1; then
  echo "error: $SETTINGS is not valid JSON — fix it by hand, then re-run" >&2
  exit 1
fi

if jq -e --arg cmd "$CMD" '[.hooks.Stop[]?.hooks[]?.command] | index($cmd)' "$SETTINGS" >/dev/null 2>&1; then
  echo "already installed"
  exit 0
fi

tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT
if ! jq --arg cmd "$CMD" \
  '.hooks.Stop = ((.hooks.Stop // []) + [{"hooks": [{"type": "command", "command": $cmd, "timeout": 30}]}])' \
  "$SETTINGS" > "$tmp"; then
  echo "error: could not rewrite $SETTINGS — settings.json is not valid JSON" >&2
  exit 1
fi
mv "$tmp" "$SETTINGS"
echo "installed — new sessions pick it up; running sessions need a restart"
