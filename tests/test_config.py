import os
import textwrap
import pytest
from council.config import load_panels, get_api_key, Settings, truncate


@pytest.fixture(autouse=True, scope="module")
def _clear_ambient_council_key():
    """VENICE_COUNCIL_KEY is a project-specific key that ~/.env now (or soon
    will) export into every shell on this machine. The older tests below
    predate that var and only set/clear VENICE_API_KEY, so they assume no
    other key var is present. Strip any ambient VENICE_COUNCIL_KEY for the
    duration of this module so that assumption holds regardless of the host
    environment; tests further down that exercise VENICE_COUNCIL_KEY set it
    themselves via monkeypatch, which restores per-test as usual."""
    old_value = os.environ.pop("VENICE_COUNCIL_KEY", None)
    yield
    if old_value is not None:
        os.environ["VENICE_COUNCIL_KEY"] = old_value


TOML = textwrap.dedent("""
[settings]
default_panel = "decision"
router_model = "rmodel"
chair_model = "cmodel"
byte_cap = 50

[panels.decision]
description = "weigh a choice"
default_rigor = "daily"
[[panels.decision.members]]
name = "Founder"
model = "m1"
system = "be a founder"
""")


def test_load_panels(tmp_path):
    f = tmp_path / "panels.toml"
    f.write_text(TOML)
    settings, panels = load_panels(f)
    assert isinstance(settings, Settings)
    assert settings.default_panel == "decision"
    assert panels["decision"].members[0].name == "Founder"
    assert panels["decision"].default_rigor == "daily"


def test_get_api_key_reads_env(monkeypatch):
    monkeypatch.setenv("VENICE_API_KEY", "abc")
    assert get_api_key() == "abc"


def test_get_api_key_missing(monkeypatch):
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        get_api_key()
    assert exc.value.code == 2


def test_truncate_keeps_head_and_tail():
    out = truncate("x" * 100, cap=20)
    assert "truncated" in out
    assert len(out.encode()) < 100


def test_real_panels_include_spec_review():
    # loads the SHIPPED council/panels.toml (no path arg)
    settings, panels = load_panels()
    assert "spec-review" in panels
    p = panels["spec-review"]
    assert [m.name for m in p.members] == [
        "Editor", "Domain Skeptic", "Implementer", "Pre-mortem Adversary"]
    assert all(m.model and m.system for m in p.members)  # no empty models/personas


def test_get_api_key_prefers_the_council_key(monkeypatch):
    monkeypatch.setenv("VENICE_COUNCIL_KEY", "council-key")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "council-key"


def test_get_api_key_falls_back_to_the_shared_key(monkeypatch):
    monkeypatch.delenv("VENICE_COUNCIL_KEY", raising=False)
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "default-key"


def test_get_api_key_treats_blank_as_unset(monkeypatch):
    # A set-but-empty var must fall through, not be returned as a valid key.
    monkeypatch.setenv("VENICE_COUNCIL_KEY", "")
    monkeypatch.setenv("VENICE_API_KEY", "default-key")
    assert get_api_key() == "default-key"


def test_get_api_key_exits_when_neither_is_set(monkeypatch, capsys):
    monkeypatch.delenv("VENICE_COUNCIL_KEY", raising=False)
    monkeypatch.delenv("VENICE_API_KEY", raising=False)
    with pytest.raises(SystemExit) as exc:
        get_api_key()
    assert exc.value.code == 2
    # The hint must point the operator at the project key first, not just the
    # generic fallback, and must never echo a key value.
    err = capsys.readouterr().err
    assert "VENICE_COUNCIL_KEY" in err


# ── per-panel chair (docs/council-chair-decision-2026-09-20.md) ────────────────────────────
# A panel may name its own chair. A panel that names none uses [settings] chair_model.
PANEL_CHAIR_TOML = TOML + textwrap.dedent("""
[panels.code-review]
description = "review code"
{chair_line}
[[panels.code-review.members]]
name = "Eng"
model = "m2"
system = "be an eng"
""")


def _load(tmp_path, chair_line):
    f = tmp_path / "panels.toml"
    f.write_text(PANEL_CHAIR_TOML.format(chair_line=chair_line))
    return load_panels(f)


def test_a_panel_can_name_its_own_chair(tmp_path):
    from council.config import chair_for
    settings, panels = _load(tmp_path, 'chair_model = "pchair"')
    assert panels["code-review"].chair_model == "pchair"
    assert chair_for(settings, panels["code-review"]) == "pchair"
    assert settings.chair_model == "cmodel"                  # the global chair is untouched


def test_a_panel_without_its_own_chair_uses_the_global_chair(tmp_path):
    from council.config import chair_for
    settings, panels = _load(tmp_path, "")
    assert panels["code-review"].chair_model is None
    assert panels["decision"].chair_model is None
    assert chair_for(settings, panels["code-review"]) == "cmodel"
    assert chair_for(settings, panels["decision"]) == "cmodel"


@pytest.mark.parametrize("chair_line", ['chair_model = ""', 'chair_model = "   "', "chair_model = 42",
                                        "chair_model = true", 'chair_model = ["pchair"]'])
def test_an_empty_or_non_string_panel_chair_is_no_override(tmp_path, chair_line):
    # An empty model name would reach Venice as model "": treat it as "not set" instead.
    from council.config import chair_for
    settings, panels = _load(tmp_path, chair_line)
    assert panels["code-review"].chair_model is None
    assert chair_for(settings, panels["code-review"]) == "cmodel"


def test_shipped_panels_swap_the_chair_on_code_review_only():
    # Owner decision 2026-09-20: the bake-off tested the code-review panel only, so only
    # that panel gets the new chair. The path is explicit so a user override file in
    # ~/.config/council/ cannot change what this test reads.
    from importlib.resources import files
    from council.config import chair_for
    settings, panels = load_panels(str(files("council") / "panels.toml"))
    assert settings.chair_model == "claude-opus-4-8"
    assert chair_for(settings, panels["code-review"]) == "openai-gpt-56-sol"
    others = {name: chair_for(settings, p) for name, p in panels.items() if name != "code-review"}
    assert set(others) == {"decision", "brainstorm", "red-team", "spec-review"}
    assert set(others.values()) == {"claude-opus-4-8"}
    assert all(p.chair_model is None for name, p in panels.items() if name != "code-review")
