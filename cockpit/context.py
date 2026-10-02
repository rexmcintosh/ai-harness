"""Plain owner explanations, checked against their source record. No inference on reads."""
from datetime import datetime
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .actions import revision
from .briefing import record_key, parsed_time


FIELDS = ('title', 'context', 'why', 'progress', 'next_step', 'as_of')


def full_review(config, item_id):
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]*', str(item_id)):
        return ''
    path = Path(config['STATE_ROOT']) / 'reviews' / (str(item_id) + '.md')
    try:
        if path.is_symlink() or path.stat().st_size > 500_000:
            return ''
        return path.read_text()
    except (OSError, UnicodeError):
        return ''


def source_revision(record, review=''):
    return revision({'record': record, 'review': review})


def load(config, source_status=None):
    path = Path(config.get('CONTEXT_PATH') or Path(config['PROJECTS_ROOT']) / '.cockpit' / 'work-context.json')
    try:
        if path.stat().st_size > 2_000_000:
            raise ValueError('Explanation file exceeds the supported size')
        data = json.loads(path.read_text())
        if data.get('version') != 1 or not isinstance(data.get('items'), dict):
            raise ValueError('Invalid explanation file')
        valid = {key: row for key, row in data['items'].items() if isinstance(row, dict)
                and all(isinstance(row.get(field), str) and 0 < len(row[field]) <= 4000
                        for field in (*FIELDS, 'source_revision'))}
        if source_status is not None:
            source_status.append(dict(name='Work explanations', status='available' if len(valid) == len(data['items']) else 'unavailable',
                detail='Prepared owner explanations match their recorded source only; they do not prove live delivery.' if len(valid) == len(data['items']) else 'Some explanations could not be read. Their source work remains visible.'))
        return valid
    except (OSError, ValueError, TypeError, AttributeError):
        if source_status is not None:
            source_status.append(dict(name='Work explanations', status='unavailable',
                detail='The prepared explanations could not be read. Source work remains visible; the system needs to restore the explanation file.'))
        return {}


def attach(row, explanations):
    if not row.get('context_revision'):
        # Remote feeds retain the relevant source facts, not the generated explanation.
        row['context_revision'] = source_revision({k: row.get(k) for k in
            ('id', 'title', 'why', 'status', 'source', 'updated_at', 'due', 'source_type', 'action_url')})
    prior = explanations.get(record_key(row))
    state = 'missing' if not prior else ('current' if prior['source_revision'] == row['context_revision'] else 'stale')
    if state == 'current':
        row['owner_brief'] = {'state': state, **{field: prior[field] for field in FIELDS}}
    else:
        row['owner_brief'] = dict(state=state, title=row['title'], context=row.get('initiative_name') or row.get('repo') or 'Initiative not identified',
            why='The source record needs a plain explanation of its purpose and value.',
            progress='The source has changed since this explanation was prepared.' if prior else 'An owner explanation has not been prepared.',
            next_step='The system should explain the current evidence and needed decision before asking you to act.',
            as_of=str(row.get('evidence_at') or ''))
    source_type = str(row.get('source_type') or '').strip().lower()
    if row.get('source') == 'Romance Ops' and (source_type in ('engage', 'social') or row['id'].startswith('ops:ritual:')):
        row['work_type'] = 'operate'
    elif row.get('source') in ('Shared backlog', 'Backlog archive', 'Attain product queue') or source_type in ('runner', 'gate', 'launch'):
        row['work_type'] = 'improve'
    else:
        row['work_type'] = prior.get('work_type', 'unclear') if prior and state == 'current' else 'unclear'
        if row['work_type'] not in ('operate', 'improve'):
            row['work_type'] = 'unclear'
    try:
        zone = ZoneInfo(row.get('due_timezone') or 'Europe/Lisbon')
    except (ZoneInfoNotFoundError, ValueError):
        zone = ZoneInfo('UTC')
    today = datetime.now(zone).date()
    due = parsed_time(row.get('due'))
    day = (due.date() if len(str(row.get('due'))) == 10 else due.astimezone(zone).date()) if due else None
    row['due_state'] = 'undated' if not day else ('today' if day == today else ('overdue' if day < today else 'upcoming'))
    row['due_label'] = 'Date not supplied' if not day else ('Due today' if day == today else ('Overdue since ' if day < today else 'Due ') + day.isoformat())
    if not day and row['work_type'] != 'operate':
        row['due_label'] = ''
    if source_type == 'engage' and row.get('source') == 'Romance Ops':
        row.update(cadence_label='Daily TikTok comment pilot', action_label='Open prepared comments')
        closed = row.get('status') in ('Done', 'Closed', 'Dropped', 'Dismissed', 'Archived')
        row['owner_brief'] = dict(state='current', title='Post the prepared TikTok comments',
            context='Romance / ' + str(row.get('series_name') or 'Social promotion') + ' / TikTok comment pilot',
            why='Take part in relevant reader conversations to test whether they bring attention to the books. Posting the comments is your part of the pilot.',
            progress='The owner queue records this day’s task closed.' if closed else 'The daily task is ready in the owner queue. The linked table holds the video context and draft comments.',
            next_step='No further posting is requested by this closed record.' if closed else 'Open the prepared comments, check each video, post the comments you choose, and mark those targets Posted in the linked table.',
            as_of=str(row.get('evidence_at') or row.get('due') or ''))
    elif row['id'].startswith('ops:ritual:sunday:'):
        row['cadence_label'] = 'Weekly numbers review'
    elif source_type == 'social':
        row['cadence_label'] = 'Publishing calendar'


def evidence(config, raw, source, explanations, initiatives):
    """Use the same explanation on the exact-item source page without another remote read."""
    review = full_review(config, raw.get('id'))
    initiative = next((i for i in initiatives if raw.get('repo') in i.get('repos', [])), {})
    display = dict(id=str(raw.get('id', '')), title=str(raw.get('title') or 'Untitled work'),
                   source=source, repo=raw.get('repo'), status=raw.get('status'),
                   initiative_name=initiative.get('name'), evidence_at=str(raw.get('worked') or raw.get('merged') or raw.get('created') or ''),
                   context_revision=source_revision(raw, review))
    attach(display, explanations)
    return {**raw, 'original_record': dict(raw), 'owner_brief': display['owner_brief'], 'full_review': review}
