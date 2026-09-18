"""Single-host durable scheduler state and encrypted mailbox credentials."""
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time

from cryptography.fernet import Fernet
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials

from outreach_console import action, read_status
from outreach_live import Gmail, Pilot, locked


class Service:
    def __init__(self, folder, run, key, demo=False, live_enabled=False, interval=900):
        self.folder, self.run = Path(folder), Path(run)
        self.demo, self.live_enabled, self.interval = demo, live_enabled, max(60, int(interval))
        self.cipher = Fernet(key)
        read_status(self.run, demo)
        if demo and not (self.run/'console-demo.json').is_file():
            raise ValueError('Separate initialized demonstration required')
        pilot = Pilot(self.run)
        try:
            with locked(self.run):
                pilot.require_message_approval()
        finally:
            pilot.close()
        self.folder.mkdir(parents=True, exist_ok=True)
        with self.db() as db:
            db.executescript('''
            CREATE TABLE IF NOT EXISTS settings(id INTEGER PRIMARY KEY, enabled INTEGER DEFAULT 0,
              next_run REAL, last_attempt REAL, last_success REAL, last_scan REAL,
              active INTEGER DEFAULT 0, error TEXT);
            INSERT OR IGNORE INTO settings(id) VALUES(1);
            CREATE TABLE IF NOT EXISTS credential(id INTEGER PRIMARY KEY, encrypted BLOB NOT NULL);
            CREATE TABLE IF NOT EXISTS sessions(id TEXT PRIMARY KEY, email TEXT, csrf TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS oauth(state TEXT PRIMARY KEY, session TEXT, data TEXT, expires REAL);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, at REAL, actor TEXT, event TEXT);
            CREATE TABLE IF NOT EXISTS identity(id INTEGER PRIMARY KEY, run TEXT, demo INTEGER);
            ''')
            identity = db.execute('SELECT * FROM identity WHERE id=1').fetchone()
            if identity and (identity['run'] != str(self.run.resolve()) or bool(identity['demo']) != demo):
                raise ValueError('Service belongs to another ledger or mode')
            db.execute('INSERT OR IGNORE INTO identity VALUES(1,?,?)', (str(self.run.resolve()), int(demo)))
            if 'last_heartbeat' not in {r['name'] for r in db.execute('PRAGMA table_info(settings)')}:
                db.execute('ALTER TABLE settings ADD COLUMN last_heartbeat REAL')

    @contextmanager
    def db(self):
        db = sqlite3.connect(self.folder/'service.sqlite3', timeout=10)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def event(self, actor, event):
        with self.db() as db:
            db.execute('INSERT INTO events(at,actor,event) VALUES(?,?,?)', (time.time(), actor, event))

    def status(self):
        with self.db() as db:
            state = dict(db.execute('SELECT * FROM settings WHERE id=1').fetchone())
            state['mailbox_connected'] = bool(db.execute('SELECT 1 FROM credential').fetchone())
            state['events'] = [dict(r) for r in db.execute('SELECT at,actor,event FROM events ORDER BY id DESC LIMIT 30')]
        state.update(mode='SIMULATION' if self.demo else 'LIVE', live_enabled=self.live_enabled,
                     interval_seconds=self.interval)
        return state

    def save_credentials(self, credentials):
        if not credentials.refresh_token:
            raise ValueError('Offline mailbox consent is required; no refresh token received')
        encrypted = self.cipher.encrypt(credentials.to_json().encode())
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO credential VALUES(1,?)', (encrypted,))

    def gmail(self):
        with self.db() as db:
            row = db.execute('SELECT encrypted FROM credential WHERE id=1').fetchone()
        if not row:
            raise ValueError('Mailbox connection required')
        info = json.loads(self.cipher.decrypt(row['encrypted']))
        credentials = Credentials.from_authorized_user_info(info)
        credentials.refresh(Request())
        self.save_credentials(credentials)
        return Gmail(credentials.token)

    def set_schedule(self, enabled, actor):
        with locked(self.folder):
            state = self.status()
            if enabled and not self.demo and (not self.live_enabled or not state['mailbox_connected']):
                raise ValueError('Live sending is not enabled or the mailbox needs connection')
            if enabled and state['active']:
                raise ValueError('Interrupted run requires operator recovery before scheduling')
            with self.db() as db:
                db.execute('UPDATE settings SET enabled=?,next_run=?,error=NULL WHERE id=1',
                           (int(enabled), time.time()+self.interval if enabled else None))
            self.event(actor, 'schedule_enabled' if enabled else 'schedule_disabled')

    def disconnect(self, actor):
        with locked(self.folder):
            with self.db() as db:
                db.execute('DELETE FROM credential')
                db.execute('UPDATE settings SET enabled=0,next_run=NULL WHERE id=1')
            self.event(actor, 'mailbox_disconnected_locally')

    def draft_action(self, command, data, actor):
        with locked(self.folder):
            if command == 'prepare_drafts' or command == 'approve_send':
                if not self.demo and (not self.live_enabled or not self.status()['mailbox_connected']):
                    raise ValueError('Connect the approved live mailbox before scanning or sending.')
                kwargs = dict(approved_key=data.get('key'), approved_digest=data.get('digest'), actor=actor) if command == 'approve_send' else {}
                if command == 'approve_send' and (not kwargs['approved_key'] or not kwargs['approved_digest']):
                    raise ValueError('Exact draft and digest required')
                if self.demo:
                    action(self.run, True, dict(command='demo_tick', **kwargs))
                else:
                    pilot = Pilot(self.run)
                    try:
                        pilot.tick(self.gmail(), **kwargs)
                    finally:
                        pilot.close()
                with self.db() as db:
                    db.execute('UPDATE settings SET last_scan=? WHERE id=1', (time.time(),))
            else:
                pilot = Pilot(self.run)
                try:
                    pilot.revise(data.get('key'), data.get('digest'), actor,
                                 data.get('subject'), data.get('body'), command == 'reject_draft')
                finally:
                    pilot.close()
            self.event(actor, command)

    def drafts(self):
        pilot = Pilot(self.run)
        try:
            return pilot.drafts()
        finally:
            pilot.close()

    def run_due(self, now=None):
        now = time.time() if now is None else now
        with self.db() as db:
            db.execute('UPDATE settings SET last_heartbeat=? WHERE id=1', (time.time(),))
        try:
            with locked(self.folder):
                s = self.status()
                if s['active']:
                    with self.db() as db:
                        db.execute("UPDATE settings SET enabled=0,next_run=NULL,error='Previous run interrupted; operator recovery required' WHERE id=1")
                    return 'interrupted'
                if not s['enabled'] or s['next_run'] is None or s['next_run'] > now:
                    return 'not_due'
                with self.db() as db:
                    db.execute('UPDATE settings SET active=1,last_attempt=?,next_run=NULL WHERE id=1', (now,))
                self.event('scheduler', 'run_started')
                try:
                    if self.demo:
                        action(self.run, True, {'command': 'demo_tick'})
                        with self.db() as db:
                            db.execute('UPDATE settings SET last_scan=? WHERE id=1', (time.time(),))
                    else:
                        if not self.live_enabled:
                            raise ValueError('Deployment live sending gate is disabled')
                        service = self
                        class TrackedPilot(Pilot):
                            def sync(self, *args):
                                super().sync(*args)
                                with service.db() as db:
                                    db.execute('UPDATE settings SET last_scan=? WHERE id=1', (time.time(),))
                        pilot = TrackedPilot(self.run)
                        try:
                            pilot.tick(self.gmail())
                        finally:
                            pilot.close()
                    finished = time.time()
                    with self.db() as db:
                        db.execute('UPDATE settings SET active=0,last_success=?,next_run=?,error=NULL WHERE id=1',
                                   (finished, finished+self.interval))
                    self.event('scheduler', 'run_succeeded')
                    return 'succeeded'
                except Exception:
                    # Never include provider responses, credentials or email content in logs/UI.
                    with self.db() as db:
                        db.execute("UPDATE settings SET active=0,enabled=0,next_run=NULL,error='Run stopped. Check mailbox authorization and worker ledger before re-enabling.' WHERE id=1")
                    self.event('scheduler', 'run_failed_schedule_disabled')
                    return 'failed'
        except FileExistsError:
            return 'busy'
