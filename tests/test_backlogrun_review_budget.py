"""backlog-run fires at 22:00 UTC so its council reviews spend the expiring day's DIEM. On a
day the balance is already empty, a review would just fail. `wait_for_review_budget` makes the
review wait for the 00:00 UTC reset instead, and never blocks a review it cannot help."""
from datetime import datetime, timezone

import pytest

from backlogrun import review_budget as rb


def at(h, m=0):
    return datetime(2026, 9, 21, h, m, tzinfo=timezone.utc)


class Clock:
    def __init__(self, start):
        self.now, self.slept = start, []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        from datetime import timedelta
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def run(balance, clock, **kw):
    logs = []
    out = rb.wait_for_review_budget(balance=balance, now=clock, sleep=clock.sleep, log=logs.append, **kw)
    return out, logs


def test_enough_diem_means_no_wait():
    clock = Clock(at(22, 30))
    out, logs = run(lambda: 6.0, clock)
    assert out == "ok" and clock.slept == []


def test_an_empty_balance_before_the_reset_waits_until_just_after_it():
    clock = Clock(at(22, 30))
    readings = iter([0.2, 31.0])
    out, logs = run(lambda: next(readings), clock)
    assert out == "waited"
    assert sum(clock.slept) == 90 * 60 + rb.AFTER_RESET_S
    assert clock.now == datetime(2026, 9, 22, 0, 0, rb.AFTER_RESET_S % 60, tzinfo=timezone.utc).replace(
        minute=rb.AFTER_RESET_S // 60)
    assert any("waits" in line for line in logs)


def test_a_reset_that_is_too_far_away_is_not_waited_for():
    # The 03:00 UTC schedule with an empty balance: 21 hours to the reset. Go ahead and let the
    # review fail honestly rather than hold the runner's lock for a day.
    clock = Clock(at(3, 0))
    out, logs = run(lambda: 0.0, clock)
    assert out == "no_wait" and clock.slept == []


def test_a_balance_that_cannot_be_read_never_blocks_the_review():
    def boom():
        raise RuntimeError("rate_limits unreachable")
    clock = Clock(at(22, 30))
    out, logs = run(boom, clock)
    assert out == "unknown" and clock.slept == []


@pytest.mark.parametrize("bad", [float("nan"), None, "lots"])
def test_a_balance_that_is_not_a_number_never_blocks_the_review(bad):
    clock = Clock(at(22, 30))
    out, logs = run(lambda: bad, clock)
    assert out == "unknown" and clock.slept == []


def test_the_wait_is_capped_even_if_the_clock_misbehaves():
    clock = Clock(at(22, 30))
    out, logs = run(lambda: 0.0, clock, max_wait_s=600)
    assert out == "no_wait" and clock.slept == []       # 90 minutes to go is more than the cap


def test_still_empty_after_the_reset_goes_ahead_anyway():
    clock = Clock(at(23, 50))
    out, logs = run(lambda: 0.0, clock)
    assert out == "waited" and len(clock.slept) >= 1     # one wait, no loop: the review then reports what it finds


def test_the_switch_turns_it_off(monkeypatch):
    monkeypatch.setenv("BACKLOG_REVIEW_WAIT", "off")
    assert rb.enabled() is False
    monkeypatch.setenv("BACKLOG_REVIEW_WAIT", "on")
    assert rb.enabled() is True


def test_the_test_suite_itself_runs_with_the_wait_off():
    # conftest sets it: no test may reach Venice's balance endpoint or sleep for an hour.
    assert rb.enabled() is False


# --- wired into the real reviewer -------------------------------------------------------

def _cfg(tmp_path):
    from backlogrun import cli as br
    cfg = br.Config()
    cfg.env_file = str(tmp_path / "no-env")
    return br, cfg


def test_the_real_reviewer_asks_for_budget_before_it_spends_anything(tmp_path, monkeypatch):
    br, cfg = _cfg(tmp_path)
    monkeypatch.setenv("VENICE_SECOND_OPINION_KEY", "test-key")
    monkeypatch.setenv("BACKLOG_REVIEW_WAIT", "on")
    seen = {}

    def fake_wait(*, balance, log, **kw):
        seen["called"] = True
        raise RuntimeError("stop here: before any panel call")
    monkeypatch.setattr(rb, "wait_for_review_budget", fake_wait)
    rev = br.council_review(cfg, "diff --git a/x b/x", item_id="2026-09-21-x")
    assert seen == {"called": True}
    assert rev["ok"] is False and "stop here" in rev["summary"]


def test_with_the_switch_off_the_reviewer_never_asks(tmp_path, monkeypatch):
    br, cfg = _cfg(tmp_path)
    monkeypatch.setenv("VENICE_SECOND_OPINION_KEY", "test-key")
    calls = []
    monkeypatch.setattr(rb, "wait_for_review_budget", lambda **kw: calls.append(kw))
    import council.engine
    def no_panel(*a, **kw):
        raise RuntimeError("panel reached")
    monkeypatch.setattr(council.engine, "run_panel", no_panel)
    rev = br.council_review(cfg, "diff --git a/x b/x", item_id="2026-09-21-x")
    assert calls == [] and "panel reached" in rev["summary"]
