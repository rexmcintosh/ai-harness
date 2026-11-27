"""watchdog.heal: probe, one restart per 6 hours, report-only for T3."""
from watchdog import heal

NOW = 1_800_000_000.0
ALL_UP = {k: True for k in heal.SERVICES}


def step(state, down=(), t=NOW):
    alive = {k: (k not in down) for k in heal.SERVICES}
    return heal.decide(state, alive, t)


def test_healthy_does_nothing():
    state, restarts, msgs = step({})
    assert restarts == [] and msgs == []


def test_restart_after_two_failed_probes_then_cooldown():
    s, r, m = step({}, down=["finance"])
    assert r == [] and m == []
    s, r, m = step(s, down=["finance"], t=NOW + 300)
    assert r == ["finance"] and m == []
    # Fails again within 6 hours: report, no second restart, and only once.
    s, r, m = step(s, down=["finance"], t=NOW + 600)
    s, r, m = step(s, down=["finance"], t=NOW + 900)
    assert r == [] and len(m) == 1 and "within 6 hours" in m[0]
    s, r, m = step(s, down=["finance"], t=NOW + 1200)
    assert r == [] and m == []
    s, r, m = step(s, t=NOW + 1500)
    assert m == ["FIXED: the finance tracker answers again."]


def test_restart_allowed_again_after_six_hours():
    s, _, _ = step({}, down=["nutrition"])
    s, r, _ = step(s, down=["nutrition"], t=NOW + 300)
    s, _, _ = step(s, t=NOW + 600)
    s, _, _ = step(s, down=["nutrition"], t=NOW + 7 * 3600)
    s, r, _ = step(s, down=["nutrition"], t=NOW + 7 * 3600 + 300)
    assert r == ["nutrition"]


def test_t3_is_reported_never_restarted():
    s, _, _ = step({}, down=["t3"])
    s, r, m = step(s, down=["t3"], t=NOW + 300)
    assert r == [] and len(m) == 1 and "T3 thread" in m[0]


def test_pause_file_expires(tmp_path, monkeypatch):
    monkeypatch.setattr(heal, "STATE", tmp_path)
    (tmp_path / "pause").write_text("")
    import os
    assert heal.paused(os.path.getmtime(tmp_path / "pause") + 60)
    assert not heal.paused(os.path.getmtime(tmp_path / "pause") + 3 * 3600)
    assert not (tmp_path / "pause").exists()


def test_failed_sends_wait_in_the_outbox(tmp_path, monkeypatch):
    monkeypatch.setattr(heal, "STATE", tmp_path)
    heal.send_all(["one"], sender=lambda text: False)
    sent = []
    heal.send_all(["two"], sender=lambda text: sent.append(text) or True)
    assert sent == ["one", "two"]
    import json
    assert json.loads((tmp_path / "outbox.json").read_text()) == []
