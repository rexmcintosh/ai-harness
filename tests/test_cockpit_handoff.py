"""A copied prompt carries current evidence without starting or approving work."""
from pathlib import Path

import yaml

from tests.test_cockpit import world, login

URL='/api/work-prompt/backlog:item-1'


def test_prompt_reads_exact_current_task_and_full_review_without_writes(world):
    app,backlog=world
    raw=yaml.safe_load(backlog.read_text())
    raw['items'][0].update(prompt='Repair the pause timer without losing progress.',branch='claude/existing-fix')
    backlog.write_text(yaml.safe_dump(raw))
    review=Path(app.config['STATE_ROOT'])/'reviews/item-1.md';review.parent.mkdir(parents=True)
    review.write_text('Checks pass.\n'+'Review detail. '*80+'MUST preserve saved student progress.')
    before=backlog.read_bytes()
    assert app.test_client().get(URL).status_code==401
    c=app.test_client();login(c)
    response=c.get(URL)
    assert response.status_code==200
    prompt=response.json['prompt']
    assert 'Repair the pause timer' in prompt and 'MUST preserve saved student progress.' in prompt
    assert 'claude/existing-fix' in prompt and 'backlog:item-1' in prompt
    assert str(backlog) in prompt and 'Copying this prompt does not approve' in prompt
    assert 'hold_token' not in prompt and 'complete_token' not in prompt
    assert backlog.read_bytes()==before
    assert not (backlog.parent/'.cockpit/discussions').exists()
    raw['items'][0]['prompt']='A newer task scope';backlog.write_text(yaml.safe_dump(raw))
    assert 'A newer task scope' in c.get(URL).json['prompt']


def test_missing_or_ambiguous_work_never_copies_another_task(world):
    app,backlog=world;c=app.test_client();login(c)
    assert c.get('/api/work-prompt/backlog:missing').status_code==404
    data=yaml.safe_load(backlog.read_text());data['items'].append(dict(data['items'][0]))
    backlog.write_text(yaml.safe_dump(data))
    assert c.get(URL).status_code==409


def test_remote_routine_prompt_has_context_status_source_and_no_action_token(world,monkeypatch):
    from cockpit import remote
    app,_=world;app.config['REMOTE_READS']=True
    monkeypatch.setattr(remote,'remote_work',lambda cfg:([dict(id='ops:engage:sample:today',
        title='Daily comments',status='To do',why='Post the prepared comments.',repo='romance-empire',
        source='Romance Ops',source_type='engage',source_url='https://www.notion.so/task',
        action_url='https://www.notion.so/drafts',due='2026-09-13',_completion_claim={'secret':'never-copy'})],[]))
    c=app.test_client();login(c)
    snap=c.get('/api/snapshot').json
    row=next(r for r in snap['work'] if r['source']=='Romance Ops')
    response=c.get(row['prompt_url']);assert response.status_code==200
    prompt=response.json['prompt']
    assert 'Daily comments' in prompt and 'Romance Ops:ops:engage:sample:today' in prompt
    assert 'https://www.notion.so/drafts' in prompt and 'To do' in prompt
    assert 'never-copy' not in prompt and '_completion_claim' not in prompt


def test_unreadable_or_long_review_is_explicit_and_never_called_clean(world):
    app,_=world;c=app.test_client();login(c)
    review=Path(app.config['STATE_ROOT'])/'reviews/item-1.md';review.parent.mkdir(parents=True)
    review.write_text('Required review condition. '*1000)
    prompt=c.get(URL).json['prompt']
    assert 'not included' in prompt and str(review) in prompt
    review.unlink();review.symlink_to(review.parent/'absent')
    assert 'unavailable' in c.get(URL).json['prompt']


def test_embedded_chat_and_start_routes_are_removed(world):
    app,_=world;c=app.test_client();csrf=login(c)
    assert c.get('/discuss/backlog:item-1').status_code==404
    assert c.post('/api/discussions/backlog:item-1/start',json={},headers={'X-CSRF-Token':csrf}).status_code==404


def test_completed_archive_item_keeps_its_outcome_and_yaml_date(world):
    from datetime import date
    app,backlog=world;c=app.test_client();login(c)
    raw=yaml.safe_load(backlog.read_text());raw['items'][0].update(status='done',merged=date(2026,9,13),merge_commit='abc123')
    Path(app.config['ARCHIVE_PATH']).write_text(yaml.safe_dump(raw))
    backlog.write_text('items: []\n')
    response=c.get(URL);assert response.status_code==200
    prompt=response.json['prompt']
    assert 'Backlog archive' in prompt and '2026-09-13' in prompt and 'abc123' in prompt
