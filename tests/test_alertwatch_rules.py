from pathlib import Path

from alertwatch.rules import Rule, classify, load_rules

RULES = load_rules(Path("alertwatch/rules.toml"))


def entry(source, text="something broke"):
    return {"source": source, "text": text}


def test_own_source_dropped():
    assert classify(entry("alert-watch"), RULES) == ("drop", "own-source")
    assert classify(entry("alert-watch-rerun:coverage_check"), RULES) == ("drop", "own-source")


def test_check_source_is_kept():
    assert classify(entry("alert-watch-check", "Check after alert-watch merge"), RULES)[0] == "look"


def test_bebop_briefing_ignored_but_failure_kept():
    src = "/bin/bash /home/dev/projects/ai-harness/bebop/run-briefing.sh morning"
    assert classify(entry(src, "Good morning. Two meetings today."), RULES)[0] == "ignore"
    assert classify(entry(src, "⚠️ Bebop morning briefing failed (rc=1). Check logs."), RULES)[0] == "look"


EXPECT = "bash /home/dev/projects/ai-harness/watchdog/run-expect.sh"
HEAD = "MeetTrack monitor, Sat 15 Nov 10:20 Lisbon\n\n"


def test_expect_resolved_or_closed_only_is_ignored():
    assert classify(entry(EXPECT, HEAD + "[OK] RESOLVED Leiria: results after 25 min\n  Now: 48 of 48."),
                    RULES)[0] == "ignore"
    assert classify(entry(EXPECT, HEAD + "[--] CLOSED Leiria: results (open 3h00)\n  Not judged.\n"
                                         "  Last seen: x."), RULES)[0] == "ignore"


def test_expect_alert_is_looked_at_even_beside_a_resolved_line():
    text = (HEAD + "[OK] RESOLVED Leiria: results after 25 min\n  Now: 48 of 48.\n\n"
            "[CRIT] NEW Algés: results late\n  Expected: x.\n  We see: y.")
    assert classify(entry(EXPECT, text), RULES) == ("look", "expect-alert")
    assert classify(entry(EXPECT, HEAD + "[WARN] STILL MISSING Algés: results (for 1h05)"), RULES)[0] == "look"


def test_a_meet_name_cannot_fake_an_alert_line():
    text = HEAD + "[OK] RESOLVED [CRIT] fake meet: results after 5 min\n  Now: [WARN] x."
    assert classify(entry(EXPECT, text), RULES)[0] == "ignore"


def test_finance_backup_failure_is_looked_at():
    # backup_data.py sends only on failure.
    src = "/usr/bin/python3 scripts/backup/backup_data.py < /bin/sh -c cd /home/dev/projects/finance-tracker"
    assert classify(entry(src, "finance-tracker backup FAILED: disk full"), RULES)[0] == "look"


def test_reports_that_need_rex_not_a_fix_are_ignored():
    for src in ("python3 -m backlogrun.cli work", "/home/dev/.local/bin/session-gc sweep",
                "/usr/bin/python3 /home/dev/projects/hq/watch/usna-summer-seminar.py",
                "node /home/dev/projects/sat-prep/node_modules/.bin/tsx scripts/feedback-sync.ts"):
        assert classify(entry(src), RULES)[0] == "ignore", src


def test_meettrack_and_watchdog_reports_are_looked_at():
    for src in ("/usr/bin/python3 coverage_check.py", "/usr/bin/python3 discover_meets.py",
                "bash /home/dev/projects/ai-harness/watchdog/run-watchdog.sh"):
        assert classify(entry(src), RULES)[0] == "look", src


def test_unknown_source_defaults_to_look():
    assert classify(entry("python3 something_new.py"), RULES) == ("look", "default")


def test_first_match_wins():
    rules = [Rule("a", "ignore", "job", None), Rule("b", "look", "job", None)]
    assert classify(entry("job"), rules) == ("ignore", "a")


def test_rule_with_source_and_text_needs_both():
    rules = [Rule("both", "ignore", "job", "fine")]
    assert classify(entry("job", "all fine"), rules) == ("ignore", "both")
    assert classify(entry("job", "broken"), rules) == ("look", "default")
    assert classify(entry("other", "fine"), rules) == ("look", "default")


def test_text_match_ignores_case():
    rules = [Rule("t", "ignore", None, "resolved")]
    assert classify(entry("x", "[OK] RESOLVED"), rules) == ("ignore", "t")


def test_bad_action_rejected(tmp_path):
    p = tmp_path / "r.toml"
    p.write_text('[[rule]]\nname = "x"\naction = "maybe"\nsource = "y"\n')
    try:
        load_rules(p)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
