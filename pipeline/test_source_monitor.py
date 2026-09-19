"""Offline source-monitor fixtures. No campaign email or live provider calls."""
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from pypdf import PdfWriter
import source_monitor as sm
import test_outreach_hosted as hosted
import test_workspaces as workspaces


def pdf(width=100):
    out=io.BytesIO();writer=PdfWriter();writer.add_blank_page(width=width,height=100);writer.write(out);return out.getvalue()


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.old=pdf();self.new=pdf(200)
        self.url='https://www.michigan.gov/dtmb/procurement/contracts/2027.pdf'
        self.catalog={'sources':[dict(id='mi',url=self.url,kind='pdf',state='MI',sha256=sm.sha(self.old))]}
        self.m=sm.Monitor(self.root,self.catalog)
        self.now=datetime(2026,8,31,10,tzinfo=sm.ZONE).timestamp()
    def tearDown(self):self.tmp.cleanup()
    def fetcher(self,blob):
        def fetch(source,output):
            output.write_bytes(blob)
            result=dict(sha256=sm.sha(blob),bytes=len(blob),final_url=source['url'],http_status=200)
            if source['kind']=='listing':result['links']=sm.discover(blob,source['url'],source['state'])
            else:result['pages']=1
            return result
        return fetch
    def check(self,fetcher):
        self.m.control({'command':'monitor_check'},'fixture',self.now)
        result=self.m.tick(fetcher,self.now+1);self.now+=901;return result
    def test_unchanged_and_changed_deduplicated_immutable_evidence(self):
        self.assertEqual(self.check(self.fetcher(self.old)),'unchanged');self.assertEqual(self.m.view()['queue'],[])
        self.assertEqual(self.check(self.fetcher(self.new)),'changed_requires_review')
        self.check(self.fetcher(self.new));view=self.m.view();self.assertEqual(len(view['queue']),1)
        q=view['queue'][0];self.assertEqual(self.m.document(q['id']),self.new)
        self.assertEqual(view['sources'][0]['baseline'],sm.sha(self.old))
        self.assertEqual(sm.Monitor(self.root,self.catalog).view()['queue'],view['queue'])
        (self.m.folder/'objects'/q['hash']).write_bytes(b'corrupt')
        with self.assertRaises(ValueError):self.m.document(q['id'])
    def test_failure_does_not_erase_success_or_queue_and_incident_dedup(self):
        self.check(self.fetcher(self.new));before=self.m.view()['sources'][0]
        def fail(*_):raise ValueError('HTTP 403; source unavailable')
        self.check(fail);self.check(fail);v=self.m.view()
        self.assertEqual(len(v['incidents']),1);self.assertEqual(v['incidents'][0]['occurrences'],2)
        self.assertEqual(v['sources'][0]['last_success'],before['last_success']);self.assertEqual(len(v['queue']),1)
        self.check(self.fetcher(self.old));self.assertIsNotNone(self.m.view()['incidents'][0]['resolved'])
    def test_listing_discovers_new_season_removals_and_rejects_empty(self):
        cat={'sources':[dict(id='listing',url='https://www.michigan.gov/list',kind='listing',state='MI')]}
        self.m=sm.Monitor(self.root/'listing',cat)
        a=b'<html><a href="/dtmb/procurement/contracts/2028.pdf">FY2028</a></html>'
        self.assertEqual(self.check(self.fetcher(a)),'listing_baselined')
        v=self.m.view();self.assertEqual(len(v['sources']),2);self.assertEqual(len(v['queue']),1)
        # Run the discovered document separately, then change the listing.
        self.m.tick(self.fetcher(self.new),self.now)
        b=b'<a href="/dtmb/procurement/contracts/2029.pdf">FY2029</a>'
        with self.m.db() as db:db.execute("UPDATE sources SET requested=1 WHERE id='listing'")
        self.m.tick(self.fetcher(b),self.now+1)
        q=[q for q in self.m.view()['queue'] if q['kind']=='listing_changed'][0]
        self.assertIn('2028.pdf',q['detail']['removed'][0])
        self.assertEqual(len(self.m.view()['sources']),3) # removed links do not erase PDFs
        with self.assertRaises(ValueError):sm.discover(b'<html>Login or error</html>','https://www.pa.gov/','PA')
    def test_season_and_dst_calendar(self):
        cases=[('2026-05-31T10:00:00-04:00','2026-06-01T09:00:00-04:00'),
               ('2026-08-31T10:00:00-04:00','2026-09-07T09:00:00-04:00'),
               ('2026-03-06T10:00:00-05:00','2026-03-09T09:00:00-04:00'),
               ('2026-10-30T10:00:00-04:00','2026-11-02T09:00:00-05:00')]
        for start,end in cases:self.assertEqual(sm.next_check(datetime.fromisoformat(start).timestamp()),datetime.fromisoformat(end).timestamp())
    def test_pause_manual_and_recovery_hold(self):
        self.assertEqual(self.m.tick(lambda *_:self.fail('paused must not fetch'),self.now),'idle')
        self.m.control({'command':'monitor_enable'},'fixture',self.now)
        self.assertEqual(self.m.tick(self.fetcher(self.old),self.now),'unchanged')
        self.m.control({'command':'monitor_pause'},'fixture',self.now+1)
        self.assertEqual(self.m.tick(now=self.now+900000),'idle')
        self.m.hold=lambda:True
        with self.assertRaises(ValueError):self.m.control({'command':'monitor_check'},'fixture',self.now+1000)
        self.assertEqual(self.m.tick(now=self.now+1000),'recovery_hold')
    def test_timeout_restart_and_no_concurrent_claim(self):
        with patch.object(sm.subprocess,'run',side_effect=sm.subprocess.TimeoutExpired('reader',40)):
            self.assertEqual(self.check(sm.fetch),'failed')
        self.m.control({'command':'monitor_check'},'fixture',self.now)
        def nested(source,output):
            self.assertEqual(self.m.tick(now=self.now+2),'idle')
            return self.fetcher(self.old)(source,output)
        self.assertEqual(self.m.tick(nested,self.now+1),'unchanged')
        with self.m.db() as db:db.execute("UPDATE sources SET claim='interrupted',claimed=?,status='checking'",(self.now-200,))
        self.m.tick(now=self.now+1)
        self.assertIn('Interrupted',self.m.view()['sources'][0]['error'])
    def test_official_hosts_discovery_limits_and_malformed_pdf(self):
        for u in ['http://www.pa.gov/x','https://www.pa.gov.evil.org/x','https://user@www.pa.gov/x','https://127.0.0.1/a','https://www.pa.gov:8443/a']:
            with self.assertRaises(ValueError):sm.official(u)
        self.assertEqual(self.check(self.fetcher(b'<html>not a PDF</html>')),'failed')
        malicious=b'<a href="https://evil.test/road salt.pdf">x</a>'
        with self.assertRaises(ValueError):sm.discover(malicious,'https://www.pa.gov/','PA')
    def test_actual_pdf_parser_and_retrieval_metadata(self):
        class Response(io.BytesIO):
            status=200;url='https://www.pa.gov/a.pdf';headers={'Content-Type':'application/pdf','ETag':'fixture'}
        class Opener:
            def open(_,req,timeout):return Response(self.old)
        with patch.object(sm.urllib.request,'build_opener',return_value=Opener()):
            r=sm.retrieve({'url':'https://www.pa.gov/a.pdf','kind':'pdf'},self.root/'f.pdf')
        self.assertEqual(r['pages'],1);self.assertEqual(r['etag'],'fixture')
    def test_aliases_do_not_duplicate_known_or_new_pdf_and_w9_excluded(self):
        alias=dict(id='alias',url=self.url+'?rev=version',kind='pdf',state='MI')
        self.m=sm.Monitor(self.root,{'sources':[alias]})
        self.check(self.fetcher(self.old));self.m.tick(self.fetcher(self.old),self.now)
        self.assertEqual(self.m.view()['queue'],[])
        self.check(self.fetcher(self.new));self.m.tick(self.fetcher(self.new),self.now)
        self.assertEqual(len(self.m.view()['queue']),1)
        links=sm.discover(b'<a href="/dtmb/procurement/contracts/2028.pdf">salt</a><a href="/dtmb/procurement/contracts/supporting-documents/W-9.pdf">tax</a>',self.url,'MI')
        self.assertEqual(len(links),1)
    def test_triage_is_not_publication_and_is_concurrency_checked(self):
        self.check(self.fetcher(self.new));q=self.m.view()['queue'][0]
        data=dict(command='monitor_review',item=q['id'],expected='needs_review',decision='held',note='Needs annual quantity evidence')
        self.m.control(data,'reviewing-owner',self.now)
        with self.assertRaises(ValueError):self.m.control(data,'stale-owner',self.now)
        data.update(expected='held',decision='accepted')
        with self.assertRaises(ValueError):self.m.control(data,'owner',self.now)
        self.assertEqual(self.m.view()['queue'][0]['status'],'held')
    def test_backup_preserves_evidence_and_restored_monitor_holds(self):
        from cryptography.fernet import Fernet
        from outreach_backup import snapshot,restore
        self.check(self.fetcher(self.new));key=Fernet.generate_key();archive=self.root.parent/(self.root.name+'.fernet')
        try:
            snapshot({'service':self.root,'campaign':self.root/'empty-campaign'},{},key,archive)
            destination=self.root.parent/(self.root.name+'-restore')
            restore(archive,key,destination)
            restored=sm.Monitor(destination/'service',self.catalog,hold=lambda:(destination/'RECOVERY_HOLD').exists())
            q=restored.view()['queue'][0];self.assertEqual(restored.document(q['id']),self.new)
            self.assertEqual(restored.tick(lambda *_:self.fail('No restored network reads')),'recovery_hold')
        finally:
            archive.unlink(missing_ok=True)
            if 'destination' in locals():
                import shutil
                shutil.rmtree(destination)
    def test_cooldown_cache_full_and_redirect_controls(self):
        self.m.control({'command':'monitor_check'},'fixture',self.now)
        with self.assertRaises(ValueError):self.m.control({'command':'monitor_check'},'fixture',self.now+1)
        with patch.object(sm,'CACHE_LIMIT',1):self.assertEqual(self.m.tick(self.fetcher(self.new),self.now),'failed')
        self.assertEqual(self.m.view()['queue'],[])
        with self.assertRaises(ValueError):sm.Redirect().redirect_request(None,None,302,'',{},'https://evil.test/a.pdf')


