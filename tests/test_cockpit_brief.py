"""The brief must preserve uncertainty and never turn a review into approval."""
import json
from datetime import datetime, timezone, timedelta

import pytest
import yaml

from tests.test_cockpit import world, login


@pytest.fixture
def brief_world(world, tmp_path):
    app, backlog = world
    archive = tmp_path / 'archive.yaml'
    archive.write_text('items: []\n')
    app.config.update(ARCHIVE_PATH=str(archive), BRIEF_STATE_PATH=str(tmp_path / 'brief.json'))
    return app, backlog, archive


def write_rows(path, rows):
    path.write_text(yaml.safe_dump({'items': rows}))


def ack(client, csrf, snapshot):
    return client.post('/api/brief/review', json={'token': snapshot['brief']['review_token']},
                       headers={'X-CSRF-Token': csrf})


def test_first_brief_uses_recorded_results_without_calling_merges_deployments(brief_world):
    app, backlog, archive = brief_world
    today = datetime.now(timezone.utc).date().isoformat()
    write_rows(archive, [dict(id='finished', repo='sat-prep', title='Improve flash mode', status='done',
                            merged=today, merge_commit='a' * 40, note='Merged; deployment still awaits owner.',
                            deployment_followup='release-held'),
                         dict(id='old', repo='sat-prep', title='Old outcome', status='done',
                              merged='2020-01-01', merge_commit='b' * 40),
                         dict(id='undated', repo='sat-prep', title='No completion date', status='done')])
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    assert d['brief']['comparison_status'] == 'first_review'
    assert d['brief']['changes'] == []  # Historical open tasks are not all newly arrived.
    assert [r['id'] for r in d['brief']['recent_results']] == ['finished']
    result = d['brief']['recent_results'][0]
    assert result['outcome_label'] == 'Merged'
    assert result['date_precision'] == 'day'
    assert 'deployment' in result['next_action'].lower()
    assert 'still awaits' in result['why']
    assert d['resources']['cash_result'] is None
    assert len(d['results']) == 3  # Unknown dates remain available outside the recent list.
    evidence = c.get(result['source_url'])
    assert evidence.status_code == 200 and b'Merged; deployment still awaits owner.' in evidence.data


@pytest.mark.parametrize('status,owner,want', [
    ('held', None, 'held'), ('held', 'To do', 'held'), ('open', None, 'queued'),
    ('in_review', None, 'review'), ('in_review', 'Changes', 'waiting'),
])
def test_classification_never_promotes_held_or_queued_work(brief_world, monkeypatch, status, owner, want):
    from cockpit import remote
    app, backlog, _ = brief_world
    write_rows(backlog, [dict(id='one', repo='sat-prep', title='Work', status=status,
                             note='Approved ready complete running!')])
    app.config['REMOTE_READS'] = bool(owner)
    monkeypatch.setattr(remote, 'remote_work', lambda cfg: ([dict(id='ops:runner:one', backlog_id='one',
        source_url='https://www.notion.so/item', status=owner)], []))
    c = app.test_client(); login(c)
    row = c.get('/api/snapshot').json['work'][0]
    assert row['category'] == want
    assert row['next_action']


def test_review_checkpoint_is_explicit_cross_session_and_does_not_approve_work(brief_world):
    app, backlog, archive = brief_world
    app.config['ENABLE_ACTIONS'] = False
    c = app.test_client(); csrf = login(c)
    before = backlog.read_bytes(), archive.read_bytes()
    initial = c.get('/api/snapshot').json
    assert c.get('/api/snapshot').json['brief']['baseline_at'] is None
    assert ack(c, csrf, initial).status_code == 200
    d = c.get('/api/snapshot').json
    assert d['brief']['baseline_at'] and d['brief']['changes'] == []
    assert (backlog.read_bytes(), archive.read_bytes()) == before
    other = app.test_client(); login(other)
    assert other.get('/api/snapshot').json['brief']['baseline_at'] == d['brief']['baseline_at']
    assert ack(c, csrf, initial).status_code == 200  # Lost response retry is idempotent.


