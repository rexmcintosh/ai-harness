#!/usr/bin/env bash
# Re-apply the slimmed using-superpowers preamble + neutral hook wrapper after a
# superpowers plugin update. The plugin cache is versioned
# (~/.claude/plugins/cache/.../superpowers/<version>/), so every update ships the
# upstream fat SKILL.md and <EXTREMELY_IMPORTANT> hook wrapper again.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
CACHE="$HOME/.claude/plugins/cache/claude-plugins-official/superpowers"
latest="$(ls -1v "$CACHE" | tail -1)"

# --- 1. SKILL.md: wholesale copy of the slim master ---------------------------
target="$CACHE/$latest/skills/using-superpowers/SKILL.md"
[ -f "$target" ] || { echo "no SKILL.md at $target" >&2; exit 1; }
if diff -q "$HERE/SKILL.md" "$target" >/dev/null; then
  echo "SKILL.md: already applied ($latest)"
else
  cp "$target" "$HERE/SKILL.md.orig-$latest"
  cp "$HERE/SKILL.md" "$target"
  echo "SKILL.md: applied slim preamble to $latest (upstream saved as SKILL.md.orig-$latest)"
  echo "  review upstream changes: diff $HERE/SKILL.md.orig-$latest $HERE/SKILL.md"
fi

# --- 2. hook wrapper: targeted line patch, never a wholesale overwrite --------
# The hook script changes functionally between releases (JSON formats, platform
# detection), so we only replace the one session_context assignment line, and only
# when it matches the known upstream pattern. If upstream rewrote that line, we
# warn and leave the script alone for manual review.
hook="$CACHE/$latest/hooks/session-start"
[ -f "$hook" ] || { echo "no hook at $hook" >&2; exit 1; }
HOOK_PATH="$hook" ORIG_SAVE="$HERE/session-start.orig-$latest" python3 <<'PYEOF'
import os, shutil, sys

hook = os.environ["HOOK_PATH"]
orig_save = os.environ["ORIG_SAVE"]
NEW = ('session_context="Skills preamble — the \'superpowers:using-superpowers\' skill '
       '(how to find and use skills; for all other skills, use the \'Skill\' tool):'
       '\\n\\n${using_superpowers_escaped}"\n')

with open(hook, encoding="utf-8") as f:
    lines = f.readlines()

if any(l == NEW for l in lines):
    print("hook wrapper: already applied")
    sys.exit(0)

idx = [i for i, l in enumerate(lines)
       if l.startswith('session_context="<EXTREMELY_IMPORTANT>')]
if len(idx) != 1:
    print("hook wrapper: upstream session_context line not found or ambiguous — "
          "upstream changed the hook; patch it manually (see README)")
    sys.exit(0)

shutil.copy2(hook, orig_save)
lines[idx[0]] = NEW
with open(hook, "w", encoding="utf-8") as f:
    f.writelines(lines)
print(f"hook wrapper: patched (upstream saved as {os.path.basename(orig_save)})")
PYEOF

# sanity: patched hook must still parse and emit valid JSON
bash -n "$hook"
CLAUDE_PLUGIN_ROOT="$CACHE/$latest" bash "$hook" | python3 -c "import json,sys; json.load(sys.stdin)" \
  && echo "hook output: valid JSON"
