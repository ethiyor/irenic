import json
import unittest
from unittest.mock import Mock, patch
from google_auth_oauthlib.flow import Flow
from outreach_hosted import fetch_google_token, MissingGoogleScopes, IDENTITY, MAIL

class GoogleScopeTests(unittest.TestCase):
    def flow(self):
        return Flow.from_client_config({'web': {'client_id':'test', 'client_secret':'test',
            'auth_uri':'https://accounts.google.com/o/oauth2/auth',
            'token_uri':'https://oauth2.googleapis.com/token'}}, scopes=IDENTITY+MAIL,
            redirect_uri='https://analyst.example/oauth/callback')

    def exchange(self, scopes):
        flow=self.flow()
        payload={'access_token':'fixture-token','token_type':'Bearer','expires_in':3600,
                 'refresh_token':'fixture-refresh','id_token':'fixture-id','scope':' '.join(scopes)}
        response=Mock(text=json.dumps(payload))
        with patch.object(flow.oauth2session, 'request', return_value=response) as request:
            fetch_google_token(flow,'fixture-code',IDENTITY+MAIL)
            self.assertEqual(request.call_count,1)
        return flow

    def test_extra_previously_granted_scope_is_accepted(self):
        flow=self.exchange(IDENTITY+MAIL+['https://www.googleapis.com/auth/userinfo.profile'])
        self.assertEqual(flow.credentials.refresh_token,'fixture-refresh')
        self.assertTrue(flow.credentials.has_scopes(MAIL))

    def test_google_email_alias_is_accepted(self):
        self.assertEqual(self.exchange(['openid','email']+MAIL).credentials.token,'fixture-token')

    def test_missing_gmail_permission_is_rejected(self):
        for granted in [IDENTITY, IDENTITY+MAIL[:1], IDENTITY+MAIL[1:]]:
            with self.subTest(granted=granted), self.assertRaises(MissingGoogleScopes):
                self.exchange(granted)

    def test_exact_requested_scopes_work(self):
        self.assertEqual(self.exchange(IDENTITY+MAIL).credentials.token,'fixture-token')

    def test_oauth_errors_are_not_recovered(self):
        flow=self.flow()
        with patch.object(flow.oauth2session,'request',return_value=Mock(text=json.dumps({'error':'invalid_grant'}))):
            with self.assertRaises(Exception) as caught:
                fetch_google_token(flow,'fixture-code',IDENTITY+MAIL)
        self.assertEqual(type(caught.exception).__name__,'InvalidGrantError')
        self.assertFalse(flow.oauth2session.token)
