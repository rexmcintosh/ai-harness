"""Backup mode end to end: when a run switches, what the report and the log say, what exits
non-zero, and where backup must never run. Venice's transport and the child runner are faked;
everything between them is the real code."""
import importlib.util
import io
import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests

from council import backup, cli
from council.backup import BackupClient, BackupConfig, Credits, CreditGuard, RunClient
from council.config import Settings, load_panels
from council.engine import run_panel
from council.models import Member, Panel
from council.review import run_pr_review
from council.venice import VeniceClient, VeniceError, VeniceOutOfCredits
from tests.test_council_backup_client import ROUTES, FakeRun, _alive, claude_stream, codex_stream

KEY = "venice-run-key-abc123"
CHAIR_REPLY = {"recommendation": "ship it", "confidence": 8}
SEAT_REPLY = {"stance": "approve", "headline": "fine", "findings": [], "suggestions": []}
VOTE_REPLY = {"pick": "a.py", "ranking": ["a.py", "b.py"], "rationale": "shorter"}
COMPARE_CHAIR_REPLY = {"winner": "a.py", "rationale": "shorter", "ranking": ["a.py", "b.py"], "confidence": 7}


class Resp:
    def __init__(self, status, body):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def reply(body):
    text = body if isinstance(body, str) else json.dumps(body)
    return Resp(200, {"choices": [{"message": {"content": text}}], "usage": {"prompt_tokens": 1, "completion_tokens": 1}})


class FakePost:
    """Venice. `script` maps a model to a status (int), a reply body, or an exception."""

    def __init__(self, script=None, default=SEAT_REPLY):
        self.script = script or {}
        self.default = default
        self.calls = []

    def __call__(self, url, *, headers, json, timeout):
        self.calls.append(json["model"])
        answer = self.script.get(json["model"], self.default)
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, int):
            return Resp(answer, {"error": "API key USD spend limit exceeded."})
        return reply(answer)


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    (h / ".codex").mkdir(parents=True)
    (h / ".codex" / "auth.json").write_text(json.dumps({"auth_mode": "chatgpt", "OPENAI_API_KEY": None}))
    return h


CONFIG = BackupConfig(routes=ROUTES, diem_floor=0.5)
PANEL = Panel("code-review", "review", [Member("Eng Manager", "openai-gpt-53-codex", "you are eng"),
                                        Member("Inversion Founder", "claude-opus-4-7", "you are founder"),
                                        Member("Adversary", "grok-4-3", "you are adversary")],
              chair_model="openai-gpt-56-sol")
SETTINGS = Settings(default_panel="code-review", router_model="gemini-3-5-flash", chair_model="claude-opus-4-8",
                    backup={"routes": ROUTES, "diem_floor": 0.5})
RESET = "2026-11-05T00:00:00.000Z"


def chair_or_seat(call, *, seat=SEAT_REPLY, chair=CHAIR_REPLY, compare_chair=None):
    """What the fake tools answer: the chair for a chair's model, a seat for the rest."""
    chair_model = "gpt-chair" in call.argv or "claude-chair" in call.argv
    body = (compare_chair or chair) if chair_model else seat
    text = json.dumps(body)
    stream = claude_stream(text) if Path(call.argv[0]).name == "claude" else codex_stream(text)
    return SimpleNamespace(returncode=0, stdout=stream, stderr="")


def tool_run(**kw):
    return FakeRun(on_exec=lambda call: chair_or_seat(call, **kw))


def make_run(home, *, mode="auto", post=None, diem=0.0, run=None, venice_retries=0, find=None, credits=None,
             command="review", environ=None, clock=None):
    run = run or tool_run()
    env = {"PATH": "/usr/bin", "HOME": str(home), "LANG": "C.UTF-8", **(environ or {})}
    client = BackupClient(CONFIG, environ=env, run=run, find=find or (lambda name, environ: f"/opt/{name}"),
                          secrets=[KEY])
    venice = VeniceClient(KEY, post=post or FakePost(), retries=venice_retries, backoff=0)
    if credits is None:
        credits = lambda: Credits(diem, RESET)
    stderr = io.StringIO()
    return (RunClient(venice, client, CONFIG, mode=mode, credits=credits, stderr=stderr, command=command, clock=clock),
            run, stderr)


def run_cli(args, client, capsys, *, panel=PANEL):
    rc = cli.main(args, _settings=SETTINGS, _panels={panel.name: panel}, _client=client)
    captured = capsys.readouterr()
    return rc, captured.out, captured.err


def ask(fmt="md"):
    return ["ask", "should we ship?", "--panel", "code-review", "--format", fmt]


# --- the before-run trigger -------------------------------------------------------------------

def test_diem_under_the_floor_moves_the_whole_run_before_any_venice_call(home):
    post = FakePost()
    client, run, stderr = make_run(home, diem=0.49, post=post)
    assert client.prepare() is None
    assert client.complete("grok-4-3", "s", "u") == '{"stance": "approve", "headline": "fine", "findings": [], "suggestions": []}'
    assert post.calls == []
    assert len(run.execs("codex")) == 1
    assert client.trigger == "diem"


@pytest.mark.parametrize("diem", [0.0, -0.5])
def test_no_credits_means_out_of_credits_even_with_a_floor_of_zero(home, diem):
    post = FakePost()
    env = {"PATH": "/usr/bin", "HOME": str(home)}
    config = BackupConfig(routes=ROUTES, diem_floor=0.0)
    client = RunClient(VeniceClient(KEY, post=post, retries=0),
                       BackupClient(config, environ=env, run=tool_run(), find=lambda name, environ: f"/opt/{name}"),
                       config, mode="auto", credits=lambda: Credits(diem, RESET), stderr=io.StringIO())
    client.prepare()
    client.complete("grok-4-3", "s", "u")
    assert post.calls == [] and client.trigger == "diem"
    guard = CreditGuard(VeniceClient(KEY, post=post, retries=0), credits=lambda: Credits(diem, RESET), floor=0.0)
    assert guard.refusal() is not None


