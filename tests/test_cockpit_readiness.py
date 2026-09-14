"""Readiness must follow evidence, never task volume or a saved completion."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from cockpit import sources
from cockpit.app import create_app
from tests.test_cockpit import world, login


@pytest.fixture
def assessment(world, tmp_path):
    app, backlog = world
    source = tmp_path / 'sat-prep/docs/contract.md'
    source.parent.mkdir(parents=True)
    source.write_text('# Teaching contract\nA prepared design, not a learner result.\n')
    observed = datetime.now(timezone.utc) - timedelta(hours=2)
    profile = dict(
        as_of=observed.isoformat(), review_by=(observed + timedelta(days=14)).isoformat(),
        summary='A limited pilot has a defined scope.', scope='Market product',
        next_step='Build and check the teaching loop.',
        sources=[dict(id='contract', title='Teaching contract', path='sat-prep/docs/contract.md',
                      sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
                      as_of=observed.isoformat(), note='Document inspection only.')],
        capabilities=[dict(id='product', title='Product development', level=1, target=3,
                           target_basis='proposed', note='The teaching contract defines the work.',
                           evidence=['contract'], next_step='Check the implemented loop.')],
        outcomes=[dict(id='IS-01', title='Learning lasts', state='unknown',
                       note='No learner result is established.', evidence=[],
                       next_step='Observe a fresh independent answer later.')],
        requirements=[dict(id='design', title='Teaching rules prepared', state='verified', critical=True,
                           capability='product', outcomes=['IS-01'], evidence=['contract'],
                           note='The contract is authored. This check covers preparation only.',
                           next_step='Preserve the agreed teaching rules.', depends_on=[]),
                      dict(id='delivery', title='Teaching loop works', state='missing', critical=True,
                           capability='product', outcomes=['IS-01'], evidence=['contract'],
                           note='Implementation has not been established.',
                           next_step='Implement and evaluate the loop.', depends_on=['design'])],
        workstreams=[dict(id='tutor', title='Tutor pilot', purpose='Prove a bounded teaching loop.',
                         stages=[dict(id='pilot', title='Pilot', definition='A learner can complete the loop.',
                                      basis='proposed', requirements=['design', 'delivery'])])],
        work_links=[])
    path = tmp_path / '.cockpit/initiative-profiles.json'
    path.parent.mkdir()
    def save(value=None):
        path.write_text(json.dumps({'version': 1, 'initiatives': {'attain': value or profile}}))
        path.chmod(0o600)
    save()
    return app, profile, save, source


def read_profile(app):
    return sources.snapshot(app.config)['initiatives'][0]['profile']


def test_check_counts_never_turn_authored_design_into_pilot_readiness(assessment):
    app, _, _, _ = assessment
    p = read_profile(app)
    stage = p['workstreams'][0]['stages'][0]
    assert stage['state'] == 'needs_work'
    assert stage['counts'] == dict(verified=1, total=2, unknown=0, remaining=1)
    assert stage['critical_gaps'] == ['delivery']
    assert p['outcomes'][0]['state'] == 'unknown'
    assert p['requirements'][1]['work'] == []


def test_changed_local_evidence_downgrades_claims_without_losing_gaps(assessment):
    app, _, _, source = assessment
    source.write_text('# Revised teaching rules\n')
    p = read_profile(app)
    assert p['sources'][0]['state'] == 'changed'
    assert p['changed_evidence'] is True
    assert p['capabilities'][0]['level'] is None
    assert p['capabilities'][0]['recorded_level'] == 1
    assert p['requirements'][0]['state'] == 'unknown'
    assert p['requirements'][0]['recorded_state'] == 'verified'
    assert p['workstreams'][0]['stages'][0]['counts']['verified'] == 0


def test_verified_check_with_unverified_prerequisite_is_not_ready(assessment):
    app, p, save, _ = assessment
    p['requirements'][0]['state'] = 'missing'
    p['requirements'][1]['state'] = 'verified'
    p['workstreams'][0]['stages'][0]['requirements'] = ['delivery']
    save()
    stage = read_profile(app)['workstreams'][0]['stages'][0]
    assert stage['counts']['verified'] == 0
    assert stage['state'] == 'needs_work'
    assert stage['dependency_gaps'] == ['design']


@pytest.mark.parametrize('change', ['empty_stage', 'unsupported_claim', 'expired', 'future'])
def test_incomplete_or_expired_evidence_cannot_appear_ready(assessment, change):
    app, p, save, _ = assessment
    p['requirements'][1]['state'] = 'verified'
    if change == 'empty_stage': p['workstreams'][0]['stages'][0]['requirements'] = []
    if change == 'unsupported_claim': p['requirements'][0]['evidence'] = []
    if change == 'expired': p['review_by'] = (datetime.fromisoformat(p['as_of']) + timedelta(seconds=1)).isoformat()
    if change == 'future': p['as_of'] = '2099-01-01T00:00:00Z'
    save()
    p = read_profile(app)
    if p['state'] == 'unavailable': return
    stage = p['workstreams'][0]['stages'][0]
    assert stage['state'] != 'verified'
    assert stage['counts']['verified'] < stage['counts']['total'] or stage['counts']['total'] == 0


@pytest.mark.parametrize('change', ['cycle', 'dangling_dependency', 'duplicate_requirement', 'bad_target', 'bad_date'])
def test_invalid_profiles_stay_visible_and_do_not_hide_source_work(assessment, change):
    app, p, save, _ = assessment
    if change == 'cycle': p['requirements'][0]['depends_on'] = ['delivery']
    if change == 'dangling_dependency': p['requirements'][0]['depends_on'] = ['missing']
    if change == 'duplicate_requirement': p['requirements'].append(deepcopy(p['requirements'][0]))
    if change == 'bad_target': p['capabilities'][0]['target'] = True
    if change == 'bad_date': p['review_by'] = 'not-a-date'
    save()
    data = sources.snapshot(app.config)
    assert data['initiatives'][0]['profile']['state'] == 'unavailable'
    assert data['work'][0]['id'] == 'item-1'
    assert any(s['name'] == 'Initiative assessments' and s['status'] == 'unavailable' for s in data['sources'])


def test_exact_work_mapping_rejects_changed_records_and_does_not_credit_completion(assessment):
    app, p, save, _ = assessment
    work = sources.snapshot(app.config)['work'][0]
    p['work_links'] = [dict(source=work['source'], id=work['id'], revision=work['context_revision'],
                           requirements=['delivery'])]
    save()
    mapped = read_profile(app)
    assert mapped['requirements'][1]['work'][0]['id'] == 'item-1'
    assert mapped['requirements'][1]['state'] == 'missing'
    assert mapped['unmapped_work'] == []
    p['work_links'][0]['revision'] = 'old-revision'
    save()
    mapped = read_profile(app)
    assert mapped['requirements'][1]['work'] == []
    assert mapped['unmapped_work'][0]['id'] == 'item-1'
    assert mapped['mapping_issues'][0]['reason'] == 'changed'


@pytest.mark.parametrize('change', ['duplicate', 'foreign', 'archive'])
def test_mapping_identity_and_completed_work_never_infer_readiness(assessment, change):
    from cockpit import readiness
    app, p, _, _ = assessment
    row = sources.snapshot(app.config)['work'][0]
    p['work_links'] = [dict(source=row['source'], id=row['id'], revision=row['context_revision'], requirements=['delivery'])]
    initiative = dict(id='attain', repos=['sat-prep'])
    work, results = [row], []
    if change == 'duplicate': work.append(deepcopy(row))
    if change == 'foreign': row['initiative_id'] = 'other'
    if change == 'archive':
        row.update(source='Backlog archive', status='done', outcome_label='Merged')
        p['work_links'][0]['source'] = 'Backlog archive'
        work, results = [], [row]
    projected = readiness._derive(p, initiative, work, results, app.config, datetime.now(timezone.utc))
    if change == 'archive':
        assert projected['requirements'][1]['work'][0]['outcome_label'] == 'Merged'
        assert projected['requirements'][1]['state'] == 'missing'
    else:
        assert projected['requirements'][1]['work'] == []
        assert projected['mapping_issues'][0]['reason'] == ('duplicate' if change == 'duplicate' else 'different_initiative')


def test_missing_evidence_and_absent_practice_are_different(assessment):
    app, p, save, source = assessment
    p['capabilities'][0]['level'] = 0
    save()
    assert read_profile(app)['capabilities'][0]['level'] == 0
    source.unlink()
    result = read_profile(app)
    assert result['capabilities'][0]['level'] is None
    assert result['sources'][0]['state'] == 'unavailable'


def test_all_verified_requirements_with_evidence_support_a_stage(assessment):
    app, p, save, _ = assessment
    p['requirements'][1]['state'] = 'verified'
    save()
    assert read_profile(app)['workstreams'][0]['stages'][0]['state'] == 'verified'
    assert read_profile(app)['outcomes'][0]['state'] == 'unknown'


def test_absent_profile_has_an_honest_empty_state(world):
    app, _ = world
    p = read_profile(app)
    assert p['state'] == 'missing'
    assert p['unmapped_work'][0]['id'] == 'item-1'


def test_missing_initiative_id_cannot_take_down_the_whole_snapshot(assessment):
    from pathlib import Path
    app, _, _, _ = assessment
    catalog = Path(app.config['CATALOG_PATH'])
    data = json.loads(catalog.read_text())
    data['initiatives'][0].pop('id')
    catalog.write_text(json.dumps(data))
    snapshot = sources.snapshot(app.config)
    assert snapshot['initiatives'][0]['profile']['state'] == 'unavailable'
    assert snapshot['work'][0]['id'] == 'item-1'


@pytest.mark.parametrize('repos', [None, 17, {'wrong': 'shape'}])
def test_invalid_repository_links_do_not_hide_source_work(assessment, repos):
    from pathlib import Path
    app, _, _, _ = assessment
    catalog = Path(app.config['CATALOG_PATH'])
    data = json.loads(catalog.read_text())
    data['initiatives'][0]['repos'] = repos
    catalog.write_text(json.dumps(data))
    snapshot = sources.snapshot(app.config)
    assert snapshot['initiatives'][0]['profile']['state'] == 'unavailable'
    assert snapshot['work'][0]['id'] == 'item-1'


@pytest.mark.parametrize('state', ['missing', 'malformed', 'encoding'])
def test_assessment_file_failure_has_a_specific_explanation(assessment, state):
    from pathlib import Path
    app, _, _, _ = assessment
    path = Path(app.config['INITIATIVES_PATH'])
    if state == 'missing': path.unlink()
    if state == 'malformed': path.write_text('{invalid-json')
    if state == 'encoding': path.write_bytes(b'\xff')
    data = sources.snapshot(app.config)
    status = next(s for s in data['sources'] if s['name'] == 'Initiative assessments')
    assert status['status'] == 'unavailable'
    assert {'missing': 'not been prepared', 'malformed': 'invalid', 'encoding': 'encoding'}[state] in status['detail']
    assert data['work'][0]['id'] == 'item-1'


def test_source_document_requires_login_and_never_reads_an_arbitrary_path(assessment, tmp_path):
    app, p, save, source = assessment
    c = app.test_client()
    route = '/initiatives/attain/sources/contract'
    assert c.get(route).status_code == 302
    login(c)
    assert b'A prepared design, not a learner result.' in c.get(route).data
    assert c.get('/initiatives/attain/sources/missing').status_code == 404
    outside = tmp_path.parent / 'private-contract.md'
    outside.write_text('DO NOT EXPOSE THIS')
    p['sources'][0]['path'] = '../private-contract.md'
    save()
    assert c.get(route).status_code in (404, 503)
    assert b'DO NOT EXPOSE THIS' not in c.get(route).data


def test_unrelated_bad_requirement_does_not_hide_an_inspectable_source(assessment):
    app, p, save, _ = assessment
    p['requirements'][0]['depends_on'] = ['missing']
    save()
    c = app.test_client(); login(c)
    assert c.get('/initiatives/attain/sources/contract').status_code == 200


def test_broken_source_reports_unavailable_instead_of_nonexistent(assessment):
    app, _, _, source = assessment
    source.write_bytes(b'\xff\xfe')
    c = app.test_client(); login(c)
    assert c.get('/initiatives/attain/sources/contract').status_code == 503
    assert c.get('/initiatives/attain/sources/not-configured').status_code == 404


def test_changed_parent_symlink_cannot_escape_the_document_root(assessment, tmp_path):
    app, _, _, source = assessment
    outside = tmp_path.parent / ('outside-' + tmp_path.name)
    outside.mkdir()
    (outside / 'contract.md').write_text('PRIVATE OUTSIDE CONTENT')
    source.unlink()
    source.parent.rmdir()
    source.parent.symlink_to(outside, target_is_directory=True)
    c = app.test_client(); login(c)
    response = c.get('/initiatives/attain/sources/contract')
    assert response.status_code in (404, 503)
    assert b'PRIVATE OUTSIDE CONTENT' not in response.data


def test_overbroad_profile_permissions_are_visible_without_hiding_work(assessment):
    app, _, _, _ = assessment
    from pathlib import Path
    Path(app.config['INITIATIVES_PATH']).chmod(0o644)
    data = sources.snapshot(app.config)
    status = next(s for s in data['sources'] if s['name'] == 'Initiative assessments')
    assert status['status'] == 'failed'
    assert data['initiatives'][0]['profile']['state'] == 'available'
    assert data['work'][0]['id'] == 'item-1'


def test_assessment_change_invalidates_unseen_brief_review(assessment):
    app, p, save, _ = assessment
    c = app.test_client()
    csrf = login(c)
    token = c.get('/api/snapshot').json['brief']['review_token']
    p['requirements'][0]['note'] = 'The design review found a conflicting rule.'
    save()
    response = c.post('/api/brief/review', json={'token': token}, headers={'X-CSRF-Token': csrf})
    assert response.status_code == 409


def test_assessment_reads_preserve_existing_sources(assessment):
    app, _, _, source = assessment
    before = source.read_bytes()
    data = sources.snapshot(app.config)
    assert data['initiatives'][0]['profile']['state'] == 'available'
    assert source.read_bytes() == before
