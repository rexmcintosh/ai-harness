#!/usr/bin/env bash
# Loom nightly runner: single-run guard + venv; runs `absorb --live`, then sends a
# scrubbed Telegram run summary (success OR failure). Mirrors the bebop pattern.
#
# Runs loom CODE from a DEDICATED clone pinned to `main` (RUNTIME, provisioned by
# setup-runtime.sh), NOT the shared dev checkout — interactive sessions flip the shared
# repo's working tree between branches, and a flip mid-run once deleted a loom prompt out
# from under a live run. RUNTIME is never flipped. Loom DATA + logs stay in the shared
# repo (DATA_REPO): cli.py hardcodes that loom/ dir and those files are gitignored, so
# branch flips never disturb them. Install this script's path from RUNTIME in cron.
set -uo pipefail

# Cron's PATH is /usr/bin:/bin, but the claude CLI lives in ~/.local/bin since the
# 2026-07-25 installer migration. Without this, every LLM call (and the Telegram
# failure ping below) dies with FileNotFoundError — the 2026-07-26..08-01 outage.
export PATH="$HOME/.local/bin:$PATH"

# RUNTIME = this script's clone (pinned to main). DATA_REPO = shared repo (state/ledger/logs).
RUNTIME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_REPO="/home/dev/projects/ai-harness"
PY="$RUNTIME/.venv/bin/python"
# loom is not a declared package (the editable install maps only `council`), so bind
# `import loom`/`import council` to the runtime clone explicitly — never via CWD, which
# under cron is /home/dev. Data paths stay in DATA_REPO via cli.py's hardcoded constants.
export PYTHONPATH="$RUNTIME"
LOCK="$DATA_REPO/loom/.run.lock"
LOG="$DATA_REPO/loom/logs/runs.log"
CHAT_ID="7735693897"
mkdir -p "$DATA_REPO/loom/logs"

# Load VENICE_API_KEY etc. for any venice-backed path (absorb is claude, but harmless).
[ -f /home/dev/.env ] && set -a && . /home/dev/.env && set +a

exec 9>"$LOCK"
if ! flock -n 9; then echo "[$(date -Iseconds)] another run in progress; skipping" >>"$LOG"; exit 0; fi

# Under the lock (so no concurrent run resets mid-flight), pin RUNTIME to the latest merged
# main. Failure to sync is non-fatal — run the existing pinned (safe, never-flipped) checkout
# rather than skip the nightly. NOTE: this refreshes CODE only, not the venv — re-run
# setup-runtime.sh after any dependency change (loom's deps are just requests + PyYAML).
if ! { git -C "$RUNTIME" fetch --quiet origin main && git -C "$RUNTIME" reset --hard --quiet origin/main; }; then
  echo "[$(date -Iseconds)] WARN: could not sync $RUNTIME to origin/main; running existing checkout" >>"$LOG"
fi

TS="$(date -Iseconds)"
OUT="$("$PY" -m loom.cli absorb --live 2>>"$LOG.err")"; RC=$?
echo "[$TS] rc=$RC $OUT" >>"$LOG"

# --- AUTO-PROMOTE -------------------------------------------------------------
# Promote runs HERE, inside the absorb flock, rather than near the 23:50 diem
# deadline. A drain job hard-killed at that deadline dies mid-weave and leaves the
# loom-shadow worktree dirty, and promote's preflight (rightly) aborts on a dirty
# shadow — that happened on 2026-07-19. Under this flock no other loom process is
# running, so the shadow has settled. `--auto` consults the gate: it stands down on
# a hold, and refuses outright if a _staged/.claude swap is waiting (those rewrite
# live memories/skills and stay hand-gated). A refusal is a normal outcome.
PROMO="$("$PY" -m loom.cli promote --auto 2>>"$LOG.err")"; PRC=$?
echo "[$TS] promote rc=$PRC $PROMO" >>"$LOG"

# --- SNAPSHOT FOR THE MORNING BRIEFING -----------------------------------------
# One payload, read by the 07:00 briefing to emit (or omit) its single loom line.
# Written atomically (tmp + mv): the briefing may read this at any moment, and a
# half-written file would silently cost Rex the line. `promoted` carries what just
# landed — the post-promote `pending` state no longer knows, since it merged.
"$PY" - "$PROMO" <<'PY' > "$DATA_REPO/loom/pending.json.tmp" 2>>"$LOG.err" && \
  mv -f "$DATA_REPO/loom/pending.json.tmp" "$DATA_REPO/loom/pending.json" || \
  rm -f "$DATA_REPO/loom/pending.json.tmp"
import json, sys, time
from loom.cli import default_config, pending_payload
payload = pending_payload(default_config(), time.strftime("%Y-%m-%d"))
try:
    payload["promoted"] = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else {}
except json.JSONDecodeError:
    payload["promoted"] = {}
json.dump(payload, sys.stdout)
PY

# --- TELEGRAM: FAILURES ONLY ---------------------------------------------------
# Success used to ping here every night, and that is exactly what turned this
# channel into wallpaper: the summary read "loom-shadow: 90 commits to review
# ⚠️ STALE" nightly for 8 days and was never acted on. The daily report now rides
# in the 07:00 Bebop briefing — the one stream Rex actually reads. Failures still
# ping, so silence never hides a break.
#
# "Failure" includes the silent kind: absorb can exit 0 while every distill call
# errors (0 distilled, N failed — the 2026-07-26..08-01 outage shape). Treat a
# night that tried sessions and distilled none of them as a failure too.
SILENT_FAIL="$("$PY" - "$OUT" <<'PY' 2>/dev/null
import json, sys
try:
    s = json.loads(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1] else {}
except json.JSONDecodeError:
    s = {}
print(s.get("failed", 0) if s.get("failed", 0) > 0 and s.get("distilled", 0) == 0 else "")
PY
)"
if [ "$RC" -ne 0 ] || [ "$PRC" -ne 0 ] || [ -n "$SILENT_FAIL" ]; then
  MSG="$("$PY" - "$RC" "$PRC" "$SILENT_FAIL" <<'PY' 2>/dev/null
import sys
from loom.summary import scrub
rc, prc, silent = sys.argv[1], sys.argv[2], sys.argv[3]
if rc != "0" or prc != "0":
    what = "absorb" if rc != "0" else "promote"
    msg = f"⚠️ Loom {what} failed (absorb rc={rc}, promote rc={prc}). Check loom/logs/."
else:
    msg = (f"⚠️ Loom distilled 0 sessions but {silent} attempts failed (rc=0). "
           "Likely claude binary/PATH breakage. Check loom/logs/runs.log.err.")
print(scrub(msg))
PY
)"
  [ -z "$MSG" ] && MSG="⚠️ Loom failed (absorb rc=$RC, promote rc=$PRC, failed=${SILENT_FAIL:-?}). Check loom/logs/."
  # Raw Bot API send (bin/tg-send) — no claude call, no telegram plugin/MCP needed.
  "$RUNTIME/bin/tg-send" "$CHAT_ID" "$MSG" >/dev/null 2>&1 || true
fi

exit $RC
