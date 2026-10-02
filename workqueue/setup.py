"""Idempotent provisioning of the explicitly authorized Attain Prep working queue."""
from pathlib import Path
import json
from .notion import Notion, read_token, rich, plain

HUB = '3d445d88-2ebb-811f-aaa3-edbb35e155e9'
FEEDBACK = '3aa16352-e810-4a29-9ff6-8d05ef6216ad'
TITLE = 'Product work queue'
HELP = 'Product work queue: how to use'
SEEDS = [
 ('3d645d88-2ebb-81bc-8afd-f6264a36f3fa', 'Explore skill-check pause, resume and early exit'),
 ('3d645d88-2ebb-814d-aee1-e0589b3dc972', 'Explore a connected learning journey'),
 ('3d645d88-2ebb-8116-9822-e7abd8d6c2ff', 'Explore question difficulty and progression'),
]


def block(text, typ='paragraph'):
    return {'object': 'block', 'type': typ, typ: {'rich_text': rich(text)}}


def schema():
    props = {'Task': {'title': {}}, 'Feedback': {'relation': {'database_id': FEEDBACK, 'single_property': {}}}}
    for key in ('Brief', 'Done when', 'Checks', 'Run ID', 'Session', 'Branch', 'Result', 'Readiness', 'Resume'):
        props[key] = {'rich_text': {}}
    for key in ('Started', 'Finished'): props[key] = {'date': {}}
    for name, values in {
        'Status': ['Draft', 'Ready', 'Working', 'Needs your input', 'In review', 'Closed'],
        'Mode': ['Investigate', 'Build', 'Prepare session'], 'Repository': ['sat-prep']
    }.items():
        props[name] = {'select': {'options': [{'name': v} for v in values]}}
    return props


def validate_schema(properties):
    for name, spec in schema().items():
        actual = properties.get(name, {})
        kind = next(iter(spec))
        if actual.get('type') != kind:
            raise RuntimeError(f'Queue property {name} must have type {kind}')
        if kind == 'select':
            expected = {o['name'] for o in spec['select']['options']}
            present = {o['name'] for o in actual.get('select', {}).get('options', [])}
            if not expected <= present:
                raise RuntimeError(f'Queue property {name} is missing required options')
        if kind == 'relation' and actual.get('relation', {}).get('database_id', '').replace('-', '') != FEEDBACK.replace('-', ''):
            raise RuntimeError('Queue Feedback relation points to the wrong database')