def test_diem_at_the_floor_stays_on_venice_and_never_touches_the_tools(home):
    post = FakePost()
    client, run, _ = make_run(home, diem=0.5, post=post)
    assert client.prepare() is None
    client.complete("grok-4-3", "s", "u")
    assert post.calls == ["grok-4-3"]
    assert run.calls == []
    assert client.trigger is None


def test_a_failed_balance_check_stays_on_venice_and_the_402_rule_still_protects(home):
    def broken():
        raise RuntimeError("balance unreachable")
    post = FakePost(script={"deepseek-v4-pro": 402})
    client, run, stderr = make_run(home, credits=broken, post=post)
    assert client.prepare() is None
    client.complete("grok-4-3", "s", "u")
    assert run.calls == [] and client.trigger is None
    client.complete("deepseek-v4-pro", "s", "u")
    assert client.trigger == "402"
    assert run.execs("codex")


def test_force_never_reads_the_balance_or_calls_venice(home):
    def must_not_run():
        raise AssertionError("balance read under force")
    post = FakePost()
    client, run, _ = make_run(home, mode="force", credits=must_not_run, post=post)
    assert client.prepare() is None
    client.complete("grok-4-3", "s", "u")
    assert post.calls == []
    assert client.trigger == "force"


def test_force_works_with_no_venice_client_at_all(home):
    run = tool_run()
    client = RunClient(None, BackupClient(CONFIG, environ={"PATH": "/x", "HOME": str(home)}, run=run,
                                          find=lambda name, environ: f"/opt/{name}"),
                       CONFIG, mode="force", stderr=io.StringIO())
    client.prepare()
    assert client.complete("claude-opus-4-7", "s", "u")


# --- the during-run trigger -------------------------------------------------------------------

def test_venice_402_is_a_typed_error_that_is_still_a_venice_error():
    client = VeniceClient(KEY, post=FakePost(script={"m": 402}), retries=2, backoff=0)
    with pytest.raises(VeniceOutOfCredits) as exc:
        client.complete("m", "s", "u")
    assert isinstance(exc.value, VeniceError)
    assert "402" in str(exc.value)


def test_402_is_not_retried():
    post = FakePost(script={"m": 402})
    with pytest.raises(VeniceOutOfCredits):
        VeniceClient(KEY, post=post, retries=2, backoff=0).complete("m", "s", "u")
    assert len(post.calls) == 1


def test_first_402_moves_the_rest_of_the_run_and_earlier_answers_stay(home):
    post = FakePost(script={"deepseek-v4-pro": 402})
    client, run, stderr = make_run(home, diem=9, post=post)
    client.prepare()
    client.complete("grok-4-3", "s", "u")                    # answered on Venice
    client.complete("deepseek-v4-pro", "s2", "u")            # 402, re-runs on the tool
    client.complete("openai-gpt-53-codex", "s3", "u")        # goes straight to the tool
    assert post.calls == ["grok-4-3", "deepseek-v4-pro"]
    assert len(run.execs("codex")) == 2
    assert client.trigger == "402"
    assert stderr.getvalue().count("\n") == 1 and "BACKUP MODE" in stderr.getvalue()


def test_seats_in_parallel_that_all_hit_402_all_rerun_and_announce_once(home):
    post = FakePost(default=402)
    client, run, stderr = make_run(home, diem=9, post=post)
    client.prepare()
    results = run_panel(PANEL, "ctx", client)
    assert [r.error for r in results] == [None, None, None]
    assert len(run.execs()) == 3
    assert stderr.getvalue().count("BACKUP MODE") == 1


@pytest.mark.parametrize("failure", [401, 403, 404, 400, 429, 500, 502, 503, 504,
                                     requests.Timeout("slow"), requests.ConnectionError("down")])
def test_other_venice_failures_keep_todays_behavior_and_never_switch(home, failure):
    post = FakePost(script={"m": failure})
    client, run, stderr = make_run(home, diem=9, post=post)
    client.prepare()
    with pytest.raises(VeniceError):
        client.complete("m", "s", "u")
    assert run.calls == []
    assert client.trigger is None
    assert stderr.getvalue() == ""


def test_credits_that_run_out_during_a_run_move_the_rest_of_it(home):
    # A key with a USD limit never sends a 402 at DIEM 0: it just starts paying. So the balance
    # is read again later in the run, not only at the start.
    now = [0.0]
    balances = iter([Credits(0.9, RESET), Credits(0.1, RESET)])
    post = FakePost()
    client, run, stderr = make_run(home, post=post, credits=lambda: next(balances), clock=lambda: now[0])
    client.prepare()
    client.complete("grok-4-3", "s", "u")                     # seats go out on Venice
    assert post.calls == ["grok-4-3"] and run.calls == []
    now[0] = 45.0
    client.complete("deepseek-v4-pro", "s2", "u")             # the chair, a minute later
    assert post.calls == ["grok-4-3"]
    assert run.execs("codex")
    assert client.trigger == "diem"
    assert "0.10" in stderr.getvalue()


def test_the_balance_is_not_read_again_on_every_call(home):
    now = [0.0]
    reads = []
    def credits():
        reads.append(now[0])
        return Credits(5.0, RESET)
    client, _, _ = make_run(home, diem=5, credits=credits, clock=lambda: now[0])
    client.prepare()
    for _ in range(5):
        client.complete("grok-4-3", "s", "u")
    assert len(reads) == 1
    now[0] = 45.0
    client.complete("grok-4-3", "s", "u")
    assert len(reads) == 2


def test_a_failed_recheck_keeps_the_run_on_venice(home):
    now = [0.0]
    calls = {"n": 0}
    def credits():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("balance unreachable")
        return Credits(5.0, RESET)
    client, run, _ = make_run(home, credits=credits, clock=lambda: now[0])
    client.prepare()
    now[0] = 45.0
    client.complete("grok-4-3", "s", "u")
    assert client.trigger is None and run.calls == []


# --- the balance read -------------------------------------------------------------------------

def test_read_credits_uses_the_run_key_and_a_short_timeout():
    seen = {}
    def get(url, headers, timeout):
        seen.update(url=url, headers=headers, timeout=timeout)
        return Resp(200, {"data": {"balances": {"DIEM": 1.25, "USD": 9.12}, "nextEpochBegins": RESET}})
    credits = backup.read_credits("the-run-key", timeout=4, get=get)
    assert (credits.diem, credits.reset) == (1.25, RESET)
    assert seen["headers"]["Authorization"] == "Bearer the-run-key"
    assert seen["timeout"] == 4
    assert seen["url"].endswith("/api_keys/rate_limits")


