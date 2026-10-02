"""Scoped read-only Notion feeds. Explicit completion lives in completion.py."""
from __future__ import annotations

import json
from pathlib import Path
import shlex

import requests
import yaml

from .sources import safe_url, stamp


def _token(path, name):
    if name not in ('NOTION_TOKEN_ATTAINPREP', 'NOTION_TOKEN'):
        raise ValueError('Unsupported account scope')
    for line in Path(path).read_text().splitlines():
        if line.startswith(name + '='):
            parts = shlex.split(line.split('=', 1)[1], comments=True)
            if parts: return parts[0]
    raise ValueError('Scoped token unavailable')


def _value(page, name):
    prop = page.get('properties', {}).get(name, {})
    kind = prop.get('type')
    if kind in ('title', 'rich_text'):
        return ''.join(v.get('plain_text', v.get('text', {}).get('content', '')) for v in prop.get(kind, []))
    if kind in ('select', 'status'): return (prop.get(kind) or {}).get('name', '')
    if kind == 'url': return prop.get('url') or ''
    if kind == 'date': return (prop.get('date') or {}).get('start', '')
    return ''


def _query(token, route, version, post=requests.post):
    rows, cursor, cursors = [], None, set()
    for _ in range(10):
        body = {'page_size': 100}
        if cursor: body['start_cursor'] = cursor
        response = post('https://api.notion.com/v1/' + route, json=body,
                        headers={'Authorization': 'Bearer ' + token, 'Notion-Version': version}, timeout=(3, 8))
        if response.status_code != 200: raise ValueError('Scoped Notion read unavailable')
        data = response.json()
        rows.extend(data['results'])
        if not data.get('has_more'): return rows
        cursor = data.get('next_cursor')
        if not cursor or cursor in cursors: raise ValueError('Partial Notion read')
        cursors.add(cursor)
    raise ValueError('Notion feed exceeds bounded reader; coverage incomplete')


def remote_work(config):
    root, work, sources = Path(config['PROJECTS_ROOT']), [], []
    env_file = config.get('ENV_FILE', str(Path.home() / '.env'))
    feeds = (
        ('Attain product queue', 'NOTION_TOKEN_ATTAINPREP'),
        ('Romance Ops', 'NOTION_TOKEN'),
    )
    for name, token_name in feeds:
        try:
            token = _token(env_file, token_name)
            if name == 'Attain product queue':
                cfg = json.loads(Path(config.get('ATTAIN_QUEUE_CONFIG', str(Path.home() / '.config/attain-work-queue/config.json'))).read_text())
                rows = _query(token, 'databases/' + cfg['database_id'] + '/query', '2022-06-28')
            else:
                cfg = yaml.safe_load((root / 'romance-empire/config/ops.yaml').read_text())
                rows = _query(token, 'data_sources/' + cfg['notion']['data_source_id'] + '/query', '2025-09-03')
            batch = []
            for row in rows:
                status = _value(row, 'Status')
                terminal = status in ('Done', 'Closed', 'Dropped', 'Dismissed', 'Archived')
                if name == 'Attain product queue':
                    title, why, repo, iid = _value(row, 'Task'), _value(row, 'Result') or _value(row, 'Done when'), 'sat-prep', 'notion:' + row['id']
                    if terminal:
                        why = _value(row, 'Result') or 'Marked closed in the product queue; no result detail recorded.'
                    linked = None
                else:
                    title, why, repo = _value(row, 'Name'), _value(row, 'Why'), 'romance-empire'
                    key = _value(row, 'Key')
                    linked = key.split(':', 1)[1] if key.startswith('runner:') else None
                    iid = 'ops:' + (key or row['id'])
                    if terminal:
                        why = 'Closure is recorded in the owner queue. Check the source for the owner’s completion evidence.'
                completion_claim = None
                if name == 'Romance Ops' and not terminal:
                    from .completion import offer
                    completion_claim = offer(config,row,cfg=cfg)
                batch.append({'_completion_claim':completion_claim,'id': iid, 'title': title or 'Untitled work', 'why': why or 'Open the source for its current decision.',
                              'repo': repo, 'status': status or 'unknown', 'source': name,
                              'source_url': safe_url(row.get('url')), 'created': row.get('created_time', ''),
                              'updated_at': row.get('last_edited_time'), 'backlog_id': linked, 'can_hold': False,
                              'due': _value(row, 'Due'), 'due_timezone': cfg.get('timezone', 'Europe/Lisbon'),
                              'source_type': _value(row, 'Source'),
                              'series_name': _value(row, 'Series'),
                              'action_url': safe_url(_value(row, 'Source link')),
                              'is_terminal': terminal,
                              'completed_at': _value(row, 'Finished') if terminal and name == 'Attain product queue' else None})
            work.extend(batch)
            sources.append({'name': name, 'status': 'available', 'updated_at': max((r.get('last_edited_time', '') for r in rows), default=None),
                            'observed_at': stamp(), 'detail': f'{sum(not r["is_terminal"] for r in batch)} active and {sum(r["is_terminal"] for r in batch)} closed records read from the existing owner queue.'})
        except (OSError, ValueError, KeyError, TypeError, requests.RequestException, yaml.YAMLError):
            sources.append({'name': name, 'status': 'unavailable', 'updated_at': None,
                            'observed_at': stamp(), 'detail': 'The scoped owner queue could not be read. No replacement account was used.'})
    return work, sources
