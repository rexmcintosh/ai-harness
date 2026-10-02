from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path


def now():
    return datetime.now(timezone.utc).isoformat()


def fingerprint(task):
    fields = {k: task.get(k) for k in ('title', 'brief', 'done_when', 'mode', 'repo', 'checks', 'feedback_ids')}
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def validate(task):
    for name, key in [('Task', 'title'), ('Brief', 'brief'), ('Done when', 'done_when')]:
        if not isinstance(task.get(key), str) or not task[key].strip():
            return f'Fill in {name}, then move this task to Ready.'
    if task.get('repo') != 'sat-prep':
        return 'This queue only runs work in sat-prep.'
    if task.get('mode') not in ('Investigate', 'Build', 'Prepare session'):
        return 'Choose Investigate, Build or Prepare session in Mode.'
    if task['mode'] == 'Build' and not task.get('checks'):
        return 'Fill in Checks with the required verification commands before choosing Build.'
    return ''


class Service:
    """Caller holds the process lock for the entire tick, including the bounded run."""
    def __init__(self, notion, state_dir, projects, runner):
        self.notion, self.root, self.projects, self.runner = notion, Path(state_dir), projects, runner
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'journal.json'
        self.journal = json.loads(self.path.read_text()) if self.path.exists() else {'runs': {}}

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(self.journal, f, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)
        fd = os.open(self.root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def publish(self, run):
        rid, pid, result = run['id'], run['task']['id'], run['result']
        current = self.notion.get_task_meta(pid)
        changed = fingerprint(current) != run['fingerprint']
        text = result['result']
        if changed:
            text = 'The brief or work mode changed during this run. These results cover the saved starting brief. Review them before choosing Ready again.\n\n' + text
        evidence = f"Run: {rid}\nMode: {run['task']['mode']}\nStarted: {run['started']}\nFinished: {run['finished']}\n"
        evidence += f"Session: {result.get('session', '')}\nBranch: {result.get('branch', '')}\nReview readiness: {result.get('readiness', 'unknown')}\n\n"
        direction = run['task'].get('direction', {})
        evidence += (f"Ideal State: {direction.get('status', 'unavailable')}\n"
                     f"Source: {direction.get('source_url', 'not captured')}\n"
                     f"Revision: {direction.get('sha256', 'unknown')}\n"
                     f"Observed: {direction.get('observed_at', 'unknown')}\n\n")
        if direction.get('status') != 'current':
            text = 'Current Ideal State unavailable for this run; results cover the saved authorized brief.\n\n' + text
        if 'publication_text' not in run:
            starting = '\nStarting brief:\n' + run['task']['brief'] + '\nDone when:\n' + run['task']['done_when'] + '\n\n'
            run['publication_text'] = evidence + starting + text
            self.save()
        self.notion.append_result(pid, rid, run['publication_text'])
        # Do not turn a human's Draft/Closed/Ready selection back into Working/In review.
        owns = current.get('run_id') == rid and current.get('status') == 'Working'
        uncertain_ready = run.get('uncertain') and current.get('status') == 'Ready' and current.get('run_id', '') in ('', run['task'].get('run_id', ''), rid)
        if owns or uncertain_ready:
            self.notion.update_task(pid,
                status='Needs your input' if changed or run.get('uncertain') else result['status'],
                result=text[:1800], run_id=rid, finished=run['finished'],
                session=result.get('session', ''), branch=result.get('branch', ''),
                readiness=result.get('readiness', 'unknown'), resume=result.get('resume_command', ''))
        run['phase'] = 'published'
        self.save()

    def tick(self):
        recovered = False
        for run in self.journal['runs'].values():
            if run['phase'] == 'published':
                continue
            recovered = True
            if run['phase'] != 'result':
                # The adapter saves its response before returning. A crash in that narrow
                # handoff must recover completed work rather than discard it as uncertain.
                marker = self.root / 'executions' / run['id'] / 'queue-runs' / (run['id'] + '.json')
                finished = None
                try:
                    stored = json.loads(marker.read_text())
                    response = stored.get('response', {})
                    if (stored.get('run_id') == run['id'] and stored.get('state') == 'finished'
                        and response.get('status') in ('In review', 'Needs your input')
                        and isinstance(response.get('result'), str)):
                        finished = response
                except (OSError, ValueError, AttributeError):
                    pass
                if finished is not None:
                    run.update(phase='result', finished=now(), result=finished)
                else:
                    run.update(phase='result', uncertain=True, finished=now(), result={
                        'status': 'Needs your input',
                        'result': 'The controller was interrupted before it could confirm a complete result. It will not restart this run automatically. Inspect the saved run/branch, then choose Ready for a new attempt.',
                    })
                self.save()
            self.publish(run)
        if recovered:
            self.notion.health('Recovered pending run results. No agent was relaunched. ' + now())
            return
        tasks = self.notion.tasks()
        ready = [t for t in tasks if t.get('status') == 'Ready']
        if not ready:
            self.notion.health('Healthy. No Ready tasks. Checked ' + now())
            return
        task = ready[0]
        # Always read again: the query result may already be stale.
        task = self.notion.get_task(task['id'])
        if task.get('status') != 'Ready':
            return
        error = validate(task)
        if error:
            self.notion.update_task(task['id'], status='Needs your input', result=error)
            self.notion.health('A Ready task needs a clearer brief. Checked ' + now())
            return
        # Read direction before claiming/launching and keep it with the starting brief.
        # Failure is visible; it does not silently revoke authorization for a clear repair.
        try:
            task['direction'] = self.notion.ideal_state()
        except Exception:
            task['direction'] = {'status': 'unavailable', 'observed_at': now()}
        rid = 'nq-' + uuid.uuid4().hex
        run = {'id': rid, 'task': task, 'fingerprint': fingerprint(task), 'phase': 'claiming', 'started': now()}
        self.journal['runs'][rid] = run
        self.save()
        self.notion.update_task(task['id'], status='Working', run_id=rid, started=run['started'],
                                finished=None, result='Starting the saved brief. Results will appear on this page.',
                                session='', branch='', readiness='unknown', resume='')
        current = self.notion.get_task_meta(task['id'])
        if current.get('status') != 'Working' or current.get('run_id') != rid or fingerprint(current) != run['fingerprint']:
            run.update(phase='result', finished=now(), result={'status': 'Needs your input',
                'result': 'Task changed during the claim. No agent was started. Check the brief and choose Ready again.'})
            self.save()
            self.publish(run)
            return
        # A durable launching marker comes BEFORE the expensive action. Recovery never guesses.
        run['phase'] = 'launching'
        self.save()
        self.notion.health(f"Working on {task['title']}. Run {rid}, started {run['started']}.")
        try:
            result = self.runner(task, rid, str(self.root / 'executions' / rid), self.projects)
            if result.get('status') not in ('In review', 'Needs your input') or not isinstance(result.get('result'), str):
                raise ValueError('Runner returned an invalid result')
        except Exception:
            # Error details belong in the local log, not an accidental credential-bearing API response.
            import traceback
            traceback.print_exc()
            result = {'status': 'Needs your input', 'result': 'The agent run failed. Its local evidence is retained. Inspect the run log before choosing Ready to retry.'}
        run.update(phase='result', result=result, finished=now())
        self.save()
        self.publish(run)
        self.notion.health('Healthy. Finished run ' + rid + ' at ' + run['finished'])
