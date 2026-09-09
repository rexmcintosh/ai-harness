# tests/loom/test_phantom.py
import json
import subprocess
from pathlib import Path

import pytest

from loom import cli
from loom import phantom
from loom.fingerprint import markers_in
from loom.phantom import PhantomError, apply, folded_text, plan, unique_paragraphs


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], check=True,
                          capture_output=True, text=True).stdout


REAL_A = ("# A\n\nShared fact one.\n\nReal-only fact.\n\n"
          "<!-- loom-woven: r1#0 -->\n")
PHANTOM_A = ("# A\n\n**Shared** fact one.\n\n## Extra\n\nPhantom-only fact.\n\n"
             "<!-- loom-woven: p1#0 p2#1 -->\n")
PHANTOM_INDEX = "# Index\n\nMini index.\n\n<!-- loom-woven: i1#2 -->\n"
PHANTOM_ORPHAN = "# Orphan\n\nOnly lives in the phantom tree.\n\n<!-- loom-woven: o1#0 -->\n"


@pytest.fixture
def wiki(tmp_path):
    root = tmp_path / "wiki"
    root.mkdir()
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "_index.md").write_text("# Real index\n")
    (root / "tools").mkdir()
    (root / "tools" / "a.md").write_text(REAL_A)
    (root / "wiki" / "tools").mkdir(parents=True)
    (root / "wiki" / "_index.md").write_text(PHANTOM_INDEX)
    (root / "wiki" / "tools" / "a.md").write_text(PHANTOM_A)
    (root / "wiki" / "tools" / "orphan.md").write_text(PHANTOM_ORPHAN)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed with phantom tree")
    _git(root, "branch", "loom-shadow")
    return root


@pytest.fixture
def folds(tmp_path):
    d = tmp_path / "folds" / "tools"
    d.mkdir(parents=True)
    (d / "a.md").write_text("## Folded\n\nPhantom-only fact, curated.\n")
    return tmp_path / "folds"


@pytest.fixture(autouse=True)
def _no_loom_running(monkeypatch):
    monkeypatch.setattr(phantom, "loom_running", lambda: False)


# ---- pure helpers -------------------------------------------------------------

def test_unique_paragraphs_ignores_headings_markers_and_emphasis():
    out = unique_paragraphs(PHANTOM_A, REAL_A)
    assert out == ["Phantom-only fact."]          # "**Shared** fact one." matches after normalizing


def test_folded_text_keeps_marker_last_and_merges_ids():
    out = folded_text(REAL_A, "## Folded\n\nNew para.\n", ["p1#0", "p2#1"])
    assert out.endswith("<!-- loom-woven: p1#0 p2#1 r1#0 -->\n")
    assert "Real-only fact.\n\n## Folded\n\nNew para.\n\n<!--" in out
    assert out.count("loom-woven") == 1


def test_folded_text_without_fold_only_merges_markers():
    out = folded_text(REAL_A, None, ["p1#0"])
    assert out == "# A\n\nShared fact one.\n\nReal-only fact.\n\n<!-- loom-woven: p1#0 r1#0 -->\n"


def test_folded_text_no_markers_anywhere_stays_plain():
    assert folded_text("# X\n\nBody.\n", None, []) == "# X\n\nBody.\n"


# ---- plan (read-only) ----------------------------------------------------------

def test_plan_reports_actions_ids_candidates_and_is_read_only(wiki, folds):
    before = _git(wiki, "rev-parse", "HEAD")
    p = plan(wiki, folds_dir=folds)
    assert p["apply"] is False and p["folds_dir_exists"] is True
    by = {f["real_rel"]: f for f in p["files"]}
    assert by["_index.md"]["action"] == "delete" and by["_index.md"]["fold"] is None
    assert by["tools/a.md"]["action"] == "fold"
    assert by["tools/a.md"]["fold"].endswith("folds/tools/a.md")
    assert by["tools/a.md"]["ids"] == ["p1#0", "p2#1"]
    assert by["tools/a.md"]["candidate_paragraphs"] == ["Phantom-only fact."]
    assert by["tools/orphan.md"]["action"] == "move" and by["tools/orphan.md"]["real_exists"] is False
    assert p["unmatched_folds"] == []
    assert _git(wiki, "rev-parse", "HEAD") == before
    assert _git(wiki, "status", "--porcelain") == ""
    assert (wiki / "wiki" / "tools" / "a.md").exists()
    json.dumps(p)                                    # JSON-serialisable for the CLI


