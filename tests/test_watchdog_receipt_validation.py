import pytest

from watchdog.run import record_delivery, stage_delivery
from watchdog.triage import CheckStatus


def test_watchdog_rejects_a_success_label_without_meaningful_provider_acceptance(tmp_path):
    pending = tmp_path / 'pending.json'
    attempt = stage_delivery(pending, [CheckStatus('disk', 'crit', 'full')], {'disk': {'level': 'crit', 'ts': 1}}, 1)
    with pytest.raises(ValueError, match='provider receipt'):
        record_delivery(pending, tmp_path / 'state.json', tmp_path / 'last.json', attempt, 'accepted', {'status': 'accepted', 'message_ids': []})
    assert not (tmp_path / 'state.json').exists()