def test_read_credits_without_a_reset_field_still_reads_the_balance():
    get = lambda url, headers, timeout: Resp(200, {"data": {"balances": {"DIEM": 0}}})
    assert backup.read_credits("k", timeout=4, get=get) == Credits(0.0, None)


def test_the_reset_text_falls_back_to_the_next_midnight_utc(home):
    client, _, stderr = make_run(home, credits=lambda: Credits(0.0, None))
    client.prepare()
    from datetime import datetime, timedelta, timezone
    tomorrow = (datetime.now(timezone.utc) + timedelta(days=1)).strftime("%Y-%m-%d")
    assert f"{tomorrow} 00:00 UTC" in stderr.getvalue()


# --- the report -------------------------------------------------------------------------------

@pytest.mark.parametrize("fmt", ["md", "term"])
def test_banner_is_the_very_first_thing_in_both_formats(home, capsys, fmt):
    client, _, _ = make_run(home)
    rc, out, _ = run_cli(ask(fmt), client, capsys)
    assert rc == 0
    assert "BACKUP MODE" in out.splitlines()[0]
    banner_end = out.index("## Council")
    banner = out[:banner_end]
    assert "2026-11-05 00:00 UTC" in banner
    assert "credits" in banner.lower()
    assert "cover 2 model families" in banner and "rather than" in banner and "5" in banner
    assert "Eng Manager: codex/gpt-seat" in banner
    assert "Inversion Founder: claude/claude-seat" in banner
    assert "Adversary: codex/gpt-seat" in banner
    assert "Chair: codex/gpt-chair" in banner


def test_banner_names_the_families_this_run_really_used(home, capsys):
    only_codex = Panel("code-review", "review", [Member("Eng", "openai-gpt-53-codex", "x"),
                                                 Member("Adversary", "grok-4-3", "y")],
                       chair_model="openai-gpt-56-sol")
    client, _, _ = make_run(home)
    _, out, _ = run_cli(ask(), client, capsys, panel=only_codex)
    assert "Answers in this run came from 1: OpenAI" in out[:out.index("## Council")]


def test_the_family_count_leaves_out_failed_calls_and_the_router(home, capsys):
    def only_codex_answers(call):
        if Path(call.argv[0]).name == "claude":
            return SimpleNamespace(returncode=1, stdout="", stderr="claude is down")
        return chair_or_seat(call, seat={**SEAT_REPLY, "panel": "code-review"})
    client, _, _ = make_run(home, run=FakeRun(on_exec=only_codex_answers))
    rc, out, _ = run_cli(ask(), client, capsys)
    assert rc == 0
    assert "Answers in this run came from 1: OpenAI" in out[:out.index("## Council")]


def test_the_family_count_includes_seats_that_answered_on_venice(home, capsys):
    post = FakePost(script={"claude-opus-4-7": 402})
    client, _, _ = make_run(home, diem=9, post=post)
    original = cli.run_panel
    cli.run_panel = lambda panel, context, c, **kw: run_panel(panel, context, c, **{**kw, "max_workers": 1})
    try:
        _, out, _ = run_cli(ask(), client, capsys)
    finally:
        cli.run_panel = original
    assert "came from 2: OpenAI, Claude" in out[:out.index("## Council")]


def test_the_family_count_names_a_venice_only_family(home, capsys):
    panel = Panel("code-review", "review", [Member("Sec", "deepseek-v4-pro", "x"), Member("Adversary", "grok-4-3", "y"),
                                            Member("Eng", "openai-gpt-53-codex", "z")], chair_model="openai-gpt-56-sol")
    post = FakePost(script={"openai-gpt-53-codex": 402})
    client, _, _ = make_run(home, diem=9, post=post)
    original = cli.run_panel
    cli.run_panel = lambda p, context, c, **kw: run_panel(p, context, c, **{**kw, "max_workers": 1})
    try:
        _, out, _ = run_cli(ask(), client, capsys, panel=panel)
    finally:
        cli.run_panel = original
    assert "came from 3: DeepSeek, xAI, OpenAI" in out[:out.index("## Council")]


def test_mid_run_banner_says_which_seat_ran_where_and_why(home, capsys):
    post = FakePost(script={"claude-opus-4-7": 402})
    # one worker keeps seat order, so the first seat answers before the 402
    client, _, _ = make_run(home, diem=9, post=post)
    def serial_run_panel(panel, context, c, **kw):
        kw["max_workers"] = 1
        return run_panel(panel, context, c, **kw)
    original = cli.run_panel
    cli.run_panel = serial_run_panel
    try:
        rc, out, err = run_cli(ask(), client, capsys)
    finally:
        cli.run_panel = original
    banner = out[:out.index("## Council")]
    assert rc == 0
    assert "402" in banner
    assert "Eng Manager: Venice/openai-gpt-53-codex" in banner
    assert "Inversion Founder: claude/claude-seat" in banner
    assert "Adversary: codex/gpt-seat" in banner


def test_raw_panel_and_the_chair_see_the_model_that_really_answered(home, capsys):
    client, run, _ = make_run(home)
    _, out, _ = run_cli(ask(), client, capsys)
    assert "#### Eng Manager · codex/gpt-seat" in out
    assert "#### Eng Manager · openai-gpt-53-codex" not in out
    chair_call = [c for c in run.execs() if "gpt-chair" in c.argv][0]
    assert "Eng Manager (codex/gpt-seat)" in chair_call.input
    assert "openai-gpt-53-codex" not in chair_call.input


def test_router_row_appears_when_the_router_ran(home, capsys):
    panels = {"code-review": PANEL}
    client, run, _ = make_run(home)
    run.on_exec = lambda call: chair_or_seat(call, seat={**SEAT_REPLY, "panel": "code-review"})
    rc = cli.main(["ask", "q", "--format", "md"], _settings=SETTINGS, _panels=panels, _client=client)
    out = capsys.readouterr().out
    assert rc == 0
    assert "Router: codex/gpt-seat" in out[:out.index("## Council")]


