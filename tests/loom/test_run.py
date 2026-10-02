import json
from pathlib import Path
from loom import run as run_mod
from loom.state import LoomState

def _setup(tmp_path):
    projects = tmp_path / "projects"
    t = projects / "p1" / "sess1.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"user","message":{"content":"Liam swims for Bullsharks"}}\n')
    cfg = run_mod.Config(
        projects_dir=projects,
        loom_dir=tmp_path / "loom",
        state_path=tmp_path / "loom" / "state.json",
    )
    return cfg

def test_shadow_run_distills_and_marks_state(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)            # gate passes
    monkeypatch.setattr(run_mod.llm, "run",
                        lambda prompt, model, **k: "- type: fact\n  subject: Liam\n  learning: swims\n  route: wiki/people/liam")
    summary = run_mod.absorb(cfg, shadow=True)
    state = LoomState(cfg.state_path)
    assert state.state_of("sess1") == "distilled"           # shadow stops after distill+propose
    assert (cfg.loom_dir / "learnings" / "sess1.md").exists()
    assert summary["distilled"] == 1 and summary["quarantined"] == 0

def test_gate_hit_quarantines_and_skips(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: False)           # gate fails
    called = {"llm": False}
    monkeypatch.setattr(run_mod.llm, "run", lambda *a, **k: called.__setitem__("llm", True))
    summary = run_mod.absorb(cfg, shadow=True)
    assert called["llm"] is False                                          # never fed an LLM
    assert summary["quarantined"] == 1 and summary["distilled"] == 0
    assert LoomState(cfg.state_path).state_of("sess1") == "quarantined"

def test_shadow_run_is_idempotent(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    monkeypatch.setattr(run_mod.llm, "run", lambda prompt, model, **k: "- type: fact\n  learning: x")
    first = run_mod.absorb(cfg, shadow=True)
    second = run_mod.absorb(cfg, shadow=True)
    assert first["distilled"] == 1
    assert second["distilled"] == 0          # already distilled → skipped, not re-distilled


def test_stage2_gate_hit_leaves_no_unscanned_artifact(tmp_path, monkeypatch):
    cfg = _setup(tmp_path)
    # transcript (in projects/) passes; learnings artifact (in loom/) fails
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: "projects" in str(p))
    monkeypatch.setattr(run_mod.llm, "run", lambda prompt, model, **k: "- type: fact\n  learning: x")
    summary = run_mod.absorb(cfg, shadow=True)
    assert summary["quarantined"] == 1 and summary["distilled"] == 0
    assert not (cfg.loom_dir / "learnings" / "sess1.md").exists()      # nothing unscanned left
    assert not (cfg.loom_dir / "learnings" / "sess1.tmp").exists()     # temp cleaned up
    assert (cfg.loom_dir / "quarantine" / "sess1.md").exists()         # preserved for forensics
    assert LoomState(cfg.state_path).state_of("sess1") == "quarantined"


import subprocess
from loom.ledger import WeaveLedger

def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)

def _live_cfg(tmp_path):
    projects = tmp_path / "projects"
    t = projects / "p1" / "sess1.jsonl"
    t.parent.mkdir(parents=True)
    t.write_text('{"type":"user","message":{"content":"Liam swims for Bullsharks"}}\n')
    wiki = tmp_path / "wiki"; wiki.mkdir()
    _git(wiki, "init", "-q"); _git(wiki, "config", "user.email", "t@t"); _git(wiki, "config", "user.name", "t")
    (wiki / "_index.md").write_text("# RexBrain — Master Index\n\n## People\n")
    (wiki / "people").mkdir()      # routes may only land in EXISTING top-level dirs
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "seed"); _git(wiki, "checkout", "-qb", "loom-shadow")
    return run_mod.Config(
        projects_dir=projects,
        loom_dir=tmp_path / "loom",
        state_path=tmp_path / "loom" / "state.json",
        wiki_worktree=wiki,
        claude_dir=tmp_path / "claude",
        ledger_path=tmp_path / "loom" / "ledger.json",
    )

