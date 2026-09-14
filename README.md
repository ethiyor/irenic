# Milestone 14 — authenticated backend and scheduled operation

The implementation is prepared for a **private GitHub repository → Render web service**. Cloud deployment and real Google Web OAuth verification are not yet complete. The public dashboard is unchanged.

## What is implemented

- Google authorization-code sign-in with PKCE, one-use state bound to the browser session, nonce verification, verified email, and an explicit owner/reviewer allowlist. Roles are checked server-side on every request. Session cookies are HttpOnly, Secure and SameSite=Lax; mutations require an Origin and CSRF check.
- An owner-only mailbox connection requests Gmail read/send access and offline authorization. Refresh credentials are encrypted with a deployment secret, kept on the private persistent disk, and never returned through an API. Revocation or refresh failure stops scheduled sending. Disconnect deletes locally stored authorization and disables scheduling; it does not revoke the application's grant at Google.
- A scheduler checks eligibility every 15 seconds and runs approved work every 15 minutes after completion. It records heartbeat, last attempt, successful mailbox scan, successful run, next eligibility, and failure. The cadence is visible in the analyst page.
- Existing pilot gates, reply holds, bounded reminders, uncertain-send holds, and request/forward deduplication remain in force. Scheduling and live sending are separate gates. The UI cannot change the deployment's live-send gate or expand contacts.
- A local OS-exclusive lock serializes scheduled runs and analyst mutations on the single host. Durable scheduler state survives restart; incomplete runs stop scheduling for operator review. The process allows an in-progress run up to 25 seconds to finish during graceful shutdown. A forced kill may leave a lock and requires investigation, never automatic resend.
- The analyst interface shows schedule and mailbox-connection controls to owners; reviewers can inspect status and classify matched replies. Named actions are recorded. Mail text is rendered as text; attachments remain in the existing private intake pipeline, outside the web application.

## Architecture and limits

One Render web service runs the Waitress application and scheduler in the same process. Both access the existing SQLite ledger plus a separate service-state database on `/var/data`. This is deliberately **one instance, one host, persistent disk**. Do not use ephemeral storage, multiple replicas, another background worker, or a Render cron job for this database. Migrate to a shared transactional database and distributed job claims before scaling.

