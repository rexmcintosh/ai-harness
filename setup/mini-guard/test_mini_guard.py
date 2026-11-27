"""Tests for mini-guard. They run on Linux with fake command output."""
import importlib.util
import json
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("mini_guard", Path(__file__).resolve().parent / "mini_guard.py")
g = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(g)

T0 = 1_800_000_000.0
GOOD_PROBE = "port 3773 200\nport 8123 302\nport 3000 200\nport 3791 401\ndisk 72%\n"


class Fake(g.Runner):
    """Fake machine: change the attributes to change what the commands return."""

    def __init__(self):
        self.status = "Running"
        self.probe = (0, GOOD_PROBE)
        self.ping = 0
        self.batt = "Now drawing from 'AC Power'\n -Eaton 5E\t100%; charging"
        self.boot = T0 - 86400
        self.kick = 0
        self.calls = []

    def run(self, argv, timeout):
        self.calls.append(argv)
        if argv[1] == "list":
            return 0, json.dumps({"name": "vps", "status": self.status}) + "\n"
        if argv[1] == "shell":
            return self.probe
        if argv[1] == "ping":
            return self.ping, ""
        if argv[0] == "/bin/df":
            return 0, "Filesystem 1024-blocks Used Available Capacity\n/dev/disk3s5 239000000 170000000 40000000 81% /\n"
        if argv[0] == "/usr/bin/pmset":
            return 0, self.batt
        if argv[0] == "/usr/sbin/sysctl":
            return 0, "{ sec = %d, usec = 0 } Tue Sep 22 07:38:22 2026" % int(self.boot)
        if argv[0] == "/bin/launchctl":
            return self.kick, ""
        raise AssertionError(argv)

    def kicks(self):
        return [c for c in self.calls if c[0] == "/bin/launchctl"]


@pytest.fixture(autouse=True)
def state(tmp_path, monkeypatch):
    monkeypatch.setattr(g, "STATE_DIR", tmp_path / "state")
    monkeypatch.setattr(g, "now", lambda: clock["t"])
    clock["t"] = T0
    return tmp_path / "state"


clock = {"t": T0}


def run(fake, mode="start", minutes=0, lock=False):
    clock["t"] = T0 + minutes * 60
    return g.tick(fake, {"mode": mode}, clock["t"], lock_held=lock)


def test_healthy_server_sends_nothing():
    fake = Fake()
    assert run(fake) == []
    assert json.loads((g.STATE_DIR / "last-tick.json").read_text())["status"] == "Running"


def test_stopped_vm_is_started_on_the_second_check_then_fixed():
    fake = Fake()
    fake.status = "Stopped"
    first = run(fake, minutes=0)
    assert len(first) == 1 and first[0].startswith("PROBLEM") and "next check" in first[0]
    assert fake.kicks() == []
    second = run(fake, minutes=2)
    assert fake.kicks() and "kickstart" in fake.kicks()[0]
    assert second == ["UPDATE: I started it (start attempt 1 of 2 allowed in 24 hours)."]
    fake.status = "Running"
    third = run(fake, minutes=4)
    assert len(third) == 1 and third[0].startswith("FIXED")


def test_watch_only_mode_never_starts():
    fake = Fake()
    fake.status = "Stopped"
    run(fake, mode="observe", minutes=0)
    msgs = run(fake, mode="observe", minutes=2)
    assert fake.kicks() == [] and msgs == []
    first = json.loads((g.STATE_DIR / "state.json").read_text())
    assert "vm-stopped" in first["incidents"]


def test_start_limit_survives_and_then_tells_rex_what_to_do():
    fake = Fake()
    fake.status = "Stopped"
    g_messages = []
    for minute in range(0, 40, 2):
        g_messages += run(fake, minutes=minute)
    assert len(fake.kicks()) == 2
    notes = [m for m in g_messages if "stopped trying" in m]
    assert len(notes) == 1 and "limactl start vps" in notes[0]
    state = json.loads((g.STATE_DIR / "state.json").read_text())
    assert len(state["starts"]) == 2
    # A day later the limit resets.
    run(fake, minutes=24 * 60 + 30)
    assert len(fake.kicks()) == 3


