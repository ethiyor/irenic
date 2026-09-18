import unittest
import test_evidence as fixtures
from outreach_console import read_status
from outreach_workflow import workflow
from outreach_live import Pilot

class WorkflowTests(unittest.TestCase):
    setUp=fixtures.EvidenceTests.setUp
    tearDown=fixtures.EvidenceTests.tearDown
    add=fixtures.EvidenceTests.add
    def test_held_disabled_and_job_counts_reconcile(self):
        self.add()
        p=Pilot(self.run)
        try:
            p.db.execute("UPDATE requests SET state='held' WHERE id='PA-LAN-001'")
            p.db.execute("UPDATE requests SET state='suppressed' WHERE id='PA-BUT-001'")
            p.db.commit()
        finally:p.close()
        state=read_status(self.run);w=workflow(self.service,state)
        self.assertEqual(w['counts']['held'],1);self.assertEqual(w['counts']['disabled'],4);self.assertEqual(w['counts']['suppressed'],1)
        self.assertEqual(w['counts']['documents_open'],2)
        self.assertEqual(len(w['timelines']),5)
        self.assertTrue(any(t['replies'] for t in w['timelines']))
        self.assertEqual(w['counts']['initial_sent']+w['counts']['reminders_sent']+w['counts']['forwards_sent'],0)
    def test_projection_never_mutates_ledger(self):
        before=read_status(self.run);workflow(self.service,before);self.assertEqual(before,read_status(self.run))
