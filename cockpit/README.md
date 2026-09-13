# Portfolio cockpit

A private website over existing operating records. Today shows changes since Rex's last saved review, work needing owner attention, dated recorded results, and connected-source failures. It reads the shared backlog and archive, the Attain product queue, Romance Ops, two local freshness checks, and the existing model-use ledger. All active work remains accessible through combined category, initiative, and text filters.

This remains a connected view, not a complete portfolio monitor. Five initiative summaries and four reference cases are dated audit context, separated from current records. A merge is shown as a merge; a separate deployment hold stays explicit. Undated results remain available but do not count as recent completions. Closed or dropped records do not become successful outcomes. Cash receipts, allocations, and customer benefits are still unconnected. No inference runs on refresh.

## How the morning brief works

The first visit shows current work and dated results from the preceding seven days; it does not call every existing task new. **Mark brief reviewed** explicitly saves Rex's place across devices. Refreshing, filtering, logging in, and reading evidence do not acknowledge the brief. Saving a review does not approve, release, hold, or close work. Pending work remains visible after review.

A small local JSON checkpoint records the source facts shown, not a second task database. A signed snapshot fingerprint and previous-checkpoint identity prevent an old tab or changed source from acknowledging unseen facts. Writes use a separate lock and atomic replacement. Repeating the same accepted request returns the saved result. A failure before replacement preserves the previous checkpoint. If only the final directory sync fails, the server verifies the replacement and logs the remaining durability uncertainty instead of claiming that the review was not saved. An unavailable source preserves its prior comparison facts, so recovery is not mistaken for newly created work. A disappearing item is not reported complete without a terminal record. Sources first connected after a saved review establish their own first comparison; their history is not called new. A newly discovered result without a completion date is labelled newly observed. Edits to known results are changes, not new completions. Saves larger than the supported 2 MB checkpoint are rejected before replacement.

Shared `open` work is **Queued**, not running. `held` remains **Held**, including linked owner-state conflicts. `in_review` requests inspection, without claiming ready-to-merge evidence. The Attain controller's `Working` status is evidence of an in-progress run. Attain drafts and requested Romance rework are waiting; Romance `To do` and `Missed` are owner tasks. Unknown states remain visible. These labels do not grant execution authority or replace the source's controls.

Read-only local evidence pages look up exact backlog IDs and show the existing result, hold reason, task, and recorded review. Source links still lead to the existing Notion owner controls. Hold remains the only operational mutation implemented by the cockpit and stays separately gated by `COCKPIT_ENABLE_ACTIONS`.

## Run a private preview

Install from the reviewed checkout into a dedicated environment. Python 3.11 or later is required.

```sh
python3 -m venv /tmp/portfolio-preview-venv
/tmp/portfolio-preview-venv/bin/pip install '.[cockpit]'
```

Set `COCKPIT_SECRET_KEY` to a random secret of at least 32 characters and `COCKPIT_PASSWORD_HASH` to a Werkzeug password hash. Put neither value in Git, command arguments, screenshots, or this document. Generate them interactively and store the resulting environment file outside the repository with mode 0600. A deployment should load that file through the service manager, not print it.

```sh
/tmp/portfolio-preview-venv/bin/python - <<'PY'
from getpass import getpass
from pathlib import Path
from secrets import token_hex
from werkzeug.security import generate_password_hash
p = Path.home() / '.config/portfolio-cockpit.env'
p.parent.mkdir(parents=True, exist_ok=True)
with p.open('x') as f:
    p.chmod(0o600)
    f.write('COCKPIT_SECRET_KEY=' + token_hex(32) + '\n')
    f.write('COCKPIT_PASSWORD_HASH=' + generate_password_hash(getpass('New cockpit password: ')) + '\n')
PY
```

An existing file is deliberately preserved. Load the file as environment variables using the service manager or a dotenv loader that treats values literally. Do not source an unquoted password hash through a shell: its dollar signs are data.

`portfolio-cockpit --local-http --port 8790` binds only to `127.0.0.1`. This development preview can be reached through the existing authenticated SSH connection using a local port forward. Do not expose its HTTP port to the internet. Actions and remote reads are off by default.

