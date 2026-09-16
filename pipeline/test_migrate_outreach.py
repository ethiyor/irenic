import tempfile
from pathlib import Path
import unittest
from outreach_console import initialize_demo
from outreach_live import Pilot
from migrate_outreach import export,restore,verify

class MigrationTests(unittest.TestCase):
 def test_backup_restore_and_replay_refusal(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);run=root/'source';initialize_demo(run)
   app=Pilot(run);app.control('pause','Migration fixture');app.close()
   expected=export(run,root/'bundle')
   self.assertEqual(restore(root/'bundle',root/'restored'),expected)
   with self.assertRaises(ValueError):restore(root/'bundle',root/'restored')
   with (root/'bundle'/'live.sqlite3').open('ab') as f:f.write(b'tamper')
   with self.assertRaisesRegex(ValueError,'hash mismatch'):verify(root/'bundle')
 def test_running_or_uncertain_campaign_refused(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);run=root/'source';initialize_demo(run)
   with self.assertRaisesRegex(ValueError,'Pause'):export(run,root/'bundle')
   app=Pilot(run);app.control('pause','Fixture')
   app.db.execute("INSERT INTO jobs(key,state) VALUES('uncertain-test','uncertain')");app.close()
   with self.assertRaisesRegex(ValueError,'uncertain'):export(run,root/'bundle')

if __name__=='__main__':unittest.main()
