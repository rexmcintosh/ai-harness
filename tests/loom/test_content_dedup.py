import subprocess

import pytest

from loom import run as run_mod
from loom.ledger import WeaveLedger
from loom.state import LoomState


def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _live_cfg(tmp_path, article=None):
    wiki = tmp_path / "wiki"
    wiki.mkdir()
    _git(wiki, "init", "-q")
    _git(wiki, "config", "user.email", "tests@example.com")
    _git(wiki, "config", "user.name", "Loom Tests")
    (wiki / "people").mkdir()
    (wiki / "_index.md").write_text("# Index\n\n## People\n", encoding="utf-8")
    (wiki / "_backlinks.json").write_text("{}\n", encoding="utf-8")
    if article is not None:
        (wiki / "people" / "liam.md").write_text(article, encoding="utf-8")
    _git(wiki, "add", "-A")
    _git(wiki, "commit", "-qm", "seed")
    _git(wiki, "checkout", "-qb", "loom-shadow")

    cfg = run_mod.Config(
        projects_dir=tmp_path / "projects",
        loom_dir=tmp_path / "loom",
        state_path=tmp_path / "loom" / "state.json",
        wiki_worktree=wiki,
        ledger_path=tmp_path / "loom" / "ledger.json",
    )
    cfg.projects_dir.mkdir()
    return cfg


def _stage(cfg, learnings, *, action="update", target="people/liam.md"):
    lines = []
    for learning in learnings:
        lines.extend(
            [
                "- type: fact",
                "  subject: Liam",
                f"  learning: {learning}",
                "  route: wiki/people/liam",
            ]
        )
    learnings_dir = cfg.loom_dir / "learnings"
    learnings_dir.mkdir(parents=True)
    (learnings_dir / "sess1.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    LoomState(cfg.state_path).advance("sess1", "distilled")
    ledger = WeaveLedger(cfg.ledger_path)
    for index in range(len(learnings)):
        ledger.plan(f"sess1#{index}", target, action)


class FakeBackend:
    def __init__(self, coverage_results=(), weave_article=""):
        self.coverage_results = iter(coverage_results)
        self.coverage_prompts = []
        self.weave_prompts = []
        self.weave_article = weave_article

    def complete(self, role, system, user, json_mode=False):
        if role == "route":
            self.coverage_prompts.append(user)
            result = next(self.coverage_results)
            if isinstance(result, Exception):
                raise result
            return result
        if role == "weave":
            self.weave_prompts.append(user)
            return self.weave_article
        raise AssertionError(f"unexpected role: {role}")


ARTICLE = (
    "# Liam\n\n"
    "Liam swims for Bullsharks.\n\n"
    "## Training\n\n"
    "Schedule is tracked weekly.\n"
)

UPDATED_ARTICLE = (
    "# Liam\n\n"
    "Liam swims for Bullsharks and practices on Tuesdays.\n\n"
    "## Training\n\n"
    "Schedule is tracked weekly.\n"
)


def test_whole_article_equality_skips_judge_weave_and_article_edit(tmp_path, monkeypatch):
    # Catches removal of the high-precision equality path or accidental weaving.
    cfg = _live_cfg(tmp_path, "Liam swims for Bullsharks.\n")
    _stage(cfg, ["Liam swims for Bullsharks."])
    backend = FakeBackend()
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)
    before_head = _git(cfg.wiki_worktree, "rev-parse", "HEAD")
    before_bytes = (cfg.wiki_worktree / "people" / "liam.md").read_bytes()

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    entry = WeaveLedger(cfg.ledger_path).entry("sess1#0")
    assert summary["skipped_covered"] == 1
    assert summary["committed"] == 0
    assert entry["status"] == "committed"
    assert entry["reason"] == "already-covered"
    assert backend.coverage_prompts == []
    assert backend.weave_prompts == []
    assert (cfg.wiki_worktree / "people" / "liam.md").read_bytes() == before_bytes
    assert _git(cfg.wiki_worktree, "rev-parse", "HEAD") == before_head
    assert LoomState(cfg.state_path).state_of("sess1") == "committed"