def test_changes_survive_refresh_and_only_explicit_terminal_evidence_is_completion(brief_world):
    app, backlog, archive = brief_world
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    write_rows(backlog, [dict(id='new', repo='sat-prep', title='New task', status='held')])
    d = c.get('/api/snapshot').json
    kinds = {r['id']: r['kind'] for r in d['brief']['changes']}
    assert kinds == {'item-1': 'left_queue', 'new': 'new'}
    assert c.get('/api/snapshot').json['brief']['changes'] == d['brief']['changes']
    write_rows(archive, [dict(id='item-1', repo='sat-prep', title='Restore pause', status='done',
                             merged=datetime.now(timezone.utc).isoformat(), merge_commit='a' * 40)])
    changes = c.get('/api/snapshot').json['brief']['changes']
    assert {r['id']: r['kind'] for r in changes} == {'item-1': 'completed', 'new': 'new'}


def test_outage_does_not_erase_prior_records_or_invent_completion(brief_world):
    app, backlog, _ = brief_world
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    old = backlog.read_bytes(); backlog.unlink()
    d = c.get('/api/snapshot').json
    assert d['brief']['changes'] == []
    assert any(s['name'] == 'Shared backlog' for s in d['brief']['source_issues'])
    assert ack(c, csrf, d).status_code == 200
    backlog.write_bytes(old)
    assert c.get('/api/snapshot').json['brief']['changes'] == []


def test_stale_and_forged_checkpoint_cannot_hide_unseen_changes(brief_world):
    app, backlog, _ = brief_world
    c = app.test_client(); csrf = login(c)
    d = c.get('/api/snapshot').json
    write_rows(backlog, [dict(id='item-1', repo='sat-prep', title='Changed after display', status='held')])
    assert ack(c, csrf, d).status_code == 409
    assert c.get('/api/snapshot').json['brief']['baseline_at'] is None
    assert c.post('/api/brief/review', json={'token': 'forged'}, headers={'X-CSRF-Token': csrf}).status_code == 409
    assert c.post('/api/brief/review', json={'token': 'x'}).status_code == 403
    assert c.post('/api/brief/review', json={'token': 'x'}, headers={'X-CSRF-Token': csrf, 'Origin': 'https://bad.example'}).status_code == 403


def test_corrupt_checkpoint_shows_unknown_without_overwriting_it(brief_world):
    from pathlib import Path
    app, _, _ = brief_world
    path = Path(app.config['BRIEF_STATE_PATH']); path.write_text('{broken')
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    assert d['brief']['comparison_status'] == 'unavailable'
    assert d['brief']['review_token'] is None
    assert path.read_text() == '{broken'


def test_unknown_source_status_stays_visible_and_missing_result_is_not_the_goal(brief_world, monkeypatch):
    from cockpit import remote
    app, _, _ = brief_world
    app.config['REMOTE_READS'] = True
    monkeypatch.setattr(remote, 'remote_work', lambda cfg: ([
        dict(id='future', title='Unknown work', repo='sat-prep', status='New vendor status', source='Attain product queue', source_url='https://www.notion.so/x'),
        dict(id='closed', title='Claimed done', repo='sat-prep', status='Done', source='Attain product queue', source_url='https://www.notion.so/y', is_terminal=True, why='No result detail recorded.', completed_at=None)
    ], []))
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    assert next(w for w in d['work'] if w['id'] == 'future')['category'] == 'unknown'
    assert next(r for r in d['results'] if r['id'] == 'closed')['outcome_label'] == 'Recorded done'
    assert 'closed' not in [r['id'] for r in d['brief']['recent_results']]


def test_unknown_local_state_is_visible_and_closed_work_is_not_called_done(brief_world, monkeypatch):
    from cockpit import remote
    app, backlog, _ = brief_world
    write_rows(backlog, [dict(id='future-local', title='Unexpected state', repo='sat-prep', status='paused_by_vendor')])
    app.config['REMOTE_READS'] = True
    monkeypatch.setattr(remote, 'remote_work', lambda cfg: ([dict(id='abandoned', title='Never finished', repo='sat-prep',
        status='Archived', source='Attain product queue', source_url='', is_terminal=True)], []))
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    assert d['work'][0]['id'] == 'future-local' and d['work'][0]['category'] == 'unknown'
    assert d['results'][0]['outcome_label'] == 'Closed'