def test_plan_flags_fold_files_that_match_no_phantom(wiki, folds):
    (folds / "tools" / "typo.md").write_text("x\n")
    assert plan(wiki, folds_dir=folds)["unmatched_folds"] == ["tools/typo.md"]


def test_plan_without_phantom_tree_is_empty(tmp_path):
    (tmp_path / "w").mkdir()
    assert plan(tmp_path / "w")["files"] == []


# ---- apply --------------------------------------------------------------------

def test_apply_folds_merges_markers_removes_moves_and_commits(wiki, folds):
    res = apply(wiki, folds_dir=folds)
    assert res["applied"] is True
    assert res["removed"] == ["wiki/_index.md", "wiki/tools/a.md"]
    assert res["folded"] == ["tools/a.md"] and res["moved"] == ["tools/orphan.md"]
    assert res["markers_merged"] == {"tools/a.md": ["p1#0", "p2#1"]}
    assert res["shadow_synced"] is None and res["push"].endswith("push origin master")
    assert not (wiki / "wiki").exists()
    a = (wiki / "tools" / "a.md").read_text()
    assert "Phantom-only fact, curated." in a and "Real-only fact." in a
    assert markers_in(a) == {"r1#0", "p1#0", "p2#1"}
    assert a.rstrip().endswith("-->")
    assert (wiki / "tools" / "orphan.md").read_text() == PHANTOM_ORPHAN
    assert (wiki / "_index.md").read_text() == "# Real index\n"       # never folded into
    assert _git(wiki, "status", "--porcelain") == ""
    assert _git(wiki, "rev-parse", "HEAD").strip() == res["commit"]
    assert "phantom wiki/ tree" in _git(wiki, "log", "-1", "--format=%s")
    assert "wiki/" not in _git(wiki, "ls-tree", "-r", "--name-only", "HEAD")


def test_apply_without_folds_dir_only_merges_markers(wiki):
    res = apply(wiki, folds_dir=None)
    assert res["folded"] == [] and res["markers_merged"] == {"tools/a.md": ["p1#0", "p2#1"]}
    a = (wiki / "tools" / "a.md").read_text()
    assert "Phantom-only fact" not in a and markers_in(a) == {"r1#0", "p1#0", "p2#1"}


def test_apply_is_idempotent(wiki, folds):
    apply(wiki, folds_dir=folds)
    again = apply(wiki, folds_dir=folds)
    assert again["applied"] is False and again["reason"] == "no phantom tree"


def test_apply_refuses_missing_folds_dir(wiki, tmp_path):
    with pytest.raises(PhantomError, match="folds dir does not exist"):
        apply(wiki, folds_dir=tmp_path / "nope")


def test_apply_refuses_unmatched_fold_file(wiki, folds):
    (folds / "tools" / "typo.md").write_text("x\n")
    with pytest.raises(PhantomError, match="typo.md"):
        apply(wiki, folds_dir=folds)
    assert (wiki / "wiki" / "tools" / "a.md").exists()


def test_apply_refuses_dirty_tree(wiki, folds):
    (wiki / "tools" / "a.md").write_text(REAL_A + "edit\n")
    with pytest.raises(PhantomError, match="dirty"):
        apply(wiki, folds_dir=folds)


def test_apply_refuses_off_master(wiki, folds):
    _git(wiki, "checkout", "-q", "loom-shadow")
    with pytest.raises(PhantomError, match="expected master"):
        apply(wiki, folds_dir=folds)


def test_apply_refuses_while_loom_runs(wiki, folds):
    with pytest.raises(PhantomError, match="running"):
        apply(wiki, folds_dir=folds, running_check=lambda: True)
    assert (wiki / "wiki").exists()


