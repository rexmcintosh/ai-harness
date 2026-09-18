"""Jev shadow mode: a second opinion on cron-log tails that is only ever written to a log.

Nothing here may change what the watchdog alerts on, and no failure here may break a poll.
"""
import json

from watchdog import jev_shadow as js


# --- redact -----------------------------------------------------------------

def test_redact_removes_email_addresses():
    assert "rex@example.com" not in js.redact("skip rex@example.com: test address")


def test_redact_drops_customer_rows_entirely():
    out = js.redact("  mary@example.com | Norris | seat trialing | students 1")
    assert "Norris" not in out and "example.com" not in out


def test_redact_drops_mail_subject_lines_that_can_carry_names():
    out = js.redact("  mailed parent@example.com: Sam did a first session. Two quick questions.")
    assert "Sam" not in out


def test_redact_removes_wiki_article_lists():
    line = '[2026-09-18] promote rc=0 {"promoted": true, "articles": ["people/someone.md", "places/madrid.md"], "n": 2}'
    out = js.redact(line)
    assert "people/someone.md" not in out
    assert '"promoted": true' in out          # the part the judgement needs survives


def test_redact_removes_long_token_like_strings_and_url_queries():
    out = js.redact("GET https://x.example/rest/v1/t?apikey=abc123&select=* key=" + "A1b2" * 12)
    assert "apikey" not in out and "A1b2A1b2A1b2" not in out


def test_redact_removes_social_handles():
    assert "@somecreator" not in js.redact("  @somecreator 11h: genre fit 1 < 2")


def test_redact_keeps_ordinary_error_lines_intact():
    line = "2026-09-18 02:00:02 could not reach results server, giving up after 5 tries"
    assert js.redact(line) == line


# --- request shape and banding ---------------------------------------------

def test_request_pins_an_exact_model_version_not_a_moving_alias():
    req = js.build_request("some log tail")
    assert req["model"] == "jev-1.13.0"
    assert req["state"] == "some log tail"
    assert {q["type"] for q in req["questions"].values()} == {"noul"}
    assert set(req["questions"]) == {"needs_human", "latest_run_failed"}


def test_band_edges():
    assert js.band(0.0) == "ok"
    assert js.band(0.29) == "ok"
    assert js.band(0.3) == "gray"
    assert js.band(0.69) == "gray"
    assert js.band(0.7) == "alert"
    assert js.band(1.0) == "alert"


# --- judge never raises -----------------------------------------------------

def _good_reply(needs=0.9, latest=0.8):
    return {"model": "jev-1.13.0", "usage": {"input_tokens": 1500, "output_tokens": 0},
            "answers": {"needs_human": {"type": "noul", "noul": needs},
                        "latest_run_failed": {"type": "noul", "noul": latest}}}


def test_judge_returns_the_two_probabilities():
    got = js.judge("tail", key="k", transport=lambda req, key, timeout: _good_reply(0.91, 0.12))
    assert got["needs_human"] == 0.91 and got["latest_run_failed"] == 0.12
    assert got["input_tokens"] == 1500


def test_judge_returns_none_when_the_transport_raises():
    def boom(req, key, timeout):
        raise OSError("network down")
    assert js.judge("tail", key="k", transport=boom) is None


def test_judge_returns_none_on_a_malformed_reply():
    assert js.judge("tail", key="k", transport=lambda *a: {"answers": {}}) is None


# --- the shadow pass --------------------------------------------------------

def _pass(tmp_path, logs, judge, key="k", **kw):
    return js.shadow_pass(logs, now_epoch=1_800_000_000, key=key, judge=judge,
                          log_path=tmp_path / "jev-shadow.jsonl",
                          state_path=tmp_path / "jev-shadow-state.json", **kw)


def _records(tmp_path):
    p = tmp_path / "jev-shadow.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def test_shadow_pass_writes_one_record_with_both_opinions(tmp_path):
    n = _pass(tmp_path, [("loom", "absorb start\nexit rc=2\n", "ok")],
              judge=lambda tail, key: {"needs_human": 0.95, "latest_run_failed": 0.9,
                                       "model": "jev-1.13.0", "input_tokens": 40, "seconds": 0.6})
    assert n == 1
    (rec,) = _records(tmp_path)
    assert rec["log"] == "loom" and rec["regex_level"] == "ok"
    assert rec["jev_needs_human"] == 0.95 and rec["jev_band"] == "alert"
    assert rec["agree"] is False             # rule said ok, Jev says alert: the interesting case
    assert "exit rc=2" in rec["tail"]


def test_shadow_pass_stores_only_the_redacted_tail(tmp_path):
    _pass(tmp_path, [("bento", "  mary@example.com | Norris | seat trialing\nbento-sync failed: Gateway Timeout\n", "warn")],
          judge=lambda tail, key: {"needs_human": 0.8, "latest_run_failed": 0.8})
    (rec,) = _records(tmp_path)
    assert "Norris" not in rec["tail"] and "Gateway Timeout" in rec["tail"]


