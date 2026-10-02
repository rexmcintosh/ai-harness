from types import SimpleNamespace
from diem.config import DiemConfig
from diem.report import evening_ping, send_telegram, write_morning_report

SUMMARY = {"aborted": None, "floor": 15.0, "started_balance": 40.0,
           "ended_balance": 12.0, "deadline": "2026-07-04T00:50:00",
           "ran": [{"id": "a1", "type": "review", "ok": True, "cost": 2.0,
                    "duration_s": 120.0, "output_path": "/o/r.md", "error": None},
                   {"id": "b2", "type": "images", "ok": False, "cost": 0.0,
                    "duration_s": 5.0, "output_path": None, "error": "exit 2: x"}],
           "skipped": [{"id": "c3", "type": "backfill", "reason": "deadline"}]}

def _cfg(tmp_path, telegram=None):
    return DiemConfig(daily_diem=100.0, repos=[], state_dir=tmp_path / "state",
                      telegram=telegram)

def test_evening_ping_one_line(tmp_path):
    line = evening_ping(SUMMARY, _cfg(tmp_path))
    assert "\n" not in line and "12" in line  # ended balance visible

def test_morning_report_contents(tmp_path):
    cfg = _cfg(tmp_path)
    path = write_morning_report(cfg, "2026-07-04", [SUMMARY])
    text = path.read_text()
    assert path.name == "2026-07-04.md"
    assert "/o/r.md" in text            # output linked
    assert "exit 2: x" in text          # failure surfaced
    assert "deadline" in text           # skips surfaced

def test_send_telegram_posts(tmp_path):
    calls = []
    def post(url, json=None, timeout=None):
        calls.append((url, json))
        return SimpleNamespace(status_code=200)
    ok = send_telegram(_cfg(tmp_path, {"bot_token": "T", "chat_id": "9"}),
                       "hi", post=post)
    assert ok and calls[0][0] == "https://api.telegram.org/botT/sendMessage"
    assert calls[0][1] == {"chat_id": "9", "text": "hi"}

def test_send_telegram_unconfigured_or_failing_is_quiet(tmp_path):
    assert send_telegram(_cfg(tmp_path), "hi") is False
    def post(url, json=None, timeout=None):
        raise ConnectionError("down")
    assert send_telegram(_cfg(tmp_path, {"bot_token": "T", "chat_id": "9"}),
                         "hi", post=post) is False

def test_aborted_checkpoint_with_none_balances(tmp_path):
    """Regression: aborted checkpoints can have None balances."""
    cfg = _cfg(tmp_path)
    summary = {
        "aborted": "balance_unavailable",
        "floor": 10.0,
        "started_balance": None,
        "ended_balance": None,
        "deadline": "2026-07-05T00:50:00",
        "ran": [],
        "skipped": []
    }
    # evening_ping should handle None via "?" branch
    line = evening_ping(summary, cfg)
    assert "?" in line
    assert "\n" not in line

    # write_morning_report should print raw None values without raising
    path = write_morning_report(cfg, "2026-07-05", [summary])
    text = path.read_text()
    assert "balance_unavailable" in text
    assert "None → None" in text
