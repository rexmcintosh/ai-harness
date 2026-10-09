#!/usr/bin/env bash
# run-fixit.sh — feedback -> fix -> ship. Claim an issue, fix it on a fresh branch,
# open a PR into the existing council CI gate. The human merges. Never touches main.
#
# Usage:
#   run-fixit.sh --issue "title :: body"      # ad-hoc issue
#   run-fixit.sh --file path/to/issue.md      # issue from a file (first line = title)
#   run-fixit.sh                              # claim the next issue from fixit/queue/
#   run-fixit.sh ... --run                    # actually fix + open PR (default: dry-run)
#   run-fixit.sh ... --base main              # PR target branch (default: main)
#
# Default is --dry-run: resolves the issue and prints the plan WITHOUT running the
# agent or opening a PR. Pass --run to execute. Needs `gh` + `claude`.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE="$(cd "$DIR/.." && pwd)"
QDIR="$DIR/queue"
PROMPT_FILE="$DIR/prompts/fix.md"
MODEL="${FIXIT_MODEL:-sonnet}"
CLAUDE_BIN="$(command -v claude || echo /usr/bin/claude)"
LOG_DIR="$DIR/logs"; mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/runs.log"

DRY_RUN=1; BASE_BRANCH="main"; ISSUE_SRC=""; ISSUE_ARG=""; FILE_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --run) DRY_RUN=0 ;;
    --dry-run) DRY_RUN=1 ;;
    --base) BASE_BRANCH="$2"; shift ;;
    --issue) ISSUE_ARG="$2"; ISSUE_SRC="arg"; shift ;;
    --file) FILE_ARG="$2"; ISSUE_SRC="file"; shift ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
  shift
done

# --- resolve the issue (title + body + id) ---
ISSUE_ID=""; TITLE=""; BODY=""
if [ "$ISSUE_SRC" = "arg" ]; then
  TITLE="${ISSUE_ARG%% :: *}"; BODY="${ISSUE_ARG#* :: }"
  [ "$BODY" = "$ISSUE_ARG" ] && BODY="$TITLE"
  ISSUE_ID="$(printf '%s' "$TITLE" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-' | cut -c1-48)"
elif [ "$ISSUE_SRC" = "file" ]; then
  [ -f "$FILE_ARG" ] || { echo "no such file: $FILE_ARG" >&2; exit 2; }
  TITLE="$(head -1 "$FILE_ARG")"; BODY="$(cat "$FILE_ARG")"
  ISSUE_ID="$(basename "$FILE_ARG" | tr '[:upper:] ' '[:lower:]-' | tr -cd 'a-z0-9-.' | cut -c1-48)"
else
  # claim next from the queue (only on a real run; dry-run just peeks)
  if [ "$DRY_RUN" = "1" ]; then
    PEEK="$(python3 -c "import sys;sys.path.insert(0,'$BASE');from fixit.queue import list_pending;import json;p=list_pending('$QDIR');print(json.dumps(p[0]) if p else '')")"
    [ -n "$PEEK" ] || { echo "queue empty, nothing to do."; exit 0; }
    TITLE="$(printf '%s' "$PEEK" | python3 -c "import json,sys;print(json.load(sys.stdin)['title'])")"
    ISSUE_ID="DRYRUN"
  else
    CLAIM="$(python3 -c "import sys;sys.path.insert(0,'$BASE');from fixit.queue import claim_next;import json;c=claim_next('$QDIR');print(json.dumps(c) if c else '')")"
    [ -n "$CLAIM" ] || { echo "queue empty, nothing to do."; exit 0; }
    ISSUE_ID="$(printf '%s' "$CLAIM" | python3 -c "import json,sys;print(json.load(sys.stdin)['id'])")"
    TITLE="$(printf '%s' "$CLAIM" | python3 -c "import json,sys;print(json.load(sys.stdin)['title'])")"
    BODY="$(printf '%s' "$CLAIM" | python3 -c "import json,sys;print(json.load(sys.stdin)['body'])")"
  fi
fi
[ -n "$ISSUE_ID" ] || ISSUE_ID="issue"
BRANCH="fixit/${ISSUE_ID}"
TS="$(date -Iseconds)"

