#!/usr/bin/env bash
# Pull the Venice review council's usage artifacts out of GitHub Actions and
# merge them into this machine's ledger.
#
# Why: the merge gate's Venice calls are logged by VeniceClient._log_usage() into
# the RUNNER's ~/.local/state/venice-usage/ledger.db, which dies with the job. The
# workflow exports those rows to a build artifact (see
# docs/ci-usage-rollout-2026-09-12.md); this script is the other half.
#
# Ingest is idempotent (ext_id = gh:<repo>:<run>:<attempt>#<row>), so running this
# every night and re-running it after a failure cannot double-count spend.
#
# Usage:  tools/ci-usage/pull.sh [--days 8] [--repos FILE] [--dest DIR] [--keep]
# Needs:  gh (authenticated, `repo` scope) and the `venice-usage` console script.
# Exit:   0 all repos listed. 1 one or more could not be listed (their spend was
#         NOT pulled) — everything reachable is still ingested first. 2 bad args.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DAYS=8
REPOS="$HERE/repos.txt"
DEST=""
KEEP=0
WORKFLOW="venice-review.yml"
PATTERN="venice-usage*"

while [ $# -gt 0 ]; do
  case "$1" in
    --days)   DAYS="$2"; shift 2 ;;
    --repos)  REPOS="$2"; shift 2 ;;
    --dest)   DEST="$2"; shift 2 ;;
    --keep)   KEEP=1; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

command -v gh >/dev/null || { echo "error: gh is not installed" >&2; exit 2; }
command -v venice-usage >/dev/null || { echo "error: venice-usage is not on PATH" >&2; exit 2; }
[ -f "$REPOS" ] || { echo "error: no repo list at $REPOS" >&2; exit 2; }

ERRLOG="$(mktemp -t venice-ci-usage-err-XXXXXX)"
CLEAN_DEST=0
if [ -z "$DEST" ]; then
  DEST="$(mktemp -d -t venice-ci-usage-XXXXXX)"
  [ "$KEEP" -eq 1 ] || CLEAN_DEST=1
fi
# One trap for both temporaries — a second `trap ... EXIT` would silently
# replace the first and leak the download directory.
cleanup() { rm -f "$ERRLOG"; [ "$CLEAN_DEST" -eq 1 ] && rm -rf "$DEST"; return 0; }
trap cleanup EXIT
mkdir -p "$DEST"

SINCE="$(date -u -d "-${DAYS} days" +%Y-%m-%dT%H:%M:%SZ)"
echo "pulling venice-review usage artifacts created since $SINCE into $DEST"

runs_found=0
unreachable=0
while IFS= read -r repo; do
  case "$repo" in ''|\#*) continue ;; esac
  # A repo gh cannot reach must be LOUD. A 404 (renamed repo, a repos.txt entry
  # that is the ~/projects directory name rather than the GitHub repo name, the
  # workflow not on the default branch) used to be swallowed and printed as "no
  # runs in window" — indistinguishable from a quiet week, so that repo's spend
  # was dropped silently and forever.
  if ! ids="$(gh run list --repo "$repo" --workflow "$WORKFLOW" --limit 100 \
                --json databaseId,createdAt \
                --jq "[.[] | select(.createdAt >= \"$SINCE\")] | .[].databaseId" \
                2>"$ERRLOG")"; then
    echo "  $repo: error listing runs: $(head -n1 "$ERRLOG")" >&2
    unreachable=$((unreachable + 1))
    continue
  fi
  [ -n "$ids" ] || { echo "  $repo: no runs in window"; continue; }
  n=0
  for id in $ids; do
    out="$DEST/${repo//\//__}/$id"
    mkdir -p "$out"
    # A run with no matching artifact (older workflow, cancelled job) is normal.
    if gh run download "$id" --repo "$repo" --pattern "$PATTERN" --dir "$out" >/dev/null 2>&1; then
      n=$((n + 1)); runs_found=$((runs_found + 1))
    else
      rmdir "$out" 2>/dev/null || true
    fi
  done
  echo "  $repo: $n run(s) with usage artifacts"
done < "$REPOS"

if [ "$runs_found" -eq 0 ]; then
  echo "nothing to ingest"
else
  echo "ingesting into ${VENICE_USAGE_DB:-$HOME/.local/state/venice-usage/ledger.db}"
  venice-usage ingest "$DEST" --json
fi

# Ingest what we could reach first, THEN fail — a cron wrapper needs a nonzero
# exit to notice that part of the fleet was not pulled, but the rows we did get
# must still land in the ledger.
if [ "$unreachable" -gt 0 ]; then
  echo "error: $unreachable repo(s) could not be listed — their CI spend was NOT pulled" >&2
  exit 1
fi
