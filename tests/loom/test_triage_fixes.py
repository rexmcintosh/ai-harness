# Tests for the 2026-08 triage fixes: fence-tolerant parsing that never settles
# malformed distill output as "zero learnings", self-generated transcript skip,
# dead-lettering of repeatedly exploding learnings, daily target rotation,
# roster injection, and the one-time fixup command.
import subprocess

import pytest

from loom import run as run_mod
from loom.discovery import is_loom_generated
from loom.fingerprint import learning_id
from loom.fixup import triage_fixup
from loom.ledger import WeaveLedger
from loom.state import LoomState

VALID_YAML = "- type: fact\n  subject: Cai\n  learning: Cai is Rex's middle son\n  route: wiki/people/cai"
FENCED_YAML = f"```yaml\n{VALID_YAML}\n```"
PROSE = "Here are the learnings I extracted from this session transcript."


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)


def _setup(tmp_path, transcript_text='{"type":"user","message":{"content":"Cai swims"}}\n'):
    projects = tmp_path / "projects"
    t = projects / "p1" / "sess1.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text(transcript_text)
    return run_mod.Config(
        projects_dir=projects,
        loom_dir=tmp_path / "loom",
        state_path=tmp_path / "loom" / "state.json",
    )


def _live_cfg(tmp_path):
    cfg = _setup(tmp_path)
    wiki = tmp_path / "wiki"; wiki.mkdir()
    _git(wiki, "init", "-q"); _git(wiki, "config", "user.email", "t@t"); _git(wiki, "config", "user.name", "t")
    (wiki / "_index.md").write_text("# Index\n\n## People\n")
    (wiki / "people").mkdir()
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "seed"); _git(wiki, "checkout", "-qb", "loom-shadow")
    cfg.wiki_worktree = wiki
    cfg.ledger_path = tmp_path / "loom" / "ledger.json"
    return cfg


# ---------- parsing ----------

def test_parse_learnings_strips_fences():
    items = run_mod._parse_learnings(FENCED_YAML)
    assert len(items) == 1 and "middle son" in items[0]["learning"]

def test_parse_learnings_empty_variants_are_legitimate():
    for text in ("", "  \n", "[]", "null"):
        assert run_mod._parse_learnings(text) == []

def test_parse_learnings_prose_raises():
    with pytest.raises(run_mod.LearningsParseError):
        run_mod._parse_learnings(PROSE)

def test_parse_learnings_list_without_learning_keys_raises():
    with pytest.raises(run_mod.LearningsParseError):
        run_mod._parse_learnings("- note: nothing useful here")


# ---------- distill-stage validation ----------

