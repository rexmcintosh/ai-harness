import pathlib
import sys
import unittest


SCRIPT_DIR = pathlib.Path(__file__).parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import claude_connector as adapter  # noqa: E402


class ClaudeConnectorTests(unittest.TestCase):
    def test_notion_tool_is_qualified(self):
        self.assertEqual(
            adapter.qualified_tool("notion", "notion-search"),
            "mcp__claude_ai_Notion__notion-search",
        )

    def test_cross_connector_tool_is_rejected(self):
        with self.assertRaises(ValueError):
            adapter.qualified_tool("notion", "mcp__claude_ai_Gmail__gmail-search")

    def test_command_disables_builtin_tools_and_allows_only_selection(self):
        command = adapter.build_command("haiku", ["mcp__claude_ai_Notion__notion-search"])
        self.assertIn("--tools", command)
        self.assertEqual(command[command.index("--tools") + 1], "")
        self.assertIn("--no-session-persistence", command)
        self.assertEqual(command[command.index("--permission-mode") + 1], "manual")
        self.assertEqual(command[command.index("--permission-prompts") + 1], "none")
        self.assertEqual(command[command.index("--effort") + 1], "medium")
        self.assertNotIn("--dangerously-skip-permissions", command)

    def test_verified_cross_connector_reads_use_small_model_path(self):
        for connector,tool in [("notion","notion-fetch"),("gmail","get_message"),("calendar","list_events"),("drive","search_files"),("supabase","list_projects"),("authority-hacker","search_ah_knowledge")]:
            self.assertFalse(adapter.requires_write(connector,adapter.qualified_tool(connector,tool)))
        self.assertEqual(adapter.DEFAULT_READ_TOOLS["notion"],("notion-search",))

    def test_unknown_tool_requires_write_even_if_its_name_has_no_write_marker(self):
        self.assertTrue(adapter.requires_write("supabase", "mcp__claude_ai_Supabase__execute_sql"))
        self.assertEqual(adapter.qualified_tool("supabase", "execute_sql"), "mcp__claude_ai_Supabase__execute_sql")
        self.assertFalse(adapter.requires_write("notion", "mcp__claude_ai_Notion__notion-search"))

    def test_missing_claude_has_stable_exit_without_traceback(self):
        import io
        from unittest.mock import patch
        error=io.StringIO()
        with patch.object(adapter.sys,'stdin',io.StringIO('Read-only lookup')), patch.object(adapter.sys,'stderr',error), patch.object(adapter.subprocess,'run',side_effect=FileNotFoundError('missing claude')):
            self.assertEqual(adapter.main(['--connector','notion']),127)
        self.assertIn('Check that claude is installed',error.getvalue())
        self.assertNotIn('Traceback',error.getvalue())

    def test_tool_suffix_rejects_allowlist_escapes(self):
        for invalid in ("notion-search,*", "notion-search extra", "notion-search()"):
            with self.assertRaises(ValueError):
                adapter.qualified_tool("notion", invalid)


if __name__ == "__main__":
    unittest.main()
