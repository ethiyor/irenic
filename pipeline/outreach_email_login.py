"""Allowlisted, browser-bound, single-use email sign-in; never grants mailbox access."""
import secrets
import time

from flask import abort, jsonify, request
from outreach_live import sha

GENERIC = ('If this address has email-link access, a link will arrive shortly. '
           'Open it in this browser within 15 minutes. If it does not arrive, try again in a minute.')


def register_email_login(app, service, cfg, session_id, new_session):
    with service.db() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS email_login(
          digest TEXT PRIMARY KEY, email TEXT NOT NULL, session TEXT NOT NULL,
          created REAL NOT NULL, expires REAL NOT NULL, state TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS email_login_created ON email_login(created);
        CREATE TABLE IF NOT EXISTS login_attempts(
          id INTEGER PRIMARY KEY, session TEXT NOT NULL, email_hash TEXT NOT NULL, at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS login_attempts_at ON login_attempts(at);
        ''')

    def browser_session():
        if request.headers.get('Origin') != cfg['origin']:
            abort(403)
        with service.db() as db:
            row = db.execute('SELECT * FROM sessions WHERE id=? AND expires>?',
                             (session_id(), time.time())).fetchone()
        if not row or not secrets.compare_digest(row['csrf'], request.headers.get('X-CSRF-Token', '')):
            abort(403)
        return row['id']

    @app.get('/auth/session')
    def email_login_session():
        with service.db() as db:
            row = db.execute('SELECT csrf FROM sessions WHERE id=? AND expires>?',
                             (session_id(), time.time())).fetchone()
        if row:
            return jsonify(csrf=row['csrf'])
        response, sid = new_session(jsonify(ok=True))
        with service.db() as db:
            row = db.execute('SELECT csrf FROM sessions WHERE id=?', (sid,)).fetchone()
        response.set_data(app.json.dumps({'csrf': row['csrf']}))
        return response

    @app.post('/auth/request-link')
    def request_email_link():
        sid = browser_session()
        data = request.get_json(silent=True)
        email = data.get('email', '') if isinstance(data, dict) else ''
        if not isinstance(email, str) or len(email) > 254:
            abort(400)
        email = email.strip().lower()
        now = time.time()
        token = None
        with service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM login_attempts WHERE at<?', (now-3600,))
            db.execute('DELETE FROM email_login WHERE created<?', (now-86400,))
            counts = db.execute('''SELECT count(*),
                coalesce(sum(session=? AND at>?),0),
                coalesce(sum(email_hash=?),0),coalesce(sum(email_hash=? AND at>?),0)
                FROM login_attempts''', (sid, now-600, sha(email.encode()), sha(email.encode()), now-60)).fetchone()
            if counts[0] >= 200 or counts[1] >= 5 or counts[2] >= 5 or counts[3] >= 1:
                return jsonify(message=GENERIC)
            db.execute('INSERT INTO login_attempts(session,email_hash,at) VALUES(?,?,?)',
                       (sid, sha(email.encode()), now))
            sent_count = db.execute('SELECT count(*) FROM email_login WHERE created>?', (now-3600,)).fetchone()[0]
            if cfg['allowlist'].get(email) in {'approver', 'reviewer'} and sent_count < 30:
                token = secrets.token_urlsafe(32)
                db.execute("UPDATE email_login SET state='revoked' WHERE email=? AND state IN ('sending','sent')", (email,))
                db.execute('INSERT INTO email_login VALUES(?,?,?,?,?,?)',
                           (sha(token.encode()), email, sid, now, now+900, 'sending'))
        if token:
            try:
                # Fragment keeps bearer credentials out of access logs and HTTP referrers.
                service.send_signin_link(email, cfg['origin']+'/#access='+token)
                with service.db() as db:
                    db.execute("UPDATE email_login SET state='sent' WHERE digest=? AND state='sending'", (sha(token.encode()),))
                service.event(email, 'email_signin_link_sent')
            except Exception:
                # No retry of a possibly accepted Gmail send; a fresh user request is required.
                with service.db() as db:
                    db.execute("UPDATE email_login SET state='revoked' WHERE digest=?", (sha(token.encode()),))
                service.event(email, 'email_signin_delivery_failed')
        return jsonify(message=GENERIC)

    @app.post('/auth/consume-link')
    def consume_email_link():
        sid = browser_session()
        data = request.get_json(silent=True)
        token = data.get('token', '') if isinstance(data, dict) else ''
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            abort(400)
        now = time.time()
        with service.db() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM email_login WHERE digest=? AND session=? AND state='sent' AND expires>?",
                             (sha(token.encode()), sid, now)).fetchone()
            if not row or cfg['allowlist'].get(row['email']) not in {'approver', 'reviewer'}:
                return jsonify(error='Link expired, already used, or opened in a different browser. Request a new link here.'), 400
            db.execute("UPDATE email_login SET state='used' WHERE digest=?", (row['digest'],))
        response, _ = new_session(jsonify(ok=True), row['email'])
        service.event(row['email'], 'email_link_sign_in')
        return response
