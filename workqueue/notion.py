from __future__ import annotations

import time
import hashlib
from datetime import datetime, timezone
from pathlib import Path
import shlex
import requests


class NotionError(RuntimeError):
    pass


def read_token(path):
    for line in Path(path).read_text().splitlines():
        if line.startswith('NOTION_TOKEN_ATTAINPREP='):
            parts = shlex.split(line.split('=', 1)[1], comments=True)
            if parts:
                return parts[0]
    raise NotionError('Scoped Attain Prep Notion credential missing')


def rich(text):
    return [{'type': 'text', 'text': {'content': text[i:i+1800]}} for i in range(0, len(text), 1800)]


def plain(prop):
    return ''.join(x.get('plain_text', x.get('text', {}).get('content', '')) for x in prop.get(prop.get('type', 'rich_text'), []))


def parse_task(page):
    p = page['properties']
    txt = lambda name: plain(p.get(name, {}))
    sel = lambda name: (p.get(name, {}).get('select') or {}).get('name', '')
    return dict(id=page['id'], source_url=page['url'], title=txt('Task'), brief=txt('Brief'),
                done_when=txt('Done when'), checks=[s.strip() for s in txt('Checks').splitlines() if s.strip()],
                mode=sel('Mode'), status=sel('Status'), repo=sel('Repository'), run_id=txt('Run ID'),
                feedback_ids=[r['id'] for r in p.get('Feedback', {}).get('relation', [])])


class Notion:
    def __init__(self, token, database_id='', health_block_id='', session=None):
        self.database_id, self.health_block_id = database_id, health_block_id
        self.http = session or requests.Session()
        self.http.headers.update({'Authorization': 'Bearer '+token, 'Notion-Version': '2022-06-28', 'Content-Type': 'application/json'})

    def call(self, method, path, payload=None):
        # Retry only reads and rate-limited requests. Uncertain writes recover via journal/read-back.
        for attempt in range(3):
            r = self.http.request(method, 'https://api.notion.com/v1'+path, json=payload, timeout=(10, 40))
            if r.status_code == 429 and attempt < 2:
                try: delay = min(10, max(1, float(r.headers.get('Retry-After', '2'))))
                except ValueError: delay = 2
                time.sleep(delay)
                continue
            if r.status_code >= 500 and method == 'GET' and attempt < 2:
                time.sleep(1 + attempt)
                continue
            if not r.ok:
                raise NotionError(f'Notion request failed: HTTP {r.status_code}, {method} {path.split("?")[0]}')
            return r.json()
        raise NotionError('Notion retry exhausted')

    def children(self, page_id, *, max_blocks=None):
        out, cursor, cursors = [], None, set()
        while True:
            result = self.call('GET', '/blocks/'+page_id+'/children?page_size=100'+('&start_cursor='+cursor if cursor else ''))
            out.extend(result['results'])
            if max_blocks is not None and len(out) > max_blocks:
                raise NotionError('Source exceeds the bounded reader')
            if not result.get('has_more'): return out
            cursor = result.get('next_cursor')
            if not cursor or cursor in cursors:
                raise NotionError('Source pagination is incomplete')
            cursors.add(cursor)

    def tasks(self):
        out, cursor = [], None
        while True:
            data = {'page_size': 100, 'filter': {'property': 'Status', 'select': {'equals': 'Ready'}},
                    'sorts': [{'timestamp': 'created_time', 'direction': 'ascending'}]}
            if cursor: data['start_cursor'] = cursor
            result = self.call('POST', '/databases/'+self.database_id+'/query', data)
            out.extend(parse_task(p) for p in result['results'])
            if not result.get('has_more'): return out
            cursor = result['next_cursor']

    def ideal_state(self):
        """Read only Attain's canonical page using this controller's scoped client."""
        page_id = '3d645d882ebb81db9d50e35698091788'
        page = self.call('GET', '/pages/' + page_id)
        lines = []
        count = 0

        def collect(block_id, depth=0):
            nonlocal count
            if depth > 12:
                raise NotionError('Ideal State nesting exceeds the bounded reader')
            for block in self.children(block_id, max_blocks=400-count):
                count += 1
                if count > 400:
                    raise NotionError('Ideal State exceeds the bounded reader')
                kind = block.get('type', '')
                body = block.get(kind, {})
                text = ''.join(t.get('plain_text', t.get('text', {}).get('content', ''))
                               for t in body.get('rich_text', []))
                if text:
                    lines.append('  ' * depth + text)
                if block.get('has_children'):
                    collect(block['id'], depth + 1)

        collect(page_id)
        text = '\n'.join(lines)
        if not text.strip() or len(text) > 40000:
            raise NotionError('Ideal State is empty or exceeds the context limit')
        after = self.call('GET', '/pages/' + page_id)
        if after.get('last_edited_time') != page.get('last_edited_time'):
            raise NotionError('Ideal State changed during reading')
        return {'status': 'current', 'source_url': page['url'],
                'source_updated_at': page['last_edited_time'],
                'observed_at': datetime.now(timezone.utc).isoformat(),
                'sha256': hashlib.sha256(text.encode()).hexdigest(), 'text': text}

    def get_task_meta(self, page_id):
        return parse_task(self.call('GET', '/pages/'+page_id))

    def get_task(self, page_id):
        task = self.get_task_meta(page_id)
        evidence = []
        for fid in task['feedback_ids']:
            feedback = self.call('GET', '/pages/'+fid)
            parent = feedback.get('parent', {})
            if parent.get('database_id', '').replace('-', '') != '3aa16352e8104a299ff68d05ef6216ad':
                raise NotionError('Feedback relation points outside the allowed feedback log')
            props = feedback['properties']
            body = []
            for child in self.children(fid):
                body.append(''.join(t.get('plain_text', t.get('text', {}).get('content', '')) for t in child.get(child['type'], {}).get('rich_text', [])))
            evidence.append(plain(props.get('Item', {}))+'\n'+feedback['url']+'\n'+'\n'.join(body))
        task['feedback'] = '\n\n'.join(evidence)
        return task

    def update_task(self, page_id, **fields):
        names = {'status': 'Status', 'run_id': 'Run ID', 'started': 'Started', 'finished': 'Finished',
                 'result': 'Result', 'session': 'Session', 'branch': 'Branch', 'readiness': 'Readiness', 'resume': 'Resume'}
        properties = {}
        for key, value in fields.items():
            if key == 'status': prop = {'select': {'name': value}}
            elif key in ('started', 'finished'): prop = {'date': {'start': value} if value else None}
            else: prop = {'rich_text': rich(str(value)[:1800])}
            properties[names[key]] = prop
        self.call('PATCH', '/pages/'+page_id, {'properties': properties})

    def append_result(self, page_id, run_id, text):
        # Each chunk has a stable marker. A lost write acknowledgement is reconciled by reading.
        existing = set()
        for block in self.children(page_id):
            data = block.get(block['type'], {})
            existing.add(''.join(t.get('plain_text', t.get('text', {}).get('content', '')) for t in data.get('rich_text', [])))
        chunks = [text[i:i+1500] for i in range(0, len(text), 1500)] or ['']
        for index, part in enumerate(chunks):
            content = f'Run {run_id} | result {index+1}/{len(chunks)}\n{part}'
            if content in existing: continue
            self.call('PATCH', '/blocks/'+page_id+'/children', {'children': [
                {'object': 'block', 'type': 'paragraph', 'paragraph': {'rich_text': rich(content)}}]})

    def health(self, text):
        if self.health_block_id:
            self.call('PATCH', '/blocks/'+self.health_block_id, {'paragraph': {'rich_text': rich(text)}})
