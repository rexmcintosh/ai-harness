# tests/loom/test_indexer.py
from pathlib import Path
from loom.indexer import rebuild_backlinks, upsert_index_entry, SECTION_FOR, clean_summary
import json
import stat
import loom.indexer as indexer


def _article(root, rel, body):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def test_rebuild_backlinks_reverse_maps_wikilinks(tmp_path):
    _article(tmp_path, "people/liam.md", "# Liam\nSon of [[rex-mcintosh]]; see [[portugal]].\n")
    _article(tmp_path, "people/rex-mcintosh.md", "# Rex\nFather of [[liam]].\n")
    rebuild_backlinks(tmp_path)
    data = json.loads((tmp_path / "_backlinks.json").read_text())
    assert data["rex-mcintosh"] == ["liam"]
    assert sorted(data["liam"]) == ["rex-mcintosh"]
    assert data["portugal"] == ["liam"]


def test_section_for_known_dirs():
    assert SECTION_FOR["people"] == "People"
    assert SECTION_FOR["decisions"] == "Decisions"


def test_upsert_index_entry_adds_under_section(tmp_path):
    (tmp_path / "_index.md").write_text(
        "---\ntitle: \"_index\"\ntotal_pages: 1\n---\n\n# RexBrain — Master Index\n\n## People\n- [[rex-mcintosh]] — Rex.\n"
    )
    upsert_index_entry(tmp_path, "liam", "people", "Rex's son; competitive swimmer.", today="2026-06-08")
    txt = (tmp_path / "_index.md").read_text()
    assert "- [[liam]] — Rex's son; competitive swimmer." in txt
    assert txt.index("## People") < txt.index("[[liam]]")


def test_upsert_index_entry_is_idempotent(tmp_path):
    (tmp_path / "_index.md").write_text("# RexBrain — Master Index\n\n## People\n- [[rex-mcintosh]] — Rex.\n")
    for _ in range(2):
        upsert_index_entry(tmp_path, "liam", "people", "Son.", today="2026-06-08")
    assert (tmp_path / "_index.md").read_text().count("[[liam]]") == 1


def test_upsert_index_entry_updates_existing_summary_in_place(tmp_path):
    original = (
        "---\ntotal_pages: 2\nlast_updated: 2026-08-24\n---\n\n"
        "# RexBrain — Master Index\n\n"
        "> Last updated: 2026-08-24 · Total pages: 2\n\n"
        "## People\n"
        "- [[sachiko-uchida]] — A colleague.\n"
        "- [[gale-mcintosh]] — Mac's mother.\n"
        "- [[rex-mcintosh]] — Gale's son.\n"
    )
    (tmp_path / "_index.md").write_text(original)

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    expected = (
        original.replace(
            "- [[gale-mcintosh]] — Mac's mother.",
            "- [[gale-mcintosh]] — Rex's mother.",
        )
        .replace("last_updated: 2026-08-24", "last_updated: 2026-08-25")
        .replace("Last updated: 2026-08-24", "Last updated: 2026-08-25")
    )
    txt = (tmp_path / "_index.md").read_text()
    assert txt == expected
    assert txt.count("[[gale-mcintosh]]") == 1


def test_upsert_index_entry_normalizes_legacy_lines(tmp_path):
    legacy_lines = [
        "*   [[gale-mcintosh]] - Mac's mother.",
        "- [[gale-mcintosh]]",
    ]
    for position, legacy_line in enumerate(legacy_lines):
        root = tmp_path / str(position)
        root.mkdir()
        (root / "_index.md").write_text(
            f"# RexBrain — Master Index\n\n## People\n{legacy_line}\n"
        )

        upsert_index_entry(
            root, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
        )

        txt = (root / "_index.md").read_text()
        assert txt == (
            "# RexBrain — Master Index\n\n## People\n"
            "- [[gale-mcintosh]] — Rex's mother.\n"
        )
        assert txt.count("[[gale-mcintosh]]") == 1


def test_upsert_index_entry_removes_later_duplicates_without_bumping_total(tmp_path):
    original = (
        "---\ntotal_pages: 2\n---\n\n"
        "# RexBrain — Master Index\n\n"
        "> Total pages: 2\n\n"
        "## People\n"
        "* [[gale-mcintosh]] - First stale summary.\n"
        "- [[gale-mcintosh]] — Second stale summary.\n"
    )
    (tmp_path / "_index.md").write_text(original)

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    txt = (tmp_path / "_index.md").read_text()
    assert txt == (
        "---\ntotal_pages: 2\n---\n\n"
        "# RexBrain — Master Index\n\n"
        "> Total pages: 2\n\n"
        "## People\n"
        "- [[gale-mcintosh]] — Rex's mother.\n"
    )
    assert txt.count("[[gale-mcintosh]]") == 1
    assert "total_pages: 2" in txt
    assert "Total pages: 2" in txt


