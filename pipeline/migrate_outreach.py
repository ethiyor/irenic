"""Private stopped-worker ledger backup/verification. Does not send or initialize campaigns."""
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
from outreach_live import locked


def fingerprint(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def inspect(path):
    db=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True)
    db.row_factory=sqlite3.Row
    try:
        if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('Ledger integrity check failed')
        campaign=db.execute('SELECT * FROM campaign').fetchone()
        if not campaign or not campaign['paused']:
            raise ValueError('Pause the campaign before migration')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','uncertain')").fetchone():
            raise ValueError('Resolve pending or uncertain jobs before migration')
        config=json.loads(campaign['config'])
        if hashlib.sha256(campaign['config'].encode()).hexdigest()!=campaign['digest']:
            raise ValueError('Campaign approval digest differs')
        return {'campaign_id':config['campaign_id'],'approval_digest':campaign['digest'],
                'paused':campaign['paused'],'expanded':campaign['expanded'],
                'requests':[dict(r) for r in db.execute('SELECT * FROM requests ORDER BY id')],
                'jobs':[dict(r) for r in db.execute('SELECT key,state,provider,thread FROM jobs ORDER BY key')],
                'incoming':[dict(r) for r in db.execute('SELECT id,rid,kind FROM incoming ORDER BY id')],
                'audit_count':db.execute('SELECT count(*) FROM audit').fetchone()[0]}
    finally: db.close()


def export(run, destination):
    run,destination=Path(run),Path(destination)
    if not (run/'live.sqlite3').is_file(): raise ValueError('Existing canonical ledger required')
    if destination.exists(): raise ValueError('Choose a new private backup directory')
    with locked(run):
        inspect(run/'live.sqlite3')
        destination.mkdir(parents=True,mode=0o700)
        backup=destination/'live.sqlite3'
        source=sqlite3.connect((run/'live.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)
        target=sqlite3.connect(backup)
        try: source.backup(target)
        finally: target.close();source.close()
        backup.chmod(0o600)
        report={'kind':'paused_outreach_migration_backup','sha256':fingerprint(backup),'ledger':inspect(backup)}
        (destination/'manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        return report


def verify(bundle):
    bundle=Path(bundle)
    report=json.loads((bundle/'manifest.json').read_text(encoding='utf-8'))
    if report['kind']!='paused_outreach_migration_backup' or fingerprint(bundle/'live.sqlite3')!=report['sha256']:
        raise ValueError('Backup hash mismatch')
    if inspect(bundle/'live.sqlite3')!=report['ledger']: raise ValueError('Backup identities differ')
    return report


def restore(bundle, run):
    # Operator must stop all hosted processes first; refuse any existing target.
    bundle,run=Path(bundle),Path(run)
    report=verify(bundle)
    run.mkdir(parents=True,exist_ok=True)
    with locked(run):
        if any(p.name!='worker.lock' for p in run.iterdir()):
            raise ValueError('Restore requires an empty target directory')
        with (run/'live.sqlite3').open('xb') as stream:
            stream.write((bundle/'live.sqlite3').read_bytes())
        (run/'live.sqlite3').chmod(0o600)
        if fingerprint(run/'live.sqlite3')!=report['sha256'] or inspect(run/'live.sqlite3')!=report['ledger']:
            raise ValueError('Restored ledger verification failed; keep service stopped')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['export','verify','restore']);p.add_argument('--run',type=Path);p.add_argument('--bundle',type=Path,required=True)
    a=p.parse_args()
    if a.command!='verify' and a.run is None:p.error('--run is required')
    result=export(a.run,a.bundle) if a.command=='export' else restore(a.bundle,a.run) if a.command=='restore' else verify(a.bundle)
    print(json.dumps(result,indent=2))
if __name__=='__main__':main()
