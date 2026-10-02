# Claude to Codex mirror

This directory contains three bounded tools. Neither changes live configuration unless `--apply` is passed.

`mirror.py` calls Codex's native `externalAgentConfig/detect` method. It uses `maxSessions: 0`, so it does not inspect or import conversations. The native importer supports `AGENTS_MD`, `CONFIG`, `SKILLS`, `PLUGINS`, `MCP_SERVER_CONFIG`, `SUBAGENTS`, `HOOKS`, `COMMANDS`, `MEMORY`, and `SESSIONS` in Codex 0.153.4. Detection only reports work that remains. Skill import does not replace an existing target directory.

`mirror_files.py` keeps Claude as the one source of truth for shared skills. `~/.claude/skills` and the enabled Claude plugins own the skill files; `~/.codex/skills` holds only symlinks to them, small wrappers for Claude commands, and the Codex-specific ports (`delegate`, `site-flow`, `codex`, `claude-connectors`) plus Codex system skills. Each run reads Claude's current skills, commands, `plugins/installed_plugins.json` and `settings.json` `enabledPlugins`, so plugin links follow the installed version instead of a frozen snapshot. Project-scoped commands and skills still come from the saved metadata-only inventory at `~/.codex/mirrors/claude/inventory.json` (or `/tmp/claude-skill-inventory.json` on first install) and become small Codex skill wrappers under each repository `.agents/skills` directory. Project wrappers contain an explicit repository check. `--frozen` plans from the saved inventory alone. The adapted `delegate` and `site-flow` ports and unmanaged target paths are preserved. An unmanaged Codex skill folder with the same files, file contents, executable bits and internal links as its Claude source is a duplicate: apply renames it into the backup folder (same filesystem only) and replaces it with a symlink. A copy that differs in any of those is preserved and reported as `preserve-unmanaged`. Apply reads Claude's state, plans and applies under the single-writer lock, so a run that waited behind another cannot restore older links. This live reading is the default since 2026-09-27; `--frozen` gives the earlier snapshot-only behavior.

```bash
python3 setup/claude-codex-mirror/mirror_files.py
python3 setup/claude-codex-mirror/mirror_files.py --apply
```

A scheduled apply keeps the links current after Claude plugin updates. `--quiet` prints one JSON line with the counts and changed targets:

```cron
47 * * * *  /usr/bin/python3 /home/dev/projects/ai-harness/setup/claude-codex-mirror/mirror_files.py --apply --quiet >> /home/dev/.codex/mirrors/claude/sync.log 2>&1
```

The apply mode writes `~/.codex/claude-mirror-owned.json`. It also saves the exact input metadata at `~/.codex/mirrors/claude/inventory.json`. For a source refresh, create a new read-only Claude/Codex inventory and pass its path with `--inventory`. Normal verification uses the durable saved inventory and does not depend on temporary files. Later runs may replace only unchanged paths in the ownership file. Manually edited owned paths and all unmanaged paths are preserved. It backs up an unchanged owned path before replacement under `~/.codex/backups/claude-mirror/`. Apply uses a single-writer lock, rechecks current targets, and records a pending operation before atomically replacing a file or symlink. A later run can recover ownership when the installed target still matches that recorded operation. Invalid path segments and target-parent escapes are rejected. These checks do not coordinate unrelated editors or promise a transaction across the whole machine. Symlink sources remain canonical, and the tool does not copy `.env`, credential, transcript, or session files.

Plugin skills are discovered from the enabled plugins active cache path, so omitted inventory names such as `frontend-design` are still included. Orphaned plugin versions are never selected.

The native detector found the Claude `Stop` hook on this host. Do not import it. It consumes Claude's transcript JSON and is incompatible with Codex hook input. Do not use native `CONFIG`, `AGENTS_MD`, `SKILLS`, or `PLUGINS` import for this mirror because those broad operations can overwrite policy, duplicate the working agreement, expose runtime files, or replace adapted ports. Use the reviewed file plan and a separate targeted config merge instead.

## Installed configuration and compatibility

`settings.py` plans the compatible settings and `settings.py --apply` installs them.
It writes a marked block in `~/.codex/config.toml`, backs up the original before
changes, refuses to overwrite an edited managed block or proceed without its ownership report/hash, and preserves the existing
model, sandbox, approval policy and project configuration. It creates a global
`~/.codex/AGENTS.md` pointer only when none exists. Project `CLAUDE.md` files remain
the maintained instructions; the pointer tells Codex to read them in the relevant
repository. Settings applies also use a single-writer lock with a 30-second wait limit and recheck the current configuration before committing changes. Missing or unreadable Claude source settings stop with a clear error so refresh cannot silently erase the source preferences. Claude shell allowlists and UI/model settings do not silently become
global Codex permissions. There were no source project deny rules to migrate.

The source's 14 disabled skills remain disabled; Codex matches its disabled-skill
paths through the symlinks. Existing Codex `delegate`, `site-flow` and system skills
are retained. The former identical Cloudflare skill copies are now symlinks to
`~/.claude/skills`.
Enabled plugin skills link to the currently installed Claude version, including
the local slim Superpowers preamble. Disabled code-review, GitHub and Telegram
plugins remain excluded. No custom source subagent definitions were present.

On this host the official `notion@openai-curated-remote` plugin is installed with
`codex plugin add notion@openai-curated-remote`. It owns the Notion MCP definition;
the settings mirror avoids adding a second copy while that plugin cache exists.
The other registered services are Authority Hacker, Context7, Gmail, Google
Calendar, Google Drive, Playwright, Slack and Supabase. Configuration is not
authentication: a fresh native connector can still require OAuth even when the
Claude account is connected. Slack already needed authentication in Claude.

The `claude-connectors/` skill is installed in `~/.codex/skills/claude-connectors/`.
Its helper reuses the existing Claude client for one authorized connector task,
with selected tools, denied permission prompts, and no built-in shell/filesystem
tools. It has a verified Notion search path. It does not transfer OAuth caches.
Haiku handles known reads; Sonnet handles explicitly authorized writes.

## Verification and rollback

Run the focused checks from the repository root:

```sh
python3 -m pytest tests/claude_codex_mirror setup/claude-codex-mirror/test_settings.py setup/claude-codex-mirror/claude-connectors/tests -q
python3 setup/claude-codex-mirror/mirror_files.py
python3 setup/claude-codex-mirror/settings.py
```

After installation, both plans should report no changes. Validate actual skill
discovery with the installed Codex app-server `skills/list` API for the home
workspace and affected repositories; a filesystem copy alone is not proof of
discovery. The detector drains stdout and stderr together, handles partial or grouped responses, and terminates its child process on success, error, or timeout. Restart or start a fresh Codex session to load new configuration.

The settings backup path is recorded in `~/.codex/mirrors/claude/settings-report.json`.
Restore its `config.toml` only after checking for subsequent user edits. The
pre-Notion-plugin configuration is also saved under
`~/.codex/mirrors/claude/backups/pre-notion-plugin/`. To undo the file mirror,
remove only entries listed in `~/.codex/claude-mirror-owned.json` whose symlink
target or generated-file hash still matches the recorded value; preserve any
subsequent edits. The source Claude files are not rollback targets.

## Source documentation

- [Codex skills and supported symlinks](https://learn.chatgpt.com/docs/build-skills)
- [Codex MCP configuration and authentication](https://learn.chatgpt.com/docs/extend/mcp)
- [Native Claude import and compatibility limits](https://learn.chatgpt.com/docs/import)