def test_a_run_that_stays_on_venice_prints_exactly_what_it_prints_with_backup_off(home, capsys):
    client, _, _ = make_run(home, diem=9, post=FakePost(default=SEAT_REPLY, script={"openai-gpt-56-sol": CHAIR_REPLY}))
    _, with_auto, err_auto = run_cli(ask(), client, capsys)
    off = VeniceClient(KEY, post=FakePost(default=SEAT_REPLY, script={"openai-gpt-56-sol": CHAIR_REPLY}), retries=0)
    _, with_off, err_off = run_cli(ask(), off, capsys)
    assert with_auto == with_off
    assert err_auto == err_off == ""


# --- exit codes -------------------------------------------------------------------------------

def failing(kind):
    """A tool run where `kind` of the calls fail: all, chair, two (of three seats), one."""
    count = {"seat": 0}
    def on_exec(call):
        is_chair = "gpt-chair" in call.argv or "claude-chair" in call.argv
        bad = SimpleNamespace(returncode=1, stdout="", stderr="tool exploded")
        if kind == "chair" and is_chair:
            return bad
        if not is_chair:
            count["seat"] += 1
            if kind == "seats" or (kind == "two" and count["seat"] <= 2) or (kind == "one" and count["seat"] == 1):
                return bad
        return chair_or_seat(call)
    return FakeRun(on_exec=on_exec)


@pytest.mark.parametrize("kind,word", [("seats", "seats failed"), ("chair", "chair failed"), ("two", "2 of 3 seats failed")])
def test_a_failed_backup_run_is_never_a_pass(home, capsys, kind, word):
    client, _, _ = make_run(home, run=failing(kind))
    rc, out, err = run_cli(ask(), client, capsys)
    assert rc not in (0, None)
    assert "BACKUP MODE" in out.splitlines()[0]         # the partial report is still shown
    banner = out[:out.index("## Council")]
    assert "INCOMPLETE" in banner and word in banner    # a saved report must say so without stderr
    assert "counts as a council review" not in banner
    assert word in err
    assert "COUNCIL_BACKUP=off" in err
    assert "not a passed review" in err


def test_one_failed_seat_of_three_is_reported_and_still_completes(home, capsys):
    client, _, _ = make_run(home, run=failing("one"))
    rc, out, _ = run_cli(ask(), client, capsys)
    assert rc == 0
    banner = out[:out.index("## Council")]
    assert "(failed)" in banner and "INCOMPLETE" not in banner
    assert "counts as a council review" in banner


def test_both_tools_missing_fails_before_any_seat_with_the_deliberate_way_to_pay(home, capsys):
    post = FakePost()
    client, run, _ = make_run(home, find=lambda name, environ: None, post=post)
    rc, out, err = run_cli(ask(), client, capsys)
    assert rc not in (0, None)
    assert post.calls == [] and run.calls == []
    assert "COUNCIL_BACKUP=off" in err
    assert "## Council" not in out


def test_402_with_both_tools_missing_never_falls_back_to_paying(home, capsys):
    post = FakePost(default=402)
    client, run, _ = make_run(home, diem=9, find=lambda name, environ: None, post=post)
    rc, _, err = run_cli(ask(), client, capsys)
    assert rc not in (0, None)
    assert "COUNCIL_BACKUP=off" in err


def test_venice_only_failures_keep_todays_exit_status(home, capsys):
    client, run, _ = make_run(home, diem=9, post=FakePost(default=500))
    rc, out, _ = run_cli(ask(), client, capsys)
    assert rc == 0
    assert run.calls == []
    assert "BACKUP MODE" not in out


def _compare_args(tmp_path):
    (tmp_path / "a.py").write_text("print(1)\n")
    (tmp_path / "b.py").write_text("print(2)\n")
    return ["compare", "--task", "t", str(tmp_path / "a.py"), str(tmp_path / "b.py"), "--panel", "code-review"]


def test_compare_shows_the_banner_and_relabels_votes(home, capsys, tmp_path):
    run = FakeRun(on_exec=lambda call: chair_or_seat(call, seat=VOTE_REPLY, compare_chair=COMPARE_CHAIR_REPLY))
    client, _, _ = make_run(home, run=run, command="compare")
    rc, out, _ = run_cli(_compare_args(tmp_path), client, capsys)
    assert rc == 0
    assert "BACKUP MODE" in out.splitlines()[0]
    assert "Chair: codex/gpt-chair" in out
    assert "#### Eng Manager · codex/gpt-seat — pick: a.py" in out


def test_compare_chair_sees_the_model_that_really_voted(home, capsys, tmp_path):
    run = FakeRun(on_exec=lambda call: chair_or_seat(call, seat=VOTE_REPLY, compare_chair=COMPARE_CHAIR_REPLY))
    client, _, _ = make_run(home, run=run, command="compare")
    run_cli(_compare_args(tmp_path), client, capsys)
    chair_call = [c for c in run.execs() if "gpt-chair" in c.argv][0]
    assert "Eng Manager (codex/gpt-seat)" in chair_call.input
    assert "openai-gpt-53-codex" not in chair_call.input


def test_compare_all_votes_failed_is_not_a_pass(home, capsys, tmp_path):
    client, _, _ = make_run(home, run=failing("seats"), command="compare")
    rc, _, err = run_cli(_compare_args(tmp_path), client, capsys)
    assert rc not in (0, None)
    assert "seats failed" in err


def test_compare_chair_with_no_winner_is_not_a_pass(home, capsys, tmp_path):
    run = FakeRun(on_exec=lambda call: chair_or_seat(call, seat=VOTE_REPLY, compare_chair={"rationale": "no idea"}))
    client, _, _ = make_run(home, run=run, command="compare")
    rc, _, err = run_cli(_compare_args(tmp_path), client, capsys)
    assert rc not in (0, None)
    assert "chair failed" in err


def test_compare_with_both_tools_missing_stops_before_any_vote(home, capsys, tmp_path):
    post = FakePost()
    client, run, _ = make_run(home, find=lambda name, environ: None, post=post, command="compare")
    rc, _, _ = run_cli(_compare_args(tmp_path), client, capsys)
    assert rc not in (0, None)
    assert post.calls == []


