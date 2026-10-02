# tests/loom/test_promote.py
import json
import subprocess
from pathlib import Path
import pytest
from loom.promote import promote, rollback, PromoteError


def _git(root, *a):
    subprocess.run(["git", "-C", str(root), *a], check=True, capture_output=True, text=True)


@pytest.fixture
def env(tmp_path):
    wiki = tmp_path / "wiki"; wiki.mkdir()
    _git(wiki, "init", "-q"); _git(wiki, "config", "user.email", "t@t"); _git(wiki, "config", "user.name", "t")
    (wiki / "people").mkdir(); (wiki / "people" / "liam.md").write_text("# Liam\nv0\n")
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "seed")
    _git(wiki, "branch", "loom-shadow")
    # on shadow: update an article AND stage a .claude memory file
    _git(wiki, "checkout", "-q", "loom-shadow")
    (wiki / "people" / "liam.md").write_text("# Liam\nv1 woven\n")
    staged = wiki / "_staged" / ".claude" / "memory" / "feedback-x.md"
    staged.parent.mkdir(parents=True); staged.write_text("a new preference\n")
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "weave + staged")
    _git(wiki, "checkout", "-q", "master")
    claude = tmp_path / "claude"; (claude / "memory").mkdir(parents=True)
    backups = tmp_path / "backups"
    return {"wiki": wiki, "claude": claude, "backups": backups}


def test_promote_applies_claude_and_merges(env):
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"])
    # .claude memory landed
    assert (env["claude"] / "memory" / "feedback-x.md").read_text() == "a new preference\n"
    # master advanced and carries no _staged/
    head = subprocess.run(["git", "-C", str(env["wiki"]), "log", "master", "--oneline"],
                          capture_output=True, text=True).stdout
    assert "weave" in head
    assert not (env["wiki"] / "_staged").exists()
    assert (env["wiki"] / "people" / "liam.md").read_text() == "# Liam\nv1 woven\n"


def test_dirty_claude_target_aborts_before_touching(env):
    # pre-existing modified target out of band
    tgt = env["claude"] / "memory" / "feedback-x.md"
    tgt.write_text("USER EDIT\n")
    with pytest.raises(PromoteError):
        promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"],
                expect_unmodified=True)
    # untouched
    assert tgt.read_text() == "USER EDIT\n"


def test_rollback_restores_from_manifest(env):
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"])
    tgt = env["claude"] / "memory" / "feedback-x.md"
    tgt.write_text("changed after promote\n")
    ts = sorted(p.name for p in env["backups"].iterdir())[-1]
    rollback(backups_dir=env["backups"], ts=ts)
    # the pre-promote state for a NEWLY created file is absence
    assert not tgt.exists()


def test_rollback_restores_preexisting_file(env):
    # a file that already exists in ~/.claude before promote (will be backed up)
    tgt = env["claude"] / "memory" / "feedback-x.md"
    tgt.write_text("ORIGINAL\n")
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"])
    assert tgt.read_text() == "a new preference\n"          # promote overwrote it
    ts = sorted(p.name for p in env["backups"].iterdir())[-1]
    rollback(backups_dir=env["backups"], ts=ts)
    assert tgt.read_text() == "ORIGINAL\n"                  # restored from backup


def test_promote_update_of_loom_managed_file_succeeds(env):
    # first promote creates the .claude file and records its promoted_sha
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"], expect_unmodified=True)
    tgt = env["claude"] / "memory" / "feedback-x.md"
    assert tgt.read_text() == "a new preference\n"
    # re-stage an UPDATED version on loom-shadow
    _git(env["wiki"], "checkout", "-q", "loom-shadow")
    staged = env["wiki"] / "_staged" / ".claude" / "memory" / "feedback-x.md"
    staged.parent.mkdir(parents=True, exist_ok=True); staged.write_text("an updated preference\n")
    _git(env["wiki"], "add", "-A"); _git(env["wiki"], "commit", "-qm", "restage update")
    _git(env["wiki"], "checkout", "-q", "master")
    # second promote with the guard ON must SUCCEED (on-disk still matches last promoted)
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"], expect_unmodified=True)
    assert tgt.read_text() == "an updated preference\n"


@pytest.fixture
def wt_env(tmp_path):
    """Mirrors the real topology: ~/wiki (master worktree) + ~/wiki-loom-shadow
    (loom-shadow linked worktree). promote must never `git checkout` either branch,
    or it hits 'already checked out elsewhere'."""
    wiki = tmp_path / "wiki"; wiki.mkdir()
    _git(wiki, "init", "-q", "-b", "master")
    _git(wiki, "config", "user.email", "t@t"); _git(wiki, "config", "user.name", "t")
    (wiki / "people").mkdir(); (wiki / "people" / "liam.md").write_text("# Liam\nv0\n")
    _git(wiki, "add", "-A"); _git(wiki, "commit", "-qm", "seed")
    shadow = tmp_path / "wiki-loom-shadow"
    _git(wiki, "worktree", "add", "-q", "-b", "loom-shadow", str(shadow), "master")
    # weave happens in the shadow worktree
    (shadow / "people" / "liam.md").write_text("# Liam\nv1 woven\n")
    staged = shadow / "_staged" / ".claude" / "memory" / "feedback-x.md"
    staged.parent.mkdir(parents=True); staged.write_text("a new preference\n")
    _git(shadow, "add", "-A"); _git(shadow, "commit", "-qm", "weave + staged")
    claude = tmp_path / "claude"; (claude / "memory").mkdir(parents=True)
    backups = tmp_path / "backups"
    return {"wiki": wiki, "shadow": shadow, "claude": claude, "backups": backups}