def test_live_run_weaves_and_commits(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    def fake_complete(role, system, user, json_mode=False):
        if role == "route":
            return '{"target":"people/liam.md","action":"create","cross_links":[]}'
        if role == "weave":
            return "# Liam\n\nLiam swims for the Bullsharks club.\n"
        return "- type: fact\n  subject: Liam\n  learning: swims for Bullsharks\n  route: wiki/people/liam"
    class B:
        def complete(self, role, system, user, json_mode=False):
            return fake_complete(role, system, user, json_mode)
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")
    assert summary["committed"] >= 1
    assert LoomState(cfg.state_path).state_of("sess1") == "committed"
    assert (cfg.wiki_worktree / "people" / "liam.md").exists()

def test_per_run_cap_defers_excess(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    def fake_complete(role, system, user, json_mode=False):
        if role == "route":
            import json as J
            subj = "a"
            for key in ("alpha", "beta", "gamma"):
                if key in user: subj = key
            return J.dumps({"target": f"people/{subj}.md", "action": "create", "cross_links": []})
        if role == "weave":
            return "# T\n\nbody.\n"
        return ("- type: fact\n  subject: alpha\n  learning: x\n  route: wiki/people/alpha\n"
                "- type: fact\n  subject: beta\n  learning: y\n  route: wiki/people/beta\n"
                "- type: fact\n  subject: gamma\n  learning: z\n  route: wiki/people/gamma\n")
    class B:
        def complete(self, role, system, user, json_mode=False):
            return fake_complete(role, system, user, json_mode)
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", max_targets=2)
    assert summary["committed"] == 2 and summary["deferred"] >= 1


def test_max_per_target_defers_overflow(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    def fake_complete(role, system, user, json_mode=False):
        if role == "route":
            return '{"target":"people/alpha.md","action":"create","cross_links":[]}'
        if role == "weave":
            return "# Alpha\n\nbody.\n"
        # distill: 5 learnings, all same subject -> all route to the SAME target
        return "\n".join(f"- type: fact\n  subject: alpha\n  learning: fact {i}\n  route: wiki/people/alpha"
                         for i in range(5))
    class B:
        def complete(self, role, system, user, json_mode=False):
            return fake_complete(role, system, user, json_mode)
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", max_targets=10, max_per_target=2)
    assert summary["committed"] == 2 and summary["deferred"] == 3   # 2 woven, 3 overflow deferred
    assert LoomState(cfg.state_path).state_of("sess1") == "distilled"  # not all settled -> retried


def test_distill_false_skips_distill(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)                 # has a pending sess1.jsonl
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    class B:
        def complete(self, role, system, user, json_mode=False):
            raise AssertionError("distill must not run when distill=False")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="venice", distill=False)
    assert summary["distilled"] == 0
    assert LoomState(cfg.state_path).state_of("sess1") == "pending"   # never distilled


def test_run_deadline_stops_processing(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    # fake clock: first reading (start) = 0; every later reading = 1000 -> deadline 5 is exceeded
    calls = {"n": 0}
    def clock():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 1000.0
    monkeypatch.setattr(run_mod.time, "monotonic", clock)
    class B:
        def complete(self, role, system, user, json_mode=False):
            return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", deadline_seconds=5)
    assert summary["deadline_hit"] is True
    assert summary["distilled"] == 0          # distill loop broke before processing


def test_weave_keeps_a_reserved_slice_of_the_deadline(tmp_path, monkeypatch):
    """Regression for the 13-night silent stall (2026-07-11..07-23).

    Every nightly `absorb --live` in that window logged deadline_hit=True,
    committed=0, and a deferred count climbing 176 -> 1152: the distill stage
    consumed the whole 3600s budget, so _weave_all started with an already-expired
    clock and deferred every target with reason "run deadline". Nothing was ever
    woven by the nightly. Distill must not be able to starve the weave.
    """
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)

    class D:
        def complete(self, role, system, user, json_mode=False):
            return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: D())
    run_mod.absorb(cfg, shadow=True)                  # sess1 -> distilled, artifact written

    (cfg.projects_dir / "p1" / "sess2.jsonl").write_text(   # something still to distill
        '{"type":"user","message":{"content":"another"}}\n')

    # start=0, every later reading = 70: past the distill slice (60% of 100) but
    # still inside the full 100s deadline, so the weave stage must get its turn.
    calls = {"n": 0}
    def clock():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 70.0
    monkeypatch.setattr(run_mod.time, "monotonic", clock)

    class W:
        def complete(self, role, system, user, json_mode=False):
            if role == "route":
                return '{"target":"people/x.md","action":"create","cross_links":[]}'
            return "# X\n\nbody.\n"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: W())

    summary = run_mod.absorb(cfg, shadow=False, backend="claude",
                             deadline_seconds=100, weave_reserve=0.4)
    assert summary["distilled"] == 0            # distill's slice was spent -> it stopped
    assert summary["distill_deadline_hit"] is True
    assert summary["committed"] == 1            # ...and the weave still ran
    assert (cfg.wiki_worktree / "people" / "x.md").exists()


