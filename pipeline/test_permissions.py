"""Role regression tests use disposable ledgers and fake mail only."""
import unittest
from unittest.mock import patch
import test_outreach_hosted as fixtures
import test_workspaces as workspace_fixtures


class PermissionTests(unittest.TestCase):
    setUp = fixtures.HostedTests.setUp
    tearDown = fixtures.HostedTests.tearDown
    login = fixtures.HostedTests.login
    get = fixtures.HostedTests.get
    post = fixtures.HostedTests.post

    def reply(self):
        h = self.login()
        self.post('prepare_drafts', h)
        d = self.service.drafts()[0]
        self.post('approve_send', h, key=d['key'], digest=d['digest'])
        self.post('demo_reply', h)
        self.post('prepare_drafts', h)
        return self.get('/api/status').json['incoming'][0]['id']

    def test_reviewer_cannot_classify_real_fixture_reply(self):
        mid = self.reply()
        before = self.get('/api/status').json
        h = self.login('reviewer@example.com')
        self.assertEqual(self.post('classify', h, message=mid, kind='stop', note='Not permitted').status_code, 403)
        after = self.get('/api/status').json
        for key in ('incoming', 'requests', 'audit'):
            self.assertEqual(before[key], after[key])

    def test_complete_role_matrix_and_unknown_commands(self):
        self.cfg['allowlist']['approver@example.com'] = 'approver'
        commands = ['add_request', 'prepare_drafts', 'approve_send', 'edit_draft', 'reject_draft',
                    'schedule_on', 'schedule_off', 'disconnect', 'pause', 'resume', 'classify', 'demo_reply']
        for role in ['reviewer', 'approver', 'owner']:
            h = self.login(role+'@example.com')
            self.assertEqual(self.get('/api/status').status_code, 200)
            for command in commands:
                permitted = role == 'owner' or (role == 'approver' and command in {'approve_send','reject_draft'})
                with self.subTest(role=role, command=command), \
                     patch('outreach_hosted.action') as action, \
                     patch('outreach_hosted.add_request') as add, \
                     patch.object(self.service, 'draft_action') as draft, \
                     patch.object(self.service, 'set_schedule') as schedule, \
                     patch.object(self.service, 'disconnect') as disconnect:
                    response = self.post(command, h, note='Fixture')
                    self.assertEqual(response.status_code, 200 if permitted else 403)
                    self.assertEqual(sum(x.call_count for x in [action,add,draft,schedule,disconnect]), int(permitted))
            for unknown in ['delete_everything', '', None, [], {}]:
                self.assertEqual(self.post(unknown,h).status_code,403)
            self.login(role+'@example.com', expires=1)
            self.assertEqual(self.post('classify',h,note='Expired').status_code,401)

    def test_classification_note_readable_and_hosted_status_not_legacy(self):
        mid = self.reply()
        h = self.login()
        self.assertEqual(self.post('classify',h,message=mid,kind='reply',note='Evidence reviewed').status_code,200)
        self.login('reviewer@example.com')
        state = self.get('/api/status').json
        self.assertIn('Evidence reviewed',state['incoming'][0]['classification_note'])
        self.assertNotIn('scheduler',state)
        self.assertNotIn('live_reply_validation',state)
        self.assertIn('enabled',state['service'])

    def test_connect_mailbox_requires_owner_and_valid_session(self):
        self.cfg['allowlist']['approver@example.com'] = 'approver'
        for role in ['reviewer','approver']:
            h=self.login(role+'@example.com')
            r=self.client.post('/api/connect',base_url=self.cfg['origin'],headers=h,json={})
            self.assertEqual(r.status_code,403)


class WorkspacePermissionTests(unittest.TestCase):
    setUp = workspace_fixtures.WorkspaceTests.setUp
    tearDown = workspace_fixtures.WorkspaceTests.tearDown
    login = workspace_fixtures.WorkspaceTests.login
    post = workspace_fixtures.WorkspaceTests.post
    get = workspace_fixtures.WorkspaceTests.get

    def test_personal_owner_cannot_inherit_shared_or_other_permissions(self):
        self.cfg['allowlist']['ryan@example.com']='reviewer'
        for wid in ['shared',self.other,'../../shared']:
            for command in ['classify','approve_send','reject_draft','edit_draft','schedule_on','add_request','disconnect']:
                self.assertEqual(self.post(command,wid,note='Denied').status_code,403)
        self.assertEqual(self.post('resume',self.ryan,note='Own workspace').status_code,200)
        self.assertEqual(self.get('/api/status','shared').status_code,200)
        self.assertEqual(self.get('/api/status',self.other).status_code,403)


if __name__ == '__main__':
    unittest.main()
