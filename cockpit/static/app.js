'use strict';

const $ = (id) => document.getElementById(id);
const csrf = document.querySelector('meta[name="csrf-token"]').content;
const CATEGORY_ORDER = ['review', 'running', 'queued', 'waiting', 'held', 'unknown'];
const CATEGORY_LABELS = { review: 'Review', running: 'Running', queued: 'Queued', waiting: 'Waiting', held: 'Held', unknown: 'Unknown' };
const WORK_TYPE_ORDER = ['operate', 'improve', 'unclear'];
const WORK_TYPE_LABELS = { operate: 'Run the business', improve: 'Improve the business', unclear: 'Not classified' };
let snapshot;
let selected;

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
};
const add = (parent, ...children) => { children.forEach((child) => parent.append(child)); return parent; };
const list = (value) => Array.isArray(value) ? value : [];
const text = (value, fallback = '') => value === null || value === undefined || value === '' ? fallback : String(value);
const number = (value) => new Intl.NumberFormat(undefined, { notation: 'compact', maximumFractionDigits: 1 }).format(Number(value) || 0);

function validDate(value) {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

function fmt(value, precision = 'instant') {
  const date = validDate(value);
  if (!date) return 'Not observed';
  if (precision === 'day') return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  return date.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function statusKey(value) {
  return text(value, 'unknown').toLowerCase().replace(/[^a-z0-9_-]/g, '-');
}

function categoryOf(work) {
  const category = statusKey(work.category);
  return CATEGORY_ORDER.includes(category) ? category : 'unknown';
}

function workTypeOf(work) {
  const workType = statusKey(work.work_type);
  return WORK_TYPE_ORDER.includes(workType) ? workType : 'unclear';
}

function badge(value, label) {
  return el('span', `badge ${statusKey(value)}`, text(label, text(value, 'Unknown').replaceAll('_', ' ')));
}

function link(url, label) {
  const value = text(url);
  const safeInternal = /^\/decisions\/[a-zA-Z0-9_.-]+$/.test(value) || /^\/work\/[a-zA-Z0-9._~%-]+$/.test(value);
  if (!value || !(value.startsWith('https://') || value.startsWith('http://') || safeInternal)) return el('span', 'muted', label);
  const anchor = el('a', 'text-link', label);
  anchor.href = value;
  if (value.startsWith('http')) { anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; }
  return anchor;
}

function empty(message) {
  return el('p', 'empty-inline', message);
}

function ownerBrief(item) {
  const brief = item && item.owner_brief && typeof item.owner_brief === 'object' ? item.owner_brief : {};
  const state = ['current', 'stale', 'missing'].includes(brief.state) ? brief.state : 'missing';
  return {
    state,
    title: text(brief.title, state === 'stale' ? 'Explanation needs updating' : 'Explanation not prepared'),
    context: text(brief.context, 'Business context not recorded'),
    why: text(brief.why, state === 'stale' ? 'The recorded facts changed after this explanation was prepared.' : 'A plain-language explanation has not been prepared for this record.'),
    progress: text(brief.progress, 'Recorded progress is not explained yet.'),
    nextStep: text(brief.next_step),
    asOf: brief.as_of
  };
}

function technicalRecord(item) {
  const details = el('details', 'technical-record');
  const body = el('dl', 'technical-record-body');
  const fields = [
    ['Original title', item.title],
    ['Original reason', item.why],
    ['Source', item.source],
    ['Created', item.created ? fmt(item.created) : ''],
    ['Record ID', item.id],
    ['Repository', item.repo],
    ['Source status', item.status],
    ['Owner queue', item.owner_surface_status]
  ];
  fields.filter(([, value]) => text(value)).forEach(([label, value]) => {
    add(body, el('dt', '', label), el('dd', '', text(value)));
  });
  return add(details, el('summary', '', 'Technical record'), body);
}

function explanationCard(item, kind, statusBadge) {
  const brief = ownerBrief(item);
  const card = el('details', `context-card ${kind === 'work' ? 'work-row' : 'brief-row-disclosure'}`);
  card.dataset.explanationState = brief.state;
  const summary = el('summary', 'context-summary');
  const lead = el('span', 'context-lead');
  const titleLine = add(el('span', 'context-title-line'), el(kind === 'work' ? 'span' : 'h4', 'context-title', brief.title));
  if (statusBadge) titleLine.append(statusBadge);
  const contextLine = add(el('span', 'owner-context-line'), el('span', 'owner-context', brief.context));
  if (brief.state !== 'current') {
    contextLine.append(el('span', `explanation-state ${brief.state}`, brief.state === 'stale' ? 'Explanation needs updating' : 'Explanation not prepared'));
  }
  if (kind === 'work') {
    contextLine.append(el('span', `work-type ${workTypeOf(item)}`, WORK_TYPE_LABELS[workTypeOf(item)]));
    if (item.cadence_label) contextLine.append(el('span', 'cadence-label', text(item.cadence_label)));
    if (item.due_label) contextLine.append(el('span', 'operation-due', text(item.due_label)));
  }
  add(lead, titleLine, contextLine, el('span', 'owner-why', brief.why));
  add(summary, el('span', 'context-toggle', ''), lead);

  const detail = el('div', 'context-detail');
  if (brief.state !== 'current') {
    detail.append(el('p', `explanation-warning ${brief.state}`, brief.state === 'stale' ? 'Explanation needs updating' : 'Explanation not prepared'));
  }
  add(detail,
    el('h5', '', 'Recorded progress'),
    el('p', 'recorded-progress', brief.progress));
  if (brief.state === 'current') {
    add(detail,
      el('h5', '', 'Recommended next step'),
      el('p', 'recommended-next-step', text(brief.nextStep, 'No next step is recommended.')));
  }
  if (brief.asOf) detail.append(el('small', 'explanation-date', `Source record as of ${fmt(brief.asOf)}`));
  detail.append(technicalRecord(item));
  return { card: add(card, summary, detail), detail, brief };
}

const completing = new Set();
let copyingPrompt = false;
function copyPromptControl(item) {
  const wrap = el('div', 'prompt-control');
  const button = el('button', 'quiet', 'Copy work prompt');
  button.type = 'button'; button.dataset.promptUrl = item.prompt_url;
  const status = el('p', 'muted'); status.setAttribute('role', 'status'); status.hidden = true;
  button.addEventListener('click', async () => {
    if (copyingPrompt) return;
    copyingPrompt = true; status.hidden = true;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 30000);
    const buttons = [...document.querySelectorAll('[data-prompt-url]')];
    buttons.forEach(b => { b.disabled = true; }); button.textContent = 'Preparing prompt…';
    try {
      if (!/^\/api\/work-prompt\/[a-zA-Z0-9._~%:-]+$/.test(item.prompt_url)) throw new Error('The prompt link is unavailable. Refresh the cockpit.');
      const payload = fetch(item.prompt_url, {cache:'no-store', signal:controller.signal}).then(async response => {
        if (response.status === 401) { location.assign('/login'); throw new Error('Log in before copying.'); }
        const data = await response.json();
        if (!response.ok || typeof data.prompt !== 'string' || !data.prompt.trim()) throw new Error(data.error || 'The prompt could not be read. Try again.');
        return data;
      });
      // Start the clipboard operation during the click, including on Safari.
      const blob = payload.then(data => new Blob([data.prompt], {type:'text/plain'}));
      blob.catch(() => {}); // A clipboard refusal must not leave an unhandled fetch rejection.
      try {
        if (window.ClipboardItem && navigator.clipboard?.write) await navigator.clipboard.write([new ClipboardItem({'text/plain':blob})]);
        else await navigator.clipboard.writeText((await payload).prompt);
        status.textContent = 'Copied. Paste into your normal chat.'; status.hidden = false;
      } catch (_) {
        const data = await payload; // Source failures go to the error state, never an old prompt.
        $('copy-title').textContent = data.title || 'Work prompt'; $('copy-text').value = data.prompt;
        $('copy-help').textContent = 'Automatic copy was blocked. Click Copy prompt, or copy the selected text.';
        $('copy-dialog').showModal(); $('copy-text').focus(); $('copy-text').select();
      }
    } catch (error) { status.textContent = error.name === 'AbortError' ? 'The task took too long to load. Try copying again.' : error.message; status.hidden = false; }
    finally { clearTimeout(timeout); copyingPrompt = false; buttons.forEach(b => { b.disabled = false; }); button.textContent = 'Copy work prompt'; }
  });
  return add(wrap, button, status);
}

function completionControl(item) {
  const wrap = el('div', 'completion-control');
  const button = el('button', 'quiet', 'Mark complete');
  button.type = 'button'; button.dataset.completeToken = item.complete_token;
  const error = el('p', 'error compact-error'); error.setAttribute('role', 'alert');
  error.hidden = true;
  wrap.append(button, error);
  button.addEventListener('click', async () => {
    const token = item.complete_token;
    if (completing.has(token)) return;
    completing.add(token); error.textContent = ''; error.hidden = true;
    $('completion-notice').hidden = true;
    const copies = [...document.querySelectorAll('button[data-complete-token]')].filter(b => b.dataset.completeToken === token);
    copies.forEach(b => { b.disabled = true; b.textContent = 'Saving completion…'; });
    let complete = false;
    try {
      const response = await fetch('/api/operations/complete', {method: 'POST',
        headers: {'Content-Type':'application/json', 'X-CSRF-Token':csrf}, body:JSON.stringify({token})});
      if (response.status === 401) { location.assign('/login'); return; }
      let result = {}; try { result = await response.json(); } catch (_) {}
      if (!response.ok || !result.verified || result.status !== 'Done') throw new Error(result.error || 'Completion could not be confirmed. Refresh before retrying.');
      complete = true; copies.forEach(b => { b.textContent = 'Completed'; });
      $('completion-notice').textContent = 'Marked complete in Notion.'; $('completion-notice').hidden = false;
      await refresh({ preserveCompletionNotice: true });
    } catch (failure) { error.textContent = failure.message; error.hidden = false; }
    finally {
      completing.delete(token);
      if (!complete) copies.forEach(b => { b.disabled = false; b.textContent = 'Mark complete'; });
    }
  });
  return wrap;
}

function populateSelect(select, options, currentValue) {
  select.replaceChildren(...options.map(({ value, label }) => {
    const option = el('option', '', label);
    option.value = value;
    return option;
  }));
  if (options.some((option) => option.value === currentValue)) select.value = currentValue;
}

function briefRow(item, kind) {
  const row = el('article', 'brief-row');
  const title = text(item.title, text(item.name, 'Untitled item'));
  const top = add(el('div', 'brief-row-heading'), el('h4', '', title));
  if (kind === 'change') top.append(badge(item.kind, text(item.kind, 'changed').replaceAll('_', ' ')));
  if (kind === 'work') top.append(badge(categoryOf(item), text(item.category_label, CATEGORY_LABELS[categoryOf(item)])));
  if (kind === 'result') top.append(badge(item.outcome_label, text(item.outcome_label, 'Recorded result')));
  if (kind === 'source') top.append(badge(item.status));
  const context = text(item.initiative_name, text(item.repo, text(item.name)));
  const observed = item.evidence_at || item.completed_at || item.updated_at;
  const precision = item.date_precision || 'instant';
  const meta = observed ? `Evidence ${fmt(observed, precision)}` : 'Evidence date unavailable';
  const footer = add(el('div', 'brief-row-footer'), el('small', '', meta));
  if (item.action_url) footer.append(link(item.action_url, text(item.action_label, 'Open the work')));
  if (item.source_url) footer.append(link(item.source_url, kind === 'result' ? 'Open result' : 'Open source'));
  if (kind === 'work' || kind === 'result' || (kind === 'change' && item.owner_brief)) {
    const status = kind === 'change'
      ? badge(item.kind, text(item.kind, 'changed').replaceAll('_', ' '))
      : kind === 'work'
        ? badge(categoryOf(item), text(item.category_label, CATEGORY_LABELS[categoryOf(item)]))
        : badge(item.outcome_label, text(item.outcome_label, 'Recorded result'));
    const explanation = explanationCard(item, kind, status);
    if (kind === 'change' && item.summary) explanation.detail.prepend(el('p', 'change-summary', text(item.summary)));
    explanation.detail.append(footer);
    if (item.prompt_url) explanation.detail.append(copyPromptControl(item));
    row.append(explanation.card);
    if (kind === 'work' && item.complete_token) row.append(completionControl(item));
  } else {
    row.append(top);
    if (context) row.append(el('p', 'brief-context', context));
    const description = text(item.summary, text(item.detail, text(item.why)));
    if (description) row.append(el('p', 'brief-description', description));
    if (item.next_action && item.next_action !== description) row.append(add(el('p', 'brief-next-action'), el('strong', '', 'Next: '), document.createTextNode(item.next_action)));
    row.append(footer);
  }
  return row;
}

function renderBrief() {
  const work = list(snapshot.work);
  const supplied = snapshot.brief && typeof snapshot.brief === 'object' ? snapshot.brief : null;
  const snapshotAt = validDate(snapshot.generated_at) || new Date();
  const windowStart = new Date(snapshotAt.getTime() - (7 * 24 * 60 * 60 * 1000));
  const fallbackResults = list(snapshot.results).filter((item) => {
    const completedAt = validDate(item.completed_at);
    return completedAt && item.date_precision !== 'unknown' && completedAt >= windowStart && completedAt <= snapshotAt;
  });
  const counts = supplied && supplied.counts ? supplied.counts : CATEGORY_ORDER.reduce((all, category) => {
    all[category] = work.filter((item) => categoryOf(item) === category).length;
    return all;
  }, {});
  const brief = supplied || {
    baseline_at: null,
    comparison_status: 'unavailable',
    changes: [],
    recent_results: fallbackResults,
    attention: work.filter((item) => categoryOf(item) === 'review'),
    source_issues: list(snapshot.sources).filter((source) => ['unavailable', 'overdue', 'failed'].includes(statusKey(source.status))),
    detail: 'A saved comparison is not available yet.',
    review_token: null
  };

  $('brief-detail').textContent = text(brief.detail, 'Current source facts and recorded outcomes.');
  const baselineLabels = {
    first_review: 'First review. No changes are claimed.',
    available: brief.baseline_at ? `Compared with ${fmt(brief.baseline_at)}` : 'Compared with the saved review.',
    unavailable: 'Comparison unavailable.'
  };
  $('brief-baseline').textContent = baselineLabels[brief.comparison_status] || baselineLabels.unavailable;
  $('brief-counts').replaceChildren(...CATEGORY_ORDER.map((category) => {
    const chip = el('button', `count-chip ${category}`, undefined);
    chip.type = 'button';
    chip.dataset.category = category;
    chip.setAttribute('aria-label', `Show ${CATEGORY_LABELS[category]} work`);
    add(chip, el('strong', '', String(Number(counts[category]) || 0)), el('span', '', CATEGORY_LABELS[category]));
    return chip;
  }));

  const changes = list(brief.changes);
  const attention = list(brief.attention);
  const results = list(brief.recent_results);
  const operations = list(brief.operations);
  const upcomingOperations = list(brief.upcoming_operations);
  const issues = list(brief.source_issues);
  const noChanges = brief.comparison_status === 'first_review'
    ? 'No change list appears until you mark the first brief reviewed.'
    : brief.comparison_status === 'unavailable'
      ? 'Changes cannot be confirmed because the previous review or source facts are unavailable.'
      : 'No recorded changes since the last review.';
  $('changes').replaceChildren(...(changes.length ? changes.map((item) => briefRow(item, 'change')) : [empty(noChanges)]));
  $('operations').replaceChildren(...(operations.length ? operations.map((item) => briefRow(item, 'work')) : [empty('No routine work is due or overdue in connected sources.') ]));
  $('operations-count').textContent = `${operations.length} item${operations.length === 1 ? '' : 's'}`;
  $('upcoming-operations-count').textContent = `${upcomingOperations.length} item${upcomingOperations.length === 1 ? '' : 's'}`;
  $('upcoming-operations').replaceChildren(...(upcomingOperations.length ? upcomingOperations.map((item) => briefRow(item, 'work')) : [empty('No future routine work is recorded.') ]));
  const noAttention = issues.length ? 'No connected item is awaiting review. Source issues mean owner work may be missing.' : 'Nothing is awaiting review in connected sources.';
  $('attention').replaceChildren(...(attention.length ? attention.map((item) => briefRow(item, 'work')) : [empty(noAttention)]));
  $('attention-count').textContent = `${attention.length} item${attention.length === 1 ? '' : 's'}`;
  $('attention-title').textContent = attention.some((item) => workTypeOf(item) === 'unclear') ? 'Improvement or unclassified work needing you' : 'Improvement work needing you';
  $('recent-results').replaceChildren(...(results.length ? results.map((item) => briefRow(item, 'result')) : [empty('No dated results were recorded in the last seven days.') ]));
  $('source-issues').replaceChildren(...(issues.length ? issues.map((item) => briefRow(item, 'source')) : [empty('No source issues are reported.') ]));
  const recentKeys = new Set(results.map((item) => `${text(item.source)}:${text(item.id)}`));
  const archivedResults = list(snapshot.results).filter((item) => !recentKeys.has(`${text(item.source)}:${text(item.id)}`));
  $('all-results-count').textContent = `${list(snapshot.results).length} total`;
  $('results-archive-note').textContent = results.length ? `${results.length} recent result${results.length === 1 ? ' appears' : 's appear'} above. Earlier and undated results follow.` : 'All recorded results appear below.';
  $('all-results').replaceChildren(...(archivedResults.length ? archivedResults.map((item) => briefRow(item, 'result')) : [empty(results.length ? 'No additional recorded results.' : 'No recorded results are connected.') ]));

  const reviewButton = $('review-brief');
  reviewButton.hidden = !brief.review_token;
  $('review-explanation').hidden = !brief.review_token;
  reviewButton.dataset.token = text(brief.review_token);
}

function renderInitiatives() {
  const initiatives = list(snapshot.initiatives);
  $('initiative-list').replaceChildren(...initiatives.map((initiative) => {
    const row = el('article', 'initiative');
    const heading = add(el('div', 'initiative-heading'), el('h3', '', text(initiative.name, 'Unnamed initiative')), badge(initiative.assessment || 'unassessed'));
    const intent = add(el('div', 'initiative-intent'), el('p', '', text(initiative.purpose, 'The current Ideal State has not been linked.')), initiative.ideal_url ? link(initiative.ideal_url, 'Ideal State') : el('small', '', 'No verified Ideal State link connected'));
    const evidence = add(el('div', 'initiative-evidence'), el('p', '', text(initiative.evidence, 'Outcome evidence is not connected.')), el('small', '', `Baseline evidence: ${fmt(initiative.observed_at)}`));
    const count = list(snapshot.work).filter((work) => work.initiative_id === initiative.id || (!work.initiative_id && list(initiative.repos).includes(work.repo))).length;
    add(row, heading, intent, evidence, el('p', 'initiative-count', `${count} live item${count === 1 ? '' : 's'}`));
    return row;
  }));
  if (!initiatives.length) $('initiative-list').append(empty('No initiative baselines are connected.'));
}

function renderReferenceCases() {
  const decisions = list(snapshot.decisions);
  $('reference-count').textContent = `${decisions.length} case${decisions.length === 1 ? '' : 's'}`;
  $('decisions').replaceChildren(...decisions.map((decision) => {
    const row = el('article', 'decision');
    add(row, el('p', 'decision-initiative', text(decision.initiative, 'Reference')), el('h3', '', text(decision.title, 'Untitled reference case')));
    if (decision.recommendation) row.append(el('p', 'decision-recommendation', decision.recommendation));
    if (decision.why) row.append(el('p', 'decision-why', decision.why));
    add(row, el('p', 'muted', `${text(decision.horizon, 'Dated case')} · Evidence ${fmt(decision.observed_at)}`), link(decision.source_url, 'Read the reference case'), badge('reference', 'Reference'));
    return row;
  }));
  if (!decisions.length) $('decisions').append(empty('No dated reference cases are connected.'));
}

function buildWorkFilters() {
  const work = list(snapshot.work);
  const categoryValue = $('category-filter').value;
  const initiativeValue = $('initiative-filter').value;
  const workTypeValue = $('work-type-filter').value;
  const categoryCounts = work.reduce((counts, item) => { const key = categoryOf(item); counts[key] = (counts[key] || 0) + 1; return counts; }, {});
  populateSelect($('category-filter'), [
    { value: 'all', label: `All statuses (${work.length})` },
    ...CATEGORY_ORDER.map((category) => ({ value: category, label: `${CATEGORY_LABELS[category]} (${categoryCounts[category] || 0})` }))
  ], categoryValue || 'all');

  const initiatives = new Map();
  work.forEach((item) => {
    const key = text(item.initiative_id, item.initiative_name ? `name:${item.initiative_name}` : 'unmapped');
    const label = text(item.initiative_name, 'Unmapped');
    const record = initiatives.get(key) || { value: key, label, count: 0 };
    record.count += 1;
    initiatives.set(key, record);
  });
  const options = [...initiatives.values()].sort((a, b) => a.label.localeCompare(b.label)).map((item) => ({ value: item.value, label: `${item.label} (${item.count})` }));
  populateSelect($('initiative-filter'), [{ value: 'all', label: `All initiatives (${work.length})` }, ...options], initiativeValue || 'all');

  const workTypeCounts = work.reduce((counts, item) => {
    const key = workTypeOf(item);
    counts[key] = (counts[key] || 0) + 1;
    return counts;
  }, {});
  populateSelect($('work-type-filter'), [
    { value: 'all', label: `All types (${work.length})` },
    ...WORK_TYPE_ORDER.map((workType) => ({ value: workType, label: `${WORK_TYPE_LABELS[workType]} (${workTypeCounts[workType] || 0})` }))
  ], workTypeValue || 'all');
}

function workInitiativeKey(item) {
  return text(item.initiative_id, item.initiative_name ? `name:${item.initiative_name}` : 'unmapped');
}

function renderWork() {
  const all = list(snapshot.work);
  const query = $('filter').value.trim().toLowerCase();
  const category = $('category-filter').value || 'all';
  const initiative = $('initiative-filter').value || 'all';
  const workType = $('work-type-filter').value || 'all';
  const rows = all.filter((item) => {
    const brief = ownerBrief(item);
    const haystack = [
      brief.title, brief.context, brief.why, brief.progress, brief.state === 'current' ? brief.nextStep : '',
      item.title, item.repo, item.why, item.id, item.source, item.status,
      item.owner_surface_status, item.category_label, item.initiative_name, item.next_action
    ].map((value) => text(value)).join(' ').toLowerCase();
    return (!query || haystack.includes(query))
      && (category === 'all' || categoryOf(item) === category)
      && (initiative === 'all' || workInitiativeKey(item) === initiative)
      && (workType === 'all' || workTypeOf(item) === workType);
  });
  $('work-total').textContent = `${all.length} active item${all.length === 1 ? '' : 's'}`;
  $('work-count').textContent = rows.length === all.length ? `Showing all ${all.length} items` : `Showing ${rows.length} of ${all.length} items`;
  $('work-list').replaceChildren(...rows.map((work) => {
    const explanation = explanationCard(work, 'work', badge(categoryOf(work), text(work.category_label, CATEGORY_LABELS[categoryOf(work)])));
    const detail = explanation.detail;
    if (work.complete_token) detail.append(completionControl(work));
    if (work.prompt_url) detail.append(copyPromptControl(work));
    if (work.action_url) detail.append(link(work.action_url, text(work.action_label, 'Open the work')));
    if (work.source_url) detail.append(link(work.source_url, 'Open the source'));
    if (work.hold_token) {
      const button = el('button', 'quiet', 'Hold this work');
      button.type = 'button';
      button.addEventListener('click', () => {
        selected = work; $('hold-title').textContent = work.title; $('hold-reason').value = ''; $('hold-error').textContent = ''; $('hold-dialog').showModal(); $('hold-reason').focus();
      });
      detail.append(button);
    }
    return explanation.card;
  }));
  $('empty').hidden = rows.length > 0;
}

function renderSources() {
  const sources = list(snapshot.sources);
  $('sources').replaceChildren(...sources.map((source) => add(el('article', 'source-row'),
    add(el('div', ''), el('h3', '', text(source.name, 'Unnamed source')), el('p', '', text(source.detail))), badge(source.status), el('small', '', `Source updated: ${fmt(source.updated_at)}`))));
  if (!sources.length) $('sources').append(empty('No source status is available.'));
  const notes = list(snapshot.coverage_notes).slice();
  if (list(snapshot.unmapped_repositories).length) notes.push(`Work without a linked initiative: ${snapshot.unmapped_repositories.join(', ')}.`);
  $('coverage').replaceChildren(el('h3', '', 'Coverage still to close'), ...(notes.length ? notes.map((note) => el('p', '', note)) : [el('p', '', 'No coverage gaps are reported.') ]));
}

function renderResources() {
  const resources = snapshot.resources || {};
  const summary = add(el('div', 'resource-summary'),
    add(el('div', ''), el('h3', '', 'Cash and allocation'), el('p', '', 'Not yet reconciled'), el('small', '', 'Unknown is not zero.')),
    add(el('div', ''), el('h3', '', 'Owner time'), el('p', '', '€75/hour, nominal'), el('small', '', 'No saved hours or cash benefit claimed.')),
    add(el('div', ''), el('h3', '', 'Review window'), el('p', '', 'All necessary work'), el('small', '', 'Filters change the view, not the queue.')));
  const usage = el('div', 'usage');
  add(usage, el('h3', '', 'Recorded model use, last seven days'), el('p', 'muted', text(resources.detail, 'Usage evidence is not connected.')));
  list(resources.usage).forEach((item) => add(usage, add(el('div', 'usage-row'), el('span', '', text(item.project, 'Unknown project')), el('span', '', `${number(item.calls)} calls`), el('span', '', `${number(item.tokens_in)} input tokens`), el('span', '', `${number(item.tokens_out)} output tokens`))));
  if (!list(resources.usage).length) usage.append(el('p', '', 'No usage rows are available in this view.'));
  $('resource-view').replaceChildren(summary, usage);
}

function render() {
  const generatedAt = validDate(snapshot.generated_at);
  $('date').textContent = generatedAt ? generatedAt.toLocaleDateString(undefined, { weekday: 'long', day: 'numeric', month: 'long' }) : 'Today';
  $('mode').textContent = text(snapshot.mode, 'Mode unknown');
  $('freshness').textContent = generatedAt ? `Read ${fmt(snapshot.generated_at)}. Evidence dates appear with each item.` : 'Snapshot time unavailable. Evidence dates appear with each item.';
  renderBrief();
  buildWorkFilters();
  renderWork();
  renderInitiatives();
  renderReferenceCases();
  renderSources();
  renderResources();
}

let refreshGeneration = 0;
let lastRefresh = 0;
async function refresh({ preserveCompletionNotice = false } = {}) {
  if (!preserveCompletionNotice) $('completion-notice').hidden = true;
  const generation = ++refreshGeneration;
  $('refresh').disabled = true;
  try {
    const response = await fetch('/api/snapshot', { cache: 'no-store' });
    if (response.status === 401) { location.assign('/login'); return; }
    if (!response.ok) throw new Error('The portfolio could not refresh. The previous view remains visible. Try again.');
    const nextSnapshot = await response.json();
    if (generation !== refreshGeneration) return;
    snapshot = nextSnapshot;
    lastRefresh = Date.now();
    render();
    $('content').hidden = false;
    $('error').hidden = true;
  } catch (error) {
    if (generation !== refreshGeneration) return;
    $('error').textContent = error.message;
    $('error').hidden = false;
  } finally {
    if (generation === refreshGeneration) { $('loading').hidden = true; $('refresh').disabled = false; }
  }
}

async function markBriefReviewed() {
  const button = $('review-brief');
  const token = button.dataset.token;
  if (!token) return;
  button.disabled = true;
  $('brief-review-error').hidden = true;
  try {
    const response = await fetch('/api/brief/review', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ token }) });
    if (response.status === 401) { location.assign('/login'); return; }
    let result = {};
    try { result = await response.json(); } catch (_) { result = {}; }
    if (!response.ok) {
      const fallback = response.status === 409 ? 'The brief changed before it was marked reviewed. Refresh and review the current facts.' : 'The review save could not be confirmed. Refresh before trying again.';
      throw new Error(result.error || fallback);
    }
    await refresh();
  } catch (error) {
    $('brief-review-error').textContent = error.message;
    $('brief-review-error').hidden = false;
  } finally {
    button.disabled = false;
  }
}

