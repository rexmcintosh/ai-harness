---
name: claude-connectors
description: Use the existing Claude CLI connector account when a requested Notion, Google, Slack, Supabase, or Authority Hacker connector is unavailable in Codex.
---

# Claude connector fallback

Use this fallback only when the native Codex connector is unavailable in the current session. It invokes the existing Claude CLI account on this machine. It does not make the connector native to Codex, copy OAuth state, migrate credentials, or copy a Claude session transcript.

Read `~/.claude/CLAUDE.md` before an external action. Its protocol and the user's explicit task authorization govern the action.

## Routing

- Notion: search pages, inspect a known page, create or update authorized dashboards.
- Gmail: find messages and draft or send only when the user authorizes it.
- Google Calendar: inspect availability and create or change events only when authorized.
- Google Drive: locate and read files; create, share, or edit only when authorized.
- Supabase: inspect connected project information, then make authorized management changes.
- Slack: search or summarize connected workspace content; send or change content only when authorized.
- Authority Hacker: retrieve connected research or SEO data for the requested analysis.

The native Codex marketplace plugin remains preferred. Its OAuth connection is separate from Claude's account connection.

## Run the adapter

Use `scripts/claude_connector.py`. Supply the connector, the exact connector tool names, and one task prompt. Tool names are always passed through Claude's exact allowlist.

Reads are the default. The adapter rejects known write tools unless `--write` is present. `--write` does not create authorization. Use it only after the user authorizes the stated external change.

Use `--model haiku` for lookup and discovery. Use `--model sonnet --write` for an authorized creation or update. The adapter removes all built-in tools. Other MCP tools can remain visible, but manual permission mode with denied prompts prevents their execution unless they are in the exact selected allowlist. It uses `--no-session-persistence`, `--effort medium`, and a 120-second timeout. It never enables dangerous permission bypass.

For Notion, `notion-search` is the default read tool. `notion-create-pages` supports an optional `parent`; without it, the page is created in the private workspace. When no suitable existing Backlog or Operations parent page is found, create the authorized dashboard as a workspace-level private page unless the user names another parent.

Examples:

```sh
printf %s 'Find a page titled Backlog. Return titles and URLs only.' | python3 scripts/claude_connector.py --connector notion --tool notion-search
python3 scripts/claude_connector.py --connector notion --tool notion-create-pages --write --model sonnet --prompt-file dashboard-prompt.md
```

If the helper says Claude no longer exposes the requested connector tool, stop. Do not add a token, initiate OAuth, or substitute a different account without explicit authorization.