class MonitorAccessTests(unittest.TestCase):
    setUp=hosted.HostedTests.setUp;tearDown=hosted.HostedTests.tearDown
    login=hosted.HostedTests.login;get=hosted.HostedTests.get;post=hosted.HostedTests.post
    def test_roles_csrf_no_mail_and_recovery(self):
        self.assertEqual(self.get('/api/source-monitor').status_code,401)
        h=self.login();self.assertEqual(self.get('/api/source-monitor').status_code,200)
        self.assertEqual(self.post('monitor_check',{}).status_code,403)
        with patch.object(self.service,'draft_action',side_effect=AssertionError('mail forbidden')):
            self.assertEqual(self.post('monitor_check',h).status_code,200)
        self.assertEqual(self.get('/api/status').json['jobs'],[])
        for role in ['reviewer','approver']:
            self.cfg['allowlist']['reviewer@example.com']=role;h=self.login('reviewer@example.com')
            self.assertEqual(self.get('/api/source-monitor').status_code,200)
            for c in ['monitor_enable','monitor_pause','monitor_check','monitor_review']:
                self.assertEqual(self.post(c,h).status_code,403)


class MonitorIsolationTests(unittest.TestCase):
    setUp=workspaces.WorkspaceTests.setUp;tearDown=workspaces.WorkspaceTests.tearDown
    login=workspaces.WorkspaceTests.login
    def test_personal_owner_cannot_read_or_change_shared_monitor(self):
        self.login('other@example.com');h=self.headers
        self.assertEqual(self.client.get('/api/source-monitor',base_url=self.cfg['origin'],headers=h).status_code,403)
        self.assertEqual(self.client.post('/api/action',base_url=self.cfg['origin'],headers=h,json={'command':'monitor_check'}).status_code,403)


if __name__=='__main__':unittest.main()
