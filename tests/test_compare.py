"""Tests for `council compare` — rank N candidate solutions, pick a winner."""
import json

from council.compare import run_compare
from council.models import Panel, Member
from tests.conftest import FakeClient

PANEL = Panel("code-review", "review code", [
    Member("Eng", "m1", "be an eng"),
    Member("Sec", "m2", "be a sec officer"),
])

CANDIDATES = [("A", "def f(): return 1"), ("B", "def f(): return 2")]


def _vote(pick, ranking, rationale="because"):
    return json.dumps({"pick": pick, "ranking": ranking, "rationale": rationale})


def _synth(winner, ranking, grafts=(), conf=8):
    return json.dumps({"winner": winner, "confidence": conf, "ranking": ranking,
                       "rationale": "B is correct", "grafts": list(grafts)})


def test_compare_returns_chair_winner():
    client = FakeClient(by_model={
        "m1": _vote("B", ["B", "A"]),
        "m2": _vote("B", ["B", "A"]),
        "c": _synth("B", ["B", "A"], grafts=["take the docstring from A"]),
    })
    res = run_compare("make f return 2", CANDIDATES, PANEL, client, chair_model="c")
    assert res.winner == "B"
    assert res.ranking == ["B", "A"]
    assert res.grafts == ["take the docstring from A"]
    assert res.confidence == 8


def test_compare_sends_all_candidates_to_each_member():
    client = FakeClient(by_model={"m1": _vote("A", ["A", "B"]),
                                  "m2": _vote("A", ["A", "B"]),
                                  "c": _synth("A", ["A", "B"])})
    run_compare("task", CANDIDATES, PANEL, client, chair_model="c")
    member_calls = [c for c in client.calls if c["model"] in ("m1", "m2")]
    assert len(member_calls) == 2
    for call in member_calls:
        assert "def f(): return 1" in call["user"]  # candidate A present
        assert "def f(): return 2" in call["user"]  # candidate B present


def test_compare_votes_recorded_in_panel_order():
    client = FakeClient(by_model={"m1": _vote("A", ["A", "B"]),
                                  "m2": _vote("B", ["B", "A"]),
                                  "c": _synth("A", ["A", "B"])})
    res = run_compare("task", CANDIDATES, PANEL, client, chair_model="c")
    assert [v.member for v in res.votes] == ["Eng", "Sec"]
    assert res.votes[0].pick == "A"
    assert res.votes[1].pick == "B"


def test_compare_tolerates_member_error():
    client = FakeClient(by_model={"m2": _vote("B", ["B", "A"]),
                                  "c": _synth("B", ["B", "A"])},
                        raises_for={"m1"})
    res = run_compare("task", CANDIDATES, PANEL, client, chair_model="c")
    assert res.winner == "B"                  # chair still runs on surviving votes
    errored = [v for v in res.votes if v.error]
    assert len(errored) == 1 and errored[0].member == "Eng"


def test_compare_counts_a_junk_but_valid_vote_as_an_errored_voter():
    # The garbled forced-JSON reply seen from deepseek-v4-pro: it parses, and it picks nothing.
    client = FakeClient(by_model={"m1": '{": ": ", "}', "m2": _vote("B", ["B", "A"]),
                                  "c": _synth("B", ["B", "A"])})
    res = run_compare("task", CANDIDATES, PANEL, client, chair_model="c")
    errored = [v for v in res.votes if v.error]
    assert [v.member for v in errored] == ["Eng"] and "no usable answer" in errored[0].error


def test_compare_chair_error_is_surfaced_not_raised():
    client = FakeClient(by_model={"m1": _vote("A", ["A", "B"]),
                                  "m2": _vote("A", ["A", "B"])},
                        raises_for={"c"})
    res = run_compare("task", CANDIDATES, PANEL, client, chair_model="c")
    assert res.error is not None
    assert res.winner == ""                    # no false winner on chair failure


def test_compare_requires_at_least_two_candidates():
    import pytest
    with pytest.raises(ValueError):
        run_compare("task", [("A", "only one")], PANEL, FakeClient(), chair_model="c")


# --- render -----------------------------------------------------------------

def test_render_comparison_shows_winner_grafts_and_votes():
    from council.render import render_comparison
    from council.models import ComparisonResult, CandidateVote
    res = ComparisonResult(
        winner="B", rationale="B is correct", ranking=["B", "A"],
        grafts=["take the docstring from A"], confidence=8,
        votes=[CandidateVote("Eng", "m1", pick="B", ranking=["B", "A"], rationale="cleaner")])
    out = render_comparison("make f return 2", res)
    assert "B" in out
    assert "B is correct" in out
    assert "take the docstring from A" in out
    assert "Eng" in out and "cleaner" in out


def test_render_comparison_surfaces_chair_error():
    from council.render import render_comparison
    from council.models import ComparisonResult
    out = render_comparison("t", ComparisonResult(winner="", error="VeniceError: boom"))
    assert "boom" in out


# --- CLI --------------------------------------------------------------------

def _cli_env():
    from council.config import Settings
    settings = Settings(chair_model="c")
    panels = {"code-review": Panel("code-review", "review", [Member("Eng", "m1", "eng")])}
    client = FakeClient(by_model={"m1": _vote("B", ["B", "A"]),
                                  "c": _synth("B", ["B", "A"], grafts=["graft from A"])})
    return settings, panels, client


def test_cli_compare_picks_winner(tmp_path, capsys):
    from council import cli
    settings, panels, client = _cli_env()
    a = tmp_path / "a.py"; a.write_text("def f(): return 1\n")
    b = tmp_path / "b.py"; b.write_text("def f(): return 2\n")
    rc = cli.main(["compare", "--task", "make f return 2", str(a), str(b)],
                  _settings=settings, _panels=panels, _client=client)
    assert rc == 0
    out = capsys.readouterr().out
    assert "Winner" in out and "graft from A" in out


def test_cli_compare_requires_two_files(tmp_path, capsys):
    from council import cli
    settings, panels, client = _cli_env()
    a = tmp_path / "a.py"; a.write_text("x\n")
    rc = cli.main(["compare", "--task", "t", str(a)],
                  _settings=settings, _panels=panels, _client=client)
    assert rc == 2
    assert "at least two" in capsys.readouterr().err