if [ "$DRY_RUN" = "1" ]; then
  echo "── fixit dry-run ──"
  echo "issue:  $TITLE"
  echo "branch: $BRANCH  (PR -> $BASE_BRANCH)"
  echo "model:  $MODEL"
  echo "(pass --run to fix it on the branch and open a PR into the council CI gate)"
  exit 0
fi

_fail() { echo "FAILED: $1" >&2; echo "[$TS] id=$ISSUE_ID rc=1 :: $1" >> "$LOG"
  [ "$ISSUE_SRC" = "" ] && python3 -c "import sys;sys.path.insert(0,'$BASE');from fixit.queue import mark_failed;mark_failed('$QDIR','$ISSUE_ID',error='''$1''')" 2>/dev/null
  exit 1; }

# --- prepare a clean branch off the base ---
cd "$BASE" || _fail "cannot cd to repo"
# Safety: only run from a checkout sitting on the base branch. This refuses to
# hijack a feature branch or a linked git worktree (where `git switch` would move
# that worktree's HEAD). In production fixit runs from the main checkout on `main`.
CUR_BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null)"
[ "$CUR_BRANCH" = "$BASE_BRANCH" ] || _fail "must run from a checkout on '$BASE_BRANCH' (currently on '$CUR_BRANCH'); refusing to hijack it"
[ -z "$(git status --porcelain)" ] || _fail "working tree is dirty; refusing to start"
git fetch origin "$BASE_BRANCH" --quiet 2>/dev/null || true
git switch -c "$BRANCH" "origin/$BASE_BRANCH" 2>/dev/null \
  || git switch -c "$BRANCH" "$BASE_BRANCH" 2>/dev/null \
  || _fail "could not create branch $BRANCH"

# --- run the constrained fix agent (edits + commits only) ---
PROMPT="$(cat "$PROMPT_FILE")"
PROMPT="${PROMPT//\{\{BRANCH\}\}/$BRANCH}"
PROMPT="${PROMPT//\{\{BASE\}\}/$BASE}"
PROMPT="${PROMPT//\{\{TITLE\}\}/$TITLE}"
PROMPT="${PROMPT//\{\{BODY\}\}/$BODY}"

RESULT=$("$CLAUDE_BIN" -p "$PROMPT" --model "$MODEL" \
  --allowedTools Read Edit Write Bash \
  --dangerously-skip-permissions --output-format json 2>>"$LOG.err" \
  | python3 -c "import json,sys
try: print(json.load(sys.stdin).get('result','').strip())
except Exception as e: print('PARSE_ERROR:'+str(e))" 2>/dev/null)

# --- gate: must have committed something on the branch ---
if [ -z "$(git log "origin/$BASE_BRANCH..$BRANCH" --oneline 2>/dev/null || git log "$BASE_BRANCH..$BRANCH" --oneline)" ]; then
  git switch - >/dev/null 2>&1; git branch -D "$BRANCH" >/dev/null 2>&1
  _fail "agent produced no commit ($RESULT)"
fi

# --- gate: tests must pass ---
if ! python3 -m pytest tests/ -q >>"$LOG.err" 2>&1; then
  _fail "tests failed after fix; leaving branch $BRANCH for inspection (no PR opened)"
fi

# --- ship: push + open PR into the council CI gate ---
git push -u origin "$BRANCH" --quiet || _fail "git push failed"
PR_URL=$(gh pr create --base "$BASE_BRANCH" --head "$BRANCH" \
  --title "fixit: $TITLE" \
  --body "Automated fix by the fixit loop.

**Issue:** $TITLE

$BODY

---
🤖 The council CI review runs on this PR. A human merges — fixit never merges." \
  2>>"$LOG.err") || _fail "gh pr create failed"

echo "[$TS] id=$ISSUE_ID rc=0 :: $PR_URL" >> "$LOG"
[ "$ISSUE_SRC" = "" ] && python3 -c "import sys;sys.path.insert(0,'$BASE');from fixit.queue import mark_done;mark_done('$QDIR','$ISSUE_ID',result='''$PR_URL''')" 2>/dev/null
echo "opened PR: $PR_URL"
