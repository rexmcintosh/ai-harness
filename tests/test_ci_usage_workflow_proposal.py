"""The venice-review usage-artifact rollout is a patch this repo hands to 18
repos by hand. Nothing executes it here, so these tests are the only thing that
can catch a proposal that has rotted: a placeholder left in the pin, a `.patch`
and a `.proposed` that no longer agree, or an edit that quietly went missing.

The three edits, per workflow:
  1. `VENICE_USAGE_DB` on the review step's env (the ledger must leave $HOME)
  2. an `if: always()` export step (the ledger must become a file)
  3. an `if: always()` upload step (the file must leave the runner)
plus the council pin bump, without which step 2 has no command to run.
"""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/venice-review.yml"
PROPOSALS = ROOT / "docs/proposals"
PATCH = PROPOSALS / "venice-review-usage-artifact.patch"
PROPOSED = PROPOSALS / "venice-review.yml.proposed"
SW_PATCH = PROPOSALS / "venice-review-usage-artifact-swimtrack-website.patch"
SW_PROPOSED = PROPOSALS / "venice-review.yml.swimtrack-website.proposed"

# council-v0.4.0 — the pin every repo carried before this rollout. It packages
# `council` alone: no venice_usage, so no `venice-usage` command and no usage
# logging at all on the runner.
OLD_PIN = "4a01298dcce2734115ac57f02592146969d76f48"
# The repo was renamed build-ai-automation-workflow -> ai-harness on 2026-09-18.
# Repos not yet rolled out still carry the old URL, which GitHub redirects.
PIN_RE = re.compile(r"(?:build-ai-automation-workflow|ai-harness)@([0-9a-f]{40})\"")


def _pins(text: str) -> list[str]:
    return PIN_RE.findall(text)


@pytest.mark.parametrize("path", [PROPOSED, SW_PROPOSED, PATCH, SW_PATCH])
def test_no_placeholder_survives_in_a_proposal(path):
    assert "REPLACE_WITH" not in path.read_text(), f"{path.name} still has a placeholder"


@pytest.mark.parametrize("path", [PROPOSED, SW_PROPOSED])
def test_proposed_workflow_pins_a_real_sha_and_not_the_old_one(path):
    pins = _pins(path.read_text())
    assert len(pins) == 1, f"{path.name}: expected exactly one pin, got {pins}"
    assert pins[0] != OLD_PIN, f"{path.name}: pin was not bumped off council-v0.4.0"


def test_both_proposals_pin_the_same_council():
    assert _pins(PROPOSED.read_text()) == _pins(SW_PROPOSED.read_text())


def test_this_repo_uses_the_compatible_proposed_workflow():
    # The local rollout advances its pin again to include the privileged tooling fix.
    current = WORKFLOW.read_text()
    pin = _pins(current)[0]
    assert len(pin) == 40 and pin != OLD_PIN
    config = subprocess.check_output(["git", "show", f"{pin}:council/config.py"], cwd=ROOT, text=True)
    assert "max_completion_tokens" in config
    gate = subprocess.check_output(["git", "show", f"{pin}:council/gate.py"], cwd=ROOT, text=True)
    scope = {"__name__": "council.pinned_gate", "__package__": "council"}
    exec(compile(gate, "pinned/council/gate.py", "exec"), scope)
    assert scope["risk_tier"](["tools/publish.py"]) == "full"


def test_the_pin_is_a_commit_that_exists_in_this_repo_and_carries_venice_usage():
    """The pin has to be reachable from this repo's history, and it has to be a
    commit where `venice-usage` is a console script — the export step's command."""
    pin = _pins(PROPOSED.read_text())[0]
    if shutil.which("git") is None or not (ROOT / ".git").exists():
        pytest.skip("not a git checkout")
    ok = subprocess.run(["git", "-C", str(ROOT), "cat-file", "-e", f"{pin}^{{commit}}"])
    assert ok.returncode == 0, f"pin {pin} is not a commit in this repo"
    pyproject = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{pin}:pyproject.toml"], text=True)
    assert 'venice-usage = "venice_usage.cli:main"' in pyproject, (
        f"pin {pin} has no venice-usage console script; the export step would fail")
    config = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{pin}:council/config.py"], text=True)
    assert "max_completion_tokens" in config, (
        f"pin {pin} lacks Settings.max_completion_tokens; this repo's review script would fail")


