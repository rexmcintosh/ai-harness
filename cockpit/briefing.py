"""A display checkpoint, not another work queue or execution authority."""
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import fcntl
import hashlib
import json
import logging
import os
from pathlib import Path
import tempfile


CATEGORIES = ('review', 'running', 'queued', 'waiting', 'held', 'unknown')


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=True).encode()).hexdigest()


def classify(row):
    source, status = row.get('source'), row.get('status')
    owner = row.get('owner_surface_status')
    if source == 'Shared backlog':
        if status == 'held':
            next_step = 'Read the recorded hold reason before choosing the next step.'
            if owner and owner not in ('Held', 'Waiting'):
                next_step = f'The backlog is held; the owner queue says {owner}. Check both records.'
            return 'held', 'Held', next_step
        if owner == 'Changes':
            return 'waiting', 'Rework requested', 'Review the requested changes in the owner queue.'
        if status == 'open':
            return 'queued', 'Queued', 'Available for the existing runner to select; no running task is confirmed here.'
        if status == 'in_review':
            return 'review', 'Needs your review', 'Read the result and review conditions before deciding. Readiness is not confirmed here.'
    states = {
        'Attain product queue': {
            'Draft': ('waiting', 'Draft', 'Finish the brief before making it ready for work.'),
            'Ready': ('queued', 'Queued', 'Waiting for the existing controller to select this task.'),
            'Working': ('running', 'In progress', 'The controller records a run in progress; its result is still due.'),
            'In review': ('review', 'Needs your review', 'Read the result and checks before choosing the next step.'),
            'Needs your input': ('review', 'Needs your input', 'Read the controller’s request and supply the missing decision or information.'),
        },
        'Romance Ops': {
            'To do': ('review', 'Owner task', 'Open the owner task and its instructions.'),
            'Missed': ('review', 'Missed owner task', 'Review the missed task and decide its next step.'),
            'Changes': ('waiting', 'Rework requested', 'Requested changes are recorded in the owner queue.'),
            'Waiting': ('waiting', 'Waiting', 'Read the source for the dependency and next step.'),
        },
    }
    return states.get(source, {}).get(status, ('unknown', 'Needs clarification', 'Open the source to establish the current state and next step.'))


