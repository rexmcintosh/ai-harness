'use strict';

// A focused view over the server's assessed records. No scoring or task writes here.
window.CockpitInitiatives = (() => {
  const LEVELS = ['Missing', 'Defined', 'Used', 'Dependable', 'Improving'];
  const LABELS = { unknown: 'Unknown', missing: 'Missing', defined: 'Defined', built: 'Built, not checked',
    verified: 'Verified', blocked: 'Blocked', partial: 'Partial evidence', supported: 'Supported within scope', contradicted: 'Contradicted' };
  let chosen, view = 'readiness', streamId, stageId, currentData, helpers;
  let el, add, list, text, badge, link, fmt;
  const get = (id) => document.getElementById(id);
  const button = (label, cls, handler) => {
    const node = el('button', cls, label);
    node.type = 'button';
    node.addEventListener('click', handler);
    return node;
  };
  const heading = (title, note) => add(el('div', 'assessment-heading'), el('h3', '', title), el('p', 'muted', note));

  function evidence(row, profile) {
    const sources = list(row.evidence).map((id) => list(profile.sources).find((s) => s.id === id)).filter(Boolean);
    if (!sources.length) return el('p', 'evidence-note', 'Evidence has not been recorded for this assessment.');
    const block = el('div', 'assessment-evidence');
    sources.forEach((source) => add(block, add(el('div', 'evidence-reference'),
      link(source.url, source.title), el('small', '', `Inspected ${fmt(source.as_of, 'day')}. ${source.state === 'changed' ? 'Document changed; review needed.' : source.state === 'unavailable' ? 'Document unavailable.' : source.note}`))));
    return block;
  }

  function workItem(item) {
    const brief = helpers.ownerBrief(item);
    const title = brief.state === 'current' ? brief.title : text(item.title, 'Untitled work');
    const row = add(el('div', 'mapped-work'), add(el('div', 'mapped-work-heading'), el('h5', '', title),
      badge(item.category_label ? 'queued' : 'unknown', text(item.outcome_label, text(item.category_label, item.status)))));
    if (brief.state === 'current') row.append(el('p', '', brief.why));
    if (item.source_url) row.append(link(item.source_url, 'Open work record'));
    if (item.prompt_url) row.append(helpers.copyPromptControl(item));
    return row;
  }

  function requirement(row, profile) {
    const item = el('details', 'requirement');
    item.dataset.requirement = row.id;
    const label = row.state === 'verified' && !row.satisfied ? 'Prerequisite not verified' : LABELS[row.state] || 'Unknown';
    const summary = add(el('summary', 'requirement-summary'),
      add(el('div', ''), el('span', 'requirement-title', row.title),
        el('small', '', row.critical ? 'Required for this stage' : 'Supporting condition')),
      badge(row.satisfied ? 'verified' : row.state === 'verified' ? 'unknown' : row.state, label));
    const detail = add(el('div', 'requirement-detail'), el('p', '', row.note));
    if (row.recorded_state !== row.state) detail.append(el('p', 'assessment-warning', `Earlier assessment: ${LABELS[row.recorded_state] || row.recorded_state}. Its evidence needs review.`));
    if (list(row.depends_on).length) {
      const names = row.depends_on.map((id) => list(profile.requirements).find((r) => r.id === id)?.title || id);
      detail.append(el('p', 'dependency-note', `Depends on: ${names.join('; ')}.`));
    }
    add(detail, el('p', 'requirement-next', `Next: ${row.evidence_state === 'needs_review' && row.recorded_state !== 'unknown' ? 'Review the changed or missing evidence before relying on this assessment.' : row.next_step}`), evidence(row, profile));
    if (list(row.work).length) {
      detail.append(el('h5', '', 'Linked work'));
      row.work.forEach((work) => detail.append(workItem(work)));
    } else {
      detail.append(el('p', 'no-linked-work', row.satisfied ? 'No task linked.' : 'No task linked. This gap is still part of the plan.'));
    }
    return add(item, summary, detail);
  }

  function stages(profile) {
    const block = el('div', 'readiness-view');
    const streams = list(profile.workstreams);
    if (!streams.length) return el('p', 'empty-inline', 'Readiness stages have not been defined.');
    const stream = streams.find((s) => s.id === streamId) || streams[0];
    streamId = stream.id;
    const picker = el('select', 'workstream-picker');
    picker.id = 'workstream-picker';
    streams.forEach((s) => { const option = el('option', '', s.title); option.value = s.id; picker.append(option); });
    picker.value = stream.id;
    picker.addEventListener('change', () => { streamId = picker.value; stageId = null; renderBody(); get('workstream-picker')?.focus(); });
    const label = el('label', '', 'Product or pilot'); label.htmlFor = picker.id;
    add(block, add(el('div', 'workstream-heading'), add(el('div', ''), label, picker), el('p', 'muted', stream.purpose)));
    const stage = list(stream.stages).find((s) => s.id === stageId) || list(stream.stages).find((s) => s.state !== 'verified') || list(stream.stages)[0];
    if (!stage) return add(block, el('p', 'empty-inline', 'Readiness checks have not been defined for this workstream.'));
    stageId = stage.id;
    const choices = el('div', 'stage-map'); choices.setAttribute('role', 'group'); choices.setAttribute('aria-label', 'Readiness stages');
    list(stream.stages).forEach((s) => {
      const selected = s.id === stage.id;
      const choice = button('', `stage-choice${selected ? ' selected' : ''}`, () => { stageId = s.id; renderBody(); get(`stage-${s.id}`)?.focus(); });
      choice.id = `stage-${s.id}`; choice.setAttribute('aria-pressed', String(selected));
      const counts = s.counts || {};
      add(choice, el('span', 'stage-name', s.title), el('span', 'stage-count', `${counts.verified || 0} of ${counts.total || 0} checks verified`));
      const bar = el('span', 'readiness-segments'); bar.setAttribute('aria-hidden', 'true');
      list(s.requirements).forEach((id) => {
        const req = profile.requirements.find((r) => r.id === id);
        if (req) { const segment = el('span', `readiness-segment ${req.satisfied ? 'verified' : req.state === 'verified' ? 'unknown' : req.state}`); segment.title = `${req.title}: ${LABELS[req.state]}`; bar.append(segment); }
      });
      if (!s.requirements.length) bar.append(el('span', 'readiness-segment unknown'));
      add(choice, bar, el('small', '', s.state === 'verified' ? 'Stage checks supported' : !counts.total ? 'Checks not defined' : `${counts.remaining || 0} need work; ${counts.unknown || 0} unknown`));
      choices.append(choice);
    });
    add(block, choices, el('p', 'map-legend', 'Green: verified · Blue: defined or built · Amber: missing · Red: blocked · Pattern: unknown'));
    const detail = add(el('div', 'stage-detail'), heading(stage.title, stage.definition));
    detail.append(el('p', 'stage-basis', stage.basis === 'proposed' ? 'Proposed readiness checks. These do not change release approval.' : 'Agreed readiness checks. Release approval remains separate.'));
    const critical = list(stage.critical_gaps).length;
    if (critical) detail.append(el('p', 'stage-gap-note', `${critical} required condition${critical === 1 ? '' : 's'} still need${critical === 1 ? 's' : ''} work or evidence.`));
    stage.requirements.forEach((id) => { const req = profile.requirements.find((r) => r.id === id); if (req) detail.append(requirement(req, profile)); });
    const external = list(stage.dependency_gaps).filter((id) => !stage.requirements.includes(id));
    if (external.length) {
      detail.append(el('h4', 'dependency-heading', 'Earlier requirements to resolve'));
      external.forEach((id) => detail.append(requirement(profile.requirements.find((r) => r.id === id), profile)));
    }
    return add(block, detail);
  }

  function strength(profile) {
    const block = add(el('div', 'strength-view'), heading('Operating strength', 'The level records how a practice works. The target reflects the stage it needs to support.'));
    if (profile.target_scope) block.append(el('p', 'target-note', profile.target_scope));
    if (!list(profile.capabilities).length) block.append(el('p', 'empty-inline', 'Operating practices have not been assessed.'));
    list(profile.capabilities).forEach((row) => {
      const card = add(el('article', 'capability'), add(el('div', 'capability-heading'), el('h4', '', row.title), badge(row.level === null ? 'unknown' : 'defined', row.level === null ? 'Unknown' : LEVELS[row.level])));
      const scale = el('ol', 'maturity-scale'); scale.setAttribute('aria-label', `${row.title}: ${row.level === null ? 'Unknown' : LEVELS[row.level]}`);
      LEVELS.forEach((label, i) => {
        const point = el('li', `${row.level !== null && i <= row.level ? 'reached' : ''}${i === row.target ? ' target' : ''}`, label);
        if (i === row.level) point.setAttribute('aria-current', 'step');
        scale.append(point);
      });
      add(card, scale, el('p', 'target-note', row.target === null ? 'Stage target not set.' : `${row.target_basis === 'proposed' ? 'Proposed' : 'Agreed'} target: ${LEVELS[row.target]}`), el('p', '', row.note));
      if (row.level !== row.recorded_level) card.append(el('p', 'assessment-warning', `Earlier assessment: ${LEVELS[row.recorded_level] || 'Unknown'}. Review the evidence before using that level.`));
      const details = add(el('details', 'assessment-more'), el('summary', '', 'Evidence and next improvement'), evidence(row, profile), el('p', '', `Next: ${row.evidence_state === 'needs_review' && row.recorded_level !== null ? 'Review the assessment evidence.' : row.next_step}`));
      const gaps = profile.requirements.filter((r) => r.capability === row.id && !r.satisfied);
      if (gaps.length) details.append(el('p', 'muted', `Open requirements: ${gaps.map((r) => r.title).join('; ')}.`));
      add(card, details); block.append(card);
    });
    return block;
  }

  function outcomes(profile) {
    const block = add(el('div', 'outcomes-view'), heading('Progress toward the Ideal State', 'Outcome evidence stays separate from completed features and tasks.'));
    if (!list(profile.outcomes).length) block.append(el('p', 'empty-inline', 'Ideal State criteria have not been connected.'));
    list(profile.outcomes).forEach((row) => {
      const detail = el('details', 'outcome');
      add(detail, add(el('summary', 'requirement-summary'), add(el('div', ''), el('small', '', row.id), el('span', 'requirement-title', row.title)), badge(row.state, LABELS[row.state])));
      const body = add(el('div', 'requirement-detail'), el('p', '', row.note), evidence(row, profile), el('p', 'requirement-next', `Next: ${row.evidence_state === 'needs_review' && row.recorded_state !== 'unknown' ? 'Review the outcome evidence.' : row.next_step}`));
      const related = profile.requirements.filter((r) => list(r.outcomes).includes(row.id));
      if (related.length) add(body, el('h5', '', 'Requirements that support this outcome'), ...related.map((r) => requirement(r, profile)));
      add(detail, body); block.append(detail);
    });
    return block;
  }

  function ahead(profile) {
    const block = add(el('div', 'work-ahead-view'), heading('Work ahead', 'Requirements connect the intended outcome to the work. Unverified gaps stay visible even when no task exists.'));
    list(profile.capabilities).forEach((cap) => {
      const rows = profile.requirements.filter((r) => r.capability === cap.id && (!r.satisfied || list(r.work).length));
      if (!rows.length) return;
      block.append(el('h4', 'work-group-title', cap.title));
      rows.forEach((r) => block.append(requirement(r, profile)));
    });
    if (list(profile.mapping_issues).length) block.append(el('p', 'assessment-warning', `${profile.mapping_issues.length} work link${profile.mapping_issues.length === 1 ? '' : 's'} need${profile.mapping_issues.length === 1 ? 's' : ''} review because the source changed or could not be matched.`));
    block.append(heading('Work not yet mapped', 'These active items remain available while their place in the plan is assessed.'));
    if (!list(profile.unmapped_work).length) block.append(el('p', 'empty-inline', 'No unmapped active items in the connected sources.'));
    list(profile.unmapped_work).forEach((item) => block.append(workItem(item)));
    return block;
  }

  function renderBody() {
    const initiative = list(currentData.initiatives).find((i) => i.id === chosen);
    if (!initiative || !get('initiative-body')) return;
    const profile = initiative.profile;
    document.querySelectorAll('[data-initiative-view]').forEach((b) => { b.setAttribute('aria-pressed', String(b.dataset.initiativeView === view)); });
    get('initiative-body').replaceChildren(({ readiness: stages, strength, outcomes, work: ahead }[view] || stages)(profile));
  }

  function select(id, updateHash = true) {
    if (chosen !== id) { chosen = id; streamId = null; stageId = null; }
    if (updateHash) history.replaceState(null, '', `#initiative-${encodeURIComponent(id)}`);
    render(currentData, helpers);
  }

  function render(data, api) {
    currentData = data; helpers = api;
    ({ el, add, list, text, badge, link, fmt } = api);
    const items = list(data.initiatives);
    let fromHash = '';
    if (location.hash.startsWith('#initiative-')) {
      try { fromHash = decodeURIComponent(location.hash.slice(12)); } catch { /* Keep the current selection for an invalid link. */ }
    }
    const initiative = items.find((i) => i.id === fromHash) || items.find((i) => i.id === chosen) || items.find((i) => i.profile?.state === 'available') || items[0];
    const host = get('initiative-list'); host.replaceChildren();
    if (!initiative) { host.append(el('p', 'empty-inline', 'No initiatives are connected.')); return; }
    chosen = initiative.id;
    const picker = el('div', 'initiative-picker'); picker.setAttribute('role', 'group'); picker.setAttribute('aria-label', 'Choose an initiative');
    items.forEach((i) => {
      const choice = button('', 'initiative-choice', () => { select(i.id); get(`choose-${i.id}`)?.focus(); });
      choice.id = `choose-${i.id}`; choice.setAttribute('aria-pressed', String(i.id === chosen));
      add(choice, el('span', '', i.name), el('small', '', ['available', 'stale'].includes(i.profile?.state) ? 'Readiness map' : 'Not yet assessed')); picker.append(choice);
    });
    const profile = initiative.profile || { state: 'missing', message: 'A readiness assessment has not been prepared.', unmapped_work: [] };
    const panel = el('article', 'initiative-panel'); panel.id = 'initiative-panel';
    const headline = add(el('div', 'initiative-title'), add(el('div', ''), el('h3', '', initiative.name), el('p', '', initiative.purpose)));
    if (initiative.ideal_url) headline.append(link(initiative.ideal_url, 'Open Ideal State'));
    panel.append(headline);
    if (['available', 'stale'].includes(profile.state)) {
      add(panel, el('p', 'initiative-summary', profile.summary), el('p', 'initiative-scope', profile.scope),
        el('p', 'assessment-date', `Assessed ${fmt(profile.as_of, 'day')}. Review by ${fmt(profile.review_by, 'day')}.`));
      if (profile.state === 'stale') panel.append(el('p', 'assessment-warning', profile.message));
      else if (profile.changed_evidence) panel.append(el('p', 'assessment-warning', 'Some source documents changed or became unavailable. Affected claims are shown as unknown.'));
      else panel.append(el('p', 'assessment-note', profile.message));
      panel.append(add(el('div', 'initiative-next'), el('strong', '', 'Next useful step'), el('p', '', profile.state === 'stale' || profile.changed_evidence ? 'Refresh the assessment against its current sources.' : profile.next_step)));
      const tabs = el('div', 'initiative-views'); tabs.setAttribute('role', 'group'); tabs.setAttribute('aria-label', 'Initiative view');
      [['readiness', 'Ready to use'], ['strength', 'Operating strength'], ['outcomes', 'Ideal State'], ['work', 'Work ahead']].forEach(([id, label]) => {
        const control = button(label, '', () => { view = id; renderBody(); }); control.dataset.initiativeView = id; tabs.append(control);
      });
      const body = el('div', 'initiative-body'); body.id = 'initiative-body';
      add(panel, tabs, body);
    } else {
      add(panel, el('p', 'empty-inline', profile.message), el('p', '', text(initiative.evidence, 'Outcome evidence has not been connected.')));
      if (initiative.observed_at) panel.append(el('small', '', `Earlier baseline: ${fmt(initiative.observed_at, 'day')}`));
      list(profile.unmapped_work).forEach((item) => panel.append(workItem(item)));
    }
    panel.append(button('See all initiative work', 'quiet initiative-all-work', () => api.showWork(chosen)));
    add(host, picker, panel);
    renderBody();
  }

  window.addEventListener('hashchange', () => { if (currentData && location.hash.startsWith('#initiative-')) render(currentData, helpers); });
  return { render };
})();