# --- the log ----------------------------------------------------------------------------------

def _log_rows():
    path = Path(os.environ["COUNCIL_BACKUP_LOG"])
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def test_log_row_records_the_run_and_nothing_private(home, capsys):
    client, _, _ = make_run(home)
    run_cli(["ask", f"SECRET-PROMPT-TEXT {KEY}", "--panel", "code-review", "--format", "md"], client, capsys)
    rows = _log_rows()
    assert len(rows) == 1
    row = rows[0]
    assert row["trigger"] == "diem"
    assert row["panel"] == "code-review"
    assert row["seats"] == {"Eng Manager": "codex/gpt-seat", "Inversion Founder": "claude/claude-seat",
                            "Adversary": "codex/gpt-seat", "Chair": "codex/gpt-chair"}
    assert (row["ok"], row["failed"]) == (3, 0)
    assert row["time"]
    text = Path(os.environ["COUNCIL_BACKUP_LOG"]).read_text()
    assert "SECRET-PROMPT-TEXT" not in text and KEY not in text
    assert "you are eng" not in text                  # nor the persona, which is prompt text too


@pytest.mark.parametrize("mode,diem,script,trigger", [("force", 9, {}, "force"), ("auto", 0, {}, "diem"),
                                                      ("auto", 9, {"claude-opus-4-7": 402}, "402")])
def test_log_row_names_each_trigger(home, capsys, mode, diem, script, trigger):
    client, _, _ = make_run(home, mode=mode, diem=diem, post=FakePost(script=script))
    run_cli(ask(), client, capsys)
    assert _log_rows()[-1]["trigger"] == trigger


def test_failed_counts_reach_the_log(home, capsys):
    client, _, _ = make_run(home, run=failing("two"))
    run_cli(ask(), client, capsys)
    row = _log_rows()[-1]
    assert (row["ok"], row["failed"]) == (1, 2)


def test_a_run_that_stayed_on_venice_writes_no_row(home, capsys):
    client, _, _ = make_run(home, diem=9)
    run_cli(ask(), client, capsys)
    assert _log_rows() == []


def test_a_log_that_cannot_be_written_never_changes_the_result(home, capsys, monkeypatch, tmp_path):
    (tmp_path / "file").write_text("x")
    monkeypatch.setenv("COUNCIL_BACKUP_LOG", str(tmp_path / "file" / "nope.jsonl"))
    client, _, _ = make_run(home)
    rc, out, err = run_cli(ask(), client, capsys)
    assert rc == 0 and "BACKUP MODE" in out
    assert "could not write the backup log" in err


def test_backup_calls_write_nothing_to_the_venice_ledger(home, capsys, monkeypatch, tmp_path):
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    client, _, _ = make_run(home)
    rc, _, _ = run_cli(ask(), client, capsys)
    assert rc == 0
    if db.exists():
        with sqlite3.connect(db) as conn:
            assert conn.execute("SELECT COUNT(*) FROM usage").fetchone()[0] == 0


def test_mid_run_ledger_holds_only_the_venice_calls(home, capsys, monkeypatch, tmp_path):
    db = tmp_path / "ledger.db"
    monkeypatch.setenv("VENICE_USAGE_DB", str(db))
    post = FakePost(script={"claude-opus-4-7": 402})
    client, _, _ = make_run(home, diem=9, post=post)
    original = cli.run_panel
    cli.run_panel = lambda panel, context, c, **kw: run_panel(panel, context, c, **{**kw, "max_workers": 1})
    try:
        run_cli(ask(), client, capsys)
    finally:
        cli.run_panel = original
    with sqlite3.connect(db) as conn:
        models = [r[0] for r in conn.execute("SELECT model FROM usage")]
    assert models == ["openai-gpt-53-codex"]


# --- the real entry point: modes from the environment ---------------------------------------

PANELS_TOML = """
[settings]
default_panel = "code-review"
router_model = "gemini-3-5-flash"
chair_model = "claude-opus-4-8"

[settings.backup]
diem_floor = 0.5
balance_timeout = 3

[[settings.backup.routes]]
match = "openai-gpt-56-sol"
tool = "codex"
model = "gpt-chair"
[[settings.backup.routes]]
match = "*"
tool = "codex"
model = "gpt-seat"

[panels.code-review]
description = "review"
chair_model = "openai-gpt-56-sol"
[[panels.code-review.members]]
name = "Adversary"
model = "grok-4-3"
system = "attack"
[[panels.code-review.members]]
name = "Eng"
model = "openai-gpt-53-codex"
system = "build"

[panels.red-team]
description = "break"
[[panels.red-team.members]]
name = "Adversary"
model = "grok-4-3"
system = "attack"
"""


class World:
    """The real cli.main with only the outermost edges replaced."""

    def __init__(self, monkeypatch, tmp_path, home, *, diem=9.0, post=None, balance_error=None):
        self.panels = tmp_path / "panels.toml"
        self.panels.write_text(PANELS_TOML)
        self.post = post or FakePost(script={"openai-gpt-56-sol": CHAIR_REPLY})
        self.balance_calls = []
        self.run = tool_run()
        def fake_get(url, headers, timeout):
            self.balance_calls.append({"headers": headers, "timeout": timeout})
            if balance_error:
                raise balance_error
            return Resp(200, {"data": {"balances": {"DIEM": diem}, "nextEpochBegins": RESET}})
        monkeypatch.setattr(requests, "get", fake_get)
        monkeypatch.setattr(requests, "post", self.post)
        monkeypatch.setattr(backup, "run_child", self.run)
        monkeypatch.setattr(backup, "find_tool", lambda name, environ: f"/opt/{name}")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("VENICE_COUNCIL_KEY", KEY)
        monkeypatch.delenv("VENICE_API_KEY", raising=False)
        monkeypatch.delenv("COUNCIL_BACKUP", raising=False)

    def main(self, *args):
        return cli.main([*args, "--panels", str(self.panels)])


def test_cli_defaults_to_auto_when_the_variable_is_unset(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home, diem=0.0)
    rc = world.main("ask", "q", "--panel", "code-review", "--format", "md")
    out = capsys.readouterr().out
    assert rc == 0
    assert "BACKUP MODE" in out.splitlines()[0]
    assert world.post.calls == []
    assert world.balance_calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert world.balance_calls[0]["timeout"] == 3


