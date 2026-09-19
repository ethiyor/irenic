import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

from cryptography.fernet import Fernet, InvalidToken
from outreach_console import initialize_demo, read_status
from outreach_hosted import create_app
from outreach_live import Pilot, canonical, sha


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        run = self.root/'original'
        initialize_demo(run)
        self.cfg = dict(origin='https://analyst.example', run=run, state=self.root/'auth',
            key=Fernet.generate_key(), demo=False, live_enabled=True, personal_workspaces=True,
            allowlist={'analyst@example.invalid':'owner', 'ryan@example.com':'approver', 'other@example.com':'approver'},
            google_client={'web':dict(client_id='test', client_secret='test',
                auth_uri='https://accounts.google.com/o/oauth2/auth', token_uri='https://oauth2.googleapis.com/token')})
        self.app = create_app(self.cfg)
        self.manager = self.app.extensions['outreach_workspaces']
        self.auth = self.app.extensions['outreach_service']
        self.client = self.app.test_client()
        self.ryan = self.manager.personal['ryan@example.com']
        self.other = self.manager.personal['other@example.com']
        self.login('ryan@example.com')

    def tearDown(self):
        self.tmp.cleanup()

    def login(self, email):
        sid, csrf = secrets.token_urlsafe(), secrets.token_urlsafe()
        with self.auth.db() as db:
            db.execute('INSERT INTO sessions VALUES(?,?,?,?)', (sha(sid.encode()), email, csrf, time.time()+1000))
        self.client.set_cookie('__Host-outreach', sid, domain='analyst.example')
        self.selected=self.manager.personal.get(email,'shared')
        self.headers = {'Origin':self.cfg['origin'], 'X-CSRF-Token':csrf,'X-Workspace':self.selected}

    def get(self, path, wid=None):
        return self.client.get(path, base_url=self.cfg['origin'], headers={'X-Workspace':wid or self.selected})

    def post(self, command, wid=None, **values):
        return self.client.post('/api/action', base_url=self.cfg['origin'],
            headers=self.headers | {'X-Workspace':wid or self.selected}, json=dict(command=command, **values))

    def contact(self, wid=None):
        return self.post('add_request', wid, organization='Test office', recipient='office@example.com',
            subject='Records request', body='Please provide road salt contract records.')

    def test_invited_default_shared_and_explicit_personal_workspace(self):
        default=self.client.get('/api/session',base_url=self.cfg['origin']).json
        self.assertEqual(default['workspace']['id'],'shared')
        self.assertEqual(default['role'],'approver')
        user = self.get('/api/session').json
        self.assertEqual(user['role'], 'owner')
        self.assertEqual(user['workspace']['id'], self.ryan)
        self.assertEqual({w['id'] for w in user['workspaces']}, {self.ryan,'shared'})
        self.assertEqual(self.get('/api/session','shared').json['role'], 'approver')
        status = self.get('/api/status').json
        self.assertEqual(status['sender'], 'ryan@example.com')
        self.assertEqual(status['requests'], [])
        self.assertEqual(status['incoming'], [])
        self.assertEqual(status['jobs'], [])
        self.assertTrue(status['paused'])
        self.assertFalse(status['service']['enabled'])
        self.assertFalse(status['service']['mailbox_connected'])

    def test_cross_workspace_access_and_admin_mutations_are_blocked(self):
        for path in ['/api/status','/api/session']:
            self.assertEqual(self.get(path,self.other).status_code,403)
            self.assertEqual(self.get(path,'../../shared').status_code,403)
        for command in ['pause','schedule_on','disconnect','add_request','prepare_drafts']:
            self.assertEqual(self.post(command,self.other,note='test').status_code,403)
            self.assertEqual(self.post(command,'shared',note='test').status_code,403)
        self.assertEqual(self.post('resume',note='My campaign').status_code,200)
        self.assertFalse(self.get('/api/status').json['paused'])
        self.assertTrue(read_status(self.manager.services[self.other].run)['paused'])

    def test_contact_creation_scoped_validated_and_idempotent(self):
        before = read_status(self.auth.run)
        self.assertEqual(self.contact().status_code,200)
        self.assertEqual(self.contact().status_code,400)
        self.assertEqual(len(self.get('/api/status').json['requests']),1)
        self.assertEqual(read_status(self.auth.run),before)
        self.assertEqual(read_status(self.manager.services[self.other].run)['requests'],[])
        self.assertEqual(self.post('add_request',organization='X',recipient='x@example.com',subject='bad\r\nBcc:x',body='x').status_code,400)
        self.post('resume',note='Testing pause requirement')
        self.assertEqual(self.contact().status_code,400)

    def test_shared_coowners_use_original_sender_and_keep_personal_isolation(self):
        self.cfg['allowlist'].update({'ryan@example.com':'owner','other@example.com':'owner'})
        for email in ('ryan@example.com','other@example.com'):
            self.login(email)
            self.assertEqual(self.get('/api/session','shared').json['role'],'owner')
            self.assertEqual(self.get('/api/status','shared').json['sender'],'analyst@example.invalid')
            self.assertEqual(self.post('pause','shared',note='Co-owner review').status_code,200)
            self.assertEqual(self.post('schedule_off','shared').status_code,200)
            self.assertEqual(self.get('/api/operations','shared').status_code,200)
            self.assertEqual(self.get('/api/status').json['sender'],email)
            other = self.other if email=='ryan@example.com' else self.ryan
            self.assertEqual(self.get('/api/status',other).status_code,403)

    def test_credentials_are_separate_encrypted_and_disconnect_is_scoped(self):
        a,b = self.manager.services[self.ryan], self.manager.services[self.other]
        credentials = SimpleNamespace(refresh_token='test-refresh', to_json=lambda:json.dumps({'token':'private-test'}))
        a.save_credentials(credentials)
        with a.db() as db:
            encrypted=db.execute('SELECT encrypted FROM credential').fetchone()[0]
        with self.assertRaises(InvalidToken):
            b.cipher.decrypt(encrypted)
        self.assertFalse(b.status()['mailbox_connected'])
        self.assertFalse(self.auth.status()['mailbox_connected'])
        self.assertEqual(self.post('schedule_on').status_code,200)
        self.assertFalse(b.status()['enabled'])
        self.assertEqual(self.post('disconnect').status_code,200)
        self.assertFalse(a.status()['mailbox_connected'])
        self.assertFalse(a.status()['enabled'])

    def test_oauth_is_bound_to_the_initiating_workspace(self):
        response=self.client.post('/api/connect',base_url=self.cfg['origin'],headers=self.headers,json={})
        self.assertEqual(response.status_code,200)
        state=parse_qs(urlsplit(response.json['url']).query)['state'][0]
        with self.auth.db() as db:
            pending=json.loads(db.execute('SELECT data FROM oauth').fetchone()[0])
        self.assertEqual(pending['workspace'],self.ryan)
        a,b=self.manager.services[self.ryan],self.manager.services[self.other]
        claims=dict(email='ryan@example.com',email_verified=True,nonce=pending['nonce'])
        credentials=Mock(token='test',id_token='test')
        credentials.has_scopes.return_value=True
        with patch('outreach_hosted.Flow.from_client_config',return_value=Mock(oauth2session=SimpleNamespace(token={}), credentials=credentials)), patch('outreach_hosted.id_token.verify_oauth2_token',return_value=claims), patch('outreach_hosted.Gmail',return_value=Mock(profile=lambda:'ryan@example.com')), patch.object(a,'save_credentials') as saved, patch.object(b,'save_credentials') as other, patch.object(self.auth,'save_credentials') as original:
            # Switching a header while consent is open cannot redirect credential storage.
            response=self.get('/oauth/callback?state='+state+'&code=test','shared')
            self.assertEqual(response.status_code,302)
            saved.assert_called_once()
            other.assert_not_called()
            original.assert_not_called()

    def test_oauth_exchange_failure_reports_safe_reference_only(self):
        response=self.client.post('/api/connect',base_url=self.cfg['origin'],headers=self.headers,json={})
        state=parse_qs(urlsplit(response.json['url']).query)['state'][0]
        flow=Mock()
        flow.fetch_token.side_effect=Warning('secret-token-and-authorization-code')
        with patch('outreach_hosted.Flow.from_client_config',return_value=flow), self.assertLogs(self.app.logger,level='WARNING') as logs:
            result=self.get('/oauth/callback?state='+state+'&code=test')
        self.assertEqual(result.status_code,403)
        self.assertIn(b'token_exchange',result.data)
        self.assertIn(b'Return to workspace',result.data)
        self.assertNotIn(b'secret-token',result.data)
        self.assertNotIn('secret-token',' '.join(logs.output))
        self.assertIn('type=Warning',' '.join(logs.output))
        self.assertFalse(self.manager.services[self.ryan].status()['mailbox_connected'])

    def test_wrong_oauth_account_cannot_store_credentials(self):
        response=self.client.post('/api/connect',base_url=self.cfg['origin'],headers=self.headers,json={})
        state=parse_qs(urlsplit(response.json['url']).query)['state'][0]
        with self.auth.db() as db:
            pending=json.loads(db.execute('SELECT data FROM oauth').fetchone()[0])
        claims=dict(email='other@example.com',email_verified=True,nonce=pending['nonce'])
        with patch('outreach_hosted.Flow.from_client_config',return_value=Mock(oauth2session=SimpleNamespace(token={}), credentials=Mock(id_token='test'))), patch('outreach_hosted.id_token.verify_oauth2_token',return_value=claims):
            self.assertEqual(self.get('/oauth/callback?state='+state+'&code=test').status_code,403)
        self.assertFalse(self.manager.services[self.other].status()['mailbox_connected'])

    def test_private_state_survives_restart_and_scheduler_visits_each_workspace(self):
        self.contact()
        restarted=create_app(self.cfg).extensions['outreach_workspaces']
        self.assertEqual(len(read_status(restarted.services[self.ryan].run)['requests']),1)
        mocks=[]
        for service in restarted.services.values():
            service.run_due=Mock()
            mocks.append(service.run_due)
        mocks[0].side_effect=ValueError('isolated failure')
        restarted.run_due()
        for mock in mocks:
            mock.assert_called_once()

    def test_other_workspace_draft_digest_cannot_be_approved(self):
        self.contact()
        a=self.manager.services[self.ryan]
        pilot=Pilot(a.run)
        try:
            _,config=pilot.config()
            request=config['requests'][0]
            pilot.enqueue(config,'request:CONTACT-1:0','CONTACT-1','request',0,request['to'],request['subject'],request['body'])
        finally:
            pilot.close()
        draft=a.drafts()[0]
        self.login('other@example.com')
        with patch.object(self.manager.services[self.other], 'gmail') as gmail:
            self.assertEqual(self.post('reject_draft',key=draft['key'],digest=draft['digest']).status_code,400)
            self.assertEqual(self.post('approve_send',self.ryan,key=draft['key'],digest=draft['digest']).status_code,403)
            gmail.assert_not_called()
        self.assertEqual(len(a.drafts()),1)


if __name__ == '__main__':
    unittest.main()
