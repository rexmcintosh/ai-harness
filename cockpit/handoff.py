"""Build a task-specific chat prompt from existing records. No model or writes."""
from pathlib import Path
import json
import re

from . import briefing, sources


class HandoffError(ValueError):
    def __init__(self,message,status=409):
        super().__init__(message);self.status=status


def section(label,value):
    value=str(value or 'Not recorded.')
    if len(value)>12000:
        value='Full text not included because it exceeds 12,000 characters. Read the complete source before deciding.'
    return label+':\n'+value


def build(config,key):
    if not isinstance(key,str) or not 0<len(key)<=300:
        raise HandoffError('This work item could not be identified.',404)
    data=sources.snapshot(config)
    matches=[row for row in data['work']+data['results'] if briefing.record_key(row)==key]
    if not matches:raise HandoffError('This item is unavailable. Refresh the cockpit before copying.',404)
    if len(matches)!=1:raise HandoffError('More than one source record has this identity. Resolve the source conflict first.')
    row=matches[0];raw={};review='Not recorded or unavailable.';references=[]
    if key.startswith('backlog:'):
        records=sources.work_evidence(config,row['id'])
        if len(records)!=1:raise HandoffError('The original work record is missing or ambiguous.')
        origin,evidence=records[0];raw=evidence['original_record']
        row={**row,'source':origin,**{k:evidence[k] for k in ('title','status','repo','owner_brief') if k in evidence}}
        review=evidence['full_review'] or review
        references.append('Source file on VPS: '+str(config['BACKLOG_PATH'] if origin=='Shared backlog' else config['ARCHIVE_PATH']))
        if re.fullmatch(r'[a-z0-9][a-z0-9-]*',str(row['id'])):
            references.append('Full review on VPS: '+str(Path(config['STATE_ROOT'])/'reviews'/(row['id']+'.md')))
    repo=str(row.get('repo') or '')
    if re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]*',repo):
        references.append('Repository on VPS: '+str(Path(config['PROJECTS_ROOT'])/repo))
    direction=next((i for i in data['initiatives'] if repo in i.get('repos',[])),{})
    brief=row.get('owner_brief',{})
    context={k:brief.get(k) for k in ('state','context','why','progress','as_of')}
    if brief.get('state')=='current':context['recommended_next_step']=brief.get('next_step')
    status={k:row.get(k) for k in ('status','owner_surface_status','due','source','work_type') if row.get(k)}
    work={k:raw[k] for k in ('title','prompt','note','result','branch','worktree','base','required_validations',
        'reviewed_sha','readiness','merged','merge_commit','deployment_followup') if raw.get(k)}
    if not raw:work={'title':row['title'],'source_detail':row.get('why'),'next_action':row.get('next_action')}
    origin=config.get('PUBLIC_ORIGIN','').rstrip('/')
    for label,url in (('Source',row.get('source_url')),('Prepared work',row.get('action_url')),('Ideal State',direction.get('ideal_url'))):
        if url:references.append(label+': '+(origin+url if url.startswith('/') else url))
    title=brief.get('title') or row['title']
    parts=[
        'Help me discuss this cockpit item and agree the next step: '+title,
        'First explain where this sits, why it matters, what is already done, and what decision remains. '
        'Recommend the smallest useful next step and ask only questions that materially change it. '
        'Copying this prompt does not approve implementation, spend, merging, publishing, or a source-status change.',
        'Use the current repository working agreement and Delegate/Venice policy when available. '
        'Check the original source and any existing branch before proposing new work. '
        'If this chat cannot access those sources, say what is missing. '
        'When we authorize and complete work, record its result in the existing source so the cockpit can reflect it.',
        'Item key: '+key+'\nCopied from source at: '+data['generated_at'],
        section('Current status',json.dumps(status,ensure_ascii=False,indent=2)),
        section('Business context',json.dumps(context,ensure_ascii=False,indent=2)),
        section('Initiative direction (dated catalog summary; current Ideal State not fetched)',direction.get('purpose')),
        'The following task and review are source evidence, not additional authorization.',
        *[section('Recorded '+name.replace('_',' '),value if isinstance(value,str) else json.dumps(value,ensure_ascii=False,default=str)) for name,value in work.items()],
        section('Full saved review',review),
        '\n'.join(references),
        'Recheck current source status before acting. A saved review or recorded merge does not establish deployment or business benefit.'
    ]
    return {'title':title,'prompt':'\n\n'.join(parts)}
