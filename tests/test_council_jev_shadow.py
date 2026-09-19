"""Jev shadow signals for the council: display and log only.

Two promises are tested here above all. (1) Nothing Jev returns changes the chair's input,
the gate count, `review_status`, readiness, `approve` or an exit code. (2) No failure in the
Jev step breaks the review it rides on. No test may reach TypeSafe: every call goes through
an injected `ask` or a fake transport.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

import jev.client as shared_client              # the shared client: the one door to TypeSafe
from council import jev                        # the council's thin layer over it


class _RefusingOpener:
    def __init__(self, opened):
        self.opened = opened

    def open(self, request, *args, **kwargs):
        self.opened.append(getattr(request, "full_url", str(request)))
        raise AssertionError("a test opened a real connection to TypeSafe")


@pytest.fixture(autouse=True)
def real_connections(monkeypatch):
    """No test here may reach TypeSafe. conftest already sets JEV_DISABLED=1 and COUNCIL_JEV=0;
    this is the last line of defence, the HTTP opener of the one shared client. The shadow
    code swallows exceptions by design, so the guard records and fails at teardown."""
    opened = []
    monkeypatch.setattr(shared_client, "_OPENER", _RefusingOpener(opened))
    yield opened
    assert not opened, f"a test opened a real connection: {opened}"


@pytest.fixture
def jev_on(monkeypatch):
    """Both switches on and a test key, as a wiring test needs them. Returns a function that
    installs a fake in place of the shared client's real transport."""
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.setenv("COUNCIL_JEV", "1")
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    return lambda transport: monkeypatch.setattr(shared_client, "http_post", transport)


def _reply(answers, model=jev.MODEL):
    return {"model": model, "answers": answers, "usage": {"input_tokens": 10}}


# ── the client ───────────────────────────────────────────────────────────────────────────
def test_the_guard_itself_catches_a_call_that_reaches_the_opener(real_connections, jev_on):
    with pytest.raises(jev.JevError):                          # the real transport, all the way down
        jev.ask("s", {"q": {"type": "noul", "instructions": "?"}}, key="k")
    assert real_connections == ["https://api.typesafe.ai/v1/systemone"]
    real_connections.clear()                                   # this one reach was the test


