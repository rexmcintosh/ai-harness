from __future__ import annotations

from .routing import split_diff_by_type, changed_paths
from .engine import run_panel
from .synthesize import synthesize
from .render import render_combined
from .prompts import REVIEW_SYNTH_OUTPUT
from .gate import risk_tier, decide_blocking
from .config import Settings, resolve_budget


def _code_context(code_diff: str, file_context: str) -> str:
    """The review context shared by the panel and the chair. The chair grounds its
    blocking decision against this, so the full-file context (when present) must be
    here, not just the diff hunk (audit F3/F4)."""
    ctx = f"Review this code diff:\n\n```diff\n{code_diff}\n```"
    if file_context.strip():
        ctx += ("\n\nFULL CONTENTS OF THE CHANGED FILES (for context — flag only the "
                "diff above, but reason with this; do not flag issues already handled "
                "here):\n\n" + file_context)
    return ctx


def run_pr_review(diff: str, panels: dict, client, *, chair_model: str,
                  file_context: str = "", settings=None):
    """Split a PR diff, review the code slice (gated) and the doc slice (advisory).

    Returns (body, blocking, unavailable):
      - body: one combined comment (a section per non-empty slice)
      - blocking: count of CHAIR-CONFIRMED blocking findings in the CODE section. The
        chair grounds these against the provided context and is the sole arbiter; a raw
        panelist finding no longer blocks on its own (audit F1/F2/F4). Blast radius is
        factored in via the tier (audit F6).
      - unavailable: fail-closed flag keyed on the CODE review only (chair errored, or
        >= half the code panel errored). Doc-side failures never set it.

    chair_model: the GLOBAL chair, as the per-repo CI shims pass it. A panel that names its
      own `chair_model` in panels.toml uses that for its slice instead, so the shims need
      no change (docs/council-chair-decision-2026-09-20.md).
    file_context: optional full contents of the changed files, supplied by the CI shim
      from the checkout, so the panel and chair can verify findings (audit S1).
    settings: optional council Settings, for the output ceilings. Omitting it is
      normal — the per-repo shims predate the argument — and falls back to the
      Settings class defaults, so an un-updated shim is still capped rather than
      unbounded.
    """
    settings = settings or Settings()
    code_diff, doc_diff = split_diff_by_type(diff)
    sections = []
    blocking = 0
    unavailable = False

    if code_diff.strip():
        ctx = _code_context(code_diff, file_context)
        panel = panels["code-review"]
        budget = resolve_budget(settings, panel, panel.default_rigor)
        results = run_panel(panel, ctx, client, task_type="review", budget=budget)
        syn = synthesize(ctx, results, client, chair_model=panel.chair_model or chair_model,
                         system=REVIEW_SYNTH_OUTPUT, task_type="review",
                         max_completion_tokens=budget.chair)
        sections.append(("Code review (gate)", "", "Code changes", syn, results))
        tier = risk_tier(changed_paths(code_diff))
        blocking = decide_blocking(results, syn, tier=tier)
        errored = sum(1 for r in results if r.error)
        unavailable = syn.error is not None or not results or errored * 2 >= len(results)

    if doc_diff.strip():
        panel = panels["spec-review"]
        budget = resolve_budget(settings, panel, panel.default_rigor)
        results = run_panel(
            panel,
            f"Review this design doc / spec / plan diff:\n\n```diff\n{doc_diff}\n```", client,
            task_type="review", budget=budget)
        syn = synthesize("Doc review", results, client,
                         chair_model=panel.chair_model or chair_model,
                         task_type="review", max_completion_tokens=budget.chair)
        sections.append(("Docs review (advisory)",
                         "Advisory only — does not affect the merge check.",
                         "Doc changes", syn, results))

    if not sections:
        return "Nothing to review.", 0, False
    return render_combined(sections), blocking, unavailable
