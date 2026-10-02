import json

from watchdog import run as watchdog_run
from watchdog.triage import CheckStatus


def configure(monkeypatch, tmp_path):
    paths = {
        "WATCHDOG_STATE": tmp_path / "state.json",
        "WATCHDOG_PENDING": tmp_path / "pending.json",
        "WATCHDOG_DELIVERY_LAST": tmp_path / "last.json",
        "WATCHDOG_METRICS": tmp_path / "metrics.json",
    }
    for key, path in paths.items(): monkeypatch.setenv(key, str(path))
    monkeypatch.setattr(watchdog_run, "collect", lambda now, prior: ([CheckStatus("disk", "crit", "full")], {}))
    monkeypatch.setattr(watchdog_run.time, "time", lambda: 100)
    return paths


def payload(output):
    line = next(line for line in output.splitlines() if line.startswith("WATCHDOG_JSON:"))
    return json.loads(line.removeprefix("WATCHDOG_JSON:"))


def test_known_failure_is_retryable_on_the_next_existing_poll(monkeypatch, tmp_path, capsys):
    paths = configure(monkeypatch, tmp_path)
    watchdog_run.main([])
    first = payload(capsys.readouterr().out)
    watchdog_run.record_delivery(paths["WATCHDOG_PENDING"], paths["WATCHDOG_STATE"], paths["WATCHDOG_DELIVERY_LAST"], first["attempt_id"], "failed")
    watchdog_run.main([])
    second = payload(capsys.readouterr().out)
    assert second["escalate"] is True
    assert second["attempt_id"] != first["attempt_id"]
    assert not paths["WATCHDOG_STATE"].exists()


def test_uncertain_send_is_visible_but_not_automatically_replayed(monkeypatch, tmp_path, capsys):
    paths = configure(monkeypatch, tmp_path)
    watchdog_run.main([])
    first = payload(capsys.readouterr().out)
    watchdog_run.record_delivery(paths["WATCHDOG_PENDING"], paths["WATCHDOG_STATE"], paths["WATCHDOG_DELIVERY_LAST"], first["attempt_id"], "uncertain")
    watchdog_run.main([])
    second = payload(capsys.readouterr().out)
    assert second["escalate"] is False
    assert second["delivery_uncertain"] is True
    assert second["attempt_id"] is None


def test_interrupted_send_intent_is_not_replayed(monkeypatch, tmp_path, capsys):
    paths = configure(monkeypatch, tmp_path)
    watchdog_run.main([])
    first = payload(capsys.readouterr().out)
    watchdog_run.record_delivery(paths['WATCHDOG_PENDING'], paths['WATCHDOG_STATE'], paths['WATCHDOG_DELIVERY_LAST'], first['attempt_id'], 'attempting')
    watchdog_run.main([])
    second = payload(capsys.readouterr().out)
    assert not second['escalate'] and second['delivery_uncertain']


def test_receipt_persistence_failure_cannot_commit_suppression(monkeypatch, tmp_path, capsys):
    import pytest
    paths = configure(monkeypatch, tmp_path)
    watchdog_run.main([])
    first = payload(capsys.readouterr().out)
    original = watchdog_run.save_state
    def fail(path, data):
        if path == paths['WATCHDOG_DELIVERY_LAST']: raise OSError('disk full')
        original(path, data)
    monkeypatch.setattr(watchdog_run, 'save_state', fail)
    receipt = {'ok':True,'status':'accepted','message_ids':[9]}
    with pytest.raises(OSError):
        watchdog_run.record_delivery(paths['WATCHDOG_PENDING'], paths['WATCHDOG_STATE'], paths['WATCHDOG_DELIVERY_LAST'], first['attempt_id'], 'accepted', receipt)
    assert not paths['WATCHDOG_STATE'].exists()
    monkeypatch.setattr(watchdog_run, 'save_state', original)
    watchdog_run.main([])
    assert not payload(capsys.readouterr().out)['escalate']