def provision(config_path: Path):
    config_path = Path(config_path)
    env_file = str(Path.home()/'.env')
    n = Notion(read_token(env_file))
    n.call('GET', '/pages/'+HUB)
    n.call('GET', '/databases/'+FEEDBACK)
    children = n.children(HUB)
    matches = [b for b in children if b['type']=='child_database' and b['child_database']['title']==TITLE]
    if len(matches)>1: raise RuntimeError('Multiple Product work queues found; refusing to guess')
    if matches:
        database = n.call('GET', '/databases/'+matches[0]['id'])
        validate_schema(database['properties'])
    else:
        database = n.call('POST', '/databases', {'parent': {'type': 'page_id', 'page_id': HUB},
                         'title': rich(TITLE), 'properties': schema()})
    help_matches = [b for b in children if b['type']=='child_page' and b['child_page']['title']==HELP]
    if len(help_matches)>1: raise RuntimeError('Multiple queue help pages found')
    if help_matches:
        help_page = n.call('GET', '/pages/'+help_matches[0]['id'])
    else:
        help_page = n.call('POST', '/pages', {'parent': {'page_id': HUB}, 'properties': {'title': {'title': rich(HELP)}}, 'children': [
            block('Move a product task to Ready to start an agent. Editing the feedback log alone does not launch work.'),
            block('1. Open Product work queue and create a task. Link one or more feedback entries in Feedback. Fill in Brief and Done when. Repository is sat-prep. The three initial investigations are already drafted.'),
            block('2. Choose Mode: Investigate returns findings and recommendations; Build follows the brief and requires verification commands in Checks; Prepare session creates a handoff and a session you can resume in a terminal.'),
            block('3. Change Status from Draft to Ready. The controller checks every two minutes and runs one task at a time. A Ready task waits while another task is Working. Each run is bounded to about 30 minutes of agent work plus review.'),
            block('4. Working shows the starting time and run reference. In review means results are available, not that every check passed. Read Result, Readiness and the full run sections on the page. Needs your input means clarify the decision, inspect a failure or resolve a conflict, then choose Ready for a new attempt.'),
            block('Changes during a run do not rewrite its starting brief. Returning a running task to Draft prevents automatic status overwrite but does not stop the active agent immediately. To stop the controller and its agent, run systemctl --user stop attain-work-queue.service; recovery will require your input.'),
            block('Results and earlier runs stay on the task. The Resume field gives a terminal command for a retained session/worktree; Prepare session does not open a browser chat automatically. Ready always authorizes a fresh attempt, not an automatic continuation of an old session.'),
            block('Runs can prepare code and reports in isolated branches. They do not push, merge, deploy, send messages or change customer records. Production release remains a separate decision.'),
            block('Operating controls: systemctl --user status attain-work-queue.timer; journalctl --user -u attain-work-queue.service; notion-work-queue status. Disable new starts with systemctl --user disable --now attain-work-queue.timer. Keep the saved journal and result branches when stopping.'),
            block('QUEUE HEALTH: Provisioned; activation check pending.')
        ]})
    help_blocks = n.children(help_page['id'])
    health = next((b for b in help_blocks if b['type']=='paragraph' and ''.join(t.get('plain_text','') for t in b['paragraph']['rich_text']).startswith('QUEUE HEALTH:')), None)
    if health is None:
        # Health text may have been updated since setup. Its id is authoritative in local config.
        saved = json.loads(config_path.read_text()) if config_path.exists() else {}
        if saved.get('health_block_id'): health = {'id': saved['health_block_id']}
        else:
            response = n.call('PATCH', '/blocks/'+help_page['id']+'/children', {'children': [block('QUEUE HEALTH: Activation check pending.')]})
            health = response['results'][0]
    config = {'database_id': database['id'], 'health_block_id': health['id'], 'help_page_id': help_page['id'],
              'env_file': env_file, 'state_dir': str(Path.home()/'.local/state/attain-work-queue'),
              'projects': str(Path.home()/'projects')}
    config_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    config_path.write_text(json.dumps(config, indent=2));config_path.chmod(0o600)
    # Page relation is the stable seed identity. No task status or edited brief is reset.
    existing, cursor = [], None
    while True:
        payload = {'page_size':100}
        if cursor: payload['start_cursor']=cursor
        result = n.call('POST', '/databases/'+database['id']+'/query', payload)
        existing += result['results']
        if not result.get('has_more'): break
        cursor=result['next_cursor']
    seed_pages = []
    for fid, title in SEEDS:
        match = next((p for p in existing if any(r['id'].replace('-','')==fid.replace('-','') for r in p['properties']['Feedback']['relation'])), None)
        if match:
            seed_pages.append(match);continue
        feedback = n.call('GET', '/pages/'+fid)
        fb_blocks = n.children(fid)
        next_step = plain(feedback['properties']['Next step'])
        content = [''.join(t.get('plain_text','') for t in b.get(b['type'],{}).get('rich_text',[])) for b in fb_blocks]
        done = next((s for s in content if s.startswith('Done criteria')), 'A source-backed recommendation, options and a concrete next step; no application changes.')
        created = n.call('POST', '/pages', {'parent': {'database_id': database['id']}, 'properties': {
            'Task': {'title': rich(title)}, 'Status': {'select': {'name':'Draft'}}, 'Mode': {'select': {'name':'Investigate'}},
            'Repository': {'select': {'name':'sat-prep'}}, 'Brief': {'rich_text': rich(next_step)},
            'Done when': {'rich_text': rich(done)}, 'Feedback': {'relation': [{'id':fid}]}
        }, 'children': [block('Source feedback: '+feedback['url']), block('This is an investigation task. Choose Ready to authorize work.')]})
        seed_pages.append(created)
    return {'queue_url': database['url'], 'help_url': help_page['url'], 'draft_tasks': [p['url'] for p in seed_pages], 'config_path': str(config_path)}