def test_shadow_pass_sends_the_redacted_tail_not_the_raw_one(tmp_path):
    seen = []
    _pass(tmp_path, [("bento", "skip rex@example.com: test address\n", "ok")],
          judge=lambda tail, key: seen.append(tail) or {"needs_human": 0.1, "latest_run_failed": 0.1})
    assert seen and "rex@example.com" not in seen[0]


def test_shadow_pass_skips_a_tail_that_has_not_changed(tmp_path):
    calls = []
    judge = lambda tail, key: calls.append(1) or {"needs_human": 0.1, "latest_run_failed": 0.1}
    logs = [("loom", "absorb done rc=0\n", "ok")]
    _pass(tmp_path, logs, judge)
    _pass(tmp_path, logs, judge)
    assert len(calls) == 1 and len(_records(tmp_path)) == 1


def test_shadow_pass_judges_again_when_the_tail_changes(tmp_path):
    judge = lambda tail, key: {"needs_human": 0.1, "latest_run_failed": 0.1}
    _pass(tmp_path, [("loom", "run 1 rc=0\n", "ok")], judge)
    _pass(tmp_path, [("loom", "run 1 rc=0\nrun 2 rc=0\n", "ok")], judge)
    assert len(_records(tmp_path)) == 2


def test_shadow_pass_does_nothing_without_a_key(tmp_path):
    calls = []
    n = _pass(tmp_path, [("loom", "x\n", "ok")], judge=lambda t, k: calls.append(1), key=None)
    assert n == 0 and not calls and _records(tmp_path) == []


def test_shadow_pass_survives_a_judge_that_raises(tmp_path):
    def boom(tail, key):
        raise RuntimeError("anything")
    assert _pass(tmp_path, [("loom", "x\n", "ok")], judge=boom) == 0


def test_shadow_pass_retries_next_poll_when_the_judge_had_no_answer(tmp_path):
    logs = [("loom", "x\n", "ok")]
    _pass(tmp_path, logs, judge=lambda t, k: None)             # outage: nothing recorded
    _pass(tmp_path, logs, judge=lambda t, k: {"needs_human": 0.1, "latest_run_failed": 0.1})
    assert len(_records(tmp_path)) == 1


def test_shadow_pass_stops_when_the_time_budget_is_spent(tmp_path):
    clock = iter([0.0, 0.0, 100.0, 100.0, 100.0, 100.0])
    n = _pass(tmp_path, [("a", "1\n", "ok"), ("b", "2\n", "ok")],
              judge=lambda t, k: {"needs_human": 0.1, "latest_run_failed": 0.1},
              budget_seconds=20, clock=lambda: next(clock))
    assert n == 1


def test_shadow_pass_only_looks_at_the_last_50_lines(tmp_path):
    seen = []
    text = "\n".join(f"line {i}" for i in range(200)) + "\n"
    _pass(tmp_path, [("big", text, "ok")],
          judge=lambda tail, key: seen.append(tail) or {"needs_human": 0.1, "latest_run_failed": 0.1})
    assert seen[0].splitlines()[0] == "line 150" and len(seen[0].splitlines()) == 50


# --- key loading ------------------------------------------------------------

def test_load_key_prefers_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "from-env")
    assert js.load_key(env_file=tmp_path / "none") == "from-env"


