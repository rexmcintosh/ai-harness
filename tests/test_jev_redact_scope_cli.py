"""jev.redact, jev.scope and the `jev` command line (the door for callers that are not Python)."""
import io
import json

import pytest

import jev
from jev import cli, client, redact, scope


# --- redact -----------------------------------------------------------------

def test_redact_text_removes_addresses_url_queries_and_token_shaped_strings():
    out = redact.redact_text("mail rex@example.com, GET https://x.example/a?apikey=abc123 key=" + "A1b2" * 12)
    assert "rex@example.com" not in out and "apikey" not in out and "A1b2A1b2A1b2" not in out


def test_redact_text_leaves_prose_and_code_whole():
    code = "@decorator\ndef f(x):\n    return x  # a very long line " + "word " * 80
    assert redact.redact_text(code) == code


def test_redact_state_walks_dicts_and_lists_but_not_keys():
    out = redact.redact_state({"rex@example.com": ["to rex@example.com", {"n": 3}]})
    assert out == {"rex@example.com": ["to <email>", {"n": 3}]}


def test_redact_log_drops_customer_rows_mail_subjects_item_lists_and_handles():
    text = ("2026-09-18 INFO  mary@example.com | Norris | seat trialing\n"
            "  mailed parent@example.com: Sam did a first session.\n"
            'rc=0 {"failed": 0, "quarantined_items": [["id#1", "note about a person"]], "limit_hit": false}\n'
            "  @somecreator 11h: genre fit 1 < 2\n")
    out = redact.redact_log(text)
    for private in ("Norris", "Sam", "about a person", "@somecreator", "example.com"):
        assert private not in out
    assert '"limit_hit": false' in out


def test_strip_json_data_lists_handles_nesting_and_an_unclosed_list():
    assert redact.strip_json_data_lists('x {"a_items": [["q\\"]", "failed"], ["z"]], "failed": 2}') == 'x {, "failed": 2}'
    assert "private" not in redact.strip_json_data_lists('{"n": 1, "articles": ["private note that was trunc')


# --- scope ------------------------------------------------------------------

def test_scope_refuses_paths_that_can_hold_personal_or_customer_data():
    assert scope.in_scope("/home/dev/.local/state/diem/drain.log")
    for path in ("/home/dev/projects/sat-prep/tmp/bento-sync.log", "/home/dev/projects/ai-harness/bebop/logs/cron.log",
                 "/home/dev/wiki/people/someone.md", "relative.log", ""):
        assert not scope.in_scope(path), path


def test_the_scope_note_says_what_the_owner_allowed_and_when():
    assert "manuscript" in scope.ALLOWED_NOTE.lower() and "2026-09-19" in scope.ALLOWED_NOTE


# --- scope: which repositories' work may be described to Jev -----------------

FIVE = {"ai-harness", "swimtrack", "swimtrack-website", "ultimate-portugal", "aris-management-website"}


def test_the_repo_allow_list_is_exactly_the_five_measured_repositories():
    # Adding a repository is an owner decision: this test is meant to be in the way.
    assert scope.IN_SCOPE_REPOS == FIVE and isinstance(scope.IN_SCOPE_REPOS, frozenset)


@pytest.mark.parametrize("repo", sorted(FIVE))
def test_each_listed_repo_is_in_scope(repo):
    assert scope.repo_in_scope(repo) is True
    assert scope.repo_in_scope(repo, "2026-09-19-fix-a-typo") is True


@pytest.mark.parametrize("repo", [
    "sat-prep", "monthly-bidding", "romance-empire", "tax-advisor", "brand-new-repo",   # not listed
    None, "", "none", 0, ["ai-harness"],                                                # not a repo name
    "AI-Harness", "Swimtrack",                                                          # exact match only
    "ai-harness-fork", "swimtrack-coach", " ai-harness", "ai-harness ",                 # longer names
    "/home/dev/projects/ai-harness", "projects/ai-harness", "../ai-harness",            # a path is not a name
])
def test_anything_else_is_refused(repo):
    assert scope.repo_in_scope(repo) is False


@pytest.mark.parametrize("name", ["2026-09-19-bebop-briefing-fix", "2026-09-19-Tax-export", "gmail-filter-rules",
                                  "2026-09-01-sat-prep-feedback-copy"])
def test_a_listed_repo_is_refused_when_the_name_of_the_work_holds_an_out_of_scope_word(name):
    assert scope.repo_in_scope("ai-harness", name) is False


def test_the_repo_rule_leaves_the_path_rule_and_its_word_list_alone():
    assert scope.OUT_OF_SCOPE == ("sat-prep", "attainprep", "bento", "bebop", "tax", "finance", "rent",
                                  "swimtrack-coach", "gmail", "mail", "/wiki/")
    assert scope.in_scope("/home/dev/.local/state/diem/drain.log")


