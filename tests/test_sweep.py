"""Tests for `council sweep` — repo-wide security pass: chunk, fan out, dedup, gate."""
import json

from council.sweep import chunk_repo, dedup_findings, run_sweep
from council.models import Panel, Member, Finding
from tests.conftest import FakeClient

PANEL = Panel("red-team", "break it", [
    Member("Adversary", "m1", "attacker"),
    Member("Sec", "m2", "cso"),
])


# --- chunk_repo -------------------------------------------------------------

def test_chunk_repo_skips_dotfiles_and_binary(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "b.py").write_text("print(2)\n")
    (tmp_path / ".env").write_text("VENICE_API_KEY=secret\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00bin\x00")
    chunks, dropped = chunk_repo(str(tmp_path), cap=100_000, max_chunks=50)
    labels = [lbl for lbl, _ in chunks]
    bodies = "\n".join(t for _, t in chunks)
    assert any("a.py" in l for l in labels) and any("b.py" in l for l in labels)
    assert "secret" not in bodies      # dotfile excluded
    assert not any("logo.png" in l for l in labels)  # binary excluded
    assert dropped == 0


def test_chunk_repo_respects_max_chunks_and_reports_dropped(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"print({i})\n")
    chunks, dropped = chunk_repo(str(tmp_path), cap=100_000, max_chunks=3)
    assert len(chunks) == 3
    assert dropped == 2   # nothing silently dropped — the count is surfaced


# --- dedup_findings ---------------------------------------------------------

def test_dedup_merges_same_point_across_chunks():
    f = Finding("SQL injection in query builder", "high", 9)
    tagged = [("a.py", "Adversary", f),
              ("b.py", "Sec", Finding("sql injection in query builder", "high", 8))]
    merged = dedup_findings(tagged)
    assert len(merged) == 1
    assert set(merged[0].locations) == {"a.py", "b.py"}
    assert merged[0].confidence == 9          # keeps the highest confidence
    assert set(merged[0].sources) == {"Adversary", "Sec"}


def test_dedup_keeps_distinct_findings_separate():
    tagged = [("a.py", "Adversary", Finding("SQLi", "high", 9)),
              ("a.py", "Sec", Finding("XSS", "high", 9))]
    assert len(dedup_findings(tagged)) == 2


# --- run_sweep --------------------------------------------------------------

def _member(findings):
    return json.dumps({"stance": "oppose", "headline": "issues",
                       "findings": [{"point": p, "severity": s, "confidence": c}
                                    for p, s, c in findings],
                       "suggestions": []})


def _summary(text="2 real risks"):
    return json.dumps({"summary": text})


def test_run_sweep_aggregates_across_chunks():
    chunks = [("a.py", "code a"), ("b.py", "code b")]
    client = FakeClient(by_model={
        "m1": _member([("SQLi in a", "high", 9)]),
        "m2": _member([("hardcoded secret in b", "critical", 9)]),
        "c": _summary(),
    })
    rep = run_sweep(chunks, PANEL, client, chair_model="c")
    assert rep.chunks_scanned == 2
    points = {f.point for f in rep.findings}
    assert "SQLi in a" in points and "hardcoded secret in b" in points
    assert rep.summary == "2 real risks"


def test_run_sweep_gates_low_confidence_but_keeps_critical():
    chunks = [("a.py", "x")]
    client = FakeClient(by_model={
        "m1": _member([("weak finding", "med", 3), ("real crit", "critical", 2)]),
        "m2": _member([]),
        "c": _summary("1 risk"),
    })
    rep = run_sweep(chunks, PANEL, client, chair_model="c", min_conf=7)
    points = {f.point for f in rep.findings}
    assert "weak finding" not in points     # med + low confidence -> dropped
    assert "real crit" in points            # critical always kept


def test_run_sweep_sorts_critical_first():
    chunks = [("a.py", "x")]
    client = FakeClient(by_model={
        "m1": _member([("a high", "high", 9), ("a crit", "critical", 9)]),
        "m2": _member([]),
        "c": _summary(),
    })
    rep = run_sweep(chunks, PANEL, client, chair_model="c")
    assert rep.findings[0].severity == "critical"


def test_run_sweep_chair_error_surfaced_findings_still_present():
    chunks = [("a.py", "x")]
    client = FakeClient(by_model={"m1": _member([("SQLi", "high", 9)]), "m2": _member([])},
                        raises_for={"c"})
    rep = run_sweep(chunks, PANEL, client, chair_model="c")
    assert rep.error is not None
    assert any(f.point == "SQLi" for f in rep.findings)  # findings survive a summary failure


# --- render -----------------------------------------------------------------

def test_render_sweep_shows_summary_findings_and_coverage():
    from council.render import render_sweep
    from council.models import SweepReport, SweepFinding
    rep = SweepReport(
        findings=[SweepFinding("SQLi in query", "high", 9, ["a.py"], ["Adversary"])],
        chunks_scanned=4, dropped=2, summary="One real SQLi to fix first")
    out = render_sweep("/repo", rep)
    assert "One real SQLi to fix first" in out
    assert "SQLi in query" in out and "a.py" in out
    assert "4" in out and "2" in out   # coverage: scanned 4, dropped 2 surfaced


# --- CLI --------------------------------------------------------------------

def test_cli_sweep_runs_and_prints(tmp_path, capsys):
    from council import cli
    from council.config import Settings
    (tmp_path / "app.py").write_text("query = 'SELECT ' + user_input\n")
    settings = Settings(chair_model="c")
    panels = {"red-team": PANEL}
    client = FakeClient(by_model={"m1": _member([("SQLi via string concat", "high", 9)]),
                                  "m2": _member([]), "c": _summary("1 risk: SQLi")})
    rc = cli.main(["sweep", str(tmp_path)], _settings=settings, _panels=panels, _client=client)
    assert rc == 0
    out = capsys.readouterr().out
    assert "SQLi" in out and "1 risk" in out


def test_cli_sweep_uses_the_panels_own_chair(tmp_path, capsys):
    from council import cli
    from council.config import Settings
    (tmp_path / "app.py").write_text("query = 'SELECT ' + user_input\n")
    panel = Panel("red-team", "break it", [Member("Adversary", "m1", "attacker")], chair_model="pc")
    client = FakeClient(by_model={"m1": _member([("SQLi via string concat", "high", 9)]),
                                  "c": _summary("global chair"), "pc": _summary("panel chair")})
    rc = cli.main(["sweep", str(tmp_path)], _settings=Settings(chair_model="c"),
                  _panels={"red-team": panel}, _client=client)
    assert rc == 0
    assert [c["model"] for c in client.calls] == ["m1", "pc"]    # never the global chair "c"
    assert "panel chair" in capsys.readouterr().out


def test_cli_sweep_empty_target_is_clean_noop(tmp_path, capsys):
    from council import cli
    from council.config import Settings
    rc = cli.main(["sweep", str(tmp_path)], _settings=Settings(chair_model="c"),
                  _panels={"red-team": PANEL}, _client=FakeClient())
    assert rc == 0
    assert "nothing to scan" in capsys.readouterr().out.lower()
