from copy import deepcopy
from pathlib import Path
import json

import pytest
import yaml
pytest.importorskip("flask")
from werkzeug.security import generate_password_hash

from cockpit.app import create_app


@pytest.fixture
def world(tmp_path):
    backlog = tmp_path / 'backlog.yaml'
    backlog.write_text(yaml.safe_dump({'items': [dict(id='item-1', repo='sat-prep', title='Restore pause', status='open', created='2026-09-12', prompt='Private task context')]}))
    catalog = tmp_path / 'portfolio.json'
    catalog.write_text(json.dumps({'initiatives': [{'id': 'attain', 'name': 'Attain Prep', 'repos': ['sat-prep'], 'purpose': 'Independent retained learning', 'ideal_url': 'https://www.notion.so/attain'}], 'decisions': [], 'checks': []}))
    cfg = dict(TESTING=True, SECRET_KEY='fixture-signing-key-' * 3, PASSWORD_HASH=generate_password_hash('fixture-password'),
               SESSION_COOKIE_SECURE=False, BACKLOG_PATH=str(backlog), CATALOG_PATH=str(catalog), PROJECTS_ROOT=str(tmp_path),
               STATE_ROOT=str(tmp_path / 'state'), USAGE_DB=str(tmp_path / 'missing-ledger.db'), ENABLE_ACTIONS=True, REMOTE_READS=False, TRUSTED_HOSTS=['localhost'])
    app = create_app(cfg)
    return app, backlog


def login(client):
    client.get('/login')
    with client.session_transaction() as s: csrf = s['csrf']
    response = client.post('/login', data={'password': 'fixture-password', 'csrf': csrf})
    assert response.status_code == 302
    with client.session_transaction() as s: return s['csrf']


def test_private_data_requires_login_after_logout(world):
    app, _ = world
    c = app.test_client()
    assert c.get('/api/snapshot').status_code == 401
    assert b'Private task context' not in c.get('/login').data
    csrf = login(c)
    assert c.get('/api/snapshot').status_code == 200
    assert c.post('/logout', data={'csrf': csrf}).status_code == 302
    assert c.get('/api/snapshot').status_code == 401


def test_complete_work_list_and_unknown_health_are_not_hidden(world):
    app, backlog = world
    backlog.write_text(yaml.safe_dump({'items': [dict(id=f'item-{i}', repo='sat-prep', title=f'Necessary decision {i}', status='held', note='Awaiting owner') for i in range(85)]}))
    c = app.test_client(); login(c)
    data = c.get('/api/snapshot').json
    assert len(data['work']) == 85
    assert all(item['status'] == 'held' for item in data['work'])
    assert any(source['status'] == 'unavailable' for source in data['sources'])
    assert data['resources']['cash_result'] is None


def test_exact_action_completes_once_and_retains_owner_evidence(world):
    app, backlog = world
    c = app.test_client(); csrf = login(c)
    work = c.get('/api/snapshot').json['work'][0]
    payload = {'token': work['hold_token'], 'reason': 'Wait for the customer evidence'}
    headers = {'X-CSRF-Token': csrf}
    assert c.post('/api/work/item-1/hold', json=payload, headers=headers).status_code == 200
    record = yaml.safe_load(backlog.read_text())['items'][0]
    assert record['status'] == 'held'
    assert record['note'] == 'Wait for the customer evidence'
    assert record['cockpit_decision']['action'] == 'hold'
    before = backlog.read_bytes()
    assert c.post('/api/work/item-1/hold', json=payload, headers=headers).status_code == 200
    assert backlog.read_bytes() == before
    assert c.get('/api/snapshot').json['work'][0]['status'] == 'held'


def test_stale_or_forged_decision_cannot_change_work(world):
    app, backlog = world
    c = app.test_client(); csrf = login(c)
    work = c.get('/api/snapshot').json['work'][0]
    d = yaml.safe_load(backlog.read_text()); d['items'][0]['title'] = 'A different task'; backlog.write_text(yaml.safe_dump(d))
    before = backlog.read_bytes()
    payload = {'token': work['hold_token'], 'reason': 'Hold'}
    assert c.post('/api/work/item-1/hold', json=payload, headers={'X-CSRF-Token': csrf}).status_code == 409
    assert c.post('/api/work/item-1/hold', json=payload).status_code == 403
    assert c.post('/api/work/item-1/hold', json=payload, headers={'X-CSRF-Token': csrf, 'Origin': 'https://attacker.example'}).status_code == 403
    assert backlog.read_bytes() == before