def test_every_call_goes_through_the_shared_client_and_lands_in_its_usage_ledger(jev_on, tmp_path, monkeypatch):
    # docs/contracts/jev.md rule 1: one door, no second HTTP client anywhere.
    monkeypatch.setenv("JEV_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    jev_on(lambda req, key, timeout: _reply({"q": {"type": "noul", "noul": 0.5}}))
    jev.ask("s", {"q": {"type": "noul", "instructions": "?"}}, key="k", task="council-verdict")
    (row,) = [json.loads(line) for line in (tmp_path / "usage.jsonl").read_text().splitlines()]
    assert (row["project"], row["task"], row["model"], row["ok"]) == ("ai-harness", "council-verdict", "jev-1.13.0", True)
    source = Path(jev.__file__).read_text()
    assert "urllib" not in source and "build_opener" not in source


def test_the_shared_off_switch_turns_the_council_off_too(monkeypatch):
    assert jev.shadow_enabled({"TYPESAFE_API_KEY": "k"}) is True
    assert jev.shadow_enabled({"TYPESAFE_API_KEY": "k", "JEV_DISABLED": "1"}) is False
    assert jev.shadow_enabled({"TYPESAFE_API_KEY": "k", "JEV_DISABLED": "0"}) is True
    monkeypatch.setenv("JEV_DISABLED", "1")                    # and the shared client refuses by itself
    with pytest.raises(jev.JevError, match="JEV_DISABLED"):
        jev.ask("s", {"q": {}}, key="k", transport=lambda *a: _reply({"q": {}}))


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


def test_ask_pins_the_model_and_redacts_the_state_and_the_questions(jev_on):
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


def test_ask_refuses_an_answer_from_another_model_version(jev_on):
    with pytest.raises(jev.JevError, match="not the pinned jev-1.13.0"):
        jev.ask("s", {"q": {}}, key="k", transport=lambda *a: _reply({}, model="jev-2.0.0"))


def test_ask_turns_every_failure_into_a_jev_error_that_never_names_the_key(jev_on):
    def down(req, key, timeout):
        raise OSError(f"connection refused for bearer {key}")
    with pytest.raises(jev.JevError) as err:
        jev.ask("s", {"q": {}}, key="sekret-key", transport=down)
    assert "sekret-key" not in str(err.value)
    with pytest.raises(jev.JevError, match="answers"):
        jev.ask("s", {"q": {}}, key="k", transport=lambda *a: {"model": jev.MODEL, "answers": "nope"})


def test_ask_without_a_transport_uses_the_shared_transport_looked_up_at_call_time(jev_on):
    jev_on(lambda req, key, timeout: _reply({"q": {"noul": 0.1}}))
    assert jev.ask("s", {"q": {}}, key="k") == {"q": {"noul": 0.1}}


def test_a_failed_call_is_not_retried_inside_a_review(jev_on):
    tries = []

    def busy(req, key, timeout):
        tries.append(1)
        raise shared_client.TransientError("HTTP 529")
    with pytest.raises(jev.JevError):
        jev.ask("s", {"q": {}}, key="k", transport=busy)
    assert len(tries) == 1                                      # the time budget is for other questions


ALLOWED_REPOS = ("ai-harness", "swimtrack", "swimtrack-website", "ultimate-portugal", "aris-management-website")
HARMLESS_LOOKING_UNKNOWN_REPOS = ("vps-tools", "splash_poller", "brand-new-repo")


def test_scope_is_an_allow_list_of_the_repos_the_signals_were_measured_on():
    # A deny list is not fail-closed: a new private repo would be in scope until someone
    # remembered to add its name. The list is pinned here so that extending it is deliberate.
    assert jev.IN_SCOPE_REPOS == frozenset(ALLOWED_REPOS)
    for repo in ALLOWED_REPOS:
        assert jev.in_scope("", repo) and jev.in_scope("2026-07-27-time-standards", repo), repo


@pytest.mark.parametrize("repo", HARMLESS_LOOKING_UNKNOWN_REPOS + ("Ai-Harness", "ai-harness ", "ai-harness-fork"))
def test_a_repo_that_is_not_on_the_allow_list_is_refused_however_harmless_it_looks(repo):
    assert not jev.in_scope("", repo)


def test_scope_rule_refuses_private_repos_manuscripts_and_unknown_repos():
    assert jev.in_scope("", "ai-harness") and jev.in_scope("2026-07-27-time-standards", "swimtrack-website")
    for name, repo in (("x", "sat-prep"), ("x", "romance-empire"), ("x", "tax-advisor"),
                       ("2026-09-02-sat-prep-vocab", "ai-harness"), ("x", "finance-tracker"),
                       ("x", None), ("x", "")):
        assert not jev.in_scope(name, repo)


@pytest.mark.parametrize("repo", ALLOWED_REPOS)
def test_an_allowed_repo_is_still_refused_when_the_item_id_names_private_work(repo):
    for item_id in ("2026-09-02-sat-prep-vocab", "bebop-briefing-fix", "2026-08-01-tax-export", "gmail-filter-cleanup"):
        assert not jev.in_scope(item_id, repo), (item_id, repo)


def test_the_allow_list_and_the_explicit_refusal_list_never_overlap(monkeypatch):
    assert not jev.IN_SCOPE_REPOS & jev.OUT_OF_SCOPE_REPOS
    # belt and braces: a name on BOTH lists (a future editing slip) is still refused
    monkeypatch.setattr(jev, "IN_SCOPE_REPOS", jev.IN_SCOPE_REPOS | {"romance-empire"})
    assert not jev.in_scope("", "romance-empire")


def test_the_offline_harness_shares_the_one_scope_list_and_key_loader():
    import jev.scope as shared_scope
    from tools.jev_council import jev as offline, reviews
    assert reviews.OUT_OF_SCOPE is jev.OUT_OF_SCOPE is shared_scope.OUT_OF_SCOPE      # one word list for every caller
    assert reviews.OUT_OF_SCOPE_REPOS is jev.OUT_OF_SCOPE_REPOS
    assert reviews.in_scope is jev.in_scope and offline.load_key is jev.load_key
    assert offline.MODEL == jev.MODEL and offline.redact_text is jev.redact_text


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


def test_a_free_text_severity_never_becomes_a_label():
    odd = [MemberResult("A", "m", "concerns", "h", findings=[Finding("p", "High", 9), Finding("q", "high: leaks the plot", "8")])]
    assert [(f.severity, f.confidence) for f in signals.numbered_findings(odd)] == [("high", 9), ("other", 8)]


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
    signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON, ask=agreeing_ask(), log_path=log,
                    panel="code-review")
    raw = log.read_text()
    for text in (OVER_CAPTURE, SPLITS, MISSING_TEST, RECOMMENDATION, "two edge paths", "parser splits",
                 "over-captures", "README", "nobody on the panel", "UnusableReply", "test-key"):
        assert text not in raw
    allowed_strings = {"", "review", "ai-harness", "code-review", "jev-1.13.0", "unknown", "approve_with_conditions", "none",
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
    (ON, "vps-tools", ""), (ON, "splash_poller", ""), (ON, "brand-new-repo", ""),   # not on the allow list
    (ON, "ai-harness", "2026-09-02-sat-prep-vocab"),                       # the item names private work
])
def test_collect_returns_none_and_makes_no_call_when_off_out_of_scope_or_unknown(tmp_path, environ, repo, name):
    ask, log = FakeAsk(), tmp_path / "jev.jsonl"
    assert signals.collect(repo, panel_results(), synthesis(BLOCKS), environ=environ, ask=ask,
                           log_path=log, name=name) is None
    assert ask.calls == [] and not log.exists()


def test_collect_without_an_injected_ask_uses_the_key_and_the_module_transport(tmp_path, monkeypatch, jev_on):
    seen = []

    def transport(req, key, timeout):
        seen.append((key, sorted(req["questions"])))
        return _reply({"verdict": {"choice": "approve", "confidence": 0.8, "probabilities": {}}})
    jev_on(transport)
    monkeypatch.setenv("JEV_USAGE_LOG", str(tmp_path / "usage.jsonl"))
    sig = signals.collect("ai-harness", [], synthesis(), environ=ON, log_path=tmp_path / "l.jsonl")
    assert seen == [("test-key", ["verdict"])] and sig.verdict["label"] == "approve"
    assert json.loads((tmp_path / "usage.jsonl").read_text())["task"] == "council-verdict"


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


