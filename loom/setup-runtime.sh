#!/usr/bin/env bash
# Provision (or repair) the dedicated Loom cron runtime: a clone of this repo pinned to
# `main`, with its OWN venv. The nightly cron runs loom from HERE, never from the shared
# dev checkout — because interactive sessions flip the shared repo's working tree between
# branches, and a flip mid-run once deleted loom/prompts/route.md out from under a live
# backfill. This clone's working tree is never flipped, so loom's tracked code+prompts
# are stable for the duration of a run.
#
# Loom DATA (state/ledger/spool/learnings/logs) is NOT duplicated here: cli.py hardcodes
# the shared repo's loom/ dir, and those files are gitignored so branch flips never touch
# them. So this runtime supplies CODE only; the shared repo remains the data home.
#
# `import loom`/`import council` and loom's _PROMPTS are bound by the editable install +
# module location, so they MUST resolve to THIS clone — hence a separate venv with an
# editable install rooted here. Idempotent: safe to re-run to repair the clone or venv.
set -euo pipefail

SHARED="/home/dev/projects/ai-harness"
RUNTIME="/home/dev/loom-runtime"
PY="${PYTHON:-python3}"

# 1. Clone (from the LOCAL shared repo, so we track its `main` ref — which is ahead of
#    GitHub and is where the rollout merges land — without requiring a push).
if [ ! -d "$RUNTIME/.git" ]; then
  echo "[setup-runtime] cloning $SHARED -> $RUNTIME"
  git clone --quiet "$SHARED" "$RUNTIME"
fi
git -C "$RUNTIME" remote set-url origin "$SHARED"
git -C "$RUNTIME" fetch --quiet origin main
git -C "$RUNTIME" checkout --quiet -B main origin/main
git -C "$RUNTIME" reset --hard --quiet origin/main

# 2. Dedicated venv with an editable install rooted in $RUNTIME (so `import loom`/`council`
#    and _PROMPTS resolve here), plus loom's non-stdlib deps beyond `requests`:
#    PyYAML, and detect-secrets — WITHOUT it the secret gate (loom/gate.py) fails
#    closed and quarantines every transcript it scans.
if [ ! -x "$RUNTIME/.venv/bin/python" ]; then
  echo "[setup-runtime] creating venv at $RUNTIME/.venv"
  "$PY" -m venv "$RUNTIME/.venv"
fi
"$RUNTIME/.venv/bin/pip" install --quiet --upgrade pip
"$RUNTIME/.venv/bin/pip" install --quiet -e "$RUNTIME"
"$RUNTIME/.venv/bin/pip" install --quiet PyYAML detect-secrets

# 3. Verify isolation, exactly as the cron invokes it: PYTHONPATH bound to the runtime, from
#    a neutral CWD (cron runs from /home/dev). loom must resolve to the runtime clone.
RESOLVED="$(cd / && PYTHONPATH="$RUNTIME" "$RUNTIME/.venv/bin/python" -c 'import loom; print(loom.__file__)')"
case "$RESOLVED" in
  "$RUNTIME"/*) echo "[setup-runtime] OK: loom resolves to runtime clone: $RESOLVED" ;;
  *) echo "[setup-runtime] FAIL: loom resolves to $RESOLVED (expected under $RUNTIME)" >&2; exit 1 ;;
esac
echo "[setup-runtime] ready: $RUNTIME @ $(git -C "$RUNTIME" rev-parse --short HEAD)"
