import json

from watchdog.run import record_delivery, stage_delivery
from watchdog.triage import CheckStatus


def test_failed_delivery_does_not_commit_suppression_state(tmp_path):
    state = tmp_path / "state.json"
    pending = tmp_path / "delivery-pending.json"
    attempt = stage_delivery(
        pending,
        [CheckStatus("disk", "crit", "full")],
        {"disk": {"level": "crit", "ts": 100}},
        100,
    )
    record_delivery(pending, state, tmp_path / "delivery-last.json", attempt, "failed")
    assert not state.exists()
    assert json.loads(pending.read_text())["delivery"]["status"] == "failed"


def test_accepted_delivery_commits_suppression_and_provider_receipt(tmp_path):
    state = tmp_path / "state.json"
    pending = tmp_path / "delivery-pending.json"
    last = tmp_path / "delivery-last.json"
    attempt = stage_delivery(
        pending,
        [CheckStatus("disk", "crit", "full")],
        {"disk": {"level": "crit", "ts": 100}},
        100,
    )
    receipt = {"ok": True, "status": "accepted", "chat_id": 1, "message_ids": [9]}
    record_delivery(pending, state, last, attempt, "accepted", receipt)
    assert json.loads(state.read_text()) == {"disk": {"level": "crit", "ts": 100}}
    saved = json.loads(last.read_text())
    assert saved["delivery"]["status"] == "accepted"
    assert saved["delivery"]["provider_receipt"] == receipt
    assert not pending.exists()


def test_uncertain_delivery_stays_pending_without_committing_or_replay(tmp_path):
    state = tmp_path / "state.json"
    pending = tmp_path / "delivery-pending.json"
    attempt = stage_delivery(
        pending,
        [CheckStatus("disk", "crit", "full")],
        {"disk": {"level": "crit", "ts": 100}},
        100,
    )
    record_delivery(pending, state, tmp_path / "delivery-last.json", attempt, "uncertain")
    assert not state.exists()
    saved = json.loads(pending.read_text())
    assert saved["delivery"]["status"] == "uncertain"


def test_dry_run_does_not_stage_an_alert_or_rewrite_state(tmp_path, monkeypatch, capsys):
    from watchdog import run
    from watchdog.triage import CheckStatus
    paths = {key: tmp_path / key for key in ('WATCHDOG_STATE','WATCHDOG_PENDING','WATCHDOG_DELIVERY_LAST','WATCHDOG_METRICS')}
    for key, path in paths.items(): monkeypatch.setenv(key, str(path))
    monkeypatch.setattr(run, 'collect', lambda *a: ([CheckStatus('disk','crit','full')], {'disk': {'value': 99}}))
    run.main(['--dry-run'])
    assert not any(p.exists() for p in paths.values())
    assert '"escalate": true' in capsys.readouterr().out
