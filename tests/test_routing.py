from council.routing import classify_path, split_diff_by_type, changed_paths


def test_changed_paths_lists_each_files_new_side_path():
    diff = ("diff --git a/tools/i18n/translate.mjs b/tools/i18n/translate.mjs\n"
            "--- a/tools/i18n/translate.mjs\n+++ b/tools/i18n/translate.mjs\n@@ -1 +1 @@\n-a\n+b\n"
            "diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n")
    assert changed_paths(diff) == ["tools/i18n/translate.mjs", "src/app.py"]


def test_changed_paths_empty_for_empty_diff():
    assert changed_paths("") == []


def test_classify_by_extension():
    assert classify_path("README.md") == "doc"
    assert classify_path("notes/today.rst") == "doc"
    assert classify_path("a/b/thing.txt") == "doc"
    assert classify_path("design.adoc") == "doc"
    assert classify_path("src/app.py") == "code"
    assert classify_path("lib/util.js") == "code"


def test_classify_by_directory_segment():
    assert classify_path("docs/architecture.png") == "doc"   # under docs/
    assert classify_path("specs/api/example.py") == "doc"     # under specs/
    assert classify_path("plans/q3.json") == "doc"
    assert classify_path("plan/rollout.yaml") == "doc"
    assert classify_path("src/docs_helper.py") == "code"      # 'docs' only as a substring, not a segment


def test_classify_empty_is_code():
    assert classify_path("") == "code"
    assert classify_path("   ") == "code"


CODE_FILE = (
    "diff --git a/src/app.py b/src/app.py\n"
    "index 111..222 100644\n--- a/src/app.py\n+++ b/src/app.py\n"
    "@@ -1 +1 @@\n-old\n+new\n"
)
DOC_FILE = (
    "diff --git a/docs/design.md b/docs/design.md\n"
    "index 333..444 100644\n--- a/docs/design.md\n+++ b/docs/design.md\n"
    "@@ -1 +1 @@\n-old doc\n+new doc\n"
)


def test_split_mixed_diff():
    code_diff, doc_diff = split_diff_by_type(CODE_FILE + DOC_FILE)
    assert "src/app.py" in code_diff and "docs/design.md" not in code_diff
    assert "docs/design.md" in doc_diff and "src/app.py" not in doc_diff


def test_split_code_only():
    code_diff, doc_diff = split_diff_by_type(CODE_FILE)
    assert "src/app.py" in code_diff
    assert doc_diff == ""


def test_split_doc_only():
    code_diff, doc_diff = split_diff_by_type(DOC_FILE)
    assert code_diff == ""
    assert "docs/design.md" in doc_diff


def test_split_empty_or_garbage():
    assert split_diff_by_type("") == ("", "")
    assert split_diff_by_type("not a diff at all") == ("", "")


SPACE_DOC = (
    'diff --git a/my docs/Design Notes.md b/my docs/Design Notes.md\n'
    '--- a/my docs/Design Notes.md\n+++ b/my docs/Design Notes.md\n'
    '@@ -1 +1 @@\n-x\n+y\n'
)
QUOTED_DOC = (
    'diff --git "a/d\303\251sign.md" "b/d\303\251sign.md"\n'
    '--- "a/d\303\251sign.md"\n+++ "b/d\303\251sign.md"\n'
    '@@ -1 +1 @@\n-x\n+y\n'
)
DELETED_DOC = (
    'diff --git a/docs/old.md b/docs/old.md\n'
    'deleted file mode 100644\n--- a/docs/old.md\n+++ /dev/null\n'
    '@@ -1 +0,0 @@\n-gone\n'
)
NEW_CODE = (
    'diff --git a/src/new.py b/src/new.py\n'
    'new file mode 100644\n--- /dev/null\n+++ b/src/new.py\n'
    '@@ -0,0 +1 @@\n+print()\n'
)


def test_split_path_with_spaces():
    code_diff, doc_diff = split_diff_by_type(SPACE_DOC)
    assert code_diff == ""
    assert "Design Notes.md" in doc_diff   # space-bearing doc routed correctly


def test_split_quoted_path():
    code_diff, doc_diff = split_diff_by_type(QUOTED_DOC)
    assert code_diff == ""
    assert doc_diff != ""                  # git-quoted .md routed to docs


def test_split_deletion_uses_old_path():
    code_diff, doc_diff = split_diff_by_type(DELETED_DOC)
    assert code_diff == ""
    assert "docs/old.md" in doc_diff       # +++ is /dev/null, falls back to --- path


def test_split_new_file_uses_new_path():
    code_diff, doc_diff = split_diff_by_type(NEW_CODE)
    assert "src/new.py" in code_diff
    assert doc_diff == ""
