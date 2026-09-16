import json
from pathlib import Path
import secrets
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from cryptography.fernet import Fernet
from outreach_console import initialize_demo, read_status
from outreach_hosted import create_app, environment
from outreach_live import locked, sha
from outreach_service import Service


class HostedTests(unittest.TestCase):
    def test_missing_configuration_is_actionable_without_secret_values(self):
        with patch.dict('os.environ', {'OUTREACH_ENCRYPTION_KEY': 'never-print-this-secret'}, clear=True):
            with self.assertRaises(ValueError) as error:
                environment()
        self.assertIn('OUTREACH_ORIGIN', str(error.exception))
        self.assertIn('OUTREACH_GOOGLE_WEB_CLIENT', str(error.exception))
        self.assertNotIn('never-print-this-secret', str(error.exception))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run = self.root/'demo'
        initialize_demo(self.run)
        self.cfg = dict(origin='https://analyst.example', run=self.run, state=self.root/'service',
                        key=Fernet.generate_key(), demo=True, live_enabled=False,
                        allowlist={'owner@example.com': 'owner', 'reviewer@example.com': 'reviewer'},
                        google_client={'web': {'client_id': 'test-client', 'client_secret': 'test-secret',
                         'auth_uri': 'https://accounts.google.com/o/oauth2/auth', 'token_uri': 'https://oauth2.googleapis.com/token'}})
        self.app = create_app(self.cfg)
        self.service = self.app.extensions['outreach_service']
        self.client = self.app.test_client()

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, email='owner@example.com', expires=None):
        sid, csrf = secrets.token_urlsafe(), secrets.token_urlsafe()
        with self.service.db() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (sha(sid.encode()), email, csrf, expires or time.time()+1000))
        self.client.set_cookie('__Host-outreach', sid, domain='analyst.example')
        return {'X-CSRF-Token': csrf, 'Origin': self.cfg['origin']}

    def get(self, path):
        return self.client.get(path, base_url=self.cfg['origin'])

    def post(self, command, headers, **kw):
        return self.client.post('/api/action', base_url=self.cfg['origin'], headers=headers,
                                json={'command': command, **kw})

    def due(self):
        with self.service.db() as db:
            db.execute('UPDATE settings SET next_run=0 WHERE id=1')

    def test_access_csrf_roles_and_session_expiry(self):
        self.assertEqual(self.get('/api/status').status_code, 401)
        h = self.login()
        self.assertEqual(self.get('/api/status').status_code, 200)
        self.assertEqual(self.post('pause', {}, note='x').status_code, 403)
        self.assertEqual(self.post('pause', h | {'Origin': 'https://evil.example'}, note='x').status_code, 403)
        self.assertEqual(self.post('pause', h, note='Analyst pause').status_code, 200)
        h = self.login('reviewer@example.com')
        self.assertEqual(self.post('resume', h, note='x').status_code, 403)
        self.assertEqual(self.post('schedule_on', h).status_code, 403)
        self.login(expires=1)
        self.assertEqual(self.get('/api/status').status_code, 401)

    def test_scheduler_pause_restart_reply_and_no_duplicate(self):
        self.assertEqual(self.service.run_due(), 'not_due')
        h = self.login()
        self.assertEqual(self.post('schedule_on', h).status_code, 200)
        self.assertEqual(self.post('pause', h, note='Pause for validation').status_code, 200)
        self.due()
        self.assertEqual(self.service.run_due(), 'succeeded')
        self.assertEqual(read_status(self.run)['jobs'], [])
        self.post('resume', h, note='Resume demo')
        self.due()
        self.service.run_due()
        self.assertEqual(len(read_status(self.run)['jobs']), 1)
        self.post('demo_reply', h)
        self.service = create_app(self.cfg).extensions['outreach_service']
        self.due()
        self.service.run_due()
        self.due()
        self.service.run_due()
        self.assertEqual(len(read_status(self.run)['jobs']), 2)
        self.assertIsNotNone(self.service.status()['last_success'])
        self.assertEqual(sum(r['enabled'] for r in read_status(self.run)['requests']), 1)

    def test_overlap_and_crash_stop(self):
        self.service.set_schedule(True, 'owner')
        self.due()
        with locked(self.service.folder):
            self.assertEqual(self.service.run_due(), 'busy')
        with self.service.db() as db:
            db.execute('UPDATE settings SET active=1')
        self.assertEqual(self.service.run_due(), 'interrupted')
        self.assertFalse(self.service.status()['enabled'])
        self.assertEqual(read_status(self.run)['jobs'], [])

    def test_failure_disables_and_does_not_expose_provider_details(self):
        self.service.set_schedule(True, 'owner')
        self.due()
        with patch('outreach_service.action', side_effect=RuntimeError('SECRET_TOKEN')):
            self.assertEqual(self.service.run_due(), 'failed')
        s = self.service.status()
        self.assertFalse(s['enabled'])
        self.assertNotIn('SECRET_TOKEN', json.dumps(s))
        self.assertEqual(self.service.run_due(), 'not_due')

    def test_credentials_encrypted_and_disconnect_disables(self):
        credentials = SimpleNamespace(refresh_token='sensitive-refresh', to_json=lambda: json.dumps({'refresh_token': 'sensitive-refresh'}))
        self.service.save_credentials(credentials)
        with self.service.db() as db:
            encrypted = db.execute('SELECT encrypted FROM credential').fetchone()[0]
        self.assertNotIn(b'sensitive-refresh', encrypted)
        self.assertIn(b'sensitive-refresh', self.service.cipher.decrypt(encrypted))
        self.service.set_schedule(True, 'owner')
        self.service.disconnect('owner')
        self.assertFalse(self.service.status()['mailbox_connected'])
        self.assertFalse(self.service.status()['enabled'])

    def test_live_gate_cannot_be_enabled_from_ui(self):
        cfg = self.cfg | {'demo': False, 'state': self.root/'live-service'}
        live = create_app(cfg).extensions['outreach_service']
        with self.assertRaises(ValueError):
            live.set_schedule(True, 'owner')
        self.assertEqual(read_status(self.run)['jobs'], [])

    def test_oauth_state_binding_nonce_and_replay(self):
        response = self.get('/login')
        self.assertEqual(response.status_code, 302)
        self.assertIn('code_challenge=', response.location)
        from urllib.parse import parse_qs, urlsplit
        state = parse_qs(urlsplit(response.location).query)['state'][0]
        with self.service.db() as db:
            pending = json.loads(db.execute('SELECT data FROM oauth').fetchone()[0])
        flow = Mock(credentials=SimpleNamespace(id_token='signed-token'))
        claims = {'email': 'owner@example.com', 'email_verified': True, 'nonce': pending['nonce']}
        with patch('outreach_hosted.Flow.from_client_config', return_value=flow), patch('outreach_hosted.id_token.verify_oauth2_token', return_value=claims):
            self.assertEqual(self.get('/oauth/callback?state=wrong&code=test').status_code, 400)
            self.assertEqual(self.get('/oauth/callback?state='+state+'&code=test').status_code, 302)
            self.assertEqual(self.get('/api/session').json['email'], 'owner@example.com')
            self.assertEqual(self.get('/oauth/callback?state='+state+'&code=test').status_code, 400)
        flow.fetch_token.assert_called_once()

    def test_no_unknown_account_or_wrong_nonce(self):
        from urllib.parse import parse_qs, urlsplit
        for email, nonce in [('outsider@example.com', 'valid'), ('owner@example.com', 'wrong')]:
            response = self.get('/login')
            state = parse_qs(urlsplit(response.location).query)['state'][0]
            with self.service.db() as db:
                pending = json.loads(db.execute('SELECT data FROM oauth WHERE state=?', (sha(state.encode()),)).fetchone()[0])
            claims = {'email': email, 'email_verified': True, 'nonce': pending['nonce'] if nonce == 'valid' else nonce}
            with patch('outreach_hosted.Flow.from_client_config', return_value=Mock(credentials=SimpleNamespace(id_token='token'))), patch('outreach_hosted.id_token.verify_oauth2_token', return_value=claims):
                self.assertEqual(self.get('/oauth/callback?state='+state+'&code=test').status_code, 403)
            self.assertEqual(self.get('/api/status').status_code, 401)


if __name__ == '__main__':
    unittest.main()