def test_shadow_run_gives_distill_the_whole_deadline(tmp_path, monkeypatch):
    # No weave happens in shadow mode, so reserving time for it would just waste budget.
    cfg = _setup(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    calls = {"n": 0}
    def clock():
        calls["n"] += 1
        return 0.0 if calls["n"] == 1 else 70.0
    monkeypatch.setattr(run_mod.time, "monotonic", clock)
    monkeypatch.setattr(run_mod.llm, "run",
                        lambda prompt, model, **k: "- type: fact\n  learning: y")
    summary = run_mod.absorb(cfg, shadow=True, deadline_seconds=100, weave_reserve=0.4)
    assert summary["distilled"] == 1            # 70 < 100: still inside the full budget
    assert summary["distill_deadline_hit"] is False


def test_cached_route_is_reused_not_recomputed(tmp_path, monkeypatch):
    from loom.ledger import WeaveLedger
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    # 1) distill sess1 -> one learning, session 'distilled', artifact written
    class D:
        def complete(self, role, system, user, json_mode=False):
            assert role == "distill"
            return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: D())
    run_mod.absorb(cfg, shadow=True)
    # 2) pre-seed a cached route for sess1#0 in the ledger
    WeaveLedger(cfg.ledger_path).plan("sess1#0", "people/cached.md", "create")
    # 3) weave with a backend that ASSERTS if route is recomputed -> must reuse the cached route
    class W:
        def complete(self, role, system, user, json_mode=False):
            assert role != "route", "route must not be recomputed for a cached learning"
            return "# Cached\n\nbody.\n"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: W())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", distill=False)
    assert summary["committed"] == 1
    assert (cfg.wiki_worktree / "people" / "cached.md").exists()   # woven to the cached target


def test_distill_aborts_on_usage_limit(tmp_path, monkeypatch):
    projects = tmp_path / "projects"
    for name in ("sessA", "sessB"):
        t = projects / "p1" / f"{name}.jsonl"
        t.parent.mkdir(parents=True, exist_ok=True)
        t.write_text('{"type":"user","message":{"content":"hi"}}\n')
    cfg = run_mod.Config(projects_dir=projects, loom_dir=tmp_path / "loom",
                         state_path=tmp_path / "loom" / "state.json")
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    calls = {"n": 0}
    def limited(prompt, model, **k):
        calls["n"] += 1
        raise run_mod.llm.UsageLimitError("claude usage limit")
    monkeypatch.setattr(run_mod.llm, "run", limited)
    summary = run_mod.absorb(cfg, shadow=True)
    assert calls["n"] == 1                        # broke after the first limit; sessB untouched
    assert summary["limit_hit"] is True
    assert summary["distilled"] == 0 and summary["failed"] == 0
    assert LoomState(cfg.state_path).state_of("sessA") == "pending"   # not advanced


