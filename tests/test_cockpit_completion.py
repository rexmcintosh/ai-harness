"""Completion changes the source status, never approval, publication, or child records."""
from copy import deepcopy
from pathlib import Path
import pytest
import requests
import yaml
from tests.test_cockpit import world,login

PAGE_ID='11111111-1111-4111-8111-111111111111'
DS_ID='22222222-2222-4222-8222-222222222222'

def page(status='To do',kind='Watch',source='engage',key='engage:sample:2026-09-13'):
    def prop(t,v):return {'type':t,t:v}
    return dict(id=PAGE_ID,parent={'type':'data_source_id','data_source_id':DS_ID},archived=False,
        properties={'Name':prop('title',[{'plain_text':'Post the prepared comments'}]),
            'Key':prop('rich_text',[{'plain_text':key}]),'Kind':prop('select',{'name':kind}),
            'Source':prop('select',{'name':source}),'Status':prop('select',{'name':status}),
            'Due':prop('date',{'start':'2026-09-13'})})

@pytest.fixture
def completion(world,monkeypatch):
    from cockpit import completion as module,remote
    app,backlog=world
    app.config.update(REMOTE_READS=True,COMPLETION_ENABLED=True,OPS_LOCK_PATH=str(backlog.parent/'ops.lock'))
    cfg=backlog.parent/'romance-empire/config/ops.yaml';cfg.parent.mkdir(parents=True)
    cfg.write_text(yaml.safe_dump({'notion':{'data_source_id':DS_ID},'hooks':{}}))
    current=page();calls=[]
    monkeypatch.setattr(remote,'_token',lambda *args:'test-scoped-token')
    def request(method,url,**kwargs):
        calls.append((method,url,kwargs.get('json')))
        assert url=='https://api.notion.com/v1/pages/'+PAGE_ID
        if method=='PATCH':current['properties']['Status'][current['properties']['Status']['type']]['name']='Done'
        class Response:
            status_code=200
            def json(self):return deepcopy(current)
        return Response()
    monkeypatch.setattr(module.requests,'request',request)
    return app,backlog,cfg,current,calls


def claim(config,raw):
    from cockpit import completion as module
    return module.offer(config,raw)


@pytest.mark.parametrize('status_type',['select','status'])
def test_complete_updates_only_status_and_verifies_it(completion,status_type):
    from cockpit import completion as module
    app,backlog,cfg,raw,calls=completion;before=backlog.read_bytes()
    raw['properties']['Status']={'type':status_type,status_type:{'name':'To do'}}
    result=module.complete(app.config,claim(app.config,raw))
    assert result['status']=='Done' and result['verified']
    assert [c[0] for c in calls]==['GET','PATCH','GET']
    assert calls[1][2]=={'properties':{'Status':{status_type:{'name':'Done'}}}}
    assert backlog.read_bytes()==before


def test_repeat_is_read_only_and_a_changed_task_is_rejected(completion):
    from cockpit import completion as module
    app,_,_,raw,calls=completion;saved=claim(app.config,raw)
    module.complete(app.config,saved);calls.clear()
    assert module.complete(app.config,saved)['replayed'] and [c[0] for c in calls]==['GET']
    raw['properties']['Status']['select']['name']='To do'
    raw['properties']['Name']['title'][0]['plain_text']='A different task'
    with pytest.raises(module.CompletionError):module.complete(app.config,saved)
    assert not any(c[0]=='PATCH' for c in calls)


@pytest.mark.parametrize('changes',[
    {'kind':'Decision','source':'runner','key':'runner:fix'},
    {'kind':'Task','source':'launch','key':'launch:sample:release-day'},
    {'status':'Approve'},{'status':'Changes'}])
def test_approval_and_launch_work_never_get_a_complete_action(completion,changes):
    app,*_=completion
    assert claim(app.config,page(**changes)) is None


def test_changed_hook_or_parent_cannot_turn_completion_into_an_action(completion):
    from cockpit import completion as module
    app,_,cfg,raw,calls=completion;saved=claim(app.config,raw)
    cfg.write_text(yaml.safe_dump({'notion':{'data_source_id':DS_ID},'hooks':{'engage:*':{'file':'a'}}}))
    with pytest.raises(module.CompletionError):module.complete(app.config,saved)
    assert not any(c[0]=='PATCH' for c in calls)
    cfg.write_text(yaml.safe_dump({'notion':{'data_source_id':DS_ID},'hooks':{}}))
    raw['parent']['data_source_id']='33333333-3333-4333-8333-333333333333'
    with pytest.raises(module.CompletionError):module.complete(app.config,saved)
    assert not any(c[0]=='PATCH' for c in calls)


def test_completion_post_requires_login_csrf_valid_token_and_enable_flag(completion):
    from cockpit import completion as module
    app,_,_,raw,calls=completion
    url='/api/operations/complete'
    c=app.test_client()
    assert c.post(url,json={}).status_code==401
    csrf=login(c)
    token=module.signer(app.config).dumps(claim(app.config,raw))
    assert c.post(url,json={'token':token}).status_code==403
    assert c.post(url,json={'token':'forged'},headers={'X-CSRF-Token':csrf}).status_code==409
    app.config['COMPLETION_ENABLED']=False
    assert c.post(url,json={'token':token},headers={'X-CSRF-Token':csrf}).status_code==403
    app.config['COMPLETION_ENABLED']=True
    assert c.post(url,json={'token':token},headers={'X-CSRF-Token':csrf}).status_code==200


