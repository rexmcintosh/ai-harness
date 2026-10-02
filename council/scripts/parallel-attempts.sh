#!/usr/bin/env bash
# parallel-attempts.sh — the "waste tokens, save time" loop, end to end.
#
# Generate K candidate answers to the SAME task across K different Venice models
# (in parallel), then let `council compare` rank them and pick a winner. This is
# the self-contained version for a single prompt; for repo-wide code building the
# candidate generator is `claude` / parallel git worktrees instead.
#
# Usage:
#   parallel-attempts.sh "your task / prompt"
#   echo "your task" | parallel-attempts.sh
#   MODELS="claude-opus-4-8,gemini-3-1-pro-preview,deepseek-v4-pro" parallel-attempts.sh "..."
#
# Needs VENICE_COUNCIL_KEY (preferred) or VENICE_API_KEY (fallback), sourced
# from /home/dev/.env if present — resolved by council.config.get_api_key(),
# same as the rest of council, so spend bills to council's own key.
set -uo pipefail

[ -f /home/dev/.env ] && set -a && . /home/dev/.env && set +a

TASK="${1:-$(cat)}"
[ -n "$TASK" ] || { echo "usage: parallel-attempts.sh \"task\"" >&2; exit 2; }
MODELS="${MODELS:-claude-opus-4-8,gemini-3-1-pro-preview,deepseek-v4-pro}"

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
IFS=',' read -ra MODEL_ARR <<< "$MODELS"

gen() {  # $1 = model, $2 = outfile
  TASK="$TASK" python3 - "$1" "$2" <<'PY'
import os, sys
from council.config import get_api_key
from council.venice import VeniceClient
model, out = sys.argv[1], sys.argv[2]
client = VeniceClient(get_api_key())
try:
    text = client.complete(
        model,
        "You are a senior engineer. Answer the task directly and completely. "
        "Output only the solution, no preamble.",
        os.environ["TASK"], json_mode=False)
except Exception as e:  # noqa: BLE001
    text = f"(generation failed: {e})"
open(out, "w").write(text)
PY
}

echo "Generating ${#MODEL_ARR[@]} candidates across: $MODELS" >&2
FILES=()
for i in "${!MODEL_ARR[@]}"; do
  f="$WORK/candidate-$i-${MODEL_ARR[$i]//\//_}.txt"
  gen "${MODEL_ARR[$i]}" "$f" &
  FILES+=("$f")
done
wait

echo "Comparing…" >&2
council compare --task "$TASK" "${FILES[@]}"
