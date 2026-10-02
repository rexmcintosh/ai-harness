# tests/diem/test_findings.py
import json
import fcntl
import os
import threading
import time
from datetime import datetime
from pathlib import Path

import pytest

from diem import findings as findings_mod
from diem.findings import (Findings, backfill, is_blocking, one_line,
                           parse_recommendation, record_review)
from diem.queue import new_item

RID = "b7f96582041443b79b941d762a938d44"


def _review_md(rec: str) -> str:
    return ("[panel: code-review · rigor: daily]\n\n## Council\n\n"
            "**Question:** Review this:\n\ndiff --git a/x b/x\n+### Recommendation fake\n\n"
            f"### Recommendation (confidence 8/10)\n\n{rec}\n\n"
            "**Consensus:**\n- something\n\n#### Eng Manager · codex — concerns\n")


# --- parsing verdicts -------------------------------------------------------------

def test_parse_takes_the_paragraph_under_the_real_heading():
    text = _review_md("Request changes before merge. The lock is taken\ntoo late.")
    assert parse_recommendation(text) == ("Request changes before merge. The lock is "
                                          "taken too late.")


def test_parse_heading_without_confidence_and_missing_heading():
    assert parse_recommendation("### Recommendation\n\nApprove.\n") == "Approve."
    assert parse_recommendation("## Council\n\nno verdict here\n") is None
    assert parse_recommendation("### Recommendation\n\n") is None


@pytest.mark.parametrize("rec", [
    "Request changes before merge. The blocking defect is X.",
    "Request changes — do not merge as-is. There is a bug.",
    "Request a small script hardening change before merge; the doc is fine.",
    "Changes requested. Before merge: (1) enforce the timeout.",
    "**Request changes.** Fix the race.",
    "Block. This deletes data.",
    "Do not merge: the migration is irreversible.",
    "Reject this change; it leaks the key.",
])
def test_request_changes_is_blocking(rec):
    assert is_blocking(rec) is True


@pytest.mark.parametrize("rec", [
    "Approve. No security or correctness blocker is established.",
    "Approve with minor, non-blocking follow-ups. Do not merge without a note.",
    "Approve-with-fixes. This is a documentation PR.",
    "Approve with changes. The core design shift is sound.",
    "**Approve.** Ship it.",
    "",
    None,
])
def test_approve_is_not_blocking(rec):
    assert is_blocking(rec) is False


# The "request a ..." family only blocks when it plainly asks for a change before
# merge. A request for something after merge (a follow-up issue, a later ticket) is
# not a finding, and neither is wording that mixes both.
@pytest.mark.parametrize("rec", [
    "Request a small script hardening change before merge; the doc is fine.",
    "Request a fix before merging. The lock is taken too late.",
    "Request an explicit guard prior to merge: the path can escape.",
    "Request minor changes pre-merge. Two typos in the migration.",
    "**Request a** one-line fix before this merges.",
])
def test_request_a_pre_merge_change_is_blocking(rec):
    assert is_blocking(rec) is True


@pytest.mark.parametrize("rec", [
    "Request a follow-up issue after merge.",
    "Request a follow-up issue after merge; the change itself is fine.",
    "Request a follow-up ticket; approve the change as-is.",
    "Request a post-merge cleanup of the old flag.",
    "Request minor, non-blocking tidy-ups before merge.",
    "Request a follow-up before merge or after, either is fine.",
    "Request a second opinion.",
    "Request small wording tweaks.",
])
def test_request_a_follow_up_is_not_blocking(rec):
    assert is_blocking(rec) is False


def test_one_line_is_first_sentence_and_bounded():
    assert one_line("Request changes before merge. More text here.") == \
        "Request changes before merge."
    long = "Request changes " + "x" * 400
    out = one_line(long)
    assert len(out) <= 160 and out.endswith("...")
    assert "\n" not in one_line("Request\nchanges\x07 now. Tail.")


# --- the findings file -----------------------------------------------------------

def _write_review(tmp_path, repo="splash_poller", id_=RID, rec="Request changes. Bug."):
    out = tmp_path / "out" / "reviews" / f"{repo}-{id_}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_review_md(rec))
    return out


