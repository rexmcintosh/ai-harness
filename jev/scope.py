"""What may go to TypeSafe. The owner decides; this file records it and enforces the part
that code can enforce (file paths). TypeSafe states it does not train on requests, but its
DPA gives no retention period, so the scope stays narrow.
"""
from __future__ import annotations

ALLOWED_NOTE = (
    "Owner decisions. 2026-09-18: public and operations data (public news, public social posts, "
    "marketing copy, public listings, synthetic questions, operations logs). 2026-09-19: unpublished "
    "manuscript prose is also allowed. NOT allowed: personal email, the private wiki, children's or "
    "student data, customer data, financial and tax records."
)

# A path holding any of these is refused at run time, whatever a config file says.
# Extend it; never trim it without the owner.
OUT_OF_SCOPE = ("sat-prep", "attainprep", "bento", "bebop", "tax", "finance", "rent",
                "swimtrack-coach", "gmail", "mail", "/wiki/")


def in_scope(path) -> bool:
    text = str(path or "")
    return text.startswith("/") and not any(word in text.lower() for word in OUT_OF_SCOPE)
