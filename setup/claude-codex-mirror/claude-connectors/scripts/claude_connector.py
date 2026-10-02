#!/usr/bin/env python3
"""Narrow Claude CLI connector adapter for Codex fallback skills."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import subprocess
import sys


CONNECTORS = {
    "notion": "Notion",
    "gmail": "Gmail",
    "calendar": "Google_Calendar",
    "drive": "Google_Drive",
    "supabase": "Supabase",
    "slack": "Slack",
    "authority-hacker": "Authority_Hacker",
}

# Verified against the connected Claude tool catalog, 2026-09-06.
READ_ONLY_TOOLS = {
    'notion': frozenset(['notion-search', 'notion-fetch', 'notion-get-comments', 'notion-get-teams', 'notion-get-users', 'notion-list-favorite-pages', 'notion-list-private-pages', 'notion-list-recent-pages', 'notion-list-shared-pages', 'notion-query-data-sources', 'notion-query-multiple-data-sources']),
    'gmail': frozenset(['get_draft', 'get_message', 'get_thread', 'list_drafts', 'list_labels', 'search_threads']),
    'calendar': frozenset(['get_event', 'list_calendars', 'list_events', 'search_events', 'suggest_time']),
    'drive': frozenset(['download_file_content', 'get_file_metadata', 'get_file_permissions', 'list_recent_files', 'read_file_content', 'search_files']),
    'supabase': frozenset(['generate_typescript_types', 'get_advisors', 'get_cost', 'get_edge_function', 'get_organization', 'get_project', 'get_project_url', 'list_branches', 'list_edge_functions', 'list_extensions', 'list_migrations', 'list_organizations', 'list_projects', 'list_tables', 'query_logs', 'search_docs']),
    'slack': frozenset([]),
    'authority-hacker': frozenset(['list_ah_sources', 'search_ah_knowledge']),
}
DEFAULT_READ_TOOLS = {"notion": ("notion-search",)}

TOOL_SUFFIX = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+)*\Z")


def qualified_tool(connector: str, tool: str) -> str:
    """Accept one exact, unambiguous MCP tool name for the connector."""
    prefix = f"mcp__claude_ai_{CONNECTORS[connector]}__"
    suffix = tool[len(prefix):] if tool.startswith(prefix) else tool
    if tool.startswith("mcp__") and not tool.startswith(prefix):
        raise ValueError(f"tool must be in the {connector} Claude MCP namespace")
    if not TOOL_SUFFIX.fullmatch(suffix):
        raise ValueError("tool suffix must contain only lowercase letters, digits, hyphens, and underscores")
    return prefix + suffix


def requires_write(connector: str, tool: str) -> bool:
    """Unknown tools are write-capable until explicitly reviewed and added here."""
    return tool.rsplit("__", 1)[-1] not in READ_ONLY_TOOLS[connector]


def build_command(model: str, tools: list[str]) -> list[str]:
    """Keep the Claude process limited to exactly the selected connector tools."""
    return [
        "claude",
        "-p",
        "--model",
        model,
        "--permission-mode",
        "manual",
        "--permission-prompts",
        "none",
        "--tools",
        "",
        "--allowedTools",
        ",".join(tools),
        "--effort",
        "medium",
        "--no-session-persistence",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connector", choices=sorted(CONNECTORS), required=True)
    parser.add_argument("--tool", action="append", default=[])
    parser.add_argument("--prompt-file", type=Path, help="Read the task prompt from a file; use - for stdin.")
    parser.add_argument("--model", choices=("haiku", "sonnet"), default="haiku")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args(argv)

    requested = args.tool or list(DEFAULT_READ_TOOLS.get(args.connector, ()))
    if not requested:
        parser.error("--tool is required for this connector")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    if args.prompt_file is None:
        prompt = sys.stdin.read()
    elif str(args.prompt_file) == "-":
        prompt = sys.stdin.read()
    else:
        try:
            prompt = args.prompt_file.read_text(encoding="utf-8")
        except OSError as error:
            parser.error(f"cannot read --prompt-file: {error}")
    if not prompt:
        parser.error("provide a non-empty prompt through stdin or --prompt-file")
    try:
        tools = [qualified_tool(args.connector, tool) for tool in requested]
    except ValueError as error:
        parser.error(str(error))

    writes = [tool for tool in tools if requires_write(args.connector, tool)]
    if writes and not args.write:
        parser.error("unverified or write-capable tool requires --write and explicit user authorization")
    if args.write and args.model != "sonnet":
        parser.error("authorized writes require --model sonnet")

    try:
        result = subprocess.run(
            build_command(args.model, tools),
            input=prompt,
            text=True,
            stdout=sys.stdout,
            stderr=sys.stderr,
            check=False,
            timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"Claude connector timed out after {args.timeout} seconds.", file=sys.stderr)
        return 124
    except OSError as error:
        print(f"Cannot start Claude CLI: {error}. Check that claude is installed and on PATH.", file=sys.stderr)
        return 127
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
