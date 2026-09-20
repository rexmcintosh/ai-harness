from council import cli
from council.models import Panel, Member
from council.config import Settings
from tests.conftest import FakeClient


def _env(member_json):
    settings = Settings(default_panel="decision", router_model="r", chair_model="c")
    panels = {"decision": Panel("decision", "weigh", [Member("Founder", "m1", "founder")]),
              "code-review": Panel("code-review", "review", [Member("Eng", "m1", "eng")])}
    client = FakeClient(
        by_model={"m1": member_json(stance="approve", headline="go"),
                  "r": {"panel": "decision"},
                  "c": {"recommendation": "do X", "confidence": 8, "consensus": [],
                        "disagreements": [], "cross_panel_themes": []}})
    return settings, panels, client


def test_ask_runs_and_prints_recommendation(capsys, member_json):
    settings, panels, client = _env(member_json)
    rc = cli.main(["ask", "ship X?"], _settings=settings, _panels=panels, _client=client)
    assert rc == 0
    assert "do X" in capsys.readouterr().out


def test_ask_forwards_task_type_ask_for_usage_logging(capsys, member_json):
    # council/cli.py's ask path must thread task_type="ask" all the way down to
    # every complete() call (router + panel + chair) for accurate usage attribution.
    settings, panels, client = _env(member_json)
    cli.main(["ask", "ship X?"], _settings=settings, _panels=panels, _client=client)
    assert client.calls  # sanity: calls actually happened
    assert all(c["task_type"] == "ask" for c in client.calls)


def test_review_cmd_leaves_task_type_at_default(capsys, member_json, tmp_path):
    # council/cli.py's single-file/dir/diff `review` subcommand is distinct from
    # council/review.py's PR-review path — only the latter was asked to log "review".
    settings, panels, client = _env(member_json)
    f = tmp_path / "app.py"
    f.write_text("print('hi')\n")
    cli.main(["review", str(f), "--panel", "code-review"],
             _settings=settings, _panels=panels, _client=client)
    assert client.calls
    assert all(c["task_type"] == "chat" for c in client.calls)


def test_panels_lists_names(capsys, member_json):
    settings, panels, client = _env(member_json)
    rc = cli.main(["panels"], _settings=settings, _panels=panels, _client=client)
    assert rc == 0
    out = capsys.readouterr().out
    assert "decision" in out and "code-review" in out


def test_ask_explicit_panel_overrides_router(capsys, member_json):
    settings, panels, client = _env(member_json)
    cli.main(["ask", "x", "--panel", "code-review"], _settings=settings,
             _panels=panels, _client=client)
    # the router model "r" should never have been called
    assert all(c["model"] != "r" for c in client.calls)


def test_ask_unknown_panel_errors_friendly(capsys, member_json):
    settings, panels, client = _env(member_json)
    import pytest
    with pytest.raises(SystemExit) as exc:
        cli.main(["ask", "x", "--panel", "nope"], _settings=settings,
                 _panels=panels, _client=client)
    assert exc.value.code == 2
    assert "unknown panel" in capsys.readouterr().err


def test_panels_does_not_require_api_key(capsys, monkeypatch, tmp_path):
    # Listing panels is a local-only operation: it must work with no key set,
    # via the production path (no injected client) — the client is built lazily.
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    f = tmp_path / "panels.toml"
    f.write_text('[settings]\ndefault_panel = "decision"\n\n'
                 '[panels.decision]\ndescription = "weigh"\n'
                 '[[panels.decision.members]]\nname = "Founder"\nmodel = "m1"\nsystem = "s"\n')
    rc = cli.main(["panels", "--panels", str(f)])
    assert rc == 0
    assert "decision" in capsys.readouterr().out


def test_read_for_review_skips_dotfiles_and_binary(tmp_path):
    (tmp_path / "keep.py").write_text("print('hi')\n")
    (tmp_path / ".env").write_text("VENICE_API_KEY=secret-should-never-be-sent\n")
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\x00\x00binary\x00data")
    text = cli._read_for_review(str(tmp_path), cap=200_000)
    assert "keep.py" in text and "print('hi')" in text
    assert "secret-should-never-be-sent" not in text  # dotfile excluded
    assert "logo.png" not in text                      # binary excluded


def test_read_for_review_enforces_byte_budget(tmp_path):
    for i in range(50):
        (tmp_path / f"f{i}.txt").write_text("x" * 1000)
    text = cli._read_for_review(str(tmp_path), cap=5000)
    assert "stopped collecting" in text
    assert len(text.encode()) < 5000 * 3  # bounded during collection, not unbounded


def test_review_diff_surfaces_git_failure(capsys, member_json, monkeypatch):
    settings, panels, client = _env(member_json)
    import subprocess, pytest

    class _Proc:
        returncode = 128
        stdout = ""
        stderr = "fatal: not a git repository"

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: _Proc())
    rc = cli.main(["review", "--diff"], _settings=settings, _panels=panels, _client=client)
    assert rc == 2
    assert "git diff` failed" in capsys.readouterr().err