def _item(id_=RID, **payload):
    it = new_item("review", payload or {"repo": "/home/dev/projects/splash_poller",
                                        "range": "aaa..bbb", "head": "bbb"},
                  created="2026-09-25T21:00:01")
    it.id = id_
    return it


def test_record_review_appends_one_new_finding(tmp_path):
    out = _write_review(tmp_path)
    added = record_review(tmp_path / "state", _item(), str(out),
                          now=datetime(2026, 9, 25, 21, 5))
    assert added is True
    recs = Findings(tmp_path / "state").all()
    assert recs == [{
        "id": RID, "repo": "splash_poller", "reviewed": "aaa..bbb",
        "date": "2026-09-25", "recommendation": "Request changes.",
        "output_path": str(out), "status": "new"}]


def test_record_review_skips_approvals(tmp_path):
    out = _write_review(tmp_path, rec="Approve. Looks fine.")
    assert record_review(tmp_path / "state", _item(), str(out),
                         now=datetime(2026, 9, 25)) is False
    assert Findings(tmp_path / "state").all() == []


def test_record_review_is_idempotent_and_keeps_an_ack(tmp_path):
    state = tmp_path / "state"
    out = _write_review(tmp_path)
    now = datetime(2026, 9, 25)
    record_review(state, _item(), str(out), now=now)
    assert Findings(state).ack([RID[:8]]) == [RID]
    assert record_review(state, _item(), str(out), now=now) is False
    recs = Findings(state).all()
    assert len(recs) == 1 and recs[0]["status"] == "acked"


def test_record_review_diff_payload_and_fail_open(tmp_path):
    state = tmp_path / "state"
    out = _write_review(tmp_path)
    it = _item(repo="/r/swim", diff=True)
    assert record_review(state, it, str(out), now=datetime(2026, 9, 25)) is True
    assert Findings(state).all()[0]["reviewed"] == "working-tree diff"
    # a missing output or a broken findings file never raises
    assert record_review(state, _item("f" * 32), str(tmp_path / "nope.md"),
                         now=datetime(2026, 9, 25)) is False
    (state / "findings.json").write_text("{not json")
    assert record_review(state, _item("e" * 32), str(out),
                         now=datetime(2026, 9, 25)) is False


def test_ack_unknown_and_ambiguous_ids(tmp_path):
    state = tmp_path / "state"
    for id_ in ("abc11111" + "0" * 24, "abc22222" + "0" * 24):
        out = _write_review(tmp_path, id_=id_)
        record_review(state, _item(id_), str(out), now=datetime(2026, 9, 25))
    f = Findings(state)
    assert f.ack(["abc"]) == []            # ambiguous prefix acks nothing
    assert f.ack(["zzz"]) == []
    assert f.ack(["abc2"]) == ["abc22222" + "0" * 24]
    assert [r["status"] for r in Findings(state).all()] == ["new", "acked"]
    assert len(Findings(state).new()) == 1


def test_backfill_reads_recent_outputs_once(tmp_path):
    state = tmp_path / "state"
    outputs = tmp_path / "out"
    old = _write_review(tmp_path, id_="0" * 32)
    t_old = time.time() - 20 * 86400
    os.utime(old, (t_old, t_old))
    _write_review(tmp_path, repo="aris-management-website", id_="1" * 32,
                  rec="Approve. Fine.")
    _write_review(tmp_path, repo="ultimate-portugal", id_="2" * 32)
    arch = state / "archive"
    arch.mkdir(parents=True)
    (arch / f"{'2' * 32}.json").write_text(json.dumps({
        "id": "2" * 32, "type": "review", "created": "2026-09-24T21:00:00",
        "payload": {"repo": "/home/dev/projects/ultimate-portugal",
                    "range": "c1..c2", "head": "c2"}}))
    now = datetime(2026, 9, 28)
    assert backfill(state, outputs, days=14, now=now) == 1
    recs = Findings(state).all()
    assert [(r["repo"], r["reviewed"], r["date"]) for r in recs] == \
        [("ultimate-portugal", "c1..c2", "2026-09-24")]
    assert backfill(state, outputs, days=14, now=now) == 0


# --- concurrent writers ----------------------------------------------------------

def _rec(id_, status="new"):
    return {"id": id_, "repo": "r", "reviewed": "a..b", "date": "2026-09-25",
            "recommendation": "Request changes.", "output_path": "/x.md",
            "status": status}


