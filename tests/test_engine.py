import pytest

from council.engine import run_panel
from council.models import Panel, Member
from tests.conftest import FakeClient


def _panel():
    return Panel(name="decision", description="d", members=[
        Member("Founder", "m1", "be a founder"),
        Member("Eng", "m2", "be an eng"),
    ])


def test_run_panel_parallel_collects_results(member_json):
    client = FakeClient(by_model={
        "m1": member_json(stance="approve", headline="go",
                          findings=[("ship it", "info", 9)]),
        "m2": member_json(stance="concerns", headline="careful"),
    })
    results = run_panel(_panel(), "ship X?", client)
    by = {r.member: r for r in results}
    assert by["Founder"].stance == "approve"
    assert by["Founder"].findings[0].confidence == 9
    assert by["Eng"].stance == "concerns"


def test_run_panel_isolates_member_errors(member_json):
    client = FakeClient(by_model={"m2": member_json()}, raises_for={"m1"})
    results = run_panel(_panel(), "x", client)
    by = {r.member: r for r in results}
    assert by["Founder"].error is not None
    assert by["Founder"].stance == "na"
    assert by["Eng"].error is None  # one failure doesn't kill the panel


def test_run_panel_coerces_bad_json():
    client = FakeClient(default="not json at all")
    results = run_panel(_panel(), "x", client)
    assert all(r.error is not None for r in results)


def test_run_panel_tolerates_non_numeric_confidence():
    # A bad confidence on one finding must not nullify the whole member.
    bad = {"stance": "concerns", "headline": "h",
           "findings": [{"point": "p1", "severity": "med", "confidence": "high"},
                        {"point": "p2", "severity": "low", "confidence": None}],
           "suggestions": []}
    client = FakeClient(default=bad)
    results = run_panel(_panel(), "x", client)
    for r in results:
        assert r.error is None
        assert [f.confidence for f in r.findings] == [5, 5]  # coerced to default


def test_run_panel_caps_worker_pool():
    # A pathologically large custom panel must not spin up one thread per seat.
    import council.engine as eng
    captured = {}
    real = eng.concurrent.futures.ThreadPoolExecutor

    class Spy(real):
        def __init__(self, *a, max_workers=None, **k):
            captured["max_workers"] = max_workers
            super().__init__(*a, max_workers=max_workers, **k)

    eng.concurrent.futures.ThreadPoolExecutor = Spy
    try:
        big = Panel("big", "d", [Member(f"M{i}", "m", "s") for i in range(40)])
        run_panel(big, "x", FakeClient(default={"stance": "na", "headline": "h"}))
    finally:
        eng.concurrent.futures.ThreadPoolExecutor = real
    assert captured["max_workers"] == 8


# ── a reply that parses but says nothing is a FAILED seat, never a quiet "na" ─────────────
# Captured 2026-09-19 from deepseek-v4-pro under Venice `response_format: json_object`:
# the forced-JSON decoder garbles the first key and the model often stops right there. It is
# valid JSON, so the old engine returned stance "na" with no error, and the code-review panel
# silently ran without its Security Officer in 34 of 41 saved reviews (2026-08-29..09-19).
@pytest.mark.parametrize("junk", [
    '{": ": ", "}',
    '{": ": "} "}',
    '{": ": "} is not valid JSON. I\'ll output the JSON object directly."}',
    "{}",
    "[]",
    '"approve"',
    '{"stance": "", "headline": "  ", "findings": [], "suggestions": []}',
])
def test_a_reply_with_no_usable_seat_fields_is_an_error(junk):
    results = run_panel(_panel(), "x", FakeClient(default=junk))
    for r in results:
        assert r.error is not None and "no usable answer" in r.error
        assert r.stance == "na" and r.headline == "(member errored)" and r.findings == []


def test_a_reply_with_a_garbled_stance_key_keeps_its_real_content():
    # Also captured live: the first key came back as ".stance" but the rest was intact.
    # The headline and findings are real work, so the seat stays usable (stance unknown).
    garbled = {".stance": "approve", "headline": "No security risk", "suggestions": [],
               "findings": [{"point": "p", "severity": "low", "confidence": 8}]}
    for r in run_panel(_panel(), "x", FakeClient(default=garbled)):
        assert r.error is None and r.stance == "na"
        assert r.headline == "No security risk" and len(r.findings) == 1


def test_a_seat_is_asked_in_the_json_mode_its_member_declares(member_json):
    panel = Panel(name="p", description="d", members=[
        Member("Default", "m1", "sys"),
        Member("NoForcedJson", "m2", "sys", json_mode=False),
    ])
    client = FakeClient(default=member_json())
    run_panel(panel, "x", client)
    assert {c["model"]: c["json_mode"] for c in client.calls} == {"m1": True, "m2": False}

