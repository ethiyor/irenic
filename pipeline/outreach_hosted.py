"""Authenticated single-host analyst application; no public data or live-send bypass."""
import json
import base64
import hashlib
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

from flask import Flask, abort, g, jsonify, redirect, request
from google.auth.transport.requests import Request as GoogleRequest
from google.oauth2 import id_token
from google_auth_oauthlib.flow import Flow

from outreach_console import action, read_status
from outreach_live import Gmail, ROOT, locked, sha
from outreach_service import Service

IDENTITY = ['openid', 'https://www.googleapis.com/auth/userinfo.email']
MAIL = ['https://www.googleapis.com/auth/gmail.readonly', 'https://www.googleapis.com/auth/gmail.send']


def environment():
    if len(os.environ['OUTREACH_ENCRYPTION_KEY']) < 32:
        raise ValueError('Use a randomly generated encryption secret of at least 32 characters')
    return {'origin': os.environ['OUTREACH_ORIGIN'].rstrip('/'),
            'run': os.environ['OUTREACH_RUN'], 'state': os.environ['OUTREACH_STATE'],
            'key': base64.urlsafe_b64encode(hashlib.sha256(os.environ['OUTREACH_ENCRYPTION_KEY'].encode()).digest()),
            'allowlist': json.loads(os.environ['OUTREACH_ANALYSTS']),
            'google_client': json.loads(os.environ['OUTREACH_GOOGLE_WEB_CLIENT']),
            'demo': os.environ.get('OUTREACH_MODE', 'demo') == 'demo',
            'live_enabled': os.environ.get('OUTREACH_LIVE_ENABLED') == 'true'}


def create_app(settings=None):
    cfg = settings or environment()
    origin = cfg['origin']
    parsed = urlsplit(origin)
    if (parsed.scheme != 'https' and not (cfg.get('testing') and parsed.hostname == '127.0.0.1')) or parsed.path or parsed.query or parsed.fragment or parsed.username:
        raise ValueError('An exact HTTPS origin is required')
    if not cfg['allowlist'] or any(role not in {'owner', 'reviewer'} for role in cfg['allowlist'].values()):
        raise ValueError('Explicit analyst allowlist and roles required')
    client = cfg['google_client'].get('web')
    if not client or client.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth' or client.get('token_uri') != 'https://oauth2.googleapis.com/token':
        raise ValueError('Google Web application client JSON required; Desktop client is not supported')
    service = Service(cfg['state'], cfg['run'], cfg['key'], cfg['demo'], cfg['live_enabled'])
    app = Flask(__name__)
    app.config.update(MAX_CONTENT_LENGTH=8192, TESTING=bool(cfg.get('testing')))
    app.extensions['outreach_service'] = service
    cookie = '__Host-outreach' if parsed.scheme == 'https' else 'outreach_test'

    def session_id():
        return sha(request.cookies.get(cookie, '').encode())

    def new_session(response, email=None):
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with service.db() as db:
            db.execute('DELETE FROM sessions WHERE id=? OR expires<?', (session_id(), time.time()))
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (sha(sid.encode()), email, csrf, time.time()+28800))
        response.set_cookie(cookie, sid, secure=parsed.scheme == 'https', httponly=True, samesite='Lax', max_age=28800, path='/')
        return response, sha(sid.encode())

    @app.before_request
    def guard():
        if request.host != parsed.netloc and request.path != '/healthz':
            abort(400)
        with service.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?', (session_id(), time.time())).fetchone()
        g.user = dict(row) if row else None
        if g.user and g.user['email'] not in cfg['allowlist']:
            g.user = None
        if request.path.startswith('/api/'):
            if not g.user:
                abort(401)
            if request.method == 'POST' and (request.headers.get('Origin') != origin or
                not secrets.compare_digest(request.headers.get('X-CSRF-Token', ''), g.user['csrf'])):
                abort(403)

    @app.after_request
    def security(response):
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer', 'Content-Security-Policy': "default-src 'none'; script-src 'nonce-console'; style-src 'nonce-console'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
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

    def owner():
        if not g.user or cfg['allowlist'][g.user['email']] != 'owner':
            abort(403)

    def start_oauth(purpose):
        if purpose == 'mail':
            owner()
            if cfg['demo']:
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
                   'scopes': scopes, 'email': g.user['email'] if purpose == 'mail' else None}
        with service.db() as db:
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
        with service.db() as db:
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
            if not claims.get('email_verified') or claims.get('nonce') != pending['nonce'] or email not in cfg['allowlist']:
                raise ValueError('Identity not permitted')
            if pending['purpose'] == 'mail':
                if not g.user or email != pending['email'] or cfg['allowlist'][email] != 'owner':
                    raise ValueError('Owner session required')
                if Gmail(credentials.token).profile() != read_status(service.run)['sender'].lower():
                    raise ValueError('Approved sender mismatch')
                if not credentials.has_scopes(MAIL):
                    raise ValueError('Mailbox scopes missing')
                with locked(service.folder):
                    service.save_credentials(credentials)
                    service.event(email, 'mailbox_connected')
            response, _ = new_session(redirect('/'), email)
            service.event(email, 'sign_in')
            return response
        except Exception:
            return 'Sign-in or mailbox connection failed. Retry sign-in and check the approved account and configuration.', 403

    @app.get('/healthz')
    def health():
        return jsonify(status='ok')

    @app.get('/')
    def index():
        if not g.user:
            return '<!doctype html><html lang="en"><meta name="viewport" content="width=device-width"><title>Analyst sign-in</title><body><h1>Road Salt · Analyst workspace</h1><p>Private access for approved analysts.</p><a href="/login">Sign in with Google</a></body></html>'
        return (ROOT/'pipeline/outreach_hosted.html').read_text(encoding='utf-8')

    @app.get('/api/session')
    def session():
        return jsonify(email=g.user['email'], role=cfg['allowlist'][g.user['email']], csrf=g.user['csrf'])

    @app.get('/api/status')
    def status():
        state = read_status(service.run, service.demo)
        state['service'] = service.status()
        return jsonify(state)

    @app.post('/api/action')
    def perform():
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            abort(400)
        command = data.get('command')
        if command != 'classify':
            owner()
        try:
            if command in {'schedule_on', 'schedule_off'}:
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
        with service.db() as db:
            db.execute('DELETE FROM sessions WHERE id=?', (session_id(),))
        response = jsonify(ok=True)
        response.delete_cookie(cookie, path='/')
        return response

    return app
