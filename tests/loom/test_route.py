# tests/loom/test_route.py
from loom.route import confirm_route


class _Backend:
    def __init__(self, reply): self._reply = reply
    def complete(self, role, system, user, json_mode=False):
        assert role == "route" and json_mode is True
        return self._reply


_LEARNING = {"type": "fact", "subject": "Liam", "learning": "swims for Bullsharks",
             "route": "wiki/people/liam"}


def test_parses_model_json():
    b = _Backend('{"target": "people/liam.md", "action": "update", "cross_links": ["portugal"]}')
    r = confirm_route(b, _LEARNING, index_listing="- [[liam]] ...")
    assert r == {"target": "people/liam.md", "action": "update", "cross_links": ["portugal"]}


def test_tolerates_json_in_code_fence():
    b = _Backend('```json\n{"target":"people/liam.md","action":"update","cross_links":[]}\n```')
    r = confirm_route(b, _LEARNING, index_listing="")
    assert r["target"] == "people/liam.md"


def test_falls_back_to_suggested_route_on_garbage():
    b = _Backend("not json at all")
    r = confirm_route(b, _LEARNING, index_listing="")
    assert r["target"] == "people/liam.md" and r["action"] == "update"


def test_returns_none_when_unparseable_and_no_suggestion():
    b = _Backend("garbage")
    r = confirm_route(b, {"type": "fact", "subject": "x", "learning": "y"}, index_listing="")
    assert r is None


# --- target normalization -------------------------------------------------
# The router echoes the distill artifact's `wiki/<dir>/<slug>` route form often
# enough that taking the model's target verbatim built a whole second tree at
# <wiki>/wiki/ (4 stray articles + 19 queued learnings, found 2026-07-23).
# _suggested_target already stripped the prefix; the model path did not.

def test_strips_wiki_prefix_from_model_target():
    b = _Backend('{"target": "wiki/tools/loom.md", "action": "update"}')
    r = confirm_route(b, _LEARNING, index_listing="")
    assert r["target"] == "tools/loom.md"


def test_strips_repeated_and_dotted_prefixes():
    b = _Backend('{"target": "./wiki/wiki/patterns/x.md", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "patterns/x.md"


def test_strips_leading_slash():
    b = _Backend('{"target": "/people/liam.md", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_appends_md_suffix():
    b = _Backend('{"target": "people/liam", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_rejects_traversal_and_falls_back():
    b = _Backend('{"target": "../../.ssh/authorized_keys.md", "action": "update"}')
    r = confirm_route(b, _LEARNING, index_listing="")
    assert r["target"] == "people/liam.md"          # fell back to the suggested route


def test_rejects_traversal_with_no_suggestion():
    b = _Backend('{"target": "people/../../etc/passwd.md", "action": "update"}')
    r = confirm_route(b, {"type": "fact", "subject": "x", "learning": "y"}, index_listing="")
    assert r is None


def test_suggested_route_is_normalized_too():
    b = _Backend("garbage")
    r = confirm_route(b, {"learning": "y", "route": "/wiki/tools/loom"}, index_listing="")
    assert r["target"] == "tools/loom.md"


def test_rejects_drive_qualified_path():
    # Contained on this POSIX host (it would just make a `C:` dir), but the
    # docstring promises containment unconditionally — so refuse it outright.
    b = _Backend('{"target": "C:/Users/x.md", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_rejects_drive_relative_path():
    b = _Backend('{"target": "C:notes.md", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_backslash_path_is_folded_not_escaped():
    b = _Backend(r'{"target": "\\\\server\\share\\notes.md", "action": "update"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "server/share/notes.md"


def test_rejects_a_sentence_shaped_target():
    # Real 2026-07-23 output: the router returned its own routing VERDICT as the path,
    # and loom created `drop — ephemeral run result, not durable.md` at the wiki root
    # (with unrelated content woven into it). No wiki slug has spaces, commas or dashes
    # like this — 363 articles checked, only the junk one matched.
    b = _Backend('{"target": "drop — ephemeral run result, not durable.md"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_rejects_sentence_shaped_directory_segment():
    b = _Backend('{"target": "decisions, maybe/liam.md"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == "people/liam.md"


def test_keeps_underscores_and_leading_underscore():
    # feedback_working_style.md and _index.md are real wiki filenames — do not reject them.
    b = _Backend('{"target": "patterns/feedback_working_style.md"}')
    assert confirm_route(b, _LEARNING, index_listing="")["target"] == \
        "patterns/feedback_working_style.md"


def test_logs_when_a_model_target_is_refused(caplog):
    b = _Backend('{"target": "../../escape.md", "action": "update"}')
    with caplog.at_level("WARNING"):
        confirm_route(b, _LEARNING, index_listing="")
    assert "../../escape.md" in caplog.text


def test_suggested_route_traversal_is_refused():
    b = _Backend("garbage")
    r = confirm_route(b, {"learning": "y", "route": "wiki/../../etc/passwd"}, index_listing="")
    assert r is None


# --- top-level directory guard --------------------------------------------
# `etc/passwd.md` used to happily mint ~/wiki/etc/ — the same silent-new-tree
# bug class as wiki/wiki/. With a wiki_root, a target whose top-level dir does
# not already exist is refused; root-level files and existing dirs still pass.
# (A prompt-derived allow-list was rejected: the live ledger routes to memory/,
# reference/, feedback/, procedures/ and 67 root-level files.)
import pytest
from loom.route import normalize_target


@pytest.fixture
def wiki(tmp_path):
    for d in ("people", "tools", "memory"):
        (tmp_path / d).mkdir()
    (tmp_path / "glossary.md").write_text("# glossary\n")
    return tmp_path


def test_refuses_target_in_nonexistent_toplevel_dir(wiki):
    assert normalize_target("etc/passwd.md", wiki) is None


def test_allows_target_in_existing_dir(wiki):
    assert normalize_target("people/liam.md", wiki) == "people/liam.md"


def test_allows_root_level_file(wiki):
    assert normalize_target("new-note.md", wiki) == "new-note.md"


def test_allows_new_subdir_under_existing_toplevel(wiki):
    # only the TOP level is guarded; deeper structure under a known dir is fine
    assert normalize_target("tools/sub/new.md", wiki) == "tools/sub/new.md"


def test_without_wiki_root_stays_a_pure_string_check():
    assert normalize_target("etc/passwd.md") == "etc/passwd.md"


def test_wiki_prefix_still_stripped_before_the_dir_check(wiki):
    assert normalize_target("wiki/people/liam.md", wiki) == "people/liam.md"


def test_confirm_route_refuses_new_toplevel_and_falls_back(wiki):
    b = _Backend('{"target": "etc/passwd.md", "action": "update"}')
    r = confirm_route(b, _LEARNING, index_listing="", wiki_root=wiki)
    assert r["target"] == "people/liam.md"          # fell back to the suggestion


def test_confirm_route_defers_when_suggestion_is_new_toplevel_too(wiki):
    b = _Backend("garbage")
    r = confirm_route(b, {"learning": "y", "route": "brandnew/x"},
                      index_listing="", wiki_root=wiki)
    assert r is None                                 # caller defers — no crash


def test_refused_new_toplevel_is_logged(wiki, caplog):
    b = _Backend('{"target": "etc/passwd.md", "action": "update"}')
    with caplog.at_level("WARNING"):
        confirm_route(b, {"learning": "y"}, index_listing="", wiki_root=wiki)
    assert "etc/passwd.md" in caplog.text
