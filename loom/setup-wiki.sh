#!/usr/bin/env bash
# One-time: make ~/wiki a local-only git repo, add a detect-secrets pre-commit
# hook, and create the loom-shadow branch for v0 dry-run weaves.
set -euo pipefail
WIKI="/home/dev/wiki"
cd "$WIKI"
[ -d .git ] || git init -q
git config --local user.email "loom@localhost"
git config --local user.name "Loom"
# pre-commit: block any commit containing a detected secret
# Uses detect-secrets-hook (not detect-secrets scan) — the hook handles file
# paths correctly and returns nonzero when a secret is found.
# detect-secrets is not on PATH in the git hook environment, so we use the
# explicit venv binary path.
DETECT_SECRETS_HOOK="/home/dev/projects/ai-harness/.venv/bin/detect-secrets-hook"
HOOK=".git/hooks/pre-commit"
cat > "$HOOK" <<EOF
#!/usr/bin/env bash
set -euo pipefail
staged=\$(git diff --cached --name-only)
[ -z "\$staged" ] && exit 0
# detect-secrets-hook returns nonzero if any secret is found.
# Base64HighEntropyString and HexHighEntropyString are disabled to prevent
# false positives on benign IDs (Gmail thread IDs, Drive doc IDs, hashes).
# The LLM distill sanitize pass is the entropy backstop. Credential-specific
# and keyword detectors remain active to catch real secrets.
if printf '%s\n' "\$staged" | xargs -d '\n' "${DETECT_SECRETS_HOOK}" \
  --disable-plugin Base64HighEntropyString \
  --disable-plugin HexHighEntropyString \
  2>/dev/null; then
  exit 0  # no secrets found
else
  echo "pre-commit: secret detected in staged files — aborting commit" >&2
  exit 1
fi
EOF
chmod +x "$HOOK"
[ -f .gitignore ] || printf '_absorb_log.json\n.obsidian/\n' > .gitignore
git add -A && git commit -q -m "loom: initial wiki snapshot" || true
git branch -f loom-shadow
# v1: a dedicated worktree on loom-shadow so ~/wiki stays on master during runs.
WORKTREE="/home/dev/wiki-loom-shadow"
git worktree prune   # clear any stale registration (dir removed without prune)
if [ ! -d "$WORKTREE" ]; then
  git worktree add -q "$WORKTREE" loom-shadow
fi
echo "loom-shadow worktree: $WORKTREE"
echo "wiki repo ready; shadow branch 'loom-shadow' created; NO remote configured"
git remote -v  # must be empty