def test_the_scope_note_records_the_repo_allow_list():
    note = scope.ALLOWED_NOTE
    assert "allow-list" in note and "IN_SCOPE_REPOS" in note and "backlog" in note and "refused" in note


# --- the watchdog keeps working through the shared client --------------------

def test_the_watchdog_shadow_uses_the_shared_client_and_keeps_its_old_names():
    from watchdog import jev_shadow
    assert jev_shadow.MODEL == jev.MODEL
    assert jev_shadow.load_key is client.load_key
    assert jev_shadow.in_scope is scope.in_scope
    assert jev_shadow.redact is redact.redact_log
    for name in ("_http_post", "_EMAIL", "_TOKENISH", "_URL_QUERY", "_NoRedirect", "_OPENER"):
        assert hasattr(jev_shadow, name), name      # tools/jev_council imports these


# --- command line -----------------------------------------------------------

@pytest.fixture
def live(monkeypatch, tmp_path):
    monkeypatch.delenv("JEV_DISABLED", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-key")
    monkeypatch.setattr(client, "http_post", lambda req, key, timeout: {
        "model": req["model"], "usage": {"input_tokens": 10},
        "answers": {name: {"type": "noul", "noul": 0.25} for name in req["questions"]}})
    return tmp_path


def run_cli(argv, stdin=""):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, stdin=io.StringIO(stdin), stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_cli_ask_reads_one_request_from_stdin_and_prints_one_json_line(live):
    code, out, _ = run_cli(["ask", "--project", "up", "--task", "prefilter"],
                           json.dumps({"state": {"title": "t"}, "questions": {"keep": jev.noul("?")}}))
    assert code == 0 and json.loads(out)["answers"]["keep"]["noul"] == 0.25


def test_cli_batch_is_jsonl_in_jsonl_out_in_order_with_shared_questions(live, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"keep": jev.noul("?")}))
    lines = "\n".join(json.dumps({"id": i, "state": f"s{i}"}) for i in range(3)) + "\n"
    code, out, _ = run_cli(["batch", "--project", "up", "--task", "prefilter", "--questions", str(qfile)], lines)
    rows = [json.loads(l) for l in out.splitlines()]
    assert code == 0 and [r["id"] for r in rows] == [0, 1, 2] and rows[0]["answers"]["keep"]["noul"] == 0.25


def test_cli_batch_lets_a_caller_pin_its_own_model(live, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"keep": jev.noul("?")}))
    code, out, _ = run_cli(["batch", "--project", "up", "--task", "p", "--questions", str(qfile), "--model", "jev-1.13.0"],
                           json.dumps({"id": "a", "state": "s"}) + "\n")
    assert json.loads(out)["model"] == "jev-1.13.0"


def test_cli_failures_are_one_json_error_line_and_exit_1_never_a_traceback():
    code, out, err = run_cli(["ask", "--project", "up", "--task", "p"],
                             json.dumps({"state": "s", "questions": {"q": jev.noul("?")}}))   # disabled in tests
    assert code == 1 and "JEV_DISABLED" in json.loads(out)["error"] and "Traceback" not in err
    code, out, _ = run_cli(["ask", "--project", "up", "--task", "p"], "not json")
    assert code == 1 and "error" in json.loads(out)


def test_cli_batch_reports_a_bad_item_in_place_and_still_answers_the_rest(live, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"keep": jev.noul("?")}))
    code, out, _ = run_cli(["batch", "--project", "up", "--task", "p", "--questions", str(qfile)],
                           json.dumps({"id": 1, "state": "s"}) + "\nnot json\n" + json.dumps({"id": 3, "state": "s"}) + "\n")
    rows = [json.loads(l) for l in out.splitlines()]
    assert code == 0 and "answers" in rows[0] and "error" in rows[1] and "answers" in rows[2]


def test_cli_usage_prints_a_table_from_the_ledger(live, monkeypatch, tmp_path):
    monkeypatch.setenv("JEV_USAGE_LOG", str(tmp_path / "u.jsonl"))
    jev.ask("s", {"q": jev.noul("?")}, project="watchdog", task="log-tail")
    code, out, _ = run_cli(["usage"])
    assert code == 0 and "watchdog" in out and "log-tail" in out


def test_cli_never_prints_the_key(live):
    code, out, err = run_cli(["doctor"])
    assert "test-key" not in out + err


def test_cli_turns_any_unexpected_fault_into_one_json_error_line(live, monkeypatch, tmp_path):
    qfile = tmp_path / "q.json"
    qfile.write_text(json.dumps({"keep": jev.noul("?")}))
    def boom(*a, **k):
        raise MemoryError("anything at all, with test-key inside")
    monkeypatch.setattr(client, "ask_many", boom)
    code, out, err = run_cli(["batch", "--project", "up", "--task", "p", "--questions", str(qfile)],
                             json.dumps({"id": 1, "state": "s"}) + "\n")
    assert code == 1 and "error" in json.loads(out) and "Traceback" not in err and "test-key" not in out
