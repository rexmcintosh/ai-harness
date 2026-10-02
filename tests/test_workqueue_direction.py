from copy import deepcopy
import hashlib
import json

import pytest

from workqueue.notion import Notion
from workqueue.runner import _prompt
from workqueue.service import Service

PAGE = '3d645d882ebb81db9d50e35698091788'


def paragraph(text, **extra):
    return {'id': 'block-1', 'type': 'paragraph', 'paragraph': {'rich_text': [{'plain_text': text}]}, **extra}


def test_canonical_direction_fetch_preserves_nested_evidence_and_revision():
    n = Notion('fixture-credential')
    def call(method, path, payload=None):
        assert method == 'GET'
        if path == '/pages/' + PAGE:
            return {'id': PAGE, 'url': 'https://www.notion.so/' + PAGE,
                    'last_edited_time': '2026-09-12T10:00:00Z'}
        if path.startswith('/blocks/' + PAGE + '/children'):
            return {'results': [paragraph('Accepted direction: independent retained learning.', has_children=True)], 'has_more': False}
        if path.startswith('/blocks/block-1/children'):
            return {'results': [paragraph('Counterevidence: repeat-question success is insufficient.')], 'has_more': False}
        pytest.fail(path)
    n.call = call
    context = n.ideal_state()
    assert context['source_url'] == 'https://www.notion.so/' + PAGE
    assert context['source_updated_at'] == '2026-09-12T10:00:00Z'
    assert 'independent retained learning' in context['text']
    assert 'repeat-question success is insufficient' in context['text']
    assert context['sha256'] == hashlib.sha256(context['text'].encode()).hexdigest()
    assert 'fixture-credential' not in json.dumps(context)


class Queue:
    def __init__(self):
        self.row = dict(id='task-1', title='Fix pause', brief='Restore the existing pause control.',
                        done_when='Pause resumes correctly.', checks=['pytest'], mode='Build',
                        repo='sat-prep', source_url='https://www.notion.so/task', status='Ready', run_id='')
        self.direction_calls = 0
        self.result = ''
        self.fail_direction = False
    def tasks(self): return [deepcopy(self.row)]
    def get_task(self, pid): return deepcopy(self.row)
    def get_task_meta(self, pid): return deepcopy(self.row)
    def update_task(self, pid, **fields): self.row.update(fields)
    def append_result(self, pid, rid, text): self.result = text
    def health(self, text): pass
    def ideal_state(self):
        self.direction_calls += 1
        if self.fail_direction:
            raise RuntimeError('do not leak a credential-bearing response')
        return {'status': 'current', 'source_url': 'https://www.notion.so/' + PAGE,
                'source_updated_at': '2026-09-12T10:00:00Z', 'observed_at': '2026-09-12T11:00:00Z',
                'sha256': 'fixture-revision', 'text': 'Retained learning matters. Draft criteria are not approved targets.'}


def test_controller_saves_direction_before_worker_and_publishes_revision(tmp_path):
    n = Queue()
    seen = []
    def run(task, rid, state_dir, projects):
        journal = json.loads((tmp_path / 'journal.json').read_text())
        assert journal['runs'][rid]['task']['direction']['sha256'] == 'fixture-revision'
        seen.append(_prompt(task, rid))
        return {'status': 'In review', 'result': 'Pause restored and checked.'}
    Service(n, tmp_path, '/projects', run).tick()
    assert n.direction_calls == 1
    assert 'Retained learning matters' in seen[0]
    assert 'fixture-revision' in seen[0]
    assert 'fixture-revision' in n.result
    assert n.row['status'] == 'In review'


def test_direction_outage_is_visible_but_does_not_invent_approval_gate(tmp_path):
    n = Queue()
    n.fail_direction = True
    seen = []
    def run(task, *args):
        seen.append(task)
        return {'status': 'In review', 'result': 'Verified routine repair.'}
    Service(n, tmp_path, '/projects', run).tick()
    assert len(seen) == 1
    assert seen[0]['direction']['status'] == 'unavailable'
    assert 'unavailable' in n.result.lower()
    assert n.row['status'] == 'In review'
    assert 'credential-bearing' not in (tmp_path / 'journal.json').read_text()


def test_recovery_keeps_original_direction_and_never_starts_another_worker(tmp_path):
    n = Queue()
    def crash(*args): raise KeyboardInterrupt()
    with pytest.raises(KeyboardInterrupt):
        Service(n, tmp_path, '/projects', crash).tick()
    n.fail_direction = True
    Service(n, tmp_path, '/projects', lambda *args: pytest.fail('relaunch')).tick()
    assert n.direction_calls == 1
    assert 'fixture-revision' in n.result
    assert n.row['status'] == 'Needs your input'


def test_bounded_direction_read_rejects_repeated_pagination_cursor():
    n = Notion('fixture')
    n.call = lambda *a, **k: {'results': [paragraph('context')], 'has_more': True, 'next_cursor': 'same'}
    from workqueue.notion import NotionError
    with pytest.raises(NotionError): n.children('page', max_blocks=400)