def test_failed_start_is_reported_and_still_counted():
    fake = Fake()
    fake.status = "Stopped"
    fake.kick = 5
    run(fake, minutes=0)
    msgs = run(fake, minutes=2)
    assert len(msgs) == 1 and "launchd refused" in msgs[0]
    assert len(json.loads((g.STATE_DIR / "state.json").read_text())["starts"]) == 1


def test_no_start_while_the_mini_is_booting():
    fake = Fake()
    fake.status = "Stopped"
    fake.boot = T0 - 60
    run(fake, minutes=0)
    run(fake, minutes=2)
    assert fake.kicks() == []


def test_pause_hides_outages_and_blocks_starts():
    fake = Fake()
    g.cmd_pause(30, "test")
    fake.status = "Stopped"
    assert run(fake, minutes=0) == []
    assert run(fake, minutes=2) == []
    assert fake.kicks() == []


def test_expired_pause_is_removed_and_reported():
    fake = Fake()
    g.cmd_pause(10, "test")
    fake.status = "Stopped"
    run(fake, minutes=2)
    msgs = run(fake, minutes=12)
    assert not (g.STATE_DIR / "pause.json").exists()
    assert any("pause ended" in m for m in msgs)


def test_too_long_pause_is_capped():
    g.cmd_pause(10_000, "forgotten")
    until = json.loads((g.STATE_DIR / "pause.json").read_text())["until"]
    assert until - clock["t"] <= g.MAX_PAUSE_S + 1


def test_maintenance_lock_blocks_starts():
    fake = Fake()
    fake.status = "Stopped"
    run(fake, minutes=0, lock=True)
    run(fake, minutes=2, lock=True)
    assert fake.kicks() == []


def test_hung_guest_alerts_after_ten_minutes_and_never_acts():
    fake = Fake()
    fake.probe = (255, "")
    for minute in (0, 2, 4, 6, 8):
        assert run(fake, minutes=minute) == []
    msgs = run(fake, minutes=10)
    assert len(msgs) == 1 and "does not answer" in msgs[0]
    assert fake.kicks() == []


def test_service_down_alerts_once_then_reminds_then_fixed():
    fake = Fake()
    fake.probe = (0, GOOD_PROBE.replace("port 8123 302", "port 8123 000"))
    assert run(fake, minutes=0) == []
    msgs = run(fake, minutes=6)
    assert msgs == ["PROBLEM: Home Assistant does not answer on the server."]
    assert run(fake, minutes=8) == []
    remind = run(fake, minutes=6 + 6 * 60)
    assert len(remind) == 1 and remind[0].startswith("STILL A PROBLEM")
    fake.probe = (0, GOOD_PROBE)
    fixed = run(fake, minutes=6 + 6 * 60 + 2)
    assert len(fixed) == 1 and fixed[0].startswith("FIXED")


def test_short_blip_sends_nothing():
    fake = Fake()
    fake.probe = (0, GOOD_PROBE.replace("port 3000 200", "port 3000 000"))
    assert run(fake, minutes=0) == []
    fake.probe = (0, GOOD_PROBE)
    assert run(fake, minutes=2) == []
    assert json.loads((g.STATE_DIR / "state.json").read_text())["blips"] == 1


def test_tailnet_problem_is_separate_and_never_acts():
    fake = Fake()
    fake.ping = 1
    run(fake, minutes=0)
    msgs = run(fake, minutes=10)
    assert len(msgs) == 1 and "Tailscale" in msgs[0]
    assert fake.kicks() == []


def test_battery_and_disk_alert_at_once():
    fake = Fake()
    fake.batt = "Now drawing from 'UPS Power'\n -Eaton 5E\t64%; discharging"
    fake.probe = (0, GOOD_PROBE.replace("disk 72%", "disk 93%"))
    msgs = run(fake, minutes=0)
    assert any("UPS battery (64% left)" in m for m in msgs)
    assert any("93% full" in m for m in msgs)


def test_broken_lima_status_alerts_after_four_minutes():
    fake = Fake()
    fake.status = "Broken"
    assert run(fake, minutes=0) == []
    msgs = run(fake, minutes=4)
    assert len(msgs) == 1 and "'Broken'" in msgs[0]
    assert fake.kicks() == []


