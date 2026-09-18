import sqlite3
from email.message import EmailMessage
from email import policy
from hashlib import sha256
from unittest.mock import patch
import unittest
import test_outreach_hosted as hosted
import test_workspaces as workspaces
from outreach_evidence import evidence

class EvidenceTests(unittest.TestCase):
    setUp=hosted.HostedTests.setUp
    tearDown=hosted.HostedTests.tearDown
    login=hosted.HostedTests.login
    get=hosted.HostedTests.get
    def add(self):
        msg=EmailMessage();msg['From']='office@example.com';msg['Subject']='Evidence';msg['Message-ID']='<fixture@example.com>'
        msg.set_content('<p>Road award confirmed</p><script>steal()</script><img src="https://tracker.invalid/x"><form>secret</form>',subtype='html')
        self.pdf=b'%PDF-1.4\nfixture\n%%EOF';msg.add_attachment(self.pdf,maintype='application',subtype='pdf',filename='../../evil<script>.pdf')
        msg.add_attachment(b'<script>bad()</script>',maintype='text',subtype='html',filename='active.html')
        self.raw=msg.as_bytes(policy=policy.SMTP)
        with sqlite3.connect(self.run/'live.sqlite3') as db:
            rid=db.execute('SELECT id FROM requests LIMIT 1').fetchone()[0]
            db.execute('INSERT INTO incoming VALUES(?,?,?,?)',('fixture',rid,self.raw,'reply'))
        db.close()
        return '/api/evidence/fixture'
    def test_reviewer_html_manifest_download_and_original(self):
        path=self.add();self.login('reviewer@example.com');r=self.get(path);self.assertEqual(r.status_code,200);e=r.json
        self.assertIn('Road award confirmed',e['text']);self.assertNotIn('steal',e['text']);self.assertNotIn('tracker',e['text']);self.assertNotIn('secret',e['text'])
        f=e['attachments'][0];self.assertNotIn('/',f['name']);self.assertNotIn('<',f['name']);self.assertEqual(f['sha256'],sha256(self.pdf).hexdigest())
        d=self.get(path+'/'+f['id']);self.assertEqual(d.data,self.pdf);self.assertEqual(d.headers['Cache-Control'],'no-store');self.assertIn('attachment;',d.headers['Content-Disposition']);self.assertEqual(d.mimetype,'application/octet-stream')
        self.assertEqual(self.get(path+'/original').data,self.raw)
        self.assertFalse(e['attachments'][1]['available']);self.assertEqual(self.get(path+'/'+e['attachments'][1]['id']).status_code,404)
    def test_auth_missing_unmatched_and_limits(self):
        path=self.add();self.assertEqual(self.get(path).status_code,401);self.login();self.assertEqual(self.get('/api/evidence/missing').status_code,404)
        with patch('outreach_evidence.MAX_MESSAGE',1):self.assertEqual(self.get(path).status_code,413)
        with patch('outreach_evidence.MAX_FILE',1):self.assertFalse(self.get(path).json['attachments'][0]['available'])
        with patch('outreach_evidence.MAX_PARTS',1):self.assertEqual(self.get(path).status_code,413)
        self.cfg['evidence_enabled']=False;self.assertEqual(self.get(path).status_code,404)
    def test_corrupt_pdf_and_history(self):
        path=self.add();self.login()
        msg=EmailMessage();msg.set_content('Body');msg.add_attachment(b'%PDF-broken',maintype='application',subtype='pdf',filename='broken.pdf')
        with sqlite3.connect(self.run/'live.sqlite3') as db:
            db.execute("INSERT INTO audit(at,event,detail) VALUES('today','classified_reply','fixture: reviewed')")
            db.execute('UPDATE incoming SET raw=? WHERE id=?',(msg.as_bytes(),'fixture'))
        db.close()
        self.assertEqual(self.get(path).json['attachments'][0]['state'],'Corrupt or empty PDF')
        self.assertEqual(self.get(path).json['history'][0]['detail'],'fixture: reviewed')
        with sqlite3.connect(self.run/'live.sqlite3') as db:
            db.execute('UPDATE incoming SET rid=NULL WHERE id=?',('fixture',))
        db.close()
        self.assertEqual(self.get(path).status_code,404)

class EvidenceWorkspaceTests(unittest.TestCase):
    setUp=workspaces.WorkspaceTests.setUp
    tearDown=workspaces.WorkspaceTests.tearDown
    login=workspaces.WorkspaceTests.login
    get=workspaces.WorkspaceTests.get
    def test_cross_workspace_and_path_denied(self):
        for suffix in ['', '/original', '/'+'a'*64]:
            self.assertEqual(self.get('/api/evidence/fixture'+suffix,self.other).status_code,403)
        self.assertEqual(self.get('/api/evidence/fixture','../../shared').status_code,403)
        self.assertEqual(self.get('/api/evidence/fixture',self.ryan).status_code,404)
