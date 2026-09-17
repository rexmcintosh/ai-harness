"""Tests for the watchdog triage core — pure functions over collected signals."""
from watchdog.triage import (
    CheckStatus,
    check_bebop_runs,
    check_cron_log,
    check_disk,
    check_orphan_processes,
    check_service_active,
    triage,
)

HOUR = 3600
NOW = 1_000_000  # arbitrary fixed "now" epoch for deterministic tests


# --- check_bebop_runs -------------------------------------------------------

def _bebop_line(ts_iso, rc, result="SENT"):
    return f'[{ts_iso}] mode=morning rc={rc} result="{result}" cost_usd=0.1 in=1 out=2'


def test_bebop_last_run_failed_is_crit():
    # last run rc != 0 -> crit, regardless of recency
    log = _bebop_line("2026-06-16T07:00:01+00:00", 1, "FAILED rc=1")
    st = check_bebop_runs(log, NOW)
    assert st.level == "crit"
    assert "rc=1" in st.summary or "failed" in st.summary.lower()


def test_bebop_recent_success_is_ok():
    # timestamp ~1h before NOW, rc=0 -> ok
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(NOW - HOUR, tz=timezone.utc).isoformat()
    st = check_bebop_runs(_bebop_line(ts, 0), NOW)
    assert st.level == "ok"


def test_bebop_stale_success_is_warn():
    # last successful run is older than the max gap -> warn (a briefing was missed)
    from datetime import datetime, timezone
    ts = datetime.fromtimestamp(NOW - 20 * HOUR, tz=timezone.utc).isoformat()
    st = check_bebop_runs(_bebop_line(ts, 0), NOW, max_gap_hours=14)
    assert st.level == "warn"


def test_bebop_empty_log_is_warn():
    st = check_bebop_runs("", NOW)
    assert st.level == "warn"


def test_bebop_reads_last_line_not_first():
    from datetime import datetime, timezone
    old = datetime.fromtimestamp(NOW - 20 * HOUR, tz=timezone.utc).isoformat()
    new = datetime.fromtimestamp(NOW - HOUR, tz=timezone.utc).isoformat()
    log = _bebop_line(old, 1, "FAILED") + "\n" + _bebop_line(new, 0)
    # latest line is a recent success -> ok (must not trip on the older failure)
    assert check_bebop_runs(log, NOW).level == "ok"


# --- check_disk -------------------------------------------------------------

def test_disk_under_threshold_ok():
    out = "Filesystem 1024-blocks Used Available Capacity Mounted on\n/dev/sda1 100 33 67 33% /"
    assert check_disk(out, threshold=85).level == "ok"


def test_disk_over_threshold_warn():
    out = "/dev/sda1 100 88 12 88% /"
    assert check_disk(out, threshold=85).level == "warn"


def test_disk_critical_at_95():
    out = "/dev/sda1 100 96 4 96% /"
    assert check_disk(out, threshold=85).level == "crit"


# --- check_service_active ---------------------------------------------------

def test_service_active_ok():
    assert check_service_active("tailscaled", "active").level == "ok"


def test_service_inactive_crit():
    st = check_service_active("tailscaled", "failed")
    assert st.level == "crit"
    assert "tailscaled" in st.summary


# --- check_cron_log ---------------------------------------------------------

def test_cron_log_clean_is_ok():
    assert check_cron_log("loom", "absorb ok\n3 woven, 0 rejected\n").level == "ok"


def test_cron_log_with_error_is_warn():
    st = check_cron_log("loom", "starting\nTraceback (most recent call last):\nKeyError\n")
    assert st.level == "warn"
    assert "loom" in st.name


def test_cron_log_only_scans_tail():
    # an error far in the past (outside the tail window) must not fire
    log = "Traceback boom\n" + "\n".join(f"line {i} ok" for i in range(200))
    assert check_cron_log("x", log, tail_lines=50).level == "ok"


def test_cron_log_ignores_metric_counters():
    # "failed=0" / "error=0" are key=value metrics, not errors (real false positive
    # found live against the MeetTrack supervise log).
    log = "[supervise] launched=0 relaunched=0 queued=0 finished=0 failed=0\n"
    assert check_cron_log("meettrack", log).level == "ok"


def test_cron_log_matches_real_failure_word():
    # a genuine failure (word not followed by '=') must still fire
    assert check_cron_log("bebop", "run FAILED rc=1\n").level == "warn"


# --- triage (escalation + flap suppression) ---------------------------------

def _crit(name="disk"):
    return CheckStatus(name=name, level="crit", summary="boom")