def test_cli_auto_with_credits_stays_on_venice(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home, diem=5.0)
    rc = world.main("ask", "q", "--panel", "code-review", "--format", "md")
    assert rc == 0
    assert "BACKUP MODE" not in capsys.readouterr().out
    assert world.post.calls and world.run.calls == []


def test_cli_off_is_venice_only_and_reads_no_balance(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home, diem=0.0, post=FakePost(default=402))
    monkeypatch.setenv("COUNCIL_BACKUP", "off")
    rc = world.main("ask", "q", "--panel", "code-review", "--format", "md")
    out = capsys.readouterr().out
    assert rc == 0                                   # exactly today's result: seats errored, exit 0
    assert world.balance_calls == []
    assert world.run.calls == []
    assert "BACKUP MODE" not in out
    assert "402" in out


def test_cli_force_needs_no_venice_key(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home)
    monkeypatch.delenv("VENICE_COUNCIL_KEY")
    monkeypatch.setenv("COUNCIL_BACKUP", "force")
    rc = world.main("ask", "q", "--panel", "code-review", "--format", "md")
    assert rc == 0
    assert world.post.calls == [] and world.balance_calls == []
    assert "COUNCIL_BACKUP=force" in capsys.readouterr().out


def test_cli_auto_without_a_key_still_refuses_as_it_does_today(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home)
    monkeypatch.delenv("VENICE_COUNCIL_KEY")
    with pytest.raises(SystemExit) as exc:
        world.main("ask", "q", "--panel", "code-review")
    assert exc.value.code == 2


def test_cli_rejects_an_unknown_mode(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home)
    monkeypatch.setenv("COUNCIL_BACKUP", "sometimes")
    with pytest.raises(SystemExit) as exc:
        world.main("ask", "q", "--panel", "code-review")
    assert exc.value.code == 2
    assert "COUNCIL_BACKUP" in capsys.readouterr().err


def test_cli_scrubs_the_venice_key_from_a_backup_prompt(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home, diem=0.0)
    world.main("ask", f"my key is {KEY}", "--panel", "code-review", "--format", "md")
    assert world.run.execs()
    assert all(KEY not in call.input for call in world.run.execs())


def test_force_mode_scrubs_the_venice_key_though_it_never_loads_a_client(monkeypatch, tmp_path, home, capsys):
    # get_api_key reads only these environment names, and they all look like secrets.
    world = World(monkeypatch, tmp_path, home)
    monkeypatch.setenv("COUNCIL_BACKUP", "force")
    monkeypatch.setenv("VENICE_API_KEY", "generic-venice-key-999")
    world.main("ask", f"keys {KEY} generic-venice-key-999", "--panel", "code-review", "--format", "md")
    assert world.run.execs()
    for call in world.run.execs():
        assert KEY not in call.input and "generic-venice-key-999" not in call.input


def test_panels_command_ignores_the_variable_entirely(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home)
    monkeypatch.setenv("COUNCIL_BACKUP", "sometimes")
    assert cli.main(["panels", "--panels", str(world.panels)]) == 0
    assert world.balance_calls == []


def test_an_empty_review_reads_no_balance_and_needs_no_tools(monkeypatch, tmp_path, home, capsys):
    world = World(monkeypatch, tmp_path, home, diem=0.0)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert world.main("review", "-") == 0
    assert world.balance_calls == [] and world.run.calls == []


# --- sweep never uses backup ------------------------------------------------------------------

def _sweep_args(tmp_path):
    target = tmp_path / "code"
    target.mkdir(exist_ok=True)
    (target / "a.py").write_text("print(1)\n")
    return ("sweep", str(target), "--panel", "red-team")


@pytest.mark.parametrize("mode", [None, "auto", "force", "off"])
def test_sweep_refuses_when_credits_are_out_in_every_mode(monkeypatch, tmp_path, home, capsys, mode):
    world = World(monkeypatch, tmp_path, home, diem=0.0)
    if mode:
        monkeypatch.setenv("COUNCIL_BACKUP", mode)
    rc = world.main(*_sweep_args(tmp_path))
    captured = capsys.readouterr()
    assert rc not in (0, None)
    assert "2026-11-05 00:00 UTC" in captured.err
    assert world.post.calls == [] and world.run.calls == []


@pytest.mark.parametrize("mode", [None, "force", "off"])
def test_sweep_with_credits_runs_on_venice_only(monkeypatch, tmp_path, home, capsys, mode):
    world = World(monkeypatch, tmp_path, home, diem=5.0)
    if mode:
        monkeypatch.setenv("COUNCIL_BACKUP", mode)
    assert world.main(*_sweep_args(tmp_path)) == 0
    assert world.post.calls and world.run.calls == []


def test_sweep_with_an_unreadable_balance_does_not_start(monkeypatch, tmp_path, home, capsys):
    # Unlike ask and review there is no backup to protect it, and one sweep is dozens of panel
    # runs: on a key with a USD limit "probably fine" could be a large bill.
    world = World(monkeypatch, tmp_path, home, balance_error=requests.ConnectionError("down"))
    rc = world.main(*_sweep_args(tmp_path))
    assert rc not in (0, None)
    assert "balance" in capsys.readouterr().err
    assert world.post.calls == [] and world.run.calls == []


def test_a_sweep_stops_when_a_later_balance_read_shows_credits_are_gone():
    now = [0.0]
    balances = iter([Credits(5.0, RESET), Credits(0.1, RESET)])
    post = FakePost()
    guard = CreditGuard(VeniceClient(KEY, post=post, retries=0), credits=lambda: next(balances), floor=0.5,
                        clock=lambda: now[0])
    assert guard.refusal() is None
    guard.complete("m", "s", "u")
    now[0] = 45.0
    with pytest.raises(VeniceOutOfCredits):
        guard.complete("m", "s", "u")
    assert guard.exhausted is True
    assert len(post.calls) == 1
    assert "2026-11-05 00:00 UTC" in guard.exhausted_message()


