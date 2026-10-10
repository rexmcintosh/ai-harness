import pytest


@pytest.fixture(autouse=True)
def _no_live_expect_state(tmp_path, monkeypatch):
    """watchdog/test_freshness.py runs collect_metrics; on a host where the
    expect monitor runs, its fresh state file would make the meet rules stand
    down (defer_to_expect). Tests never read the live file."""
    monkeypatch.setenv("WATCHDOG_EXPECT_STATE", str(tmp_path / "expect-state.json"))
