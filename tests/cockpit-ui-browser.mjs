import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { writeFileSync, mkdtempSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { createServer } from 'node:http';
import { resolve } from 'node:path';
import { readFile, rm } from 'node:fs/promises';

// Run from the repository root, or pass that directory as the first argument.
const root = resolve(process.argv[2] || '.');
const profile = mkdtempSync(resolve(tmpdir(), 'cockpit-ui-'));
const host = '127.0.0.1';
const appPort = 8765;
const debugPort = 9223;
const state = { fail: false, reviews: 0, completions: 0, completionFail: true, reads: 0, promptReads: 0, promptFail: false, promptStatus: 200 };
const work = [
  { id: 'w/1', prompt_url: '/api/work-prompt/backlog%3Aw%2F1', title: 'Review the release', repo: 'alpha', status: 'In review', why: 'Checks finished.', source: 'Shared backlog', source_url: '/work/w%2F1', created: '2026-09-12', category: 'review', category_label: 'Needs your review', next_action: 'Read the result.', initiative_id: 'alpha', initiative_name: 'Alpha', work_type: 'improve', evidence_at: '2026-09-13T07:00:00+00:00', owner_surface_status: 'needs_review', owner_brief: { state: 'current', title: 'Decide whether the source fix is ready', context: 'Alpha / Publishing / Source reliability', why: 'The fix protects new publishing work from missing source facts.', progress: 'The code and checks are complete. A review decision remains.', next_step: 'Read the saved result and decide whether to merge.', as_of: '2026-09-13T07:00:00+00:00' } },
  { id: 'w2', prompt_url: '/api/work-prompt/Romance%20Ops%3Aw2', complete_token: 'complete-today', title: 'Engage: 2 To do · 0 bench', repo: 'ops', status: 'To do', why: 'Daily TikTok commenting task.', source: 'Ops control', source_url: 'https://example.test/ops', action_url: 'https://example.test/drafts', action_label: 'Open prepared drafts', created: '2026-09-13T05:40:00+00:00', category: 'queued', category_label: 'Queued', next_action: 'Open TikTok drafts.', initiative_id: '', initiative_name: 'Romance', work_type: 'operate', cadence_label: 'Daily', due_label: 'Due today', evidence_at: '2026-09-13T05:40:00+00:00', owner_brief: { state: 'current', title: 'Comment on today’s TikTok posts', context: 'Romance / Social promotion / Daily engagement', why: 'Daily comments keep the promotion loop active around current books.', progress: 'Two prepared comments are ready.', next_step: 'Open the prepared drafts and post the two comments.', as_of: '2026-09-13T05:40:00+00:00' } },
  { id: 'w3', title: 'Queue copy edit', repo: 'alpha', status: 'Ready', why: 'Ready to begin.', source: 'Queue', created: '2026-09-11', category: 'queued', category_label: 'Queued', next_action: 'OLD ACTION MUST NEVER APPEAR', initiative_id: 'alpha', initiative_name: 'Alpha', work_type: 'improve', evidence_at: null, owner_brief: { state: 'stale', title: 'Refresh the launch copy plan', context: 'Alpha / Launch / Copy', why: 'The source facts changed after this explanation was written.', progress: 'The copy edit remains queued.', next_step: 'OLD ACTION MUST NEVER APPEAR', as_of: '2026-09-11' } },
  { id: 'w4', title: 'Prepare tomorrow comments', repo: 'ops', status: 'Working', why: 'Routine preparation.', source: 'Ops control', source_url: 'https://example.test/ops', created: '2026-09-13', category: 'running', category_label: 'In progress', initiative_id: '', initiative_name: 'Romance', work_type: 'operate', cadence_label: 'Daily', due_label: 'Due Sep 14', evidence_at: null, owner_brief: { state: 'current', title: 'Prepare tomorrow’s social comments', context: 'Romance / Social promotion / Daily engagement', why: 'Prepared comments make tomorrow’s promotion easier to run.', progress: 'Draft preparation is underway.', next_step: 'Let the preparation run finish.', as_of: '2026-09-13T08:00:00+00:00' } }
];
const result = { id: 'r1', title: 'Merged source fix', repo: 'ops', status: 'done', why: 'Recorded merge evidence.', source: 'Shared backlog', source_url: '/work/r1', completed_at: '2026-09-12', date_precision: 'day', outcome_label: 'Merged', next_action: 'Read the result.', initiative_name: 'Ops', evidence_at: '2026-09-12', owner_brief: { state: 'current', title: 'Publishing source protection merged', context: 'Alpha / Publishing / Source reliability', why: 'Concurrent queues can now keep each other’s recorded state intact.', progress: 'The source fix is recorded as merged.', next_step: 'Check the separate deployment record before claiming it is live.', as_of: '2026-09-12' } };
const archivedResult = { id: 'r2', title: 'Archived note without date', repo: 'ops', status: 'closed', why: 'The recorded archive note.', source: 'Backlog archive', source_url: '/work/r2', completed_at: null, date_precision: 'unknown', outcome_label: 'Recorded done', next_action: 'Read the result.', initiative_name: 'Ops', evidence_at: null, owner_brief: { state: 'missing', title: 'Explanation not prepared', context: 'Operations / Archive', why: 'A business explanation has not been prepared for this result.', progress: 'The archive records this item as done.', next_step: '', as_of: null } };
const sourceIssue = { name: 'Owner queue', status: 'unavailable', detail: 'The owner queue could not be read.', updated_at: null };
const snapshot = {
  generated_at: '2026-09-13T08:00:00+00:00', mode: 'read only', work, results: [result, archivedResult],
  brief: { baseline_at: null, comparison_status: 'first_review', window_start: '2026-09-06T08:00:00+00:00', counts: { review: 1, running: 1, queued: 2, waiting: 0, held: 0, unknown: 0 }, changes: [], operations: [work[1]], upcoming_operations: [work[3]], recent_results: [result], attention: [work[0]], source_issues: [sourceIssue], review_token: 'review-token', detail: 'First review: recent recorded results and current work.' },
  initiatives: [], decisions: [], sources: [], coverage_notes: [], unmapped_repositories: [], resources: { detail: 'Fixture', usage: [] }
};

function reply(response, code, body, type = 'application/json') {
  response.writeHead(code, { 'Content-Type': type });
  response.end(body);
}

const server = createServer(async (request, response) => {
  if (request.method === 'GET' && request.url === '/') {
    const html = (await readFile(resolve(root, 'cockpit/templates/index.html'), 'utf8')).replace('{{ csrf }}', 'fixture');
    return reply(response, 200, html, 'text/html');
  }
  if (request.method === 'GET' && request.url === '/api/snapshot') { state.reads++; return state.fail ? reply(response, 500, '{}') : reply(response, 200, JSON.stringify(snapshot)); }
  if (request.method === 'GET' && request.url.startsWith('/api/work-prompt/')) {
    state.promptReads++;
    if (state.promptHang) return;
    if (state.promptFail) return reply(response, 503, JSON.stringify({error:'Task context unavailable.'}));
    if (state.promptStatus === 401) return reply(response, 401, '{}');
    const title = request.url.includes('Romance') ? 'Daily comments' : 'Review the release';
    return reply(response, 200, JSON.stringify({title, prompt:'Discuss '+title+'\nCurrent source and full review condition.'}));
  }
  if (request.method === 'GET' && request.url === '/static/app.js') return reply(response, 200, await readFile(resolve(root, 'cockpit/static/app.js'), 'utf8'), 'text/javascript');
  if (request.method === 'GET' && request.url === '/static/app.css') return reply(response, 200, await readFile(resolve(root, 'cockpit/static/app.css'), 'utf8'), 'text/css');
  if (request.method === 'POST' && request.url === '/api/operations/complete') {
    let body = '';
    for await (const part of request) body += part;
    if (JSON.parse(body).token !== 'complete-today' || request.headers['x-csrf-token'] !== 'fixture') return reply(response, 400, '{}');
    state.completions++;
    await delay(150);
    if (state.completionFail) return reply(response, 503, JSON.stringify({error:'Source completion could not be confirmed.'}));
    snapshot.work = work.filter(item => item.id !== 'w2');
    snapshot.brief.operations = [];
    snapshot.results.push({...work[1], status:'Done', complete_token:null});
    return reply(response, 200, JSON.stringify({status:'Done', verified:true}));
  }
  if (request.method === 'POST' && request.url === '/api/brief/review') {
    if (state.expired) return reply(response, 401, '{}');
    let body = '';
    for await (const part of request) body += part;
    if (JSON.parse(body).token !== 'review-token' || request.headers['x-csrf-token'] !== 'fixture') return reply(response, 400, '{}');
    state.reviews += 1;
    return reply(response, 200, JSON.stringify({ reviewed_at: '2026-09-13T08:01:00+00:00' }));
  }
  reply(response, 404, '{}');
});

const delay = (milliseconds) => new Promise((resolveDelay) => setTimeout(resolveDelay, milliseconds));

async function retry(action, attempts = 30) {
  let error;
  for (let count = 0; count < attempts; count += 1) {
    try { return await action(); } catch (caught) { error = caught; await delay(100); }
  }
  throw error;
}

await new Promise((resolveListen) => server.listen(appPort, host, resolveListen));
const chrome = spawn('/usr/bin/google-chrome', ['--headless', '--no-sandbox', '--disable-gpu', `--remote-debugging-port=${debugPort}`, `--user-data-dir=${profile}`, `http://${host}:${appPort}/`], { stdio: 'ignore', detached: true });

try {
  const page = await retry(async () => {
    const response = await fetch(`http://${host}:${debugPort}/json/list`);
    if (!response.ok) throw new Error('Chrome debugging endpoint is not ready');
    const pages = await response.json();
    const match = pages.find((candidate) => candidate.url === `http://${host}:${appPort}/`);
    if (!match) throw new Error('Cockpit page is not ready');
    return match;
  });
  const socket = new WebSocket(page.webSocketDebuggerUrl);
  await new Promise((resolveSocket, rejectSocket) => { socket.onopen = resolveSocket; socket.onerror = rejectSocket; });
  let sequence = 0;
  const pending = new Map();
  socket.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id); }
  };
  const call = (method, params = {}) => new Promise((resolveCall) => {
    const id = ++sequence;
    pending.set(id, resolveCall);
    socket.send(JSON.stringify({ id, method, params }));
  });
  const evaluate = async (expression) => {
    const message = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (message.result.exceptionDetails) throw new Error(message.result.exceptionDetails.text);
    return message.result.result.value;
  };
  const screenshot = async (path) => {
    const message = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: false });
    writeFileSync(path, Buffer.from(message.result.data, 'base64'));
  };

  await call('Runtime.enable');
  await call('Page.enable');
  await retry(async () => assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 4));
  assert.equal(await evaluate('document.querySelector(".attention-disclosure").open'), false);
  assert.equal(await evaluate('document.getElementById("operations").textContent.includes("Comment on today’s TikTok posts")'), true);
  assert.equal(await evaluate('document.getElementById("operations").textContent.includes("Due today")'), true);
  assert.equal(await evaluate('document.getElementById("operations").textContent.includes("Open prepared drafts")'), true);
  assert.equal(await evaluate('document.querySelector(".upcoming-operations-disclosure").open'), false);
  assert.equal(await evaluate('document.getElementById("upcoming-operations-count").textContent'), '1 item');
  assert.equal(await evaluate('document.getElementById("upcoming-operations").textContent.includes("Prepare tomorrow’s social comments")'), true);
  assert.equal(await evaluate('document.getElementById("source-issues").textContent.includes("Owner queue")'), true);
  assert.equal(await evaluate('document.getElementById("review-explanation").textContent'), 'Saves your place. Does not approve work.');
  assert.deepEqual(await evaluate('[document.getElementById("all-results-count").textContent,document.querySelectorAll("#all-results .brief-row").length,document.getElementById("all-results").textContent.includes("Archived note without date")]'), ['2 total', 1, true]);
  assert.equal(await evaluate('document.getElementById("recent-results").textContent.includes("Publishing source protection merged")'), true);
  await evaluate('document.querySelector("#recent-results .context-card").open=true');
  assert.equal(await evaluate('document.getElementById("recent-results").textContent.includes("The source fix is recorded as merged.")'), true);
  assert.equal(await evaluate('document.querySelector("#work-list .technical-record").open'), false);
  assert.equal(await evaluate('document.querySelector("#work-list .technical-record dl").checkVisibility()'), false);
  assert.equal(await evaluate('document.getElementById("work-list").textContent.includes("Explanation needs updating")'), true);
  assert.equal(await evaluate('document.querySelector("#work-list [data-explanation-state=stale] .explanation-state").checkVisibility()'), true);
  assert.equal(await evaluate('document.body.textContent.includes("OLD ACTION MUST NEVER APPEAR")'), false);
  await call('Emulation.setDeviceMetricsOverride', { width: 1440, height: 1100, deviceScaleFactor: 1, mobile: false });
  await screenshot('/tmp/cockpit-desktop.png');
  await call('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await screenshot('/tmp/cockpit-mobile.png');

  // Real clipboard write/read in this isolated browser, then denied-permission fallback.
  await call('Browser.grantPermissions', {origin:`http://${host}:${appPort}`,permissions:['clipboardReadWrite','clipboardSanitizedWrite']});
  assert.equal(await evaluate('document.querySelector("#work-list .prompt-control button").tagName'), 'BUTTON');
  assert.equal(await evaluate('document.body.textContent.includes("Discuss this work")'), false);
  await evaluate('document.querySelector("#work-list .prompt-control button").click()');
  await retry(async () => assert.equal(await evaluate('document.querySelector("#work-list .prompt-control [role=status]").textContent'), 'Copied. Paste into your normal chat.'));
  assert.equal(await evaluate('navigator.clipboard.readText()'), 'Discuss Review the release\nCurrent source and full review condition.');
  assert.equal(state.promptReads, 1);
  await evaluate('window.realClipboard=navigator.clipboard;Object.defineProperty(navigator,"clipboard",{configurable:true,value:{write:()=>Promise.reject(new Error("denied")),writeText:()=>Promise.reject(new Error("denied"))}})');
  await evaluate('document.querySelector("#operations .prompt-control button").click()');
  await retry(async () => assert.equal(await evaluate('document.getElementById("copy-dialog").open'), true));
  assert.equal(await evaluate('document.getElementById("copy-text").value'), 'Discuss Daily comments\nCurrent source and full review condition.');
  assert.equal(await evaluate('document.documentElement.scrollWidth <= window.innerWidth'), true);
  await screenshot('/tmp/cockpit-copy-mobile.png');
  await evaluate('document.getElementById("copy-again").click()');
  await retry(async () => assert.equal(await evaluate('document.getElementById("copy-help").textContent'), 'Copy the selected text with your usual copy command.'));
  await evaluate('document.getElementById("close-copy").click()');
  assert.equal(await evaluate('document.getElementById("copy-text").value'), '');
  state.promptFail = true;
  await evaluate('document.querySelector("#work-list .prompt-control button").click()');
  await retry(async () => assert.equal(await evaluate('document.querySelector("#work-list .prompt-control [role=status]").textContent'), 'Task context unavailable.'));
  assert.equal(await evaluate('document.getElementById("copy-dialog").open'), false);
  assert.equal(state.completions, 0);
  state.promptFail = false;
  state.promptHang = true;
  await evaluate('document.querySelector("#work-list .prompt-control button").click()');
  await retry(async () => assert.equal(await evaluate('document.querySelector("#work-list .prompt-control [role=status]").textContent'), 'The task took too long to load. Try copying again.'), 400);
  assert.equal(await evaluate('[...document.querySelectorAll("[data-prompt-url]")].every(b=>!b.disabled)'), true);
  state.promptHang = false;
  await evaluate('Object.defineProperty(navigator,"clipboard",{configurable:true,value:window.realClipboard})');

  assert.equal(state.reviews, 0);
  await evaluate('(()=>{const e=document.getElementById("category-filter");e.value="queued";e.dispatchEvent(new Event("change"));const t=document.getElementById("work-type-filter");t.value="improve";t.dispatchEvent(new Event("change"))})()');
  assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 1);
  await evaluate('(()=>{const i=document.getElementById("initiative-filter");i.value="alpha";i.dispatchEvent(new Event("change"));const q=document.getElementById("filter");q.value="Review the release";q.dispatchEvent(new Event("input"))})()');
  assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 0);
  await evaluate('(()=>{const e=document.getElementById("category-filter");e.value="review";e.dispatchEvent(new Event("change"))})()');
  assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 1);
  await evaluate('document.getElementById("clear-filters").click()');
  assert.deepEqual(await evaluate('[document.querySelectorAll("#work-list .work-row").length,document.getElementById("category-filter").value,document.getElementById("work-type-filter").value,document.getElementById("initiative-filter").value,document.getElementById("filter").value]'), [4, 'all', 'all', 'all', '']);
  assert.equal(state.reviews, 0);
  await evaluate('document.getElementById("review-brief").click()');
  await retry(async () => assert.equal(state.reviews, 1));
  snapshot.brief.comparison_status = 'unavailable';
  snapshot.brief.attention = [];
  snapshot.brief.review_token = null;
  await evaluate('document.getElementById("refresh").click()');
  await retry(async () => assert.equal(await evaluate('document.getElementById("changes").textContent.includes("Changes cannot be confirmed")'), true));
  assert.equal(await evaluate('document.getElementById("attention").textContent.includes("owner work may be missing")'), true);
  state.fail = true;
  await evaluate('document.getElementById("refresh").click()');
  await retry(async () => assert.deepEqual(await evaluate('[document.getElementById("content").hidden,document.getElementById("error").hidden,document.querySelectorAll("#work-list .work-row").length]'), [false, false, 4]));
  state.fail = false;
  snapshot.brief.review_token = 'review-token';
  await evaluate('document.getElementById("refresh").click()');
  await retry(async () => assert.equal(await evaluate('document.getElementById("review-brief").hidden'), false));
  // A routine has one explicit completion action, mirrored in Today and All work.
  assert.equal(await evaluate('document.querySelector("#operations [data-complete-token]").checkVisibility()'), true);
  assert.equal(await evaluate('document.querySelector("#operations .completion-control [role=alert]").checkVisibility()'), false);
  await evaluate('document.querySelector("#operations [data-complete-token]").click();document.querySelector("#work-list [data-complete-token]").click()');
  assert.equal(await evaluate('[...document.querySelectorAll("[data-complete-token]")].every(b => b.disabled)'), true);
  await retry(async () => assert.equal(await evaluate('document.querySelector("#operations .completion-control [role=alert]").textContent'), 'Source completion could not be confirmed.'));
  assert.equal(await evaluate('document.querySelector("#operations .completion-control [role=alert]").checkVisibility()'), true);
  assert.equal(state.completions, 1);
  assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 4);
  state.completionFail = false;
  await evaluate('document.querySelector("#operations [data-complete-token]").click()');
  await retry(async () => assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 3));
  assert.equal(state.completions, 2);
  assert.equal(await evaluate('document.querySelector("#operations [data-complete-token]")'), null);
  assert.equal(await evaluate('document.getElementById("completion-notice").textContent'), 'Marked complete in Notion.');
  assert.equal(await evaluate('document.getElementById("upcoming-operations").textContent.includes("Prepare tomorrow’s social comments")'), true);
  const priorReads = state.reads;
  await evaluate('window.dispatchEvent(new Event("focus"))');
  await delay(100);
  assert.equal(state.reads, priorReads);
  await evaluate('const actualNow=Date.now;Date.now=()=>actualNow()+61000;window.dispatchEvent(new Event("focus"))');
  await retry(async () => assert.equal(state.reads, priorReads + 1));
  assert.equal(await evaluate('document.getElementById("completion-notice").hidden'), true);
  state.promptStatus = 401;
  await evaluate('document.querySelector("#work-list .prompt-control button").click()');
  await retry(async () => assert.equal(await evaluate('location.pathname'), '/login'));
  state.promptStatus = 200;
  await call('Page.navigate', {url:`http://${host}:${appPort}/`});
  await retry(async () => assert.equal(await evaluate('document.querySelectorAll("#work-list .work-row").length'), 3));
  state.expired = true;
  await evaluate('document.getElementById("review-brief").click()');
  await retry(async () => assert.equal(await evaluate('location.pathname'), '/login'));
  assert.equal(state.reviews, 1);
  socket.close();
} finally {
  // Stop this fixture's process group, including profile-writing Chrome children.
  try { process.kill(-chrome.pid, 'SIGTERM'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
  if (chrome.exitCode === null) await new Promise((done) => chrome.once('exit', done));
  await rm(profile, {recursive:true, force:true, maxRetries:10, retryDelay:100});
  await new Promise((resolveClose) => server.close(resolveClose));
}
console.log('browser checks passed: contextual work, routine operations, stale safety, combined filters, clear-all, explicit review POST, uncertainty, refresh-error retention, clipboard copy, denied-copy fallback, failed-context handling, verified routine completion, failed-write retention, repeat-click guard, return refresh, expired-session login, 390px viewport');
console.log('screenshots: /tmp/cockpit-desktop.png /tmp/cockpit-mobile.png');
