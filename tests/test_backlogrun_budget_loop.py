"""backlog-run work: the budget-driven nightly loop, the Opus 5.5 high default and the
short-SHA repair of session-reported validations.

The loop keeps taking planned items while the DIEM balance is at least the floor, an item
can finish before the UTC stop time, and the nightly Claude-session cap is not reached."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backlogrun.cli as br
from backlogrun import readiness
from tests.test_backlogrun import item, load_items, work_args, world  # noqa: F401  (fixture)
from tests.test_backlogrun import clean_reviewer


def at(hh: int, mm: int = 0) -> datetime:
    return datetime(2026, 9, 25, hh, mm, tzinfo=timezone.utc)


def three(world):
    return world.build([item("2026-01-01-a"), item("2026-01-02-b", created="2026-01-02"),
                        item("2026-01-03-c", created="2026-01-03")])


def fake_work(sessions):
    def work_one(cfg, p, **k):
        sessions.append(p.item["id"])
        return {"id": p.item["id"], "status": "open", "note": "fake", "council": ""}
    return work_one


# ----------------------------------------------------------------------------- the rule


def test_defaults_are_the_budget_loop_and_opus_high():
    cfg = br.Config()
    assert (cfg.max_items, cfg.diem_floor, cfg.stop_utc, cfg.item_estimate) == (10, 1.5, "23:30", 1200)
    assert (cfg.model, cfg.effort) == ("claude-opus-5-5", "high")
    assert cfg.item_timeout == 3600       # the kill limit is not the estimate


def test_budget_stop_is_none_while_all_three_hold():
    cfg = br.Config()
    stop = br.stop_moment(cfg, at(18))
    assert br.budget_stop(cfg, worked=3, now=at(22), stop=stop, balance=lambda: 9.0) is None


def test_budget_stop_on_low_balance():
    cfg = br.Config()
    why = br.budget_stop(cfg, worked=0, now=at(18), stop=br.stop_moment(cfg, at(18)), balance=lambda: 1.2)
    assert why and "DIEM balance 1.20" in why and "1.5" in why


def test_budget_stop_before_the_utc_stop_time():
    cfg = br.Config()
    stop = br.stop_moment(cfg, at(18))
    assert stop == at(23, 30)
    assert br.budget_stop(cfg, worked=0, now=at(23, 10), stop=stop, balance=lambda: 9.0) is None
    why = br.budget_stop(cfg, worked=0, now=at(23, 11), stop=stop, balance=lambda: 9.0)
    assert why and "23:30 UTC" in why and "20 min" in why


def test_budget_stop_at_the_session_cap_without_reading_the_balance():
    cfg = br.Config(max_items=4)
    asked = []
    why = br.budget_stop(cfg, worked=4, now=at(18), stop=None, balance=lambda: asked.append(1) or 9.0)
    assert why and "cap" in why and "4" in why and asked == []


def test_unreadable_balance_falls_back_to_the_cap():
    cfg = br.Config()
    assert br.budget_stop(cfg, worked=9, now=at(18), stop=None, balance=lambda: None) is None
    assert "cap" in br.budget_stop(cfg, worked=10, now=at(18), stop=None, balance=lambda: None)


def test_stop_time_is_fixed_at_the_run_start_so_a_review_that_waits_past_midnight_ends_the_night():
    cfg = br.Config()
    stop = br.stop_moment(cfg, at(18))
    after_reset = at(0, 5) + timedelta(days=1)
    assert "23:30" in br.budget_stop(cfg, worked=1, now=after_reset, stop=stop, balance=lambda: 31.0)
    assert br.stop_moment(br.Config(stop_utc=None), at(18)) is None


def test_read_diem_balance_logs_and_returns_none_when_unreadable(monkeypatch, tmp_path):
    monkeypatch.delenv("BACKLOG_RUN_BALANCE", raising=False)
    cfg = br.Config(env_file=str(tmp_path / "no.env"))
    monkeypatch.delenv("VENICE_SECOND_OPINION_KEY", raising=False)
    lines = []
    assert br.read_diem_balance(cfg, log=lines.append) is None
    assert lines and "not readable" in lines[0] and "cap" in lines[0]


def test_read_diem_balance_uses_the_review_key_and_the_drains_client(monkeypatch, tmp_path):
    monkeypatch.delenv("BACKLOG_RUN_BALANCE", raising=False)
    monkeypatch.setenv("VENICE_SECOND_OPINION_KEY", "review-key")
    seen = []
    monkeypatch.setattr(br.review_budget, "venice_balance", lambda key: seen.append(key) or 7.25)
    assert br.read_diem_balance(br.Config(), log=lambda *_: None) == 7.25
    assert seen == ["review-key"]
    monkeypatch.setattr(br.review_budget, "venice_balance", lambda key: float("nan"))
    assert br.read_diem_balance(br.Config(), log=lambda *_: None) is None


def test_read_diem_balance_switch_off_never_reads(monkeypatch):
    monkeypatch.setenv("BACKLOG_RUN_BALANCE", "off")
    monkeypatch.setattr(br.review_budget, "venice_balance", lambda key: pytest.fail("read the balance"))
    assert br.read_diem_balance(br.Config(), log=lambda *_: None) is None


# ----------------------------------------------------------------------------- the loop in cmd_work


def test_work_stops_on_low_balance_and_leaves_the_rest_open(world, monkeypatch, capsys):
    cfg = three(world)
    cfg.max_items = 10
    balances = iter([5.0, 1.0])
    monkeypatch.setattr(br, "read_diem_balance", lambda cfg, **k: next(balances))
    sessions = []
    monkeypatch.setattr(br, "work_one", fake_work(sessions))
    assert br.cmd_work(work_args("--no-notify"), cfg) == 0
    out = capsys.readouterr().out
    assert sessions == ["2026-01-01-a"]
    assert "DIEM balance 1.00" in out
    assert {it["status"] for it in load_items(cfg)} == {"open"}


def test_work_stops_before_the_utc_stop_time(world, monkeypatch, capsys):
    cfg = three(world)
    cfg.max_items, cfg.stop_utc = 10, "23:30"
    clock = iter([at(22, 40), at(22, 50), at(23, 15), at(23, 16)])
    monkeypatch.setattr(br, "_utc_now", lambda: next(clock))
    sessions = []
    monkeypatch.setattr(br, "work_one", fake_work(sessions))
    assert br.cmd_work(work_args("--no-notify"), cfg) == 0
    assert sessions == ["2026-01-01-a"]
    assert "23:30 UTC" in capsys.readouterr().out


def test_work_stops_at_the_session_cap(world, monkeypatch, capsys):
    cfg = three(world)
    sessions = []
    monkeypatch.setattr(br, "work_one", fake_work(sessions))
    assert br.cmd_work(work_args("--no-notify", "--max-items", "2"), cfg) == 0
    assert sessions == ["2026-01-01-a", "2026-01-02-b"]


def test_work_runs_on_the_cap_when_the_balance_is_unreadable(world, monkeypatch):
    cfg = three(world)
    cfg.max_items = 10
    monkeypatch.setattr(br, "read_diem_balance", lambda cfg, **k: None)
    sessions = []
    monkeypatch.setattr(br, "work_one", fake_work(sessions))
    assert br.cmd_work(work_args("--no-notify"), cfg) == 0
    assert sessions == ["2026-01-01-a", "2026-01-02-b", "2026-01-03-c"]


def test_only_and_repo_still_filter_the_loop(world, monkeypatch):
    cfg = three(world)
    sessions = []
    monkeypatch.setattr(br, "work_one", fake_work(sessions))
    br.cmd_work(work_args("--no-notify", "--only", "2026-01-03-c"), cfg)
    br.cmd_work(work_args("--no-notify", "--repo", "elsewhere"), cfg)
    assert sessions == ["2026-01-03-c"]


def test_dry_run_says_why_the_loop_would_stop(world, monkeypatch, capsys):
    cfg = three(world)
    cfg.max_items, cfg.stop_utc = 10, "23:30"
    monkeypatch.setattr(br, "_utc_now", lambda: at(22, 50))
    before = Path(cfg.backlog_path).read_text()
    assert br.cmd_work(work_args("--dry-run"), cfg) == 0
    out = capsys.readouterr().out
    assert "balance 31.00" in out and "floor 1.5" in out
    assert "would stop after 2 item(s)" in out and "23:30 UTC" in out
    assert Path(cfg.backlog_path).read_text() == before


def test_dry_run_names_the_cap_and_an_unreadable_balance(world, monkeypatch, capsys):
    cfg = three(world)
    monkeypatch.setattr(br, "read_diem_balance", lambda cfg, **k: None)
    br.cmd_work(work_args("--dry-run", "--max-items", "2"), cfg)
    out = capsys.readouterr().out
    assert "balance unreadable" in out
    assert "then stop: session cap" in out


def test_budget_flags_reach_the_config(monkeypatch):
    seen = {}
    monkeypatch.setattr(br, "cmd_work", lambda args, cfg: seen.update(cfg=cfg) or 0)
    br.main(["work", "--diem-floor", "3", "--stop-utc", "22:15", "--item-estimate", "900", "--max-items", "4"])
    cfg = seen["cfg"]
    assert (cfg.diem_floor, cfg.stop_utc, cfg.item_estimate, cfg.max_items) == (3.0, "22:15", 900, 4)
    br.main(["work", "--stop-utc", "off"])
    assert seen["cfg"].stop_utc is None
    with pytest.raises(SystemExit):
        br.build_parser().parse_args(["work", "--stop-utc", "25:00"])


@pytest.mark.parametrize("flag,value", [
    ("--item-estimate", "0"), ("--item-estimate", "-60"), ("--item-estimate", "1.5"), ("--item-estimate", "nan"),
    ("--max-items", "0"), ("--max-items", "-1"), ("--max-items", "inf"),
    ("--diem-floor", "-0.5"), ("--diem-floor", "nan"), ("--diem-floor", "NaN"),
    ("--diem-floor", "inf"), ("--diem-floor", "-inf"), ("--diem-floor", "Infinity"),
])
def test_invalid_budget_flags_are_rejected_before_work_starts(monkeypatch, capsys, flag, value):
    called = []
    monkeypatch.setattr(br, "cmd_work", lambda args, cfg: called.append(cfg) or 0)
    with pytest.raises(SystemExit) as exc:
        br.main(["work", flag, value])
    assert exc.value.code == 2 and not called
    assert flag in capsys.readouterr().err


def test_valid_edge_values_are_accepted(monkeypatch):
    seen = {}
    monkeypatch.setattr(br, "cmd_work", lambda args, cfg: seen.update(cfg=cfg) or 0)
    br.main(["work", "--diem-floor", "0", "--max-items", "1", "--item-estimate", "1"])
    assert (seen["cfg"].diem_floor, seen["cfg"].max_items, seen["cfg"].item_estimate) == (0.0, 1, 1)


@pytest.mark.parametrize("field,value", [
    ("item_estimate", 0), ("item_estimate", -1200), ("item_estimate", 1.5), ("item_estimate", True),
    ("item_estimate", float("nan")),
    ("max_items", 0), ("max_items", -3), ("max_items", float("inf")), ("max_items", "10"),
    ("diem_floor", -1.0), ("diem_floor", float("nan")), ("diem_floor", float("inf")),
    ("diem_floor", float("-inf")), ("diem_floor", "1.5"), ("diem_floor", True),
    ("stop_utc", "25:00"), ("stop_utc", "late"),
])
def test_validate_budget_config_rejects_bad_values(field, value):
    cfg = br.Config()
    setattr(cfg, field, value)
    with pytest.raises(ValueError):
        br.validate_budget_config(cfg)


def test_validate_budget_config_accepts_the_defaults_and_no_stop_time():
    br.validate_budget_config(br.Config())
    br.validate_budget_config(br.Config(stop_utc=None, diem_floor=0, max_items=1, item_estimate=1))


@pytest.mark.parametrize("field,value", [
    ("item_estimate", -1200), ("max_items", 0), ("diem_floor", float("nan")), ("diem_floor", float("inf")),
])
def test_cmd_work_refuses_an_invalid_config_before_touching_anything(tmp_path, monkeypatch, capsys, field, value):
    # The backlog does not exist: cmd_work must refuse before it reads, plans or works anything.
    cfg = br.Config(backlog_path=str(tmp_path / "missing" / "backlog.yaml"), state_dir=str(tmp_path / "state"))
    setattr(cfg, field, value)
    monkeypatch.setattr(br, "load_yaml", lambda path: pytest.fail("backlog read with a bad config"))
    monkeypatch.setattr(br, "work_one", lambda *a, **k: pytest.fail("item worked with a bad config"))
    monkeypatch.setattr(br, "read_diem_balance", lambda cfg: pytest.fail("balance read with a bad config"))
    assert br.cmd_work(work_args(), cfg) == 2
    assert field in capsys.readouterr().err
    assert not (tmp_path / "state").exists()


# ----------------------------------------------------------------------------- (2) model and effort


@pytest.mark.parametrize("cmd", ["work", "rework"])
def test_default_session_argv_is_opus_5_5_high(world, monkeypatch, cmd):
    seen = {}
    monkeypatch.setattr(br, "cmd_work" if cmd == "work" else "cmd_rework",
                        lambda args, cfg: seen.update(cfg=cfg) or 0)
    br.main([cmd] + (["x"] if cmd == "rework" else []))
    cfg = seen["cfg"]
    env = br.scrubbed_env(str(world.repo))
    captured = {}

    class Proc:
        returncode = 0
        pid = 123
        def communicate(self, prompt, timeout):
            return json.dumps({"result": "ok"}), ""

    monkeypatch.setattr(br.subprocess, "Popen", lambda argv, **k: captured.update(argv=argv) or Proc())
    br.run_session(cfg, "brief", cwd=str(world.repo), env=env, timeout=3)
    assert captured["argv"][-4:] == ["--model", "claude-opus-5-5", "--effort", "high"]


def test_model_and_effort_overrides_still_win(monkeypatch):
    seen = {}
    monkeypatch.setattr(br, "cmd_work", lambda args, cfg: seen.update(cfg=cfg) or 0)
    br.main(["work", "--model", "sonnet", "--effort", "medium"])
    assert (seen["cfg"].model, seen["cfg"].effort) == ("sonnet", "medium")


# ----------------------------------------------------------------------------- (3) short SHAs


HEAD = "0123456789abcdef0123456789abcdef01234567"


@pytest.mark.parametrize("reported,expected", [
    ("0123456", HEAD), ("0123456789AB", HEAD), (HEAD, HEAD), (HEAD.upper(), HEAD.upper()),
    ("012345", "012345"),            # too short: left as reported (invalid)
    ("1234567", "1234567"),          # not a prefix of the head
    ("0123456z", "0123456z"),        # not hex
    (None, None), (1234567, 1234567),
])
def test_expand_sha_accepts_only_a_true_prefix_of_the_head(reported, expected):
    assert readiness.expand_sha(reported, HEAD) == expected


def test_expand_sha_needs_a_full_head():
    assert readiness.expand_sha("0123456", "0123456") == "0123456"
    assert readiness.expand_sha("0123456", "") == "0123456"


def _validated(world, reported):
    cfg = world.build([])
    br.ensure_state(cfg)
    payload = [{"name": "fixture", "branch_sha": reported, "status": "passed", "evidence": "pytest: 1 passed"}]
    captured = br._capture_validations(cfg, "fixture", "RUNNER-VALIDATIONS: " + json.dumps(payload), head_sha=HEAD)
    return br._save_review(cfg, iid="x", stem="fixture", sha=HEAD, kind="done", required=["fixture"],
                           validations=captured, rev=clean_reviewer(None, None, item_id="x"))


def test_a_short_sha_prefix_of_the_head_makes_the_item_ready(world):
    assert _validated(world, HEAD[:7])["status"] == "ready"


@pytest.mark.parametrize("reported", ["fedcba9", HEAD[:6], "not-a-sha"])
def test_anything_else_stays_invalid(world, reported):
    assert _validated(world, reported)["status"] == "unknown"
