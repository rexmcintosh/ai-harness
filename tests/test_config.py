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


def test_member_json_mode_loads_from_toml_and_defaults_to_forced_json(tmp_path):
    from council.config import load_panels
    toml = tmp_path / "panels.toml"
    toml.write_text('''
[settings]
chair_model = "c"

[panels.p]
description = "d"

[[panels.p.members]]
name = "A"
model = "m1"
system = "s"

[[panels.p.members]]
name = "B"
model = "m2"
system = "s"
json_mode = false
''')
    _, panels = load_panels(str(toml))
    assert [m.json_mode for m in panels["p"].members] == [True, False]


def test_shipped_panels_never_force_json_on_deepseek_v4_pro():
    # Root cause of the empty Security Officer seat: Venice's json_object mode garbles this
    # model's first key (0 of 4 usable with it on, 4 of 4 clean with it off, 2026-09-19).
    from council.config import load_panels
    _, panels = load_panels(None)
    seats = [(p.name, m.name, m.json_mode) for p in panels.values() for m in p.members
             if m.model.startswith("deepseek-v4-pro")]
    assert seats and all(mode is False for _, _, mode in seats), seats

