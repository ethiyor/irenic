"""Server-selected workspace membership and isolated campaign storage."""
import base64
import hashlib
import hmac
import json
import logging
import threading
import time
from pathlib import Path

from outreach_console import read_status
from outreach_live import Pilot, address, canonical, locked, sha, validate
from outreach_service import Service


class Workspaces:
    def __init__(self, cfg, original):
        self.cfg, self.original = cfg, original
        self.services = {'shared': original}
        self.personal = {}
        if not cfg.get('personal_workspaces'):
            return
        if cfg['demo']:
            raise ValueError('Personal workspaces require live mode; they never copy demo mail')
        self.lock = threading.RLock()
        self.original_sender = read_status(original.run)['sender'].lower()
        with original.db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_accounts(email TEXT PRIMARY KEY, created REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1)')
            registered = [r['email'] for r in db.execute('SELECT email FROM workspace_accounts WHERE active=1')]
        for email in dict.fromkeys([*cfg['allowlist'], *registered]):
            self.provision(email)

    def provision(self, email):
        if email == self.original_sender or email in self.personal:
            return
        address(email)
        wid = 'personal-' + sha(email.encode())[:24]
        root = Path(self.cfg['state'])/'workspaces'/wid
        run, state = root/'campaign', root/'service'
        key = base64.urlsafe_b64encode(hmac.new(self.cfg['key'], wid.encode(), hashlib.sha256).digest())
        run.mkdir(parents=True, exist_ok=True)
        pilot = Pilot(run)
        try:
            if not pilot.db.execute('SELECT 1 FROM campaign').fetchone():
                config = dict(workspace_schema=1, campaign_id=wid, sender=email,
                    forward_to=[email], pilot_contact=None, timezone='America/New_York',
                    holidays=[], followup_business_days=5, max_followups=2,
                    followup_text='Following up on the records request below.', requests=[])
                pilot.initialize(config, sha(canonical(config)), 'Empty personal workspace; no outreach authorized')
                pilot.control('pause', 'New personal workspace starts paused')
            _, config = pilot.config()
            if config['sender'] != email or config.get('workspace_schema') != 1:
                raise ValueError('Workspace identity mismatch')
        finally:
            pilot.close()
        self.services[wid] = Service(state, run, key, False, self.cfg['live_enabled'])
        self.personal[email] = wid

    def permitted(self, email):
        if email in self.cfg['allowlist']:
            return True
        if not self.cfg.get('personal_workspaces') or not email:
            return False
        with self.original.db() as db:
            return bool(db.execute('SELECT 1 FROM workspace_accounts WHERE email=? AND active=1', (email,)).fetchone())

    def register_verified(self, email):
        # Called only after server-side Google ID token, nonce and email verification.
        if self.permitted(email):
            if self.cfg.get('personal_workspaces'):
                with self.lock:
                    self.provision(email)
            return
        if not self.cfg.get('open_signup') or not self.cfg.get('personal_workspaces'):
            raise ValueError('Account is not invited')
        address(email)
        with self.lock:
            with self.original.db() as db:
                db.execute('BEGIN IMMEDIATE')
                existing = db.execute('SELECT active FROM workspace_accounts WHERE email=?', (email,)).fetchone()
                if existing:
                    if not existing['active']:
                        raise ValueError('Account disabled')
                else:
                    count = db.execute('SELECT count(*) FROM workspace_accounts').fetchone()[0]
                    recent = db.execute('SELECT count(*) FROM workspace_accounts WHERE created>?', (time.time()-3600,)).fetchone()[0]
                    if count >= self.cfg.get('workspace_limit', 100) or recent >= 10:
                        raise ValueError('New workspace capacity temporarily unavailable')
                    db.execute('INSERT INTO workspace_accounts(email,created) VALUES(?,?)', (email,time.time()))
            self.provision(email)
            self.original.event(email, 'personal_workspace_registered')

    def choices(self, email):
        result = []
        if email in self.cfg['allowlist']:
            result.append({'id': 'shared', 'name': 'LionMail campaign', 'role': self.cfg['allowlist'][email]})
        if email in self.personal:
            result.insert(0, {'id': self.personal[email], 'name': 'My workspace · '+email, 'role': 'owner'})
        return result

    def select(self, email, wid=None):
        if not self.permitted(email):
            raise PermissionError('Account not available')
        choices = self.choices(email)
        if not choices:
            raise PermissionError('Workspace not available')
        wid = wid or choices[0]['id']
        match = next((x for x in choices if x['id'] == wid), None)
        if not match:
            raise PermissionError('Workspace not available')
        return match, self.services[wid]

    def run_due(self):
        # One bounded scheduler. Failure in one tenant must not skip others.
        if self.cfg.get('personal_workspaces'):
            with self.lock:
                entries = list(self.services.items())
        else:
            entries = list(self.services.items())
        for wid, service in entries:
            try:
                if wid != 'shared':
                    email = read_status(service.run)['sender']
                    if not self.permitted(email):
                        continue
                service.run_due()
            except Exception:
                logging.error('Workspace scheduler unavailable; no private details logged.')


def add_request(service, data, actor):
    """Explicitly add one reviewed recipient; never copy an existing campaign."""
    def text(name, limit):
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise ValueError('Complete the office, recipient, subject and message fields')
        return value.strip()
    organization, recipient = text('organization', 200), address(text('recipient', 254))
    subject, body = text('subject', 300), text('body', 12000)
    if '\n' in subject or '\r' in subject:
        raise ValueError('Subject must be a single line')
    with locked(service.folder), locked(service.run):
        pilot = Pilot(service.run)
        try:
            campaign, config = pilot.config()
            if config.get('workspace_schema') != 1 or config['sender'] != actor:
                raise ValueError('Requests can only be added to your personal workspace')
            if not campaign['paused'] or service.status()['enabled']:
                raise ValueError('Pause outreach and disable scheduling before adding contacts')
            if len(config['requests']) >= 5 or any(r['to'] == recipient for r in config['requests']):
                raise ValueError('This workspace supports up to five distinct recipients')
            rid = 'CONTACT-' + str(len(config['requests'])+1)
            config['requests'].append(dict(id=rid, organization=organization, to=recipient, subject=subject, body=body))
            config['pilot_contact'] = config['requests'][0]['id']
            validate(config)
            raw = canonical(config)
            pilot.db.execute('BEGIN IMMEDIATE')
            pilot.db.execute('UPDATE campaign SET config=?,digest=?,expanded=1 WHERE id=1', (raw.decode(), sha(raw)))
            pilot.db.execute('INSERT INTO requests(id) VALUES(?)', (rid,))
            pilot.log('contact_added', actor+': '+rid+'; messages still require approval')
            pilot.db.commit()
        finally:
            pilot.close()
    service.event(actor, 'contact_added')