function refreshOnReturn() {
  if (!document.hidden && snapshot && !completing.size && !$('refresh').disabled && Date.now() - lastRefresh > 60000) refresh();
}
window.addEventListener('focus', refreshOnReturn);
document.addEventListener('visibilitychange', refreshOnReturn);
$('close-copy').addEventListener('click', () => { $('copy-dialog').close(); $('copy-text').value = ''; });
$('copy-again').addEventListener('click', async () => {
  try { await navigator.clipboard.writeText($('copy-text').value); $('copy-help').textContent = 'Copied. Paste into your normal chat.'; }
  catch (_) { $('copy-help').textContent = 'Copy the selected text with your usual copy command.'; $('copy-text').focus(); $('copy-text').select(); }
});
$('copy-dialog').addEventListener('close', () => { $('copy-text').value = ''; });
$('refresh').addEventListener('click', refresh);
$('review-brief').addEventListener('click', markBriefReviewed);
['filter', 'category-filter', 'initiative-filter', 'work-type-filter'].forEach((id) => $(id).addEventListener(id === 'filter' ? 'input' : 'change', () => { if (snapshot) renderWork(); }));
$('brief-counts').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-category]');
  if (!button || !snapshot) return;
  $('category-filter').value = button.dataset.category;
  renderWork();
  $('work').scrollIntoView();
});
$('clear-filters').addEventListener('click', () => { $('filter').value = ''; $('category-filter').value = 'all'; $('initiative-filter').value = 'all'; $('work-type-filter').value = 'all'; if (snapshot) renderWork(); });
$('cancel-hold').addEventListener('click', () => $('hold-dialog').close());
$('hold-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const button = event.submitter;
  button.disabled = true;
  try {
    const response = await fetch(`/api/work/${encodeURIComponent(selected.id)}/hold`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf }, body: JSON.stringify({ token: selected.hold_token, reason: $('hold-reason').value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || 'The result is uncertain. Refresh before trying again.');
    $('hold-dialog').close();
    await refresh();
  } catch (error) { $('hold-error').textContent = error.message; }
  finally { button.disabled = false; }
});

refresh();
