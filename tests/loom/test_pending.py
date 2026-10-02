# tests/loom/test_pending.py
from loom.pending import cluster_blocked


def _item(lid, target, text):
    return {"id": lid, "target": target, "reason": "weave failed guards after retry",
            "text": text}


VPS_A = """- type: decision
  subject: VPS change constraint during client onboarding
  learning: >
    When onboarding a new client device, the only permitted VPS change is
    appending the new device's ed25519 public key to dev's ~/.ssh/authorized_keys.
  route: memory
"""
VPS_B = """- type: decision
  subject: Only permitted VPS change during new-client onboarding
  learning: >
    When onboarding a new client machine, the only acceptable VPS-side change is
    appending the new machine's ed25519 public key to dev's ~/.ssh/authorized_keys.
  route: memory
"""
MACOS = """- type: fact
  subject: macOS Remote Login CLI restriction
  learning: "`sudo systemsetup -setremotelogin on` requires Full Disk Access for the
    calling terminal app on macOS Ventura+. Use System Settings instead."
"""


def test_same_fact_captured_twice_is_one_decision():
    """The same learning re-captured across sessions must collapse to ONE decision —
    showing near-identical rows is the noise that makes the surface get ignored."""
    out = cluster_blocked([_item("s1#0", "decisions/vps.md", VPS_A),
                           _item("s2#3", "projects/infra.md", VPS_B)])
    assert len(out) == 1
    assert out[0]["n"] == 2
    assert out[0]["targets"] == ["decisions/vps.md", "projects/infra.md"]


def test_unrelated_fact_stays_its_own_decision():
    out = cluster_blocked([_item("s1#0", "decisions/vps.md", VPS_A),
                           _item("s9#1", "tools/macos.md", MACOS)])
    assert len(out) == 2


def test_decisions_are_ordered_by_how_often_seen():
    out = cluster_blocked([_item("s9#1", "tools/macos.md", MACOS),
                           _item("s1#0", "decisions/vps.md", VPS_A),
                           _item("s2#3", "projects/infra.md", VPS_B)])
    assert out[0]["n"] == 2 and out[1]["n"] == 1


def test_subject_and_body_are_extracted_for_display():
    out = cluster_blocked([_item("s1#0", "decisions/vps.md", VPS_A)])
    assert "VPS change constraint" in out[0]["subject"]
    assert "authorized_keys" in out[0]["body"]
    assert "route:" not in out[0]["body"]      # trailing YAML keys stripped


def test_empty_input_is_no_decisions():
    assert cluster_blocked([]) == []


# --- pending_summary: the one payload the briefing line and review page share ---
import json as _json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import pytest
from loom.pending import _quarantined_sessions, pending_summary


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def wiki(tmp_path):
    w = tmp_path / "wiki"; w.mkdir()
    _git(w, "init", "-q"); _git(w, "config", "user.email", "t@t"); _git(w, "config", "user.name", "t")
    (w / "people").mkdir(); (w / "people" / "liam.md").write_text("# Liam\nv0\n")
    _git(w, "add", "-A"); _git(w, "commit", "-qm", "seed")
    _git(w, "branch", "loom-shadow"); _git(w, "checkout", "-q", "loom-shadow")
    (w / "people" / "liam.md").write_text("# Liam\nv1\n")
    (w / "tools").mkdir(); (w / "tools" / "macos.md").write_text("# macOS\n")
    _git(w, "add", "-A"); _git(w, "commit", "-qm", "weave")
    _git(w, "checkout", "-q", "master")
    return w


def test_pending_summary_counts_new_vs_updated(wiki, tmp_path):
    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings",
                        loom_dir=tmp_path / "loom", today="2026-07-21")
    assert s["commits"] == 1
    assert sorted(a["file"] for a in s["articles"]) == ["people/liam.md", "tools/macos.md"]
    assert s["new"] == 1                       # tools/macos.md is added
    assert s["updated"] == 1                   # people/liam.md existed on master


def test_pending_summary_surfaces_decisions_not_raw_rows(wiki, tmp_path):
    led = tmp_path / "l.json"
    led.write_text(_json.dumps({
        "s1#0": {"status": "quarantined", "target": "decisions/vps.md", "reason": "guards"},
        "s2#3": {"status": "quarantined", "target": "projects/infra.md", "reason": "guards"},
    }))
    ld = tmp_path / "learnings"; ld.mkdir()
    (ld / "s1.md").write_text(VPS_A)
    (ld / "s2.md").write_text("- type: x\n  subject: filler\n  learning: filler\n" * 3 + VPS_B)
    s = pending_summary(wiki_root=wiki, ledger_path=led, learnings_dir=ld,
                        loom_dir=tmp_path / "loom", today="2026-07-21")
    assert len(s["decisions"]) == 1            # 2 rows -> 1 human decision
    assert s["decisions"][0]["n"] == 2


