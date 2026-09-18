"""Owner-only, local operational signals. Never fetch Gmail to render status."""
from contextlib import closing
import shutil
import sqlite3
import time
import os
import json
from pathlib import Path


def operations(service, now=None):
    now = time.time() if now is None else now
    result = {'schema': 1, 'checked_at': now, 'database': 'unavailable', 'incidents': []}
    def incident(code, severity, action):
        result['incidents'].append(dict(code=code, severity=severity, action=action))
    try:
        state = service.status()
        with closing(sqlite3.connect(f'file:{(service.run/"live.sqlite3").as_posix()}?mode=ro', uri=True, timeout=2)) as db:
            counts = dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state'))
        result.update(database='available', uncertain_jobs=counts.get('uncertain', 0),
            blocked_jobs=sum(counts.get(k, 0) for k in ('rejected', 'cancelled')),
            last_scan=state['last_scan'], last_success=state['last_success'],
            consent='stored_not_verified' if state['mailbox_connected'] else 'not_connected',
            schedule_enabled=bool(state['enabled']), heartbeat=state['last_heartbeat'])
        if state['enabled'] and (not state['last_heartbeat'] or now-state['last_heartbeat'] > 360):
            incident('scheduler_stale', 'critical', 'Worker heartbeat is over 6 minutes old or absent. Check the host process; a healthy web page does not prove the worker is running.')
        if state['enabled'] and (not state['last_scan'] or now-state['last_scan'] > max(1800, service.interval*2)):
            incident('scan_overdue', 'warning', 'No successful mailbox scan within two intervals. Inspect worker status before enabling further work.')
        if state['error']:
            incident('run_stopped', 'critical', state['error']+' Follow the recovery guide; sends must never be retried blindly.')
        if state['active'] and state['last_attempt'] and now-state['last_attempt'] > 300:
            incident('run_interrupted', 'critical', 'Run active for over 5 minutes. Stop the worker, inspect locks and reconcile the ledger before recovery.')
        if counts.get('uncertain'):
            incident('send_uncertain', 'critical', 'Reconcile each intent with Gmail Sent using Message-ID, recipient and content. Do not recreate or resend unresolved jobs.')
        if not service.demo and not state['mailbox_connected']:
            incident('mailbox_disconnected', 'warning', 'The owner must connect the approved Gmail account before scheduling.')
        with service.db() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name='scan_profile'").fetchone():
                row = db.execute('SELECT seconds,calls,retries FROM scan_profile WHERE id=1').fetchone()
                result['scan_profile'] = dict(row) if row else None
    except (sqlite3.Error, OSError):
        incident('database_unavailable', 'critical', 'Check disk capacity and database access. Do not replace the live ledger with a backup.')
    try:
        disk = shutil.disk_usage(service.folder)
        result['disk_free_bytes'] = disk.free
        if disk.free < 100*1024*1024 or disk.free/disk.total < .1:
            incident('disk_low', 'critical', 'Less than 100 MB or 10% disk space remains. Pause work and expand or safely archive storage before writes fail.')
    except OSError:
        incident('disk_unavailable', 'critical', 'Check the persistent disk mount and permissions.')
    for folder in (service.folder, service.run):
        lock = folder/'worker.lock'
        if lock.exists() and now-lock.stat().st_mtime > 300:
            incident('lock_review', 'critical', 'Lock older than 5 minutes. Confirm the process is stopped before inspecting or removing it; age alone does not prove it is stale.')
    if (service.folder/'RECOVERY_HOLD').exists():
        incident('recovery_hold', 'critical', 'Restored workspace is quarantined. Reconcile provider outcomes and review identity/configuration before operator reactivation.')
    backup_dir = os.environ.get('OUTREACH_BACKUP_DIR')
    result['backup'] = {'configured': bool(backup_dir and os.environ.get('OUTREACH_BACKUP_KEY')), 'off_host_copy_verified': False}
    if not result['backup']['configured']:
        incident('backup_not_configured', 'warning', 'Operator must configure a separate recovery key and backup directory. No automatic backup is currently promised.')
    else:
        try:
            receipt = json.loads((Path(backup_dir)/'latest.json').read_text())
            result['backup']['created'] = receipt['created']
            if now-receipt['created'] > 90000:
                incident('backup_overdue', 'critical', 'No successful local backup within 25 hours. Check storage and the backup worker.')
        except (OSError, ValueError, KeyError):
            incident('backup_missing', 'critical', 'No successful backup receipt. Check storage and backup configuration.')
        incident('off_host_copy_needed', 'warning', 'Local backups share the host disk. An encrypted off-host copy and independently held recovery keys are required for disk-loss recovery.')
    result['status'] = 'critical' if any(i['severity']=='critical' for i in result['incidents']) else 'warning' if result['incidents'] else 'ok'
    return result