## Proposed permanent placement and budget

Use the current VPS because the authoritative backlog, locks, scoped tokens and installed runners already live there. Keep one Python service; avoid a second task database, queue, or synchronization engine. Put the existing authenticated private access route and TLS in front of loopback. A Cloudflare-hosted static UI would require another backend boundary and offers no demonstrated benefit for this first version.

Proposed initial service limit: one Gunicorn process, four threads, 384 MB memory, no scheduled inference and no new paid hosting commitment. Each owner refresh reads existing records; it does not launch a worker. Allow a bounded two-hour activation and rollback check. These are proposed deployment resources, not an allocation already granted. Public DNS, a new tunnel, or a new hosting bill is not assumed.

For the approved deployment, the service command from a versioned environment is:

```sh
gunicorn --bind 127.0.0.1:8790 --workers 1 --threads 4 --timeout 180 'cockpit.app:create_app()'
```

Run it as the existing owner account that owns the shared backlog locks. Configure the following explicitly:

| Variable | Meaning |
|---|---|
| `COCKPIT_SECRET_KEY`, `COCKPIT_PASSWORD_HASH` | Private signing key and owner password hash. Startup rejects missing configuration. |
| `COCKPIT_HOSTS` | Exact accepted hostnames; default `localhost,127.0.0.1`. |
| `COCKPIT_PUBLIC_ORIGIN` | Exact HTTPS origin used by the owner. Required when the TLS proxy changes the incoming scheme. |
| `COCKPIT_PROJECTS` | Existing projects root; default `~/projects`. |
| `COCKPIT_DOCS_ROOT` | Reviewed repository's `docs` directory. Required for a non-editable wheel install; decision documents are not copied into the wheel. |
| `COCKPIT_REMOTE_READS=1` | Enable read-only Attain and Romance Notion feeds, using each existing scoped token. |
| `COCKPIT_ENABLE_ACTIONS=1` | Enable the tested exact-item Hold action after the live queue action check is approved. |
| `COCKPIT_BRIEF_STATE_PATH` | Optional display-checkpoint file; defaults to `<projects>/.cockpit/review.json`. Keep this on the VPS local filesystem, durable and private. A preview uses its own file. |