# ── the shadow section, and `council review` ─────────────────────────────────────────────
from council import cli                                                          # noqa: E402
from council.config import Settings                                              # noqa: E402
from council.models import Member, Panel                                         # noqa: E402
from council.render import render_jev_shadow, render_markdown                    # noqa: E402
from tests.conftest import FakeClient                                            # noqa: E402

TITLE = "### Jev shadow signals (display only; not used by the chair or the gate)"


def test_the_shadow_section_names_ids_seats_labels_and_numbers_only(tmp_path):
    sig = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS), environ=ON, ask=agreeing_ask(),
                          log_path=tmp_path / "l.jsonl", tier="full")
    text = render_jev_shadow(sig, chair_status="unknown")
    assert text.splitlines()[0] == TITLE
    assert "Jev reads the chair's verdict as: **approve_with_conditions** (0.91)" in text
    assert "F1.1 (Eng Manager) and F3.1 (Adversary) look like the same problem (0.94); raised by 2 of 2 seats" in text
    assert "Block 1 -> F1.1 (Eng Manager, high c9, eligible at this tier) (1.00)" in text
    assert "Block 2 -> none of the panel findings (0.93)" in text
    for private in (OVER_CAPTURE, SPLITS, RECOMMENDATION, "over-captures", "README"):
        assert private not in text
    # saved reviews are parsed back by tools: no line here may look like a panel finding
    import re
    assert not [ln for ln in text.splitlines() if re.match(r"^- `[^`]+` \(c\d+\)", ln) or ln.startswith("#### ")]


def test_the_shadow_section_says_so_when_a_link_is_not_eligible_or_the_tier_is_unknown(tmp_path):
    low = FakeAsk(source=("F1.2", 0.8))
    full = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS[:1]), environ=ON, ask=low,
                           log_path=tmp_path / "l.jsonl", tier="full")
    assert "Block 1 -> F1.2 (Eng Manager, low c9, not eligible at this tier) (0.80)" in render_jev_shadow(full)
    no_tier = signals.collect("ai-harness", panel_results(), synthesis(BLOCKS[:1]), environ=ON, ask=low,
                              log_path=tmp_path / "l.jsonl")
    assert "Block 1 -> F1.2 (Eng Manager, low c9) (0.80)" in render_jev_shadow(no_tier)


def test_the_shadow_section_groups_three_seats_and_reports_failures(tmp_path):
    results = panel_results() + [MemberResult("Designer", "m4", "concerns", "h", findings=[Finding(SPLITS + " Again.", "med", 8)])]
    ask = FakeAsk(same=lambda s: 0.2 if MISSING_TEST in (s["a"], s["b"]) else 0.9)
    text = render_jev_shadow(signals.collect("ai-harness", results, synthesis(), environ=ON, ask=ask,
                                             log_path=tmp_path / "l.jsonl"))
    assert "F1.1 (Eng Manager), F3.1 (Adversary) and F4.1 (Designer) look like the same problem (0.90); raised by 3 of 3 seats" in text

    def down(state, questions):
        raise jev.JevError("down")
    failed = render_jev_shadow(signals.collect("ai-harness", panel_results(), synthesis(), environ=ON, ask=down,
                                               log_path=tmp_path / "l.jsonl"))
    assert failed.splitlines()[0] == TITLE and "3 Jev calls failed" in failed
    assert "Jev reads the chair's verdict" not in failed


def test_the_shadow_section_says_when_only_some_pairs_were_checked(tmp_path):
    severities = ["high", "med", "low", "info", "critical", "high", "med", "low"]
    big = [MemberResult(name, "m", "concerns", "h",
                        findings=[Finding(f"{name} point {n}", sev, 8) for n, sev in enumerate(severities)])
           for name in ("A", "B")]
    sig = signals.collect("ai-harness", big, synthesis(), environ=ON, ask=FakeAsk(), log_path=tmp_path / "l.jsonl")
    assert (sig.pairs_total, sig.pairs_asked, sig.truncated) == (64, 60, True)
    text = render_jev_shadow(sig)
    assert "Only 60 of 64 finding pairs were checked, the most severe first" in text
    assert "No two seats raised the same problem (60 pairs checked, cut 0.85)" in text


DIFF = ("diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n"
        "@@ -1 +1 @@\n-old\n+new\n")
CHAIR = {"recommendation": RECOMMENDATION, "confidence": 7, "consensus": [], "disagreements": [],
         "cross_panel_themes": [],
         "blocking_findings": [{"point": "note extraction over-captures", "severity": "high", "why": "slices to the end"}]}


def review_world(member_json):
    settings = Settings(default_panel="code-review", router_model="r", chair_model="c")
    panels = {"code-review": Panel("code-review", "review", [Member("Eng Manager", "m1", "eng"),
                                                             Member("Adversary", "m3", "adv")])}
    client = FakeClient(by_model={"m1": member_json(findings=[(OVER_CAPTURE, "high", 9), (MISSING_TEST, "low", 9)]),
                                  "m3": member_json(findings=[(SPLITS, "med", 8)]), "c": CHAIR})
    return settings, panels, client