def test_remote_closed_record_does_not_use_success_criteria_as_achieved_result(brief_world, tmp_path, monkeypatch):
    from cockpit import remote
    app, _, _ = brief_world
    env = tmp_path / 'env'; env.write_text('NOTION_TOKEN_ATTAINPREP=fixture\n')
    config = tmp_path / 'attain.json'; config.write_text('{"database_id":"fixture-db"}')
    def text(value):
        return {'type': 'rich_text', 'rich_text': [{'plain_text': value}]}
    monkeypatch.setattr(remote, '_query', lambda *args: [dict(id='one', last_edited_time='2026-09-13T10:00:00Z',
        properties={'Status': {'type': 'select', 'select': {'name': 'Done'}}, 'Task': text('Improve retention'),
                    'Done when': text('Every student retains everything'), 'Result': text('')})])
    rows, sources = remote.remote_work({**app.config, 'ENV_FILE': str(env), 'ATTAIN_QUEUE_CONFIG': str(config)})
    assert len(rows) == 1 and rows[0]['is_terminal']
    assert 'Every student' not in rows[0]['why']
    assert not rows[0]['completed_at']  # An edit timestamp is not a completion timestamp.


def test_future_result_and_dropped_item_never_claim_newly_completed_work(brief_world):
    app, _, archive = brief_world
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    future = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    write_rows(archive, [dict(id='future', title='Future report', repo='sat-prep', status='done', merged=future),
                         dict(id='abandoned', title='Abandoned', repo='sat-prep', status='dropped', dropped='2026-09-13')])
    d = c.get('/api/snapshot').json
    assert 'future' not in [r['id'] for r in d['brief']['recent_results']]
    assert next(r for r in d['brief']['changes'] if r['id'] == 'abandoned')['kind'] == 'closed'


def test_stale_tab_cannot_replace_newer_checkpoint(brief_world):
    app, backlog, _ = brief_world
    c = app.test_client(); csrf = login(c)
    stale = c.get('/api/snapshot').json
    write_rows(backlog, [dict(id='newer', title='Newer evidence', repo='sat-prep', status='open')])
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    assert ack(c, csrf, stale).status_code == 409
    assert c.get('/api/snapshot').json['brief']['changes'] == []


def test_checkpoint_write_failure_preserves_previous_review(brief_world, monkeypatch):
    from cockpit import briefing
    from pathlib import Path
    app, backlog, _ = brief_world
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    path = Path(app.config['BRIEF_STATE_PATH']); old = path.read_bytes()
    write_rows(backlog, [dict(id='two', title='New work', repo='sat-prep', status='open')])
    def fail_replace(*args):
        raise OSError('Disk unavailable')
    monkeypatch.setattr(briefing.os, 'replace', fail_replace)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 503
    assert path.read_bytes() == old
    assert c.get('/api/snapshot').json['brief']['changes']


def test_duplicate_ids_prevent_checkpoint_but_do_not_hide_work(brief_world):
    app, backlog, _ = brief_world
    write_rows(backlog, [dict(id='same', title='First', repo='sat-prep', status='held'),
                         dict(id='same', title='Second', repo='sat-prep', status='held')])
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    assert len(d['work']) == 2 and d['brief']['review_token'] is None
    assert c.get('/work/same').status_code == 409
    assert c.get('/work/../../etc/passwd').status_code == 404


def test_numeric_id_evidence_opens_without_granting_new_hold_authority(brief_world):
    app, backlog, _ = brief_world
    write_rows(backlog, [dict(id=123, title='Numeric legacy identity', repo='sat-prep', status='open', note='Preserved evidence')])
    c = app.test_client(); login(c)
    d = c.get('/api/snapshot').json
    response = c.get(d['work'][0]['source_url'])
    assert response.status_code == 200 and b'Preserved evidence' in response.data
    assert not d['work'][0].get('hold_token')


def test_post_replace_sync_failure_reports_recorded_review(brief_world, monkeypatch, caplog):
    from cockpit import briefing
    app, _, _ = brief_world
    c = app.test_client(); csrf = login(c)
    snapshot = c.get('/api/snapshot').json
    real_sync = briefing.os.fsync
    calls = []
    def fail_directory_sync(fd):
        calls.append(fd)
        if len(calls) == 2:
            raise OSError('Directory durability unavailable')
        return real_sync(fd)
    monkeypatch.setattr(briefing.os, 'fsync', fail_directory_sync)
    response = ack(c, csrf, snapshot)
    assert response.status_code == 200
    assert c.get('/api/snapshot').json['brief']['baseline_at'] == response.json['reviewed_at']
    assert 'durability' in caplog.text.lower()
    assert ack(c, csrf, snapshot).status_code == 200


def test_expired_session_requests_login_instead_of_claiming_save_failure(brief_world):
    app, _, _ = brief_world
    c = app.test_client(); csrf = login(c)
    d = c.get('/api/snapshot').json
    with c.session_transaction() as session:
        session.clear()
    assert ack(c, csrf, d).status_code == 401