def test_triage_all_ok_does_not_escalate():
    statuses = [CheckStatus("disk", "ok", "fine")]
    out = triage(statuses, {}, NOW)
    assert out["escalate"] is False
    assert out["fired"] == []


def test_triage_new_problem_escalates():
    out = triage([_crit()], {}, NOW)
    assert out["escalate"] is True
    assert [s.name for s in out["fired"]] == ["disk"]
    assert out["state"]["disk"]["level"] == "crit"


def test_triage_repeat_within_cooldown_is_suppressed():
    prior = {"disk": {"level": "crit", "ts": NOW - HOUR}}
    out = triage([_crit()], prior, NOW, cooldown_hours=6)
    assert out["escalate"] is False           # same level, within cooldown
    assert out["state"]["disk"]["ts"] == NOW - HOUR  # original alert time preserved


def test_triage_refires_after_cooldown():
    prior = {"disk": {"level": "crit", "ts": NOW - 7 * HOUR}}
    out = triage([_crit()], prior, NOW, cooldown_hours=6)
    assert out["escalate"] is True
    assert out["state"]["disk"]["ts"] == NOW   # re-stamped on refire


def test_triage_worsening_refires_within_cooldown():
    prior = {"disk": {"level": "warn", "ts": NOW - HOUR}}
    out = triage([_crit()], prior, NOW, cooldown_hours=6)
    assert out["escalate"] is True             # warn -> crit escalates immediately


def test_triage_recovery_clears_state():
    prior = {"disk": {"level": "crit", "ts": NOW - HOUR}}
    out = triage([CheckStatus("disk", "ok", "fine")], prior, NOW)
    assert out["escalate"] is False
    assert "disk" not in out["state"]          # recovered -> dropped from state


# --- check_orphan_processes -------------------------------------------------

# `ps -eo pid,ppid,etime,args` output. PPID 1 means the launching session died
# and the process was reparented to init.
PS_HEADER = "    PID    PPID     ELAPSED COMMAND"
CODEX = ("/home/dev/.nvm/versions/node/v22.22.3/lib/node_modules/@openai/codex/"
         "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex "
         "exec review -c sandbox_mode=read-only --model gpt-5.6-sol")


def _ps(*rows):
    return "\n".join([PS_HEADER, *rows]) + "\n"


def test_orphan_check_flags_a_weeks_old_codex_review():
    out = check_orphan_processes(_ps(f"2430396       1  47-20:29:37 {CODEX}"))
    assert out.name == "proc:orphans"
    assert out.level == "warn"
    assert "47d" in out.summary
    assert "2430396" in out.evidence


def test_orphan_check_counts_every_orphan_and_leads_with_the_oldest():
    out = check_orphan_processes(
        _ps(
            f"1413812       1  18-20:22:43 {CODEX}",
            f"2430396       1  47-20:29:37 {CODEX}",
            "1414648       1  18-20:22:16 codex-code-mode --port 1234",
        )
    )
    assert out.level == "warn"
    assert out.summary.startswith("3 orphaned codex process")
    assert "47d" in out.summary
    assert out.evidence.splitlines()[0].startswith("pid 2430396")


def test_orphan_check_ignores_codex_with_a_living_parent():
    out = check_orphan_processes(_ps(f"660312  660311  1-07:50:02 {CODEX}"))
    assert out.level == "ok"
    assert out.evidence == ""


def test_orphan_check_ignores_a_young_orphan():
    # a review that is merely slow is not a leak
    out = check_orphan_processes(_ps(f"1425942       1     2:47:02 {CODEX}"), min_hours=6)
    assert out.level == "ok"


def test_orphan_check_reads_short_elapsed_formats():
    out = check_orphan_processes(
        _ps(
            f"1 1 59:45 {CODEX}",          # mm:ss, young
            f"2 1 08:15:00 {CODEX}",       # hh:mm:ss, over the threshold
        ),
        min_hours=6,
    )
    assert out.level == "warn"
    assert out.summary.startswith("1 orphaned codex process")
    assert "8h" in out.summary


def test_orphan_check_ignores_non_codex_orphans():
    # plenty of daemons legitimately live under init
    out = check_orphan_processes(_ps("999       1  10-00:00:00 /usr/bin/tailscaled --state /var/lib"))
    assert out.level == "ok"


def test_orphan_check_survives_unusable_ps_output():
    for text in ("", PS_HEADER + "\n", "ps: command not found\n", "garbage\n\n"):
        out = check_orphan_processes(text)
        assert out.level == "ok"