def _rev(root, ref):
    return subprocess.run(["git", "-C", str(root), "rev-parse", ref],
                          capture_output=True, text=True).stdout.strip()


def test_promote_with_worktrees_applies_and_ffs_shadow(wt_env):
    e = wt_env
    promote(wiki_root=e["wiki"], shadow_root=e["shadow"],
            claude_root=e["claude"], backups_dir=e["backups"])
    # .claude memory landed
    assert (e["claude"] / "memory" / "feedback-x.md").read_text() == "a new preference\n"
    # master advanced with the woven article and carries no _staged/
    assert (e["wiki"] / "people" / "liam.md").read_text() == "# Liam\nv1 woven\n"
    assert not (e["wiki"] / "_staged").exists()
    # loom-shadow fast-forwarded to master (so the next weave starts clean, no _staged)
    assert _rev(e["wiki"], "master") == _rev(e["wiki"], "loom-shadow")
    assert not (e["shadow"] / "_staged").exists()


def test_promote_rejects_foreign_shadow_root(wt_env, tmp_path):
    """A valid git repo that is NOT a worktree of wiki_root must be rejected before any
    mutation — otherwise the ff silently advances a foreign branch and the real
    loom-shadow goes stale."""
    e = wt_env
    before = _rev(e["wiki"], "master")
    foreign = tmp_path / "foreign"; foreign.mkdir()
    _git(foreign, "init", "-q", "-b", "loom-shadow")
    _git(foreign, "config", "user.email", "t@t"); _git(foreign, "config", "user.name", "t")
    (foreign / "x").write_text("x\n"); _git(foreign, "add", "-A"); _git(foreign, "commit", "-qm", "x")
    with pytest.raises(PromoteError):
        promote(wiki_root=e["wiki"], shadow_root=foreign,
                claude_root=e["claude"], backups_dir=e["backups"])
    assert not (e["claude"] / "memory" / "feedback-x.md").exists()
    assert _rev(e["wiki"], "master") == before


def test_promote_rejects_shadow_not_on_loom_shadow(wt_env):
    e = wt_env
    before = _rev(e["wiki"], "master")
    _git(e["shadow"], "checkout", "-q", "-b", "wrongbranch")
    with pytest.raises(PromoteError):
        promote(wiki_root=e["wiki"], shadow_root=e["shadow"],
                claude_root=e["claude"], backups_dir=e["backups"])
    assert not (e["claude"] / "memory" / "feedback-x.md").exists()
    assert _rev(e["wiki"], "master") == before


def test_promote_rejects_dirty_shadow(wt_env):
    e = wt_env
    before = _rev(e["wiki"], "master")
    (e["shadow"] / "people" / "liam.md").write_text("uncommitted edit\n")
    with pytest.raises(PromoteError):
        promote(wiki_root=e["wiki"], shadow_root=e["shadow"],
                claude_root=e["claude"], backups_dir=e["backups"])
    assert not (e["claude"] / "memory" / "feedback-x.md").exists()
    assert _rev(e["wiki"], "master") == before


