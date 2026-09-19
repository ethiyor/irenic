# Source monitoring: owner and reviewer guide

Open the Analyst Workspace, select **LionMail campaign**, then **Source monitor**.

1. Owner: choose Seasonal (daily June–August, Monday otherwise), Daily or Weekly; press **Enable / save schedule**. Checks are eligible at 09:00 America/New_York; a healthy host executes them one at a time. First activation checks sources that have never been checked.
2. **Check sources once** queues a bounded pass immediately, including while scheduled monitoring is paused. Reload the monitor after a few minutes to see all results. Repeated requests are limited to once per 15 minutes.
3. **Pause source monitor** cancels waiting checks independently of the Gmail schedule. One active read may finish. Its result does not publish data or send mail.
4. Inspect last attempt, last success and failures separately. A heartbeat older than three minutes while enabled indicates a worker problem. A failed check is not an unchanged source. Inspect the official page manually when an adapter fails; do not bypass source controls.
5. Open **Discovery & review queue**. A new or changed PDF has a hash-checked evidence download and retrieval receipt. PDF structure validation is not a malware certification or validated extraction. A listing-baseline record establishes which relevant links were visible; removed links do not prove rescission.
6. Owner: record an evidence-based hold, exclusion or handoff reference. Handed off means an operator has taken responsibility; it does not mean extracted or published. Save history and the source hash with the handoff. Reviewers/approvers have read/download access only.
7. Operator: use the existing government-source catalog/extraction process with the downloaded, hash-bound evidence. Verify source/season/units/supplier/quantities, revisions and overlaps, and run dataset controls. Government discoveries do not masquerade as email attachments in the private Research importer. Accept source facts and publication separately through the snapshot gate; preserve the current snapshot until that succeeds. Update the monitor's reviewed-source projection only as a separate audited baseline migration after acceptance.
8. For cache/full-disk incidents, pause monitoring, export and verify the source evidence plus database in an owner-controlled archive, and plan retention/capacity before reactivation. Do not delete sole evidence copies. The source cache has a deliberate 24 MiB ceiling. Production off-host backups remain a separate pending setup item.

No external incident notifications are configured; status and receipts appear in the shared web workspace. No Gmail connection is required to read source findings. The monitor has no campaign send or public deployment capability.

State location: `<OUTREACH_STATE>/source-monitor/monitor.sqlite3`, immutable content-addressed files under `objects/`. These live inside the private persistent disk and complete-backup roots. Restores remain under `RECOVERY_HOLD`; reconcile the recovery before enabling network work.

Starting official listing adapters:

- [Michigan bulk rock salt](https://www.michigan.gov/dtmb/procurement/mideal-extended-purchasing-program/mideal-contract-search/categories/folder-2/salt-bulk-rock)
- [Pennsylvania COSTARS member information](https://www.pa.gov/agencies/dgs/programs-and-services/costars/member-information)

The dashboard review date is not the source-monitor retrieval date. A new check does not make old data newly reviewed.
