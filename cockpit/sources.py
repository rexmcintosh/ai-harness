"""Read existing records without executing any operational command."""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sqlite3
from urllib.parse import quote

import yaml

from .actions import revision
from . import briefing, context, readiness


def stamp():
    return datetime.now(timezone.utc).isoformat()


def read_document(path):
    path = Path(path)
    if path.stat().st_size > 12_000_000:
        raise ValueError('Source exceeds the bounded reader')
    value = yaml.safe_load(path.read_text()) if path.suffix in ('.yaml', '.yml') else json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError('Source is not an object')
    return value


def safe_url(value):
    return value if isinstance(value, str) and value.startswith(('https://', 'http://')) else ''


def source_record(name, path, status, detail=''):
    try: updated = datetime.fromtimestamp(Path(path).stat().st_mtime, timezone.utc).isoformat()
    except OSError: updated = None
    return {'name': name, 'status': status, 'updated_at': updated, 'observed_at': stamp(), 'detail': detail}


def local_work(config):
    path = config['BACKLOG_PATH']
    try:
        data = read_document(path)
        rows = data['items']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid queue')
        counts = Counter(str(row.get('id', '')) for row in rows)
        work = []
        for row in rows:
            if row.get('status') in ('done', 'dropped'):
                continue
            iid = str(row.get('id', ''))
            work.append({'id': iid, 'title': str(row.get('title') or 'Untitled work'),
                         'repo': str(row.get('repo') or 'Unassigned'), 'status': str(row.get('status') or 'unknown'),
                         'why': str(row.get('note') or 'No decision reason recorded.'),
                         'created': str(row.get('created') or ''), 'source': 'Shared backlog',
                         'source_url': safe_url(row.get('source')) or '/work/' + quote(iid, safe=''),
                         'evidence_at': str(row.get('worked') or row.get('created') or '') or None,
                         'revision': revision(row),
                         'context_revision': context.source_revision(row, context.full_review(config, iid)),
                         'can_hold': isinstance(row.get('id'), str) and bool(iid) and counts[iid] == 1 and row.get('status') in ('open', 'in_review')})
        return work, source_record('Shared backlog', path, 'available', 'All active items, including held work.')
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return [], source_record('Shared backlog', path, 'unavailable', 'The queue could not be read. An empty view does not mean no work.')


def archived_results(config):
    path = config['ARCHIVE_PATH']
    try:
        rows = read_document(path)['items']
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Invalid archive')
        results = []
        for row in rows:
            if row.get('status') not in ('done', 'dropped'):
                continue
            iid = str(row.get('id', ''))
            merged = bool(row.get('merge_commit') and row.get('merged'))
            completed = row.get('merged') or row.get('dropped')
            result = dict(id=iid, title=str(row.get('title') or 'Untitled result'), repo=str(row.get('repo') or 'Unassigned'),
                          status=row['status'], why=str(row.get('note') or row.get('result') or row.get('resolution') or 'No result detail recorded.'),
                          source='Backlog archive', source_url=safe_url(row.get('source')) or '/work/' + quote(iid, safe=''),
                          completed_at=str(completed) if completed else None,
                          context_revision=context.source_revision(row, context.full_review(config, iid)),
                          merge_commit=str(row.get('merge_commit') or ''),
                          deployment_followup=str(row.get('deployment_followup') or ''),
                          evidence_at=str(completed) if completed else None,
                          outcome_label='Merged' if merged else ('Dropped' if row['status'] == 'dropped' else 'Recorded done'))
            if row.get('deployment_followup'):
                result['next_action'] = 'Deployment is a separate held task: ' + str(row['deployment_followup']) + '.'
            elif merged:
                result['next_action'] = 'The merge is recorded. Check the result notes for remaining release or outcome checks.'
            results.append(result)
        return results, source_record('Backlog archive', path, 'available', 'Recorded completions and dropped work; a merge does not prove a live release.')
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
        return [], source_record('Backlog archive', path, 'unavailable', 'Completed work could not be read. Recent results may be missing.')


def work_evidence(config, item_id):
    """Read one exact record. Never turn a request path into a filesystem path."""
    matches = []
    explanations = context.load(config)
    try:
        initiatives = read_document(config['CATALOG_PATH']).get('initiatives', [])
    except (OSError, ValueError, yaml.YAMLError):
        initiatives = []
    for name, path in (('Shared backlog', config['BACKLOG_PATH']), ('Backlog archive', config['ARCHIVE_PATH'])):
        try:
            rows = read_document(path)['items']
            if not isinstance(rows, list):
                raise ValueError('Invalid records')
            for row in rows:
                if isinstance(row, dict) and str(row.get('id', '')) == item_id:
                    matches.append((name, context.evidence(config, row, name, explanations, initiatives)))
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            continue
    return matches