def test_read_only_mode_never_mutates_and_invalid_host_is_rejected(world):
    app, backlog = world
    app.config['ENABLE_ACTIONS'] = False
    c = app.test_client(); csrf = login(c)
    data = c.get('/api/snapshot').json
    assert not data['work'][0].get('hold_token')
    before = backlog.read_bytes()
    assert c.post('/api/work/item-1/hold', json={'token': 'x', 'reason': 'Hold'}, headers={'X-CSRF-Token': csrf}).status_code == 403
    assert c.get('/api/snapshot', headers={'Host': 'attacker.example'}).status_code == 400
    assert backlog.read_bytes() == before


def test_missing_auth_configuration_refuses_startup():
    with pytest.raises(ValueError): create_app({'SECRET_KEY': '', 'PASSWORD_HASH': ''})


def test_resource_reader_uses_real_ledger_schema_and_never_calls_tokens_cash(tmp_path):
    import sqlite3
    from datetime import datetime, timezone
    from cockpit.sources import resources
    path = tmp_path / 'usage.db'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE usage (ts TEXT, project TEXT, tokens_in INTEGER, tokens_out INTEGER)')
        db.execute('INSERT INTO usage VALUES (?, ?, ?, ?)', (datetime.now(timezone.utc).isoformat(), 'council', 300, 20))
    data = resources({'USAGE_DB': str(path)})
    assert data['status'] == 'available'
    assert data['usage'] == [{'project': 'council', 'calls': 1, 'tokens_in': 300, 'tokens_out': 20}]
    assert data['cash_result'] is None and data['cash_spend'] is None


def test_remote_partial_feed_is_rejected_and_accounts_stay_scoped(world, tmp_path, monkeypatch):
    from cockpit import remote
    class Response:
        status_code = 200
        def json(self): return {'results': [{'id': 'partial'}], 'has_more': True, 'next_cursor': 'same'}
    with pytest.raises(ValueError, match='Partial'):
        remote._query('fixture', 'databases/id/query', '2022-06-28', post=lambda *a, **k: Response())
    app, _ = world
    env = tmp_path / 'scoped.env'; env.write_text('NOTION_TOKEN=romance-fixture\n')
    ops = tmp_path / 'romance-empire/config'; ops.mkdir(parents=True)
    (ops / 'ops.yaml').write_text('notion:\n  data_source_id: romance-db\n')
    seen = []
    def query(token, route, version):
        seen.append((token, route, version)); return []
    monkeypatch.setattr(remote, '_query', query)
    cfg = {**app.config, 'ENV_FILE': str(env)}
    work, sources = remote.remote_work(cfg)
    assert work == [] and sources[0]['status'] == 'unavailable'
    assert sources[1]['status'] == 'available'
    assert seen == [('romance-fixture', 'data_sources/romance-db/query', '2025-09-03')]
    assert 'romance-fixture' not in json.dumps(sources)


def test_owner_queue_reference_does_not_duplicate_backlog_work(world, monkeypatch):
    from cockpit import remote
    app, _ = world
    app.config['REMOTE_READS'] = True
    monkeypatch.setattr(remote, 'remote_work', lambda cfg: ([{'id': 'ops:runner:item-1', 'backlog_id': 'item-1', 'source_url': 'https://www.notion.so/item', 'status': 'Changes'}], []))
    c = app.test_client(); login(c)
    work = c.get('/api/snapshot').json['work']
    assert len(work) == 1 and work[0]['id'] == 'item-1'
    assert work[0]['owner_surface_status'] == 'Changes'
    assert work[0]['source_url'] == 'https://www.notion.so/item'


def test_browser_post_retains_origin_for_the_origin_check(world):
    app, _ = world
    # no-referrer can make a browser's form POST Origin null. Keep it for same-origin requests.
    assert app.test_client().get('/login').headers['Referrer-Policy'] == 'same-origin'
