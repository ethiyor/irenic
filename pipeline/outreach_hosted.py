"""Authenticated single-host analyst application; no public data or live-send bypass."""
import json
import base64
import hashlib
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, request, send_file
from io import BytesIO
from outreach_evidence import evidence
from outreach_workflow import workflow
from werkzeug.local import LocalProxy
from outreach_workspaces import Workspaces, add_request
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from outreach_console import action, read_status
from outreach_live import Gmail, ROOT, locked, sha
from outreach_service import Service

IDENTITY = ['openid', 'https://www.googleapis.com/auth/userinfo.email']
MAIL = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send']

# Campaign mutations are explicitly authorized; new/unknown commands fail closed.
COMMAND_ROLES = {
    'add_request': {'owner'}, 'prepare_drafts': {'owner'},
    'approve_send': {'owner', 'approver'}, 'reject_draft': {'owner', 'approver'},
    'edit_draft': {'owner'}, 'schedule_on': {'owner'}, 'schedule_off': {'owner'},
    'disconnect': {'owner'}, 'pause': {'owner'}, 'resume': {'owner'},
    'classify': {'owner'}, 'demo_reply': {'owner'},
    'research_import': {'owner'}, 'research_decide': {'owner'}, 'research_publication': {'owner'}, 'research_receipt': {'owner'},
    'monitor_enable': {'owner'}, 'monitor_pause': {'owner'}, 'monitor_check': {'owner'}, 'monitor_review': {'owner'},
}


def environment():
    required = ['OUTREACH_ENCRYPTION_KEY', 'OUTREACH_ORIGIN', 'OUTREACH_RUN',
                'OUTREACH_STATE', 'OUTREACH_ANALYSTS', 'OUTREACH_GOOGLE_WEB_CLIENT']
    missing = [name for name in required if not os.environ.get(name, '').strip()]
    if missing:
        raise ValueError('Missing required hosted configuration: ' + ', '.join(missing))
    mode = os.environ.get('OUTREACH_MODE', 'demo')
    if mode not in {'demo', 'live'}:
        raise ValueError('OUTREACH_MODE must be demo or live')
    if os.environ.get('OUTREACH_LIVE_ENABLED', 'false') not in {'true', 'false'}:
        raise ValueError('OUTREACH_LIVE_ENABLED must be true or false')
    if len(os.environ['OUTREACH_ENCRYPTION_KEY']) < 32:
        raise ValueError('Use a randomly generated encryption secret of at least 32 characters')
    return {'origin': os.environ['OUTREACH_ORIGIN'].rstrip('/'),
            'run': os.environ['OUTREACH_RUN'], 'state': os.environ['OUTREACH_STATE'],
            'key': base64.urlsafe_b64encode(hashlib.sha256(os.environ['OUTREACH_ENCRYPTION_KEY'].encode()).digest()),
            'allowlist': json.loads(os.environ['OUTREACH_ANALYSTS']),
            'google_client': json.loads(os.environ['OUTREACH_GOOGLE_WEB_CLIENT']),
            'demo': mode == 'demo',
            'open_signup': os.environ.get('OUTREACH_OPEN_SIGNUP') == 'true',
            'personal_workspaces': os.environ.get('OUTREACH_PERSONAL_WORKSPACES') == 'true',
            'live_enabled': os.environ.get('OUTREACH_LIVE_ENABLED') == 'true'}