def health_sources(config, catalog):
    out = []
    root = Path(config['PROJECTS_ROOT'])
    for check in catalog.get('checks', []):
        path = Path(check['path'].replace('{projects}', str(root)).replace('{home}', str(Path.home())))
        try:
            data = read_document(path)
            seen = data.get(check.get('time_field', 'checked_at'))
            observed = datetime.fromisoformat(str(seen).replace('Z', '+00:00'))
            if observed.tzinfo is None: raise ValueError('Timestamp lacks timezone')
            age = (datetime.now(timezone.utc) - observed).total_seconds()
            status = 'current' if 0 <= age <= check['max_age_seconds'] else 'overdue'
            if age < 0: status = 'unavailable'
            if data.get('status') in ('error', 'failed'): status = 'failed'
            out.append({'name': check['name'], 'status': status, 'updated_at': observed.isoformat(),
                        'observed_at': stamp(), 'detail': check['meaning']})
        except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError):
            out.append(source_record(check['name'], path, 'unavailable', check['meaning']))
    if not out:
        out.append({'name': 'Unattended work', 'status': 'unavailable', 'updated_at': None,
                    'observed_at': stamp(), 'detail': 'No expected-result feeds are configured.'})
    return out


def resources(config):
    path = Path(config.get('USAGE_DB') or Path.home() / '.local/state/venice-usage/ledger.db')
    result = {'cash_result': None, 'cash_spend': None, 'allocation': None, 'owner_time_eur_per_hour': 75,
              'time_value_is_nominal': True, 'usage': [], 'observed_at': stamp(),
              'detail': 'Cash results and allocations are not reconciled. Tokens measure recorded use, not cash or value.'}
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=2) as db:
            db.row_factory = sqlite3.Row
            # Match the existing ledger schema, verified at this source boundary.
            rows = db.execute('SELECT project, COUNT(*) AS calls, SUM(tokens_in) AS tokens_in, SUM(tokens_out) AS tokens_out FROM usage WHERE ts >= ? GROUP BY project ORDER BY SUM(tokens_in) DESC', (since,)).fetchall()
        result.update(usage=[dict(row) for row in rows], since=since, status='available')
    except (OSError, sqlite3.Error):
        result['status'] = 'unavailable'
    return result


def snapshot(config):
    try:
        catalog = read_document(config['CATALOG_PATH'])
        catalog_source = source_record('Portfolio direction and decisions', config['CATALOG_PATH'], 'available', 'Accepted direction and dated audit evidence; proposals stay proposals.')
    except (OSError, ValueError, yaml.YAMLError):
        catalog = {}
        catalog_source = source_record('Portfolio direction and decisions', config['CATALOG_PATH'], 'unavailable', 'Direction could not be read.')
    for initiative in catalog.get('initiatives', []):
        repos = initiative.get('repos', [])
        if not isinstance(repos, list) or not all(isinstance(repo, str) for repo in repos):
            initiative['repos'] = []
            initiative['catalog_error'] = 'Repository links have an invalid format.'
            catalog_source.update(status='unavailable', detail='Some initiative repository links are invalid. Source work remains visible.')
    work, backlog_source = local_work(config)
    results, archive_source = archived_results(config)
    sources = [catalog_source, backlog_source, archive_source, *health_sources(config, catalog)]
    if config.get('REMOTE_READS'):
        from .remote import remote_work
        remote, remote_sources = remote_work(config)
        # An Ops row may point to the same backlog item. Keep its owner source link.
        existing = {row['id']: row for row in work}
        existing_results = {row['id']: row for row in results}
        for row in remote:
            linked = row.pop('backlog_id', None)
            if row.pop('is_terminal', False):
                # A linked owner acknowledgement is not a second completion.
                if linked in existing_results:
                    existing_results[linked]['source_url'] = row['source_url']
                elif linked not in existing:
                    results.append(row)
                else:
                    existing[linked]['owner_surface_status'] = row['status']
                continue
            if linked in existing_results:
                # An unfinished owner task must remain visible even after a merge.
                row['why'] = 'The backlog records completion; this owner task is still open. ' + row.get('why', '')
            if linked in existing:
                existing[linked]['source_url'] = row['source_url']
                existing[linked]['owner_surface_status'] = row['status']
            else:
                work.append(row)
        sources.extend(remote_sources)
    else:
        sources.extend({'name': name, 'status': 'unavailable', 'updated_at': None,
                        'observed_at': stamp(), 'detail': 'Remote reads are not enabled in this view.'}
                       for name in ('Attain product queue', 'Romance Ops'))
    known_repos = {repo for initiative in catalog.get('initiatives', []) for repo in initiative.get('repos', [])}
    coverage = sorted({row['repo'] for row in work if row['repo'] not in known_repos})
    initiatives = catalog.get('initiatives', [])
    explanations = context.load(config, sources)
    for row in work:
        briefing.enrich(row, initiatives)
        context.attach(row, explanations)
    for row in results:
        briefing.enrich(row, initiatives, terminal=True)
        context.attach(row, explanations)
    for row in work + results:
        row['prompt_url'] = '/api/work-prompt/' + quote(briefing.record_key(row), safe='')
    readiness.attach(config, initiatives, work, results, sources)
    data = {'generated_at': stamp(), 'initiatives': initiatives, 'results': results,
            'decisions': catalog.get('decisions', []), 'work': work, 'sources': sources,
            'resources': resources(config), 'unmapped_repositories': coverage,
            'coverage_notes': catalog.get('coverage_notes', []),
            'mode': 'Owner controls enabled' if (config.get('ENABLE_ACTIONS') or (config.get('COMPLETION_ENABLED') and config.get('REMOTE_READS'))) else 'Read-only view'}
    briefing.build(data, config)
    return data
