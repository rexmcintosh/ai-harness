import sys

import pytest

from loom.gate import find_hook, scan_clean

# Tests that exercise a real scan need the hook; fail-closed tests always run.
requires_hook = pytest.mark.skipif(
    find_hook() is None,
    reason="detect-secrets-hook not reachable from this interpreter (not beside "
    f"{sys.executable} and not on PATH) — install detect-secrets or run the "
    "suite with .venv/bin/python -m pytest",
)


@requires_hook
def test_clean_text_passes(tmp_path):
    f = tmp_path / "c.txt"
    f.write_text("the quick brown fox jumped over the lazy dog")
    assert scan_clean(f) is True


@requires_hook
def test_aws_key_is_caught(tmp_path):
    f = tmp_path / "d.txt"
    f.write_text("aws_secret = 'AKIAIOSFODNN7EXAMPLE'\nkey='wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY'\n")
    assert scan_clean(f) is False


def test_nonexistent_file_is_refused(tmp_path):
    assert scan_clean(tmp_path / "does_not_exist.txt") is False


def test_directory_is_refused(tmp_path):
    d = tmp_path / "adir"
    d.mkdir()
    assert scan_clean(d) is False


@requires_hook
def test_benign_high_entropy_ids_pass(tmp_path):
    f = tmp_path / "ids.txt"
    f.write_text(
        "Gmail thread 19e3fcd6543b7ddf\n"
        "Drive doc 1lDZfHGHj_N8dWPg3l3HZ4Si_gs_PZDdYPBgqXfCTA7s\n"
        "just some ordinary notes about the project\n"
    )
    assert scan_clean(f) is True


@requires_hook
def test_hook_found_on_path_when_not_beside_interpreter(tmp_path, monkeypatch):
    # A real hook installed on PATH must be honoured even when the running
    # interpreter's bin dir has no detect-secrets-hook (e.g. system python).
    hook_dir = str(find_hook().parent)
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv("PATH", hook_dir)
    f = tmp_path / "c.txt"
    f.write_text("the quick brown fox jumped over the lazy dog")
    assert scan_clean(f) is True


def test_no_hook_anywhere_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "executable", "/usr/bin/python3")
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    f = tmp_path / "c.txt"
    f.write_text("the quick brown fox jumped over the lazy dog")
    assert scan_clean(f) is False
