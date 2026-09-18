import io
import json
import os
from pathlib import Path
import sqlite3
import time
import unittest
from unittest.mock import patch, Mock
from urllib.error import HTTPError, URLError
from cryptography.fernet import Fernet, InvalidToken
import test_outreach_hosted as fixtures
from outreach_backup import snapshot, restore, automatic
from outreach_live import Gmail, MailReadError, locked
from outreach_operations import operations
from outreach_service import Service


class RecoveryTests(unittest.TestCase):
    setUp = fixtures.HostedTests.setUp
    tearDown = fixtures.HostedTests.tearDown
    login = fixtures.HostedTests.login
    get = fixtures.HostedTests.get

    def test_liveness_independent_of_db_and_private_diagnostics(self):
        self.assertEqual(self.get('/api/operations').status_code, 401)
        with patch.object(self.service, 'db', side_effect=sqlite3.OperationalError('private-path')):
            self.assertEqual(self.get('/healthz').json, {'status':'ok'})
        self.login('reviewer@example.com')
        self.assertEqual(self.get('/api/operations').status_code, 403)
        self.assertNotIn('operations', self.get('/api/status').json)
        self.login()
        result = self.get('/api/operations')
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('owner@example', result.get_data(as_text=True))
        self.assertNotIn('test-secret', result.get_data(as_text=True))

    def test_nonce_matches_document_changes_each_response(self):
        a,b = self.get('/'), self.get('/')
        import re
        nonce = re.search("script-src 'nonce-([^']+)'", a.headers['Content-Security-Policy'])[1]
        self.assertIn(('nonce="'+nonce+'"').encode(), a.data)
        self.assertNotIn(b'nonce="console"', a.data)
        self.assertNotEqual(a.headers['Content-Security-Policy'], b.headers['Content-Security-Policy'])

    def test_stopped_scheduler_disk_loss_and_unknown_consent_alerts(self):
        with self.service.db() as db:
            db.execute('UPDATE settings SET enabled=1,last_heartbeat=1,last_scan=1')
        disk=Mock(free=1,total=1000)
        with patch('outreach_operations.shutil.disk_usage',return_value=disk):
            codes={i['code'] for i in operations(self.service,now=10000)['incidents']}
        self.assertTrue({'scheduler_stale','scan_overdue','disk_low'} <= codes)
        with patch.object(self.service,'status',side_effect=sqlite3.OperationalError('SECRET')):
            result=operations(self.service)
        self.assertEqual(result['database'],'unavailable')
        self.assertNotIn('SECRET',json.dumps(result))

    def test_read_backoff_timeout_quota_and_no_post_retry(self):
        for error in [URLError('private'), TimeoutError(), HTTPError('url',429,'quota',{},None),HTTPError('url',503,'down',{},None)]:
            with patch('outreach_live.time.sleep'),patch('outreach_live.urlopen',side_effect=[error,io.BytesIO(b'{}')]) as request:
                api=Gmail('secret');self.assertEqual(api.get('id'),{});self.assertEqual(request.call_count,2)
                self.assertEqual(api.read_retries,1)
            with patch('outreach_live.time.sleep'),patch('outreach_live.urlopen',side_effect=error) as request:
                with self.assertRaises(RuntimeError):Gmail('secret').send(b'approved')
                self.assertEqual(request.call_count,1)
        with patch('outreach_live.time.sleep'),patch('outreach_live.urlopen',side_effect=TimeoutError()) as request:
            with self.assertRaises(MailReadError):Gmail('secret').get('id')
            self.assertEqual(request.call_count,3)
        with patch('outreach_live.urlopen') as request:
            with self.assertRaises(MailReadError):Gmail('secret',deadline=0).get('id')
            request.assert_not_called()

    def test_encrypted_restore_drill_complete_hashes_and_mail_disabled(self):
        self.service.draft_action('prepare_drafts',{},'fixture')
        draft=self.service.drafts()[0]
        self.service.draft_action('approve_send',{'key':draft['key'],'digest':draft['digest']},'fixture')
        from outreach_console import action
        action(self.run,True,{'command':'demo_reply'})
        self.service.draft_action('prepare_drafts',{},'fixture')
        from outreach_live import Pilot
        pilot=Pilot(self.run)
        try:pilot.db.execute("UPDATE jobs SET state='uncertain' WHERE state='pending'")
        finally:pilot.close()
        self.login()
        (self.run/'evidence.pdf').write_bytes(b'%PDF-1.7\nfixture evidence')
        key=Fernet.generate_key();archive=self.root/'backups'/'fixture.fernet'
        started=time.monotonic()
        result=snapshot({'campaign':self.run,'service':self.service.folder},{'allowlist':self.cfg['allowlist'],'client':'fixture-secret'},key,archive)
        self.assertNotIn(b'fixture-secret',archive.read_bytes())
        with self.assertRaises(InvalidToken):restore(archive,Fernet.generate_key(),self.root/'bad-key')
        self.assertFalse((self.root/'bad-key').exists())
        target=self.root/'restored'
        report=restore(archive,key,target)
        self.assertEqual(report['verified_files'],result['files'])
        self.assertEqual((target/'campaign'/'evidence.pdf').read_bytes(),(self.run/'evidence.pdf').read_bytes())
        restored=Service(target/'service',target/'campaign',self.cfg['key'],True,False)
        self.assertEqual(restored.run_due(),'recovery_hold')
        from outreach_console import read_status
        self.assertEqual([j['state'] for j in read_status(restored.run)['jobs']],['sent','uncertain'])
        self.assertFalse(restored.status()['enabled'])
        with restored.db() as db:self.assertEqual(db.execute('SELECT count(*) FROM sessions').fetchone()[0],0)
        with self.assertRaises(ValueError):restored.gmail()
        with self.assertRaises(ValueError):restored.set_schedule(True,'fixture')
        with self.assertRaises(ValueError):restored.draft_action('approve_send',{},'fixture')
        with self.assertRaises(ValueError):restore(archive,key,target)
        print('Isolated restore drill: %.3fs, %s verified files; all outbound paths held' % (time.monotonic()-started,result['files']))

    def test_backup_lock_contention_and_daily_retention(self):
        manager=self.app.extensions['outreach_workspaces'];folder=self.root/'versions'
        with patch.dict(os.environ,{'OUTREACH_BACKUP_KEY':Fernet.generate_key().decode(),'OUTREACH_BACKUP_DIR':str(folder)}):
            with locked(self.run):
                with self.assertRaises(FileExistsError):automatic(manager)
            manager._backup_attempt=float('-inf');automatic(manager)
            self.assertEqual(len(list(folder.glob('*.fernet'))),1)
            automatic(manager);self.assertEqual(len(list(folder.glob('*.fernet'))),1)
            self.assertFalse(json.loads((folder/'latest.json').read_text())['off_host_copy_verified'])

    def test_fairness_rotates_even_when_first_workspace_fails(self):
        manager=self.app.extensions['outreach_workspaces'];visits=[]
        def fake(name):
            s=Mock();s.run_due.side_effect=lambda:visits.append(name);return s
        manager.services={'shared':fake('shared'),'second':fake('second')}
        with patch('outreach_workspaces.read_status',return_value={'sender':'owner@example.com'}):
            manager.run_due();manager.run_due()
        self.assertEqual(visits,['shared','second','second','shared'])

    def test_backup_tamper_and_overlapping_destination_fail_closed(self):
        key=Fernet.generate_key();path=self.root/'copy.fernet'
        with self.assertRaises(ValueError):snapshot({'campaign':self.run,'service':self.service.folder},{},key,self.run/'backup.fernet')
        snapshot({'campaign':self.run,'service':self.service.folder},{},key,path)
        data=bytearray(path.read_bytes());data[-10] ^= 1;path.write_bytes(data)
        with self.assertRaises(InvalidToken):restore(path,key,self.root/'corrupt')
        self.assertFalse((self.root/'corrupt').exists())

    def test_concurrent_approvals_accept_only_one_message(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from outreach_console import action, read_status
        self.service.draft_action('prepare_drafts',{},'fixture')
        draft=self.service.drafts()[0]
        entered, release = threading.Event(), threading.Event()
        def slow(*args, **kwargs):
            entered.set();release.wait(5)
            return action(*args,**kwargs)
        with patch('outreach_service.action',side_effect=slow), ThreadPoolExecutor(max_workers=2) as executor:
            first=executor.submit(self.service.draft_action,'approve_send',{'key':draft['key'],'digest':draft['digest']},'fixture')
            self.assertTrue(entered.wait(3))
            try:
                with self.assertRaises(FileExistsError):self.service.draft_action('approve_send',{'key':draft['key'],'digest':draft['digest']},'fixture')
            finally:release.set()
            first.result()
        self.assertEqual(sum(j['state']=='sent' for j in read_status(self.run)['jobs']),1)


class WorkspaceRestoreTests(unittest.TestCase):
    def test_all_registered_workspaces_evidence_and_membership_restore(self):
        from test_workspaces import WorkspaceTests
        from outreach_hosted import create_app
        fixture=WorkspaceTests();fixture.setUp()
        try:
            fixture.contact()
            fixture.manager.cfg['open_signup']=True
            fixture.manager.register_verified('registered@example.com')
            service=fixture.manager.services[fixture.ryan]
            evidence=service.run/'proof.eml';evidence.write_bytes(b'From: fixture\r\n\r\nprivate evidence')
            key=Fernet.generate_key();archive=fixture.root/'all.fernet'
            started=time.monotonic()
            snapshot({'campaign':fixture.cfg['run'],'service':fixture.cfg['state']},{'roles':fixture.cfg['allowlist']},key,archive)
            target=fixture.root/'recovered';receipt=restore(archive,key,target)
            restarted=create_app(fixture.cfg|{'run':target/'campaign','state':target/'service'}).extensions['outreach_workspaces']
            self.assertEqual(set(restarted.services),set(fixture.manager.services))
            for wid, restored in restarted.services.items():
                self.assertEqual(restored.run_due(),'recovery_hold')
                self.assertFalse(restored.status()['enabled'])
            self.assertEqual((restarted.services[fixture.ryan].run/'proof.eml').read_bytes(),evidence.read_bytes())
            self.assertEqual(restarted.choices('ryan@example.com'),fixture.manager.choices('ryan@example.com'))
            restarted.register_verified('new-after-restore@example.com')
            new_service=restarted.services[restarted.personal['new-after-restore@example.com']]
            self.assertEqual(new_service.run_due(),'recovery_hold')
            with self.assertRaises(ValueError):new_service.gmail()
            print('Multi-workspace restore drill: %.3fs, %d workspaces, %d verified files' % (time.monotonic()-started,len(restarted.services),receipt['verified_files']))
        finally:fixture.tearDown()


if __name__ == '__main__':unittest.main()