@pytest.mark.parametrize('missing', ['backlog', 'archive'])
def test_first_source_recovery_does_not_call_its_history_new(brief_world, missing):
    app, backlog, archive = brief_world
    write_rows(archive, [dict(id='old', title='Old result', repo='sat-prep', status='done', merged='2025-01-01')])
    path = backlog if missing == 'backlog' else archive
    original = path.read_bytes(); path.unlink()
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    path.write_bytes(original)
    d = c.get('/api/snapshot').json
    assert d['brief']['changes'] == []
    assert d['brief']['uncompared_sources']
    assert ack(c, csrf, d).status_code == 200
    if missing == 'backlog':
        write_rows(backlog, [dict(id='genuinely-new', title='New since source was observed', repo='sat-prep', status='open')])
    else:
        write_rows(archive, [dict(id='genuinely-new', title='New recorded completion', repo='sat-prep', status='done', merged=datetime.now(timezone.utc).isoformat())])
    assert 'genuinely-new' in [r['id'] for r in c.get('/api/snapshot').json['brief']['changes']]


def test_undated_discovery_and_edited_old_result_are_not_new_completions(brief_world):
    app, _, archive = brief_world
    write_rows(archive, [dict(id='old', title='Known old result', repo='sat-prep', status='done', merged='2025-01-01', note='Old note')])
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    write_rows(archive, [dict(id='old', title='Known old result', repo='sat-prep', status='done', merged='2025-01-01', note='Additional evidence'),
                         dict(id='undated', title='Undated old result', repo='sat-prep', status='done')])
    changes = c.get('/api/snapshot').json['brief']['changes']
    assert {r['id']: r['kind'] for r in changes} == {'old': 'changed', 'undated': 'newly_observed'}


def test_checkpoint_size_limit_never_saves_an_unreadable_checkpoint(brief_world):
    from cockpit import briefing
    from pathlib import Path
    app, _, _ = brief_world
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    path = Path(app.config['BRIEF_STATE_PATH']); original = path.read_bytes()
    with pytest.raises(ValueError):
        briefing.save(app.config, {'records': {'large': 'x' * 2_000_001}, 'observed_sources': []}, 'oversized')
    assert path.read_bytes() == original


def test_incomplete_checkpoint_record_keeps_current_work_visible(brief_world):
    from pathlib import Path
    app, _, archive = brief_world
    write_rows(archive, [dict(id='old', title='Old result', repo='sat-prep', status='done', merged='2025-01-01')])
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    path = Path(app.config['BRIEF_STATE_PATH'])
    state = json.loads(path.read_text())
    del state['records']['backlog:old']['terminal']
    path.write_text(json.dumps(state)); original = path.read_bytes()
    write_rows(archive, [dict(id='old', title='Corrected result', repo='sat-prep', status='done', merged='2025-01-01')])
    response = c.get('/api/snapshot')
    assert response.status_code == 200
    assert response.json['work']
    assert response.json['brief']['comparison_status'] == 'unavailable'
    assert response.json['brief']['review_token'] is None
    assert path.read_bytes() == original


def test_temporarily_disabled_remote_reads_preserve_comparison(brief_world, monkeypatch):
    from cockpit import remote
    app, _, _ = brief_world
    rows = [dict(id='attain-existing', title='Queued work', repo='sat-prep', status='Ready', source='Attain product queue', source_url='https://www.notion.so/a'),
            dict(id='ops:runner:item-1', backlog_id='item-1', title='Linked owner work', repo='sat-prep', status='To do', source='Romance Ops', source_url='https://www.notion.so/b')]
    monkeypatch.setattr(remote, 'remote_work', lambda cfg: ([dict(r) for r in rows], [dict(name=name, status='available', detail='fixture') for name in ('Attain product queue', 'Romance Ops')]))
    app.config['REMOTE_READS'] = True
    c = app.test_client(); csrf = login(c)
    assert ack(c, csrf, c.get('/api/snapshot').json).status_code == 200
    app.config['REMOTE_READS'] = False
    disconnected = c.get('/api/snapshot').json
    assert disconnected['brief']['changes'] == []
    assert ack(c, csrf, disconnected).status_code == 200
    app.config['REMOTE_READS'] = True
    recovered = c.get('/api/snapshot').json
    assert recovered['brief']['changes'] == []
    assert 'attain-existing' in [r['id'] for r in recovered['work']]