def test_matching_line_under_context_heading_requires_semantic_judgment(tmp_path, monkeypatch):
    # Catches exact-line skipping when a heading changes the line's current meaning.
    article = "# Obsolete claim\n\nLiam swims for Bullsharks.\n"
    cfg = _live_cfg(tmp_path, article)
    _stage(cfg, ["Liam swims for Bullsharks."])
    backend = FakeBackend(
        ['{"covered": false}'],
        "# Current claim\n\nLiam swims for Bullsharks.\n",
    )
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 0
    assert summary["committed"] == 1
    assert len(backend.coverage_prompts) == 1
    assert len(backend.weave_prompts) == 1

@pytest.mark.parametrize(
    ("article", "learning", "woven"),
    [
        (
            "# Reading\n\nThe recorded temperature is 5 C.\n\n## Notes\n\nChecked weekly.\n",
            "The recorded temperature is -5 C.",
            "# Reading\n\nThe recorded temperatures are -5 C and 5 C.\n\n## Notes\n\nChecked weekly.\n",
        ),
        (
            "# Release\n\nThe current version is 1 2.\n\n## Notes\n\nChecked weekly.\n",
            "The current version is 1.2.",
            "# Release\n\nThe source says version 1.2, not 1 2.\n\n## Notes\n\nChecked weekly.\n",
        ),
        (
            "# Liam\n\nLiam no longer supports Project Atlas.\n\n## Notes\n\nChecked weekly.\n",
            "supports Project Atlas.",
            "# Liam\n\nLiam supports Project Atlas again.\n\n## Notes\n\nChecked weekly.\n",
        ),
        (
            "Key is a  b.\n",
            "Key is a b.",
            "Key is a b, with one internal space.\n\nKey is a  b.\n",
        ),
    ],
)
def test_fast_path_does_not_collapse_distinct_facts(
    tmp_path, monkeypatch, article, learning, woven
):
    # Catches punctuation, whitespace, or substring normalization that discards a distinct fact.
    cfg = _live_cfg(tmp_path, article)
    _stage(cfg, [learning])
    backend = FakeBackend(['{"covered": false}'], woven)
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 0
    assert summary["committed"] == 1
    assert len(backend.coverage_prompts) == 1
    assert len(backend.weave_prompts) == 1
    assert learning in backend.weave_prompts[0]

def test_semantic_restatement_skips_weave(tmp_path, monkeypatch):
    # Catches ignoring an explicit semantic covered=true decision.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(cfg, ["Liam belongs to the Bullsharks swimming club."])
    backend = FakeBackend(['{"covered": true}'])
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 1
    assert backend.weave_prompts == []
    assert len(backend.coverage_prompts) == 1
    assert WeaveLedger(cfg.ledger_path).entry("sess1#0")["reason"] == "already-covered"


def test_genuinely_new_update_weaves(tmp_path, monkeypatch):
    # Catches interpreting covered=false as permission to discard a new fact.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(cfg, ["Liam practices on Tuesdays."])
    backend = FakeBackend(['{"covered": false}'], UPDATED_ARTICLE)
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 0
    assert summary["committed"] == 1
    assert len(backend.coverage_prompts) == 1
    assert len(backend.weave_prompts) == 1
    assert "practices on Tuesdays" in (cfg.wiki_worktree / "people" / "liam.md").read_text()


@pytest.mark.parametrize("result", ['{"covered": "maybe"}', ValueError("judge unavailable")])
def test_ambiguous_or_failed_coverage_check_preserves_learning(tmp_path, monkeypatch, result):
    # Catches fail-open data loss when the cheap judge is malformed or unavailable.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(cfg, ["Liam practices on Tuesdays."])
    backend = FakeBackend([result], UPDATED_ARTICLE)
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 0
    assert summary["committed"] == 1
    assert len(backend.coverage_prompts) == 1
    assert len(backend.weave_prompts) == 1
    assert WeaveLedger(cfg.ledger_path).status_of("sess1#0") == "committed"


