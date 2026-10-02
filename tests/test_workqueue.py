from copy import deepcopy
from pathlib import Path
import pytest
from workqueue.service import Service


def task(status='Ready', **kw):
    return dict(id='a'*32, title='Investigate pause', status=status, mode='Investigate',
                brief='Inspect Diagnostic.tsx and explain pause/resume behavior.',
                done_when='A recommendation supported by source and reproduction.', checks=[],
                repo='sat-prep', source_url='https://notion.so/task', run_id='', **kw)


class FakeNotion:
    def __init__(self, row):
        self.row = deepcopy(row)
        self.results = {}
        self.fail_result = False
    def tasks(self): return [deepcopy(self.row)]
    def get_task(self, page_id): return deepcopy(self.row)
    def get_task_meta(self, page_id): return deepcopy(self.row)
    def update_task(self, page_id, **fields): self.row.update(fields)
    def append_result(self, page_id, run_id, text):
        if self.fail_result: raise OSError('Notion offline')
        self.results.setdefault(run_id, text)
    def health(self, text): self.health_text = text


def setup(tmp_path, row=None, runner=None):
    n = FakeNotion(row or task())
    calls = []
    def run(t, rid, state_dir, projects):
        calls.append((t, rid))
        return dict(status='In review', result='Observed behavior and recommendation.',
                    session='session-1', branch='claude/test', readiness='unknown', cost_usd=0,
                    resume_command='claude --resume session-1')
    s = Service(n, tmp_path, '/projects', runner or run)
    return s, n, calls


def test_draft_never_launches(tmp_path):
    s,n,calls=setup(tmp_path,task('Draft'))
    s.tick(); assert calls==[] and n.row['status']=='Draft'


def test_ready_launches_once_and_publishes(tmp_path):
    s,n,calls=setup(tmp_path)
    s.tick();s.tick()
    assert len(calls)==1 and n.row['status']=='In review'
    assert len(n.results)==1 and n.row['session']=='session-1'
    assert n.row['run_id']==calls[0][1]


def test_missing_brief_blocks_without_launch(tmp_path):
    s,n,calls=setup(tmp_path)
    n.row['brief']=''
    s.tick()
    assert calls==[] and n.row['status']=='Needs your input'
    assert 'Brief' in n.row['result']


def test_build_needs_explicit_checks(tmp_path):
    s,n,calls=setup(tmp_path)
    n.row['mode']='Build'
    s.tick()
    assert calls==[] and 'Checks' in n.row['result']


def test_result_outage_recovers_without_rerun(tmp_path):
    s,n,calls=setup(tmp_path)
    n.fail_result=True
    with pytest.raises(OSError):s.tick()
    n.fail_result=False
    s.tick()
    assert len(calls)==1 and n.row['status']=='In review' and len(n.results)==1


def test_user_edit_during_run_preserved(tmp_path):
    s,n,calls=setup(tmp_path)
    def run(*args):
        n.row['status']='Draft'; n.row['brief']='Changed direction'
        return dict(status='In review',result='Old brief findings')
    s.runner=run
    s.tick();s.tick()
    assert n.row['status']=='Draft' and n.row['brief']=='Changed direction'
    assert 'Old brief findings' in next(iter(n.results.values()))


def test_brief_edit_during_run_requires_input(tmp_path):
    s,n,calls=setup(tmp_path)
    def run(*args):
        n.row['brief']='Changed direction'
        return dict(status='In review',result='Old brief findings')
    s.runner=run;s.tick()
    assert n.row['status']=='Needs your input'
    assert 'changed' in n.row['result'].lower()


def test_explicit_requeue_creates_new_run(tmp_path):
    s,n,calls=setup(tmp_path)
    s.tick();n.row['status']='Ready';s.tick()
    assert len(calls)==2 and calls[0][1]!=calls[1][1] and len(n.results)==2


def test_crash_after_launch_never_relaunches(tmp_path):
    s,n,calls=setup(tmp_path)
    def crash(*args):raise KeyboardInterrupt()
    s.runner=crash
    with pytest.raises(KeyboardInterrupt):s.tick()
    s2=Service(n,tmp_path,'/projects',lambda *a:pytest.fail('duplicate launch'))
    s2.tick()
    assert n.row['status']=='Needs your input'
    assert 'interrupted' in n.row['result'].lower()


def test_ambiguous_claim_failure_never_launches(tmp_path):
    s,n,calls=setup(tmp_path)
    original=n.update_task
    def update(*args,**kwargs):
        original(*args,**kwargs)
        if kwargs.get('status')=='Working':raise OSError('Lost acknowledgement')
    n.update_task=update
    with pytest.raises(OSError):s.tick()
    n.update_task=original;s.tick()
    assert calls==[] and n.row['status']=='Needs your input'


def test_claim_reread_sees_changed_brief_and_aborts(tmp_path):
    s,n,calls=setup(tmp_path)
    original=n.update_task
    def update(*args,**kwargs):
        original(*args,**kwargs)
        if kwargs.get('status')=='Working':n.row['brief']='New brief'
    n.update_task=update;s.tick()
    assert calls==[] and n.row['status']=='Needs your input'


def test_unexpected_runner_exception_becomes_actionable_result(tmp_path):
    s,n,calls=setup(tmp_path)
    s.runner=lambda *a:(_ for _ in ()).throw(RuntimeError('fixture failure'))
    s.tick()
    assert n.row['status']=='Needs your input' and 'failed' in n.row['result'].lower()


def test_non_allowed_repo_and_unknown_mode_block(tmp_path):
    for key,val in [('repo','../other'),('mode','Deploy')]:
        s,n,calls=setup(tmp_path/key);n.row[key]=val;s.tick()
        assert calls==[] and n.row['status']=='Needs your input'