def test_unknown_status_when_limactl_fails():
    class Broken(Fake):
        def run(self, argv, timeout):
            if argv[1] == "list":
                return None, ""
            return super().run(argv, timeout)
    assert g.vm_status(Broken()) == "Unknown"


def test_messages_wait_in_the_outbox_when_telegram_is_down():
    g.deliver(["one"], {}, sender=lambda text, cfg: False)
    assert json.loads((g.STATE_DIR / "outbox.json").read_text()) == ["one"]
    sent = []
    g.deliver(["two"], {}, sender=lambda text, cfg: sent.append(text) or True)
    assert sent == ["one", "two"]
    assert json.loads((g.STATE_DIR / "outbox.json").read_text()) == []


def test_send_keeps_the_token_out_of_command_lines(tmp_path):
    token = tmp_path / "token"
    token.write_text("123:ABC\n")
    seen = {}

    class Reply:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(url, data, timeout):
        seen["url"], seen["data"] = url, data
        return Reply()
    assert g.send("hi", {"telegram_token_file": str(token), "chat_id": "7"}, opener=opener)
    assert "123:ABC" in seen["url"] and b"chat_id=7" in seen["data"]
    assert not g.send("hi", {}, opener=opener)


def test_cooldown_between_starts():
    fake = Fake()
    fake.status = "Stopped"
    for minute in range(0, 10, 2):
        run(fake, minutes=minute)
    assert len(fake.kicks()) == 1
    run(fake, minutes=14)
    assert len(fake.kicks()) == 2


def test_overlapping_tick_changes_nothing(monkeypatch):
    held = g.try_lock("tick.lock")
    assert held is not None
    called = []
    monkeypatch.setattr(g, "tick", lambda *a, **k: called.append(1) or [])
    monkeypatch.setattr(g, "config", lambda: {"mode": "start"})
    assert g.cmd_tick() == 0
    assert called == []
    held.close()


def test_maintenance_lock_makes_the_tick_a_paused_one(monkeypatch):
    maint = g.try_lock("lock")
    seen = {}
    monkeypatch.setattr(g, "tick", lambda runner, cfg, t, lock_held: seen.setdefault("held", lock_held) and [])
    monkeypatch.setattr(g, "config", lambda: {"mode": "start"})
    monkeypatch.setattr(g, "deliver", lambda *a, **k: None)
    monkeypatch.setattr(g, "heartbeat", lambda *a, **k: None)
    g.cmd_tick()
    assert seen["held"] is True
    maint.close()


def test_delivery_stops_at_the_first_failure_and_is_bounded():
    g.save_json(g.STATE_DIR / "outbox.json", [f"old {i}" for i in range(10)])
    tried = []
    g.deliver(["new"], {}, sender=lambda text, cfg: tried.append(text) or False)
    assert tried == ["old 0"]
    assert len(json.loads((g.STATE_DIR / "outbox.json").read_text())) == 11
    tried.clear()
    g.deliver([], {}, sender=lambda text, cfg: tried.append(text) or True)
    assert len(tried) == g.MAX_SENDS_PER_TICK
    assert len(json.loads((g.STATE_DIR / "outbox.json").read_text())) == 11 - g.MAX_SENDS_PER_TICK


@pytest.mark.parametrize("bad", ['{"until": "soon"}', '{"until": "nan"}', '{"until": "inf"}', '[1, 2]', '"x"'])
def test_bad_pause_file_is_removed_not_fatal(bad):
    g.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (g.STATE_DIR / "pause.json").write_text(bad)
    paused, note = g.pause_state(clock["t"])
    assert paused is False
    assert not (g.STATE_DIR / "pause.json").exists()


def test_heartbeat_sends_own_client_name(tmp_path, monkeypatch):
    # Cloudflare answers 403 (error 1010) to Python's default User-Agent.
    monkeypatch.setattr(g, "STATE_DIR", tmp_path)
    seen = {}

    class Reply:
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def opener(req, timeout):
        seen["ua"], seen["url"] = req.get_header("User-agent"), req.full_url
        return Reply()
    g.heartbeat({"healthcheck_url": "https://example.test/ping/x"}, opener=opener)
    assert seen == {"ua": "mini-guard/1", "url": "https://example.test/ping/x"}
    g.heartbeat({}, opener=lambda *a, **k: 1 / 0)      # no URL: nothing sent
