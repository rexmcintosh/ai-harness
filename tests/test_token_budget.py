"""Output caps: `max_completion_tokens` on every Venice call.

Before this, nothing in council/ or diem/ set any completion bound, so a
one-line `council ask` and a 200 KB code review both bought one unbounded
synthesis. The cap is a blast-radius guard, not an average-cost cut — it bounds
the tail, and the defaults are sized so that no call in 54 days of ledger
history would have been truncated.

Field name: `max_completion_tokens`. Venice's own OpenAPI spec (version
20260911.122036, fetched the day this landed) documents `max_tokens` as "now
deprecated in favor of max_completion_tokens", and describes the replacement as
"an upper bound ... including visible output tokens and reasoning tokens".
Every current council seat is a reasoning model, so the reasoning half is the
half that matters.
"""
from __future__ import annotations
import textwrap

import pytest

from council.config import Settings, load_panels, resolve_budget
from council.venice import VeniceClient


def _resp(content='{"ok": true}', status=200):
    class R:
        status_code = status
        def json(self): return {"choices": [{"message": {"content": content}}]}
        def raise_for_status(self): pass
    return R()


def _capture():
    sent = {}
    def post(url, **kw):
        sent.update(kw["json"])
        return _resp()
    return sent, post


# ---------- the client sends it -------------------------------------------

def test_complete_sends_max_completion_tokens_when_configured():
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=8000).complete("m", "s", "u")
    assert sent["max_completion_tokens"] == 8000


def test_complete_uses_the_current_field_not_the_deprecated_one():
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=8000).complete("m", "s", "u")
    assert "max_tokens" not in sent


def test_complete_omits_the_cap_when_none_is_configured():
    sent, post = _capture()
    VeniceClient("k", post=post).complete("m", "s", "u")
    assert "max_completion_tokens" not in sent


@pytest.mark.parametrize("value", [0, -1])
def test_zero_or_negative_disables_the_cap(value):
    """The operator's kill switch, and it matches Venice's own semantics:
    'values of 0 or less are ignored'. Setting 0 in panels.toml must send
    nothing at all rather than a nonsense bound."""
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=value).complete("m", "s", "u")
    assert "max_completion_tokens" not in sent


def test_a_per_call_cap_overrides_the_client_default():
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=8000).complete(
        "m", "s", "u", max_completion_tokens=24000)
    assert sent["max_completion_tokens"] == 24000


def test_a_per_call_zero_overrides_a_client_default():
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=8000).complete(
        "m", "s", "u", max_completion_tokens=0)
    assert "max_completion_tokens" not in sent


def test_the_cap_does_not_disturb_the_rest_of_the_payload():
    sent, post = _capture()
    VeniceClient("k", post=post, max_completion_tokens=100, temperature=0).complete(
        "m", "sys", "usr")
    assert sent["model"] == "m" and sent["temperature"] == 0
    assert sent["response_format"] == {"type": "json_object"}
    assert [x["role"] for x in sent["messages"]] == ["system", "user"]


# ---------- resolving a budget from config --------------------------------

BASE = textwrap.dedent("""
[settings]
default_panel = "decision"
router_model = "rmodel"
chair_model = "cmodel"
max_completion_tokens = 1000
chair_max_completion_tokens = 2000
router_max_completion_tokens = 300

[settings.rigor.deep]
max_completion_tokens = 1500
chair_max_completion_tokens = 2500

[panels.decision]
description = "weigh a choice"
default_rigor = "daily"
[[panels.decision.members]]
name = "Founder"
model = "m1"
system = "s"
[[panels.decision.members]]
name = "Eng"
model = "m2"
system = "s"

[panels.red-team]
description = "break it"
default_rigor = "deep"
max_completion_tokens = 9000
chair_max_completion_tokens = 9500
[panels.red-team.rigor.deep]
max_completion_tokens = 11000
[[panels.red-team.members]]
name = "Adversary"
model = "m3"
system = "s"
[[panels.red-team.members]]
name = "Quiet Seat"
model = "m4"
system = "s"
max_completion_tokens = 400
""")