Render documents that [persistent disks require paid services](https://render.com/docs/disks), [free web services sleep and have no persistent disk](https://render.com/docs/free), and [cron jobs cannot access a persistent disk](https://render.com/docs/cronjobs). The blueprint uses one `0.5c-512mb` service and a 1 GB disk, with automatic deployments off. Confirm the exact charge shown by Render before creating paid resources; no purchase is included in this milestone.

This deployment provides scheduled execution but not guaranteed real-time delivery. The scheduler stops when the host is stopped, restarting, or unavailable. The UI exposes failures; external uptime paging and high availability are not implemented. A successful worker run while outreach is paused means the mailbox scan completed; it does not mean mail was sent.

## GitHub and Render setup

1. Upload only the source package produced by `pipeline/package_hosted.py` into a new **private** GitHub repository. Do not upload the workspace wholesale: it contains private correspondence, downloaded credentials, and the live ledger. The package has a manifest and explicit source allowlist.
2. In Render, create a Blueprint from that repository. Review the plan and disk cost before applying. The blueprint defaults to **demo**, live sending **false**, schedule **disabled**, one instance.
3. Obtain the service's assigned HTTPS `onrender.com` URL and set `OUTREACH_ORIGIN` to that exact origin, without a trailing slash. The first service start may need to wait until this value and the OAuth client are configured.
4. In the existing Google project, create a separate **Web application** OAuth client. Keep the Desktop client for the local worker. Register exactly `https://YOUR-SERVICE.onrender.com/oauth/callback` as an authorized redirect URI. The backend's Google flow follows the [web-server OAuth documentation](https://developers.google.com/identity/protocols/oauth2/web-server).
5. Store that Web client's full JSON in Render's secret environment variable `OUTREACH_GOOGLE_WEB_CLIENT`. Never commit it. Store `OUTREACH_ANALYSTS` as an explicit JSON mapping such as `{"your-approved-address@example.com":"owner"}`. Ryan being a forwarding recipient does not automatically give him analyst access.
6. Retain the generated `OUTREACH_ENCRYPTION_KEY` across redeployments. Losing or changing it makes existing credentials unreadable. Back up that secret separately from the database using the hosting account's protected secret management.
7. Deploy, sign in with the allowed account, and enable the **demo** schedule. After the first scheduled request, add the simulated reply. A later run records and forwards it in the fake mailbox. Classify it and confirm repeat runs leave the count at two. Pause/resume and redeploy; verify the ledger and scheduler history remain intact. No Gmail connection is available in demo mode.

Google's [OAuth token expiration rules](https://developers.google.com/identity/protocols/oauth2) can make offline tokens expire after seven days for external apps in Testing using Gmail scopes. Account/admin policies also apply. If the token expires or access is revoked, this worker stops and requires reconnection; do not promise permanent authorization.

## Move the real pilot only after hosted demo acceptance

Do not initialize a second live campaign: it could resend Lancaster's already-sent request.

- Stop local worker invocations and take a consistent backup of the canonical live ledger while holding its worker lock. Include the sent-job history, incoming records, campaign approval and audit. Record hashes and counts. Keep this backup private.
- Transfer the backup through the hosting provider's authenticated private channel, never GitHub, email attachments, or public URLs. Restore to `/var/data/pilot/live.sqlite3`. Verify the sole Lancaster initial send and the four disabled contacts before proceeding.
- Set the run path to `/var/data/pilot`, service state to `/var/data/pilot-service`, and mode to `live`. Keep `OUTREACH_LIVE_ENABLED=false` and the schedule disabled during verification. Do not switch back to the local worker after cutover.
- Sign in as the approved owner and connect the approved Gmail sender. This step explicitly grants stored offline authorization to the hosted server. Verify the connection and run a controlled mailbox exercise before unattended real sends. Live Lancaster reply/forward acceptance is still pending and must not be marked complete by the demo.
- Only after that check, authorize the deployment live-send gate and enable the schedule. The original pilot expansion gate still applies. Monitor the first scheduled run and the next reminder date.

There is no web endpoint for importing a database or bypassing approval. Storage migration and restoration remain operator steps. Never restore an older sent ledger into an active sender without reconciling Gmail, because that can reintroduce duplicate intent.

## Recovery

An error disables the schedule and displays a generic actionable message without token/provider-response contents. Check the mailbox authorization, campaign ledger and logs. Reconnect if necessary, then enable scheduling only after reviewing the failure. An `uncertain` job remains held by the worker.

For a forced-kill lock, first stop the service and prove no worker is active. Preserve the ledger and reconcile uncertain jobs; only then remove the stale lock and clear the service `active` flag as an audited operator recovery. The UI intentionally has no blind recovery/retry button. Service and worker locks are separate files under their respective directories.

## Validation

Hosted tests cover authentication boundaries, CSRF and roles, state/PKCE/nonce/replay handling, unknown-user rejection, encrypted storage, disconnect, scheduled pause/resume, restart persistence, duplicate prevention, overlapping runs, interrupted runs, failure reporting, and the separate live gate. They use fake credentials/provider responses, not real Google or Render accounts. Cloud OAuth, HTTPS deployment, live credential refresh, and hosted process restart still require integration verification.

Run the backend suite with the hosted dependencies installed:

```powershell
python -m unittest discover -s pipeline -p test_outreach_hosted.py
python -m unittest discover -s pipeline -p test_outreach_live.py
python -m unittest discover -s pipeline -p test_outreach_console.py
```

The browser/API exposes private mail metadata only after sign-in. No mail, credentials, private data, or cloud resources were transmitted or activated while implementing this phase.