def fake_jev_transport(calls):
    def transport(req, key, timeout):
        calls.append(req)
        q = req["questions"]
        if "verdict" in q:
            return _reply({"verdict": {"type": "choice", "choice": "approve_with_conditions", "confidence": 0.91,
                                       "probabilities": {}}})
        if "same" in q:
            same = {req["state"]["a"], req["state"]["b"]} == {OVER_CAPTURE, SPLITS}
            return _reply({"same": {"type": "noul", "noul": 0.94 if same else 0.12}})
        return _reply({"source": {"type": "choice", "choice": "F1.1", "confidence": 1.0, "probabilities": {}}})
    return transport


@pytest.fixture
def in_scope_checkout(tmp_path, monkeypatch):
    """A git checkout named like an in-scope repo, as the working directory, with a diff file."""
    repo = tmp_path / "projects" / "swimtrack"
    repo.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    (repo / "change.diff").write_text(DIFF)
    monkeypatch.chdir(repo)
    return repo


def run_review(member_json, capsys, *args):
    settings, panels, client = review_world(member_json)
    rc = cli.main(["review", *args], _settings=settings, _panels=panels, _client=client)
    return rc, capsys.readouterr().out, client


@pytest.mark.parametrize("switch", ["COUNCIL_JEV", "JEV_DISABLED"])
def test_review_output_is_byte_identical_to_today_when_the_shadow_is_off(member_json, capsys, in_scope_checkout,
                                                                         monkeypatch, jev_on, switch):
    calls = []
    jev_on(fake_jev_transport(calls))                                 # a key and a transport exist...
    monkeypatch.setenv(switch, "0" if switch == "COUNCIL_JEV" else "1")   # ...and one of the two switches is off
    rc, out, client = run_review(member_json, capsys, "change.diff", "--format", "md")
    settings, panels, replay = review_world(member_json)
    from council.engine import run_panel
    from council.synthesize import synthesize
    ctx = f"Review this:\n\n--- change.diff ---\n{DIFF}"
    results = run_panel(panels["code-review"], ctx, replay)
    syn = synthesize(ctx, results, replay, chair_model="c")
    assert out == "[panel: code-review · rigor: daily]\n\n" + render_markdown(ctx[:120], syn, results, rigor="daily") + "\n"
    assert rc == 0 and calls == [] and "Jev" not in out


def test_review_shows_the_shadow_section_at_the_end_when_on(member_json, capsys, in_scope_checkout, monkeypatch, jev_on):
    calls = []
    jev_on(fake_jev_transport(calls))
    monkeypatch.setenv("COUNCIL_JEV", "0")
    rc_off, off, client_off = run_review(member_json, capsys, "change.diff", "--format", "md")
    monkeypatch.setenv("COUNCIL_JEV", "1")
    rc_on, on, client_on = run_review(member_json, capsys, "change.diff", "--format", "md")
    assert rc_on == rc_off == 0
    assert on.startswith(off) and on[len(off):].strip().splitlines()[0] == TITLE      # strictly appended
    assert "raised by 2 of 2 seats" in on and "Block 1 -> F1.1 (Eng Manager, high c9, eligible at this tier) (1.00)" in on
    by_model = lambda client: sorted(client.calls, key=lambda c: c["model"])     # noqa: E731  (seats run in threads)
    assert by_model(client_on) == by_model(client_off)          # the panel and the chair saw exactly the same input
    assert len(calls) == 4 and all(c["model"] == "jev-1.13.0" for c in calls)
    wire = json.dumps(calls)
    assert "+new" not in wire and "diff --git" not in wire      # the diff itself is never sent
    (row,) = read_log(Path(__import__("os").environ["COUNCIL_JEV_LOG"]))
    assert row["repo"] == "swimtrack" and row["tier"] == "full" and row["chair_blocks"] == 1
    assert row["panel"] == "code-review"


def test_review_in_the_terminal_format_gets_the_section_too(member_json, capsys, in_scope_checkout, monkeypatch, jev_on):
    jev_on(fake_jev_transport([]))
    rc, out, _ = run_review(member_json, capsys, "change.diff")
    assert rc == 0 and out.rstrip().count(TITLE) == 1 and out.index(TITLE) > out.index("── Raw panel ──")


def test_a_broken_jev_step_never_changes_the_review_or_its_exit_code(member_json, capsys, in_scope_checkout, monkeypatch, jev_on):
    jev_on(fake_jev_transport([]))
    monkeypatch.setenv("COUNCIL_JEV", "0")
    rc_off, off, _ = run_review(member_json, capsys, "change.diff", "--format", "md")
    monkeypatch.setenv("COUNCIL_JEV", "1")

    def explode(*a, **k):
        raise RuntimeError("signals blew up")
    monkeypatch.setattr(signals, "collect", explode)
    rc, out, _ = run_review(member_json, capsys, "change.diff", "--format", "md")
    assert (rc, out) == (rc_off, off)


@pytest.mark.parametrize("folder", ["sat-prep", "romance-empire", *HARMLESS_LOOKING_UNKNOWN_REPOS])
def test_review_in_an_out_of_scope_checkout_makes_no_jev_call(member_json, capsys, tmp_path, monkeypatch, folder, jev_on):
    repo = tmp_path / folder
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "change.diff").write_text(DIFF)
    monkeypatch.chdir(repo)
    calls = []
    jev_on(fake_jev_transport(calls))
    rc, out, _ = run_review(member_json, capsys, "change.diff", "--format", "md")
    assert rc == 0 and calls == [] and "Jev" not in out