def _load(tmp_path, toml=BASE):
    f = tmp_path / "panels.toml"
    f.write_text(toml)
    return load_panels(f)


def test_settings_carry_the_three_default_caps(tmp_path):
    settings, _ = _load(tmp_path)
    assert settings.max_completion_tokens == 1000
    assert settings.chair_max_completion_tokens == 2000
    assert settings.router_max_completion_tokens == 300


def test_budget_falls_back_to_the_settings_defaults(tmp_path):
    settings, panels = _load(tmp_path)
    b = resolve_budget(settings, panels["decision"], "daily")
    assert b.member("Founder") == 1000
    assert b.chair == 2000 and b.router == 300


def test_rigor_overrides_the_settings_default(tmp_path):
    settings, panels = _load(tmp_path)
    b = resolve_budget(settings, panels["decision"], "deep")
    assert b.member("Founder") == 1500 and b.chair == 2500
    assert b.router == 300          # no rigor override for the router -> base


def test_a_panel_override_beats_the_settings_default(tmp_path):
    settings, panels = _load(tmp_path)
    b = resolve_budget(settings, panels["red-team"], "daily")
    assert b.member("Adversary") == 9000 and b.chair == 9500


def test_a_panel_rigor_override_beats_the_panel_default(tmp_path):
    settings, panels = _load(tmp_path)
    b = resolve_budget(settings, panels["red-team"], "deep")
    assert b.member("Adversary") == 11000
    assert b.chair == 9500          # panel chair value; no panel+rigor chair override


def test_a_seat_override_beats_everything(tmp_path):
    settings, panels = _load(tmp_path)
    for rigor in ("daily", "deep"):
        assert resolve_budget(settings, panels["red-team"], rigor).member("Quiet Seat") == 400


def test_an_unknown_rigor_falls_back_to_the_base_values(tmp_path):
    settings, panels = _load(tmp_path)
    b = resolve_budget(settings, panels["decision"], "shallow")
    assert b.member("Founder") == 1000 and b.chair == 2000


def test_an_unknown_seat_name_gets_the_panel_value(tmp_path):
    settings, panels = _load(tmp_path)
    assert resolve_budget(settings, panels["red-team"], "daily").member("Nobody") == 9000


def test_zero_in_config_survives_resolution_as_zero(tmp_path):
    """0 means 'no cap' and must not be mistaken for 'unset' by the fallback
    chain — otherwise the kill switch silently re-enables the parent's cap."""
    toml = BASE + textwrap.dedent("""
    [panels.decision.rigor.daily]
    max_completion_tokens = 0
    """)
    settings, panels = _load(tmp_path, toml)
    assert resolve_budget(settings, panels["decision"], "daily").member("Founder") == 0


def test_a_config_with_no_cap_keys_at_all_still_loads(tmp_path):
    """Back-compat: ~/.config/council/panels.toml may predate this feature."""
    toml = textwrap.dedent("""
    [settings]
    default_panel = "decision"
    [panels.decision]
    description = "d"
    [[panels.decision.members]]
    name = "A"
    model = "m"
    system = "s"
    """)
    settings, panels = _load(tmp_path, toml)
    b = resolve_budget(settings, panels["decision"], "daily")
    assert b.member("A") == Settings.max_completion_tokens
    assert b.chair == Settings.chair_max_completion_tokens


# ---------- the shipped defaults are the ones the evidence supports -------

# Highest completion_tokens ever recorded per role in
# ~/.local/state/venice-usage/ledger.db, project IN ('council','backlog-runner'),
# 1,732 rows spanning 2026-07-20 .. 2026-09-11:
#
#   member seats pooled (n=1312)  p99 10804   p99.9 14549   max 17142  (gpt-53-codex)
#   chair  claude-opus-4-8 (n=420) p99  3656   p99.9  4690   max  5174
#
# A cap below these truncates a real answer, and a truncated JSON body is a
# failed seat — which on the gate path is a step towards `unavailable`, i.e. a
# fail-closed merge block. So the shipped defaults must clear the observed max
# with room, and this test is the guard that keeps a future "tidy-up" from
# quietly lowering them below the evidence.
OBSERVED_MEMBER_MAX = 17142
OBSERVED_CHAIR_MAX = 5174


