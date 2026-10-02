"""Private owner cockpit. No operational action runs on a read request."""
from __future__ import annotations

from datetime import timedelta
import hashlib
import os
from pathlib import Path
import secrets
import time

from flask import Flask, abort, jsonify, redirect, render_template, request, session
from itsdangerous import BadSignature, URLSafeTimedSerializer
from werkzeug.security import check_password_hash
from werkzeug.exceptions import SecurityError

from . import actions, sources, briefing, completion, handoff, readiness


def create_app(config=None):
    app = Flask(__name__)
    projects = Path(os.environ.get('COCKPIT_PROJECTS', str(Path.home() / 'projects')))
    app.config.update(
        SECRET_KEY=os.environ.get('COCKPIT_SECRET_KEY', ''), PASSWORD_HASH=os.environ.get('COCKPIT_PASSWORD_HASH', ''),
        SESSION_COOKIE_NAME='portfolio_session', SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE='Strict', PERMANENT_SESSION_LIFETIME=timedelta(hours=2),
        MAX_CONTENT_LENGTH=16_384, MAX_FORM_PARTS=8,
        TRUSTED_HOSTS=os.environ.get('COCKPIT_HOSTS', 'localhost,127.0.0.1').split(','),
        PROJECTS_ROOT=str(projects), BACKLOG_PATH=str(projects / 'backlog/backlog.yaml'),
        STATE_ROOT=str(projects / '.backlog-run'), CATALOG_PATH=str(Path(__file__).with_name('portfolio.json')),
        REMOTE_READS=os.environ.get('COCKPIT_REMOTE_READS') == '1',
        ENABLE_ACTIONS=os.environ.get('COCKPIT_ENABLE_ACTIONS') == '1',
        COMPLETION_ENABLED=os.environ.get('COCKPIT_COMPLETION_ENABLED') == '1',
        DOCS_ROOT=os.environ.get('COCKPIT_DOCS_ROOT', str(Path(__file__).resolve().parent.parent / 'docs')),
        PUBLIC_ORIGIN=os.environ.get('COCKPIT_PUBLIC_ORIGIN', ''),
    )
    app.config.update(config or {})
    app.config.setdefault('ARCHIVE_PATH', str(Path(app.config['BACKLOG_PATH']).with_name('archive.yaml')))
    app.config.setdefault('BRIEF_STATE_PATH', os.environ.get('COCKPIT_BRIEF_STATE_PATH', str(Path(app.config['PROJECTS_ROOT']) / '.cockpit' / 'review.json')))
    app.config.setdefault('CONTEXT_PATH', os.environ.get('COCKPIT_CONTEXT_PATH', str(Path(app.config['PROJECTS_ROOT']) / '.cockpit' / 'work-context.json')))
    app.config.setdefault('INITIATIVES_PATH', os.environ.get('COCKPIT_INITIATIVES_PATH', str(Path(app.config['PROJECTS_ROOT']) / '.cockpit' / 'initiative-profiles.json')))
    if len(app.config['SECRET_KEY']) < 32 or not app.config['PASSWORD_HASH']:
        raise ValueError('Set a strong cockpit signing key and owner password hash before starting')
    signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='portfolio-owner-action')
    review_signer = URLSafeTimedSerializer(app.config['SECRET_KEY'], salt='portfolio-brief-review')
    attempts = {}

    @app.before_request
    def protect():
        if isinstance(request.routing_exception, SecurityError):
            raise request.routing_exception
        if request.endpoint not in ('login', 'static') and not session.get('owner'):
            if request.path.startswith('/api/'):
                return jsonify(error='Log in to see your portfolio.'), 401
            return redirect('/login')
        if request.method == 'POST':
            expected_origin = app.config['PUBLIC_ORIGIN'] or request.host_url.rstrip('/')
            if request.headers.get('Origin') and request.headers['Origin'] != expected_origin:
                abort(403)
            supplied = request.headers.get('X-CSRF-Token') or request.form.get('csrf', '')
            if not supplied or not session.get('csrf') or not secrets.compare_digest(supplied, session['csrf']):
                abort(403)

    @app.after_request
    def headers(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'X-Frame-Options': 'DENY', 'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"})
        return response

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        error = ''
        if request.method == 'POST':
            key, now = request.remote_addr or 'owner', time.monotonic()
            tries, start = attempts.get(key, (0, now))
            if now - start >= 60: tries, start = 0, now
            if tries >= 5: return render_template('login.html', error='Wait one minute, then try again.', csrf=session['csrf']), 429
            attempts[key] = (tries + 1, start)
            if check_password_hash(app.config['PASSWORD_HASH'], request.form.get('password', '')):
                attempts.pop(key, None)
                session.clear()
                session.update(owner=True, csrf=secrets.token_urlsafe(32))
                session.permanent = True
                return redirect('/')
            error = 'That password did not match.'
        if 'csrf' not in session: session['csrf'] = secrets.token_urlsafe(32)
        return render_template('login.html', error=error, csrf=session['csrf'])

    @app.post('/logout')
    def logout():
        session.clear()
        return redirect('/login')

    @app.get('/')
    def index():
        return render_template('index.html', csrf=session['csrf'])

    @app.get('/initiatives/<initiative_id>/sources/<source_id>')
    def initiative_source(initiative_id, source_id):
        try:
            document = readiness.source_document(app.config, initiative_id, source_id)
        except readiness.SourceNotFound:
            abort(404)
        except readiness.SourceUnavailable as exc:
            app.logger.warning('initiative source unavailable: %s', str(exc))
            abort(503)
        return render_template('initiative-source.html', document=document)

    @app.get('/api/snapshot')
    def snapshot():
        data = sources.snapshot(app.config)
        checkpoint = data.pop('_checkpoint')
        if checkpoint['available']:
            data['brief']['review_token'] = review_signer.dumps({k: checkpoint[k] for k in ('snapshot', 'baseline')})
        for item in data['work']:
            claim = item.pop('_completion_claim',None)
            if claim and app.config['COMPLETION_ENABLED'] and app.config['REMOTE_READS']:
                item['complete_token'] = completion.signer(app.config).dumps(claim)
            if item.pop('can_hold', False) and app.config['ENABLE_ACTIONS']:
                item['hold_token'] = signer.dumps({'id': item['id'], 'revision': item['revision'], 'action': 'hold'})
            item.pop('revision', None)
        for item in data['results']:
            item.pop('_completion_claim',None)
        return jsonify(data)

    @app.post('/api/operations/complete')
    def complete_operation():
        if not app.config['COMPLETION_ENABLED'] or not app.config['REMOTE_READS']:
            return jsonify(error='Source completion is not enabled in this cockpit.'),403
        body=request.get_json(silent=True)
        token=body.get('token') if isinstance(body,dict) else None
        if not isinstance(token,str) or not token:
            return jsonify(error='Refresh the task before marking it complete.'),400
        try:
            claim=completion.signer(app.config).loads(token,max_age=3600)
            return jsonify(completion.complete(app.config,claim))
        except BadSignature:
            return jsonify(error='This completion control expired or changed. Refresh the task.'),409
        except completion.CompletionError as exc:
            app.logger.warning('completion rejected status=%s reason=%s',exc.status,str(exc))
            return jsonify(error=str(exc)),exc.status
        except BlockingIOError:
            return jsonify(error='The source queue is busy. Nothing changed; try again after it finishes.'),409
        except (OSError,ValueError,KeyError,TypeError,completion.requests.RequestException) as exc:
            app.logger.warning('completion failed type=%s',type(exc).__name__)
            return jsonify(error='The source completion could not be confirmed. Refresh before retrying.'),503

    @app.post('/api/brief/review')
    def review_brief():
        body = request.get_json(silent=True)
        token = body.get('token') if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            return jsonify(error='Refresh the brief before marking it reviewed.'), 400
        try:
            claim = review_signer.loads(token, max_age=3600)
            token_id = hashlib.sha256(token.encode()).hexdigest()
            with briefing.checkpoint_lock(app.config):
                prior, prior_status = briefing.load_state(app.config)
                if prior_status == 'available' and prior.get('token_id') == token_id:
                    return jsonify(reviewed_at=prior['reviewed_at'])
                data = sources.snapshot(app.config)
                checkpoint = data['_checkpoint']
                if (not checkpoint['available'] or claim != {k: checkpoint[k] for k in ('snapshot', 'baseline')}):
                    return jsonify(error='The brief changed. Refresh and read the new information before marking it reviewed.'), 409
                return jsonify(reviewed_at=briefing.save(app.config, checkpoint, token_id))
        except BadSignature:
            return jsonify(error='The brief expired or changed. Refresh before marking it reviewed.'), 409
        except BlockingIOError:
            return jsonify(error='Another review is being saved. Refresh and try again.'), 409
        except (OSError, ValueError):
            return jsonify(error='The review save could not be confirmed. Your work is unchanged; refresh before trying again.'), 503

    @app.get('/api/work-prompt/<path:item_key>')
    def work_prompt(item_key):
        try:
            return jsonify(handoff.build(app.config,item_key))
        except handoff.HandoffError as exc:
            return jsonify(error=str(exc)),exc.status
        except (OSError,ValueError,KeyError,TypeError):
            return jsonify(error='The task context could not be read. Refresh before copying.'),503

    @app.get('/work/<item_id>')
    def work_evidence(item_id):
        records = sources.work_evidence(app.config, item_id)
        if not records:
            abort(404)
        return render_template('work.html', records=records), 200 if len(records) == 1 else 409

    @app.post('/api/work/<item_id>/hold')
    def hold(item_id):
        if not app.config['ENABLE_ACTIONS']: abort(403)
        body = request.get_json(silent=True) or {}
        if not isinstance(body, dict):
            return jsonify(error='Provide the current decision and a short reason.'), 400
        token, reason = body.get('token'), body.get('reason')
        if not isinstance(token, str) or not isinstance(reason, str) or not reason.strip() or len(reason) > 4000:
            return jsonify(error='Provide the current decision and a short reason.'), 400
        try:
            claim = signer.loads(token, max_age=3600)
            if claim.get('id') != item_id or claim.get('action') != 'hold': raise BadSignature('Wrong action')
            decision_id = hashlib.sha256((token + '\0' + reason.strip()).encode()).hexdigest()
            result = actions.hold(app.config, item_id, claim['revision'], reason.strip(), decision_id)
            return jsonify(result)
        except BadSignature:
            return jsonify(error='This decision expired or changed. Refresh the view.'), 409
        except (actions.StaleDecision, KeyError, ValueError):
            return jsonify(error='The work changed. Refresh and review its current state.'), 409
        except (TimeoutError, SystemExit):
            return jsonify(error='The runner is using this queue. Nothing changed; try again after it finishes.'), 409
        except OSError:
            return jsonify(error='The queue write could not be confirmed. Refresh before trying again.'), 503

    @app.get('/decisions/<name>')
    def decision(name):
        # Only prepared decision documents are served, never a caller-supplied file path.
        if not name.endswith('.md') or '/' in name or '\\' in name: abort(404)
        root = Path(app.config['DOCS_ROOT']) / 'decisions'
        path = root / name
        if path.parent.resolve() != root.resolve() or not path.is_file() or path.is_symlink(): abort(404)
        return render_template('decision.html', title=name.replace('.md', '').replace('-', ' '), text=path.read_text())

    return app