@pytest.mark.parametrize("second_read", [Credits(0.1, RESET), RuntimeError("unreachable")])
def test_no_sweep_call_slips_past_a_balance_read_in_progress(second_read):
    # While one worker waits on the balance, another must wait too: if the answer is "out",
    # a call that went ahead has already been paid for.
    now = [0.0]
    reading, release = threading.Event(), threading.Event()
    reads = iter([Credits(5.0, RESET), second_read])
    def credits():
        answer = next(reads)
        if answer is second_read:
            reading.set()
            release.wait(5)
        if isinstance(answer, Exception):
            raise answer
        return answer
    post = FakePost()
    guard = CreditGuard(VeniceClient(KEY, post=post, retries=0), credits=credits, floor=0.5, clock=lambda: now[0])
    assert guard.refusal() is None
    now[0] = 45.0
    errors = []
    def call():
        try:
            guard.complete("m", "s", "u")
        except VeniceOutOfCredits as exc:
            errors.append(exc)
    first = threading.Thread(target=call)
    first.start()
    assert reading.wait(5)
    second = threading.Thread(target=call)
    second.start()
    time.sleep(0.15)
    assert post.calls == []                              # the second worker is waiting too
    release.set()
    first.join(5)
    second.join(5)
    assert post.calls == [] and len(errors) == 2


def test_no_review_call_slips_past_a_balance_read_in_progress(home):
    now = [0.0]
    reading, release = threading.Event(), threading.Event()
    reads = iter([Credits(5.0, RESET), Credits(0.1, RESET)])
    def credits():
        answer = next(reads)
        if answer.diem < 1:
            reading.set()
            release.wait(5)
        return answer
    post = FakePost()
    client, run, _ = make_run(home, credits=credits, post=post, clock=lambda: now[0])
    client.prepare()
    now[0] = 45.0
    first = threading.Thread(target=client.complete, args=("grok-4-3", "s", "u"))
    first.start()
    assert reading.wait(5)
    second = threading.Thread(target=client.complete, args=("deepseek-v4-pro", "s2", "u"))
    second.start()
    time.sleep(0.15)
    assert post.calls == []
    release.set()
    first.join(5)
    second.join(5)
    assert post.calls == []
    assert len(run.execs("codex")) == 2


@pytest.mark.parametrize("value", ["NaN", "Infinity", float("inf")])
def test_a_balance_that_is_not_a_number_counts_as_unreadable(value):
    from diem.balance import BalanceUnavailable
    get = lambda url, headers, timeout: Resp(200, {"data": {"balances": {"DIEM": value}}})
    with pytest.raises(BalanceUnavailable):
        backup.read_credits("k", timeout=4, get=get)


def test_a_sweep_call_already_admitted_when_another_gets_402_may_finish_and_the_next_may_not():
    # The boundary the docs promise: calls in flight finish, and no new call starts after the 402.
    in_flight, finish = threading.Event(), threading.Event()
    calls = []
    def post(url, *, headers, json, timeout):
        calls.append(json["model"])
        if json["model"] == "slow":
            in_flight.set()
            finish.wait(5)
            return reply(SEAT_REPLY)
        return Resp(402, {"error": "spend limit"})
    guard = CreditGuard(VeniceClient(KEY, post=post, retries=0), credits=lambda: Credits(5.0, RESET), floor=0.5)
    assert guard.refusal() is None
    answers = []
    slow = threading.Thread(target=lambda: answers.append(guard.complete("slow", "s", "u")))
    slow.start()
    assert in_flight.wait(5)
    with pytest.raises(VeniceOutOfCredits):
        guard.complete("broke", "s", "u")
    finish.set()
    slow.join(5)
    assert answers                                       # the admitted call finished
    with pytest.raises(VeniceOutOfCredits):
        guard.complete("later", "s", "u")
    assert calls == ["slow", "broke"]                    # nothing started after the 402


def test_a_sweep_stops_when_a_later_balance_read_fails():
    # The rule at the start holds throughout: no confirmed credits, no more calls.
    now = [0.0]
    calls = {"n": 0}
    def credits():
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("balance unreachable")
        return Credits(5.0, RESET)
    post = FakePost()
    guard = CreditGuard(VeniceClient(KEY, post=post, retries=0), credits=credits, floor=0.5, clock=lambda: now[0])
    assert guard.refusal() is None
    now[0] = 45.0
    with pytest.raises(VeniceOutOfCredits):
        guard.complete("m", "s", "u")
    assert post.calls == []
    assert guard.exhausted is True
    assert "could not read" in guard.exhausted_message()


# --- where backup must never run --------------------------------------------------------------

def test_run_pr_review_stays_venice_only_and_fails_closed_on_402(monkeypatch, home):
    monkeypatch.setenv("COUNCIL_BACKUP", "force")
    def must_not_run(*a, **kw):
        raise AssertionError("a library entry point reached the backup tools")
    monkeypatch.setattr(backup, "run_child", must_not_run)
    post = FakePost(default=402)
    diff = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -1 +1 @@\n-a\n+b\n"
    body, blocking, unavailable = run_pr_review(diff, {"code-review": PANEL}, VeniceClient(KEY, post=post, retries=0),
                                                chair_model="openai-gpt-56-sol")
    assert unavailable is True
    assert "BACKUP MODE" not in body
    assert post.calls


def test_library_modules_do_not_import_the_backup_module():
    code = ("import sys, council.review, council.engine, council.synthesize, council.router, council.sweep, "
            "council.compare, council.venice, backlogrun.cli, diem.runners; "
            "assert 'council.backup' not in sys.modules, 'imported'")
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                          cwd=Path(__file__).parents[1])
    assert done.returncode == 0, done.stderr[-400:]


def test_the_diem_drain_turns_backup_off_for_the_council_it_shells_out_to(tmp_path, monkeypatch):
    from diem.cli import _drain_env
    monkeypatch.delenv("COUNCIL_BACKUP")              # the suite's own guard must not stand in for the code
    env_file = tmp_path / ".env"
    env_file.write_text("VENICE_API_KEY=abc\n")
    assert _drain_env("key", env_file)["COUNCIL_BACKUP"] == "off"


