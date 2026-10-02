"""What may go to TypeSafe. The owner decides; this file records it and enforces the part
that code can enforce (file paths). TypeSafe states it does not train on requests, but its
DPA gives no retention period, so the scope stays narrow.
"""
from __future__ import annotations

ALLOWED_NOTE = (
    "Owner decisions. 2026-09-18: public and operations data (public news, public social posts, "
    "marketing copy, public listings, synthetic questions, operations logs). 2026-09-19: unpublished "
    "manuscript prose is also allowed. 2026-09-19, for council work only: Jev may also receive the "
    "council's own words (panel findings, chair text) and code snippets and diffs; the NOT-allowed "
    "list below is unchanged, and repos that hold such data stay out of the council signals. "
    "NOT allowed: personal email, the private wiki, children's or "
    "student data, customer data, financial and tax records. 2026-09-19: anything that describes a "
    "repository's work to Jev (the backlog hold gate sends an item's title and prompt) follows a repo "
    "allow-list, IN_SCOPE_REPOS below; a repository that is not on it, or that is unknown, is refused."
)

# A path holding any of these is refused at run time, whatever a config file says.
# Extend it; never trim it without the owner.
OUT_OF_SCOPE = ("sat-prep", "attainprep", "bento", "bebop", "tax", "finance", "rent",
                "swimtrack-coach", "gmail", "mail", "/wiki/")


def in_scope(path) -> bool:
    text = str(path or "")
    return text.startswith("/") and not any(word in text.lower() for word in OUT_OF_SCOPE)


# The repositories whose work may be described to Jev (an item's title and prompt, a diff
# summary). These are the five in the 2026-09-19 offline council test. Adding one is an owner
# decision: extend it deliberately. A repository that is not listed, or is unknown, is refused.
IN_SCOPE_REPOS = frozenset({"ai-harness", "swimtrack", "swimtrack-website", "ultimate-portugal",
                            "aris-management-website"})


def repo_in_scope(repo, name="") -> bool:
    """True only for an exact listed repository name. `name` is what the work is called (a
    backlog item id, a branch): an OUT_OF_SCOPE word in it, or in the repo, refuses it too."""
    if not isinstance(repo, str) or repo not in IN_SCOPE_REPOS:
        return False
    text = f"{name} {repo}".lower()
    return not any(word in text for word in OUT_OF_SCOPE)
