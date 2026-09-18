import json
import unittest
from unittest.mock import patch, Mock
from email import policy
from email.parser import BytesParser

from outreach_email_login import GENERIC
from outreach_hosted import create_app
import test_outreach_hosted as hosted_tests


class EmailLoginTests(unittest.TestCase):
    tearDown = hosted_tests.HostedTests.tearDown
    login = hosted_tests.HostedTests.login
    get = hosted_tests.HostedTests.get
    post = hosted_tests.HostedTests.post

    def setUp(self):
        hosted_tests.HostedTests.setUp(self)
        self.cfg['allowlist']['approver@example.com'] = 'approver'

    def guest(self, client=None):
        client = client or self.client
        response = client.get('/auth/session', base_url=self.cfg['origin'])
        self.assertEqual(response.status_code, 200)
        return {'Origin': self.cfg['origin'], 'X-CSRF-Token': response.json['csrf']}

    def auth(self, path, headers, client=None, **data):
        return (client or self.client).post('/auth/'+path, base_url=self.cfg['origin'], headers=headers, json=data)

    def issue(self, headers, email='approver@example.com'):
        with patch.object(self.service, 'send_signin_link') as send:
            response = self.auth('request-link', headers, email=email)
        self.assertEqual(response.json['message'], GENERIC)
        send.assert_called_once()
        self.assertEqual(send.call_args.args[0], email)
        return send.call_args.args[1].split('#access=')[1]

    def test_approver_can_approve_reject_but_not_administer_or_edit(self):
        h = self.login()
        self.post('prepare_drafts', h)
        draft = self.service.drafts()[0]
        h = self.login('approver@example.com')
        self.assertEqual(self.get('/api/status').status_code, 200)
        for command in ('pause','resume','schedule_on','schedule_off','disconnect','prepare_drafts','edit_draft','demo_reply'):
            self.assertEqual(self.post(command, h, key=draft['key'], digest=draft['digest'], note='test').status_code, 403)
        self.assertEqual(self.client.post('/api/connect', base_url=self.cfg['origin'], headers=h, json={}).status_code, 403)
        self.assertEqual(self.post('approve_send', h, key=draft['key'], digest=draft['digest']).status_code, 200)
        self.assertEqual(self.post('approve_send', h, key=draft['key'], digest=draft['digest']).status_code, 400)
        owner = self.login()
        self.post('demo_reply', owner)
        self.post('prepare_drafts', owner)
        forward = self.service.drafts()[0]
        h = self.login('approver@example.com')
        self.assertEqual(self.post('reject_draft', h, key=forward['key'], digest=forward['digest']).status_code, 200)
        audit = self.get('/api/status').json['audit']
        self.assertTrue(any(a['event']=='message_approved' and a['detail'].startswith('approver@example.com:') for a in audit))

    def test_link_signin_single_use_hashed_and_no_mailbox_grant(self):
        h = self.guest()
        token = self.issue(h)
        self.assertNotIn(token.encode(), (self.service.folder/'service.sqlite3').read_bytes())
        result = self.auth('consume-link', h, token=token)
        self.assertEqual(result.status_code, 200)
        user = self.get('/api/session').json
        self.assertEqual(user['email'], 'approver@example.com')
        self.assertEqual(user['role'], 'approver')
        self.assertFalse(self.service.status()['mailbox_connected'])
        h = {'Origin': self.cfg['origin'], 'X-CSRF-Token': user['csrf']}
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 400)

    def test_other_browser_and_cross_origin_cannot_redeem_or_request(self):
        h = self.guest()
        token = self.issue(h)
        other = self.app.test_client()
        oh = self.guest(other)
        self.assertEqual(self.auth('consume-link', oh, client=other, token=token).status_code, 400)
        self.assertEqual(self.auth('consume-link', h | {'Origin':'https://evil.example'}, token=token).status_code, 403)
        self.assertEqual(self.auth('request-link', {}, email='approver@example.com').status_code, 403)
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 200)

    def test_unknown_and_owner_addresses_cannot_get_email_access(self):
        h = self.guest()
        with patch.object(self.service, 'send_signin_link') as send:
            for address in ('unknown@example.com','owner@example.com'):
                self.assertEqual(self.auth('request-link', h, email=address).json['message'], GENERIC)
            send.assert_not_called()
        self.assertEqual(self.get('/api/status').status_code, 401)

    def test_expired_removed_and_rate_limited_links(self):
        h = self.guest()
        token = self.issue(h)
        with patch.object(self.service, 'send_signin_link') as send:
            self.assertEqual(self.auth('request-link', h, email='approver@example.com').json['message'], GENERIC)
            send.assert_not_called()
        with self.service.db() as db:
            db.execute('UPDATE email_login SET expires=1')
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 400)
        with self.service.db() as db:
            db.execute('UPDATE email_login SET expires=9999999999')
        del self.cfg['allowlist']['approver@example.com']
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 400)

    def test_restart_preserves_binding_and_revocation(self):
        h = self.guest()
        token = self.issue(h)
        # A second app process uses the same durable token and session state.
        create_app(self.cfg)
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 200)
        self.cfg['allowlist'].pop('approver@example.com')
        self.assertEqual(self.get('/api/status').status_code, 401)

    def test_uncertain_auth_email_is_not_retried_and_token_is_revoked(self):
        h = self.guest()
        with patch.object(self.service, 'send_signin_link', side_effect=RuntimeError('provider failure')) as send:
            self.assertEqual(self.auth('request-link', h, email='approver@example.com').json['message'], GENERIC)
            token = send.call_args.args[1].split('#access=')[1]
            send.assert_called_once()
        self.assertEqual(self.auth('consume-link', h, token=token).status_code, 400)

    def test_authentication_email_uses_approved_sender_without_procurement_jobs(self):
        self.service.demo = False
        self.service.live_enabled = True
        api = Mock()
        api.profile.return_value = 'analyst@example.invalid'
        with patch.object(self.service, 'gmail', return_value=api):
            self.service.send_signin_link('approver@example.com', 'https://analyst.example/#access=fake')
        api.send.assert_called_once()
        msg = BytesParser(policy=policy.default).parsebytes(api.send.call_args.args[0])
        self.assertEqual(str(msg['To']), 'approver@example.com')
        self.assertEqual(str(msg['From']), 'analyst@example.invalid')
        self.assertIn('same browser', msg.get_content())
        self.assertEqual(self.service.drafts(), [])


if __name__ == '__main__':
    unittest.main()
