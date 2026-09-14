"""Dated initiative assessments over existing evidence and work. Reads never infer success."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from urllib.parse import urlsplit


REQUIREMENT_STATES = {'unknown', 'missing', 'defined', 'built', 'verified', 'blocked'}
OUTCOME_STATES = {'unknown', 'partial', 'supported', 'contradicted'}
LEVELS = {None, 0, 1, 2, 3, 4}


class SourceNotFound(Exception):
    pass


class SourceUnavailable(Exception):
    pass


def _text(value, limit=4000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError('Invalid assessment text')
    return value


def _date(value):
    date = datetime.fromisoformat(_text(value, 50).replace('Z', '+00:00'))
    if date.tzinfo is None:
        raise ValueError('Assessment dates need a timezone')
    return date


def _id(value):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', _text(value, 80)):
        raise ValueError('Invalid assessment identity')
    return value


def _rows(value, limit=200):
    if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, dict) for v in value):
        raise ValueError('Invalid assessment records')
    return value


def _index(rows):
    result = {_id(row['id']): row for row in _rows(rows)}
    if len(result) != len(rows):
        raise ValueError('Duplicate assessment identity')
    return result


def _refs(value, known):
    if (not isinstance(value, list) or any(not isinstance(v, str) for v in value)
            or len(set(value)) != len(value) or not set(value) <= set(known)):
        raise ValueError('Invalid assessment reference')


def _object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError('Duplicate JSON key')
    return result


def _read_fd(fd, limit):
    with os.fdopen(fd, 'rb') as stream:
        mode = os.fstat(stream.fileno()).st_mode
        if not stat.S_ISREG(mode):
            raise ValueError('Evidence must be a regular file')
        raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError('Evidence exceeds supported size')
    return raw, mode


def read_profiles(config):
    path = Path(config.get('INITIATIVES_PATH') or Path(config['PROJECTS_ROOT']) / '.cockpit/initiative-profiles.json')
    raw, mode = _read_fd(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), 2_000_000)
    data = json.loads(raw.decode('utf-8'), object_pairs_hook=_object)
    if type(data.get('version')) is not int or data['version'] != 1 or not isinstance(data.get('initiatives'), dict) or len(data['initiatives']) > 100:
        raise ValueError('Invalid assessment file')
    return data['initiatives'], not bool(mode & 0o077)


def local_source(config, source):
    """Only exact configured Markdown documents under the project root can be read."""
    relative = Path(_text(source.get('path'), 1000))
    root = Path(config['PROJECTS_ROOT']).resolve()
    if relative.is_absolute() or '..' in relative.parts or relative.suffix.lower() != '.md':
        raise ValueError('Source path is outside the document boundary')
    # Resolve each component from an open parent, refusing symlink swaps between
    # the boundary check and the read. Never open a request-supplied absolute path.
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in relative.parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        fd = os.open(relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    raw, _ = _read_fd(fd, 500_000)
    return raw.decode('utf-8'), hashlib.sha256(raw).hexdigest()


def _validate_source(source, observed=None):
    _id(source['id']); _text(source['title']); _text(source['note'])
    inspected = _date(source['as_of'])
    if observed is not None and inspected > observed:
        raise ValueError('Source is newer than its assessment')
    if 'path' in source:
        _text(source['path'], 1000)
        if not re.fullmatch(r'[a-f0-9]{64}', _text(source['sha256'], 64)):
            raise ValueError('Invalid document fingerprint')
    else:
        url = urlsplit(_text(source['url'], 2000))
        if url.scheme not in ('https', 'http') or not url.netloc or url.username or url.password:
            raise ValueError('Invalid source URL')
        _text(source['revision'], 200)


def _validate(profile, now):
    if not isinstance(profile, dict):
        raise ValueError('Invalid profile')
    for name in ('summary', 'scope', 'next_step'):
        _text(profile[name])
    if 'target_scope' in profile:
        _text(profile['target_scope'])
    observed, review = _date(profile['as_of']), _date(profile['review_by'])
    if observed > now or review <= observed:
        raise ValueError('Invalid assessment window')
    sources = _index(profile['sources'])
    capabilities = _index(profile['capabilities'])
    outcomes = _index(profile['outcomes'])
    requirements = _index(profile['requirements'])
    for source in sources.values():
        _validate_source(source, observed)
    for row in [*capabilities.values(), *outcomes.values(), *requirements.values()]:
        for name in ('title', 'note', 'next_step'):
            _text(row[name])
        _refs(row['evidence'], sources)
    for row in capabilities.values():
        for key in ('level', 'target'):
            if row[key] is not None and (type(row[key]) is not int or row[key] not in LEVELS):
                raise ValueError('Invalid maturity level')
        if row['target_basis'] not in ('agreed', 'proposed'):
            raise ValueError('Invalid target basis')
    for row in outcomes.values():
        if row['state'] not in OUTCOME_STATES:
            raise ValueError('Invalid outcome state')
    for row in requirements.values():
        if row['state'] not in REQUIREMENT_STATES or type(row['critical']) is not bool:
            raise ValueError('Invalid readiness state')
        if row['capability'] not in capabilities:
            raise ValueError('Unknown capability')
        _refs(row['outcomes'], outcomes)
        _refs(row['depends_on'], requirements)
    pending = {key: set(row['depends_on']) for key, row in requirements.items()}
    order = []
    while pending:
        ready = {key for key, deps in pending.items() if not deps}
        if not ready:
            raise ValueError('Cyclic readiness dependencies')
        order.extend(sorted(ready))
        pending = {key: deps - ready for key, deps in pending.items() if key not in ready}
    streams = _index(profile['workstreams'])
    for stream in streams.values():
        _text(stream['title']); _text(stream['purpose'])
        for stage in _index(stream['stages']).values():
            _text(stage['title']); _text(stage['definition'])
            if stage['basis'] not in ('agreed', 'proposed'):
                raise ValueError('Invalid stage basis')
            _refs(stage['requirements'], requirements)
    for row in _rows(profile['work_links'], 1000):
        for name in ('source', 'id', 'revision'):
            _text(row[name], 500)
        _refs(row['requirements'], requirements)
    return order


def _work(row):
    # Never embed signed actions or private raw records in the assessment.
    return {key: row.get(key) for key in ('id', 'source', 'title', 'status', 'category_label',
        'source_url', 'prompt_url', 'outcome_label', 'owner_brief', 'completed_at')}


def _belongs(row, initiative):
    key = initiative.get('id')
    if not isinstance(key, str) or not key:
        return False
    repos = initiative.get('repos', [])
    if not isinstance(repos, list):
        repos = []
    return row.get('initiative_id') == key or (
        not row.get('initiative_id') and row.get('repo') in repos)


def _derive(raw, initiative, work, results, config, now):
    order = _validate(raw, now)
    p = deepcopy(raw)
    p['state'] = 'stale' if now > _date(p['review_by']) else 'available'
    p['message'] = ('This assessment is due for review. Earlier claims are shown as unknown.'
                    if p['state'] == 'stale' else 'A prepared assessment of dated evidence. Refresh reads work; it does not repeat the assessment.')
    evidence = {}
    for source in p['sources']:
        if 'path' in source:
            try:
                _, digest = local_source(config, source)
                source['state'] = 'current' if digest == source['sha256'] else 'changed'
            except (OSError, ValueError, UnicodeError):
                source['state'] = 'unavailable'
            source['url'] = '/initiatives/' + initiative['id'] + '/sources/' + source['id']
        else:
            source['state'] = 'dated'
        evidence[source['id']] = source
        source.pop('path', None)
        source.pop('sha256', None)
    p['changed_evidence'] = any(s['state'] in ('changed', 'unavailable') for s in p['sources'])
    requirements = {r['id']: r for r in p['requirements']}
    for row in [*p['requirements'], *p['capabilities'], *p['outcomes']]:
        valid = (p['state'] == 'available' and bool(row['evidence'])
                 and all(evidence[key]['state'] in ('current', 'dated') for key in row['evidence']))
        row['evidence_state'] = 'recorded' if valid else 'needs_review'
        if 'level' in row:
            row['recorded_level'] = row['level']
            if not valid: row['level'] = None
        else:
            row['recorded_state'] = row['state']
            if not valid: row['state'] = 'unknown'
        row['work'] = []
    for key in order:
        row = requirements[key]
        row['satisfied'] = row['state'] == 'verified' and all(requirements[d]['satisfied'] for d in row['depends_on'])
        gaps = set()
        for dependency in row['depends_on']:
            if not requirements[dependency]['satisfied']: gaps.add(dependency)
            gaps.update(requirements[dependency]['dependency_gaps'])
        row['dependency_gaps'] = sorted(gaps)
    for stream in p['workstreams']:
        for stage in stream['stages']:
            rows = [requirements[key] for key in stage['requirements']]
            done = sum(row['satisfied'] for row in rows)
            unknown = sum(row['state'] == 'unknown' for row in rows)
            stage['counts'] = dict(verified=done, total=len(rows), unknown=unknown, remaining=len(rows)-done-unknown)
            stage['state'] = 'unknown' if not rows or unknown == len(rows) else ('verified' if done == len(rows) else 'needs_work')
            stage['critical_gaps'] = [row['id'] for row in rows if row['critical'] and not row['satisfied']]
            stage['dependency_gaps'] = sorted({key for row in rows for key in row['dependency_gaps']})
    all_work = work + results
    identities = Counter((row.get('source'), row.get('id')) for row in all_work)
    candidates = {(row.get('source'), row.get('id')): row for row in all_work}
    links = Counter((row['source'], row['id']) for row in p['work_links'])
    mapped, p['mapping_issues'] = set(), []
    for ref in p.pop('work_links'):
        key = (ref['source'], ref['id'])
        row = candidates.get(key)
        reason = ('missing' if row is None else 'duplicate' if identities[key] != 1 or links[key] != 1
                  else 'different_initiative' if not _belongs(row, initiative)
                  else 'changed' if row.get('context_revision') != ref['revision'] else '')
        if reason:
            p['mapping_issues'].append(dict(source=ref['source'], id=ref['id'], reason=reason))
            continue
        if ref['requirements']:
            mapped.add(key)
        for req in ref['requirements']:
            requirements[req]['work'].append(_work(row))
    p['unmapped_work'] = [_work(row) for row in work if _belongs(row, initiative) and (row['source'], row['id']) not in mapped]
    return p


def attach(config, initiatives, work, results, source_status):
    now = datetime.now(timezone.utc)
    private = True
    failure = ''
    try:
        profiles, private = read_profiles(config)
        file_state = 'available'
    except FileNotFoundError:
        profiles, file_state = {}, 'missing'
        failure = 'A private initiative assessment file has not been prepared at the configured location.'
    except UnicodeError:
        profiles, file_state = {}, 'unavailable'
        failure = 'The assessment file encoding is invalid. Restore the prepared UTF-8 file.'
    except OSError:
        profiles, file_state = {}, 'unavailable'
        failure = 'The assessment file cannot be opened. Restore access to the prepared file.'
    except (ValueError, TypeError, AttributeError, RecursionError):
        profiles, file_state = {}, 'unavailable'
        failure = 'The assessment file has an invalid format. Restore the complete prepared file.'
    invalid = file_state == 'unavailable'
    for initiative in initiatives:
        key = initiative.get('id')
        fallback = dict(state='missing' if file_state != 'unavailable' else 'unavailable',
                        message='A readiness assessment has not been prepared for this initiative.' if file_state != 'unavailable'
                        else 'The assessment could not be read. Source work remains available.',
                        unmapped_work=[])
        try:
            _id(key)
            repos = initiative.get('repos', [])
            if initiative.get('catalog_error') or not isinstance(repos, list) or not all(isinstance(repo, str) for repo in repos):
                raise ValueError('invalid initiative repository links')
            fallback['unmapped_work'] = [_work(row) for row in work if _belongs(row, initiative)]
            if key in profiles:
                fallback = _derive(profiles[key], initiative, work, results, config, now)
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            invalid = True
            fallback.update(state='unavailable', message='The assessment could not be read. Source work remains available.')
        initiative['profile'] = fallback
    if failure:
        detail = failure + ' Source work remains visible.'
    elif invalid:
        detail = 'One or more initiative assessments have invalid records. Valid assessments and source work remain visible.'
    elif not private:
        detail = 'The private assessment file allows access by other local users. Restore owner-only file permissions (0600). Source work remains visible.'
    else:
        detail = 'Prepared assessments retain their evidence dates. Missing, changed, and unassessed coverage stays visible.'
    source_status.append(dict(name='Initiative assessments', status='unavailable' if invalid or file_state != 'available' else ('available' if private else 'failed'),
        detail=detail,
        observed_at=now.isoformat(), updated_at=None))


def source_document(config, initiative_id, source_id):
    """Lookup a configured identity, never a request-supplied document path."""
    try:
        _id(initiative_id); _id(source_id)
    except ValueError as exc:
        raise SourceNotFound from exc
    try:
        profiles, _ = read_profiles(config)
        if initiative_id not in profiles:
            raise SourceNotFound
        profile = profiles[initiative_id]
        matches = [s for s in _rows(profile['sources']) if s.get('id') == source_id]
        if not matches:
            raise SourceNotFound
        if len(matches) != 1:
            raise ValueError('Duplicate source identity')
        source = matches[0]
        _validate_source(source)
        if 'path' not in source:
            raise SourceNotFound
        body, digest = local_source(config, source)
    except FileNotFoundError as exc:
        raise SourceUnavailable('A configured source file is missing') from exc
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
        raise SourceUnavailable(type(exc).__name__) from exc
    return dict(title=source['title'], body=body, as_of=source['as_of'], note=source['note'],
                changed=digest != source['sha256'], initiative_id=initiative_id)
