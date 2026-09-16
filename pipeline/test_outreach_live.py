"""Live-worker contract tests use an in-memory provider; never access Gmail."""
import base64
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from outreach_live import Gmail, Pilot, canonical, locked, prepare, sha
from outreach_dry_run import ROOT


from outreach_fixture import Provider


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.app = Pilot(Path(self.temp.name))
        self.config = prepare(ROOT/'data/outreach/milestone-9/contacts.json')
        self.config.update(sender='operator@example.com', forward_to=['review@example.com'], timezone='UTC')
        self.api = Provider()

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def approve(self):
        self.app.initialize(self.config, sha(canonical(self.config)), 'Test fixture authorization only')

    def test_unapproved_and_wrong_hash_block(self):
        with self.assertRaises(ValueError):
            self.app.tick(self.api)
        with self.assertRaises(ValueError):
            self.app.initialize(self.config, 'wrong', 'test')
        self.assertEqual(self.api.sends, [])

    def test_validation_and_identity(self):
        self.config['sender'] = 'a@example.com\nBcc: b@example.com'
        with self.assertRaises(ValueError):
            self.approve()
        self.config['sender'] = 'other@example.com'
        self.approve()
        with self.assertRaises(ValueError):
            self.app.tick(self.api)
        self.assertFalse(self.api.sends)

    def test_one_contact_gate_and_idempotence(self):
        self.approve()
        self.app.tick(self.api)
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)
        with self.assertRaises(ValueError):
            self.app.control('expand', 'test')
        r = self.app.db.execute("SELECT * FROM requests WHERE stage=1").fetchone()
        self.assertEqual(r['id'], self.config['pilot_contact'])

    def test_provider_rewritten_id_matches_cross_thread_reply_and_reminder(self):
        self.approve()
        self.app.tick(self.api)
        first = self.api.rows[self.api.sends[0]['id']]
        msg = BytesParser(policy=policy.default).parsebytes(first['_raw'])
        msg.replace_header('Message-ID', '<rewritten@example.com>')
        raw = msg.as_bytes(policy=policy.SMTP)
        first.update(_raw=raw, raw=base64.urlsafe_b64encode(raw).decode(),
                     payload={'headers': [{'name': k, 'value': str(v)} for k, v in msg.items()]})
        self.app.db.execute("UPDATE requests SET due='2000-01-01' WHERE stage=1")
        self.app.tick(self.api)
        reminder = BytesParser(policy=policy.default).parsebytes(self.api.rows[self.api.sends[1]['id']]['_raw'])
        self.assertEqual(str(reminder['In-Reply-To']), '<rewritten@example.com>')
        self.api.reply(thread='changed-thread')
        self.app.tick(self.api)
        self.assertEqual(len(self.app.report()['incoming']), 1)
        self.assertEqual(len(self.api.sends), 3)

    def test_sent_metadata_mismatch_blocks_outbound(self):
        self.approve()
        self.app.tick(self.api)
        self.api.rows[self.api.sends[0]['id']]['labelIds'] = []
        with self.assertRaises(RuntimeError):
            self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)

    def test_reply_forward_once_then_expand(self):
        self.approve()
        self.app.tick(self.api)
        mid = self.api.reply()
        self.app.tick(self.api)
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 2)
        forward = BytesParser(policy=policy.default).parsebytes(self.api.rows[self.api.sends[1]['id']]['_raw'])
        self.assertEqual(str(forward['To']), 'review@example.com')
        attachment = list(forward.iter_attachments())[0]
        self.assertEqual(attachment.get_payload(decode=True), self.api.rows[mid]['_raw'])
        with self.assertRaises(ValueError):
            self.app.control('expand', 'not yet classified')
        self.app.classify(mid, 'reply', 'Human reply confirmed in fixture')
        self.app.control('expand', 'Approve remaining four fixture contacts')
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 6)

    def test_uncertain_acceptance_restart_reconciles(self):
        self.approve()
        self.api.fail = 'after'
        with self.assertRaises(TimeoutError):
            self.app.tick(self.api)
        self.app.close()
        self.app = Pilot(self.temp.name)
        self.api.fail = None
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)
        self.assertEqual(self.app.report()['jobs'][0]['state'], 'sent')

    def test_uncertain_absent_is_not_retried(self):
        self.approve()
        self.api.fail = 'before'
        with self.assertRaises(TimeoutError):
            self.app.tick(self.api)
        self.api.fail = None
        with self.assertRaises(RuntimeError):
            self.app.tick(self.api)
        self.assertFalse(self.api.sends)

    def test_sync_failure_and_pause_block_outbound(self):
        self.approve()
        self.api.fail_scan = True
        with self.assertRaises(RuntimeError):
            self.app.tick(self.api)
        self.assertFalse(self.api.sends)
        self.api.fail_scan = False
        self.app.tick(self.api)
        self.app.control('pause', 'test')
        self.api.reply()
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)
        self.assertEqual(self.app.report()['incoming'][0]['kind'], 'unknown')
        self.app.control('resume', 'test')
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 2)

    def test_stop_sticky(self):
        self.approve()
        self.app.tick(self.api)
        mid = self.api.reply()
        self.app.tick(self.api)
        self.app.classify(mid, 'stop', 'Explicit stop request in fixture')
        self.app.classify(mid, 'reply', 'Later clarification')
        row = self.app.db.execute('SELECT state FROM requests WHERE id=?', (self.config['pilot_contact'],)).fetchone()
        self.assertEqual(row['state'], 'suppressed')

    def test_cross_thread_reply_matches_reference(self):
        self.approve()
        self.app.tick(self.api)
        self.api.reply(thread='different-thread')
        self.app.tick(self.api)
        self.assertEqual(len(self.app.report()['incoming']), 1)
        self.assertEqual(len(self.api.sends), 2)

    def test_routed_reply_preserves_four_attachments_and_replay_is_idempotent(self):
        self.approve()
        self.app.tick(self.api)
        original = self.api.rows[self.api.sends[0]['id']]
        sent = BytesParser(policy=policy.default).parsebytes(original['_raw'])
        sent.replace_header('Message-ID', '<provider-rewritten@example.com>')
        raw_sent = sent.as_bytes(policy=policy.SMTP)
        original['_raw'] = raw_sent
        original['raw'] = base64.urlsafe_b64encode(raw_sent).decode()
        msg = EmailMessage()
        msg['From'] = 'routed-buyer@example.com'
        msg['To'] = self.config['sender']
        msg['References'] = '<provider-rewritten@example.com> <internal-routing@example.com>'
        msg['In-Reply-To'] = '<internal-routing@example.com>'
        msg.set_content('Requested documents attached. Fixture only.')
        for i in range(4):
            msg.add_attachment(bytes([i, 0, 255, 13, 10]), maintype='application',
                               subtype='octet-stream', filename=f'fixture-{i}.bin')
        raw = msg.as_bytes()
        mid = self.api.save(raw, 'different-thread', ['INBOX'])['id']
        self.app.control('pause', 'Review routed reply before sending')
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)
        self.assertEqual(self.app.report()['incoming'][0]['rid'], self.config['pilot_contact'])
        self.app.classify(mid, 'reply', 'Routed buyer supplied four fixture files')
        self.app.control('resume', 'Forward reviewed fixture reply')
        self.app.tick(self.api)
        forwarded = BytesParser(policy=policy.default).parsebytes(
            self.api.rows[self.api.sends[1]['id']]['_raw'])
        self.assertEqual([p.get_payload(decode=True) for p in forwarded.walk()
                          if p.get_filename() == 'original-message.eml'], [raw])
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 2)
        row = self.app.db.execute('SELECT state,due FROM requests WHERE id=?',
                                  (self.config['pilot_contact'],)).fetchone()
        self.assertEqual(tuple(row), ('held', None))
        self.assertFalse(self.app.config()[0]['expanded'])

    def test_bounded_reminders_and_headers(self):
        self.approve()
        self.app.tick(self.api)
        for _ in range(3):
            self.app.db.execute("UPDATE requests SET due='2000-01-01' WHERE stage>0")
            self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 3)
        msg = BytesParser(policy=policy.default).parsebytes(self.api.rows[self.api.sends[1]['id']]['_raw'])
        self.assertIsNotNone(msg['In-Reply-To'])
        self.assertEqual(self.api.sends[1]['threadId'], self.api.sends[0]['threadId'])
        self.assertEqual(self.app.db.execute('SELECT state FROM requests WHERE stage=3').fetchone()[0], 'closed_no_response')

    def test_config_tampering_and_exclusive_lock(self):
        self.approve()
        with locked(self.app.folder):
            with self.assertRaises(FileExistsError):
                self.app.tick(self.api)
        self.app.db.execute("UPDATE campaign SET config='{}'")
        with self.assertRaises(ValueError):
            self.app.tick(self.api)

    def test_forward_uncertain_does_not_repeat_request(self):
        self.approve()
        self.app.tick(self.api)
        self.api.reply()
        self.api.fail = 'after'
        with self.assertRaises(TimeoutError):
            self.app.tick(self.api)
        self.api.fail = None
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 2)

    def test_gmail_paginates_and_encodes_mime(self):
        api = Gmail('test-token')
        with patch.object(api, 'api', side_effect=[{'messages': [{'id': 'a'}], 'nextPageToken': 'next'}, {'messages': [{'id': 'b'}]}]) as request:
            self.assertEqual(len(api.messages('query')), 2)
            self.assertEqual(request.call_args.args[1]['pageToken'], 'next')
        with patch.object(api, 'api', return_value={'id': 'sent'}) as request:
            api.send(b'raw message', 'thread')
            self.assertEqual(request.call_args.kwargs['body']['threadId'], 'thread')
            self.assertEqual(base64.urlsafe_b64decode(request.call_args.kwargs['body']['raw']), b'raw message')

    def test_unrelated_mail_is_not_stored_or_forwarded(self):
        self.approve()
        msg = EmailMessage()
        msg['From'] = 'unrelated@example.com'
        msg.set_content('Private unrelated email')
        self.api.save(msg.as_bytes(), 'unrelated-thread', ['INBOX'])
        self.app.tick(self.api)
        self.assertEqual(self.app.report()['incoming'], [])
        self.assertEqual(len(self.api.sends), 1)

    def test_conflicting_references_hold_both_without_forward(self):
        self.approve()
        self.app.tick(self.api)
        mid = self.api.reply()
        self.app.tick(self.api)
        self.app.classify(mid, 'reply', 'fixture')
        self.app.control('expand', 'fixture')
        self.app.tick(self.api)
        second = self.api.rows[self.api.sends[2]['id']]
        reference = str(BytesParser(policy=policy.default).parsebytes(second['_raw'])['Message-ID'])
        conflict = self.api.reply(extra_reference=reference)
        count = len(self.api.sends)
        self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), count)
        self.assertIsNone(self.app.db.execute('SELECT rid FROM incoming WHERE id=?', (conflict,)).fetchone()[0])

    def test_reply_arriving_before_reminder_cancels_it(self):
        self.approve()
        self.app.tick(self.api)
        self.app.db.execute("UPDATE requests SET due='2000-01-01' WHERE stage=1")
        actual_sync = self.app.sync
        calls = 0
        def arriving_sync(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.api.reply()
            return actual_sync(*args)
        with patch.object(self.app, 'sync', side_effect=arriving_sync):
            self.app.tick(self.api)
        self.assertEqual(len(self.api.sends), 1)
        self.assertEqual(self.app.db.execute("SELECT state FROM jobs WHERE stage=1 AND kind='request'").fetchone()[0], 'cancelled')


if __name__ == '__main__':
    unittest.main()
