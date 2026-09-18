"""Milestone 11: supervised Gmail pilot. No network unless an operator runs tick.

Approval is an operator record, not authentication. Keep the run directory private.
"""
import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import format_datetime, parseaddr
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from outreach_dry_run import ROOT, business_day, request_text


def sha(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False).encode()


def address(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", value):
        raise ValueError('A single plain email address is required')
    return value.lower()


def decode(value):
    return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))


def mime_content(msg):
    return [(p.get_content_type(), p.get_filename(), sha(p.get_payload(decode=True) or b''))
            for p in msg.walk() if not p.is_multipart()]


def prepare(registry):
    contacts = json.loads(Path(registry).read_text(encoding='utf-8'))['contacts']
    requests = []
    for c in contacts:
        text = request_text(c).split('\n', 1)[1]
        subject, body = text.split('\n\n', 1)
        body = body.replace('\n[Sender address and signature require review before a live campaign.]', '')
        requests.append({'id': c['contact_id'], 'organization': c['organization'], 'to': c['email'],
                         'subject': subject.removeprefix('Subject: '), 'body': body})
    return {'campaign_id': 'pa-fy2027-pilot', 'sender': None, 'forward_to': [],
            'pilot_contact': 'PA-LAN-001', 'timezone': 'America/New_York', 'holidays': [],
            'followup_business_days': 5, 'max_followups': 2,
            'followup_text': 'Following up on the road-salt records request below. Please advise if another office or request channel is appropriate.',
            'registry_sha256': sha(Path(registry).read_bytes()), 'requests': requests}


def validate(config):
    address(config['sender'])
    if not config['forward_to'] or len(set(config['forward_to'])) != len(config['forward_to']):
        raise ValueError('Specify unique approved forwarding recipients')
    for recipient in config['forward_to']:
        address(recipient)
    ZoneInfo(config['timezone'])
    for day in config['holidays']:
        datetime.strptime(day, '%Y-%m-%d')
    if config['followup_business_days'] != 5 or config['max_followups'] != 2:
        raise ValueError('This bounded pilot supports five business days and two follow-ups')
    rows = config['requests']
    personal = config.get('workspace_schema') == 1
    if (not (0 <= len(rows) <= 5) if personal else len(rows) != 5) or len({r['id'] for r in rows}) != len(rows) or len({r['to'].lower() for r in rows}) != len(rows):
        raise ValueError('Exactly five distinct contacts required')
    if not (personal and not rows and config['pilot_contact'] is None) and config['pilot_contact'] not in {r['id'] for r in rows}:
        raise ValueError('Unknown pilot contact')
    if not re.fullmatch(r'[a-zA-Z0-9-]+', config['campaign_id']):
        raise ValueError('Invalid campaign identifier')
    for r in rows:
        address(r['to'])
        if not re.fullmatch(r'[A-Z0-9-]+', r['id']) or not r['body'].strip() or not r['subject'].strip():
            raise ValueError('Invalid request')
        if '\n' in r['subject'] or '\r' in r['subject']:
            raise ValueError('Invalid subject')


class MailReadError(RuntimeError):
    def __init__(self, kind):
        self.kind = kind
        super().__init__('Mailbox read stopped: ' + kind)