def test_review_outside_any_repository_or_of_a_path_in_a_private_repo_makes_no_jev_call(
        member_json, capsys, tmp_path, in_scope_checkout, monkeypatch, jev_on):
    calls = []
    jev_on(fake_jev_transport(calls))
    private = tmp_path / "tax-advisor"
    private.mkdir()
    _git(private, "init", "-q", "-b", "main")
    (private / "return.py").write_text("income = 1\n")
    rc, out, _ = run_review(member_json, capsys, str(private / "return.py"), "--panel", "code-review", "--format", "md")
    assert rc == 0 and calls == [] and "Jev" not in out          # the working directory is in scope; the file is not
    plain = tmp_path / "loose"
    plain.mkdir()
    (plain / "change.diff").write_text(DIFF)
    monkeypatch.chdir(plain)
    rc, out, _ = run_review(member_json, capsys, "change.diff", "--format", "md")
    assert rc == 0 and calls == [] and "Jev" not in out          # unknown repo: fail closed


def test_the_gate_and_the_commands_load_no_jev_code_until_a_review_asks_for_it():
    # The CI gate imports council.review from an installed copy. Jev code is imported lazily,
    # inside guarded blocks, so a missing or broken Jev layer cannot break an import.
    import sys
    code = ("import sys, council.review, council.cli, council.sweep, council.render, backlogrun.cli; "
            "print([m for m in ('jev', 'council.jev', 'council.signals') if m in sys.modules])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.stdout.strip() == "[]"


def test_ask_never_runs_the_shadow(member_json, capsys, in_scope_checkout, monkeypatch, jev_on):
    calls = []
    jev_on(fake_jev_transport(calls))
    settings, panels, client = review_world(member_json)
    assert cli.main(["ask", "ship it?", "--panel", "code-review"], _settings=settings, _panels=panels, _client=client) == 0
    assert calls == [] and "Jev" not in capsys.readouterr().out


def test_the_ci_gate_path_never_calls_jev_even_with_a_key(member_json, monkeypatch, in_scope_checkout, jev_on):
    from council.review import run_pr_review
    calls = []
    jev_on(fake_jev_transport(calls))
    settings, panels, client = review_world(member_json)
    body, blocking, unavailable = run_pr_review(DIFF, panels, client, chair_model="c")
    assert (blocking, unavailable) == (1, False) and calls == [] and "Jev" not in body
    import council.review as review_module
    source = Path(review_module.__file__).read_text()
    assert "jev" not in source.lower() and "signals" not in source


# ── the weekly sweep: a shadow note, nothing else ────────────────────────────────────────
from council.models import SweepFinding                                           # noqa: E402
from council.render import render_sweep                                           # noqa: E402
from council.sweep import run_sweep                                               # noqa: E402

SWEEP_PANEL = Panel("red-team", "break it", [Member("Adversary", "m1", "attacker"), Member("Sec", "m2", "cso")])
SQLI_A = "SQL injection: the query in a.py is built by string concatenation from user input"
SQLI_B = "User input reaches the database query in a.py unescaped, so an attacker can inject SQL"
SECRET = "A hardcoded API secret is committed in b.py"


def sweep_client():
    def member(findings):
        return json.dumps({"stance": "oppose", "headline": "issues", "suggestions": [],
                           "findings": [{"point": p, "severity": s, "confidence": c} for p, s, c in findings]})
    return FakeClient(by_model={"m1": member([(SQLI_A, "high", 9), (SECRET, "critical", 9)]),
                                "m2": member([(SQLI_B, "high", 8)]), "c": json.dumps({"summary": "2 real risks"})})


def sweep_transport(calls, p_same=0.93):
    def transport(req, key, timeout):
        calls.append(req)
        same = {req["state"]["a"], req["state"]["b"]} == {SQLI_A, SQLI_B}
        return _reply({"same": {"type": "noul", "noul": p_same if same else 0.05}})
    return transport


def test_collect_sweep_groups_findings_the_text_dedup_missed_and_logs_no_text(tmp_path):
    findings = [SweepFinding(SECRET, "critical", 9, ["b.py"], ["Adversary"]),
                SweepFinding(SQLI_A, "high", 9, ["a.py"], ["Adversary"]),
                SweepFinding(SQLI_B, "high", 8, ["a.py"], ["Sec"])]
    ask = FakeAsk(same=lambda s: 0.93 if {s["a"], s["b"]} == {SQLI_A, SQLI_B} else 0.05)
    note = signals.collect_sweep("ai-harness", findings, environ=ON, ask=ask, log_path=tmp_path / "l.jsonl")
    assert note["groups"] == [{"findings": [2, 3], "p_min": 0.93, "p_max": 0.93}]
    assert (note["pairs_total"], note["pairs_asked"], note["truncated"], note["errors"]) == (3, 3, False, 0)
    raw = (tmp_path / "l.jsonl").read_text()
    (row,) = read_log(tmp_path / "l.jsonl")
    assert row["kind"] == "sweep" and row["repo"] == "ai-harness" and row["model"] == "jev-1.13.0"
    assert row["findings"] == [{"id": "S1", "severity": "critical", "confidence": 9, "files": 1, "seats": 1},
                               {"id": "S2", "severity": "high", "confidence": 9, "files": 1, "seats": 1},
                               {"id": "S3", "severity": "high", "confidence": 8, "files": 1, "seats": 1}]
    assert "SQL" not in raw and "secret" not in raw.lower() and "a.py" not in raw


def test_collect_sweep_caps_the_pairs_and_refuses_out_of_scope_repos(tmp_path):
    many = [SweepFinding(f"finding number {n}", "high", 9, ["a.py"], ["Sec"]) for n in range(12)]    # 66 pairs
    ask = FakeAsk(same=0.1)
    note = signals.collect_sweep("ai-harness", many, environ=ON, ask=ask, log_path=tmp_path / "l.jsonl")
    assert (note["pairs_total"], note["pairs_asked"], note["truncated"], len(ask.calls)) == (66, 40, True, 40)
    for repo in ("sat-prep", "romance-empire", None, *HARMLESS_LOOKING_UNKNOWN_REPOS):
        quiet = FakeAsk()
        assert signals.collect_sweep(repo, many, environ=ON, ask=quiet, log_path=tmp_path / "l.jsonl") is None
        assert quiet.calls == []
    assert signals.collect_sweep("ai-harness", many, environ={**ON, "COUNCIL_JEV": "off"}, ask=FakeAsk()) is None


def test_sweep_reports_the_same_findings_in_the_same_order_with_only_a_note_added(monkeypatch, jev_on):
    chunks = [("a.py", "code a"), ("b.py", "code b")]
    calls = []
    jev_on(sweep_transport(calls))
    plain = run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c")
    assert plain.jev_shadow is None and calls == []             # no repo named: no Jev step
    private = run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c", jev_repo="finance-tracker")
    assert private.jev_shadow is None and calls == []
    shadowed = run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c", jev_repo="ai-harness")
    assert [(f.point, f.severity, f.confidence, f.locations, f.sources) for f in shadowed.findings] == \
           [(f.point, f.severity, f.confidence, f.locations, f.sources) for f in plain.findings]
    assert (shadowed.summary, shadowed.error, shadowed.chunks_scanned) == (plain.summary, plain.error, plain.chunks_scanned)
    before, after = render_sweep("/repo", plain), render_sweep("/repo", shadowed)
    assert after.startswith(before)
    note = after[len(before):]
    assert "display only" in note and "Jev would also group: finding 2 + finding 3 (0.93)" in note
    count = lambda text: sum(1 for ln in text.splitlines() if ln.startswith("- "))      # noqa: E731
    assert count(after) == count(before) == 3                   # security-sweep.sh counts "- " lines as findings
    assert "### Findings" not in note and "### Summary" not in note                       # and cuts its summary there
    assert len(calls) == 3 and SQLI_A not in note and "a.py" not in note


def test_sweep_with_nothing_to_group_or_a_jev_outage_renders_exactly_as_before(monkeypatch, jev_on):
    chunks = [("a.py", "code a"), ("b.py", "code b")]
    jev_on(sweep_transport([], p_same=0.4))
    plain = render_sweep("/repo", run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c"))
    assert render_sweep("/repo", run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c", jev_repo="ai-harness")) == plain

    def down(req, key, timeout):
        raise OSError("down")
    jev_on(down)
    assert render_sweep("/repo", run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c", jev_repo="ai-harness")) == plain
    monkeypatch.setattr(signals, "collect_sweep", lambda *a, **k: 1 / 0)
    assert render_sweep("/repo", run_sweep(chunks, SWEEP_PANEL, sweep_client(), chair_model="c", jev_repo="ai-harness")) == plain


@pytest.mark.parametrize("folder,grouped", [("ultimate-portugal", True), ("swimtrack-coach", False),
                                            ("vps-tools", False), ("brand-new-repo", False)])
def test_cli_sweep_applies_the_scope_rule_to_the_swept_repo(tmp_path, capsys, monkeypatch, folder, grouped, jev_on):
    repo = tmp_path / folder
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.py").write_text("query = 'SELECT ' + user_input\n")
    calls = []
    jev_on(sweep_transport(calls))
    rc = cli.main(["sweep", str(repo)], _settings=Settings(chair_model="c"), _panels={"red-team": SWEEP_PANEL},
                  _client=sweep_client())
    out = capsys.readouterr().out
    assert rc == 0 and ("Jev would also group" in out) is grouped and bool(calls) is grouped


# ── council review 2026-09-19: a hard deadline, a tri-state scope, a locked log ───────────
def test_ask_hands_the_transport_the_time_it_was_given_capped_at_eight_seconds(jev_on):
    seen = []

    def transport(req, key, timeout):
        seen.append(timeout)
        return _reply({"q": {"noul": 0.5}})
    for given in (None, 3.25, 30):
        jev.ask("s", {"q": {}}, key="k", transport=transport, timeout=given)
    assert seen == [8, 3.25, 8]


@pytest.mark.parametrize("each_call_takes,expected", [
    (None, [8, 8, 4]),              # every call blocks for its whole timeout: 8 + 8 + 4 is the budget
    (6.5, [8, 8, 7]),               # 0 s, 6.5 s, 13 s; at 19.5 s under a second is left: nothing starts
    (0.5, [8] * 14),                # the normal case: 1 verdict, 2 blocks, 11 pairs, 7 s in all
])
def test_the_time_budget_is_a_real_deadline(jev_on, tmp_path, each_call_takes, expected):
    now, calls = [100.0], []

    def transport(req, key, timeout):
        calls.append((now[0] - 100.0, timeout))                  # (seconds into the pass, time it may take)
        now[0] += timeout if each_call_takes is None else each_call_takes
        return fake_jev_transport([])(req, key, timeout)
    jev_on(transport)
    many = panel_results() + [MemberResult("Designer", "m4", "concerns", "h",
                                           findings=[Finding(f"point {n}", "med", 8) for n in range(3)])]
    sig = signals.collect("ai-harness", many, synthesis(BLOCKS), environ=ON, budget_seconds=20,
                          clock=lambda: now[0], log_path=tmp_path / "l.jsonl")
    assert [timeout for _, timeout in calls] == expected
    for started_at, timeout in calls:
        assert 1.0 <= timeout <= 8 and started_at + timeout <= 20    # no call can END after the deadline
    assert now[0] - 100.0 <= 20 and sig.errors == 0 and sig.calls == len(expected)
    assert sig.budget_exhausted is (each_call_takes != 0.5)
    if each_call_takes is None:                                  # the worst case: every call blocks to the end
        assert sum(timeout for _, timeout in calls) == 20        # time asked for never exceeds the budget
    if each_call_takes != 0.5:
        assert calls[-1][1] < 8                                  # the last call got what was left, not 8 s


def test_an_injected_ask_that_takes_a_timeout_gets_one_and_a_plain_one_is_left_alone(tmp_path):
    given = []

    def timed(state, questions, timeout=None):
        given.append(timeout)
        return FakeAsk()(state, questions)
    signals.collect("ai-harness", [], synthesis(), environ=ON, ask=timed, log_path=tmp_path / "l.jsonl")
    assert given == [8]
    assert signals.collect("ai-harness", [], synthesis(), environ=ON, ask=FakeAsk(),
                           log_path=tmp_path / "l.jsonl").verdict["label"] == "approve_with_conditions"


def test_scope_probes_of_council_review_count_inside_the_same_budget(monkeypatch, tmp_path):
    now, seen = [50.0], {}

    def slow_scope(cwd, path=None):
        now[0] += 5.0                                            # two git probes that took five seconds
        return "ai-harness"

    def capture(repo, results, syn, **kwargs):
        seen.update(repo=repo, budget=kwargs["budget_seconds"])
        return None
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("COUNCIL_JEV", "1")
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.setattr(jev, "scope_repo", slow_scope)
    monkeypatch.setattr(signals, "collect", capture)
    assert cli._jev_shadow_section("text", None, clock=lambda: now[0])(panel_results(), synthesis(), "code-review") == ""
    assert seen == {"repo": "ai-harness", "budget": 15.0}


def test_a_git_probe_is_short_and_a_probe_that_times_out_means_an_unknown_repo(monkeypatch, tmp_path):
    seen = {}

    def hang(argv, **kwargs):
        seen.update(timeout=kwargs["timeout"], lang=kwargs["env"].get("LC_ALL"))
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
    monkeypatch.setattr(jev.subprocess, "run", hang)
    assert jev.resolve_repo(tmp_path) == ("unknown", None) and jev.repo_name(tmp_path) is None
    assert seen == {"timeout": 3, "lang": "C"}                   # C locale: the "not a git repository" text is matched


_REAL_RUN = subprocess.run


def _fake_git(monkeypatch, behaviour):
    """behaviour: {directory name: "missing" | "timeout" | "denied" | "dubious"} for the repo
    probe of that directory. Every other command, and every other directory, runs for real."""
    real = _REAL_RUN

    def run(argv, **kwargs):
        probe = list(argv[:2]) == ["git", "-C"] and "--git-common-dir" in argv
        kind = behaviour.get(Path(argv[2]).name) if probe else None
        if kind == "missing":
            raise FileNotFoundError("git")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        if kind == "denied":
            raise PermissionError("permission denied")
        if kind == "dubious":
            return subprocess.CompletedProcess(argv, 128, "", "fatal: detected dubious ownership in repository")
        return real(argv, **kwargs)
    monkeypatch.setattr(jev.subprocess, "run", run)


@pytest.fixture
def three_places(tmp_path):
    """An allowed checkout, a private checkout, and a folder outside any repository."""
    allowed, private, loose = tmp_path / "swimtrack", tmp_path / "tax-advisor", tmp_path / "loose"
    for repo in (allowed, private):
        repo.mkdir()
        _git(repo, "init", "-q", "-b", "main")
        (repo / "a.py").write_text("x = 1\n")
    loose.mkdir()
    (loose / "saved.diff").write_text(DIFF)
    return allowed, private, loose


def test_resolve_repo_tells_a_repo_from_no_repo_from_cannot_tell(three_places, monkeypatch):
    allowed, private, loose = three_places
    assert jev.resolve_repo(allowed / "a.py") == ("repo", "swimtrack")
    assert jev.resolve_repo(loose / "saved.diff") == ("outside", None)          # git itself said: not a repository
    assert jev.resolve_repo(loose / "gone" / "x.diff") == ("unknown", None)     # an unreadable path
    for kind in ("missing", "timeout", "denied", "dubious"):
        _fake_git(monkeypatch, {"swimtrack": kind})
        assert jev.resolve_repo(allowed / "a.py") == ("unknown", None), kind


def test_scope_repo_judges_a_path_outside_any_repository_by_the_working_directory(three_places):
    allowed, private, loose = three_places
    assert jev.scope_repo(allowed, loose / "saved.diff") == "swimtrack"         # as documented
    assert jev.scope_repo(allowed) == "swimtrack" and jev.scope_repo(allowed, "-") == "swimtrack"
    assert jev.scope_repo(private, loose / "saved.diff") is None
    assert jev.scope_repo(loose, loose / "saved.diff") is None                  # no repository at all: refuse
    assert jev.scope_repo(loose, allowed / "a.py") == "swimtrack"               # the file itself is in an allowed repo


def test_scope_repo_refuses_when_either_side_is_out_of_scope(three_places):
    allowed, private, loose = three_places
    assert jev.scope_repo(allowed, private / "a.py") is None                    # path out of scope, cwd in scope
    assert jev.scope_repo(private, allowed / "a.py") is None                    # path in scope, cwd out of scope


@pytest.mark.parametrize("kind", ["missing", "timeout", "denied", "dubious"])
def test_scope_repo_never_falls_back_to_the_working_directory_when_the_path_cannot_be_resolved(
        three_places, monkeypatch, kind):
    allowed, private, loose = three_places
    _fake_git(monkeypatch, {"tax-advisor": kind})
    assert jev.scope_repo(allowed, private / "a.py") is None                    # not "judge by cwd": refuse
    _fake_git(monkeypatch, {"loose": kind})
    assert jev.scope_repo(allowed, loose / "saved.diff") is None                # cannot tell it is outside: refuse
    _fake_git(monkeypatch, {"swimtrack": kind})
    assert jev.scope_repo(allowed) is None and jev.scope_repo(allowed, loose / "saved.diff") is None


def test_review_of_a_diff_saved_outside_any_repository_is_judged_by_the_working_directory(
        member_json, capsys, three_places, monkeypatch, jev_on):
    allowed, private, loose = three_places
    calls = []
    jev_on(fake_jev_transport(calls))
    monkeypatch.chdir(allowed)
    rc, out, _ = run_review(member_json, capsys, str(loose / "saved.diff"), "--format", "md")
    assert rc == 0 and len(calls) == 4 and TITLE in out
    calls.clear()
    _fake_git(monkeypatch, {"loose": "timeout"})                                # the same command, git cannot answer
    rc, out, _ = run_review(member_json, capsys, str(loose / "saved.diff"), "--format", "md")
    assert rc == 0 and calls == [] and "Jev" not in out


def test_two_log_appends_are_two_intact_private_lines(tmp_path):
    log = tmp_path / "deep" / "er" / "jev.jsonl"
    signals._append_log({"n": 1, "text": "x" * 5000}, log)
    signals._append_log({"n": 2}, log)
    raw = log.read_bytes()
    assert raw.endswith(b"\n") and [json.loads(line)["n"] for line in raw.decode().splitlines()] == [1, 2]
    assert (log.stat().st_mode & 0o777) == 0o600


def test_every_log_line_is_one_write_under_an_exclusive_lock(tmp_path, monkeypatch):
    import fcntl
    events, log_fds = [], set()
    real_flock, real_write = fcntl.flock, os.write

    def flock(fd, op):
        log_fds.add(fd)
        events.append(("flock", op & ~fcntl.LOCK_NB))
        return real_flock(fd, op)

    def write(fd, data):
        if fd in log_fds:                                        # only writes to the log's own descriptor
            events.append(("write", data[-1:]))
        return real_write(fd, data)
    monkeypatch.setattr(fcntl, "flock", flock)
    monkeypatch.setattr(signals.os, "write", write)
    signals._append_log({"n": 1}, tmp_path / "jev.jsonl")
    assert events == [("flock", fcntl.LOCK_EX), ("write", b"\n"), ("flock", fcntl.LOCK_UN)]


def test_concurrent_appends_never_tear_a_line(tmp_path):
    import threading
    log = tmp_path / "jev.jsonl"
    threads = [threading.Thread(target=signals._append_log, args=({"n": n, "pad": "y" * 3000}, log)) for n in range(24)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(json.loads(line)["n"] for line in log.read_text().splitlines()) == list(range(24))


def test_a_log_that_is_locked_or_unwritable_never_raises_and_never_stalls(tmp_path, monkeypatch):
    import fcntl
    monkeypatch.setattr(signals, "LOG_LOCK_TRIES", 3)
    monkeypatch.setattr(signals, "LOG_LOCK_WAIT_SECONDS", 0.001)
    log = tmp_path / "jev.jsonl"
    log.write_text("")
    with open(log, "a") as holder:
        fcntl.flock(holder, fcntl.LOCK_EX)                       # someone else holds the lock and never lets go
        signals._append_log({"n": 1}, log)
    assert log.read_text() == ""                                 # no lock, no write: the row is dropped, not torn
    signals._append_log({"n": 2}, log)
    assert [json.loads(line)["n"] for line in log.read_text().splitlines()] == [2]
    blocker = tmp_path / "a-file"
    blocker.write_text("x")
    signals._append_log({"n": 3}, blocker / "sub" / "jev.jsonl")  # the parent is a file
    signals._append_log({"n": 4}, tmp_path)                       # the path is a directory
    signals._append_log({"n": object()}, log)                    # not JSON: swallowed too
    assert len(log.read_text().splitlines()) == 1