def test_promote_post_mutation_failure_rolls_back(wt_env, monkeypatch):
    """A failure AFTER the .claude swap and the master merge+rm (here: the final shadow
    fast-forward) must roll back the swap, reset master to its pre-promote HEAD, and leave
    the master working tree clean (no half-merge, no leftover staged-removal)."""
    import loom.promote as P
    e = wt_env
    before = _rev(e["wiki"], "master")
    real_git = P._git

    def flaky_git(root, *args, check=True):
        if args[:2] == ("merge", "--ff-only"):     # the very last step, post-mutation
            raise P.PromoteError("simulated fast-forward failure")
        return real_git(root, *args, check=check)
    monkeypatch.setattr(P, "_git", flaky_git)

    with pytest.raises(PromoteError):
        promote(wiki_root=e["wiki"], shadow_root=e["shadow"],
                claude_root=e["claude"], backups_dir=e["backups"])
    # newly-created .claude target removed, master back where it was
    assert not (e["claude"] / "memory" / "feedback-x.md").exists()
    assert _rev(e["wiki"], "master") == before
    # master working tree clean — no in-progress merge, no orphaned files
    porcelain = subprocess.run(["git", "-C", str(e["wiki"]), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
    assert porcelain == ""


def test_promote_refuses_out_of_band_edit(env):
    promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"], expect_unmodified=True)
    tgt = env["claude"] / "memory" / "feedback-x.md"
    tgt.write_text("USER EDITED THIS\n")                 # out-of-band change after promote
    _git(env["wiki"], "checkout", "-q", "loom-shadow")
    staged = env["wiki"] / "_staged" / ".claude" / "memory" / "feedback-x.md"
    staged.parent.mkdir(parents=True, exist_ok=True); staged.write_text("loom update\n")
    _git(env["wiki"], "add", "-A"); _git(env["wiki"], "commit", "-qm", "restage")
    _git(env["wiki"], "checkout", "-q", "master")
    with pytest.raises(PromoteError):
        promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"], expect_unmodified=True)
    assert tgt.read_text() == "USER EDITED THIS\n"        # untouched


# --- mirroring to the remote -------------------------------------------------
# Loom is useless as a knowledge base if Rex cannot read it. He reads it in Obsidian,
# which clones the GitHub remote — so a promote that never leaves the VPS is invisible.

@pytest.fixture
def env_remote(env, tmp_path):
    """`env` plus a bare origin holding master — the GitHub mirror Obsidian clones."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    _git(env["wiki"], "remote", "add", "origin", str(origin))
    _git(env["wiki"], "push", "-q", "-u", "origin", "master")
    return {**env, "origin": origin}


def _remote_sha(origin, ref="master"):
    return subprocess.run(["git", "-C", str(origin), "rev-parse", ref],
                          capture_output=True, text=True).stdout.strip()


def test_promote_pushes_master_to_origin(env_remote):
    """The whole point: what promote lands must reach the remote, or Obsidian never sees it."""
    res = promote(wiki_root=env_remote["wiki"], claude_root=env_remote["claude"],
                  backups_dir=env_remote["backups"])
    assert res["pushed"] is True
    assert _remote_sha(env_remote["origin"]) == _rev(env_remote["wiki"], "master")


def test_promote_takes_remote_edits_before_merging(env_remote):
    """Rex fixes an article in Obsidian and pushes. Promote must absorb that first,
    or its own push is rejected non-fast-forward and mirroring silently dies."""
    clone = env_remote["origin"].parent / "clone"
    subprocess.run(["git", "clone", "-q", str(env_remote["origin"]), str(clone)], check=True)
    _git(clone, "config", "user.email", "t@t"); _git(clone, "config", "user.name", "t")
    (clone / "people" / "rex.md").write_text("# Rex\nhand-written in Obsidian\n")
    _git(clone, "add", "-A"); _git(clone, "commit", "-qm", "obsidian edit")
    _git(clone, "push", "-q", "origin", "master")

    res = promote(wiki_root=env_remote["wiki"], claude_root=env_remote["claude"],
                  backups_dir=env_remote["backups"])

    assert res["pushed"] is True
    # Rex's edit survived AND the weave landed
    assert (env_remote["wiki"] / "people" / "rex.md").read_text() == "# Rex\nhand-written in Obsidian\n"
    assert (env_remote["wiki"] / "people" / "liam.md").read_text() == "# Liam\nv1 woven\n"
    assert _remote_sha(env_remote["origin"]) == _rev(env_remote["wiki"], "master")


def test_unreachable_remote_aborts_before_mutating(env_remote):
    """If we cannot even read the remote we cannot know we are up to date, so stop
    while nothing has changed rather than promote onto a possibly stale master."""
    gone = env_remote["origin"].parent / "vanished.git"
    _git(env_remote["wiki"], "remote", "set-url", "origin", str(gone))
    with pytest.raises(PromoteError):
        promote(wiki_root=env_remote["wiki"], claude_root=env_remote["claude"],
                backups_dir=env_remote["backups"])
    assert not (env_remote["claude"] / "memory" / "feedback-x.md").exists()
    assert _rev(env_remote["wiki"], "master") == _rev(env_remote["wiki"], "origin/master")


def test_push_failure_keeps_the_promote_and_reports_it(env_remote):
    """Fetch worked, so the promote is sound and committed; only the mirror leg failed.
    Undoing good content over a network blip would be the worse outcome — report instead."""
    hook = env_remote["origin"] / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)

    res = promote(wiki_root=env_remote["wiki"], claude_root=env_remote["claude"],
                  backups_dir=env_remote["backups"])

    assert res["pushed"] is False and res["push_error"]
    # the promote itself stands: .claude applied, weave on master, tree clean
    assert (env_remote["claude"] / "memory" / "feedback-x.md").read_text() == "a new preference\n"
    assert (env_remote["wiki"] / "people" / "liam.md").read_text() == "# Liam\nv1 woven\n"
    porcelain = subprocess.run(["git", "-C", str(env_remote["wiki"]), "status", "--porcelain"],
                               capture_output=True, text=True).stdout.strip()
    assert porcelain == ""


def test_local_only_wiki_still_promotes(env):
    """No remote configured (fresh install, tests) — promote must not require one."""
    res = promote(wiki_root=env["wiki"], claude_root=env["claude"], backups_dir=env["backups"])
    assert res["pushed"] is None          # not applicable, not a failure
    assert (env["wiki"] / "people" / "liam.md").read_text() == "# Liam\nv1 woven\n"