Use one process so its login rate limit is shared. Behind a private proxy, requests may share an address and hence a limit. Sessions last two hours; rotating the signing key invalidates all of them. Log out removes the browser cookie; this small service has no session-revocation database. Keep access private and TLS enabled. Secure, HttpOnly, SameSite cookies, CSRF checks, accepted hosts and exact Origin checks protect authenticated actions. `Referrer-Policy: same-origin` preserves the Origin on login forms while withholding it from other sites. See [Flask security](https://flask.palletsprojects.com/en/stable/web-security/) and [MDN referrer policy](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Referrer-Policy).

## Action and failure contract

A Hold request includes a signed item revision and explicit owner reason. The server reacquires the runner lock and backlog lock, rereads active and archived records, and rejects stale or duplicate identities. It saves the decision and new state together using the existing atomic YAML writer. Retrying the same accepted action returns its prior result. Other items do not change. No hold request runs a shell command, pushes Git, merges a branch, releases a product, or sends a message.

The queue write is local durable state. This first action does not commit/push the backlog, and therefore does not claim off-device replication. Existing backup/commit processes can capture it later. The live activation check must record the resulting item and verify the expected backup route; do not silently enable a new push pathway. A failed response is not proof that the write failed: refresh before retrying.

Provider acceptance, current heartbeat, human attention, task completion and business benefit remain separate. This version covers queue/report freshness; a full expected-output registry and live cash reconciliation are not implemented. Remote feeds are bounded to 1,000 rows each; excess or partial pagination reports that source unavailable rather than silently dropping work. Large sources should get a specific pagination design only when this boundary is actually reached.

## Verification

```sh
python -m pytest tests/test_cockpit.py tests/test_cockpit_brief.py tests/test_workqueue_direction.py -q
# On the VPS with Node 22 and /usr/bin/google-chrome:
node tests/cockpit-ui-browser.mjs
```

The dev extra includes Flask; a skipped cockpit module is not a passing cockpit check. Tests cover missing sources, unknown statuses, missing/future completion dates, stale tabs, duplicate identities, explicit and idempotent review, failed checkpoint writes, and unchanged work records. Run the browser checks against both complete and missing-source fixtures, and inspect the private preview at desktop and mobile widths. Preview review state and cookies must be separate from production.

## Existing VPS adapter

This cockpit uses Gunicorn under `portfolio-cockpit.service`, with Tailscale Serve proxying private HTTPS to `127.0.0.1:8790`. It is not hosted on Workers, Netlify, or Pages. `.site-flow/config.json` records this verified custom adapter. Prepare a separate authenticated preview on loopback and a separate private Serve port; do not point the production proxy at a working tree. The September 13 preview uses loopback 8791 and HTTPS 8443, a separate signing key/cookie/checkpoint, and the existing owner password hash. It disables all work mutations.

For an approved release, install the committed tree into a new versioned environment under `~/.local/share/portfolio-cockpit/releases`, update the existing `current` symlink, restart the existing service, and verify authenticated snapshot, work evidence, static assets, and login. Preserve the previous release for rollback. Do not enable Hold as a side effect. After release, stop the preview unit and remove only its Serve port; never reset the whole Serve configuration.

## Owner explanations and two kinds of work

Every card leads with its business title, its place in the initiative, and the concrete reason it matters. Expanded cards show recorded progress and the recommended next step. Original developer titles, notes, identifiers, and full saved reviews remain available under disclosures. The exact-item page reads the full saved Council review where available, so conditions cut off in old queue notes remain accessible.

`Run the business` covers existing recurring activity: the TikTok commenting pilot, scheduled social posting, and the weekly numbers review. `Improve the business` covers changes, repairs, and launch work from the product/backlog queues and explicit launch/gate providers. A repair can protect cash and still belong in improvement work; the label does not determine its priority. Sources with no supported classification say `Not classified`. Classification uses the source/provider identity, never words such as "ready" in a note.

The cockpit reads Romance Ops' actual Due date and direct Source link. Today's and overdue operations appear ahead of improvement reviews; future routines remain in Upcoming operations and All work. Due dates use the source's timezone, with Europe/Lisbon as the owner default. Saving a brief does not complete a routine. The existing Romance scanner and Ops poller create the next dated task; this cockpit adds no recurring executor, paid scan, or notification channel.

The September 13 TikTok task was already present as `Engage: 2 To do · 0 bench`. The source title hid its meaning, and the cockpit had discarded its due date and draft-table link. The new daily explanation works for each dated Engage record, showing the prepared-comment link and the owner's posting step. It does not assume all original targets remain unposted or equate a closed day with proven marketing results. If the source returns no due operations, the view does not claim every expected routine ran: full recurring-source coverage is still incomplete.

`<projects>/.cockpit/work-context.json` holds private prepared owner explanations for the inspected work and results. It is presentation data, not a second work queue. Keep it outside Git and package artifacts with mode 0600; the source repository is public. `COCKPIT_CONTEXT_PATH` can point to another private location. Each entry names the source record and its content revision, including the complete local task and saved full review when present. The reader suppresses an old recommendation whenever that source changes. Missing or malformed explanations never hide work. No model runs on refresh. The editable source remains the backlog or its existing scoped owner queue.

For a new or changed explanation, read the full task, result, review conditions, and initiative context. Prepare `title`, `context`, `why`, `progress`, and `next_step`; use the record's evidence date as `as_of`. Preserve uncertainty about current deployment, measured benefit, and unresolved review points. Bind `source_revision` to that record's current `context_revision` only after comparing the proposed explanation with those exact facts. A matching revision establishes fidelity to the saved record, not independent proof that its historical claims remain true. Future undrafted work is explicitly labelled; source producers do not yet all supply this owner explanation contract.

Additional checks: `python -m pytest tests/test_cockpit_context.py -q`. These cover stale explanation suppression, full-review evidence, rejected acknowledgement of an unseen explanation change, checkpoint upgrade continuity, per-day recurring work, due-date grouping, and direct draft links.