def test_mixed_update_bundle_weaves_only_new_learning(tmp_path, monkeypatch):
    # Catches bundle-level skipping that would erase a new fact beside a covered restatement.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(
        cfg,
        [
            "Liam belongs to the Bullsharks swimming club.",
            "Liam practices on Tuesdays.",
        ],
    )
    backend = FakeBackend(
        ['{"covered": true}', '{"covered": false}'],
        UPDATED_ARTICLE,
    )
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 1
    assert summary["committed"] == 1
    assert len(backend.coverage_prompts) == 2
    assert len(backend.weave_prompts) == 1
    assert "(fact) Liam: Liam practices on Tuesdays." in backend.weave_prompts[0]
    assert "(fact) Liam: Liam belongs to the Bullsharks swimming club." not in backend.weave_prompts[0]
    ledger = WeaveLedger(cfg.ledger_path)
    assert ledger.entry("sess1#0")["reason"] == "already-covered"
    assert ledger.status_of("sess1#1") == "committed"
    assert LoomState(cfg.state_path).state_of("sess1") == "committed"




def test_deadline_between_coverage_checks_defers_uncommitted_bundle(
    tmp_path, monkeypatch
):
    # Catches route-tier calls and weaving after the run deadline expires mid-bundle.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(
        cfg,
        [
            "Liam practices on Tuesdays.",
            "Liam practices on Wednesdays.",
        ],
    )
    backend = FakeBackend(
        ['{"covered": false}', '{"covered": false}'],
        UPDATED_ARTICLE,
    )
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)
    ticks = iter([0.0, 0.0, 0.0, 0.0, 100.0])
    monkeypatch.setattr(run_mod.time, "monotonic", lambda: next(ticks))

    summary = run_mod.absorb(
        cfg,
        shadow=False,
        distill=False,
        deadline_seconds=50,
    )

    assert summary["deadline_hit"] is True
    assert summary["committed"] == 0
    assert summary["deferred"] == 2
    assert len(backend.coverage_prompts) == 1
    assert backend.weave_prompts == []
    ledger = WeaveLedger(cfg.ledger_path)
    assert ledger.status_of("sess1#0") == "deferred"
    assert ledger.status_of("sess1#1") == "deferred"

def test_coverage_checks_only_entries_selected_by_per_target_cap(tmp_path, monkeypatch):
    # Catches route-tier spending on entries that this run will defer without weaving.
    cfg = _live_cfg(tmp_path, ARTICLE)
    _stage(
        cfg,
        [
            "Liam practices on Tuesdays.",
            "Liam practices on Wednesdays.",
            "Liam practices on Thursdays.",
        ],
    )
    backend = FakeBackend(['{"covered": false}'], UPDATED_ARTICLE)
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(
        cfg,
        shadow=False,
        distill=False,
        max_per_target=1,
    )

    assert len(backend.coverage_prompts) == 1
    assert len(backend.weave_prompts) == 1
    assert summary["committed"] == 1
    assert summary["deferred"] == 2

def test_create_always_weaves_without_coverage_check(tmp_path, monkeypatch):
    # Catches applying update-only dedup to a new article.
    cfg = _live_cfg(tmp_path)
    _stage(cfg, ["Liam swims for Bullsharks."], action="create")
    backend = FakeBackend(weave_article="# Liam\n\nLiam swims for Bullsharks.\n")
    monkeypatch.setattr(run_mod, "get_backend", lambda name: backend)

    summary = run_mod.absorb(cfg, shadow=False, distill=False)

    assert summary["skipped_covered"] == 0
    assert summary["committed"] == 1
    assert backend.coverage_prompts == []
    assert len(backend.weave_prompts) == 1


def test_shadow_summary_includes_zero_skipped_covered(tmp_path, monkeypatch):
    # Catches omission of the stable run-summary counter when no weave occurs.
    cfg = run_mod.Config(
        projects_dir=tmp_path / "projects",
        loom_dir=tmp_path / "loom",
        state_path=tmp_path / "loom" / "state.json",
    )
    cfg.projects_dir.mkdir()
    monkeypatch.setattr(run_mod, "get_backend", lambda name: FakeBackend())

    assert run_mod.absorb(cfg, shadow=True)["skipped_covered"] == 0
