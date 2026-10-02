"""Owner explanations must stay tied to evidence; recurring work must surface when due."""
import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from tests.test_cockpit import world, login


def test_explanation_tracks_full_task_and_review_without_rewriting_work(world, tmp_path):
    app, backlog = world
    app.config['CONTEXT_PATH'] = str(tmp_path / 'context.json')
    row = yaml.safe_load(backlog.read_text())['items'][0]
    review = Path(app.config['STATE_ROOT']) / 'reviews' / (row['id'] + '.md')
    review.parent.mkdir(parents=True, exist_ok=True)
    review.write_text('Full review: resolve the missing safeguard before merge.')
    c = app.test_client(); login(c)
    first = c.get('/api/snapshot').json['work'][0]
    assert first['owner_brief']['state'] == 'missing'
    entry = dict(source_revision=first['context_revision'], title='Prevent repeat video charges',
                 context='Romance / Social promotion / Videos', why='A paid clip can be ordered twice.',
                 progress='The saved review requires another safeguard.', next_step='Have the system resolve the review points.', as_of='2026-09-13')
    Path(app.config['CONTEXT_PATH']).write_text(json.dumps({'version':1,'items':{'backlog:'+row['id']:entry}}))
    before = backlog.read_bytes()
    current = c.get('/api/snapshot').json['work'][0]
    assert current['owner_brief']['state'] == 'current'
    assert current['owner_brief']['title'] == 'Prevent repeat video charges'
    assert backlog.read_bytes() == before
    detail = c.get('/work/'+row['id'])
    assert detail.status_code == 200
    assert b'Full review: resolve the missing safeguard' in detail.data
    # An updated full review must invalidate a previously correct recommendation.
    review.write_text('Full review: the previous finding was withdrawn.')
    changed = c.get('/api/snapshot').json['work'][0]
    assert changed['owner_brief']['state'] == 'stale'
    assert 'resolve the review points' not in changed['owner_brief']['next_step']


def test_bad_explanation_file_preserves_work(world, tmp_path):
    app, _ = world
    path=tmp_path/'context.json'; path.write_text('{bad')
    app.config['CONTEXT_PATH']=str(path)
    c=app.test_client(); login(c)
    d=c.get('/api/snapshot').json
    assert d['work'] and d['work'][0]['owner_brief']['state']=='missing'
    assert any(s['name']=='Work explanations' for s in d['brief']['source_issues'])


def test_changed_explanation_requires_reading_again_before_acknowledgement(world, tmp_path):
    app,_=world
    path=tmp_path/'context.json'; app.config['CONTEXT_PATH']=str(path)
    c=app.test_client();csrf=login(c)
    d=c.get('/api/snapshot').json; row=d['work'][0]
    path.write_text(json.dumps({'version':1,'items':{'backlog:'+row['id']:dict(
        source_revision=row['context_revision'],title='Understand a new recommendation',context='Shared tools',
        why='A source review found a different next step.',progress='Explanation now prepared.',
        next_step='Read the updated recommendation.',as_of='2026-09-13')}}))
    assert c.post('/api/brief/review',json={'token':d['brief']['review_token']},headers={'X-CSRF-Token':csrf}).status_code==409


def test_older_checkpoint_does_not_call_all_work_changed_after_display_upgrade(world, tmp_path):
    app,_=world
    app.config['BRIEF_STATE_PATH']=str(tmp_path/'review.json')
    c=app.test_client();csrf=login(c)
    d=c.get('/api/snapshot').json
    assert c.post('/api/brief/review',json={'token':d['brief']['review_token']},headers={'X-CSRF-Token':csrf}).status_code==200
    path=Path(app.config['BRIEF_STATE_PATH']); saved=json.loads(path.read_text())
    for record in saved['records'].values(): record.pop('detail_signature',None)
    path.write_text(json.dumps(saved))
    assert c.get('/api/snapshot').json['brief']['changes']==[]


def test_daily_tiktok_work_returns_each_day_and_future_routines_stay_separate(world, monkeypatch):
    from cockpit import remote
    app, _ = world
    app.config['REMOTE_READS']=True
    today=datetime.now(ZoneInfo('Europe/Lisbon')).date()
    tomorrow=today+timedelta(days=1)
    yesterday=today-timedelta(days=1)
    def row(day, status='To do'):
        return dict(id=f'ops:engage:heron-creek:{day}',title='Engage: 2 To do · 0 bench',why='Post the prepared comments.',
                    repo='romance-empire',source='Romance Ops',source_type='engage',status=status,due=str(day),
                    action_url='https://www.notion.so/prepared-drafts',source_url='https://www.notion.so/owner-task',
                    is_terminal=status=='Done',due_timezone='Europe/Lisbon')
    rows=[row(yesterday,'Done'),row(today),row(tomorrow)]
    monkeypatch.setattr(remote,'remote_work',lambda cfg:([dict(r) for r in rows],[dict(name='Romance Ops',status='available')]))
    c=app.test_client();csrf=login(c)
    d=c.get('/api/snapshot').json
    assert [r['due'] for r in d['brief']['operations']]==[str(today)]
    operation=d['brief']['operations'][0]
    assert operation['work_type']=='operate' and operation['due_label']=='Due today'
    assert 'TikTok' in operation['owner_brief']['title']
    assert operation['action_url'].endswith('prepared-drafts')
    assert [r['due'] for r in d['brief']['upcoming_operations']]==[str(tomorrow)]
    assert operation['id'] not in [r['id'] for r in d['brief']['attention']]
    assert c.post('/api/brief/review',json={'token':d['brief']['review_token']},headers={'X-CSRF-Token':csrf}).status_code==200
    assert c.get('/api/snapshot').json['brief']['operations'][0]['id']==operation['id']


def test_remote_feed_keeps_due_date_and_direct_task_link(world, tmp_path, monkeypatch):
    from cockpit import remote
    app,_=world
    cfg=tmp_path/'romance-empire/config'; cfg.mkdir(parents=True)
    (cfg/'ops.yaml').write_text('notion: {data_source_id: fixture}\ntimezone: Europe/Lisbon\n')
    env=tmp_path/'env';env.write_text('NOTION_TOKEN=fixture\n')
    def prop(kind,value): return {'type':kind,kind:value}
    page=dict(id='page',url='https://www.notion.so/ops',properties={
        'Name':prop('title',[{'plain_text':'Engage: 2 To do · 0 bench'}]),
        'Key':prop('rich_text',[{'plain_text':'engage:heron-creek:2026-09-13'}]),
        'Status':prop('select',{'name':'To do'}),'Source':prop('select',{'name':'engage'}),
        'Due':prop('date',{'start':'2026-09-13'}),'Source link':prop('url','https://www.notion.so/drafts')})
    monkeypatch.setattr(remote,'_query',lambda *args:[page])
    rows,_=remote.remote_work({**app.config,'PROJECTS_ROOT':str(tmp_path),'ENV_FILE':str(env)})
    assert len(rows)==1
    assert rows[0]['due']=='2026-09-13' and rows[0]['action_url'].endswith('/drafts')
    assert rows[0]['source_type']=='engage'
