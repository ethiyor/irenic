import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import test_workspaces as fixtures
from outreach_hosted import create_app
from outreach_live import sha


class PublicSignupTests(unittest.TestCase):
    def setUp(self):
        self.fixture=fixtures.WorkspaceTests()
        self.fixture.setUp()
        self.cfg=self.fixture.cfg | {'open_signup':True}
        self.app=create_app(self.cfg)
        self.manager=self.app.extensions['outreach_workspaces']
        self.auth=self.app.extensions['outreach_service']
        self.client=self.app.test_client()

    def tearDown(self):
        self.fixture.tearDown()

    def get(self,path,**kwargs):
        return self.client.get(path,base_url=self.cfg['origin'],**kwargs)

    def google_login(self,email='new@example.com',verified=True,nonce_ok=True):
        response=self.get('/login')
        query=parse_qs(urlsplit(response.location).query)
        self.assertEqual(set(query['scope'][0].split()),{'openid','https://www.googleapis.com/auth/userinfo.email'})
        state=query['state'][0]
        with self.auth.db() as db:
            pending=json.loads(db.execute('SELECT data FROM oauth WHERE state=?',(sha(state.encode()),)).fetchone()[0])
        claims=dict(email=email,email_verified=verified,nonce=pending['nonce'] if nonce_ok else 'wrong')
        with patch('outreach_hosted.Flow.from_client_config',return_value=Mock(oauth2session=SimpleNamespace(token={}), credentials=SimpleNamespace(id_token='test'))), patch('outreach_hosted.id_token.verify_oauth2_token',return_value=claims):
            return self.get('/oauth/callback?state='+state+'&code=test')

    def test_new_google_identity_gets_only_empty_private_workspace(self):
        self.assertEqual(self.google_login().status_code,302)
        session=self.get('/api/session').json
        self.assertEqual(session['role'],'owner')
        self.assertEqual(len(session['workspaces']),1)
        self.assertNotEqual(session['workspace']['id'],'shared')
        self.assertEqual(self.get('/api/status',headers={'X-Workspace':'shared'}).status_code,403)
        status=self.get('/api/status').json
        self.assertEqual(status['sender'],'new@example.com')
        self.assertEqual(status['requests'],[])
        self.assertFalse(status['service']['mailbox_connected'])
        self.assertFalse(status['service']['enabled'])

    def test_public_user_can_connect_own_mail_without_allowlist_entry(self):
        self.assertEqual(self.google_login().status_code,302)
        session=self.get('/api/session').json
        response=self.client.post('/api/connect',base_url=self.cfg['origin'],headers={
            'Origin':self.cfg['origin'],'X-CSRF-Token':session['csrf'],
            'X-Workspace':session['workspace']['id']},json={})
        self.assertEqual(response.status_code,200)
        state=parse_qs(urlsplit(response.json['url']).query)['state'][0]
        with self.auth.db() as db:
            pending=json.loads(db.execute('SELECT data FROM oauth WHERE state=?',(sha(state.encode()),)).fetchone()[0])
        claims=dict(email='new@example.com',email_verified=True,nonce=pending['nonce'])
        credentials=Mock(token='test',id_token='test',refresh_token='test')
        credentials.has_scopes.return_value=True
        personal=self.manager.services[session['workspace']['id']]
        with patch('outreach_hosted.Flow.from_client_config',return_value=Mock(oauth2session=SimpleNamespace(token={}),credentials=credentials)), patch('outreach_hosted.id_token.verify_oauth2_token',return_value=claims), patch('outreach_hosted.Gmail',return_value=Mock(profile=lambda:'new@example.com')), patch.object(personal,'save_credentials') as saved, patch.object(self.auth,'save_credentials') as shared:
            self.assertEqual(self.get('/oauth/callback?state='+state+'&code=test').status_code,302)
            saved.assert_called_once()
            shared.assert_not_called()

    def test_unverified_identity_or_wrong_nonce_creates_nothing(self):
        for verified,nonce in [(False,True),('true',True),(True,False)]:
            self.assertEqual(self.google_login(verified=verified,nonce_ok=nonce).status_code,403)
        with self.auth.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_accounts').fetchone()[0],0)

    def test_unknown_account_still_rejected_when_signup_disabled(self):
        self.cfg['open_signup']=False
        self.assertEqual(self.google_login().status_code,403)

    def test_registration_and_access_persist_after_restart(self):
        self.google_login()
        restarted=create_app(self.cfg).extensions['outreach_workspaces']
        self.assertEqual(restarted.select('new@example.com')[0]['role'],'owner')
        self.assertEqual(len(restarted.choices('new@example.com')),1)

    def test_disabled_account_cannot_re_register_or_continue_session(self):
        self.google_login()
        with self.auth.db() as db:
            db.execute('UPDATE workspace_accounts SET active=0 WHERE email=?',('new@example.com',))
        self.assertEqual(self.get('/api/status').status_code,401)
        self.assertEqual(self.google_login().status_code,403)

    def test_capacity_limits_do_not_grant_shared_access(self):
        self.cfg['workspace_limit']=0
        self.assertEqual(self.google_login().status_code,403)
        self.assertEqual(self.get('/api/status').status_code,401)
        self.assertNotIn('new@example.com',self.cfg['allowlist'])

    def test_concurrent_registration_creates_one_workspace(self):
        with ThreadPoolExecutor(max_workers=3) as pool:
            list(pool.map(self.manager.register_verified,['new@example.com']*3))
        with self.auth.db() as db:
            self.assertEqual(db.execute('SELECT count(*) FROM workspace_accounts').fetchone()[0],1)
        self.assertEqual(len(self.manager.choices('new@example.com')),1)

    def test_privacy_is_public_without_authentication(self):
        response=self.get('/privacy')
        self.assertEqual(response.status_code,200)
        self.assertIn(b'Connecting Gmail is a separate',response.data)
        self.assertEqual(self.get('/api/status').status_code,401)


if __name__=='__main__':
    unittest.main()
