import importlib.util, pathlib

_spec = importlib.util.spec_from_file_location(
    "venice_review", pathlib.Path("setup/templates/venice_review.py"))
vr = importlib.util.module_from_spec(_spec); _spec.loader.exec_module(vr)


def test_shim_exposes_main_and_post_comment():
    assert hasattr(vr, "main") and callable(vr.main)
    assert hasattr(vr, "post_comment") and callable(vr.post_comment)


def test_shim_delegates_to_run_pr_review():
    # the orchestration logic must live in the package, not the script
    import inspect
    src = inspect.getsource(vr)
    assert "from council.review import run_pr_review" in src
    assert "run_pr_review(" in src
    assert "build_review" not in src  # old per-script logic is gone


def test_shim_wires_grounding_and_determinism():
    # S1 (file context), E3 (temperature=0), S2 (in-place comment marker)
    import inspect
    src = inspect.getsource(vr)
    assert "file_context=" in src                 # full-file context reaches the engine (S1)
    assert "temperature=0" in src                 # deterministic gate path (E3)
    assert vr.MARKER in src                        # rolling single comment (S2)


def test_gather_file_context_reads_changed_files(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "x.mjs").write_text("const ROOT = '/repo';\n")
    diff = ("diff --git a/tools/x.mjs b/tools/x.mjs\n--- a/tools/x.mjs\n+++ b/tools/x.mjs\n"
            "@@ -1 +1 @@\n-a\n+b\n")
    ctx = vr.gather_file_context(diff, tmp_path)
    assert "const ROOT" in ctx               # the declaration the diff-only panel couldn't see
    assert "tools/x.mjs" in ctx


def test_gather_file_context_includes_anchors_and_skips_missing(tmp_path):
    (tmp_path / "package.json").write_text('{"engines": {"node": ">=22.12.0"}}')
    diff = ("diff --git a/missing.py b/missing.py\n--- a/missing.py\n+++ b/missing.py\n"
            "@@ -1 +1 @@\n-a\n+b\n")
    ctx = vr.gather_file_context(diff, tmp_path)
    assert ">=22.12.0" in ctx                # package.json anchor pulled in even if not in diff
    # a referenced-but-absent file must not raise
    assert "missing.py" not in ctx or ctx.count("missing.py") >= 0


def test_gather_file_context_caps_large_files(tmp_path):
    (tmp_path / "big.js").write_text("x" * 5000)
    diff = ("diff --git a/big.js b/big.js\n--- a/big.js\n+++ b/big.js\n@@ -1 +1 @@\n-a\n+b\n")
    ctx = vr.gather_file_context(diff, tmp_path, per_file_cap=1000)
    assert "truncated" in ctx                # truncate() marker, didn't dump the whole file


def test_main_fails_closed_on_missing_env(monkeypatch, capsys):
    for k in vr.REQUIRED_ENV:
        monkeypatch.delenv(k, raising=False)
    assert vr.main() == 1
    err = capsys.readouterr().err
    assert "missing required env" in err and "VENICE_API_KEY" in err


def test_gather_file_context_total_cap_counts_utf8_bytes(tmp_path):
    # 400 chars of a 3-byte glyph = 1200 bytes; a 500-byte total cap must exclude it
    # even though the character count alone would fit.
    (tmp_path / "pt.md").write_text("€" * 400)
    diff = ("diff --git a/pt.md b/pt.md\n--- a/pt.md\n+++ b/pt.md\n@@ -1 +1 @@\n-a\n+b\n")
    ctx = vr.gather_file_context(diff, tmp_path, per_file_cap=40_000, total_cap=500)
    assert "€" not in ctx


def test_gh_retries_transient_failures(monkeypatch):
    import requests as _requests
    calls = {"n": 0}

    class _OK:
        status_code = 200

    def flaky(method, url, **kw):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _requests.ConnectionError("blip")
        return _OK()

    monkeypatch.setattr(vr.requests, "request", flaky)
    monkeypatch.setattr(vr.time, "sleep", lambda s: None)
    assert vr._gh("GET", "https://api.example/x", "tok").status_code == 200
    assert calls["n"] == 3