def test_uncertain_patch_is_verified_without_resending(completion,monkeypatch):
    from cockpit import completion as module
    app,_,_,raw,calls=completion
    original=module.requests.request
    def uncertain(method,url,**kw):
        response=original(method,url,**kw)
        if method=='PATCH':raise requests.Timeout('response lost')
        return response
    monkeypatch.setattr(module.requests,'request',uncertain)
    assert module.complete(app.config,claim(app.config,raw))['verified']
    assert [c[0] for c in calls].count('PATCH')==1


def test_failure_logs_status_without_source_content_or_credentials(completion,monkeypatch,caplog):
    from cockpit import completion as module
    app,_,_,raw,_=completion
    class Denied:
        status_code=403
    monkeypatch.setattr(module.requests,'request',lambda *args,**kw:Denied())
    with pytest.raises(module.CompletionError):module.complete(app.config,claim(app.config,raw))
    assert 'source_read status=403' in caplog.text
    assert 'test-scoped-token' not in caplog.text and PAGE_ID not in caplog.text


def test_weekly_numbers_ritual_uses_social_source(completion):
    app,*_=completion
    assert claim(app.config,page(kind='Task',source='social',key='ritual:sunday:2026-09-13'))
    assert claim(app.config,page(kind='Task',source='engage',key='ritual:sunday:2026-09-13')) is None


@pytest.mark.parametrize('bad_cfg',[None,{}, {'notion':{'data_source_id':'wrong'}}, {'notion':{'data_source_id':DS_ID},'hooks':None}])
def test_invalid_settings_are_visible_and_prevent_source_calls(completion,bad_cfg,caplog):
    from cockpit import completion as module
    app,_,cfg,raw,calls=completion;saved=claim(app.config,raw)
    cfg.write_text(yaml.safe_dump(bad_cfg))
    assert claim(app.config,raw) is None
    assert 'reason=settings_or_source_invalid' in caplog.text
    with pytest.raises(module.CompletionError,match='settings need repair'):
        module.complete(app.config,saved)
    assert not calls


def test_rejected_patch_is_verified_without_retry(completion,monkeypatch):
    from cockpit import completion as module
    app,_,_,raw,calls=completion;original=module.requests.request
    def rejected(method,url,**kw):
        if method=='PATCH':
            calls.append((method,url,kw.get('json')))
            class Response:status_code=429
            return Response()
        return original(method,url,**kw)
    monkeypatch.setattr(module.requests,'request',rejected)
    with pytest.raises(module.CompletionError,match='Notion is busy'):
        module.complete(app.config,claim(app.config,raw))
    assert [c[0] for c in calls]==['GET','PATCH','GET']


def test_unconfirmed_write_keeps_task_open_without_retry(completion,monkeypatch):
    from cockpit import completion as module
    app,_,_,raw,calls=completion
    original=module.requests.request
    def failed_write(method,url,**kw):
        if method=='PATCH':
            calls.append((method,url,kw.get('json')))
            raise requests.Timeout('not sent')
        return original(method,url,**kw)
    monkeypatch.setattr(module.requests,'request',failed_write)
    with pytest.raises(module.CompletionError,match='not confirmed'):
        module.complete(app.config,claim(app.config,raw))
    assert raw['properties']['Status']['select']['name']=='To do'
    assert [c[0] for c in calls]==['GET','PATCH','GET']


def test_busy_ops_lock_prevents_source_calls(completion):
    from cockpit import completion as module
    app,_,_,raw,calls=completion;saved=claim(app.config,raw)
    with module.ops_lock(app.config):
        with pytest.raises(BlockingIOError):module.complete(app.config,saved)
    assert not calls


def test_snapshot_completion_moves_only_today_out_of_active_work(completion,monkeypatch):
    from datetime import datetime,timedelta
    from zoneinfo import ZoneInfo
    from cockpit import remote
    app,_,_,raw,calls=completion
    today=datetime.now(ZoneInfo('Europe/Lisbon')).date()
    tomorrow=today+timedelta(days=1)
    raw['properties']['Due']['date']['start']=str(today)
    future=page(key='engage:sample:'+str(tomorrow));future['id']='33333333-3333-4333-8333-333333333333'
    future['properties']['Due']['date']['start']=str(tomorrow)
    monkeypatch.setattr(remote,'_query',lambda *args:[deepcopy(raw),deepcopy(future)])
    c=app.test_client();csrf=login(c)
    app.config['COMPLETION_ENABLED']=False
    assert not any(x.get('complete_token') for x in c.get('/api/snapshot').json['work'])
    app.config['COMPLETION_ENABLED']=True
    before=c.get('/api/snapshot').json
    assert before['mode']=='Owner controls enabled'
    current=before['brief']['operations'][0]
    assert current['complete_token']
    response=c.post('/api/operations/complete',json={'token':current['complete_token']},headers={'X-CSRF-Token':csrf})
    assert response.status_code==200
    after=c.get('/api/snapshot').json
    assert current['id'] not in [x['id'] for x in after['work']]
    assert after['brief']['operations']==[]
    assert len(after['brief']['upcoming_operations'])==1
    assert any(x['id']==current['id'] and x['status']=='Done' for x in after['results'])
    assert not any(x.get('complete_token') or x.get('_completion_claim') for x in after['results'])
