# Slimmed superpowers preamble (local override)

`SKILL.md` here is the master copy of a slimmed `using-superpowers` skill that
replaces the upstream one in the superpowers plugin cache. The plugin's
SessionStart hook injects that file verbatim into every session, and the
upstream version is heavy on coercion (ALL-CAPS mandates, a 12-row "red flags"
table) that Anthropic's context-engineering guidance for Claude 5-generation
models says now *hurts* model behavior rather than helping it:
https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models

The slim version (481 -> 187 words) keeps everything functional — subagent
stop, check-skills-first rule, process-before-implementation ordering,
platform adaptation, CLAUDE.md precedence — and drops only the coercive
rule-list framing.

The override has two parts, both handled by `reapply.sh`:

1. **`SKILL.md`** — wholesale copy of the slim master over the cached skill.
2. **Hook wrapper** — the hook script (`hooks/session-start`) wraps whatever it
   injects in `<EXTREMELY_IMPORTANT>` tags; a targeted one-line patch replaces
   that with neutral "Skills preamble" framing. It is deliberately NOT a
   wholesale file copy: the hook changes functionally between releases, so the
   patch only fires when the upstream line matches the known pattern, and
   otherwise warns for manual review. The script also validates that the
   patched hook still emits valid JSON.

## Applying / re-applying

```
./reapply.sh
```

Idempotent. Needed once after every superpowers plugin update: the cache is
versioned per release, so each update ships the fat upstream file again.
The script backs up the incoming upstream file as `SKILL.md.orig-<version>`
before overwriting — after a major plugin update, diff it against the previous
orig to see if upstream changed the skill in ways worth folding into the slim
master.

This is a LOCAL override only. Upstream (obra/superpowers) explicitly rejects
PRs that reword its tuned skill content — do not send this upstream.

Takes effect on the next new session (hook fires on startup/clear/compact).
Undo: copy `SKILL.md.orig-<version>` and `session-start.orig-<version>` back
over their cache-path counterparts.