class Gmail:
    """Short-lived bearer token comes from environment; never persisted or printed.

    No automatic POST retries. A network failure requires reconciliation.
    """
    def __init__(self, token=None, deadline=None):
        self.token = token or os.environ.get('ROAD_SALT_GMAIL_ACCESS_TOKEN')
        if not self.token:
            raise ValueError('Set ROAD_SALT_GMAIL_ACCESS_TOKEN privately before live operations')
        self._next_call = 0.0
        self.deadline = deadline
        self.read_calls = 0
        self.read_retries = 0

    def remaining(self):
        remaining = 30 if self.deadline is None else self.deadline - time.monotonic()
        if remaining <= 0:
            raise MailReadError('time_budget')
        return min(30, remaining)

    def api(self, path, params=None, body=None):
        # Gmail's May 2026 quota is 6,000 units/user/minute. Pace this
        # single-worker client to at most 3,000; never retry a send here.
        cost = 100 if body is not None else (20 if path.startswith('messages/') else 5)
        now = time.monotonic()
        delay = max(0.0, self._next_call - now)
        if self.deadline is not None and delay >= self.remaining():
            raise MailReadError('time_budget')
        time.sleep(delay)
        self._next_call = time.monotonic() + cost / 50.0
        url = 'https://gmail.googleapis.com/gmail/v1/users/me/' + path
        if params:
            url += '?' + urlencode(params, doseq=True)
        req = Request(url, data=canonical(body) if body is not None else None,
                      headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        for attempt in range(3 if body is None else 1):
            try:
                if body is None:
                    self.read_calls += 1
                with urlopen(req, timeout=self.remaining()) as response:
                    chunks, size = [], 0
                    reader = getattr(response, 'read1', response.read)
                    while True:
                        self.remaining()
                        chunk = reader(65536)
                        if not chunk:
                            return json.loads(b''.join(chunks))
                        size += len(chunk)
                        if size > 24*1024*1024:
                            raise MailReadError('response_limit')
                        chunks.append(chunk)
            except HTTPError as exc:
                kind = 'quota' if exc.code in {403, 429} else 'consent' if exc.code == 401 else 'provider'
                retry = exc.code in {429, 500, 502, 503, 504}
            except (URLError, TimeoutError):
                kind, retry = 'connection', True
            if body is not None:
                raise RuntimeError('Gmail send outcome uncertain; reconcile before any further action') from None
            if not retry or attempt == 2:
                raise MailReadError(kind) from None
            delay = 2 ** attempt
            if self.deadline is not None and delay >= self.remaining():
                raise MailReadError('time_budget')
            self.read_retries += 1
            time.sleep(delay)

    def profile(self):
        return self.api('profile')['emailAddress'].lower()

    def messages(self, query):
        result, token = [], None
        while True:
            params = {'q': query, 'maxResults': 100, 'includeSpamTrash': 'true'}
            if token:
                params['pageToken'] = token
            page = self.api('messages', params)
            result.extend(page.get('messages', []))
            token = page.get('nextPageToken')
            if len(result) > 2000:
                raise RuntimeError('Mailbox scan exceeds pilot limit; outbound work blocked')
            if not token:
                return result

    def get(self, mid, raw=False):
        return self.api('messages/' + quote(mid, safe=''), {'format': 'raw' if raw else 'metadata'})

    def send(self, raw, thread=None):
        body = {'raw': base64.urlsafe_b64encode(raw).decode()}
        if thread:
            body['threadId'] = thread
        return self.api('messages/send', body=body)


@contextmanager
def locked(folder):
    """OS-exclusive process lock; stale locks require operator review, never expiry."""
    path = folder / 'worker.lock'
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        path.unlink()


class Pilot:
    def __init__(self, folder):
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.folder / 'live.sqlite3', isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS campaign (id INTEGER PRIMARY KEY CHECK(id=1), config TEXT,
          digest TEXT, approval TEXT, started INTEGER, paused INTEGER DEFAULT 0, expanded INTEGER DEFAULT 0);
        CREATE TABLE IF NOT EXISTS requests (id TEXT PRIMARY KEY, stage INTEGER DEFAULT 0,
          state TEXT DEFAULT 'queued', due TEXT, thread TEXT);
        CREATE TABLE IF NOT EXISTS jobs (key TEXT PRIMARY KEY, rid TEXT, kind TEXT, stage INTEGER,
          recipient TEXT, raw BLOB, rfcid TEXT UNIQUE, state TEXT, provider TEXT, thread TEXT, sent_day TEXT);
        CREATE TABLE IF NOT EXISTS incoming (id TEXT PRIMARY KEY, rid TEXT, raw BLOB, kind TEXT);
        CREATE TABLE IF NOT EXISTS audit (seq INTEGER PRIMARY KEY, at TEXT, event TEXT, detail TEXT);
        ''')
        # Retain our intent ID for reconciliation and the provider's actual ID for replies.
        if 'provider_rfcid' not in {r['name'] for r in self.db.execute('PRAGMA table_info(jobs)')}:
            self.db.execute('ALTER TABLE jobs ADD COLUMN provider_rfcid TEXT')

    def close(self):
        self.db.close()

    def log(self, event, detail):
        self.db.execute('INSERT INTO audit(at,event,detail) VALUES(?,?,?)',
                        (datetime.now(timezone.utc).isoformat(), event, detail))

    def initialize(self, config, approved_hash, approval_note):
        validate(config)
        if sha(canonical(config)) != approved_hash or not approval_note.strip():
            raise ValueError('Exact reviewed configuration hash and approval record required')
        with locked(self.folder):
            if self.db.execute('SELECT 1 FROM campaign').fetchone():
                raise ValueError('Campaign already exists; do not duplicate a live campaign')
            self.db.execute('BEGIN IMMEDIATE')
            try:
                self.db.execute('INSERT INTO campaign(id,config,digest,approval,started) VALUES(1,?,?,?,?)',
                                (canonical(config).decode(), approved_hash, approval_note, int(datetime.now(timezone.utc).timestamp())))
                self.db.executemany('INSERT INTO requests(id) VALUES(?)', [(r['id'],) for r in config['requests']])
                self.log('approval_recorded', approval_note)
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise

    def config(self):
        row = self.db.execute('SELECT * FROM campaign').fetchone()
        if not row or sha(row['config'].encode()) != row['digest']:
            raise ValueError('Missing or changed campaign approval')
        c = json.loads(row['config'])
        validate(c)
        return row, c

    def control(self, command, note):
        if not note.strip():
            raise ValueError('Operator note required')
        with locked(self.folder):
            _, c = self.config()
            if command == 'expand':
                replies = self.db.execute("SELECT id FROM incoming WHERE rid=? AND kind='reply'", (c['pilot_contact'],)).fetchall()
                if not any(all(self.db.execute("SELECT 1 FROM jobs WHERE key=? AND state='sent'",
                    (f'forward:{r["id"]}:{to}',)).fetchone() for to in c['forward_to']) for r in replies):
                    raise ValueError('Pilot needs an operator-confirmed reply and completed forwards before expansion')
                self.db.execute('UPDATE campaign SET expanded=1')
            else:
                self.db.execute('UPDATE campaign SET paused=?', (int(command == 'pause'),))
            self.log(command, note)

    def classify(self, mid, kind, note):
        if kind not in {'reply', 'stop', 'bounce', 'out_of_office', 'wrong_contact', 'fee', 'portal', 'unknown'} or not note.strip():
            raise ValueError('Classification and evidence note required')
        with locked(self.folder):
            r = self.db.execute('SELECT rid FROM incoming WHERE id=?', (mid,)).fetchone()
            if not r or not r['rid']:
                raise ValueError('A uniquely matched reply is required')
            self.db.execute('UPDATE incoming SET kind=? WHERE id=?', (kind, mid))
            state = 'suppressed' if kind in {'stop', 'bounce'} else 'held'
            self.db.execute("UPDATE requests SET state=?,due=NULL WHERE id=? AND state!='suppressed'", (state, r['rid']))
            self.log('classified_' + kind, mid + ': ' + note)

    def mime(self, c, recipient, subject, body, key, reference=None, original=None):
        msg = EmailMessage(policy=policy.SMTP)
        msg['From'], msg['To'], msg['Subject'] = c['sender'], recipient, subject
        msg['Date'] = format_datetime(datetime.now(timezone.utc))
        msg['Message-ID'] = '<salt-' + sha((c['campaign_id'] + key).encode()) + '@' + c['sender'].split('@')[1] + '>'
        if reference:
            msg['In-Reply-To'], msg['References'] = reference, reference
        msg.set_content(body)
        if original is not None:
            # Preserve exact bytes without extracting or executing any attachment.
            msg.add_attachment(original, maintype='application', subtype='octet-stream', filename='original-message.eml')
        return msg.as_bytes(), str(msg['Message-ID'])

    def enqueue(self, c, key, rid, kind, stage, to, subject, body, reference=None, original=None):
        raw, mid = self.mime(c, to, subject, body, key, reference, original)
        if len(raw) > 20_000_000:
            raise ValueError('Message exceeds local 20 MB limit; review manually')
        self.db.execute('INSERT OR IGNORE INTO jobs(key,rid,kind,stage,recipient,raw,rfcid,state) VALUES(?,?,?,?,?,?,?,?)',
                        (key, rid, kind, stage, to, raw, mid, 'pending'))

    def accepted(self, job, result, day):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            self.db.execute("UPDATE jobs SET state='sent',provider=?,thread=?,sent_day=? WHERE key=?",
                            (result['id'], result['threadId'], day, job['key']))
            if job['kind'] == 'request':
                _, c = self.config()
                self.db.execute("UPDATE requests SET stage=?,state=CASE WHEN state IN ('queued','waiting') THEN 'waiting' ELSE state END, due=CASE WHEN state IN ('queued','waiting') THEN ? ELSE NULL END,thread=? WHERE id=?",
                    (job['stage'] + 1, business_day(day, holidays=c['holidays']), result['threadId'], job['rid']))
            self.log('provider_accepted', job['key'])
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def reconcile(self, api, c):
        for job in self.db.execute("SELECT * FROM jobs WHERE state='uncertain'").fetchall():
            matches = api.messages('in:sent rfc822msgid:' + job['rfcid'])
            if len(matches) != 1:
                raise RuntimeError('Uncertain send unresolved; no automatic retry: ' + job['key'])
            result = api.get(matches[0]['id'], raw=True)
            msg = BytesParser(policy=policy.default).parsebytes(decode(result['raw']))
            expected = BytesParser(policy=policy.default).parsebytes(job['raw'])
            if (str(msg['Message-ID']) != job['rfcid'] or parseaddr(str(msg['To']))[1].lower() != job['recipient'].lower()
                or parseaddr(str(msg['From']))[1].lower() != c['sender'].lower()
                or str(msg['Subject']) != str(expected['Subject'])
                or 'SENT' not in result.get('labelIds', [])
                or mime_content(msg) != mime_content(expected)):
                raise RuntimeError('Sent-message reconciliation mismatch')
            day = datetime.fromtimestamp(int(result['internalDate']) / 1000, ZoneInfo(c['timezone'])).date().isoformat()
            self.accepted(job, result, day)

    def sync(self, api, campaign, c):
        # Gmail may rewrite Message-ID. Backfill from the accepted provider ID before
        # matching replies or constructing reminders. Failure blocks outbound work.
        for job in self.db.execute("SELECT * FROM jobs WHERE state='sent' AND kind='request' AND provider_rfcid IS NULL").fetchall():
            sent = api.get(job['provider'], raw=True)
            msg = BytesParser(policy=policy.default).parsebytes(decode(sent['raw']))
            expected = BytesParser(policy=policy.default).parsebytes(job['raw'])
            mid = str(msg.get('Message-ID', ''))
            if (sent.get('id') != job['provider'] or sent.get('threadId') != job['thread']
                or 'SENT' not in sent.get('labelIds', []) or not re.fullmatch(r'<[^<>\s]+>', mid)
                or parseaddr(str(msg['From']))[1].lower() != c['sender'].lower()
                or parseaddr(str(msg['To']))[1].lower() != job['recipient'].lower()
                or str(msg['Subject']) != str(expected['Subject']) or mime_content(msg) != mime_content(expected)):
                raise RuntimeError('Accepted message verification failed; outbound work blocked')
            self.db.execute('UPDATE jobs SET provider_rfcid=? WHERE key=?', (mid, job['key']))
            self.log('provider_reference_verified', job['key'])
        # Rescan overlapping campaign history, including spam/trash, to catch cross-thread
        # references and bounces. Only matched raw messages are persisted.
        jobs = self.db.execute("SELECT * FROM jobs WHERE state='sent' AND kind='request'").fetchall()
        for item in api.messages('after:' + str(campaign['started'] - 86400)):
            if self.db.execute('SELECT 1 FROM incoming WHERE id=?', (item['id'],)).fetchone():
                continue
            m = api.get(item['id'])
            if set(m.get('labelIds', [])) & {'SENT', 'DRAFT'}:
                continue
            headers = {h['name'].lower(): h['value'] for h in m.get('payload', {}).get('headers', [])}
            refs = re.findall(r'<[^<>]+>', headers.get('references', '') + ' ' + headers.get('in-reply-to', ''))
            candidates = {j['rid'] for j in jobs if j['thread'] == m.get('threadId') or j['rfcid'] in refs or j['provider_rfcid'] in refs}
            if not candidates:
                continue
            for rid in candidates:
                self.db.execute("UPDATE requests SET state=CASE WHEN state='suppressed' THEN state ELSE 'held' END,due=NULL WHERE id=?", (rid,))
            raw = decode(api.get(item['id'], raw=True)['raw'])
            if len(raw) > 14_000_000:
                raise RuntimeError('Matched reply too large for forwarding; requests held for manual review')
            rid = next(iter(candidates)) if len(candidates) == 1 else None
            self.db.execute('INSERT INTO incoming VALUES(?,?,?,?)', (item['id'], rid, raw, 'unknown'))
            self.log('reply_held' if rid else 'conflicting_reply', item['id'])
        # Durable inbox also repairs interruption between recording a reply and enqueueing forwards.
        for r in self.db.execute('SELECT * FROM incoming WHERE rid IS NOT NULL').fetchall():
            for to in c['forward_to']:
                key = f'forward:{r["id"]}:{to}'
                self.enqueue(c, key, r['rid'], 'forward', 0, to, 'Road-salt pilot reply: ' + r['rid'],
                             'A reply matched this procurement request. The original message is attached unchanged. Classification is pending operator review; attachments have not been processed.', original=r['raw'])

    def require_message_approval(self):
        self.db.execute('CREATE TABLE IF NOT EXISTS approval_policy(id INTEGER PRIMARY KEY)')
        self.db.execute('INSERT OR IGNORE INTO approval_policy VALUES(1)')

    def drafts(self):
        result = []
        for j in self.db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY key"):
            msg = BytesParser(policy=policy.default).parsebytes(j['raw'])
            body = msg.get_body(preferencelist=('plain',))
            result.append(dict(key=j['key'], digest=sha(j['raw']), kind=j['kind'], stage=j['stage'],
                recipient=j['recipient'], subject=str(msg['Subject']), body=body.get_content() if body else '',
                attachments=[dict(name=a.get_filename(), bytes=len(a.get_payload(decode=True) or b''),
                                  sha256=sha(a.get_payload(decode=True) or b'')) for a in msg.iter_attachments()]))
        return result

    def revise(self, key, digest, actor, subject=None, body=None, reject=False):
        with locked(self.folder):
            j = self.db.execute("SELECT * FROM jobs WHERE key=? AND state='pending'", (key,)).fetchone()
            if not j or sha(j['raw']) != digest:
                raise ValueError('Draft changed or is no longer awaiting approval. Refresh and review again.')
            if reject:
                self.db.execute("UPDATE jobs SET state='rejected' WHERE key=?", (key,))
            else:
                if (not isinstance(subject, str) or not subject.strip() or len(subject)>300
                    or any(c in subject for c in '\r\n') or not isinstance(body, str)
                    or not body.strip() or len(body)>12000):
                    raise ValueError('Provide a subject and message within the length limits.')
                msg = BytesParser(policy=policy.SMTP).parsebytes(j['raw'])
                msg.replace_header('Subject', subject)
                msg.get_body(preferencelist=('plain',)).set_content(body)
                self.db.execute('UPDATE jobs SET raw=? WHERE key=?', (msg.as_bytes(), key))
            self.log('draft_rejected' if reject else 'draft_edited', actor+': '+key)

    def tick(self, api, approved_key=None, approved_digest=None, actor=None):
        with locked(self.folder):
            campaign, c = self.config()
            if api.profile() != c['sender'].lower():
                raise ValueError('Authenticated mailbox differs from approved sender')
            self.reconcile(api, c)
            self.sync(api, campaign, c)
            gated = bool(self.db.execute("SELECT 1 FROM sqlite_master WHERE name='approval_policy'").fetchone())
            if campaign['paused'] and not gated:
                return
            day = datetime.now(ZoneInfo(c['timezone'])).date().isoformat()
            for r in self.db.execute("SELECT * FROM requests WHERE state IN ('queued','waiting')").fetchall():
                if not campaign['expanded'] and r['id'] != c['pilot_contact']:
                    continue
                if r['due'] and r['due'] > day:
                    continue
                if r['stage'] == 3:
                    self.db.execute("UPDATE requests SET state='closed_no_response',due=NULL WHERE id=?", (r['id'],))
                    continue
                request = next(x for x in c['requests'] if x['id'] == r['id'])
                original = self.db.execute("SELECT provider_rfcid FROM jobs WHERE rid=? AND kind='request' AND stage=0", (r['id'],)).fetchone()
                body = request['body'] if r['stage'] == 0 else c['followup_text'] + '\n\n' + request['body']
                self.enqueue(c, f'request:{r["id"]}:{r["stage"]}', r['id'], 'request', r['stage'], request['to'], request['subject'], body,
                             reference=original['provider_rfcid'] if original else None)
            if campaign['paused']:
                if approved_key:
                    raise ValueError('Campaign paused. Resume before approving a send.')
                return
            if gated and not approved_key:
                return
            if gated:
                chosen = self.db.execute("SELECT * FROM jobs WHERE key=? AND state='pending'", (approved_key,)).fetchone()
                if not chosen or sha(chosen['raw']) != approved_digest or not actor:
                    raise ValueError('Draft changed or was already handled. Refresh and review again.')
            for j in self.db.execute("SELECT * FROM jobs WHERE state='pending' ORDER BY key").fetchall():
                if gated and j['key'] != approved_key:
                    continue
                # Refresh again immediately before each send. A new reply cancels queued reminders.
                self.sync(api, campaign, c)
                r = self.db.execute('SELECT * FROM requests WHERE id=?', (j['rid'],)).fetchone()
                if j['kind'] == 'request' and r['state'] not in {'queued', 'waiting'}:
                    self.db.execute("UPDATE jobs SET state='cancelled' WHERE key=?", (j['key'],))
                    if gated:
                        raise ValueError('A reply or hold cancelled this request. Nothing was sent.')
                    continue
                if j['kind'] == 'request' and (r['stage'] != j['stage'] or (r['due'] and r['due'] > day)
                    or (not campaign['expanded'] and r['id'] != c['pilot_contact'])):
                    raise ValueError('Request is no longer eligible. Refresh the queue.')
                if gated:
                    self.log('message_approved', actor+': '+j['key']+': '+approved_digest)
                self.db.execute("UPDATE jobs SET state='uncertain' WHERE key=?", (j['key'],))
                self.log('send_attempt', j['key'])
                # Intent is committed before network I/O. Any exception/crash leaves it uncertain.
                result = api.send(j['raw'], r['thread'] if j['kind'] == 'request' else None)
                self.accepted(j, result, day)

    def report(self):
        return {'requests': [dict(r) for r in self.db.execute('SELECT * FROM requests')],
                'jobs': [dict(r) for r in self.db.execute('SELECT key,kind,state,provider,sent_day FROM jobs')],
                'incoming': [dict(r) for r in self.db.execute('SELECT id,rid,kind FROM incoming')],
                'audit': [dict(r) for r in self.db.execute('SELECT * FROM audit')]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['prepare', 'review', 'connect', 'init', 'tick', 'report', 'pause', 'resume', 'expand', 'classify'])
    p.add_argument('--run', type=Path, default=ROOT/'private/outreach-pilot')
    p.add_argument('--config', type=Path, default=ROOT/'private/outreach-proposal.json')
    p.add_argument('--registry', type=Path, default=ROOT/'data/outreach/milestone-9/contacts.json')
    p.add_argument('--approved-sha256'); p.add_argument('--note', default='')
    p.add_argument('--message'); p.add_argument('--kind')
    p.add_argument('--oauth-client', type=Path, help='Private Google Desktop OAuth client JSON; interactive login, no token saved')
    a = p.parse_args()
    if a.command == 'connect':
        c = json.loads(a.config.read_text(encoding='utf-8'))
        validate(c)
        if not a.oauth_client:
            p.error('connect requires --oauth-client')
        from google_auth_oauthlib.flow import InstalledAppFlow
        flow = InstalledAppFlow.from_client_secrets_file(str(a.oauth_client), scopes=[
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/gmail.send'])
        credentials = flow.run_local_server(port=0, open_browser=False, timeout_seconds=300, access_type='online')
        if Gmail(credentials.token).profile() != c['sender'].lower():
            raise ValueError('Authenticated mailbox differs from configured sender')
        print(json.dumps({'status': 'account_verified', 'sender': c['sender'],
                          'emails_sent': 0, 'token_saved': False}))
        return
    if a.command == 'prepare':
        a.config.parent.mkdir(parents=True, exist_ok=True)
        with a.config.open('x', encoding='utf-8') as f:
            json.dump(prepare(a.registry), f, indent=2, ensure_ascii=False)
        print('Proposal prepared; sender/forwarding recipients still require user input.')
        return
    if a.command == 'review':
        c = json.loads(a.config.read_text(encoding='utf-8'))
        print(json.dumps(c, indent=2, ensure_ascii=False))
        print('Configuration SHA256:', sha(canonical(c)))
        return
    app = Pilot(a.run)
    try:
        if a.command == 'init':
            app.initialize(json.loads(a.config.read_text(encoding='utf-8')), a.approved_sha256, a.note)
        elif a.command == 'tick':
            app.config()  # Do not prompt for account access before campaign approval exists.
            token = None
            if a.oauth_client:
                from google_auth_oauthlib.flow import InstalledAppFlow
                flow = InstalledAppFlow.from_client_secrets_file(str(a.oauth_client), scopes=[
                    'https://www.googleapis.com/auth/gmail.readonly',
                    'https://www.googleapis.com/auth/gmail.send'])
                token = flow.run_local_server(port=0, open_browser=False, timeout_seconds=600, access_type='online').token
            app.tick(Gmail(token))
        elif a.command in {'pause', 'resume', 'expand'}:
            app.control(a.command, a.note)
        elif a.command == 'classify':
            app.classify(a.message, a.kind, a.note)
        print(json.dumps(app.report(), indent=2))
    finally:
        app.close()


if __name__ == '__main__':
    main()