@pytest.mark.parametrize("path", [PROPOSED, SW_PROPOSED, WORKFLOW])
def test_proposed_workflow_carries_all_three_edits(path):
    doc = yaml.safe_load(path.read_text())
    steps = doc["jobs"]["review"]["steps"]
    by_name = {s.get("name"): s for s in steps}

    review = by_name["Run Venice review council"]
    assert review["env"]["VENICE_USAGE_DB"] == "${{ runner.temp }}/venice-usage.db"

    export = by_name["Export Venice usage"]
    assert export["if"] == "always()" and export["continue-on-error"] is True
    assert export["env"]["VENICE_USAGE_DB"] == review["env"]["VENICE_USAGE_DB"]
    assert "venice-usage export" in export["run"]

    upload = by_name["Upload Venice usage"]
    assert upload["if"] == "always()" and upload["continue-on-error"] is True
    assert upload["uses"].startswith("actions/upload-artifact@")
    # run_attempt in the name: v4 artifacts are immutable, and a re-run really
    # is a second panel's worth of spend.
    assert "${{ github.run_attempt }}" in upload["with"]["name"]

    # Accounting rides on the existing token; it must not widen permissions.
    assert doc["permissions"] == {"contents": "read", "pull-requests": "write"}
    # The export/upload pair must be last, so a review failure still reaches them.
    assert [s.get("name") for s in steps[-2:]] == ["Export Venice usage",
                                                   "Upload Venice usage"]


def test_swimtrack_website_variant_keeps_its_own_wiring():
    """swimtrack-website is the one repo the shared patch cannot touch: extra
    Node/test steps and two extra env vars on the review step. The hand edit
    must add to that env block, not replace it."""
    doc = yaml.safe_load(SW_PROPOSED.read_text())
    steps = doc["jobs"]["review"]["steps"]
    env = [s for s in steps if s.get("name") == "Run Venice review council"][0]["env"]
    assert env["GITHUB_WORKSPACE"] == "${{ github.workspace }}"
    assert "COUNCIL_ENFORCE" in env
    assert any(s.get("run") == "npm test" for s in steps), "lost the npm test step"


def _apply(patch: Path, before: str, tmp_path: Path) -> str:
    work = tmp_path / "repo"
    (work / ".github/workflows").mkdir(parents=True)
    (work / ".github/workflows/venice-review.yml").write_text(before)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    subprocess.run(["git", "apply", str(patch)], cwd=work, check=True,
                   capture_output=True, text=True)
    return (work / ".github/workflows/venice-review.yml").read_text()


def test_patch_turns_this_repo_s_workflow_into_the_proposed_file(tmp_path):
    """This repo is one of the 18. Either the rollout has not happened here yet
    (the patch applies and reproduces the proposal byte for byte) or it has
    (the workflow already IS the proposal). Anything else means the patch and
    the proposal have drifted apart."""
    current = WORKFLOW.read_text()
    if _pins(current) and _pins(current)[0] != OLD_PIN:
        pytest.skip("rollout already applied to this repo")
    assert _apply(PATCH, current, tmp_path) == PROPOSED.read_text()


def test_the_two_patches_are_not_interchangeable(tmp_path):
    """The shared patch must refuse swimtrack-website's file rather than
    mangling it — that refusal is what makes 'apply to 17, hand-edit 1' safe."""
    work = tmp_path / "repo"
    (work / ".github/workflows").mkdir(parents=True)
    sw_before = SW_PROPOSED.read_text()  # any file that is not the canonical one
    (work / ".github/workflows/venice-review.yml").write_text(sw_before)
    subprocess.run(["git", "init", "-q"], cwd=work, check=True)
    r = subprocess.run(["git", "apply", "--check", str(PATCH)], cwd=work,
                       capture_output=True, text=True)
    assert r.returncode != 0