def create_app(settings=None):
    cfg = settings or environment()
    origin = cfg['origin']
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' and not (cfg.get('testing') and parsed.hostname == '127.0.0.1')) or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('An exact HTTPS origin is required')
    if not cfg['allowlist'] or any(role not in {'owner', 'reviewer', 'approver'} for role in cfg['allowlist'].values()):
        raise ValueError('Explicit analyst allowlist and roles required')
    client = cfg['google_client'].get('web')
    if not client or client.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or client.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('Google Web application client JSON required; Desktop client is not supported')
    auth_service = Service(cfg['state'], cfg['run'], cfg['key'], cfg['demo'], cfg['live_enabled'])
    if cfg.get('open_signup') and not cfg.get('personal_workspaces'):
        raise ValueError('Open sign-up requires isolated personal workspaces')
    workspaces = Workspaces(cfg, auth_service)
    service = LocalProxy(lambda: g.workspace_service)
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=65536, TESTING=bool(cfg.get('testing')))
    app.extensions['outreach_service'] = auth_service
    app.extensions['outreach_workspaces'] = workspaces
    from source_monitor import Monitor
    monitor = Monitor(auth_service.folder, hold=auth_service.recovery_hold)
    app.extensions['source_monitor'] = monitor
    cookie = '__Host-outreach' if parsed.scheme == 'https' else 'outreach_test'

    def session_id():
        return sha(request.cookies.get(cookie, '').encode())

    def new_session(response, email=None):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with auth_service.db() as db:
            db.execute('DELETE FROM sessions WHERE id=? OR expires<?', (session_id(), time.time()))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (sha(sid.encode()), email, csrf, time.time()+28800))
        response.set_cookie(cookie, sid, secure=parsed.scheme == 'https', httponly=True, samesite='Lax', max_age=28800, path='/')
        return response, sha(sid.encode())

    @app.before_request
    def guard():
        g.csp_nonce = secrets.token_urlsafe(24)
        if request.path == '/healthz':
            return None
        if request.host != parsed.netloc and request.path != '/healthz':
            abort(400)
        with auth_service.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (session_id(), time.time())).fetchone()
        g.user = dict(row) if row else None
        if g.user and not workspaces.permitted(g.user['email']):
            g.user = None
        g.workspace_service, g.workspace = auth_service, None
        if g.user:
            try:
                g.workspace, g.workspace_service = workspaces.select(g.user['email'], request.headers.get('X-Workspace'))
            except PermissionError:
                abort(403)
        if request.path.startswith('/api/'):
            if not g.user:
                abort(401)
            if request.method == 'POST' and (request.headers.get('Origin') != origin or
                not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), g.user['csrf'])):
                abort(403)

    @app.after_request
    def security(response):
        nonce = getattr(g, 'csp_nonce', secrets.token_urlsafe(24))
        if response.mimetype == 'text/html' and not response.direct_passthrough:
            response.set_data(response.get_data().replace(b'nonce="console"', ('nonce="'+nonce+'"').encode()))
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if parsed.scheme == 'https':
            response.headers['Strict-Transport-Security'] = 'max-age=31536000'
        return response

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(403)
    @app.errorhandler(404)
    @app.errorhandler(413)
    def rejected(error):
        return jsonify(error='Sign in or check your permissions and request.'), error.code

    @app.errorhandler(sqlite3.Error)
    @app.errorhandler(OSError)
    def storage_unavailable(error):
        return jsonify(error='Private storage is unavailable. Contact the owner; do not retry a send until its outcome is reconciled.'), 503

    def owner():
        if not g.user or g.workspace['role'] != 'owner':
            abort(403)

    def start_oauth(purpose):
        if purpose == 'mail':
            owner()
            if cfg['demo'] or service.recovery_hold():
                abort(403)
        nonce = secrets.token_urlsafe(32)
        scopes = IDENTITY + (MAIL if purpose == 'mail' else [])
        flow = Flow.from_client_config(cfg['google_client'], scopes=scopes, redirect_uri=origin+'/oauth/callback', autogenerate_code_verifier=True)
        url, state = flow.authorization_url(access_type='offline' if purpose == 'mail' else 'online',
                                             prompt='consent' if purpose == 'mail' else 'select_account', nonce=nonce)
        response = redirect(url)
        sid = session_id()
        if purpose == 'login':
            response, sid = new_session(response)
        payload = {'purpose': purpose, 'nonce': nonce, 'verifier': flow.code_verifier,
                   'scopes': scopes, 'email': g.user['email'] if purpose == 'mail' else None,
                   'workspace': g.workspace['id'] if purpose == 'mail' else None}
        with auth_service.db() as db:
            db.execute('DELETE FROM oauth WHERE expires<?', (time.time(),))
            db.execute('INSERT INTO oauth VALUES(?,?,?,?)', (sha(state.encode()), sid, json.dumps(payload), time.time()+600))
        return jsonify(url=url) if purpose == 'mail' else response

    @app.get('/login')
    def login():
        return start_oauth('login')

    @app.post('/api/connect')
    def connect():
        return start_oauth('mail')

    @app.get('/oauth/callback')
    def callback():
        state = request.args.get('state', '')
        with auth_service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM oauth WHERE state=? AND session=? AND expires>?',
                             (sha(state.encode()), session_id(), time.time())).fetchone()
            if row:
                db.execute('DELETE FROM oauth WHERE state=?', (sha(state.encode()),))
        if not row or request.args.get('error') or not request.args.get('code'):
            abort(400)
        pending = json.loads(row['data'])
        try:
            flow = Flow.from_client_config(cfg['google_client'], scopes=pending['scopes'], state=state,
                                          code_verifier=pending['verifier'], redirect_uri=origin+'/oauth/callback')
            flow.fetch_token(code=request.args['code'], timeout=30)
            credentials = flow.credentials
            claims = id_token.verify_oauth2_token(credentials.id_token, GoogleRequest(), client['client_id'])
            email = claims.get('email', '').lower()
            if claims.get('email_verified') is not True or claims.get('nonce') != pending['nonce']:
                raise ValueError('Identity not permitted')
            if pending['purpose'] == 'mail':
                if not g.user or g.user['email'] != pending['email']:
                    raise ValueError('Original signed-in owner required')
                g.workspace, g.workspace_service = workspaces.select(email, pending.get('workspace', 'shared'))
                if not g.user or email != pending['email'] or g.workspace['role'] != 'owner':
                    raise ValueError('Owner session required')
                if Gmail(credentials.token).profile() != read_status(service.run)['sender'].lower():
                    raise ValueError('Approved sender mismatch')
                if not credentials.has_scopes(MAIL):
                    raise ValueError('Mailbox scopes missing')
                with locked(service.folder):
                    service.save_credentials(credentials)
                    service.event(email, 'mailbox_connected')
            else:
                workspaces.register_verified(email)
                g.workspace, g.workspace_service = workspaces.select(email)
            response, _ = new_session(redirect('/?workspace='+g.workspace['id']), email)
            service.event(email, 'sign_in')
            return response
        except Exception:
            return 'Sign-in or mailbox connection failed. Retry sign-in and check the approved account and configuration.', 403

    @app.get('/privacy')
    def privacy():
        return (ROOT/'pipeline/outreach_privacy.html').read_text(encoding='utf-8')

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def index():
        if not g.user:
            return (ROOT/'pipeline/outreach_signin.html').read_text(encoding='utf-8')
        return (ROOT/'pipeline/outreach_hosted.html').read_text(encoding='utf-8')

    @app.get('/api/session')
    def session():
        return jsonify(email=g.user['email'], role=g.workspace['role'], csrf=g.user['csrf'],
                       workspace=g.workspace, workspaces=workspaces.choices(g.user['email']))

    @app.get('/api/status')
    def status():
        state = read_status(service.run, service.demo)
        # Local-console historical placeholders are not hosted acceptance evidence.
        state.pop('scheduler', None)
        state.pop('live_reply_validation', None)
        state['service'] = service.status()
        state['drafts'] = service.drafts()
        state['workspace'] = g.workspace
        state['workflow'] = workflow(service, state)
        if g.workspace['role'] == 'owner':
            from outreach_operations import operations
            state['operations'] = operations(service)
        return jsonify(state)

    @app.get('/api/operations')
    def operational_status():
        owner()
        from outreach_operations import operations
        return jsonify(operations(service))

    @app.get('/api/evidence/<mid>')
    @app.get('/api/evidence/<mid>/<file_id>')
    def review_evidence(mid, file_id=None):
        if cfg.get('evidence_enabled', os.environ.get('OUTREACH_EVIDENCE_ENABLED', 'true') == 'true') is False:
            return jsonify(error='Evidence review is temporarily disabled.'), 404
        try:
            info, raw, files = evidence(service.run, mid)
        except LookupError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 413
        except (sqlite3.Error, OSError, RecursionError):
            return jsonify(error='Stored evidence is temporarily unavailable or malformed. Retry or contact the workspace owner.'), 503
        if file_id is None:
            return jsonify(info)
        if file_id == 'original':
            data, name = raw, 'original-message.eml'
        elif file_id in files:
            data, name = files[file_id]
        else:
            return jsonify(error='Attachment unavailable or quarantined. Refresh the evidence view.'), 404
        return send_file(BytesIO(data), mimetype='application/octet-stream', as_attachment=True,
                         download_name=name, conditional=False, etag=False)

    def shared_monitor():
        if g.workspace['id'] != 'shared':
            abort(403)

    @app.get('/api/source-monitor')
    def monitor_view():
        shared_monitor()
        return jsonify(monitor.view())

    @app.get('/api/source-monitor/<item>/document')
    def monitor_document(item):
        shared_monitor()
        try:
            data = monitor.document(item)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return send_file(BytesIO(data), mimetype='application/pdf', as_attachment=True,
                         download_name='unreviewed-source-'+item+'.pdf', conditional=False, etag=False)

    @app.get('/api/research')
    def research_view():
        from outreach_research import view
        return jsonify(view(service.run))

    @app.get('/api/research/<candidate>/export')
    def research_export(candidate):
        owner()
        from outreach_research import export_review
        try:
            payload = export_review(service.run, candidate)
        except ValueError as error:
            return jsonify(error=str(error)), 400
        return send_file(BytesIO(json.dumps(payload, indent=2).encode()), mimetype='application/json',
                         as_attachment=True, download_name='private-review-'+candidate+'.json',
                         conditional=False, etag=False)

    @app.post('/api/action')
    def perform():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        command = data.get('command')
        if not isinstance(command, str) or g.workspace['role'] not in COMMAND_ROLES.get(command, set()):
            abort(403)
        if command.startswith('monitor_'):
            shared_monitor()
        try:
            if command.startswith('monitor_'):
                return jsonify(monitor.control(data, g.user['email']))
            elif command.startswith('research_'):
                from outreach_research import mutate
                if service.recovery_hold():
                    raise ValueError('Recovery quarantine: research changes are disabled.')
                with locked(service.folder):
                    result = mutate(service.run, data, g.user['email'])
                    service.event(g.user['email'], command)
                return jsonify(result)
            elif command == 'add_request':
                add_request(service, data, g.user['email'])
            elif command in {'prepare_drafts', 'approve_send', 'edit_draft', 'reject_draft'}:
                service.draft_action(command, data, g.user['email'])
            elif command in {'schedule_on', 'schedule_off'}:
                service.set_schedule(command == 'schedule_on', g.user['email'])
            elif command == 'disconnect':
                service.disconnect(g.user['email'])
            else:
                if command not in {'pause', 'resume', 'classify', 'demo_reply'}:
                    raise ValueError('Action not available')
                note = data.get('note', '')
                if command != 'demo_reply' and (not isinstance(note, str) or not note.strip()):
                    raise ValueError('Review note required')
                data['note'] = g.user['email']+': '+note
                with locked(service.folder):
                    action(service.run, service.demo, data)
                    service.event(g.user['email'], command)
            return jsonify(ok=True)
        except FileExistsError:
            return jsonify(error='Worker is busy. Wait for this run to finish.'), 409
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except Exception:
            return jsonify(error='Operation stopped. Inspect worker state before retrying.'), 503

    @app.post('/api/logout')
    def logout():
        with auth_service.db() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (session_id(),))
        response = jsonify(ok=True)
        response.delete_cookie(cookie, path='/')
        return response

    from outreach_email_login import register_email_login
    register_email_login(app, auth_service, cfg, session_id, new_session)
    return app
