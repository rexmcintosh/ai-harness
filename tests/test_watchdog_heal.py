"""watchdog.heal: probe, one restart per 6 hours, report-only for T3."""
from watchdog import heal

NOW = 1_800_000_000.0
ALL_UP = {k: True for k in heal.SERVICES}


def step(state, down=(), t=NOW, restart_ok=True):
    """One run, including the restart command's result, like main() does."""
    alive = {k: (k not in down) for k in heal.SERVICES}
    new, restarts, msgs = heal.decide(state, alive, t)
    for key in restarts:
        heal.record_restart(new, key, t, restart_ok)
    return new, restarts, msgs


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


def test_fixed_is_said_after_a_restart_works():
    s, _, _ = step({}, down=["finance"])
    s, r, _ = step(s, down=["finance"], t=NOW + 300)
    assert r == ["finance"]
    s, r, m = step(s, t=NOW + 600)
    assert m == ["FIXED: the finance tracker answers again."]


def test_rules_ignore_recovery_but_look_at_problems():
    import tomllib
    from pathlib import Path
    import re
    rules = tomllib.loads((Path(heal.__file__).resolve().parents[1] / "alertwatch" / "rules.toml").read_text())["rule"]

    def action(source, text):
        for rule in rules:
            if "source" in rule and not re.search(rule["source"], source):
                continue
            if "text" in rule and not re.search(rule["text"], text, re.I):
                continue
            return rule["action"]
        return "look"
    src = "python3 watchdog.heal < CRON"
    assert action(src, "FIXED: the finance tracker answers again.") == "ignore"
    assert action(src, "RESTARTED: the finance tracker was running but not answering") == "ignore"
    assert action(src, "PROBLEM: the finance tracker was not answering, and restarting it failed") == "look"
    assert action("python3 watchdog.weekly < CRON", "Weekly check, Mon 30 Nov: all well.") == "ignore"
    assert action("python3 watchdog.weekly < CRON", "Traceback (most recent call last)") == "look"


def test_only_one_fixed_after_restart_and_report():
    s, _, _ = step({}, down=["finance"])
    s, r, _ = step(s, down=["finance"], t=NOW + 300)          # restart
    s, _, _ = step(s, down=["finance"], t=NOW + 600)
    s, _, m = step(s, down=["finance"], t=NOW + 900)          # PROBLEM: again within 6 h
    assert len(m) == 1
    s, _, m = step(s, t=NOW + 1200)
    assert m == ["FIXED: the finance tracker answers again."]
    s, _, m = step(s, t=NOW + 1500)
    assert m == [] and "finance" not in s["verify"]


def test_failed_restart_command_is_not_a_restart():
    s, _, _ = step({}, down=["finance"])
    s, r, _ = step(s, down=["finance"], t=NOW + 300, restart_ok=False)
    assert r == ["finance"] and "finance" not in s["restarted"] and "finance" in s["reported"]
    msg = heal.record_restart({"restarted": {}, "verify": {}, "fails": {}, "reported": {}}, "finance", NOW, False)
    assert msg.startswith("PROBLEM") and "restarting it failed" in msg


def test_outbox_send_is_bounded_and_stops_at_failure(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr(heal, "STATE", tmp_path)
    (tmp_path / "outbox.json").write_text(json.dumps([f"m{i}" for i in range(10)]))
    tried = []
    heal.send_all([], sender=lambda text: tried.append(text) or False)
    assert tried == ["m0"]
    tried.clear()
    heal.send_all([], sender=lambda text: tried.append(text) or True)
    assert tried == [f"m{i}" for i in range(heal.MAX_SENDS_PER_RUN)]