def test_usage_limit_in_distill_skips_weave(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    roles_seen = []
    class B:
        def complete(self, role, system, user, json_mode=False):
            roles_seen.append(role)
            raise run_mod.llm.UsageLimitError("limit")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")
    assert summary["limit_hit"] is True
    assert summary["committed"] == 0
    assert roles_seen == ["distill"]              # weave/route never attempted


def test_usage_limit_in_weave_is_caught(tmp_path, monkeypatch):
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    class B:
        def complete(self, role, system, user, json_mode=False):
            if role == "distill":
                return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
            raise run_mod.llm.UsageLimitError("limit during route/weave")
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude")   # must not raise
    assert summary["limit_hit"] is True


def test_poisoned_cached_route_cannot_mint_a_new_toplevel_dir(tmp_path, monkeypatch):
    """The reuse path must re-validate: a cached target planned before the
    top-level-dir guard existed (or poisoned) may not bypass it (council HIGH)."""
    from loom.ledger import WeaveLedger
    cfg = _live_cfg(tmp_path)
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    class D:
        def complete(self, role, system, user, json_mode=False):
            assert role == "distill"
            return "- type: fact\n  subject: x\n  learning: y\n  route: wiki/people/x"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: D())
    run_mod.absorb(cfg, shadow=True)
    # poisoned cache: routes into a top-level dir that does not exist
    WeaveLedger(cfg.ledger_path).plan("sess1#0", "etc/passwd.md", "create")
    class W:
        def complete(self, role, system, user, json_mode=False):
            if role == "route":   # re-route is ALLOWED for a refused cached target
                return '{"target":"people/x.md","action":"create","cross_links":[]}'
            return "# X\n\nbody.\n"
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: W())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", distill=False)
    assert not (cfg.wiki_worktree / "etc").exists()          # never minted
    assert summary["committed"] == 1
    assert (cfg.wiki_worktree / "people" / "x.md").exists()  # healed via re-route


def test_soft_directories_weave_before_ops_targets(tmp_path, monkeypatch):
    """Life-first weave order: people/ (and other soft dirs) must claim
    max_targets slots before ops targets, regardless of the daily hash
    rotation. With today=2026-01-01 the rotation alone would order
    tools/alpha < tools/beta < people/liam and starve the person."""
    cfg = _live_cfg(tmp_path)
    (cfg.wiki_worktree / "tools").mkdir()  # routes may only land in existing dirs
    monkeypatch.setattr(run_mod, "scan_clean", lambda p: True)
    def fake_complete(role, system, user, json_mode=False):
        if role == "route":
            import json as J
            if "liam" in user:
                return J.dumps({"target": "people/liam.md", "action": "create", "cross_links": []})
            subj = "alpha" if "alpha" in user else "beta"
            return J.dumps({"target": f"tools/{subj}.md", "action": "create", "cross_links": []})
        if role == "weave":
            return "# T\n\nbody.\n"
        return ("- type: fact\n  subject: alpha\n  learning: alpha tool fact\n  route: wiki/tools/alpha\n"
                "- type: fact\n  subject: beta\n  learning: beta tool fact\n  route: wiki/tools/beta\n"
                "- type: fact\n  subject: liam\n  learning: liam swims\n  route: wiki/people/liam\n")
    class B:
        def complete(self, role, system, user, json_mode=False):
            return fake_complete(role, system, user, json_mode)
    monkeypatch.setattr(run_mod, "get_backend", lambda name, api_key=None: B())
    summary = run_mod.absorb(cfg, shadow=False, backend="claude", max_targets=1,
                             today="2026-01-01")
    assert (cfg.wiki_worktree / "people" / "liam.md").exists()   # person won the slot
    assert not (cfg.wiki_worktree / "tools" / "alpha.md").exists()
    assert summary["committed"] == 1 and summary["deferred"] == 2


def test_distill_prompt_pins_life_first_editorial_policy():
    """The 2026-08 audit found 62% of learnings were about loom itself and only
    6.9% landed in soft dirs. The prompt's editorial policy is the primary
    control; pin its load-bearing parts so a rewrite can't silently drop them."""
    text = (Path(run_mod.__file__).parent / "prompts" / "distill.md").read_text()
    assert text.startswith("<!-- loom/prompts/")          # self-marker: discovery skip
    for marker in ("TIER 1", "TIER 2", "SETTLED TOPICS", "{{ROSTER}}", "{{TRANSCRIPT}}"):
        assert marker in text, f"distill.md lost required section: {marker}"
    assert "at most TWO" in text                          # tech cap
    assert "extract exhaustively" in text                 # life signal floor
