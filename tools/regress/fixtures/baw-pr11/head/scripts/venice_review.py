"""Venice review council — GitHub Action shim over the `council` engine.

Splits the PR diff, reviews code changes (gated) and doc changes (advisory) via
council.review.run_pr_review, posts/updates ONE consolidated comment, and exits 1
when there are chair-confirmed blocking code findings or the code review was
unavailable (fail closed).

Audit-driven behavior (see docs/council-audit-2026-06-25.md):
  - S1: the panel and chair see the FULL contents of the changed files (read from the
    checkout) plus package.json/.gitignore anchors — not just the diff hunk — so
    findings are grounded against the real code instead of hallucinated from context.
  - S2: a PR carries one rolling council comment (updated in place), not a new comment
    per push.
  - S3: COUNCIL_ENFORCE=0 runs the gate in advisory mode (post the comment, never fail
    on findings); genuine engine/infra failures still fail closed.
  - E3: the gate path runs at temperature 0 for run-to-run determinism.

Hardening (see docs/venice-review-shim-audit-2026-07-23.md in build-ai-automation-workflow):
  - Diff-derived paths are untrusted (council.routing.changed_paths extracts them
    verbatim), so gather_file_context resolves each one and skips anything outside the
    checkout root — a crafted diff cannot exfiltrate files via the review API/comment.
  - A non-numeric COUNCIL_FILE_CAP falls back to the default instead of crashing.

Hardening round 2 (council self-review of the CI rollout, 2026-08-15):
  - GitHub API calls retry on connection errors and 5xx, so a network blip can't
    silently drop the review comment.
  - The file-context total cap counts UTF-8 bytes like the per-file cap, not
    characters — multi-byte text no longer overshoots the engine byte budget.
  - Required env vars are validated upfront with a clear error instead of a
    KeyError traceback (fail closed, same as an engine failure).

Required env: VENICE_API_KEY, GITHUB_TOKEN, PR_NUMBER, REPO, DIFF_PATH
Optional env: COUNCIL_ENFORCE (default "1"), COUNCIL_FILE_CAP (bytes/file, default 40000),
              GITHUB_WORKSPACE (checkout root, default ".")
"""
from __future__ import annotations
import os, sys, time
from pathlib import Path

import requests
from council.config import load_panels, get_api_key, truncate
from council.venice import VeniceClient
from council.review import run_pr_review
from council.routing import changed_paths

GITHUB_API = "https://api.github.com"
MARKER = "<!-- council-review -->"            # identifies our comment for in-place updates
REQUIRED_ENV = ("VENICE_API_KEY", "GITHUB_TOKEN", "PR_NUMBER", "REPO", "DIFF_PATH")
# Cheap repo anchors that kill the most common diff-blind false positives (an engines
# pin, a gitignored secret) regardless of whether they appear in the hunk.
ANCHORS = ("package.json", ".gitignore", ".nvmrc")


def gather_file_context(diff, root, *, per_file_cap=40_000, total_cap=160_000):
    """Full contents of each changed file in the diff (plus repo anchors), read from
    the checkout at `root`, so the panel and chair can verify findings against the real
    code. Capped per file and overall — both in UTF-8 bytes — to stay within the
    engine byte budget. Missing files (deletes, renames, absent anchors) are skipped,
    never fatal."""
    root = Path(root).resolve()
    seen, parts, total = set(), [], 0
    for rel in list(changed_paths(diff)) + list(ANCHORS):
        if not rel or rel in seen:
            continue
        seen.add(rel)
        try:
            # Diff-derived paths are untrusted: refuse absolute/`..` escapes so a
            # crafted diff can never read outside the checkout.
            candidate = (root / rel).resolve()
            if not candidate.is_relative_to(root):
                continue
            text = candidate.read_text(encoding="utf-8", errors="ignore")
        except (OSError, ValueError):
            continue
        if len(text.encode("utf-8", "ignore")) > per_file_cap:
            text = truncate(text, per_file_cap)
        block = f"=== {rel} ===\n{text}\n"
        block_bytes = len(block.encode("utf-8", "ignore"))
        if total + block_bytes > total_cap:
            break
        parts.append(block)
        total += block_bytes
    return "\n".join(parts)


def _gh(method, url, token, **kw):
    """One GitHub API call, retried on connection errors and 5xx (3 attempts,
    exponential backoff) so a transient blip can't drop the review comment."""
    last_response, last_error = None, None
    for attempt in range(3):
        if attempt:
            time.sleep(2 ** attempt)
        try:
            r = requests.request(method, url,
                                 headers={"Authorization": f"Bearer {token}",
                                          "Accept": "application/vnd.github+json"},
                                 timeout=30, **kw)
        except requests.RequestException as err:
            last_error = err
            continue
        if r.status_code < 500:
            return r
        last_response = r
    if last_response is not None:
        return last_response
    raise last_error


def find_council_comment(repo, pr, token):
    """The id of our existing council comment on this PR (by marker), or None."""
    r = _gh("GET", f"{GITHUB_API}/repos/{repo}/issues/{pr}/comments?per_page=100", token)
    r.raise_for_status()
    for c in r.json():
        if MARKER in (c.get("body") or ""):
            return c["id"]
    return None


def upsert_comment(repo, pr, body, token):
    """Update our existing council comment in place if present, else create one — so a
    PR carries ONE rolling review, not a new comment per push (audit S2/F8)."""
    body = f"{body}\n\n{MARKER}"
    cid = find_council_comment(repo, pr, token)
    if cid is not None:
        _gh("PATCH", f"{GITHUB_API}/repos/{repo}/issues/comments/{cid}", token,
            json={"body": body}).raise_for_status()
    else:
        _gh("POST", f"{GITHUB_API}/repos/{repo}/issues/{pr}/comments", token,
            json={"body": body}).raise_for_status()


def post_comment(repo, pr, body, token):   # back-compat alias
    upsert_comment(repo, pr, body, token)


def main() -> int:
    missing = [k for k in REQUIRED_ENV if not os.environ.get(k)]
    if missing:
        print(f"::error::missing required env: {', '.join(missing)} — failing closed",
              file=sys.stderr)
        return 1
    settings, panels = load_panels()
    diff = truncate(Path(os.environ["DIFF_PATH"]).read_text(), settings.byte_cap)
    if not diff.strip():
        print("Empty diff, nothing to review.")
        return 0
    try:
        file_cap = int(os.environ.get("COUNCIL_FILE_CAP", "40000"))
    except ValueError:
        file_cap = 40_000
    file_context = gather_file_context(
        diff, os.environ.get("GITHUB_WORKSPACE", "."), per_file_cap=file_cap)
    # temperature=0 on the gate path for run-to-run determinism (audit E3/B).
    client = VeniceClient(get_api_key(), timeout=settings.timeout, temperature=0)
    body, blocking, unavailable = run_pr_review(
        diff, panels, client, chair_model=settings.chair_model, file_context=file_context)
    upsert_comment(os.environ["REPO"], os.environ["PR_NUMBER"], body, os.environ["GITHUB_TOKEN"])
    if unavailable:
        print("::error::review council unavailable (code panel/chair failed) — failing closed",
              file=sys.stderr)
        return 1
    if blocking:
        enforce = os.environ.get("COUNCIL_ENFORCE", "1").lower() not in ("0", "false", "no")
        msg = f"{blocking} chair-confirmed blocking finding(s) from the review council"
        if enforce:
            print(f"::error::{msg}", file=sys.stderr)
            return 1
        print(f"::warning::{msg} — advisory mode (COUNCIL_ENFORCE=0), not failing the check",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