def test_pending_summary_surfaces_quarantined_sessions(wiki, tmp_path):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)

    state = {}
    for day in range(1, 12):
        sid = f"session-{day:02d}"
        state[sid] = {"state": "quarantined"}
        transcript = quarantine / f"{sid}.jsonl"
        transcript.write_bytes(b"x" * day)
        timestamp = datetime(2026, 7, day, tzinfo=timezone.utc).timestamp()
        os.utime(transcript, (timestamp, timestamp))
    (loom / "state.json").write_text(_json.dumps(state))

    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21")
    quarantined = s["quarantined_sessions"]

    assert quarantined["count"] == 11
    assert quarantined["reasons"] == {"unknown": 11}
    assert len(quarantined["recent"]) == 10
    assert quarantined["recent"][0] == {
        "id": "session-11",
        "quarantined_on": "2026-07-11",
        "size_bytes": 11,
    }
    assert "loom resolve <id>" in quarantined["action"]


def test_pending_summary_prefers_quarantined_at_for_date_and_recency(wiki, tmp_path):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)
    older = quarantine / "session-older.jsonl"
    newer = quarantine / "session-newer.jsonl"
    older.write_text("old")
    newer.write_text("new")
    newer_mtime = datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp()
    older_mtime = datetime(2026, 7, 31, tzinfo=timezone.utc).timestamp()
    os.utime(older, (older_mtime, older_mtime))
    os.utime(newer, (newer_mtime, newer_mtime))
    (loom / "state.json").write_text(_json.dumps({
        "session-older": {
            "state": "quarantined",
            "quarantined_at": "2026-07-10T12:00:00+00:00",
        },
        "session-newer": {
            "state": "quarantined",
            "quarantined_at": "2026-07-20T12:00:00+00:00",
        },
    }))

    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21")

    assert [item["id"] for item in s["quarantined_sessions"]["recent"]] == [
        "session-newer", "session-older",
    ]
    assert [item["quarantined_on"] for item in
            s["quarantined_sessions"]["recent"]] == ["2026-07-20", "2026-07-10"]


def test_pending_summary_uses_only_exact_transcript_name(wiki, tmp_path):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "session-01.backup.jsonl").write_text("sidecar")
    (loom / "state.json").write_text(_json.dumps({
        "session-01": {"state": "quarantined"},
    }))

    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21")

    assert s["quarantined_sessions"]["recent"] == [{
        "id": "session-01",
        "quarantined_on": None,
        "size_bytes": None,
    }]


def test_pending_summary_skips_transcript_that_vanishes_during_stat(
        wiki, tmp_path, monkeypatch):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)
    transcript = quarantine / "session-01.jsonl"
    transcript.write_text("transcript")
    (loom / "state.json").write_text(_json.dumps({
        "session-01": {"state": "quarantined"},
    }))

    real_stat = Path.stat

    def disappearing_stat(path, *args, **kwargs):
        if path == transcript:
            raise FileNotFoundError(path)
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", disappearing_stat)
    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21")

    assert s["quarantined_sessions"]["count"] == 1
    assert s["quarantined_sessions"]["recent"] == [{
        "id": "session-01",
        "quarantined_on": None,
        "size_bytes": None,
    }]


def test_pending_summary_lists_quarantine_directory_once(wiki, tmp_path, monkeypatch):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "session-01.jsonl").write_text("transcript")
    (loom / "state.json").write_text(_json.dumps({
        "session-01": {"state": "quarantined"},
    }))

    real_iterdir = Path.iterdir
    listings = 0

    def counted_iterdir(path):
        nonlocal listings
        if path == quarantine:
            listings += 1
        return real_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", counted_iterdir)
    pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                    learnings_dir=tmp_path / "learnings", loom_dir=loom,
                    today="2026-07-21")

    assert listings == 1


@pytest.mark.parametrize("limit", [0, -1])
def test_pending_summary_clamps_non_positive_quarantine_limit(wiki, tmp_path, limit):
    loom = tmp_path / "loom"
    quarantine = loom / "quarantine"
    quarantine.mkdir(parents=True)
    (quarantine / "session-01.jsonl").write_text("transcript")
    (loom / "state.json").write_text(_json.dumps({
        "session-01": {"state": "quarantined"},
    }))

    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21", quarantined_limit=limit)

    assert s["quarantined_sessions"]["count"] == 1
    assert s["quarantined_sessions"]["recent"] == []