def test_notion_result_recovers_lost_ack_without_duplicate():
    from workqueue.notion import Notion
    class Client(Notion):
        def __init__(self): self.blocks=[];self.lose=True
        def children(self,pid): return self.blocks
        def call(self,method,path,payload=None):
            self.blocks += deepcopy(payload['children'])
            if self.lose:
                self.lose=False
                raise OSError('lost acknowledgement')
    c=Client()
    with pytest.raises(OSError):c.append_result('pid','run','x'*3100)
    c.append_result('pid','run','x'*3100)
    assert len(c.blocks)==3


def test_notion_task_parsing_and_checks():
    from workqueue.notion import parse_task
    props={name:{'type':'rich_text','rich_text':[{'plain_text':value}]} for name,value in {'Task':'Title','Brief':'Scope','Done when':'Done','Checks':'npm test\n npm run build:market','Run ID':''}.items()}
    props.update({n:{'select':{'name':v}} for n,v in {'Status':'Ready','Mode':'Build','Repository':'sat-prep'}.items()})
    t=parse_task({'id':'pid','url':'https://notion.so/pid','properties':props})
    assert t['checks']==['npm test','npm run build:market'] and t['mode']=='Build'


def test_feedback_edits_alone_are_not_trigger_fields(tmp_path):
    s,n,calls=setup(tmp_path,task('Draft'))
    n.row['feedback']=['Additional parent evidence']
    s.tick(); assert calls==[]


def test_publication_text_is_stable_if_user_edits_during_retry(tmp_path):
    s,n,calls=setup(tmp_path)
    attempts=[]
    def append(pid,rid,text):
        attempts.append(text)
        if len(attempts)==1:raise OSError('lost acknowledgement')
        n.results[rid]=text
    n.append_result=append
    with pytest.raises(OSError):s.tick()
    n.row['brief']='Changed while publication pending'
    s.tick()
    assert attempts[0]==attempts[1]
    assert n.row['status']=='Needs your input'


def test_cli_lock_prevents_overlapping_run(tmp_path, monkeypatch, capsys):
    import fcntl,json,sys
    from workqueue import cli
    state=tmp_path/'state';state.mkdir()
    config=tmp_path/'config.json';config.write_text(json.dumps({'state_dir':str(state)}))
    with (state/'controller.lock').open('w') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        monkeypatch.setattr(sys,'argv',['queue','--config',str(config),'tick'])
        assert cli.main()==0
    assert 'already active' in capsys.readouterr().out
    assert not (state/'journal.json').exists()


def test_only_scoped_notion_token_is_accepted(tmp_path):
    from workqueue.notion import read_token,NotionError
    env=tmp_path/'env'
    env.write_text('NOTION_TOKEN=wrong-scope\n')
    with pytest.raises(NotionError):read_token(env)
    env.write_text('NOTION_TOKEN=wrong-scope\nNOTION_TOKEN_ATTAINPREP="fixture-scoped" # comment\n')
    assert read_token(env)=='fixture-scoped'


def test_corrupt_journal_fails_closed(tmp_path):
    (tmp_path/'journal.json').write_text('{bad json')
    import json
    with pytest.raises(json.JSONDecodeError):setup(tmp_path)
    assert (tmp_path/'journal.json').read_text()=='{bad json'


def test_publication_does_not_depend_on_feedback_access(tmp_path):
    s,n,calls=setup(tmp_path)
    original=n.get_task
    n.get_task_meta=original
    def run(*args):
        n.get_task=lambda *a:(_ for _ in ()).throw(OSError('Feedback access removed'))
        return dict(status='In review',result='Completed evidence')
    s.runner=run;s.tick()
    assert n.row['status']=='In review'


def test_setup_schema_checks_types_and_options():
    from workqueue.setup import validate_schema,schema
    expected=schema()
    existing={k:{'type':next(iter(v)),**v} for k,v in expected.items()}
    validate_schema(existing)
    existing['Brief']['type']='number'
    with pytest.raises(RuntimeError):validate_schema(existing)
    existing['Brief']['type']='rich_text'
    existing['Status']['select']['options']=[{'name':'Draft'}]
    with pytest.raises(RuntimeError):validate_schema(existing)


def test_recovery_adopts_finished_runner_response(tmp_path):
    import json
    s,n,calls=setup(tmp_path)
    def finish_then_crash(task,rid,state_dir,projects):
        marker=Path(state_dir)/'queue-runs'/f'{rid}.json';marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({'run_id':rid,'state':'finished','response':{'status':'In review','result':'Finished findings survived the crash','session':'saved-session'}}))
        raise KeyboardInterrupt()
    s.runner=finish_then_crash
    with pytest.raises(KeyboardInterrupt):s.tick()
    Service(n,tmp_path,'/projects',lambda *a:pytest.fail('must not relaunch')).tick()
    assert n.row['status']=='In review' and n.row['session']=='saved-session'
    assert 'Finished findings survived' in next(iter(n.results.values()))


def test_recovery_rejects_wrong_run_finished_marker(tmp_path):
    import json
    s,n,calls=setup(tmp_path)
    def crash(task,rid,state_dir,projects):
        marker=Path(state_dir)/'queue-runs'/f'{rid}.json';marker.parent.mkdir(parents=True)
        marker.write_text(json.dumps({'run_id':'wrong-run','state':'finished','response':{'status':'In review','result':'Wrong result'}}))
        raise KeyboardInterrupt()
    s.runner=crash
    with pytest.raises(KeyboardInterrupt):s.tick()
    Service(n,tmp_path,'/projects',lambda *a:pytest.fail('must not relaunch')).tick()
    assert n.row['status']=='Needs your input' and 'Wrong result' not in next(iter(n.results.values()))
