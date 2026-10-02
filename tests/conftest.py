import json
import pytest


@pytest.fixture(autouse=True)
def _isolate_venice_usage_ledger(tmp_path, monkeypatch):
    """Belt-and-braces: no test may ever write to the real usage ledger at
    ~/.local/state/venice-usage/. Any test that needs its own ledger path can
    still monkeypatch VENICE_USAGE_DB itself — that simply overrides this."""
    monkeypatch.setenv("VENICE_USAGE_DB", str(tmp_path / "venice-usage-test.db"))


@pytest.fixture(autouse=True)
def _no_live_jev_shadow(tmp_path, monkeypatch):
    """No test may reach the real TypeSafe API or write the live watchdog shadow log.
    watchdog.run.main() runs the Jev shadow pass whenever monitors.toml enables it and
    ~/.env holds a key, so the kill switch is on for every test and the log dir is a
    temp dir. A test of the wiring itself deletes WATCHDOG_JEV_SHADOW and patches
    jev_shadow.shadow_pass / load_key."""
    monkeypatch.setenv("WATCHDOG_JEV_SHADOW", "0")
    monkeypatch.setenv("WATCHDOG_LOG_DIR", str(tmp_path / "watchdog-logs"))
    # The same rule for every caller of the shared client: JEV_DISABLED makes jev.ask refuse
    # before any transport, and the usage ledger goes to a temp file. A test of the enabled
    # path deletes JEV_DISABLED itself and passes a fake `transport=`.
    monkeypatch.setenv("JEV_DISABLED", "1")
    # the runner's "wait for the DIEM reset" guard reads Venice's balance and may sleep for an hour
    monkeypatch.setenv("BACKLOG_REVIEW_WAIT", "off")
    # ... and its budget loop reads the same balance before every item: never from a test
    monkeypatch.setenv("BACKLOG_RUN_BALANCE", "off")
    monkeypatch.setenv("JEV_USAGE_LOG", str(tmp_path / "jev-usage.jsonl"))


@pytest.fixture(autouse=True)
def _no_live_council_jev(tmp_path, monkeypatch):
    """The council's Jev shadow signals run in `council review`, `council sweep` and
    backlog-run's council step whenever a key exists, and this machine has one. They go
    through the shared client, so JEV_DISABLED above already stops every call. On top of
    that the council's own switch is off and its shadow log is a temp file, so no test writes
    ~/.local/state/council/. A test of the wiring deletes JEV_DISABLED, sets COUNCIL_JEV=1
    and replaces jev.client.http_post with a fake (tests/test_council_jev_shadow.py)."""
    monkeypatch.setenv("COUNCIL_JEV", "0")
    monkeypatch.setenv("COUNCIL_JEV_LOG", str(tmp_path / "council-jev-shadow.jsonl"))


class FakeClient:
    """Stand-in for VeniceClient. Scripted responses keyed by model name,
    or a single default. Records calls for assertions."""

    def __init__(self, by_model=None, default=None, raises_for=None):
        self.by_model = by_model or {}
        self.default = default
        self.raises_for = raises_for or set()
        self.calls = []

    def complete(self, model, system, user, *, json_mode=True, task_type="chat",
                 max_completion_tokens=None):
        self.calls.append({"model": model, "system": system, "user": user,
                           "task_type": task_type, "json_mode": json_mode,
                           "max_completion_tokens": max_completion_tokens})
        if model in self.raises_for:
            raise RuntimeError(f"boom:{model}")
        payload = self.by_model.get(model, self.default)
        if payload is None:
            raise AssertionError(f"FakeClient has no scripted reply for {model}")
        return payload if isinstance(payload, str) else json.dumps(payload)


@pytest.fixture
def member_json():
    def make(stance="concerns", headline="ok", findings=(), suggestions=()):
        return {
            "stance": stance,
            "headline": headline,
            "findings": [
                {"point": p, "severity": s, "confidence": c} for (p, s, c) in findings
            ],
            "suggestions": list(suggestions),
        }
    return make