def test_invalid_state_degrades_quarantine_summary_and_briefing(tmp_path):
    loom = tmp_path / "loom"
    loom.mkdir()
    (loom / "state.json").write_text("{invalid")

    quarantined = _quarantined_sessions(loom)

    assert quarantined["degraded"] is True
    assert quarantined["count"] == 0
    assert quarantined["error"].startswith("invalid state.json:")
    line = briefing_line({
        "promoted": {"promoted": False, "articles": []},
        "decisions": [], "held": False, "staged_claude": [],
        "quarantined_sessions": quarantined,
    })
    assert f"quarantine summary degraded: {quarantined['error']}" in line
    assert "0 quarantined" not in line


def test_unreadable_state_degrades_quarantine_summary(tmp_path, monkeypatch):
    loom = tmp_path / "loom"
    loom.mkdir()
    state_path = loom / "state.json"
    state_path.write_text("{}")
    real_read_text = Path.read_text

    def unreadable_state(path, *args, **kwargs):
        if path == state_path:
            raise PermissionError("denied")
        return real_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable_state)
    quarantined = _quarantined_sessions(loom)

    assert quarantined["degraded"] is True
    assert quarantined["error"] == "could not read state.json: denied"


def test_quarantined_at_is_persisted_once(tmp_path):
    from loom.state import LoomState

    path = tmp_path / "loom" / "state.json"
    state = LoomState(path)
    state.advance("session-01", "quarantined")
    first = _json.loads(path.read_text())["session-01"]["quarantined_at"]

    parsed = datetime.fromisoformat(first)
    assert parsed.utcoffset().total_seconds() == 0

    state.advance("session-01", "quarantined")
    second = _json.loads(path.read_text())["session-01"]["quarantined_at"]
    assert second == first


def test_pending_summary_reports_hold_state(wiki, tmp_path):
    """`held` describes the NEXT promote (the run a veto targets), not the
    calling day — a hold set at 07:00 must show as held all day."""
    from loom.autopromote import set_hold
    loom = tmp_path / "loom"; set_hold(loom, "2026-07-22")   # target = next run
    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        today="2026-07-21", promote_target="2026-07-22")
    assert s["held"] is True


def test_pending_summary_hold_for_a_past_promote_is_expired(wiki, tmp_path):
    from loom.autopromote import set_hold
    loom = tmp_path / "loom"; set_hold(loom, "2026-07-21")   # already consumed
    s = pending_summary(wiki_root=wiki, ledger_path=tmp_path / "l.json",
                        learnings_dir=tmp_path / "learnings", loom_dir=loom,
                        promote_target="2026-07-22")
    assert s["held"] is False


# --- briefing_line: the ONE line that rides in the 07:00 Bebop briefing ---
from loom.pending import briefing_line


def test_silent_when_nothing_needs_him():
    """Silence is the feature. A line every morning is how the old summary
    became wallpaper; if there's nothing to say, say nothing."""
    assert briefing_line({"promoted": {"promoted": False, "articles": []},
                          "decisions": [], "held": False, "staged_claude": []}) == ""


def test_quarantined_sessions_reach_the_briefing():
    line = briefing_line({
        "promoted": {"promoted": False, "articles": []},
        "decisions": [], "held": False, "staged_claude": [],
        "quarantined_sessions": {"count": 13},
    })
    assert "13 quarantined sessions" in line


def test_reports_what_landed_overnight():
    line = briefing_line({"promoted": {"promoted": True, "articles": ["a.md", "b.md"]},
                          "decisions": [], "held": False, "staged_claude": []})
    assert line.startswith("🧵")
    assert "2 articles" in line and "landed" in line


def test_names_what_needs_a_decision():
    """He must know WHAT he's deciding without tapping through."""
    line = briefing_line({
        "promoted": {"promoted": True, "articles": ["a.md"]},
        "decisions": [{"subject": "VPS onboarding rule", "n": 6, "targets": ["x"]},
                      {"subject": "macOS Remote Login", "n": 1, "targets": ["y"]}],
        "held": False, "staged_claude": []})
    assert "2 need your call" in line
    assert "VPS onboarding rule" in line and "macOS Remote Login" in line


def test_hold_is_visible_so_it_cannot_silently_persist():
    line = briefing_line({"promoted": {"promoted": False, "reason": "hold",
                                       "articles": ["a.md"]},
                          "decisions": [], "held": True, "staged_claude": []})
    assert "held" in line.lower() and "GO" in line


def test_staged_claude_says_it_needs_a_human():
    line = briefing_line({"promoted": {"promoted": False, "reason": "staged-claude",
                                       "articles": ["a.md"]},
                          "decisions": [], "held": False,
                          "staged_claude": ["_staged/.claude/memory/x.md"]})
    assert "memory" in line.lower() or "skill" in line.lower()
    assert "1 article" in line


def test_url_is_appended_when_available():
    line = briefing_line({"promoted": {"promoted": True, "articles": ["a.md"]},
                          "decisions": [], "held": False, "staged_claude": []},
                         url="https://example.test/x")
    assert "https://example.test/x" in line
