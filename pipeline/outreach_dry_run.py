"""Milestone 10: local-only outreach simulation. No network or live mailbox adapter."""
import argparse
from contextlib import contextmanager
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
KINDS = {'reply', 'out_of_office', 'bounce', 'stop', 'wrong_contact', 'fee', 'portal', 'unknown'}
FORWARD_TO = 'review-team@example.invalid'


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def business_day(day, count=5, holidays=()):
    day = date.fromisoformat(day)
    while count:
        day += timedelta(days=1)
        if day.weekday() < 5 and day.isoformat() not in holidays:
            count -= 1
    return day.isoformat()


def database(path):
    db = sqlite3.connect(path, isolation_level=None, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    return db


@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


class SimulatedCrash(BaseException):
    """Simulate worker death after a provider write but before the local commit."""


class FakeMailbox:
    """Durable fake transport. All destinations must use reserved .invalid domains."""
    def __init__(self, path):
        self.db = database(path)
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sent (
          action_key TEXT PRIMARY KEY, provider_id TEXT UNIQUE NOT NULL,
          recipient TEXT NOT NULL, thread_id TEXT NOT NULL,
          body TEXT NOT NULL, kind TEXT NOT NULL, sent_on TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inbox (
          event_id TEXT PRIMARY KEY, thread_id TEXT, in_reply_to TEXT,
          request_id TEXT, sender TEXT NOT NULL, body TEXT NOT NULL,
          kind TEXT NOT NULL, return_on TEXT, received_on TEXT NOT NULL);
        ''')
        self.fault = None
        self.sync_failure = False

    def close(self):
        self.db.close()

    def send(self, key, recipient, thread, body, kind, day):
        if '\n' in recipient or not recipient.endswith('.invalid'):
            raise ValueError('Fake transport accepts only .invalid recipients')
        prior = self.db.execute('SELECT * FROM sent WHERE action_key=?', (key,)).fetchone()
        if prior:
            if (prior['recipient'], prior['body'], prior['thread_id']) != (recipient, body, thread):
                raise ValueError('Action key reused with different content')
            return prior['provider_id']
        fault, self.fault = self.fault, None
        if fault == 'before':
            raise TimeoutError('Simulated timeout before provider acceptance')
        mid = 'fake-' + digest(key)[:24]
        self.db.execute('INSERT INTO sent VALUES (?,?,?,?,?,?,?)',
                        (key, mid, recipient, thread, body, kind, day))
        if fault == 'after':
            raise TimeoutError('Simulated ambiguous timeout after provider acceptance')
        if fault == 'crash':
            raise SimulatedCrash('Simulated process death after provider acceptance')
        return mid

    def sync(self, day):
        if self.sync_failure:
            raise ConnectionError('Simulated mailbox sync failure')
        return self.db.execute('SELECT * FROM inbox WHERE received_on<=? ORDER BY received_on,event_id', (day,)).fetchall()

    def inject(self, event_id, sender, body, day, *, kind='reply', thread=None,
               in_reply_to=None, request_id=None, return_on=None):
        if kind not in KINDS:
            raise ValueError('Unknown simulated reply kind')
        date.fromisoformat(day)
        if return_on:
            date.fromisoformat(return_on)
        values = (event_id, thread, in_reply_to, request_id, sender, body, kind, return_on, day)
        old = self.db.execute('SELECT * FROM inbox WHERE event_id=?', (event_id,)).fetchone()
        if old and tuple(old) != values:
            raise ValueError('Duplicate event identity with changed content')
        self.db.execute('INSERT OR IGNORE INTO inbox VALUES (?,?,?,?,?,?,?,?,?)', values)


def request_text(contact):
    greeting = contact.get('contact_name') or 'Procurement team'
    return (f'DRY RUN ONLY — not sent\nSubject: Road salt contract documents for FY2027 — {contact["organization"]}\n\n'
            f'Hello {greeting},\n\n'
            'I am Yordanos Kassa, researching public road-salt contracts. Could you provide '
            'existing awarded road-salt contract documents for the 2026–2027 winter season, '
            'including supplier, quoted price, scheduled or contracted tonnage, early/seasonal '
            'fill schedules and amendments? Please exclude water-treatment salt.\n\n'
            'If your office purchases through COSTARS or another cooperative, please identify '
            'that arrangement so I can avoid double counting. If another office holds these '
            'records, please let me know the appropriate request channel. Please advise before '
            'incurring any fees.\n\nThank you,\nYordanos Kassa\n'
            '[Sender address and signature require review before a live campaign.]')


class Outreach:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = database(self.folder / 'campaign.sqlite3')
        self.mail = FakeMailbox(self.folder / 'fake-mailbox.sqlite3')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS campaign (
          id INTEGER PRIMARY KEY CHECK(id=1), mode TEXT NOT NULL CHECK(mode='dry_run'),
          paused INTEGER NOT NULL, registry_hash TEXT NOT NULL, holidays TEXT NOT NULL,
          last_tick TEXT);
        CREATE TABLE IF NOT EXISTS requests (
          id TEXT PRIMARY KEY, organization TEXT UNIQUE NOT NULL, real_email TEXT NOT NULL,
          recipient TEXT UNIQUE NOT NULL, thread_id TEXT UNIQUE NOT NULL,
          draft TEXT NOT NULL, draft_hash TEXT NOT NULL, status TEXT NOT NULL,
          suppressed INTEGER NOT NULL DEFAULT 0, due_on TEXT, stage INTEGER NOT NULL DEFAULT 0,
          proposed_return_on TEXT);
        CREATE TABLE IF NOT EXISTS actions (
          action_key TEXT PRIMARY KEY, request_id TEXT NOT NULL REFERENCES requests(id),
          kind TEXT NOT NULL, recipient TEXT NOT NULL, body TEXT NOT NULL,
          status TEXT NOT NULL, provider_id TEXT, attempts INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS received (
          event_id TEXT PRIMARY KEY, request_id TEXT REFERENCES requests(id),
          outcome TEXT NOT NULL, original TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS events (
          seq INTEGER PRIMARY KEY, day TEXT NOT NULL, request_id TEXT,
          event TEXT NOT NULL, details TEXT NOT NULL);
        ''')

    def close(self):
        self.db.close()
        self.mail.close()

    def log(self, day, rid, event, details=''):
        self.db.execute('INSERT INTO events(day,request_id,event,details) VALUES(?,?,?,?)', (day, rid, event, details))

    def initialize(self, registry, day, holidays=()):
        date.fromisoformat(day)
        for h in holidays:
            date.fromisoformat(h)
        payload = Path(registry).read_text(encoding='utf-8')
        rows = json.loads(payload)['contacts']
        if len(rows) != 5 or len({r['contact_id'] for r in rows}) != 5 or len({r['organization'] for r in rows}) != 5:
            raise ValueError('Pilot requires five distinct contacts/offices')
        if any(r['state'] != 'PA' or r.get('send_authorized') is not False for r in rows):
            raise ValueError('Only the unapproved Pennsylvania registry is accepted')
        with transaction(self.db):
            old = self.db.execute('SELECT * FROM campaign').fetchone()
            if old:
                if old['registry_hash'] != digest(payload) or json.loads(old['holidays']) != list(holidays):
                    raise ValueError('Existing simulation has different inputs; use a new folder')
                return
            self.db.execute('INSERT INTO campaign VALUES(1,?,?,?,?,?)', ('dry_run', 0, digest(payload), json.dumps(list(holidays)), None))
            for r in rows:
                rid = r['contact_id']
                body = request_text(r)
                self.db.execute('''INSERT INTO requests
                (id,organization,real_email,recipient,thread_id,draft,draft_hash,status,due_on)
                VALUES(?,?,?,?,?,?,?,?,?)''',
                (rid, r['organization'], r['email'], rid.lower()+'@procurement.example.invalid',
                 'thread-'+rid, body, digest(body), 'queued', day))
                self.log(day, rid, 'draft_prepared', 'Simulation only; real sender not configured')

    def pause(self, value, day):
        with transaction(self.db):
            self.db.execute('UPDATE campaign SET paused=?', (int(value),))
            self.log(day, None, 'campaign_paused' if value else 'campaign_resumed')

    def _receive(self, event, day):
        if self.db.execute('SELECT 1 FROM received WHERE event_id=?', (event['event_id'],)).fetchone():
            return
        candidates = set()
        if event['thread_id']:
            candidates.update(r['id'] for r in self.db.execute('SELECT id FROM requests WHERE thread_id=?', (event['thread_id'],)))
        if event['in_reply_to']:
            candidates.update(r['request_id'] for r in self.db.execute('SELECT request_id FROM actions WHERE provider_id=?', (event['in_reply_to'],)))
        # A request-id fallback requires the known simulated sender. Never use subject matching.
        if not candidates and event['request_id']:
            candidates.update(r['id'] for r in self.db.execute('SELECT id FROM requests WHERE id=? AND recipient=?', (event['request_id'], event['sender'])))
        rid = next(iter(candidates)) if len(candidates) == 1 else None
        outcome = 'unmatched' if not candidates else 'conflicting_references' if len(candidates) > 1 else event['kind']
        self.db.execute('INSERT INTO received VALUES(?,?,?,?)', (event['event_id'], rid, outcome, json.dumps(dict(event))))
        if len(candidates) > 1:
            for candidate in candidates:
                self.db.execute("UPDATE requests SET status='needs_review',due_on=NULL WHERE id=?", (candidate,))
            self.log(day, None, outcome, event['event_id'])
            return
        if rid is None:
            self.log(day, None, 'unmatched_message', event['event_id'])
            return
        request = self.db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
        kind = event['kind']
        state = {'reply':'replied', 'out_of_office':'held', 'bounce':'bounced', 'stop':'suppressed',
                 'wrong_contact':'needs_review', 'fee':'needs_review', 'portal':'needs_review', 'unknown':'needs_review'}[kind]
        suppress = int(kind in {'stop', 'bounce'})
        if request['suppressed']:
            state = request['status']
        self.db.execute('UPDATE requests SET status=?,suppressed=MAX(suppressed,?),due_on=NULL,proposed_return_on=? WHERE id=?',
                        (state, suppress, event['return_on'] if kind == 'out_of_office' else None, rid))
        self.log(day, rid, 'received_'+kind, event['event_id'])
        # Forwarding is simulated and separate from request scheduling.
        key = 'forward:'+event['event_id']+':'+FORWARD_TO
        body = f'SIMULATED FORWARD\nRequest: {rid}\nFrom: {event["sender"]}\nOriginal event: {event["event_id"]}\n\n{event["body"]}'
        self.db.execute('INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?)',
                        (key, rid, 'forward', FORWARD_TO, body, 'pending', None, 0))

    def _send(self, key, rid, kind, recipient, body, thread, day):
        self.db.execute('INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?)', (key, rid, kind, recipient, body, 'pending', None, 0))
        action = self.db.execute('SELECT * FROM actions WHERE action_key=?', (key,)).fetchone()
        if action['status'] == 'sent':
            return True
        self.db.execute('UPDATE actions SET attempts=attempts+1 WHERE action_key=?', (key,))
        try:
            mid = self.mail.send(key, recipient, thread, body, kind, day)
        except TimeoutError as exc:
            self.db.execute("UPDATE actions SET status='reconcile' WHERE action_key=?", (key,))
            self.log(day, rid, 'send_uncertain', str(exc))
            return False
        self.db.execute("UPDATE actions SET status='sent',provider_id=? WHERE action_key=?", (mid, key))
        self.log(day, rid, 'simulated_'+kind, mid)
        return True

    def tick(self, day):
        date.fromisoformat(day)
        # One worker holds a SQLite write transaction. Process death releases the lock.
        # The separate mailbox survives local rollback so reconciliation is testable.
        with transaction(self.db):
            c = self.db.execute('SELECT * FROM campaign').fetchone()
            if not c or c['mode'] != 'dry_run':
                raise ValueError('Initialize the dry-run campaign first')
            if c['last_tick'] and day < c['last_tick']:
                raise ValueError('Simulation clock cannot move backwards')
            try:
                incoming = self.mail.sync(day)
            except ConnectionError as exc:
                self.log(day, None, 'sync_failed', str(exc))
                return
            # Reconcile uncertain provider results BEFORE associating replies.
            for sent in self.mail.db.execute('SELECT * FROM sent'):
                if sent['kind'] in {'initial','followup'}:
                    _, rid, stage_text = sent['action_key'].split(':')
                    stage = int(stage_text)
                    request = self.db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
                    if not request or sent['recipient'] != request['recipient']:
                        raise ValueError('Unexpected fake mailbox action')
                    self.db.execute('INSERT OR IGNORE INTO actions VALUES(?,?,?,?,?,?,?,?)',
                                    (sent['action_key'], rid, sent['kind'], sent['recipient'], sent['body'], 'sent', sent['provider_id'], 1))
                    if request['stage'] == stage and request['status'] in {'queued','waiting'}:
                        self.db.execute("UPDATE requests SET stage=?,status='waiting',due_on=? WHERE id=?",
                                        (stage+1, business_day(sent['sent_on'], holidays=json.loads(c['holidays'])), rid))
                        self.log(day, rid, 'provider_send_reconciled', sent['provider_id'])
                self.db.execute("UPDATE actions SET provider_id=?,status='sent' WHERE action_key=?",
                                (sent['provider_id'], sent['action_key']))
            for event in incoming:
                self._receive(event, day)
            self.db.execute('UPDATE campaign SET last_tick=?', (day,))
            if c['paused']:
                self.log(day, None, 'tick_paused')
                return
            for r in self.db.execute("SELECT * FROM requests WHERE status IN ('queued','waiting') AND suppressed=0 AND due_on<=? ORDER BY id", (day,)).fetchall():
                if digest(r['draft']) != r['draft_hash']:
                    self.db.execute("UPDATE requests SET status='needs_review',due_on=NULL WHERE id=?", (r['id'],))
                    self.log(day, r['id'], 'draft_integrity_failed')
                    continue
                stage = r['stage']
                if stage == 3:
                    self.db.execute("UPDATE requests SET status='closed_no_response',due_on=NULL WHERE id=?", (r['id'],))
                    self.log(day, r['id'], 'closed_no_response')
                    continue
                kind = 'initial' if stage == 0 else 'followup'
                body = r['draft'] if stage == 0 else f'DRY RUN ONLY — follow-up {stage} of 2\n\nFollowing up on the FY2027 road-salt records request below. Please advise if another office or request channel is appropriate.\n\n'+r['draft']
                key = f'request:{r["id"]}:{stage}'
                if self._send(key, r['id'], kind, r['recipient'], body, r['thread_id'], day):
                    # Use the provider's actual simulated send date, not retry date.
                    sent = self.mail.db.execute('SELECT sent_on FROM sent WHERE action_key=?', (key,)).fetchone()
                    due = business_day(sent['sent_on'], holidays=json.loads(c['holidays']))
                    self.db.execute("UPDATE requests SET stage=?,status='waiting',due_on=? WHERE id=?", (stage+1, due, r['id']))
            for a in self.db.execute("SELECT * FROM actions WHERE kind='forward' AND status!='sent'").fetchall():
                r = self.db.execute('SELECT thread_id FROM requests WHERE id=?', (a['request_id'],)).fetchone()
                self._send(a['action_key'], a['request_id'], 'forward', a['recipient'], a['body'], r['thread_id'], day)

    def report(self):
        return {'mode':'dry_run', 'real_emails_sent':0,
                'requests':[dict(r) for r in self.db.execute('SELECT id,organization,status,stage,due_on,suppressed,proposed_return_on FROM requests ORDER BY id')],
                'actions':[dict(r) for r in self.db.execute('SELECT action_key,kind,status,attempts,recipient FROM actions ORDER BY action_key')],
                'events':[dict(r) for r in self.db.execute('SELECT * FROM events ORDER BY seq')],
                'simulated_mailbox_records':self.mail.db.execute('SELECT COUNT(*) FROM sent').fetchone()[0]}

    def export(self):
        report = self.report()
        (self.folder/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
        lines = ['# Outreach dry run', '', '**Simulation only. No external mail sent.**', '',
                 'These outcomes are invented test fixtures, not actual responses from the offices.', '',
                 f'Simulated mailbox messages: {report["simulated_mailbox_records"]}. Real emails sent: 0.', '',
                 '| Office | Request status | Request messages | Next due |', '| --- | --- | --- | --- |']
        lines += [f'| {r["organization"]} | {r["status"]} | {r["stage"]} | {r["due_on"] or "—"} |' for r in report['requests']]
        lines += ['', 'Full actions and event history: report.json. Dates use a simulated calendar, not an active scheduler.']
        (self.folder/'REPORT.md').write_text('\n'.join(lines)+'\n', encoding='utf-8')
        for r in self.db.execute('SELECT id,real_email,recipient,draft FROM requests'):
            (self.folder/(r['id']+'-draft.txt')).write_text(f'Review-only intended contact: {r["real_email"]}\nSimulation destination: {r["recipient"]}\n\n{r["draft"]}\n', encoding='utf-8')
        return report


def demo(folder, registry):
    folder = Path(folder)
    if (folder/'campaign.sqlite3').exists():
        raise ValueError('Demo requires a new folder; existing runs are never overwritten')
    app = Outreach(folder)
    try:
        app.initialize(registry, '2026-09-14')
        app.mail.fault = 'after'
        app.tick('2026-09-14')
        app.close()
        app = Outreach(folder)  # Explicit process-state reconstruction after uncertain send.
        app.tick('2026-09-14')
        examples = [('PA-LAN-001','reply','I can provide the award after checking our records.'),
                    ('PA-WES-001','out_of_office','I am away. Please hold your request until I return.'),
                    ('PA-WAS-001','stop','Please do not send additional follow-ups.'),
                    ('PA-ERI-001','bounce','Simulated delivery failure.')]
        for rid, kind, body in examples:
            r = app.db.execute('SELECT * FROM requests WHERE id=?', (rid,)).fetchone()
            for _ in range(2):
                app.mail.inject('fixture-'+rid, r['recipient'], body, '2026-09-15', kind=kind,
                                thread=r['thread_id'], return_on='2026-09-28' if kind=='out_of_office' else None)
        for day in ['2026-09-15','2026-09-21','2026-09-28','2026-10-05']:
            app.tick(day)
        return app.export()
    finally:
        app.close()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['init','tick','inject','pause','resume','report','demo'])
    p.add_argument('--run', type=Path, required=True)
    p.add_argument('--registry', type=Path, default=ROOT/'data/outreach/milestone-9/contacts.json')
    p.add_argument('--on', default='2026-09-14', help='Simulated calendar date; no wall-clock scheduling')
    p.add_argument('--holiday', action='append', default=[])
    p.add_argument('--request'); p.add_argument('--event-id'); p.add_argument('--body', default='Simulated reply')
    p.add_argument('--kind', choices=sorted(KINDS), default='reply'); p.add_argument('--return-on')
    p.add_argument('--fault', choices=['before','after','crash']); p.add_argument('--sync-failure', action='store_true')
    a = p.parse_args()
    if a.command == 'demo':
        report = demo(a.run,a.registry)
    else:
        app = Outreach(a.run)
        try:
            if a.command == 'init': app.initialize(a.registry,a.on,a.holiday)
            elif a.command == 'tick':
                app.mail.fault=a.fault; app.mail.sync_failure=a.sync_failure; app.tick(a.on)
            elif a.command in ['pause','resume']: app.pause(a.command=='pause',a.on)
            elif a.command == 'inject':
                r=app.db.execute('SELECT * FROM requests WHERE id=?',(a.request,)).fetchone()
                if not r or not a.event_id: p.error('inject requires a valid --request and --event-id')
                app.mail.inject(a.event_id,r['recipient'],a.body,a.on,kind=a.kind,thread=r['thread_id'],return_on=a.return_on)
            report=app.export()
        finally: app.close()
    print(json.dumps({k:report[k] for k in ['mode','real_emails_sent','requests','simulated_mailbox_records']},indent=2))


if __name__ == '__main__': main()
