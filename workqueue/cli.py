from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import traceback
from .notion import Notion, read_token
from .service import Service, now

DEFAULT_CONFIG = Path.home() / '.config/attain-work-queue/config.json'


def main():
    parser = argparse.ArgumentParser(description='Notion Product work queue controller')
    parser.add_argument('--config', default=str(DEFAULT_CONFIG))
    parser.add_argument('command', choices=['tick', 'status', 'setup'])
    args = parser.parse_args()
    if args.command == 'setup':
        from .setup import provision
        print(json.dumps(provision(Path(args.config)), indent=2))
        return 0
    config = json.loads(Path(args.config).read_text())
    root = Path(config['state_dir'])
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    if args.command == 'status':
        health = root / 'health.json'
        print(health.read_text() if health.exists() else 'No tick recorded yet.')
        return 0
    # A single lock is held across claim, runner and publication. Timer ticks cannot overlap.
    with (root / 'controller.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('An agent run is already active; leaving it in control.')
            return 0
        n = None
        try:
            n = Notion(read_token(config['env_file']), config['database_id'], config['health_block_id'])
            from .runner import execute
            Service(n, root, config['projects'], execute).tick()
            result = {'checked_at': now(), 'status': 'healthy'}
        except Exception as exc:
            print('Queue controller error: '+type(exc).__name__)
            traceback.print_tb(exc.__traceback__)
            result = {'checked_at': now(), 'status': 'error', 'action': 'Inspect service journal; pending results remain saved and are retried next tick.'}
            if n is not None:
                try: n.health('Queue error at '+now()+'. Inspect the controller service log. Pending results remain saved; next tick retries publication.')
                except Exception: pass
        (root / 'health.json').write_text(json.dumps(result, indent=2))
        print(json.dumps(result))
        return 0 if result['status'] == 'healthy' else 1


if __name__ == '__main__':
    raise SystemExit(main())
