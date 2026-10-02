"""Explicit owner completion of routine tasks, written to their existing source."""
from contextlib import contextmanager
import fcntl
from fnmatch import fnmatchcase
import logging
import os
from pathlib import Path
from uuid import UUID

from itsdangerous import URLSafeTimedSerializer
import requests
import yaml

from . import remote
from .actions import revision

VERSION='2025-09-03'
SOURCE_PREFIXES={'engage':('engage:',),'social':('social:','ritual:'),'notion':('notion:',)}
KINDS={'Task','Watch','Deadline'}
OPEN={'To do','Missed','Blocked'}
LOG=logging.getLogger(__name__)


class CompletionError(ValueError):
    def __init__(self,message,status=409):
        super().__init__(message);self.status=status


def signer(config):
    return URLSafeTimedSerializer(config['SECRET_KEY'],salt='cockpit-complete-routine-v1')


def validate_settings(cfg):
    try:
        if not isinstance(cfg,dict) or not isinstance(cfg.get('notion'),dict):raise ValueError()
        UUID(cfg['notion']['data_source_id'])
        hooks=cfg.get('hooks',{})
        if not isinstance(hooks,dict) or not all(isinstance(k,str) for k in hooks):raise ValueError()
    except (ValueError,TypeError,KeyError,AttributeError):
        raise CompletionError('Completion settings need repair. Use the task’s source to update it.',503) from None
    return cfg


def settings(config):
    p=Path(config['PROJECTS_ROOT'])/'romance-empire/config/ops.yaml'
    try:
        if p.stat().st_size>100000:raise ValueError()
        cfg=yaml.safe_load(p.read_text())
    except (OSError,ValueError,yaml.YAMLError):
        raise CompletionError('Completion settings could not be read. Use the task’s source to update it.',503) from None
    return validate_settings(cfg)


def facts(page):
    return {'page_id':str(UUID(page['id'])),
            **{name:remote._value(page,name) for name in
               ('Name','Key','Kind','Source','Status','Due','Comment','Why','Evidence','Source link')}}


def eligible(page,cfg,*,closed=False):
    try:
        parent=page['parent']
        if (parent.get('type')!='data_source_id'
            or UUID(parent['data_source_id'])!=UUID(cfg['notion']['data_source_id'])
            or page.get('archived') or page.get('in_trash') or page.get('is_archived')):return False
        f=facts(page)
        return (f['Source'] in SOURCE_PREFIXES and f['Kind'] in KINDS
            and f['Key'].startswith(SOURCE_PREFIXES[f['Source']])
            and f['Status'] in (OPEN|{'Done'} if closed else OPEN)
            and not any(fnmatchcase(f['Key'],pattern) for pattern in cfg.get('hooks',{})))
    except (ValueError,KeyError,TypeError,AttributeError):return False


def offer(config,page,*,cfg=None):
    try:
        cfg=validate_settings(cfg) if cfg is not None else settings(config)
        if not eligible(page,cfg):return None
        f=facts(page)
        return dict(page_id=f['page_id'],key=f['Key'],revision=revision(f),
                    rules=revision({'source':cfg['notion']['data_source_id'],'hooks':cfg.get('hooks',{})}))
    except (OSError,ValueError,TypeError,yaml.YAMLError) as exc:
        LOG.warning('completion unavailable reason=settings_or_source_invalid type=%s',type(exc).__name__)
        return None


@contextmanager
def ops_lock(config):
    # Same file and flock protocol as the existing Romance Ops poller.
    path=Path(config.get('OPS_LOCK_PATH') or Path.home()/'.local/state/ops/lock')
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    fd=os.open(path,os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def complete(config,claim):
    if not config.get('COMPLETION_ENABLED') or not config.get('REMOTE_READS'):
        raise CompletionError('Source completion is not enabled in this cockpit.',403)
    if not isinstance(claim,dict) or set(claim)!={'page_id','key','revision','rules'}:
        raise CompletionError('Refresh the task before marking it complete.')
    try: page_id=str(UUID(claim['page_id']))
    except (ValueError,TypeError,AttributeError):raise CompletionError('Invalid task identity.') from None
    with ops_lock(config):
        cfg=settings(config)
        expected_rules=revision({'source':cfg['notion']['data_source_id'],'hooks':cfg.get('hooks',{})})
        if claim['rules']!=expected_rules:raise CompletionError('The source completion rules changed. Refresh the task.')
        token=remote._token(config.get('ENV_FILE',str(Path.home()/'.env')),'NOTION_TOKEN')
        url='https://api.notion.com/v1/pages/'+page_id
        headers={'Authorization':'Bearer '+token,'Notion-Version':VERSION}
        def read():
            response=requests.request('GET',url,headers=headers,timeout=(3,8),allow_redirects=False)
            if response.status_code!=200:
                LOG.warning('completion source_read status=%s',response.status_code)
                raise CompletionError('The source task could not be checked. Refresh before retrying.',503)
            page=response.json()
            if (not eligible(page,cfg,closed=True) or facts(page)['page_id']!=page_id
                or remote._value(page,'Key')!=claim['key']):
                raise CompletionError('This task changed or needs its source-specific workflow. Nothing was marked complete.')
            return page
        page=read()
        if remote._value(page,'Status')=='Done':
            LOG.info('completion verified already_done')
            return dict(status='Done',verified=True,replayed=True)
        if revision(facts(page))!=claim['revision']:
            raise CompletionError('The task changed. Refresh and read it before marking it complete.')
        kind=page['properties']['Status']['type']
        if kind not in ('select','status'):raise CompletionError('The source status field is unsupported.')
        # A completion never sends Approve/Run now, changes child target statuses, or writes comments.
        write_status=None
        try:
            response=requests.request('PATCH',url,headers=headers,json={'properties':{'Status':{kind:{'name':'Done'}}}},
                                      timeout=(3,8),allow_redirects=False)
            write_status=response.status_code
            LOG.log(logging.INFO if response.status_code==200 else logging.WARNING,
                    'completion source_write status=%s',response.status_code)
        except requests.RequestException as exc:
            LOG.warning('completion source_write uncertain transport=%s',type(exc).__name__)
            # A lost response is not proof that the write failed. Read once; never retry PATCH here.
        try: verified=read()
        except (requests.RequestException,CompletionError,ValueError) as exc:
            LOG.warning('completion verification_failed type=%s',type(exc).__name__)
            raise CompletionError('Completion could not be confirmed. Refresh before trying again.',503) from None
        if remote._value(verified,'Status')!='Done':
            LOG.warning('completion verification_failed status_not_done')
            if write_status in (401,403):
                raise CompletionError('Notion denied the completion update. Check the source connection permissions.',503)
            if write_status==429:
                raise CompletionError('Notion is busy. Completion was not confirmed; refresh before retrying.',503)
            raise CompletionError('The source has not confirmed completion. Refresh before trying again.',503)
        LOG.info('completion verified done')
        return dict(status='Done',verified=True,replayed=False)