A, B, C = "a" * 32, "b" * 32, "c" * 32


def test_stale_add_keeps_another_writers_record(tmp_path):
    first, second = Findings(tmp_path), Findings(tmp_path)   # both load an empty file
    assert first.add(_rec(A)) is True
    assert second.add(_rec(B)) is True                       # stale view, must not drop A
    assert sorted(r["id"] for r in Findings(tmp_path).all()) == [A, B]
    assert second.add(_rec(A)) is False                      # reload sees A: still idempotent


def test_stale_ack_keeps_a_new_record_and_add_keeps_an_ack(tmp_path):
    Findings(tmp_path).add(_rec(A))
    acker, adder = Findings(tmp_path), Findings(tmp_path)    # both see only A, status new
    adder.add(_rec(B))
    assert acker.ack([A[:8]]) == [A]                         # must not drop B
    adder.add(_rec(C))                                       # must not undo the ack of A
    recs = {r["id"]: r["status"] for r in Findings(tmp_path).all()}
    assert recs == {A: "acked", B: "new", C: "new"}


def test_ack_resolves_prefixes_against_the_file_not_a_stale_view(tmp_path):
    Findings(tmp_path).add(_rec("abc1" + "0" * 28))
    stale = Findings(tmp_path)
    Findings(tmp_path).add(_rec("abc2" + "0" * 28))
    assert stale.ack(["abc"]) == []                          # ambiguous on disk now
    assert stale.ack(["abc2"]) == ["abc2" + "0" * 28]


def test_file_broken_by_another_writer_is_not_overwritten(tmp_path):
    f = Findings(tmp_path)
    (tmp_path / "findings.json").write_text("{not json")
    assert f.add(_rec(A)) is False
    assert f.ack([A]) == []
    assert (tmp_path / "findings.json").read_text() == "{not json"


def test_backfill_keeps_a_record_added_while_it_runs(tmp_path, monkeypatch):
    state, outputs = tmp_path / "state", tmp_path / "out"
    _write_review(tmp_path, id_=B)
    real = findings_mod._record

    def record_then_race(*a, **kw):
        Findings(state).add(_rec(A))                         # the drain writes mid-backfill
        return real(*a, **kw)

    monkeypatch.setattr(findings_mod, "_record", record_then_race)
    assert backfill(state, outputs, days=14, now=datetime.now()) == 1
    assert sorted(r["id"] for r in Findings(state).all()) == [A, B]


def test_mutations_wait_for_the_lock(tmp_path):
    f = Findings(tmp_path)
    lock = open(tmp_path / "findings.json.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    t = threading.Thread(target=f.add, args=(_rec(A),))
    t.start()
    t.join(0.3)
    assert t.is_alive(), "add wrote while another process held the lock"
    assert Findings(tmp_path).all() == []
    fcntl.flock(lock, fcntl.LOCK_UN)
    lock.close()
    t.join(5)
    assert [r["id"] for r in Findings(tmp_path).all()] == [A]


def test_lock_timeout_fails_open_in_the_drain(tmp_path, monkeypatch):
    monkeypatch.setattr(findings_mod, "LOCK_TIMEOUT", 0.2)
    state = tmp_path / "state"
    state.mkdir()
    out = _write_review(tmp_path)
    lock = open(state / "findings.json.lock", "a")
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        with pytest.raises(TimeoutError):
            Findings(state).add(_rec(A))
        assert record_review(state, _item(), str(out),
                             now=datetime(2026, 9, 25)) is False
    finally:
        lock.close()


def test_many_threads_adding_and_acking_lose_nothing(tmp_path):
    ids = [f"{i:02d}" + "0" * 30 for i in range(16)]
    Findings(tmp_path).add(_rec("f" * 32))
    go = threading.Barrier(len(ids) + 1)

    def add(id_):
        go.wait()
        Findings(tmp_path).add(_rec(id_))

    def ack():
        go.wait()
        Findings(tmp_path).ack(["f" * 32])

    threads = [threading.Thread(target=add, args=(i,)) for i in ids]
    threads.append(threading.Thread(target=ack))
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    recs = {r["id"]: r["status"] for r in Findings(tmp_path).all()}
    assert set(recs) == set(ids) | {"f" * 32}
    assert recs["f" * 32] == "acked"