def test_load_key_reads_the_env_file_without_a_shell(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    f = tmp_path / ".env"
    f.write_text('OTHER=1\nTYPESAFE_API_KEY="from-file"\n')
    assert js.load_key(env_file=f) == "from-file"


def test_load_key_is_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    assert js.load_key(env_file=tmp_path / "none") is None


# --- report -----------------------------------------------------------------

def test_summarize_counts_agreement_and_lists_disagreements():
    recs = [
        {"log": "a", "regex_level": "ok", "jev_band": "ok", "agree": True, "jev_needs_human": 0.05, "ts": 1},
        {"log": "a", "regex_level": "warn", "jev_band": "ok", "agree": False, "jev_needs_human": 0.04, "ts": 2},
        {"log": "b", "regex_level": "ok", "jev_band": "alert", "agree": False, "jev_needs_human": 0.97, "ts": 3},
        {"log": "b", "regex_level": "ok", "jev_band": "gray", "agree": None, "jev_needs_human": 0.5, "ts": 4},
    ]
    s = js.summarize(recs)
    assert s["records"] == 4 and s["agree"] == 1 and s["disagree"] == 2 and s["gray"] == 1
    assert s["rule_alert_jev_ok"] == 1 and s["rule_ok_jev_alert"] == 1


# --- the shipped config -----------------------------------------------------

def test_shipped_config_shadows_operations_logs_only():
    # Data scope agreed 2026-09-18: public and operations data only. Logs that carry
    # customer, student or personal-email detail must never be listed here.
    import tomllib
    from pathlib import Path
    cfg = tomllib.loads((Path(js.__file__).parent / "monitors.toml").read_text())["jev_shadow"]
    assert cfg["enabled"] is True
    paths = [item["log"] for item in cfg["logs"]]
    assert paths and all(p.startswith("/") for p in paths)
    assert len({item["name"] for item in cfg["logs"]}) == len(cfg["logs"])
    assert all(js.in_scope(p) for p in paths)


def test_redact_removes_nested_item_lists_whole():
    line = ('rc=0 {"failed": 0, "quarantined_items": [["id#1", "note about a person"], '
            '["id#2", "second private note"]], "limit_hit": false}')
    out = js.redact(line)
    assert "private note" not in out and "about a person" not in out
    assert '"limit_hit": false' in out


# --- state durability (council review 2026-09-18) ---------------------------

def test_state_is_saved_after_each_record_so_a_kill_mid_pass_does_not_resend(tmp_path):
    def judge(tail, key):
        if "second" in tail:
            raise KeyboardInterrupt          # the process dies during the second log
        return {"needs_human": 0.1, "latest_run_failed": 0.1}
    try:
        _pass(tmp_path, [("a", "first\n", "ok"), ("b", "second\n", "ok")], judge)
    except KeyboardInterrupt:
        pass
    state = json.loads((tmp_path / "jev-shadow-state.json").read_text())
    assert "a" in state and "b" not in state


def test_a_failed_state_write_keeps_the_last_good_state(tmp_path, monkeypatch):
    judge = lambda tail, key: {"needs_human": 0.1, "latest_run_failed": 0.1}
    _pass(tmp_path, [("a", "one\n", "ok")], judge)
    good = (tmp_path / "jev-shadow-state.json").read_text()
    import os as _os
    def no_replace(src, dst):
        raise OSError("disk full")
    monkeypatch.setattr(_os, "replace", no_replace)
    _pass(tmp_path, [("a", "two\n", "ok")], judge)          # must not raise, must not corrupt
    assert (tmp_path / "jev-shadow-state.json").read_text() == good


# --- council review round 2: redaction edges and outbound integrity ---------

def test_redact_drops_a_customer_row_that_has_a_timestamp_prefix():
    out = js.redact("2026-09-18 02:00:01 INFO  mary@example.com | Norris | seat trialing")
    assert "Norris" not in out and "example.com" not in out


def test_redact_drops_a_customer_row_with_tabs_or_no_spaces():
    assert "Norris" not in js.redact("mary@example.com|Norris|seat trialing")
    assert "Norris" not in js.redact('\t"mary@example.com"\t| Norris | seat')


def test_redact_cuts_an_unclosed_item_list_to_the_end_of_the_line():
    out = js.redact('rc=0 {"failed": 0, "quarantined_items": [["id#1", "private note that the logger trunc')
    assert "private note" not in out and '"failed": 0' in out


def test_judge_rejects_a_reply_from_a_different_model_version():
    reply = _good_reply(); reply["model"] = "jev-1.14.0"
    assert js.judge("tail", key="k", transport=lambda *a: reply) is None


def test_redirects_are_refused_so_the_key_never_follows_one():
    assert js._NoRedirect().redirect_request(None, None, 302, "Found", {}, "https://elsewhere.example/") is None


def test_out_of_scope_paths_are_named_in_code_not_only_in_a_test():
    assert js.in_scope("/home/dev/.local/state/diem/drain.log")
    for path in ("/home/dev/projects/sat-prep/tmp/bento-sync.log", "/home/dev/projects/ai-harness/bebop/logs/cron.log",
                 "/home/dev/projects/.session-gc/rent-verification.log", "relative/path.log", ""):
        assert not js.in_scope(path), path


# --- test isolation ---------------------------------------------------------

def test_a_plain_main_call_in_a_test_never_reaches_the_real_api(monkeypatch, tmp_path, capsys):
    # Regression (2026-09-18): once shadow mode was enabled in monitors.toml, every test
    # that called run.main() made real Jev calls with the real key and wrote records with
    # a fake test clock into the live watchdog/logs/jev-shadow.jsonl.
    import watchdog.run as run
    for var, name in (("WATCHDOG_STATE", "s.json"), ("WATCHDOG_PENDING", "p.json"),
                      ("WATCHDOG_DELIVERY_LAST", "l.json"), ("WATCHDOG_METRICS", "m.json")):
        monkeypatch.setenv(var, str(tmp_path / name))
    calls = []

    class Opener:
        def open(self, request, timeout=None):
            calls.append(request.full_url)
            raise OSError("blocked in tests")
    monkeypatch.setattr(js, "_OPENER", Opener())
    run.main([])
    assert calls == []
