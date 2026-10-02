#!/usr/bin/env python3
"""Mirror compatible Claude settings without changing Codex execution policy."""
import argparse
import datetime as dt
import hashlib
import fcntl
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import tomllib

LOCK_TIMEOUT_SECONDS = 30.0

START = '# BEGIN managed Claude mirror'
END = '# END managed Claude mirror'
CONNECTORS = {
    'authority-hacker': 'https://plugins.authorityhacker.com/mcp',
    'slack': 'https://mcp.slack.com/mcp',
    'notion': 'https://mcp.notion.com/mcp',
    'google-drive': 'https://drivemcp.googleapis.com/mcp/v1',
    'supabase': 'https://mcp.supabase.com/mcp',
    'google-calendar': 'https://calendarmcp.googleapis.com/mcp/v1',
    'gmail': 'https://gmailmcp.googleapis.com/mcp/v1',
    'context7': 'https://mcp.context7.com/mcp',
}
GUIDANCE = '''# Shared working agreement

Read `/home/dev/.claude/CLAUDE.md` in full and follow it. It owns the working
agreement; do not duplicate its protocol here.

For the repository being worked on, also read its applicable `CLAUDE.md` and
`AGENTS.md` files. Keep project-specific instructions scoped to that project.

The Claude mirror uses maintained skill sources and Codex adapters. In imported
instructions, map Claude's Read/Edit/Bash to the available filesystem and shell
tools, WebSearch/WebFetch to available web tools, and AskUserQuestion to the
available question mechanism. Use the installed Codex delegate skill for model
routing and agent dispatch. Tool names in a source document do not prove that
those tools or account connections are available in this session.

For Claude account connectors, consult the installed `claude-connectors` skill
when a native tool is unavailable. Reuse the authenticated Claude client for the
specific user-authorized task; do not copy OAuth caches between applications.
'''

def atomic_write(path, data, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.'+path.name+'.')
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)

def read_source_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise ValueError(f'Cannot read Claude source settings at {path}; preserve installed config and restore the source before refreshing') from error


def make_config(original, home):
    text = original.decode()
    if text.count(START) != text.count(END) or text.count(START)>1:
        raise ValueError('Invalid managed block boundaries')
    if START in text:
        first = text.index(START); last = text.index(END)+len(END)
        text = text[:first]+text[last:]
    base = text.rstrip()+'\n'
    existing = tomllib.loads(base)
    claude = read_source_json(home/'.claude/settings.json')
    lines = [START]
    configured = existing.get('mcp_servers',{})
    notion_plugins = home/'.codex/plugins/cache/openai-curated-remote/notion'
    notion_plugin_present = any(notion_plugins.glob('*/.codex-plugin/plugin.json'))
    for name,url in CONNECTORS.items():
        if name in configured or (name == 'notion' and notion_plugin_present): continue
        lines += [f'[mcp_servers.{name}]',f'url = {json.dumps(url)}','startup_timeout_sec = 20','tool_timeout_sec = 90','']
    if 'playwright' not in configured:
        source = read_source_json(home/'.claude.json').get('mcpServers',{}).get('playwright')
        if source and source.get('command') and not source.get('env'):
            lines += ['[mcp_servers.playwright]',f'command = {json.dumps(source["command"])}',f'args = {json.dumps(source.get("args",[]))}','startup_timeout_sec = 30','']
    prior = {str(Path(x['path']).expanduser()):x for x in existing.get('skills',{}).get('config',[])}
    disabled=[]
    for name,value in sorted(claude.get('skillOverrides',{}).items()):
        if value != 'off': continue
        for root in [home/'.codex/skills',home/'.agents/skills']:
            path = root/name/'SKILL.md'
            if not path.exists(): continue
            disabled.append(name)
            if str(path) in prior:
                if prior[str(path)].get('enabled') is not False:
                    raise ValueError('Conflicting explicit Codex skill preference: '+name)
                continue
            lines += ['[[skills.config]]',f'path = {json.dumps(str(path))}','enabled = false','']
    result = base+'\n'+'\n'.join(lines+[END])+'\n'
    parsed=tomllib.loads(result)
    for key in ['model','model_reasoning_effort','approvals_reviewer','approval_policy','sandbox_mode','projects','rules','hooks']:
        if parsed.get(key)!=existing.get(key): raise ValueError('Unrelated setting changed: '+key)
    return result.encode(), sorted(set(disabled))

def apply(home, dry_run=True):
    if dry_run:
        return _apply(home, True)
    lock_path = home/'.codex/mirrors/claude/settings.lock'
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('Another settings mirror holds the lock; retry after that run finishes')
                time.sleep(min(.1, max(0, deadline - time.monotonic())))
        return _apply(home, False)


def _apply(home, dry_run):
    config=home/'.codex/config.toml'
    original=config.read_bytes()
    report_path=home/'.codex/mirrors/claude/settings-report.json'
    if START in original.decode():
        if not report_path.exists():
            raise ValueError('Missing managed-block ownership evidence; preserve config and restore the report before refreshing')
        previous=json.loads(report_path.read_text())
        block=original.decode().split(START,1)[1].split(END,1)[0]
        known=previous.get('managed_block_sha256')
        if not known:
            raise ValueError('Missing managed-block ownership evidence; preserve config and restore the report before refreshing')
        if hashlib.sha256(block.encode()).hexdigest()!=known:
            raise ValueError('Managed connector/skill settings were edited; preserve and review before refreshing')
    candidate,disabled=make_config(original,home)
    guidance=home/'.codex/AGENTS.md'
    report={'config_changed':candidate!=original,'disabled_skills':disabled,
            'connector_names':sorted(tomllib.loads(candidate.decode()).get('mcp_servers',{})),
            'authentication':'Configuration only; each connector requires a separate health/authentication check.',
            'guidance':'preserve_existing' if guidance.exists() else 'create_canonical_pointer'}
    if dry_run: return report
    if config.read_bytes()!=original: raise RuntimeError('Config changed during mirror; retry after review')
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup=home/'.codex/mirrors/claude/backups'/stamp
    if candidate!=original:
        backup.mkdir(parents=True,mode=0o700)
        atomic_write(backup/'config.toml',original)
        if config.read_bytes()!=original: raise RuntimeError('Config changed during mirror; retry after review')
        atomic_write(config,candidate)
    if not guidance.exists():
        atomic_write(guidance,GUIDANCE.replace('/home/dev',str(home)).encode())
    report['backup']=str(backup) if backup.exists() else None
    report['config_sha256']=hashlib.sha256(config.read_bytes()).hexdigest()
    block=candidate.decode().split(START,1)[1].split(END,1)[0]
    report['managed_block_sha256']=hashlib.sha256(block.encode()).hexdigest()
    atomic_write(home/'.codex/mirrors/claude/settings-report.json',json.dumps(report,indent=2).encode()+b'\n')
    return report

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home',type=Path,default=Path.home())
    parser.add_argument('--apply',action='store_true')
    args=parser.parse_args()
    try:
        print(json.dumps(apply(args.home,dry_run=not args.apply),indent=2))
    except (OSError, ValueError, RuntimeError) as error:
        print(str(error),file=sys.stderr)
        raise SystemExit(2)
