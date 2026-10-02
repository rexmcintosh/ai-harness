"""One owner action through the existing queue's locks and durable writer."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from backlogrun import cli as backlog


class StaleDecision(ValueError):
    pass


def revision(item):
    return hashlib.sha256(json.dumps(item, sort_keys=True, default=str).encode()).hexdigest()


def hold(config, item_id, expected_revision, reason, decision_id):
    cfg = backlog.Config(backlog_path=config['BACKLOG_PATH'], state_dir=config['STATE_ROOT'],
                         projects=config['PROJECTS_ROOT'])
    with backlog.RunLock(cfg), backlog.BacklogLock(cfg.backlog_path, wait_s=0):
        doc = backlog.load_yaml(cfg.backlog_path)
        archive = backlog.load_yaml(cfg.archive_path)
        matches = [i for i in doc.get('items', []) if i.get('id') == item_id]
        if len(matches) != 1 or any(i.get('id') == item_id for i in archive.get('items', [])):
            raise StaleDecision('The queue changed. Refresh before deciding.')
        item = matches[0]
        prior = item.get('cockpit_decision') or {}
        if prior.get('id') == decision_id and item.get('status') == 'held' and item.get('note') == reason:
            return {'status': 'held', 'replayed': True}
        if revision(item) != expected_revision or item.get('status') not in ('open', 'in_review'):
            raise StaleDecision('The work changed. Refresh and review its current state.')
        item.update(status='held', note=reason, cockpit_decision={
            'id': decision_id, 'action': 'hold', 'at': datetime.now(timezone.utc).isoformat(),
            'source_revision': expected_revision, 'reason': reason, 'owner': 'Rex',
        })
        backlog.write_yaml_atomic(cfg.backlog_path, doc)
        return {'status': 'held', 'replayed': False}