def test_read_for_review_skips_symlinks(tmp_path):
    import os
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "real.py").write_text("print('ok')\n")
    secret = tmp_path / "secret.txt"           # OUTSIDE the reviewed tree
    secret.write_text("exfiltrate me")
    os.symlink(secret, repo / "link.txt")      # only reachable by following the symlink
    text = cli._read_for_review(str(repo), cap=200_000)
    assert "real.py" in text
    assert "exfiltrate me" not in text  # symlink not followed


def test_ask_missing_file_errors_friendly(capsys, member_json):
    settings, panels, client = _env(member_json)
    import pytest
    with pytest.raises(SystemExit) as exc:
        cli.main(["ask", "review this", "--file", "/no/such/file.txt"],
                 _settings=settings, _panels=panels, _client=client)
    assert exc.value.code == 2
    assert "cannot read --file" in capsys.readouterr().err


def test_review_explicit_missing_path_exits_2(capsys, member_json, tmp_path):
    settings, panels, client = _env(member_json)
    empty = tmp_path / "empty_dir"
    empty.mkdir()
    rc = cli.main(["review", str(empty)], _settings=settings, _panels=panels, _client=client)
    assert rc == 2
    assert "nothing readable to review" in capsys.readouterr().err


def _env_with_spec(member_json):
    from council.config import Settings
    settings = Settings(default_panel="decision", router_model="r", chair_model="c")
    panels = {
        "code-review": Panel("code-review", "review code", [Member("Eng", "code1", "eng")]),
        "spec-review": Panel("spec-review", "review docs", [Member("Editor", "doc1", "editor")]),
    }
    client = FakeClient(by_model={
        "code1": member_json(stance="approve", headline="ok"),
        "doc1": member_json(stance="approve", headline="ok"),
        "c": {"recommendation": "x", "confidence": 8, "consensus": [],
              "disagreements": [], "cross_panel_themes": []}})
    return settings, panels, client


def test_review_doc_file_autopicks_spec_review(tmp_path, member_json):
    settings, panels, client = _env_with_spec(member_json)
    f = tmp_path / "design.md"
    f.write_text("# Design\nsome spec prose\n")
    cli.main(["review", str(f)], _settings=settings, _panels=panels, _client=client)
    models = {c["model"] for c in client.calls}
    assert "doc1" in models       # spec-review seat was consulted
    assert "code1" not in models  # code-review seat was not


def test_review_code_file_autopicks_code_review(tmp_path, member_json):
    settings, panels, client = _env_with_spec(member_json)
    f = tmp_path / "app.py"
    f.write_text("print('hi')\n")
    cli.main(["review", str(f)], _settings=settings, _panels=panels, _client=client)
    models = {c["model"] for c in client.calls}
    assert "code1" in models and "doc1" not in models


# ── per-panel chair: a panel that names a chair gets it, every other panel the global one ──
def _env_with_panel_chair(member_json):
    settings, panels, client = _env_with_spec(member_json)
    panels["code-review"].chair_model = "pc"           # only code-review names its own chair
    client.by_model["pc"] = client.by_model["c"]
    return settings, panels, client


def _models(client):
    return [c["model"] for c in client.calls]


def test_review_on_code_review_calls_that_panels_chair(tmp_path, member_json):
    settings, panels, client = _env_with_panel_chair(member_json)
    f = tmp_path / "app.py"
    f.write_text("print('hi')\n")
    cli.main(["review", str(f)], _settings=settings, _panels=panels, _client=client)
    assert _models(client) == ["code1", "pc"]           # the panel's chair, never the global one


def test_review_on_another_panel_calls_the_global_chair(tmp_path, member_json):
    settings, panels, client = _env_with_panel_chair(member_json)
    f = tmp_path / "design.md"
    f.write_text("# Design\nsome spec prose\n")
    cli.main(["review", str(f)], _settings=settings, _panels=panels, _client=client)
    assert _models(client) == ["doc1", "c"]             # spec-review names no chair


def test_ask_uses_the_chair_of_the_panel_it_runs(member_json):
    settings, panels, client = _env_with_panel_chair(member_json)
    cli.main(["ask", "x", "--panel", "code-review"], _settings=settings, _panels=panels, _client=client)
    cli.main(["ask", "x", "--panel", "spec-review"], _settings=settings, _panels=panels, _client=client)
    assert _models(client) == ["code1", "pc", "doc1", "c"]


def test_review_explicit_panel_overrides_autopick(tmp_path, member_json):
    settings, panels, client = _env_with_spec(member_json)
    f = tmp_path / "design.md"
    f.write_text("# Design\n")
    cli.main(["review", str(f), "--panel", "code-review"],
             _settings=settings, _panels=panels, _client=client)
    models = {c["model"] for c in client.calls}
    assert "code1" in models and "doc1" not in models