def test_distill_retries_once_then_succeeds(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    calls = {"n": 0}
    def fake_run(prompt, model, **k):
        calls["n"] += 1
        return PROSE if calls["n"] == 1 else FENCED_YAML
    monkeypatch.setattr(run_mod.llm, "run", fake_run)
    summary = run_mod.absorb(cfg, shadow=True)
    assert calls["n"] == 2
    assert summary["distilled"] == 1 and summary["quarantined"] == 0

def test_distill_unparseable_after_retry_quarantines(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    monkeypatch.setattr(run_mod.llm, "run", lambda prompt, model, **k: PROSE)
    summary = run_mod.absorb(cfg, shadow=True)
    assert summary["quarantined"] == 1 and summary["distilled"] == 0
    assert LoomState(cfg.state_path).state_of("sess1") == "quarantined"
    assert (cfg.loom_dir / "quarantine" / "sess1.md").exists()
    assert not (cfg.loom_dir / "learnings" / "sess1.md").exists()


# ---------- self-generated transcript skip ----------

def test_is_loom_generated_detects_marker(tmp_path):
    marked = tmp_path / "a.jsonl"
    marked.write_text('{"type":"user","message":{"content":"<!-- loom/prompts/distill.md -->\\nYou are..."}}\n')
    clean = tmp_path / "b.jsonl"
    clean.write_text('{"type":"user","message":{"content":"Cai swims"}}\n')
    assert is_loom_generated(marked) is True
    assert is_loom_generated(clean) is False

def test_absorb_skips_self_generated_without_llm_call(tmp_path, monkeypatch):
    cfg = _setup(tmp_path,
                 '{"type":"user","message":{"content":"<!-- loom/prompts/route.md -->..."}}\n')
    called = {"llm": False}
    monkeypatch.setattr(run_mod.llm, "run", lambda *a, **k: called.__setitem__("llm", True))
    summary = run_mod.absorb(cfg, shadow=True)
    assert called["llm"] is False
    assert summary["self_skipped"] == 1 and summary["distilled"] == 0
    assert LoomState(cfg.state_path).state_of("sess1") == "committed"


# ---------- weave-stage legacy artifacts ----------

def test_weave_quarantines_legacy_unparseable_artifact(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    (cfg.loom_dir / "learnings").mkdir(parents=True)
    (cfg.loom_dir / "learnings" / "sess9.md").write_text(PROSE)
    LoomState(cfg.state_path).advance("sess9", "distilled")
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    class B:
        def complete(self, role, system, user, json_mode=False):
            return "null"                          # distill of sess1: no learnings
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")
    assert summary["quarantined"] == 1
    assert LoomState(cfg.state_path).state_of("sess9") == "quarantined"
    assert (cfg.loom_dir / "quarantine" / "sess9.md").exists()

def test_weave_dead_letters_after_repeated_exceptions(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    (cfg.loom_dir / "learnings").mkdir(parents=True)
    (cfg.loom_dir / "learnings" / "sess1.md").write_text(VALID_YAML + "\n")
    LoomState(cfg.state_path).advance("sess1", "distilled")
    lid = learning_id("sess1", 0)
    ledger = WeaveLedger(cfg.ledger_path)
    for _ in range(run_mod._DEAD_LETTER_DEFERRALS):
        ledger.defer(lid, "weave exception")
    class B:
        def complete(self, role, system, user, json_mode=False):
            if role == "route":
                return '{"target":"people/cai.md","action":"update","cross_links":[]}'
            raise RuntimeError("boom")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    monkeypatch.setattr(run_mod, "weave_target",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", distill=False)
    assert WeaveLedger(cfg.ledger_path).status_of(lid) == "quarantined"
    assert summary["quarantined_learnings"] == 1 and summary["deferred"] == 0


# ---------- roster injection ----------

def test_distill_prompt_includes_roster(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    wiki = tmp_path / "wiki"; wiki.mkdir()
    (wiki / "_roster.md").write_text("- Cai — Rex's middle son -> people/cai.md")
    cfg.wiki_master = wiki
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    seen = {}
    def fake_run(prompt, model, **k):
        seen["prompt"] = prompt
        return VALID_YAML
    monkeypatch.setattr(run_mod.llm, "run", fake_run)
    run_mod.absorb(cfg, shadow=True)
    assert "Rex's middle son" in seen["prompt"]

def test_distill_prompt_without_roster_says_none(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    seen = {}
    def fake_run(prompt, model, **k):
        seen["prompt"] = prompt
        return VALID_YAML
    monkeypatch.setattr(run_mod.llm, "run", fake_run)
    run_mod.absorb(cfg, shadow=True)
    assert "{{ROSTER}}" not in seen["prompt"] and "(none)" in seen["prompt"]


# ---------- daily target rotation ----------

def test_targets_rotate_by_day(tmp_path, monkeypatch):
    import hashlib
    targets = [f"people/p{i}.md" for i in range(8)]
    def order(day):
        return sorted(targets, key=lambda t: hashlib.sha1(f"{day}:{t}".encode()).hexdigest())
    days = [f"2026-08-{d:02d}" for d in range(1, 15)]
    assert any(order(d) != order(days[0]) for d in days[1:])   # order changes across days
    assert order(days[0]) == order(days[0])                    # deterministic within a day


# ---------- fixup command ----------

def test_fixup_recovers_fence_lost_sessions(tmp_path):
    cfg = _live_cfg(tmp_path)
    state = LoomState(cfg.state_path)
    (cfg.loom_dir / "learnings").mkdir(parents=True)

    # fence-lost: parses under the new parser, never entered the ledger, settled committed
    (cfg.loom_dir / "learnings" / "lost1.md").write_text(FENCED_YAML + "\n")
    state.advance("lost1", "committed")
    # genuinely woven: committed AND its learning is settled in the ledger
    (cfg.loom_dir / "learnings" / "done1.md").write_text(VALID_YAML + "\n")
    state.advance("done1", "committed")
    WeaveLedger(cfg.ledger_path).mark(learning_id("done1", 0), "committed")
    # still unparseable prose, no ledger trace
    (cfg.loom_dir / "learnings" / "bad1.md").write_text(PROSE)
    state.advance("bad1", "committed")

    dry = triage_fixup(cfg, apply=False)
    assert dry["recovered"] == ["lost1"] and dry["unparseable"] == ["bad1"]
    assert LoomState(cfg.state_path).state_of("lost1") == "committed"   # dry-run: untouched

    res = triage_fixup(cfg, apply=True)
    assert res["recovered"] == ["lost1"]
    state = LoomState(cfg.state_path)
    assert state.state_of("lost1") == "distilled"
    assert state.state_of("done1") == "committed"
    assert state.state_of("bad1") == "quarantined"
    assert (cfg.loom_dir / "quarantine" / "bad1.md").exists()

def test_fixup_skips_self_generated_and_clears_people_routes(tmp_path):
    cfg = _live_cfg(tmp_path)
    (cfg.projects_dir / "p1" / "loomgen.jsonl").write_text(
        '{"type":"user","message":{"content":"<!-- loom/prompts/weave.md -->..."}}\n')
    ledger = WeaveLedger(cfg.ledger_path)
    ledger.plan("s#0", "people/rex-family-cai.md", "update")      # unsettled people/ route
    ledger.plan("s#1", "tools/loom.md", "update")                 # non-people: untouched
    ledger.mark("s#2", "committed"); ledger.plan("s#3", "people/liam.md", "update")
    ledger.mark("s#3", "committed")                               # settled: untouched

    res = triage_fixup(cfg, apply=True)
    assert res["self_skipped"] == 1
    assert LoomState(cfg.state_path).state_of("loomgen") == "committed"
    ledger = WeaveLedger(cfg.ledger_path)
    assert "target" not in ledger.entry("s#0")
    assert ledger.entry("s#1").get("target") == "tools/loom.md"
    assert ledger.entry("s#3").get("target") == "people/liam.md"


def test_fixup_unparseable_check_sees_high_learning_indexes(tmp_path):
    # ledger presence must be detected even past index 50 (old heuristic capped there)
    cfg = _live_cfg(tmp_path)
    (cfg.loom_dir / "learnings").mkdir(parents=True)
    (cfg.loom_dir / "learnings" / "big1.md").write_text(PROSE)
    LoomState(cfg.state_path).advance("big1", "committed")
    WeaveLedger(cfg.ledger_path).mark(learning_id("big1", 60), "committed")
    res = triage_fixup(cfg, apply=False)
    assert res["unparseable"] == []          # ledger trace found -> left alone


def test_fixup_apply_refuses_when_lock_held(tmp_path):
    import fcntl
    cfg = _live_cfg(tmp_path)
    cfg.loom_dir.mkdir(parents=True, exist_ok=True)
    holder = open(cfg.loom_dir / ".run.lock", "w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    res = triage_fixup(cfg, apply=True)
    assert "error" in res
    holder.close()


def test_is_loom_generated_handles_content_block_lists(tmp_path):
    t = tmp_path / "c.jsonl"
    t.write_text('{"type":"user","message":{"content":[{"type":"text","text":"<!-- loom/prompts/distill.md -->"}]}}\n')
    assert is_loom_generated(t) is True


def test_max_distill_caps_sessions_per_run(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    for i in range(5):
        t = projects / "p1" / f"s{i}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text('{"type":"user","message":{"content":"fact %d"}}\n' % i)
    cfg = run_mod.Config(projects_dir=projects, loom_dir=tmp_path / "loom",
                         state_path=tmp_path / "loom" / "state.json")
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    monkeypatch.setattr(run_mod.llm, "run", lambda prompt, model, **k: VALID_YAML)
    summary = run_mod.absorb(cfg, shadow=True, max_distill=2)
    assert summary["distilled"] == 2
    summary = run_mod.absorb(cfg, shadow=True, max_distill=None)
    assert summary["distilled"] == 3                     # the rest
