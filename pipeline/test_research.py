"""Research authorization, stale reviews and source isolation. No live mail."""
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch

import test_outreach_hosted as hosted
import test_evidence as evidence_tests
import test_workspaces as workspaces
from outreach_research import digest, inventory, view, verify_packet


class ResearchTests(unittest.TestCase):
    setUp=hosted.HostedTests.setUp
    tearDown=hosted.HostedTests.tearDown
    login=hosted.HostedTests.login
    get=hosted.HostedTests.get
    post=hosted.HostedTests.post
    add=evidence_tests.EvidenceTests.add

    def packet(self):
        self.add()
        d=inventory(self.run)[0]
        source={k:d[k] for k in ('mid','rid','sha256','message_sha256')}
        source.update(extraction_sha256='a'*64,pages=1,low_text_pages=[1],method='Synthetic source; manual review required.')
        return dict(kind='contract_review',title='Fixture contract',sources=[source],blockers=['No annual allocation'],
                    facts=[dict(label='Term',value='Two years',evidence=[dict(sha256=d['sha256'],page=1,locator='Fixture')])],
                    diff={'public_volume_change':'0'})

    def import_packet(self,packet,h):
        return self.post('research_import',h,revision=self.get('/api/research').json['revision'],packet=packet)

    def decide(self,h,key,decision,command='research_decide',**kw):
        return self.post(command,h,candidate=key,revision=self.get('/api/research').json['revision'],decision=decision,note='Evidence examined in isolated fixture.',**kw)

    def test_import_idempotent_readonly_and_no_mail(self):
        p=self.packet();h=self.login()
        with patch.object(self.service,'draft_action',side_effect=AssertionError('No mail')):
            self.assertEqual(self.import_packet(p,h).status_code,200)
            self.assertEqual(self.import_packet(p,h).status_code,200)
            r=self.get('/api/research').json;self.assertEqual(len(r['candidates']),1)
            self.assertEqual(self.decide(h,digest(p),'accept').status_code,200)
        self.assertEqual(self.get('/api/status').json['jobs'],[])
        self.login('reviewer@example.com')
        self.assertEqual(self.get('/api/research').status_code,200)
        self.assertEqual(self.get('/api/research').json['candidates'][0]['facts']['decision'],'accept')

    def test_reviewer_approver_csrf_and_expired_denied(self):
        p=self.packet();h=self.login();self.import_packet(p,h)
        for role in ('reviewer','approver'):
            self.cfg['allowlist']['reviewer@example.com']=role;rh=self.login('reviewer@example.com')
            for command in ('research_import','research_decide','research_publication','research_receipt'):
                self.assertEqual(self.post(command,rh).status_code,403)
            self.assertEqual(self.get('/api/research/'+digest(p)+'/export').status_code,403)
        self.login();self.assertEqual(self.post('research_import',{},packet=p).status_code,403)
        self.login(expires=1);self.assertEqual(self.get('/api/research').status_code,401)

    def test_hold_and_contract_cannot_be_public_even_when_facts_accepted(self):
        p=self.packet();h=self.login();self.import_packet(p,h);key=digest(p)
        self.assertEqual(self.decide(h,key,'accept').status_code,200)
        self.assertEqual(self.decide(h,key,'eligible','research_publication').status_code,400)
        self.assertEqual(self.get('/api/research/'+key+'/export').status_code,400)
        self.assertEqual(self.decide(h,key,'hold','research_publication').status_code,200)
        self.assertEqual(self.decide(h,key,'exclude').status_code,200)
        self.assertIsNone(self.get('/api/research').json['candidates'][0]['publication'])

    def test_stale_review_source_tampering_and_wrong_page_fail_closed(self):
        p=self.packet();h=self.login();self.import_packet(p,h);key=digest(p)
        old=self.get('/api/research').json['revision'];self.decide(h,key,'hold')
        self.assertEqual(self.post('research_decide',h,candidate=key,revision=old,decision='accept',note='Stale approval').status_code,400)
        bad=copy.deepcopy(p);bad['facts'][0]['evidence'][0]['page']=2
        self.assertEqual(self.import_packet(bad,h).status_code,400)
        bad=copy.deepcopy(p);bad['sources'][0]['mid']='other-workspace'
        self.assertEqual(self.import_packet(bad,h).status_code,400)
        db=sqlite3.connect(self.run/'live.sqlite3')
        try:
            db.execute("UPDATE incoming SET raw=raw||'changed' WHERE id='fixture'");db.commit()
        finally:db.close()
        # Store bytes, not a SQLite TEXT concatenation.
        db=sqlite3.connect(self.run/'live.sqlite3')
        try:
            db.execute("UPDATE incoming SET raw=? WHERE id='fixture'",(self.raw+b'changed',));db.commit()
        finally:db.close()
        self.assertEqual(self.decide(h,key,'accept').status_code,400)

    def test_annual_separate_gate_and_new_review_invalidates_export(self):
        p=self.packet();p.update(kind='annual_review',blockers=[],intake_candidate_sha256='b'*64,
                                intake_ticket={'kind':'private_outreach_handoff','base_active_sha256':'c'*64,'rows':[{'record_id':'fixture'}],'conflicts':[]})
        h=self.login();self.import_packet(p,h);key=digest(p)
        self.assertEqual(self.decide(h,key,'eligible','research_publication').status_code,400)
        self.decide(h,key,'accept');self.decide(h,key,'eligible','research_publication')
        self.assertEqual(self.get('/api/research/'+key+'/export').status_code,200)
        self.decide(h,key,'hold')
        self.assertEqual(self.get('/api/research/'+key+'/export').status_code,400)


class ResearchWorkspaceTests(unittest.TestCase):
    setUp=workspaces.WorkspaceTests.setUp
    tearDown=workspaces.WorkspaceTests.tearDown
    login=workspaces.WorkspaceTests.login
    get=workspaces.WorkspaceTests.get

    def test_personal_workspace_cannot_read_shared_research(self):
        self.assertEqual(self.get('/api/research',self.other).status_code,403)
        r=self.get('/api/research',self.ryan)
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json['candidates'],[])
        self.assertEqual(r.json['documents'],[])
