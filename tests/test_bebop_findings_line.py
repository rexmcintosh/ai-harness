"""The code-review findings line in the morning briefing.

The diem drain reviews every repo's main each night and writes a record to
~/.local/state/diem/findings.json when the council asks for changes.
`bebop/findings_line.py` turns the count of unacknowledged ones into one line, in code.
Same two rules as the backlog line: silent when there is nothing new, and fail open.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

from bebop.findings_line import briefing_line, findings_path, line_from_file

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "bebop" / "findings_line.py"


def rec(i, status="new"):
    return {"id": f"{i:032x}", "repo": "swimtrack", "reviewed": "a..b",
            "date": "2026-09-27", "recommendation": "Request changes.",
            "output_path": "/x.md", "status": status}


def test_counts_only_new_findings():
    assert briefing_line([rec(1), rec(2), rec(3, "acked")]) == \
        "2 new code-review findings on main. Look: diem findings"


def test_one_finding_is_singular():
    assert briefing_line([rec(1)]) == "1 new code-review finding on main. Look: diem findings"


def test_silent_when_nothing_is_new():
    assert briefing_line([]) == ""
    assert briefing_line([rec(1, "acked")]) == ""


def test_odd_shapes_are_skipped_not_raised():
    assert briefing_line({"items": []}) == ""
    assert briefing_line(None) == ""
    assert briefing_line(["x", 3, {"status": "new"}, rec(1)]) == \
        "1 new code-review finding on main. Look: diem findings"


def test_file_missing_broken_or_fine(tmp_path):
    assert line_from_file(tmp_path / "nope.json") == ""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert line_from_file(bad) == ""
    good = tmp_path / "findings.json"
    good.write_text(json.dumps([rec(1), rec(2)]))
    assert line_from_file(good).startswith("2 new code-review findings")


def test_env_overrides_the_default_path(tmp_path, monkeypatch):
    monkeypatch.setenv("BEBOP_FINDINGS_FILE", str(tmp_path / "f.json"))
    assert findings_path() == tmp_path / "f.json"
    monkeypatch.delenv("BEBOP_FINDINGS_FILE")
    assert findings_path() == Path.home() / ".local" / "state" / "diem" / "findings.json"


def _helper(env_file):
    env = {**os.environ, "BEBOP_FINDINGS_FILE": str(env_file)}
    return subprocess.run([sys.executable, str(HELPER)], env=env,
                          capture_output=True, text=True, timeout=30)


def test_the_helper_prints_the_line_and_always_exits_zero(tmp_path):
    f = tmp_path / "findings.json"
    f.write_text(json.dumps([rec(1)]))
    p = _helper(f)
    assert p.returncode == 0 and p.stdout == \
        "1 new code-review finding on main. Look: diem findings"
    f.write_text("[")
    p = _helper(f)
    assert p.returncode == 0 and p.stdout == ""
    p = _helper(tmp_path / "missing.json")
    assert p.returncode == 0 and p.stdout == ""