def test_upsert_index_entry_does_not_replace_prose_mention(tmp_path):
    prose = "- This prose mentions [[gale-mcintosh]] but is not an index entry."
    (tmp_path / "_index.md").write_text(
        f"# RexBrain — Master Index\n\n## People\n{prose}\n"
        "- [[gale-mcintosh]] — Old summary.\n"
    )

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    txt = (tmp_path / "_index.md").read_text()
    assert prose in txt
    assert "- [[gale-mcintosh]] — Rex's mother." in txt


def test_upsert_index_entry_leaves_same_slug_in_other_section_untouched(tmp_path):
    other_entry = "- [[gale-mcintosh]] — A project with the same slug."
    (tmp_path / "_index.md").write_text(
        "# RexBrain — Master Index\n\n"
        f"## Projects\n{other_entry}\n\n"
        "## People\n- [[gale-mcintosh]] — Old person summary.\n"
    )

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    txt = (tmp_path / "_index.md").read_text()
    assert other_entry in txt
    assert "- [[gale-mcintosh]] — Rex's mother." in txt
    assert txt.count("[[gale-mcintosh]]") == 2


def test_upsert_index_entry_deduplicates_only_within_target_section(tmp_path):
    other_entry = "- [[gale-mcintosh]] — Unrelated project entry."
    (tmp_path / "_index.md").write_text(
        "# RexBrain — Master Index\n\n"
        f"## Projects\n{other_entry}\n\n"
        "## People\n"
        "* [[gale-mcintosh]] - First stale summary.\n"
        "- [[gale-mcintosh]] — Second stale summary.\n"
    )

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    txt = (tmp_path / "_index.md").read_text()
    assert other_entry in txt
    assert txt.count("- [[gale-mcintosh]] — Rex's mother.") == 1
    assert txt.count("[[gale-mcintosh]]") == 2


def test_upsert_index_entry_does_not_match_longer_slug(tmp_path):
    longer_entry = "- [[gale-mcintosh-2]] — A different person."
    (tmp_path / "_index.md").write_text(
        f"# RexBrain — Master Index\n\n## People\n{longer_entry}\n"
    )

    upsert_index_entry(
        tmp_path, "gale-mcintosh", "people", "Rex's mother.", today="2026-08-25"
    )

    txt = (tmp_path / "_index.md").read_text()
    assert longer_entry in txt
    assert "- [[gale-mcintosh]] — Rex's mother." in txt
    assert txt.count("[[gale-mcintosh]]") == 1


def test_upsert_index_entry_writes_atomically_without_leaving_temp_file(
    tmp_path, monkeypatch
):
    (tmp_path / "_index.md").write_text(
        "# RexBrain — Master Index\n\n## People\n- [[liam]] — Old summary.\n"
    )
    real_replace = indexer.os.replace
    replacements = []

    def record_replace(source, destination):
        replacements.append((Path(source), Path(destination)))
        real_replace(source, destination)

    monkeypatch.setattr(indexer.os, "replace", record_replace)

    upsert_index_entry(
        tmp_path, "liam", "people", "New summary.", today="2026-08-25"
    )

    assert len(replacements) == 1
    temp_path, destination = replacements[0]
    assert temp_path.parent == tmp_path
    assert destination == tmp_path / "_index.md"
    assert not temp_path.exists()
    assert {path.name for path in tmp_path.iterdir()} == {"_index.md"}


def test_upsert_index_entry_preserves_existing_file_mode(tmp_path):
    index_path = tmp_path / "_index.md"
    index_path.write_text(
        "# RexBrain — Master Index\n\n## People\n- [[liam]] — Old summary.\n"
    )
    index_path.chmod(0o640)

    upsert_index_entry(
        tmp_path, "liam", "people", "New summary.", today="2026-08-25"
    )

    assert stat.S_IMODE(index_path.stat().st_mode) == 0o640


def test_clean_summary_truncates_at_word_boundary():
    short = "A concise summary."
    assert clean_summary(short) == short                       # under limit, unchanged
    long = ("The only Hermes reference under projects is the hermes-parser package inside "
            "finance-tracker node_modules and it is a transitive dependency")
    out = clean_summary(long)
    assert out.endswith("…") and len(out) <= 111
    assert not out[:-1].endswith(" ")                          # no trailing space before ellipsis
    assert out[:-1] in long or long.startswith(out[:-1])       # it's a clean prefix, not mid-word garbage
    # collapses internal whitespace, newlines, and carriage returns
    assert clean_summary("a\n\n  b\r\nc\rd") == "a b c d"


def test_upsert_increments_total_pages(tmp_path):
    (tmp_path / "_index.md").write_text(
        "---\ntotal_pages: 26\n---\n\n# RexBrain — Master Index\n\n> ... Total pages: 26\n\n## People\n"
    )
    upsert_index_entry(tmp_path, "liam", "people", "Rex's son.", today="2026-06-09")
    txt = (tmp_path / "_index.md").read_text()
    assert "total_pages: 27" in txt          # frontmatter bumped
    assert "Total pages: 27" in txt          # intro line bumped
