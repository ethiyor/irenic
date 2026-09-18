# Operations and recovery, milestone 28

This is a single-process, single-disk pilot. Web liveness is not worker readiness.
Every outgoing procurement request, reminder and forward still requires exact-message approval.

## Detection and escalation

Owners see an incident summary on every workspace view and detailed diagnostics in Settings.
`GET /api/operations` requires an owner session and the selected workspace. Reviewers and approvers cannot access it.
`/healthz` is deliberately cheap and contains no identities; it can remain healthy when the scheduler or database is not.

Critical: missing/enabled-worker heartbeat after 6 minutes, interrupted run after 5 minutes, uncertain send, database failure, disk free below 100 MB or 10%, missing backup or backup older than 25 hours. Owner investigates immediately and suspends preparation if needed. Warning: mailbox disconnected, scan older than two intervals, backup not configured, off-host copy unverified. Resolve before the next required run. In-app alerts require an open session; no email, SMS or external uptime monitor is configured by this release.

Stored consent is not proof of valid consent. The next authenticated mailbox operation verifies it. Refresh failures stop scheduling. Never show provider responses or token contents in an incident report.

## Recovery actions

| Incident | Action |
| --- | --- |
| Expired/revoked consent | Owner reconnects the same approved sender in Settings, checks the mailbox, reviews outcomes, then explicitly enables scheduling. |
| Read timeout/429/5xx | Reads retry at most twice after 1 and 2 seconds; persistent failure disables scheduling. Inspect the quota/connection, wait for recovery, run a fresh mailbox check, then resume. 403 stops conservatively; permission and quota failures cannot always be distinguished without provider inspection. |
| Interrupted scan | Check that the old process has stopped. Preserve the database and evidence; inspect active state and lock ownership. Repeat a full scan after recovery. Incoming rows and forwards are deduplicated by durable keys. |
| Stale-looking lock | Age is only an alert. Confirm no worker/request owns it before removing a lock. Record process, time and reason. Never automatically expire or delete a lock. |
| Uncertain send | Query Gmail Sent by recorded Message-ID; verify From, To, subject, MIME content and provider evidence. The existing reconciliation path accepts an exact match. Zero or multiple matches remain blocked; do not mark pending, recreate, or resend. |
| Disk full | Pause scheduling; check the persistent mount. Expand storage or move verified encrypted archives off-host before removing redundant copies. Never delete campaign/evidence databases to regain space. |
| Database unavailable | Stop mutations, preserve originals, investigate disk/permissions/integrity. A restore must use a new isolated directory, never overwrite the live tree. |

## Bounds and scan profile

Mailbox clients enforce a 120-second elapsed read budget between network/read chunks, at most 30 seconds per network operation and a 24 MB response limit. Token refresh uses a 30-second transport timeout; the authentication library may retry transient refresh failures. These are cooperative I/O bounds, not a hard process-kill deadline. Host process/OS failures still need external monitoring.

Scheduler cycles rotate the first workspace and stop admitting another workspace once 150 seconds have elapsed. A running workspace finishes or fails its bounded operation. No concurrent tenant workers are introduced. Last scan seconds, read attempts and retries are persisted without email content. Full overlapping history scans, spam/trash, bounces, cross-thread references, deduplication and immediate pre-send scans remain intact. No incremental Gmail history optimization was made without production profiling.

## Backup configuration and custody

Configure `OUTREACH_BACKUP_DIR` outside both `OUTREACH_RUN` and `OUTREACH_STATE`, for example `/var/data/encrypted-backups`. Set `OUTREACH_BACKUP_KEY` to a separately generated Fernet key. Do not reuse the runtime encryption secret. Keep the backup recovery key and the original `OUTREACH_ENCRYPTION_KEY` in separately controlled secure custody, outside the backup directory and Git. The runtime key is required to decrypt saved mailbox tokens and derive personal-workspace keys after recovery.

When configured, the scheduler attempts one complete snapshot daily; failed attempts back off for an hour. All campaign/service locks and the membership-registry lock are held during copying. SQLite's backup API includes committed WAL state. Each artifact is an authenticated encrypted ZIP containing all files under the campaign and service roots, including personal campaigns, service databases, stored evidence, membership registry and encrypted mailbox tokens. The encrypted manifest includes application configuration, Google web-client configuration, roles, inventory hashes, database table inventories and backup schema 1. Runtime and backup encryption keys are excluded. Lock files and SQLite sidecars are excluded because committed content is already in the database snapshots. Symlinks fail closed. Memory is capped at 64 MB of uncompressed state; exceeding that requires a streaming backup design before growth.

Seven successful encrypted versions are retained. Retention never removes other files. Copy an encrypted artifact off-host and compare its SHA-256 to `latest.json`; local disk copies alone cannot recover a lost Render disk. No off-host destination or unattended transfer is assumed. The UI explicitly displays that limitation. Proposed pilot targets are at most 24 hours of data loss and four-hour operator recovery, subject to owner acceptance and a working off-host schedule. They are not an achieved SLA.

## Isolated restore drill

1. Keep production untouched. Make the immutable encrypted archive available on an isolated machine with outbound network blocked. Supply `OUTREACH_BACKUP_KEY` privately in the environment, never a command argument or log.
2. Run `python pipeline/outreach_backup.py restore ARCHIVE NEW_DIRECTORY`. Existing destinations are rejected. Wrong keys, tampering, path traversal, inventory/hash mismatches and incompatible backup schemas fail closed.
3. The tool verifies all files and database integrity, validates campaign config digests, rebinds service identities to the isolated roots, disables all schedules, pauses every campaign, clears browser/OAuth/email-login sessions and adds `RECOVERY_HOLD` to every restored service. The hosted service refuses mailbox access, approval actions and scheduling while that hold exists. Original approved jobs, sent IDs, uncertain states and evidence remain intact.
4. Inspect shared and personal identities, roles, hashes and evidence. Confirm no network/provider call occurs. Record duration, file/workspace counts and failures. Keep the encrypted archive unchanged for comparison; quarantined database hashes intentionally differ after disabling schedules/sessions.
5. Before any future production recovery, stop the current host and capture its latest ledger. Reconcile **all** sent/uncertain/pending jobs and the interval after the snapshot against provider evidence, including sends missing from an older backup. Never use an old pending job as proof that no send occurred. Unresolved outcomes remain held.
6. Restore runtime keys/config in secure custody, verify sender and membership, explicitly approve reactivation, and only then remove recovery holds and restore intended schedule states. Deploy the compatible pinned source. Never point a running service at an old restored ledger.

## Compatibility and rollback

The only additional database table is optional `scan_profile`; preceding code ignores it. No destructive migration is introduced. Backup schema 1 and configuration `workspace_schema=1` are recorded. The reviewed public dataset remains snapshot `3e946cbceb28d366c33fcab17ad9231924e9a6ee64a72a6ade8b27fb1c67945f` and is not changed by recovery work. Record the exact deployed Git commit with each release. Source rollback to milestone 26 is possible with the current live databases, but old code does not enforce `RECOVERY_HOLD`, so never run old code against an isolated restore.

The browser CSP now receives a fresh random nonce for each response. Prior exposed OAuth-secret rotation is still unverified; this release does not claim Google verification or credential rotation is complete. Never include secrets, original email, database files or encrypted recovery keys in source, logs or CI artifacts.
