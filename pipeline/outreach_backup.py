"""Encrypted complete-state snapshots and quarantined restores. No mail APIs."""
import argparse
from contextlib import ExitStack, closing
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import zipfile
from cryptography.fernet import Fernet
from outreach_live import locked

SCHEMA = 1
LIMIT = 64*1024*1024


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(roots, config, key, destination):
    """Caller holds all workspace/registry locks, or has stopped the service."""
    cipher = Fernet(key)
    destination = Path(destination).resolve()
    roots = {k: Path(v).resolve() for k,v in roots.items()}
    if set(roots) != {'campaign', 'service'} or any(destination.is_relative_to(p) for p in roots.values()):
        raise ValueError('Backup destination must be outside both complete state roots')
    manifest = dict(schema=SCHEMA, created=time.time(), roots={k:str(v) for k,v in roots.items()}, config=config, files={}, sqlite={})
    buffer = io.BytesIO()
    total = 0
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        for label, root in roots.items():
            for path in sorted(root.rglob('*')):
                if path.is_symlink():
                    raise ValueError('Symlinks are not supported in private state')
                if not path.is_file() or path.name in {'worker.lock', 'host.pid'} or path.name.endswith(('-wal','-shm','-journal')):
                    continue
                name = label+'/'+path.relative_to(root).as_posix()
                if path.suffix == '.sqlite3':
                    with tempfile.TemporaryDirectory() as tmp:
                        with closing(sqlite3.connect(f'file:{path.as_posix()}?mode=ro', uri=True, timeout=5)) as src, closing(sqlite3.connect(str(Path(tmp)/'copy.sqlite3'))) as dst:
                            src.backup(dst)
                            if dst.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                                raise ValueError('Database integrity check failed')
                            manifest['sqlite'][name] = [r[0] for r in dst.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
                        if (Path(tmp)/'copy.sqlite3').stat().st_size > LIMIT:
                            raise ValueError('Database exceeds pilot backup limit')
                        data = (Path(tmp)/'copy.sqlite3').read_bytes()
                else:
                    if path.stat().st_size > LIMIT:
                        raise ValueError('Backup exceeds pilot memory limit')
                    data = path.read_bytes()
                total += len(data)
                if total > LIMIT:
                    raise ValueError('Backup exceeds pilot memory limit; operator must provision streaming backup')
                manifest['files'][name] = dict(sha256=digest(data), bytes=len(data))
                archive.writestr(name, data)
        archive.writestr('manifest.json', json.dumps(manifest, sort_keys=True))
    destination.parent.mkdir(parents=True, exist_ok=True)
    encrypted = cipher.encrypt(buffer.getvalue())
    pending = destination.with_suffix('.pending')
    with pending.open('xb') as f:
        os.chmod(pending, 0o600)
        f.write(encrypted)
        f.flush()
        os.fsync(f.fileno())
    os.replace(pending, destination)
    return dict(schema=SCHEMA, created=manifest['created'], files=len(manifest['files']), bytes=len(encrypted), sha256=digest(encrypted))


def restore(source, key, destination):
    """Never overwrite a directory. Authenticate, check paths/hashes, then quarantine."""
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Restore requires a new isolated directory')
    if Path(source).stat().st_size > LIMIT*2:
        raise ValueError('Encrypted archive exceeds pilot restore limit')
    payload = Fernet(key).decrypt(Path(source).read_bytes())
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        manifest = json.loads(archive.read('manifest.json'))
        if manifest['schema'] != SCHEMA:
            raise ValueError('Unsupported backup schema')
        if len(archive.namelist()) != len(set(archive.namelist())) or set(archive.namelist()) != set(manifest['files']) | {'manifest.json'}:
            raise ValueError('Backup inventory mismatch')
        total = 0
        for name, expected in manifest['files'].items():
            parts = name.split('/')
            if parts[0] not in {'campaign','service'} or any(p in {'','..','.'} or '\\' in p or ':' in p for p in parts):
                raise ValueError('Invalid backup path')
            total += archive.getinfo(name).file_size
            if total > LIMIT:
                raise ValueError('Restore exceeds pilot memory limit')
            data = archive.read(name)
            if digest(data) != expected['sha256'] or len(data) != expected['bytes']:
                raise ValueError('Backup hash mismatch')
        destination.mkdir(parents=True, mode=0o700)
        for name in manifest['files']:
            target = destination/name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
            os.chmod(target, 0o600)
        (destination/'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        os.chmod(destination/'manifest.json', 0o600)
    # Verify before any intentional quarantine changes. Keep archive immutable.
    for name in manifest['sqlite']:
        path = destination/name
        with closing(sqlite3.connect(path)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
                raise ValueError('Restored database failed integrity check')
            tables = manifest['sqlite'][name]
            if 'settings' in tables:
                old_run = Path(db.execute('SELECT run FROM identity WHERE id=1').fetchone()[0])
                matches = [(label, old_run.relative_to(Path(root))) for label,root in manifest['roots'].items() if old_run.is_relative_to(Path(root))]
                if len(matches) != 1:
                    raise ValueError('Restored service identity is outside the recorded roots')
                label, relative = matches[0]
                db.execute('UPDATE identity SET run=? WHERE id=1', (str((destination/label/relative).resolve()),))
                db.execute('UPDATE settings SET enabled=0,next_run=NULL,active=0')
                for table in ('sessions','oauth','email_login'):
                    if table in tables:
                        db.execute('DELETE FROM '+table)
                (path.parent/'RECOVERY_HOLD').write_text('No provider access. Operator reconciliation required.\n')
            if 'campaign' in tables:
                for raw, approved in db.execute('SELECT config,digest FROM campaign'):
                    if digest(raw.encode()) != approved:
                        raise ValueError('Restored campaign identity/configuration mismatch')
                db.execute('UPDATE campaign SET paused=1')
            db.commit()
    (destination/'RECOVERY_HOLD').write_text('Isolated restore. Never point production at this tree before reconciliation.\n')
    return dict(schema=SCHEMA, verified_files=len(manifest['files']), outbound_disabled=True)


def automatic(workspaces):
    """Optional daily backup; independent recovery key is required. Local disk only."""
    folder, key = os.environ.get('OUTREACH_BACKUP_DIR'), os.environ.get('OUTREACH_BACKUP_KEY')
    if not folder or not key:
        return
    if time.monotonic()-getattr(workspaces, '_backup_attempt', float('-inf')) < 3600:
        return
    folder = Path(folder)
    receipt = folder/'latest.json'
    if receipt.exists() and time.time()-receipt.stat().st_mtime < 86400:
        return
    workspaces._backup_attempt = time.monotonic()
    with ExitStack() as stack:
        if workspaces.cfg.get('personal_workspaces'):
            stack.enter_context(workspaces.lock)
        for _, service in sorted(workspaces.services.items()):
            stack.enter_context(locked(service.folder))
            stack.enter_context(locked(service.run))
        cfg = workspaces.cfg
        # Encryption keys are held separately. Web client/roles stay inside encryption.
        config = {k: v for k,v in cfg.items() if k not in {'key','run','state','testing'}}
        config['runtime_key_required'] = True
        config['source_commit'] = os.environ.get('RENDER_GIT_COMMIT', 'unrecorded')
        config['workspace_schema'] = 1
        path = folder/('state-'+str(time.time_ns())+'.fernet')
        result = snapshot({'campaign':cfg['run'], 'service':cfg['state']}, config, key, path)
        result['off_host_copy_verified'] = False
        receipt.write_text(json.dumps(result), encoding='utf-8')
        # Retain latest seven successful encrypted versions. Never remove other files.
        for old in sorted(folder.glob('state-*.fernet'), key=lambda p:p.stat().st_mtime, reverse=True)[7:]:
            old.unlink()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['restore'])
    parser.add_argument('archive')
    parser.add_argument('isolated_destination')
    args = parser.parse_args()
    print(json.dumps(restore(args.archive, os.environ['OUTREACH_BACKUP_KEY'], args.isolated_destination)))