def parsed_time(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed if parsed.tzinfo else None
    except ValueError:
        return None


def enrich(row, initiatives, terminal=False):
    initiative = next((i for i in initiatives if row.get('repo') in i.get('repos', [])), {})
    row.update(initiative_id=initiative.get('id', ''), initiative_name=initiative.get('name', row.get('repo', 'Unassigned')))
    row.setdefault('evidence_at', row.get('updated_at'))
    if terminal:
        completed = str(row.get('completed_at') or '')
        valid_date = parsed_time(completed)
        row['completed_at'] = completed if valid_date else None
        row['date_precision'] = ('day' if len(completed) == 10 else 'instant') if valid_date else 'unknown'
        status = str(row.get('status')).lower()
        row.setdefault('outcome_label', 'Dropped' if status in ('dropped', 'dismissed') else ('Recorded done' if status == 'done' else 'Closed'))
        row.setdefault('next_action', 'Read the recorded result. Delivery and business benefit are not established by the closed status.')
    else:
        row['category'], row['category_label'], row['next_action'] = classify(row)
    return row


def state_path(config):
    path = Path(config['BRIEF_STATE_PATH'])
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise ValueError('Review checkpoint must not redirect to another path')
    return path


def load_state(config):
    try:
        path = state_path(config)
        if not path.exists():
            return None, 'first_review'
        if path.stat().st_size > 2_000_000:
            raise ValueError('Oversize review checkpoint')
        state = json.loads(path.read_text())
        if (not isinstance(state, dict) or state.get('version') != 1
                or not parsed_time(state.get('reviewed_at')) or not isinstance(state.get('records'), dict)):
            raise ValueError('Invalid checkpoint')
        if 'observed_sources' in state and (not isinstance(state['observed_sources'], list)
                or not all(isinstance(name, str) for name in state['observed_sources'])):
            raise ValueError('Invalid source observations')
        for key, row in state['records'].items():
            if not isinstance(key, str) or not isinstance(row, dict):
                raise ValueError('Invalid checkpoint record')
            if any(not isinstance(row.get(k), str) for k in ('id', 'title', 'status', 'source', 'signature')):
                raise ValueError('Incomplete checkpoint record')
            if not isinstance(row.get('terminal'), bool):
                raise ValueError('Invalid record closure state')
            if not isinstance(row.get('dependencies'), list) or not all(isinstance(x, str) for x in row['dependencies']):
                raise ValueError('Invalid record sources')
        return state, 'available'
    except (OSError, ValueError, TypeError):
        return None, 'unavailable'


def record_key(row):
    scope = 'backlog' if row.get('source') in ('Shared backlog', 'Backlog archive') else row.get('source', 'unknown')
    return scope + ':' + str(row['id'])


def build(data, config):
    state, comparison = load_state(config)
    prior = (state or {}).get('records', {})
    known_sources = set((state or {}).get('observed_sources', []))
    known_sources.update(dep for row in prior.values() for dep in row['dependencies'])
    source_states = {s['name']: s['status'] for s in data['sources']}
    unavailable = {name for name, status in source_states.items() if status == 'unavailable'}
    observed_sources = known_sources | {name for name, status in source_states.items() if status == 'available'}
    uncompared_sources = set()
    now = datetime.now(timezone.utc)
    records, changes = {}, []
    for terminal, rows in ((False, data['work']), (True, data['results'])):
        for row in rows:
            key = record_key(row)
            deps = [row['source']]
            if row.get('owner_surface_status'):
                deps.append('Romance Ops')
            current = {k: row.get(k, '') for k in ('id', 'title', 'initiative_name', 'status', 'source', 'source_url', 'evidence_at')}
            current.update(terminal=terminal, dependencies=deps,
                           signature=digest({k: row.get(k) for k in ('title', 'status', 'why', 'owner_surface_status', 'completed_at', 'merge_commit', 'deployment_followup')}),
                           detail_signature=digest({'due': row.get('due'), 'context_revision': row.get('context_revision')}))
            if key in records:
                comparison = 'unavailable'  # Ambiguous identities never become an acknowledged baseline.
            records[key] = current
            old = prior.get(key)
            if comparison == 'available' and row['source'] not in known_sources:
                uncompared_sources.add(row['source'])
                continue
            if comparison != 'available' or unavailable.intersection((old or current)['dependencies']):
                continue
            if (old and old['signature'] == current['signature']
                    and ('detail_signature' not in old or old['detail_signature'] == current['detail_signature'])):
                continue
            kind = 'changed' if old else 'new'
            if terminal:
                completed = parsed_time(row.get('completed_at'))
                recent_completion = completed and parsed_time(state['reviewed_at']) < completed <= now
                if old and old['terminal']:
                    kind = 'changed'
                    summary = 'The recorded result was updated. '
                elif row['outcome_label'] in ('Dropped', 'Closed'):
                    kind = 'closed'
                    summary = row['outcome_label'] + ' in the source. '
                elif recent_completion:
                    kind = 'completed'
                    summary = row['outcome_label'] + ' with a completion date after the saved review. '
                else:
                    kind = 'changed' if old else 'newly_observed'
                    summary = row['outcome_label'] + ' is now recorded; completion since the last review is not established. '
                summary += row['next_action']
            elif old:
                summary = f"Recorded state: {old['status']} to {row['status']}. " + row['next_action']
            else:
                summary = 'Newly seen in this queue. ' + row['next_action']
            changes.append({**current, 'kind': kind, 'summary': summary, 'owner_brief': row.get('owner_brief')})
    if comparison == 'available':
        for key, old in prior.items():
            if key not in records and not unavailable.intersection(old['dependencies']):
                changes.append({**old, 'kind': 'left_queue', 'summary': 'No longer returned by the source. Completion is not established.'})
    if comparison == 'unavailable':
        changes = []
    # Preserve unseen facts across an outage, including linked owner records.
    merged = dict(records)
    for key, old in prior.items():
        if unavailable.intersection(old['dependencies']):
            merged[key] = old
    since = now - timedelta(days=7)
    recent = [r for r in data['results'] if (at := parsed_time(r.get('completed_at')))
              and ((since.date() <= at.date() <= now.date()) if r['date_precision'] == 'day' else since <= at <= now)]
    recent.sort(key=lambda r: r['completed_at'], reverse=True)
    counts = Counter(r['category'] for r in data['work'])
    detail = {
        'first_review': 'First review: recent recorded results and current work. No earlier review is saved.',
        'available': 'Changes since your last saved review. Refreshing does not clear them.',
        'unavailable': 'The previous review or work identity could not be confirmed. Current work remains visible; changes are unknown.',
    }[comparison]
    if uncompared_sources:
        detail += ' No previous comparison for: ' + ', '.join(sorted(uncompared_sources)) + '. Their existing records are not reported as new.'
    data['brief'] = dict(baseline_at=(state or {}).get('reviewed_at'), comparison_status=comparison,
                         window_start=since.isoformat(), counts={k: counts[k] for k in CATEGORIES}, changes=changes,
                         recent_results=recent, attention=[r for r in data['work'] if r['category'] == 'review' and r.get('work_type') != 'operate'],
                         operations=[r for r in data['work'] if r.get('work_type') == 'operate' and r.get('due_state') != 'upcoming'],
                         upcoming_operations=[r for r in data['work'] if r.get('work_type') == 'operate' and r.get('due_state') == 'upcoming'],
                         source_issues=[s for s in data['sources'] if s['status'] in ('unavailable', 'overdue', 'failed')],
                         uncompared_sources=sorted(uncompared_sources),
                         review_token=None, detail=detail)
    data['_checkpoint'] = dict(records=merged, observed_sources=sorted(observed_sources), baseline=digest(state),
                               snapshot=digest({'records': records, 'sources': source_states, 'direction': data['initiatives'],
                                                'explanations': {record_key(r): {'brief': r.get('owner_brief'), 'work_type': r.get('work_type')}
                                                                 for r in data['work'] + data['results']}}),
                               available=comparison != 'unavailable')


@contextmanager
def checkpoint_lock(config):
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def save(config, checkpoint, token_id):
    path = state_path(config)
    at = datetime.now(timezone.utc).isoformat()
    state = dict(version=1, reviewed_at=at, records=checkpoint['records'], token_id=token_id,
                 observed_sources=checkpoint['observed_sources'])
    encoded = json.dumps(state)
    if len(encoded.encode()) > 2_000_000:
        raise ValueError('Review checkpoint exceeds the supported size; previous review retained')
    fd, temporary = tempfile.mkstemp(prefix='.brief-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        try:
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except OSError:
            # Replacement has committed. Confirm the visible record before
            # reporting success, and retain the durability warning in the log.
            if json.loads(path.read_text()) != state:
                raise OSError('Replaced checkpoint could not be confirmed')
            logging.getLogger(__name__).warning('Cockpit review recorded; directory durability confirmation failed.')
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return at
