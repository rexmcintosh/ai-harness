"""The weekly all-well line."""
from watchdog import weekly
from watchdog.triage import CheckStatus

NOW = 1_800_000_000.0


def test_all_well():
    statuses = [CheckStatus("disk", "ok", "root filesystem 72% full"),
                CheckStatus("backup", "ok", "off-site backup verified 5 h ago")]
    text = weekly.summary(statuses, NOW, 3.2, {"mode": "start", "starts_24h": 0})
    assert "all well." in text and "server disk 72% full" in text
    assert "backup verified 5 h ago" in text and "start mode" in text and "up 3 days" in text


def test_problems_are_listed():
    statuses = [CheckStatus("backup", "warn", "no verified off-site backup for 31 h"),
                CheckStatus("mini-guard", "crit", "the Mac mini guard has not run for 40 min")]
    text = weekly.summary(statuses, NOW, None, None)
    assert "2 thing(s) need a look." in text
    assert "- WARN: no verified off-site backup for 31 h" in text
    assert "- CRIT: the Mac mini guard has not run for 40 min" in text
