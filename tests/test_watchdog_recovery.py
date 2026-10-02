import json

from watchdog import run as watchdog_run
from watchdog.triage import CheckStatus


def test_healthy_poll_resolves_uncertain_incident_and_allows_a_later_new_send(monkeypatch, tmp_path, capsys):
    paths = {name: tmp_path / file for name, file in {
        'WATCHDOG_STATE': 'state.json', 'WATCHDOG_PENDING': 'pending.json',
        'WATCHDOG_DELIVERY_LAST': 'last.json', 'WATCHDOG_METRICS': 'metrics.json',
    }.items()}
    for name, path in paths.items(): monkeypatch.setenv(name, str(path))
    now = [100]
    monkeypatch.setattr(watchdog_run.time, 'time', lambda: now[0])
    status = [[CheckStatus('disk', 'crit', 'full')]]
    monkeypatch.setattr(watchdog_run, 'collect', lambda current, prior: (status[0], {}))

    watchdog_run.main([])
    first = json.loads(next(x.removeprefix('WATCHDOG_JSON:') for x in capsys.readouterr().out.splitlines() if x.startswith('WATCHDOG_JSON:')))
    watchdog_run.record_delivery(paths['WATCHDOG_PENDING'], paths['WATCHDOG_STATE'], paths['WATCHDOG_DELIVERY_LAST'], first['attempt_id'], 'uncertain')

    now[0] = 200
    status[0] = [CheckStatus('disk', 'ok', 'fine')]
    watchdog_run.main([])
    capsys.readouterr()
    assert not paths['WATCHDOG_PENDING'].exists()
    assert json.loads(paths['WATCHDOG_DELIVERY_LAST'].read_text())['resolved_at'] == 200

    now[0] = 300
    status[0] = [CheckStatus('disk', 'crit', 'full again')]
    watchdog_run.main([])
    later = json.loads(next(x.removeprefix('WATCHDOG_JSON:') for x in capsys.readouterr().out.splitlines() if x.startswith('WATCHDOG_JSON:')))
    assert later['escalate'] is True
    assert later['attempt_id']
