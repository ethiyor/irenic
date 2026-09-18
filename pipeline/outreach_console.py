"""Local analyst console. Live status/review controls; isolated simulated mail demo.

Never exposes a live send, expansion, or credential endpoint. Bind only to loopback.
"""
import argparse
import base64
from contextlib import closing
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sqlite3

from outreach_live import Pilot, ROOT, canonical, prepare, sha
from outreach_fixture import Provider


def initialize_demo(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    if (folder/'live.sqlite3').exists():
        raise ValueError('Use a new demo directory; existing databases are never overwritten')
    c = prepare(ROOT/'data/outreach/milestone-9/contacts.json')
    c.update(campaign_id='console-demo', sender='analyst@example.invalid',
             forward_to=['reviewer@example.invalid'])
    for i, request in enumerate(c['requests']):
        request['to'] = f'office{i}@example.invalid'
    app = Pilot(folder)
    try:
        app.initialize(c, sha(canonical(c)), 'SIMULATION ONLY; no authorization for real mail')
    finally:
        app.close()
    (folder/'console-demo.json').write_text(json.dumps({'mode': 'simulation', 'rows': {}, 'sends': []}))


def read_status(folder, demo=False):
    path = Path(folder)/'live.sqlite3'
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('BEGIN')
        row = db.execute('SELECT * FROM campaign WHERE id=1').fetchone()
        if not row or sha(row['config'].encode()) != row['digest']:
            raise ValueError('Missing or changed campaign approval')
        config = json.loads(row['config'])
        contacts = {r['id']: r for r in config['requests']}
        requests = []
        for r in db.execute('SELECT * FROM requests ORDER BY id'):
            r = dict(r)
            r.update(contacts[r['id']])
            r['enabled'] = bool(row['expanded'] or r['id'] == config['pilot_contact'])
            requests.append(r)
        incoming = []
        for r in db.execute('SELECT * FROM incoming'):
            msg = BytesParser(policy=policy.default).parsebytes(r['raw'])
            body = msg.get_body(preferencelist=('plain',))
            try:
                preview = body.get_content()[:12000] if body else 'No plain-text body. Review the original in Gmail.'
            except (LookupError, ValueError):
                preview = 'Cannot decode body. Review the original in Gmail.'
            incoming.append({'id': r['id'], 'rid': r['rid'], 'kind': r['kind'],
                             'from': str(msg.get('From', '')), 'subject': str(msg.get('Subject', '')),
                             'preview': preview})
        return {'mode': 'SIMULATION' if demo else 'LIVE LEDGER', 'paused': bool(row['paused']),
                'expanded': bool(row['expanded']), 'sender': config['sender'], 'forward_to': config['forward_to'],
                'requests': requests, 'incoming': incoming,
                'jobs': [dict(r) for r in db.execute('SELECT key,kind,state,provider,sent_day FROM jobs')],
                'audit': [dict(r) for r in db.execute('SELECT * FROM audit ORDER BY seq DESC LIMIT 50')],
                'scheduler': 'Not configured', 'live_reply_validation': 'Pending; demo results are not live evidence'}


def action(folder, demo, data):
    command = data.get('command')
    allowed = {'pause', 'resume', 'classify'} | ({'demo_tick', 'demo_reply'} if demo else set())
    if command not in allowed:
        raise ValueError('Action unavailable in this mode')
    if not (Path(folder)/'live.sqlite3').is_file():
        raise ValueError('Campaign not found')
    app = Pilot(folder)
    try:
        if command in {'pause', 'resume'}:
            app.control(command, data.get('note', ''))
        elif command == 'classify':
            app.classify(data.get('message'), data.get('kind'), data.get('note', ''))
        else:
            fixture = Path(folder)/'console-demo.json'
            saved = json.loads(fixture.read_text())
            _, config = app.config()
            if (saved.get('mode') != 'simulation' or config['campaign_id'] != 'console-demo'
                or any(not a.endswith('@example.invalid') for a in
                       [config['sender'], *config['forward_to'], *[r['to'] for r in config['requests']]])):
                raise ValueError('Not an isolated demo campaign')
            api = Provider()
            api.account = config['sender']
            api.rows = saved['rows']
            for r in api.rows.values():
                r['_raw'] = base64.urlsafe_b64decode(r['raw'])
            api.sends = saved['sends']
            if command == 'demo_reply':
                if not api.sends or app.report()['incoming'] or any('INBOX' in r['labelIds'] for r in api.rows.values()):
                    raise ValueError('Run the demo once first; only one sample reply is available')
                api.reply(body='SIMULATED REPLY — not a real county response.\n\nThank you for your request. Our office purchases through a cooperative. Please review the purchasing arrangement before combining volumes.\n\nNo source documents are attached to this demonstration.')
            else:
                app.tick(api, data.get('approved_key'), data.get('approved_digest'), data.get('actor'))
            saved.update(rows={k: {n: v for n, v in r.items() if n != '_raw'} for k, r in api.rows.items()}, sends=api.sends)
            tmp = fixture.with_suffix('.tmp')
            tmp.write_text(json.dumps(saved))
            tmp.replace(fixture)
    finally:
        app.close()


class Console(HTTPServer):
    def __init__(self, folder, demo=False, port=8766):
        self.folder, self.demo, self.token = Path(folder), demo, secrets.token_urlsafe(32)
        read_status(self.folder, demo)  # Fail before starting if the ledger is absent/invalid.
        super().__init__(('127.0.0.1', port), Handler)
        self.origin = f'http://127.0.0.1:{self.server_port}'


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # No request/credential logging.

    def respond(self, code, value, html=False):
        raw = value.encode() if html else json.dumps(value).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'text/html; charset=utf-8' if html else 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'none'; script-src 'nonce-console'; style-src 'nonce-console'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
        self.end_headers()
        self.wfile.write(raw)

    def authorized(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            return False
        if self.headers.get('Origin') not in {None, self.server.origin}:
            return False
        return secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer '+self.server.token)

    def do_GET(self):
        if self.headers.get('Host') != self.server.origin.removeprefix('http://'):
            return self.respond(403, {'error': 'Invalid host'})
        if self.path == '/':
            return self.respond(200, (ROOT/'pipeline/outreach_console.html').read_text(encoding='utf-8'), html=True)
        if not self.authorized():
            return self.respond(403, {'error': 'Open the private link printed by the launcher'})
        if self.path != '/api/status':
            return self.respond(404, {'error': 'Not found'})
        try:
            self.respond(200, read_status(self.server.folder, self.server.demo))
        except Exception:
            self.respond(503, {'error': 'Ledger unavailable. Inspect the local worker before continuing.'})

    def do_POST(self):
        if not self.authorized():
            return self.respond(403, {'error': 'Unauthorized request'})
        if self.path != '/api/action':
            return self.respond(404, {'error': 'Not found'})
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 8192 or self.headers.get('Content-Type') != 'application/json':
                raise ValueError('Small JSON request required')
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise ValueError('JSON object required')
            action(self.server.folder, self.server.demo, data)
            self.respond(200, {'ok': True})
        except (ValueError, TypeError, KeyError) as exc:
            self.respond(400, {'error': str(exc)})
        except FileExistsError:
            self.respond(409, {'error': 'Worker busy. Refresh and try again.'})
        except Exception:
            self.respond(503, {'error': 'Operation stopped. Review local worker state before retrying.'})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, required=True)
    parser.add_argument('--demo', action='store_true')
    parser.add_argument('--init-demo', action='store_true')
    parser.add_argument('--port', type=int, default=8766)
    a = parser.parse_args()
    if a.init_demo:
        if not a.demo:
            parser.error('--init-demo requires --demo')
        initialize_demo(a.run)
    if a.demo and not (a.run/'console-demo.json').is_file():
        parser.error('Demo marker missing; use a separate new directory with --init-demo')
    server = Console(a.run, a.demo, a.port)
    print('Private local console (do not share this link): '+server.origin+'/#'+server.token, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
