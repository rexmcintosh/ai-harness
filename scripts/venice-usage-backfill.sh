#!/usr/bin/env bash
# Recover Venice spend the ledger never saw.
#
# Usage is written client-side, AFTER a response lands. A session killed mid-call
# leaves the bill with no local trace at all, and this host OOM-kills sessions
# about seventeen times a month (see the vps-oom-sessions note). Two confirmed
# holes: a hand-written recovery note for one 2.7549-DIEM claude-fable-5-1 call,
# and claude-fable-5-1 as a whole — 38.52 DIEM of output over three weeks, the
# account's dearest model, in no ledger row at all.
#
# This asks /billing/usage-history for the last N days, finds every billed
# request no ledger row claims, and appends one row per request:
#
#     project    = unattributed        (the invoice carries no project tag)
#     task_type  = recovered
#     source     = reconcile/billing
#     ext_id     = venice-bill:<requestId>
#     usd        = the billed DIEM, not a token estimate
#
# Idempotent twice over: ext_id is UNIQUE in the ledger, and on the next run the
# row this one wrote matches its own bill, so the request is no longer an orphan.
# Re-running over an overlapping window is free — which is exactly why the window
# is wider than the schedule period: a bill that lands late is still caught.
#
#   venice-usage-backfill.sh [DAYS]        default 3
#   DRY_RUN=1 venice-usage-backfill.sh     report only, write nothing
#
# Cron (a stable clock — the house rule forbids hanging a mutation off a fuzzy
# session-end event). Proposed, NOT installed:
#
#   CRON_TZ=UTC
#   20 3 * * *  /home/dev/projects/ai-harness/scripts/venice-usage-backfill.sh \
#                 >> /home/dev/.local/state/venice-usage/backfill.log 2>&1
#   CRON_TZ=Europe/Lisbon
#
# Exit codes: 0 ok, 1 could not run (no key, no binary), 2 the price table has
# drifted more than the tolerance — the row recovery still happened.
set -uo pipefail

DAYS="${1:-3}"
ENV_FILE="${VENICE_ENV_FILE:-$HOME/.env}"
STATE_DIR="${VENICE_USAGE_STATE_DIR:-$HOME/.local/state/venice-usage}"
LOCK="$STATE_DIR/backfill.lock"
BIN="${VENICE_USAGE_BIN:-$HOME/.local/bin/venice-usage}"

mkdir -p "$STATE_DIR"

# One variable out of ~/.env — never `set -a; . ~/.env; set +a`, which exports
# every secret on the box into this process just to reach one of them.
read_env_var() {
  sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" 2>/dev/null \
    | head -1 | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/"
}

if [ -z "${VENICE_ADMIN_KEY:-}" ]; then
  VENICE_ADMIN_KEY="$(read_env_var VENICE_ADMIN_KEY)"
  export VENICE_ADMIN_KEY
fi
if [ -z "${VENICE_ADMIN_KEY:-}" ]; then
  echo "$(date -u +%FT%TZ) venice-usage-backfill: VENICE_ADMIN_KEY not found in $ENV_FILE" >&2
  exit 1
fi
if [ ! -x "$BIN" ]; then
  echo "$(date -u +%FT%TZ) venice-usage-backfill: $BIN is not executable (pipx install council?)" >&2
  exit 1
fi

SINCE="$(date -u -d "$DAYS days ago" +%F)"
ARGS=(reconcile --since "$SINCE" --backfill)
[ "${DRY_RUN:-0}" = "1" ] && ARGS+=(--dry-run)

echo "$(date -u +%FT%TZ) venice-usage-backfill: since=$SINCE dry_run=${DRY_RUN:-0}"
# flock so a slow run (a wide window is many billing pages) can never overlap the
# next tick and race the same requests into the ledger twice. `-E 75` gives
# "already running" its own exit code, so it is not read as a failed reconcile.
/usr/bin/flock -n -E 75 "$LOCK" "$BIN" "${ARGS[@]}"
RC=$?
[ $RC -eq 75 ] && echo "$(date -u +%FT%TZ) venice-usage-backfill: previous run still holding $LOCK — skipped"
echo "$(date -u +%FT%TZ) venice-usage-backfill: rc=$RC"
exit $RC