def test_apply_refuses_when_lock_held(wiki, folds, tmp_path):
    import fcntl
    lock = tmp_path / ".run.lock"
    with open(lock, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(PhantomError, match="run.lock"):
            apply(wiki, folds_dir=folds, lock_path=lock)
    assert apply(wiki, folds_dir=folds, lock_path=lock)["applied"] is True


def test_apply_fast_forwards_shadow_worktree_when_in_sync(wiki, folds, tmp_path):
    shadow = tmp_path / "shadow"
    _git(wiki, "worktree", "add", "-q", str(shadow), "loom-shadow")
    assert (shadow / "wiki" / "tools" / "a.md").exists()
    res = apply(wiki, folds_dir=folds, shadow_root=shadow)
    assert res["shadow_synced"] is True
    assert not (shadow / "wiki").exists()
    assert _git(shadow, "rev-parse", "HEAD").strip() == res["commit"]


def test_apply_leaves_shadow_alone_when_it_has_unpromoted_commits(wiki, folds, tmp_path):
    shadow = tmp_path / "shadow"
    _git(wiki, "worktree", "add", "-q", str(shadow), "loom-shadow")
    (shadow / "tools" / "b.md").write_text("# B\n")
    _git(shadow, "add", "-A"); _git(shadow, "commit", "-qm", "weave: tools/b.md")
    res = apply(wiki, folds_dir=folds, shadow_root=shadow)
    assert res["shadow_synced"] is False
    assert (shadow / "wiki").exists()              # next promote carries the delete
    # and master really does merge the delete over an untouched shadow copy
    _git(wiki, "merge", "-q", "--no-ff", "loom-shadow", "-m", "promote")
    assert not (wiki / "wiki").exists() and (wiki / "tools" / "b.md").exists()


def test_apply_refuses_shadow_not_on_loom_shadow(wiki, folds, tmp_path):
    shadow = tmp_path / "shadow"
    _git(wiki, "worktree", "add", "-q", "-b", "other", str(shadow), "master")
    with pytest.raises(PhantomError, match="expected loom-shadow"):
        apply(wiki, folds_dir=folds, shadow_root=shadow)


# ---- cli ----------------------------------------------------------------------

def test_cli_plan_is_default_and_apply_passes_lock_and_shadow(monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr("loom.phantom.plan", lambda root, folds_dir=None: seen.update(plan=(root, folds_dir)) or {"apply": False})
    monkeypatch.setattr("loom.phantom.apply", lambda root, folds_dir=None, **k: seen.update(apply=(root, folds_dir, k)) or {"applied": True})
    assert cli.main(["reconcile-phantom", "--folds-dir", "/tmp/f"]) == 0
    assert seen["plan"] == (cli.default_config().wiki_master, Path("/tmp/f"))
    assert "apply" not in seen
    assert cli.main(["reconcile-phantom", "--apply", "--folds-dir", "/tmp/f"]) == 0
    root, folds_dir, kw = seen["apply"]
    assert root == cli.default_config().wiki_master and folds_dir == Path("/tmp/f")
    assert str(kw["lock_path"]).endswith("loom/.run.lock")
    assert str(kw["shadow_root"]).endswith("wiki-loom-shadow")
    out = capsys.readouterr().out
    assert '"applied": true' in out


def test_cli_default_folds_dir_is_the_repo_docs_dir(monkeypatch):
    seen = {}
    monkeypatch.setattr("loom.phantom.plan", lambda root, folds_dir=None: seen.update(f=folds_dir) or {})
    assert cli.main(["reconcile-phantom"]) == 0
    assert seen["f"] == cli._REPO / "docs" / "loom-phantom-wiki-folds"


def test_cli_apply_reports_phantom_error_as_json(monkeypatch, capsys):
    def boom(*a, **k):
        raise PhantomError("wiki working tree is dirty; aborting")
    monkeypatch.setattr("loom.phantom.apply", boom)
    assert cli.main(["reconcile-phantom", "--apply", "--folds-dir", "/tmp/f"]) == 1
    assert json.loads(capsys.readouterr().out) == {"applied": False,
                                                    "error": "wiki working tree is dirty; aborting"}