def test_shipped_defaults_clear_every_output_ever_observed():
    settings, panels = load_panels()          # the SHIPPED council/panels.toml
    for name, panel in panels.items():
        for rigor in ("daily", "deep"):
            b = resolve_budget(settings, panel, rigor)
            for m in panel.members:
                assert b.member(m.name) > OBSERVED_MEMBER_MAX, (name, rigor, m.name)
            assert b.chair > OBSERVED_CHAIR_MAX, (name, rigor)


def test_shipped_caps_are_far_below_what_the_models_would_allow():
    """The point of the cap. Venice's catalogue lets gpt-53-codex and
    claude-opus-4-8 emit 128,000 completion tokens; at 17.5 and 30 DIEM/Mtok
    that is a 6.28 DIEM round of completions from four seats. Capped, the same
    round's completions are bounded at 0.81."""
    settings, panels = load_panels()
    b = resolve_budget(settings, panels["code-review"], "daily")
    assert b.chair <= 12000
    for m in panels["code-review"].members:
        assert b.member(m.name) <= 32000


# ---------- the budget reaches every call site ----------------------------

class RecordingClient:
    """FakeClient that also records the cap each call was given."""
    def __init__(self, reply='{"stance":"approve","headline":"h"}'):
        self.reply = reply
        self.calls = []

    def complete(self, model, system, user, *, json_mode=True, task_type="chat",
                 max_completion_tokens=None):
        self.calls.append({"model": model, "cap": max_completion_tokens})
        return self.reply

    def cap_for(self, model):
        return next(c["cap"] for c in self.calls if c["model"] == model)


def _panel(tmp_path):
    settings, panels = _load(tmp_path)
    return settings, panels["red-team"]


def test_run_panel_gives_each_seat_its_own_ceiling(tmp_path):
    from council.config import resolve_budget
    from council.engine import run_panel
    settings, panel = _panel(tmp_path)
    client = RecordingClient()
    run_panel(panel, "ctx", client, budget=resolve_budget(settings, panel, "daily"))
    assert client.cap_for("m3") == 9000        # panel default
    assert client.cap_for("m4") == 400         # the seat's own override


def test_run_panel_without_a_budget_leaves_the_client_default_alone(tmp_path):
    from council.engine import run_panel
    _, panel = _panel(tmp_path)
    client = RecordingClient()
    run_panel(panel, "ctx", client)
    assert {c["cap"] for c in client.calls} == {None}


def test_synthesize_sends_the_chair_ceiling():
    from council.synthesize import synthesize
    from council.models import MemberResult
    client = RecordingClient('{"recommendation":"go","confidence":7}')
    synthesize("ctx", [MemberResult("A", "m", "approve", "h")], client,
               chair_model="chair", max_completion_tokens=8000)
    assert client.cap_for("chair") == 8000


def test_pick_panel_sends_the_router_ceiling(tmp_path):
    from council.router import pick_panel
    _, panels = _load(tmp_path)
    client = RecordingClient('{"panel": "decision"}')
    pick_panel("ctx", panels, client, router_model="rmodel", default="decision",
               max_completion_tokens=300)
    assert client.cap_for("rmodel") == 300


def test_run_pr_review_caps_both_the_panel_and_the_chair(tmp_path, monkeypatch):
    """The CI gate path — the one that spends 34 DIEM a week."""
    from council.review import run_pr_review
    settings, panels = load_panels()
    client = RecordingClient()
    diff = ("diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n"
            "+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n")
    # The shipped code-review panel names its own chair, and that one is asked (2026-09-20).
    chair = panels["code-review"].chair_model or "chair"
    run_pr_review(diff, panels, client, chair_model="chair", settings=settings)
    caps = {c["cap"] for c in client.calls if c["model"] != chair}
    assert caps == {24000}
    assert client.cap_for(chair) == 8000


