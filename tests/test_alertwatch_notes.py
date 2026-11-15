import json
import os

from alertwatch import notes
from alertwatch.config import load_config


def record(result, session="sess-1"):
    return {"run_id": "r1", "session_id": session, "result": result,
            "entries": [{"id": "a", "first_line": "results missing"}]}


def quiet():
    return {"note": "nothing to do", "entries": [{"id": "a", "verdict": "not_a_failure"},
                                                 {"id": "b", "verdict": "same_as_run"}],
            "actions": [], "needs_rex": [], "merges": [], "pending_checks": []}


def test_no_note_for_nothing_real():
    assert notes.run_note(record(quiet())) is None


def test_note_has_needs_you_and_resume_footer():
    result = quiet() | {"note": "alert-watch: Leiria results late\nSaw: x.",
                        "needs_rex": ["do it: write the 3 club rows"]}
    text = notes.run_note(record(result))
    assert text.startswith("alert-watch: Leiria results late")
    assert "Needs you: do it: write the 3 club rows" in text
    assert text.rstrip().endswith("Open the run: claude --resume sess-1  (in ~/projects)")


def test_action_makes_a_note():
    result = quiet() | {"actions": [{"kind": "restart", "what": "writer 11539", "checked": "ok"}]}
    assert notes.run_note(record(result)) is not None


def test_failure_note_names_failed_entries_and_run():
    text = notes.failure_note({"run_id": "r1", "reason": "timed out after 3600 s",
                               "session_id": "s9"}, ["a", "b"])
    assert "r1" in text and "timed out" in text and "a, b" in text and "claude --resume s9" in text


def test_pause_note_exact_copy():
    assert notes.pause_note("3 failed runs in a row") == (
        "alert-watch paused itself: 3 failed runs in a row. "
        "Remove ~/.local/state/alert-watch/PAUSED to start it again.")


def test_notes_have_no_em_dash():
    texts = [
        notes.run_note(record(quiet() | {"needs_rex": ["x"]})),
        notes.failure_note({"run_id": "r", "reason": "x", "session_id": ""}, []),
        notes.cap_note("runs_hour", 6, 6),
        notes.limit_note(1794738000.0),
        notes.pause_note("x"),
    ]
    assert all("\u2014" not in t for t in texts)


def test_send_tags_its_own_source(tmp_path):
    log = tmp_path / "sent.jsonl"
    fake = tmp_path / "tg-send"
    fake.write_text("#!/usr/bin/env python3\nimport json, os, sys\n"
                    f"open({str(log)!r}, 'a').write(json.dumps({{'argv': sys.argv[1:], "
                    "'source': os.environ.get('ALERT_SOURCE'), 'text': sys.stdin.read()}) + '\\n')\n")
    fake.chmod(0o755)
    cfg = load_config(env={"ALERT_WATCH_TG_SEND": str(fake), "ALERT_WATCH_STATE": str(tmp_path)})
    assert notes.send(cfg, "hello") == "accepted"
    (sent,) = [json.loads(l) for l in log.read_text().splitlines()]
    assert sent == {"argv": ["7735693897", "-"], "source": "alert-watch", "text": "hello"}


def test_send_reports_failure_and_uncertain(tmp_path):
    for rc, want in ((1, "failed"), (3, "uncertain")):
        fake = tmp_path / f"tg-{rc}"
        fake.write_text(f"#!/usr/bin/env bash\ncat >/dev/null\nexit {rc}\n")
        fake.chmod(0o755)
        cfg = load_config(env={"ALERT_WATCH_TG_SEND": str(fake)})
        assert notes.send(cfg, "x") == want


def test_send_missing_sender_is_failed(tmp_path):
    cfg = load_config(env={"ALERT_WATCH_TG_SEND": str(tmp_path / "nope")})
    assert notes.send(cfg, "x") == "failed"


def test_send_turns_capture_off(tmp_path):
    log = tmp_path / "env.txt"
    fake = tmp_path / "tg-send"
    fake.write_text(f"#!/usr/bin/env bash\ncat >/dev/null\necho \"$ALERT_INBOX_DIR\" > '{log}'\n")
    fake.chmod(0o755)
    cfg = load_config(env={"ALERT_WATCH_TG_SEND": str(fake)})
    notes.send(cfg, "x")
    assert log.read_text().strip() == "off"


def test_limit_note_part_way_says_no_retry():
    text = notes.limit_note(1794738000.0, part_way=True)
    assert "part way" in text and "will not be tried again" in text and "\u2014" not in text
