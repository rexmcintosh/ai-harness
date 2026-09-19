from council.synthesize import synthesize
from council.models import MemberResult
from tests.conftest import FakeClient


def _results():
    return [
        MemberResult("Founder", "m1", "approve", "go"),
        MemberResult("Eng", "m2", "concerns", "risky"),
    ]


def test_synthesize_parses_chair_json():
    client = FakeClient(default={
        "recommendation": "do X with guardrails",
        "confidence": 8,
        "consensus": ["X is the right direction"],
        "disagreements": [{"topic": "rollout", "type": "taste",
                           "positions": "founder fast, eng slow", "resolution": "stage it"}],
        "cross_panel_themes": ["timeline risk"],
    })
    s = synthesize("ship X?", _results(), client, chair_model="c")
    assert s.recommendation.startswith("do X")
    assert s.disagreements[0].type == "taste"
    assert s.cross_panel_themes == ["timeline risk"]
    assert s.error is None


def test_synthesize_parses_chair_blocking_findings():
    # The chair's grounded blocking list is what the gate counts (audit F2/F4).
    client = FakeClient(default={
        "recommendation": "request changes", "confidence": 9,
        "consensus": [], "disagreements": [], "cross_panel_themes": [],
        "blocking_findings": [
            {"point": "SQL injection at db.py:40", "severity": "critical",
             "why": "user input concatenated into query, verified in the file"},
            {"point": "ignored, malformed"},  # tolerate missing fields
        ],
    })
    s = synthesize("x", _results(), client, chair_model="c")
    assert len(s.blocking_findings) == 2
    assert s.blocking_findings[0].point.startswith("SQL injection")
    assert s.blocking_findings[0].severity == "critical"
    assert s.blocking_findings[1].severity == ""   # defaulted


def test_synthesize_blocking_findings_default_empty():
    client = FakeClient(default={"recommendation": "ok", "confidence": 7})
    s = synthesize("x", _results(), client, chair_model="c")
    assert s.blocking_findings == []


def test_synthesize_recovers_markdown_fenced_json():
    # Reproduces the real failure: the chair (claude-opus-4-8) wraps its JSON in
    # a ```json fence ~75% of the time. The lenient parser must recover it.
    fenced = ('```json\n{"recommendation": "do X", "confidence": 7, '
              '"consensus": [], "disagreements": [], "cross_panel_themes": []}\n```')
    client = FakeClient(default=fenced)
    s = synthesize("x", _results(), client, chair_model="c")
    assert s.error is None
    assert s.recommendation == "do X"
    assert s.confidence == 7


def test_synthesize_falls_back_on_error():
    client = FakeClient(raises_for={"c"})
    s = synthesize("x", _results(), client, chair_model="c")
    assert s.error is not None
    assert "unavailable" in s.recommendation.lower()


def test_a_chair_reply_with_no_recommendation_is_an_error_not_a_clean_pass():
    # Same defect class as the empty seat, with a worse outcome: a junk-but-valid chair
    # reply used to become an empty Synthesis with no error, so the merge gate saw zero
    # blocks and passed. It must surface as a chair failure (callers fail closed on it).
    for junk in ('{": ": ", "}', "{}", "[]", '{"recommendation": "   ", "blocking_findings": []}'):
        syn = synthesize("ctx", [], FakeClient(default=junk), chair_model="c")
        assert syn.error is not None and "no usable answer" in syn.error
        assert syn.blocking_findings == [] and syn.raw_response == junk

