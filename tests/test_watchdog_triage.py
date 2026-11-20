"""Tests for the watchdog triage core — pure functions over collected signals."""
import re

from watchdog.triage import (
    CheckStatus,
    check_bebop_runs,
    check_cron_log,
    check_log_coverage,
    check_disk,
    check_orphan_processes,
    check_run_age,
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


# --- check_cron_log with run_end (a quiet log judged from its last good run) --

_RUN_END = re.compile(r"^run done$", re.MULTILINE)


def test_run_end_crash_after_the_last_good_run_is_warn():
    log = "run done\nTraceback (most recent call last):\nValueError: x\n"
    st = check_cron_log("q", log, run_end=_RUN_END)
    assert st.level == "warn"
    assert "Traceback" in st.evidence


def test_run_end_crash_followed_by_a_good_run_is_ok_while_still_in_the_tail():
    log = "run done\nTraceback (most recent call last):\nValueError: x\nrun done\n"
    assert check_cron_log("q", log).level == "warn"              # the plain tail still sees it
    assert check_cron_log("q", log, run_end=_RUN_END).level == "ok"


def test_run_end_reads_every_line_after_the_last_good_run_past_the_tail():
    # a crash block longer than the tail whose last 50 lines hold no error word
    log = ("run done\nTraceback (most recent call last):\n"
           + "\n".join(f"<p>page {i}</p>" for i in range(80)) + "\n")
    assert check_cron_log("q", log).level == "ok"                # plain tail: blind to it
    assert check_cron_log("q", log, run_end=_RUN_END).level == "warn"


def test_run_end_with_no_good_run_in_the_log_warns_on_its_own():
    # every run failed (a fresh log, even past the tail): its error words count
    crashes = "Traceback (most recent call last):\n" + "\n".join(f"<p>{i}</p>" for i in range(80))
    assert check_cron_log("q", crashes, run_end=_RUN_END).level == "warn"
    # the end pattern drifted from the start: no error word, still a warning
    drifted = "\n".join(f"run finished {i}" for i in range(200)) + "\n"
    st = check_cron_log("q", drifted, run_end=_RUN_END)
    assert st.level == "warn"
    assert "after its last finished run" in st.summary


def test_run_end_any_line_after_the_last_good_run_warns():
    # nothing follows a good run, so a line there is a run that did not finish,
    # or the end pattern drifted after years of matches
    log = "run done\n" * 1000 + "run finished\n" * 30
    st = check_cron_log("q", log, run_end=_RUN_END)
    assert st.level == "warn"
    assert "30 line(s)" in st.summary
    assert check_cron_log("q", "run done\n\n  \n", run_end=_RUN_END).level == "ok"   # blank lines


def test_run_end_empty_log_is_ok():
    assert check_cron_log("q", "", run_end=_RUN_END).level == "ok"
    assert check_cron_log("q", "\n\n", run_end=_RUN_END).level == "ok"


def test_run_end_good_run_as_last_line_is_ok():
    assert check_cron_log("q", "FAILED once\nrun done\n", run_end=_RUN_END).level == "ok"


# --- check_run_age (the last finished run must be recent) ---------------------

_TIMED_END = re.compile(r"^\[(?P<end_ts>[^\]\n]+)\] run done$", re.MULTILINE)
_T0 = 1_800_000_000                                    # 2027-01-15T08:00:00+00:00


def _done(epoch):
    from datetime import datetime, timezone
    return f"[{datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()}] run done\n"


def _aged(log, now, max_age_hours=3):
    status = check_cron_log("q", log, run_end=_TIMED_END)
    return check_run_age(status, log, _TIMED_END, now, max_age_hours=max_age_hours)


def test_run_age_recent_run_keeps_the_log_status():
    log = _done(_T0 - 2 * HOUR) + _done(_T0 - HOUR)
    assert _aged(log, _T0) == check_cron_log("q", log, run_end=_TIMED_END)


def test_run_age_boundary_is_ok_at_the_limit_and_warns_one_second_past_it():
    log = _done(_T0)
    assert _aged(log, _T0 + 3 * HOUR).level == "ok"
    st = _aged(log, _T0 + 3 * HOUR + 1)
    assert st.level == "warn" and st.name == "cron:q"
    assert "no q run has finished for 3.0h (limit 3h; last at 2027-01-15T08:00:00+00:00)" in st.summary
    assert st.evidence == "[2027-01-15T08:00:00+00:00] run done"


def test_run_age_keeps_the_log_evidence_when_runs_after_it_failed():
    log = _done(_T0) + "Traceback (most recent call last):\nValueError: x\n"
    st = _aged(log, _T0 + 4 * HOUR)
    assert st.level == "warn" and "has finished for 4.0h" in st.summary
    assert "Traceback" in st.evidence


def test_run_age_unreadable_time_warns():
    st = _aged("[yesterday] run done\n", _T0)
    assert st.level == "warn" and "cannot read the time" in st.summary
    assert "[yesterday]" in st.evidence


def test_run_age_with_no_finished_run_returns_the_log_status():
    for log in ("", "Traceback (most recent call last):\n"):
        assert _aged(log, _T0) == check_cron_log("q", log, run_end=_TIMED_END)


def test_run_age_never_turns_a_crit_into_a_warn():
    crit = CheckStatus("cron:q", "crit", "worse", evidence="the crit's lines")
    assert check_run_age(crit, _done(_T0), _TIMED_END, _T0, max_age_hours=3) is crit
    stale = check_run_age(crit, _done(_T0), _TIMED_END, _T0 + 4 * HOUR, max_age_hours=3)
    assert stale.level == "crit" and "has finished for 4.0h" in stale.summary
    assert stale.evidence == "the crit's lines"
    unreadable = check_run_age(crit, "[yesterday] run done\n", _TIMED_END, _T0, max_age_hours=3)
    assert unreadable.level == "crit" and "cannot read the time" in unreadable.summary


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


def test_cron_log_warnings_repeat_on_the_six_hour_cooldown_not_the_meet_one():
    # 6-7 Nov 2026: one real ingest_entries parser error (meet 11344) stayed in
    # the tail, so cron:meettrack-ingest re-sent every 6 h. Not muted (it was
    # real), not hourly (cron:* is not a live-meet check), and a crit fires at once.
    warn = CheckStatus("cron:meettrack-ingest", "warn", "2 error marker(s)")
    out = triage([warn], {}, NOW)
    assert out["escalate"] is True
    state = out["state"]
    for hours in (1, 3, 5.9):
        assert triage([warn], state, NOW + hours * HOUR)["escalate"] is False
    assert triage([warn], state, NOW + 6 * HOUR)["escalate"] is True
    crit = CheckStatus("cron:meettrack-ingest", "crit", "worse")
    assert triage([crit], state, NOW + HOUR)["escalate"] is True


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


def test_orphan_check_reports_an_orphan_whose_elapsed_time_is_unreadable():
    # a dropped row would hide the orphan forever; report it with an unknown age
    out = check_orphan_processes(_ps(f"777       1  12-34-56 {CODEX}"))
    assert out.level == "warn"
    assert "unknown" in out.summary
    assert "pid 777" in out.evidence


def test_orphan_check_does_not_match_neighbouring_command_names():
    out = check_orphan_processes(
        _ps(
            "801       1  10-00:00:00 /usr/local/bin/codexctl daemon",
            "802       1  10-00:00:00 codexd --serve",
        )
    )
    assert out.level == "ok"


def test_cron_log_ignores_json_zero_counters():
    # loom and diem write JSON summaries; a zero or null counter is not an error.
    log = '[2026-09-18T03:00:01+01:00] rc=0 {"distilled": 3, "failed": 0, "error": null, "errors": []}\n'
    assert check_cron_log("loom", log).level == "ok"


def test_cron_log_still_fires_on_nonzero_json_counter():
    log = '[2026-09-18T03:00:01+01:00] rc=0 {"distilled": 3, "failed": 2}\n'
    assert check_cron_log("loom", log).level == "warn"


def test_cron_log_still_fires_on_json_error_message():
    assert check_cron_log("diem", '   "error": "timeout after 900s (deadline backstop)"\n').level == "warn"


# --- check_log_coverage -----------------------------------------------------

def test_log_coverage_warns_when_no_configured_log_is_readable():
    st = check_log_coverage(found=[], missing=["loom", "meettrack-ingest"])
    assert st.level == "warn"
    assert "loom" in st.summary


def test_log_coverage_ok_when_some_logs_are_readable_and_names_the_missing():
    st = check_log_coverage(found=["loom"], missing=["meettrack-ingest"])
    assert st.level == "ok"
    assert "meettrack-ingest" in st.summary


def test_log_coverage_ok_when_nothing_is_configured():
    assert check_log_coverage(found=[], missing=[]).level == "ok"


def test_cron_log_ignores_words_inside_json_data_lists():
    # loom repeats its standing quarantine list and promoted article names in every
    # run summary. Those strings are data about items, not the outcome of this run.
    log = ('[2026-09-18T03:00:01+01:00] rc=0 {"distilled": 4, "failed": 0, "quarantined_items": '
           '[["22f8#2", "dead-letter: repeated weave exceptions"], ["bm2#2", "weave failed guards after retry"]], '
           '"limit_hit": false}\n'
           '[2026-09-18T03:00:02+01:00] promote rc=0 {"promoted": true, "articles": ["patterns/error-handling.md"]}\n')
    assert check_cron_log("loom", log).level == "ok"


def test_cron_log_still_fires_when_the_run_itself_failed_next_to_a_data_list():
    log = '[2026-09-18T03:00:01+01:00] rc=1 {"distilled": 0, "failed": 3, "quarantined_items": [["a#1", "x"]]}\n'
    assert check_cron_log("loom", log).level == "warn"
