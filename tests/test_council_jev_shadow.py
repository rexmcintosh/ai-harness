"""Jev shadow signals for the council: display and log only.

Two promises are tested here above all. (1) Nothing Jev returns changes the chair's input,
the gate count, `review_status`, readiness, `approve` or an exit code. (2) No failure in the
Jev step breaks the review it rides on. No test may reach TypeSafe: every call goes through
an injected `ask` or a fake transport.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from council import jev


@pytest.fixture(autouse=True)
def real_connections(monkeypatch):
    """The last line of defence, below the conftest tripwire: the HTTP opener itself. The
    shadow code swallows exceptions by design, so the guard records and fails at teardown."""
    opened = []

    def refuse(request, *args, **kwargs):
        opened.append(getattr(request, "full_url", str(request)))
        raise AssertionError("a test opened a real connection to TypeSafe")
    monkeypatch.setattr(jev._OPENER, "open", refuse)
    yield opened
    assert not opened, f"a test opened a real connection: {opened}"


def _reply(answers, model=jev.MODEL):
    return {"model": model, "answers": answers, "usage": {"input_tokens": 10}}


# ── the client ───────────────────────────────────────────────────────────────────────────
def test_the_guard_itself_catches_a_call_that_reaches_the_opener(real_connections):
    with pytest.raises(AssertionError, match="real connection"):
        jev._OPENER.open("https://api.typesafe.ai/v1/systemone")
    assert real_connections == ["https://api.typesafe.ai/v1/systemone"]
    real_connections.clear()                                   # this one reach was the test


@pytest.mark.parametrize("value", ["0", "off", "false", "OFF", " False "])
def test_kill_switch_turns_the_shadow_off_even_with_a_key(value):
    assert jev.shadow_enabled({"COUNCIL_JEV": value, "TYPESAFE_API_KEY": "k"}) is False


def test_no_key_means_off_and_a_key_means_on_by_default(tmp_path):
    assert jev.shadow_enabled({"HOME": str(tmp_path)}) is False
    assert jev.shadow_enabled({"HOME": str(tmp_path), "TYPESAFE_API_KEY": "k"}) is True
    (tmp_path / ".env").write_text("OTHER=1\nTYPESAFE_API_KEY='from-file'\n")
    assert jev.load_key(environ={"HOME": str(tmp_path)}) == "from-file"
    assert jev.shadow_enabled({"HOME": str(tmp_path)}) is True
    assert jev.shadow_enabled({"HOME": str(tmp_path), "COUNCIL_JEV": "1"}) is True


def test_ask_pins_the_model_and_redacts_the_state_and_the_questions():
    sent = {}

    def transport(req, key, timeout):
        sent.update(req=req, key=key, timeout=timeout)
        return _reply({"q": {"type": "noul", "noul": 0.9}})
    token = "A" * 48
    answers = jev.ask({"text": f"mail rex@example.com with {token} at https://x.test/p?sig=abc"},
                      {"q": {"type": "choice", "instructions": f"see {token}",
                             "criteria": {"F1.1": "ask dev@example.com", "none": "no"}}},
                      key="k", transport=transport)
    wire = json.dumps(sent["req"])
    assert sent["req"]["model"] == "jev-1.13.0" and sent["key"] == "k" and sent["timeout"] == 8
    assert "rex@example.com" not in wire and "dev@example.com" not in wire
    assert token not in wire and "sig=abc" not in wire
    assert set(sent["req"]["questions"]["q"]["criteria"]) == {"F1.1", "none"}     # option ids survive
    assert answers == {"q": {"type": "noul", "noul": 0.9}}


def test_council_text_is_redacted_whole_without_clipping_lines():
    long_line = "Approve. " + ("word " * 300) + "END"
    out = jev.redact_text(long_line + "\n@pytest.fixture")
    assert out.splitlines()[0].endswith("END") and "@pytest.fixture" in out


def test_ask_refuses_an_answer_from_another_model_version():
    with pytest.raises(jev.JevError, match="model"):
        jev.ask("s", {"q": {}}, key="k", transport=lambda *a: _reply({}, model="jev-2.0.0"))


def test_ask_turns_every_failure_into_a_jev_error_that_never_names_the_key():
    def down(req, key, timeout):
        raise OSError(f"connection refused for bearer {key}")
    with pytest.raises(jev.JevError) as err:
        jev.ask("s", {"q": {}}, key="sekret-key", transport=down)
    assert "sekret-key" not in str(err.value)
    with pytest.raises(jev.JevError, match="answers"):
        jev.ask("s", {"q": {}}, key="k", transport=lambda *a: {"model": jev.MODEL, "answers": "nope"})


def test_ask_without_a_transport_uses_the_module_transport_looked_up_at_call_time(monkeypatch):
    monkeypatch.setattr(jev, "_http_post", lambda req, key, timeout: _reply({"q": {"noul": 0.1}}))
    assert jev.ask("s", {"q": {}}, key="k") == {"q": {"noul": 0.1}}


def test_redirects_are_never_followed():
    # urllib re-sends the Authorization header on a redirect
    assert jev._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.test/") is None


def test_scope_rule_refuses_private_repos_manuscripts_and_unknown_repos():
    assert jev.in_scope("", "ai-harness") and jev.in_scope("2026-07-27-time-standards", "swimtrack-website")
    for name, repo in (("x", "sat-prep"), ("x", "romance-empire"), ("x", "tax-advisor"),
                       ("2026-09-02-sat-prep-vocab", "ai-harness"), ("x", "finance-tracker"),
                       ("x", None), ("x", "")):
        assert not jev.in_scope(name, repo)


def test_the_offline_harness_shares_the_one_scope_list_and_key_loader():
    from tools.jev_council import jev as offline, reviews
    assert reviews.OUT_OF_SCOPE is jev.OUT_OF_SCOPE and reviews.OUT_OF_SCOPE_REPOS is jev.OUT_OF_SCOPE_REPOS
    assert reviews.in_scope is jev.in_scope and offline.load_key is jev.load_key
    assert offline.MODEL == jev.MODEL


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                   env={"PATH": "/usr/bin:/bin", "HOME": str(cwd), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def test_repo_name_is_the_main_checkout_even_inside_a_linked_worktree(tmp_path):
    repo = tmp_path / "projects" / "my-repo"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")
    linked = tmp_path / "elsewhere" / "session-1234"
    _git(repo, "worktree", "add", "-q", str(linked), "-b", "side")
    assert jev.repo_name(repo) == "my-repo"
    assert jev.repo_name(repo / "pkg" / "a.py") == "my-repo"
    assert jev.repo_name(linked / "pkg") == "my-repo"            # not "session-1234"
    plain = tmp_path / "not-a-repo"
    plain.mkdir()
    assert jev.repo_name(plain) is None and jev.repo_name(tmp_path / "missing" / "x") is None


# ── the three signals ────────────────────────────────────────────────────────────────────
from council import signals                                                      # noqa: E402
from council.models import ConfirmedBlock, Finding, MemberResult, Synthesis      # noqa: E402

OVER_CAPTURE = "Review-note extraction can over-capture to the end of the prompt (backlogrun/cli.py:568-575)."
SPLITS = "review_notes (cli.py:569) splits on every marker inside a note body, so a note is cut short."
MISSING_TEST = "Tests miss the trailing-text case (tests/test_backlogrun.py:700-718)."
RECOMMENDATION = "Approve with follow-up fixes. Two robustness gaps should be addressed before merge."


def panel_results():
    """Seat 2 errored: it is skipped, and it still counts in the numbering (F3.1, not F2.1)."""
    return [
        MemberResult("Eng Manager", "m1", "concerns", "two edge paths",
                     findings=[Finding(OVER_CAPTURE, "high", 9), Finding(MISSING_TEST, "low", 9)]),
        MemberResult("Security Officer", "m2", "na", "(member errored)", error="UnusableReply: nothing"),
        MemberResult("Adversary", "m3", "concerns", "parser splits", findings=[Finding(SPLITS, "med", 8)]),
    ]


def synthesis(blocks=(), status="unknown"):
    return Synthesis(recommendation=RECOMMENDATION, confidence=7, review_status=status,
                     blocking_findings=[ConfirmedBlock(point=p, severity="high", why=w) for p, w in blocks])


class FakeAsk:
    """Answers like Jev, by question name. Records every request."""

    def __init__(self, *, verdict=("approve_with_conditions", 0.91), same=0.1, source=("none", 0.9)):
        self.verdict, self.same, self.source, self.calls = verdict, same, source, []

    def __call__(self, state, questions):
        self.calls.append((state, questions))
        if "verdict" in questions:
            label, conf = self.verdict
            return {"verdict": {"type": "choice", "choice": label, "confidence": conf,
                                "probabilities": {label: conf}}}
        if "same" in questions:
            p = self.same(state) if callable(self.same) else self.same
            return {"same": {"type": "noul", "noul": p}}
        choice, conf = self.source(state, questions) if callable(self.source) else self.source
        return {"source": {"type": "choice", "choice": choice, "confidence": conf, "probabilities": {}}}

    def named(self, question):
        return [(s, q) for s, q in self.calls if question in q]


def test_the_question_wording_lives_once_and_the_offline_harness_reuses_it():
    from tools.jev_council import experiments as ex
    from tools.jev_council.reviews import Review, SeatFinding
    assert ex.VERDICTS is signals.VERDICTS
    a, b = SeatFinding("F1.1", "A", "high", 9, OVER_CAPTURE), SeatFinding("F3.1", "B", "med", 8, SPLITS)
    assert ex.verdict_request(Review("rid", recommendation=RECOMMENDATION)).questions \
        == signals.verdict_request(RECOMMENDATION)[1]
    assert (ex.duplicate_request("rid", a, b).state, ex.duplicate_request("rid", a, b).questions) \
        == signals.duplicate_request(OVER_CAPTURE, SPLITS)
    block = {"point": "note extraction over-captures", "why": "slices to the end"}
    offline = ex.link_request("rid", block, [a, b])
    assert (offline.state, offline.questions) == signals.link_request(
        block["point"], block["why"], {"F1.1": OVER_CAPTURE, "F3.1": SPLITS})
    # the validated wording itself, pinned: this model reads literally, so a reworded
    # question is a different, untested question
    assert signals.verdict_request("x")[1]["verdict"]["instructions"] == (
        "This is the chair's written recommendation at the end of a code review. "
        "What is the chair's verdict on merging the change?")
    assert signals.duplicate_request("a", "b")[1]["same"]["instructions"] == (
        "Two reviewers wrote findings `a` and `b` about the same code change. "
        "Do they report the same underlying problem?")
    assert list(signals.link_request("p", "w", {"F1.1": "x"})[1]["source"]["criteria"]) == ["F1.1", "none"]


def test_finding_ids_follow_panel_order_and_an_errored_seat_keeps_its_number():
    found = signals.numbered_findings(panel_results())
    assert [(f.fid, f.seat, f.severity, f.confidence) for f in found] == [
        ("F1.1", "Eng Manager", "high", 9), ("F1.2", "Eng Manager", "low", 9), ("F3.1", "Adversary", "med", 8)]


def test_verdict_label_reads_the_recommendation_only():
    ask = FakeAsk()
    out = signals.verdict_label(RECOMMENDATION, ask)
    assert out == {"label": "approve_with_conditions", "confidence": 0.91,
                   "probabilities": {"approve_with_conditions": 0.91}}
    assert ask.calls[0][0] == {"recommendation": RECOMMENDATION}


@pytest.mark.parametrize("answer", [{}, {"verdict": {"choice": "ship_it", "confidence": 0.9}},
                                    {"verdict": {"choice": "approve", "confidence": "high"}},
                                    {"verdict": {"choice": "approve", "confidence": 7}}, {"verdict": None}])
def test_verdict_label_refuses_an_answer_of_the_wrong_shape(answer):
    with pytest.raises(Exception):
        signals.verdict_label(RECOMMENDATION, lambda s, q: answer)


def test_seat_agreement_asks_cross_seat_pairs_only_and_keeps_pairs_at_the_cut():
    ask = FakeAsk(same=lambda state: 0.94 if {state["a"], state["b"]} == {OVER_CAPTURE, SPLITS} else 0.2)
    stats = {}
    pairs = signals.seat_agreement(panel_results(), ask, stats=stats)
    assert pairs == [{"a": "F1.1", "b": "F3.1", "p": 0.94}]
    assert len(ask.calls) == 2                                   # F1.1~F3.1 and F1.2~F3.1, never F1.1~F1.2
    assert stats["pairs_total"] == 2 and stats["pairs_asked"] == 2 and stats["truncated"] is False
    assert {(s["a"], s["b"]): s["p"] for s in stats["scores"]} == {("F1.1", "F3.1"): 0.94, ("F1.2", "F3.1"): 0.2}
    assert signals.seat_agreement(panel_results(), FakeAsk(same=0.84)) == []          # under the 0.85 cut
    assert len(signals.seat_agreement(panel_results(), FakeAsk(same=0.84), cut=0.8)) == 2


def test_pair_cap_keeps_the_most_severe_pairs_and_says_it_truncated():
    severities = ["info", "low", "med", "high", "critical", "low", "info", "med", "high"]
    results = [MemberResult(name, "m", "concerns", "h",
                            findings=[Finding(f"{name} point {n}", sev, 8) for n, sev in enumerate(severities, 1)])
               for name in ("A", "B")]
    ask, stats = FakeAsk(same=0.1), {}
    signals.seat_agreement(results, ask, max_pairs=10, stats=stats)
    assert stats == {**stats, "pairs_total": 81, "pairs_asked": 10, "truncated": True}
    asked = [(s["a"], s["b"]) for s in stats["scores"]]
    assert asked[0] == ("F1.5", "F2.5")                          # critical with critical first
    rank = {"critical": 4, "high": 3, "med": 2, "low": 1, "info": 0}
    sums = [rank[severities[int(a.split(".")[1]) - 1]] + rank[severities[int(b.split(".")[1]) - 1]] for a, b in asked]
    assert sums == sorted(sums, reverse=True) and min(sums) >= 6


def test_a_failed_pair_is_counted_and_the_rest_still_run():
    def flaky(state, questions):
        if MISSING_TEST in (state["a"], state["b"]):
            raise jev.JevError("timed out")
        return {"same": {"type": "noul", "noul": 0.9}}
    stats = {}
    assert signals.seat_agreement(panel_results(), flaky, stats=stats) == [{"a": "F1.1", "b": "F3.1", "p": 0.9}]
    assert stats["errors"] == 1
    bad_shape = {}
    assert signals.seat_agreement(panel_results(), lambda s, q: {"same": {"noul": "yes"}}, stats=bad_shape) == []
    assert bad_shape["errors"] == 2


def test_clusters_merge_pairs_and_count_the_seats():
    found = signals.numbered_findings(panel_results() + [
        MemberResult("Designer", "m4", "concerns", "h", findings=[Finding("same again", "med", 8)])])
    pairs = [{"a": "F1.1", "b": "F3.1", "p": 0.94}, {"a": "F3.1", "b": "F4.1", "p": 0.88}]
    assert signals.clusters(pairs, found) == [
        {"findings": ["F1.1", "F3.1", "F4.1"], "seats": 3, "p_min": 0.88, "p_max": 0.94}]
    two = signals.clusters([{"a": "F1.1", "b": "F3.1", "p": 0.94}, {"a": "F1.2", "b": "F4.1", "p": 0.9}], found)
    assert [c["findings"] for c in two] == [["F1.1", "F3.1"], ["F1.2", "F4.1"]]
    assert signals.clusters([], found) == []


def test_block_sources_offer_every_panel_finding_and_none():
    blocks = [ConfirmedBlock("note extraction over-captures", "high", "slices to the end of the prompt"),
              ConfirmedBlock("the README is out of date", "med", "nobody raised this")]
    ask = FakeAsk(source=lambda state, q: ("F1.1", 0.99) if "over-captures" in state["block"]["point"] else ("none", 0.93))
    out = signals.block_sources(blocks, panel_results(), ask)
    assert out == [{"block": 0, "source": "F1.1", "confidence": 0.99},
                   {"block": 1, "source": "none", "confidence": 0.93}]
    options = ask.calls[0][1]["source"]["criteria"]
    assert list(options) == ["F1.1", "F1.2", "F3.1", "none"] and options["F3.1"].startswith("review_notes (cli.py:569)")
    long = [MemberResult("A", "m", "concerns", "h", findings=[Finding("x" * 900, "high", 9)])]
    signals.block_sources(blocks[:1], long, ask)
    assert len(ask.calls[-1][1]["source"]["criteria"]["F1.1"]) == 320


def test_block_sources_refuse_a_source_that_was_not_an_option_and_need_a_panel_finding():
    block = [ConfirmedBlock("p", "high", "w")]
    stats = {}
    assert signals.block_sources(block, panel_results(), FakeAsk(source=("F9.9", 1.0)), stats=stats) == []
    assert stats["errors"] == 1
    ask = FakeAsk()
    nobody = [MemberResult("A", "m", "approve", "fine")]
    assert signals.block_sources(block, nobody, ask) == [{"block": 0, "source": "none", "confidence": None}]
    assert ask.calls == []                                       # nothing to choose from: no call


# ── collect(): the one entry point ───────────────────────────────────────────────────────
ON = {"TYPESAFE_API_KEY": "test-key"}
BLOCKS = (("note extraction over-captures", "slices to the end of the prompt"),
          ("the README is out of date", "nobody on the panel raised this"))


def agreeing_ask():
    return FakeAsk(same=lambda s: 0.94 if {s["a"], s["b"]} == {OVER_CAPTURE, SPLITS} else 0.2,
                   source=lambda s, q: ("F1.1", 1.0) if "over-captures" in s["block"]["point"] else ("none", 0.93))


def read_log(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines()]


def test_collect_gathers_the_three_signals_and_logs_one_line(tmp_path):
    log = tmp_path / "state" / "council" / "jev-shadow.jsonl"          # parent folders do not exist yet
    ask = agreeing_ask()
    sig = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON, ask=ask,
                          log_path=log, tier="full", name="2026-09-19-some-item")
    assert sig.verdict["label"] == "approve_with_conditions" and sig.verdict["confidence"] == 0.91
    assert sig.seat_agreement == [{"a": "F1.1", "b": "F3.1", "p": 0.94}]
    assert sig.clusters == [{"findings": ["F1.1", "F3.1"], "seats": 2, "p_min": 0.94, "p_max": 0.94}]
    assert sig.block_sources == [{"block": 0, "source": "F1.1", "confidence": 1.0, "eligible": True},
                                 {"block": 1, "source": "none", "confidence": 0.93, "eligible": None}]
    assert (sig.calls, sig.errors, sig.seats_answered) == (5, 0, 2)
    assert sig.to_dict()["verdict"]["label"] == "approve_with_conditions"
    (row,) = read_log(log)
    assert row["kind"] == "review" and row["repo"] == "ai-harness" and row["model"] == "jev-1.13.0"
    assert row["ts"].endswith("Z") and row["item"] == "2026-09-19-some-item"
    assert row["chair_review_status"] == "unknown" and row["chair_blocks"] == 2 and row["tier"] == "full"
    assert row["findings"] == [
        {"id": "F1.1", "seat": "Eng Manager", "severity": "high", "confidence": 9, "eligible": True},
        {"id": "F1.2", "seat": "Eng Manager", "severity": "low", "confidence": 9, "eligible": False},
        {"id": "F3.1", "seat": "Adversary", "severity": "med", "confidence": 8, "eligible": False}]
    assert row["verdict"]["label"] == "approve_with_conditions" and row["calls"] == 5 and row["errors"] == 0
    assert {(s["a"], s["b"]) for s in row["pair_scores"]} == {("F1.1", "F3.1"), ("F1.2", "F3.1")}
    assert len(row["recommendation_sha256"]) == 64


def test_the_shadow_log_holds_no_review_text(tmp_path):
    log = tmp_path / "jev.jsonl"
    signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON, ask=agreeing_ask(), log_path=log)
    raw = log.read_text()
    for text in (OVER_CAPTURE, SPLITS, MISSING_TEST, RECOMMENDATION, "two edge paths", "parser splits",
                 "over-captures", "README", "nobody on the panel", "UnusableReply", "test-key"):
        assert text not in raw
    allowed_strings = {"", "review", "ai-harness", "jev-1.13.0", "unknown", "approve_with_conditions", "none",
                       "Eng Manager", "Adversary", "high", "low", "med", "F1.1", "F1.2", "F3.1"}

    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for v in value.values():
                yield from strings(v)
        elif isinstance(value, list):
            for v in value:
                yield from strings(v)
    (row,) = read_log(log)
    free = {s for k, v in row.items() if k not in ("ts", "recommendation_sha256") for s in strings(v)}
    assert free <= allowed_strings, free - allowed_strings


@pytest.mark.parametrize("environ,repo,name", [
    ({"TYPESAFE_API_KEY": "k", "COUNCIL_JEV": "0"}, "ai-harness", ""),      # kill switch
    ({"HOME": "/nonexistent-home"}, "ai-harness", ""),                     # no key
    (ON, "sat-prep", ""), (ON, "romance-empire", ""),                      # private data, manuscripts
    (ON, None, ""), (ON, "", ""),                                          # unknown repo: fail closed
    (ON, "ai-harness", "2026-09-02-sat-prep-vocab"),                       # the item names private work
])
def test_collect_returns_none_and_makes_no_call_when_off_out_of_scope_or_unknown(tmp_path, environ, repo, name):
    ask, log = FakeAsk(), tmp_path / "jev.jsonl"
    assert signals.collect(repo, panel_results(), synthesis(BLOCKS), environ=environ, ask=ask,
                           log_path=log, name=name) is None
    assert ask.calls == [] and not log.exists()


def test_collect_without_an_injected_ask_uses_the_key_and_the_module_transport(tmp_path, monkeypatch):
    seen = []

    def transport(req, key, timeout):
        seen.append((key, sorted(req["questions"])))
        return _reply({"verdict": {"choice": "approve", "confidence": 0.8, "probabilities": {}}})
    monkeypatch.setattr(jev, "_http_post", transport)
    sig = signals.collect("ai-harness", [], synthesis(), environ=ON, log_path=tmp_path / "l.jsonl")
    assert seen == [("test-key", ["verdict"])] and sig.verdict["label"] == "approve"


def test_collect_never_raises_and_keeps_what_it_already_has(tmp_path):
    def breaks_after_the_verdict(state, questions):
        if "verdict" in questions:
            return {"verdict": {"choice": "request_changes", "confidence": 0.77, "probabilities": {}}}
        raise RuntimeError("TypeSafe is down")
    sig = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON,
                          ask=breaks_after_the_verdict, log_path=tmp_path / "l.jsonl")
    assert sig.verdict["label"] == "request_changes"
    assert sig.seat_agreement == [] and sig.block_sources == [] and sig.errors == 4
    (row,) = read_log(tmp_path / "l.jsonl")
    assert row["errors"] == 4 and row["verdict"]["label"] == "request_changes"

    wrong_shape = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON,
                                  ask=lambda s, q: {"verdict": "approve", "same": 1, "source": []},
                                  log_path=tmp_path / "l2.jsonl")
    assert wrong_shape.verdict is None and wrong_shape.errors == 5

    class Exploding:
        def __getattr__(self, name):
            raise RuntimeError("not a synthesis at all")
    broken = signals.collect("ai-harness", panel_results(), Exploding(), environ=ON, ask=FakeAsk(),
                             log_path=tmp_path / "l3.jsonl")
    assert broken.verdict is None and broken.block_sources == [] and broken.errors == 2
    assert len(broken.pair_scores) == 2                          # the panel half still ran


def test_collect_survives_a_log_it_cannot_write(tmp_path):
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    sig = signals.collect("ai-harness", panel_results(), synthesis(), environ=ON, ask=FakeAsk(),
                          log_path=blocker / "sub" / "jev.jsonl")
    assert sig.verdict["label"] == "approve_with_conditions"


def test_collect_stops_starting_calls_once_the_time_budget_is_spent(tmp_path):
    now = [0.0]

    def slow(state, questions):
        now[0] += 8.0                                            # every call takes eight seconds
        return FakeAsk()(state, questions)
    calls = []

    def counting(state, questions):
        calls.append(sorted(questions))
        return slow(state, questions)
    sig = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON, ask=counting,
                          budget_seconds=20, clock=lambda: now[0], log_path=tmp_path / "l.jsonl")
    assert calls == [["verdict"], ["source"], ["source"]]        # 0 s, 8 s, 16 s; at 24 s nothing starts
    assert sig.budget_exhausted is True and sig.errors == 0 and sig.calls == 3
    assert read_log(tmp_path / "l.jsonl")[0]["budget_exhausted"] is True


def test_a_failed_chair_gets_no_verdict_question(tmp_path):
    failed = Synthesis(recommendation="(synthesis unavailable — see raw panel below)", confidence=0, error="boom")
    ask = FakeAsk()
    sig = signals.collect("ai-harness", panel_results(), failed, environ=ON, ask=ask, log_path=tmp_path / "l.jsonl")
    assert sig.verdict is None and ask.named("verdict") == [] and len(ask.named("same")) == 2


def test_default_log_path_is_under_local_state_and_the_environment_can_move_it(tmp_path):
    assert signals.log_path_for({"HOME": "/home/someone"}) == Path("/home/someone/.local/state/council/jev-shadow.jsonl")
    assert signals.log_path_for({"COUNCIL_JEV_LOG": str(tmp_path / "x.jsonl")}) == tmp_path / "x.jsonl"