def test_backlog_run_sessions_get_backup_off(tmp_path):
    from backlogrun.cli import scrubbed_env
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert scrubbed_env(str(tmp_path), [])["COUNCIL_BACKUP"] == "off"


def _stub(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(0o755)


REPO = Path(__file__).parents[1]


def test_security_sweep_script_runs_council_with_backup_off(tmp_path):
    root = tmp_path / "repo"
    (root / "council" / "scripts").mkdir(parents=True)
    script = root / "council" / "scripts" / "security-sweep.sh"
    script.write_bytes((REPO / "council" / "scripts" / "security-sweep.sh").read_bytes())
    script.chmod(0o755)
    seen = tmp_path / "seen.txt"
    home = tmp_path / "scripthome"
    _stub(home / ".local" / "bin" / "council", f'echo "$COUNCIL_BACKUP" > {seen}\nprintf "### Summary\\nok\\n"\n')
    _stub(root / "bin" / "tg-send", "exit 0\n")       # the copied script must never reach the real one
    env = {"PATH": "/usr/bin:/bin", "HOME": str(home), "VENICE_API_KEY": "k"}
    done = subprocess.run([str(script)], capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr[-300:]
    assert seen.read_text().strip() == "off"


def test_parallel_attempts_script_runs_council_with_backup_off(tmp_path):
    seen = tmp_path / "seen.txt"
    bin_dir = tmp_path / "bin"
    _stub(bin_dir / "council", f'echo "$COUNCIL_BACKUP" > {seen}\n')
    _stub(bin_dir / "python3", "cat > /dev/null\nexit 0\n")   # candidate generation would call Venice
    env = {"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(tmp_path), "MODELS": "m1,m2"}
    done = subprocess.run([str(REPO / "council" / "scripts" / "parallel-attempts.sh"), "a task"],
                          capture_output=True, text=True, timeout=60, env=env)
    assert done.returncode == 0, done.stderr[-300:]
    assert seen.read_text().strip() == "off"


# --- the venice-delegate route ----------------------------------------------------------------

def test_venice_delegate_route_still_finds_both_tools(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("venice_delegate", REPO / "setup/delegate-venice/venice_delegate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    bin_dir = tmp_path / "bin"
    for name in ("claude", "codex", "council"):
        _stub(bin_dir / name, "exit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("COUNCIL_BACKUP", "force")
    captured = {}
    def fake_run(argv, env):
        captured.update(env)
        return SimpleNamespace(returncode=0)
    assert mod.council_review(["--diff"], "review-key", run=fake_run) == 0
    # What the wrapper hands the council child is what the backup client sees.
    client = BackupClient(BackupConfig(routes=ROUTES), environ=captured)
    assert client.available_tools() == ["claude", "codex"]
    assert captured["COUNCIL_BACKUP"] == "force"
    # ... and only the key the run actually uses, which is also the one the balance read uses.
    assert captured["VENICE_COUNCIL_KEY"] == "review-key"


# --- the real packaged seat map ---------------------------------------------------------------

def _packaged():
    settings, panels = load_panels(REPO / "council" / "panels.toml")
    return settings, panels, BackupConfig.from_settings(settings)


def test_every_panel_seat_chair_and_router_has_a_route_and_the_rules_hold():
    settings, panels, config = _packaged()
    assert panels
    for name, panel in panels.items():
        assert panel.members, name
        for member in panel.members:
            route = config.route(member.model)
            assert route is not None, f"{name}/{member.name}"
            if "adversary" in member.name.lower():
                assert route.tool != "claude", f"{name}/{member.name} must stay non-Claude"
            expected = "claude" if member.model.startswith("claude-") else "codex"
            assert route.tool == expected, f"{name}/{member.name}"
        chair = config.route(panel.chair_model or settings.chair_model)
        assert chair is not None, name
        assert chair.tool == ("codex" if name == "code-review" else "claude"), name
    assert config.route(settings.router_model) is not None


def test_chairs_are_a_stronger_model_than_the_seats_and_the_router_is_the_cheapest():
    settings, panels, config = _packaged()
    codex_seat = config.route("deepseek-v4-pro")
    claude_seat = config.route("claude-opus-4-7")
    assert config.route("openai-gpt-56-sol").model != codex_seat.model
    assert config.route(settings.chair_model).model != claude_seat.model
    router = config.route(settings.router_model)
    assert router.model not in (codex_seat.model, config.route("openai-gpt-56-sol").model)
    assert router.effort == "low"


def test_a_user_panels_file_without_a_backup_table_keeps_the_packaged_routes(tmp_path):
    custom = tmp_path / "panels.toml"
    custom.write_text('[settings]\nchair_model = "c"\n\n[panels.x]\ndescription = "d"\n'
                      '[[panels.x.members]]\nname = "A"\nmodel = "m"\nsystem = "s"\n')
    settings, _ = load_panels(custom)
    assert BackupConfig.from_settings(settings).route("anything") is not None


# --- children do not outlive a killed council -------------------------------------------------

@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_a_signal_to_council_takes_its_children_with_it(tmp_path, sig):
    marker = tmp_path / "child.pid"
    script = tmp_path / "parent.py"
    script.write_text(f"""
import sys, threading, time
sys.path.insert(0, {str(REPO)!r})
from council import backup
tool = {str(tmp_path / 'tool.py')!r}
with backup.child_cleanup():
    thread = threading.Thread(target=lambda: backup.run_child([sys.executable, tool], input="", env={{"PATH": "/usr/bin:/bin"}}, cwd="/", timeout=120), daemon=False)
    thread.start()
    while thread.is_alive():
        time.sleep(0.05)
""")
    (tmp_path / "tool.py").write_text(f"import os, time\nopen({str(marker)!r}, 'w').write(str(os.getpid()))\ntime.sleep(120)\n")
    parent = subprocess.Popen([sys.executable, str(script)], stderr=subprocess.DEVNULL)
    deadline = time.time() + 15
    while not marker.exists() and time.time() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    time.sleep(0.2)
    parent.send_signal(sig)
    parent.wait(timeout=15)
    pid = int(marker.read_text())
    deadline = time.time() + 5
    while time.time() < deadline and _alive(pid):
        time.sleep(0.05)
    assert not _alive(pid)