def test_run_pr_review_without_settings_still_caps_from_the_class_defaults(tmp_path):
    """An unmodified repo shim calls run_pr_review with no settings. It must
    still get a ceiling, not an unbounded call."""
    from council.review import run_pr_review
    _, panels = load_panels()
    client = RecordingClient()
    diff = ("diff --git a/src/app.py b/src/app.py\n--- a/src/app.py\n"
            "+++ b/src/app.py\n@@ -1 +1 @@\n-a\n+b\n")
    chair = panels["code-review"].chair_model or "chair"
    run_pr_review(diff, panels, client, chair_model="chair")
    assert client.cap_for(chair) == Settings.chair_max_completion_tokens
    assert {c["cap"] for c in client.calls if c["model"] != chair} == {
        Settings.max_completion_tokens}


def test_cli_builds_its_client_with_the_configured_ceiling(monkeypatch):
    """Belt and braces: the client-level default means no call site anywhere —
    compare, sweep, a future command — can be unbounded by omission."""
    from council import cli
    seen = {}
    class Client:
        def __init__(self, key, **kw): seen.update(kw)
        def complete(self, *a, **k): return '{"panel": "decision"}'
    monkeypatch.setattr(cli, "VeniceClient", Client)
    monkeypatch.setattr(cli, "get_api_key", lambda: "k")
    monkeypatch.setattr(cli, "run_panel", lambda *a, **k: [])
    monkeypatch.setattr(cli, "synthesize",
                        lambda *a, **k: __import__("council.models", fromlist=["Synthesis"])
                        .Synthesis(recommendation="r", confidence=5))
    cli.main(["ask", "hi", "--panel", "decision"])
    assert seen["max_completion_tokens"] == Settings.max_completion_tokens


# ---------- no cap may exceed what the model will accept ------------------

# model_spec.maxCompletionTokens from GET https://api.venice.ai/api/v1/models,
# fetched 2026-09-11 (catalogue build 20260911.122036). Venice rejects a request
# whose max_completion_tokens exceeds the model's own ceiling, and a 4xx is not
# retryable — for a panel seat that is an errored seat, and on the gate path
# enough errored seats is a fail-closed merge block. Re-fetch and update this
# snapshot when a seat model changes; never guess it.
CATALOGUE_MAX_COMPLETION = {
    "claude-opus-4-8": 128000, "claude-opus-4-7": 128000,
    "gemini-3-5-flash": 65536, "gemini-3-1-pro-preview": 32768,
    "openai-gpt-53-codex": 128000, "deepseek-v4-pro": 32768, "grok-4-3": 32000,
    # The code-review chair. Same 2026-09-11 fetch, as recorded in
    # docs/chair-bakeoff-design-2026-09-11.md section 3 (not re-fetched on 2026-09-20).
    "openai-gpt-56-sol": 128000,
}


def test_no_shipped_cap_exceeds_its_model_catalogue_ceiling():
    from council.config import chair_for
    settings, panels = load_panels()
    assert settings.chair_model in CATALOGUE_MAX_COMPLETION
    assert settings.router_model in CATALOGUE_MAX_COMPLETION
    for name, panel in panels.items():
        chair = chair_for(settings, panel)          # a panel may name its own chair
        assert chair in CATALOGUE_MAX_COMPLETION, (name, chair)
        for rigor in ("daily", "deep"):
            b = resolve_budget(settings, panel, rigor)
            for m in panel.members:
                assert m.model in CATALOGUE_MAX_COMPLETION, m.model
                assert b.member(m.name) <= CATALOGUE_MAX_COMPLETION[m.model], \
                    (name, rigor, m.name, m.model)
            assert b.chair <= CATALOGUE_MAX_COMPLETION[chair], (name, rigor, chair)
            assert b.router <= CATALOGUE_MAX_COMPLETION[settings.router_model]
