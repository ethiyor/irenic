# Improvement milestones 23–32

Planning baseline: September 18, 2026. Milestones 23–26 are now implemented and deployed; milestones 27–32 remain proposed. Milestone 26 retains manual native screen-reader and true 200% zoom acceptance checks, documented in its report. This plan follows milestones 21–22 and does not renumber historical work. Milestone 20 remains the prior publication objective and is incorporated into milestone 29 where applicable.

Read the supporting [product and workflow audit](PRODUCT-WORKFLOW-AUDIT-2026-09-18.md) for evidence, requirements mapping, confirmed defects, test results and uncertainty.

## How to use this plan

Implement one numbered milestone per request. At the start, inspect the actual deployed version and current worktree, then record scope, baseline and expected behavior. At the end, record what changed, checks performed, remaining limits, deployment status and rollback instructions in a dated milestone report. Do not mark a milestone complete based only on screenshots or a test count.

This plan does not itself authorize new messages, wider membership, paid services, publication of private documents, or broad public rollout. Routine local implementation and tests should use disposable fixtures. Preserve the real five-office ledger and existing approval requirements. Recheck deployment authorization and unrelated pending changes before shipping.

Effort ranges are preliminary engineering estimates, not commitments or a proposed bill. They exclude waiting for users, government replies, Google review and external security assessment. Ryan's original request was intentionally small; the full backlog is not necessary to call the pilot delivered.

| Milestone | Outcome | Priority | Dependencies | Initial estimate |
| --- | --- | --- | --- | --- |
| 23 | Accurate permissions and current baseline | Immediate | None | 2–4 hours |
| 24 | Read replies and documents inside the workspace | Next | 23 | 6–12 hours |
| 25 | Clear analyst journey, states and activity | Next | 23; integrates 24 | 4–8 hours |
| 26 | Consistent, accessible visual design | Polish | 24–25 | 4–8 hours |
| 27 | Consistent dashboard filters, scope and exports | Research usability | 23; reuse 26 styles | 5–10 hours |
| 28 | Recovery, backups and observable operations | Reliability | 23–25 | 8–16 hours |
| 29 | Reviewed document-to-publication workflow | Original broader objective | 24–25, 27–28 | 8–16 hours |
| 30 | Scheduled government-source monitoring | Original broader objective | 28–29 | 6–12 hours |
| 31 | Team acceptance and maintainable handoff | Release gate | Relevant shipped milestones | 3–6 hours |
| 32 | Organization ownership and optional wider access | Conditional | Team decision; 28, 31 | Scope after decision |

**First stopping point:** finish 23–25 and observe Ryan/Shafkat using the existing pilot. Do not automatically proceed through every row. Visual polish can be timeboxed after this review. Run milestone 31 as an acceptance checkpoint for an early release as well as after later phases.

## Invariants for every milestone

- Only accepted observations enter public totals. The active snapshot is the single source; pilots and archived revisions are never appended again.
- Keep MI/PA separate, missing distinct from zero, source units unresolved where appropriate, and the Compass $75.72/$78.96 discrepancy traceable.
- Preserve the known five requests, six provider-accepted campaign jobs and Lancaster response identities unless new actual events legitimately change them. Authentication messages are separate.
- No unsolicited procurement test messages. Fake-clock, failure and duplicate-send tests use fake providers; controlled real-mail tests require an explicitly selected sender/recipient and purpose.
- A pending or edited draft is not an approval. Exact content, attachments, sender, recipient and campaign identity must be bound to approval. A newly received reply can cancel a reminder before send.
- An uncertain network send must not be retried blindly. Provider acceptance is not recipient delivery or reading.
- Review-only membership cannot mutate shared campaign data. Personal workspace ownership must never confer shared campaign ownership.
- Raw mail, private attachments, tokens, personal contact details and internal notes remain outside public assets, build output and logs.
- Keep the single-process deployment while it meets measured needs. Do not add infrastructure or change data stores purely for appearance.

## Milestone 23: Fix role enforcement and establish a trustworthy baseline

**Status (September 18, 2026):** implemented and verified live at commit `7e08b53`; 68 isolated backend tests and three-role browser checks passed. GitHub main merge completed after explicit user approval. See [release receipt](milestone-23/README.md) and [current status](CURRENT-STATUS.md).

**Outcome:** the permissions we describe match the backend and the interface. This is a targeted correctness release, not a redesign.

**Work**

1. Replace the special-case `classify` exception in `pipeline/outreach_hosted.py` with an explicit command/role matrix. Default to deny unknown commands. Keep reviewer read-only in the shared campaign; owner retains classification; approver classification should be explicitly specified rather than inherited accidentally. Recommended baseline: approvers approve/reject prepared messages but do not edit records or classifications.
2. Hide classification form controls for roles that cannot classify; return 403 for direct API attempts. Retain read access to the classification and evidence note.
3. Extend tests across every mutating command for reviewer/approver/owner, expired sessions, wrong workspace and forged workspace identifiers. Reproduce the current HTTP-200 reviewer-classification bug as a regression test before fixing it.
4. Correct the earlier Shafkat read-only description and stale README/handoff claims. Keep dated historical reports; add a current-status entry point with source commit, deployment ID, dataset snapshot, roles and unresolved issues.
5. Separate immutable historical records from current status. Remove or replace misleading legacy `scheduler: Not configured` and `live_reply_validation: Pending` fields in hosted responses without inventing new acceptance evidence.
6. Record application/repository boundaries and pending local changes. Test the deployed baseline plus this isolated patch. Do not bundle the pending Google ownership tag automatically.

**Likely files:** `pipeline/outreach_hosted.py`, `outreach_hosted.html`, `outreach_console.py`, `test_outreach_hosted.py`, `test_workspaces.py`, root `README.md`, current access documents; corresponding backend checkout files.

**Done when**

- Reviewer classification, sends, edits, rejection, contact creation, scheduling and mailbox changes all fail server-side in the shared campaign, while reading still works.
- Owner/approver allowed operations remain functional and no cross-workspace regression occurs.
- A role-appropriate browser fixture shows no misleading controls. No real mail is sent.
- The shipped commit/config and active dataset are recorded in one current status page.

**Rollback:** restore the prior release only for an unrelated operational failure and clearly flag the reviewer limitation; do not knowingly reintroduce the permission defect as an acceptable end state. Keep production data untouched.

## Milestone 24: Make replies and attachments reviewable

**Status (September 18, 2026):** implemented and merged at `446a0a1`; all 72 backend tests pass; real Lancaster reply and four hash-matched PDF manifests verified through the deployed evidence view. See [release report](milestone-24/README.md).

**Outcome:** an authorized colleague can inspect the Lancaster response and its four documents without entering the owner's mailbox.

**Work**

1. Add a request conversation/evidence view with subject, sender, received time, matched request, original-message identity and classification history.
2. Support HTML-only mail safely. Prefer extracted readable text with an explicit fallback, or a maintained HTML sanitizer and isolated renderer. Disable scripts, forms, remote images, tracking resources and active content. Preserve original MIME unchanged.
3. Add an attachment manifest showing name, size, detected type, hash, review state and supported preview/download action. Resolve files by opaque record IDs through server-side workspace membership checks, never a browser-supplied filesystem path.
4. Allow authorized reviewers to inspect/download the matched campaign evidence only. Keep responses private/no-store; sanitize filenames and content disposition; impose size/decompression limits and quarantine unsupported types. Do not execute document macros or open active HTML inline.
5. Surface the existing Lancaster document review: road award supported; water-treatment document excluded; annual quantity mapping held. A readable attachment does not equal an accepted dataset row.
6. Show clear unsupported/corrupt/large-file states. Keep a restricted original `.eml` download as an evidence fallback, not the default review experience.

**Likely files:** `outreach_console.py`, `outreach_hosted.py/html`, existing intake storage interfaces and new narrow evidence-view modules. Add no public source link for private correspondence.

**Done when**

- A reviewer fixture can read an HTML-only reply and list/inspect its associated PDFs; no owner's Gmail access is needed.
- Anonymous and other-workspace file requests fail. Malicious HTML and filenames cannot execute or escape the intended route.
- Attachment bytes/hash round-trip unchanged; interrupted downloads and missing files have useful errors.
- The real stored Lancaster record is inspected read-only through the deployed view after release; no re-import duplicates or outgoing messages are generated.

**Rollback:** feature-flag the new evidence UI/routes while retaining originals and existing metadata. Never delete evidence to undo a preview change.

## Milestone 25: Organize work around the analyst's next action

**Status (September 18, 2026):** implemented, merged and live at `9080c18`, with 75 backend tests passing and a local reviewer/owner walkthrough. A stale-lock deployment incident was recovered with backups and audit; schedule restored. Live release verification is recorded in [milestone 25](milestone-25/README.md).

**Outcome:** a user understands the workspace, current work and next step without reading operational logs.

**Work**

1. Default invited collaborators to the shared campaign; optionally remember their last permitted workspace. Keep an empty personal workspace available but secondary. Do not change permission scope to solve navigation.
2. Organize Overview, Requests, Inbox & documents, Approvals, Activity and Settings using small sections/tabs. Put unreviewed replies and pending approvals before schedule configuration.
3. Define a common state model: request queued/waiting/held/suppressed/closed; outgoing draft pending/rejected/cancelled/uncertain/sent; document received/needs review/held/excluded/accepted; publication candidate/published. Show reasons and owning actor where appropriate.
4. Correct the held count. Count held, disabled and suppressed records separately, or label the exact existing count honestly. Distinguish requests sent, reminder sends and forwarded responses. Cards should open the records that explain them.
5. Make “no classification needed” different from “all documents reviewed.” Show the Lancaster annual-allocation hold as outstanding research even though its reply was classified.
6. Add request-level timelines joining contact provenance, request, reply, forward, review and publication references. Merge relevant service events and ledger events in display without rewriting historical audit records.
7. Add clear status freshness, request deadlines/timeouts and recovery messages. Poll only safe read endpoints at a modest visible-page interval; do not overwrite unsaved edits. Pause polling when hidden and signal stale data.
8. Show sender and selected workspace prominently for approval; disable actions with a reason. Keep privileged settings hidden from reviewers. Give new personal users a short setup checklist and a clear empty state.

**Done when**

- Shafkat's fixture lands in populated shared work, identifies the Lancaster reply and understands that it is awaiting annual mapping.
- Counts reconcile with underlying rows; zero held cannot coexist with a held record under the same definition.
- A cancelled/rejected/stale draft has an understandable outcome. UI timeout cannot cause automatic send retry.
- Users can locate a reply and an approval queue in a short observed walkthrough; measure time and misunderstandings rather than assuming success.

**Rollback:** preserve endpoints and identifiers; revert navigation/presentation without migrating the ledger destructively.

## Milestone 26: Apply a consistent, accessible visual system

**Outcome:** both products feel related and remain readable at ordinary laptop, mobile and zoom settings.

**Work**

1. Confirm actual theme behavior: the supplied dark screenshots differ from the light authored/rendered pages. Test a clean browser before changing palette. If a theme toggle is desired, implement and test explicit light/dark tokens; do not depend on browser inversion.
2. Define shared color roles, typography, spacing, border radii, tables, buttons, badges, form/error patterns and focus rings. Keep dashboard navy and workspace teal as restrained accents under a common brand.
3. Reduce oversized vertical sections. Use comfortable 14–16px body text and tabular numbers. Reserve amber/red for exceptions; routine live/connected status should be neutral.
4. Put a concise metric caveat beside each number and the full explanation in an accessible disclosure. Do not hide material coverage or approval information in hover-only tooltips.
5. Test keyboard order, form labels, screen-reader names, live status messages, contrast, reduced motion and target sizes. Provide contained horizontal table scrolling and readable controls at 390px; check 1280px laptop and 200% zoom.
6. Add loading/error/empty patterns with stable layout. Keep loading animation brief and tied to actual requests; never use animation to hide an indefinite failure.

**Done when**

- Both apps have a documented token/style reference and consistent key components.
- Measured normal-text contrast meets 4.5:1, large text 3:1, with WCAG target-size/focus checks and documented exceptions; no unsupported claim of full compliance.
- Screenshot and keyboard checks cover empty queue, pending approval, error, long recipient/filename, expanded evidence, missing dataset and selected chart/map states.
- No full-page horizontal overflow at tested widths; tables may scroll within labeled regions. Data and permission tests remain unchanged/passing.

**Rollback:** CSS/component-only reversal. Do not mix visual changes with sender, permission or publication changes.

## Milestone 27: Unify dashboard context and improve research comparisons

**Outcome:** a chart, card, map and export clearly describe the same chosen context, except where a deliberate comparison is labeled.

**Work**

1. Share state/program/fill between dashboard and map by default. Retain a trend range and one named focus year for cards/map. If independent map comparison is useful, make it an explicit mode with separate scope shown prominently.
2. Keep the share denominator across all eligible suppliers. Selecting/highlighting a supplier must not redefine its apparent share of the collected state volume.
3. Show the current filter scope beside exports, including whether table search applies. Include snapshot, units, missing counts and denominator metadata. Add a reproducible URL for safe public selections; exclude any private identifiers/tokens.
4. Retain the map but reduce dead space and add obvious MI/PA shortcuts. Unsupported years display missing coverage, not zero. Preserve keyboard state selection and whole-row details expansion without intercepting PDF-link clicks.
5. Add compact exact-value comparison tables before considering more charts. If adding year-over-year changes, show coverage/comparability qualifications and missing cohorts. Never imply actual demand or a pure price effect from a changing sample.
6. Separate last source check, data review and publication dates. Retain the acknowledged Compass discrepancy in a concise visible note with full calculation evidence available.
7. Where permitted, offer hash-identified reviewed source copies alongside official live links. Do not automatically publish attachments obtained by email.

**Done when**

- Choosing PA produces consistent PA context across default cards/map/details; changing focus year updates its declared views only.
- All 31 existing calculation/share tests pass plus explicit scope/URL interaction tests.
- MI Compass FY2026 remains 260,595 source tons and $75.72; PA FY2026 remains 757,483 known source tons for the unchanged snapshot.
- CSV/chart/table values reconcile under supplier, fill, program, missing-data and search cases. “Share” is always qualified as collected known volume.

**Rollback:** restore the previous view logic against the same accepted snapshot. No data rebuild is required solely for filter changes.

## Milestone 28: Make failure and recovery routine

**Outcome:** the owner can detect a stopped workflow and recover without losing evidence or repeating messages.

**Work**

1. Keep a cheap web liveness endpoint but add authenticated operational status: database availability, disk capacity, scheduler progress, last successful scan, consent health and blocked/uncertain jobs. Do not expose mailbox identities publicly.
2. Add actionable incident states and a recovery guide for expired consent, interrupted jobs, stale locks, Gmail quota failures, disk-full conditions and unresolvable sends. Retry safe reads with bounds/backoff; never generic-retry sends.
3. Bound each scheduled run and improve fairness across workspaces. Profile repeated mailbox scanning before changing it; preserve cross-thread references, bounces, spam/trash handling, deduplication and a full-rescan recovery path if adopting incremental history.
4. Create encrypted, versioned backups for the complete state: every campaign/service database, evidence, membership registry and configuration manifest. Keep key custody/recovery separate. Define retention and proposed recovery targets, e.g. up to 24 hours data loss and four-hour operator recovery for the pilot, subject to owner agreement.
5. Restore into an isolated environment with outbound mail disabled, verify identities and hashes, reconcile sent/uncertain jobs against provider evidence before any reactivation. Never restore an old ledger over a running service.
6. Add CI/reproducible commands for the two Python environments and dashboard. Pin deploy commits, record config schema/snapshot compatibility and keep data migrations reversible. Do not blindly sync root code over backend checkout changes.
7. Review fixed CSP nonce, private status minimization, credential rotation status and secrets in logs/artifacts. Replace fixed nonce with per-response values or a suitable script/style policy while preserving functionality.
8. Define owner alerts and escalation thresholds. Creating a real email/monitoring integration requires the selected destination and authorization; an in-app incident panel can be implemented first.

**Done when**

- Tests cover connection loss, timeouts, quota errors, restarts during scan/send, concurrent approvals, duplicate replies, expired approval digests and backup restoration with no duplicate-send outcomes.
- The operator completes a timed isolated restore drill and records achieved recovery, rather than simply declaring backups exist.
- A stopped scheduler is visible even if the web page is healthy; alert behavior is tested with a controlled fixture.
- Prior original/shared and personal workspaces remain isolated and their schedules retain intended state after deployment.

**Rollback:** use a tested source rollback compatible with current database schema. Restore data only through the documented stopped-service recovery procedure.

## Milestone 29: Connect document review to accepted publication

**Outcome:** the analyst can trace a received document to an accepted observation or a documented hold, then to a specific published snapshot.

**Work**

1. Expose the existing private intake/review model in the workspace. Distinguish document registration, extraction, field evidence, review decision and publication eligibility.
2. Link every candidate to request/message/attachment hash/page and extraction version. Surface weak OCR/text coverage; do not equate “some text extracted” with reliable extraction.
3. Show review choices with reasons: accept, hold, exclude, supersede. Preserve award versus losing quote, contract term versus fiscal year, ton basis, cooperative membership and potential overlap.
4. Keep Lancaster's $83 award and 2026–2028 range in a contract-evidence view. Do not divide the 6,694–20,269 range by two, choose a midpoint or assign FY2027 volume. Obtain adequate evidence or keep it held.
5. Produce an explicit candidate diff: records added/removed/superseded, affected volume and quotes, missing fields and overlap decisions. Review facts and publication separately; fixed-recipient approval must also apply to any clarification email.
6. Promote only reviewed public-eligible facts through the existing snapshot gate. Create a build/deploy receipt linking accepted snapshot and public output. Strip private correspondence, signatures/contact details where inappropriate, and internal notes from public material.

**Done when**

- A reviewer traces the real Lancaster evidence to its hold without guessing an annual volume.
- A synthetic fully supported candidate travels through validation, acceptance, build and rollback in isolation. A real candidate is published only if review establishes eligibility and publication is authorized.
- Reimporting a message or superseding a schedule cannot duplicate quantities. Rejected/held items never enter public totals.
- The active dataset can legitimately remain unchanged; zero new rows is an acceptable milestone result when evidence is insufficient.

**Rollback:** retain review history and restore a previous accepted manifest/projection through the release procedure. Do not erase source evidence.

## Milestone 30: Schedule public-source monitoring with a review queue

**Outcome:** new or changed government documents become visible research work during contracting season without silently replacing accepted data.

**Work**

1. Treat source monitoring as a separate job from Gmail scanning. Start with the existing MI/PA official sources and a small explicit schedule: proposed daily June–August checks, lower frequency outside the season, configurable after Ryan's preference.
2. Monitor relevant listing pages as well as current PDF URLs so a newly named FY2027/FY2028 file can be discovered. Add new hosts/adapters deliberately; retain download limits, official-host checks, hashes and retrieval evidence.
3. Queue changed/new documents for extraction and review; show last attempt, last success, change status and failure reason separately.
4. On 403, missing file, malformed PDF or network error, retain the accepted snapshot and mark the source check failed. Do not relabel it “unchanged,” overwrite with empty data, or bypass source controls.
5. Deduplicate queued changes and reminders about the same failure. Test time zones and seasonal schedule boundaries with a fake clock.

**Done when**

- Fixture unchanged, changed, new-season, removed and failed-source cases produce the correct queue/state.
- A controlled live read-only scheduled check is recorded; failures are reported honestly. No changed source automatically becomes published research.
- The owner can pause/check the source monitor independently of the mail schedule.

**Rollback:** disable the monitor while keeping queued candidates and the published snapshot. No data deletion required.

## Milestone 31: Validate the team experience and hand over operation

**Outcome:** Ryan and Shafkat can perform their intended tasks, and maintenance does not depend on undocumented steps.

**Work**

1. Update one current status/readme, a one-page reviewer guide, owner recovery guide, data methodology and limitations. Clearly label old workbook/export snapshots; regenerate approved handoff artifacts from the accepted current state when appropriate.
2. Run a short observed scenario: Ryan opens MI/FY2026, explains the supplier share denominator and quote, opens source evidence, then reviews Lancaster in the private workspace. Shafkat signs in with his own invited identity and confirms read-only behavior. Never impersonate them to claim recipient acceptance.
3. Exercise a synthetic approval and a controlled failure in a separate demo. Real recipients must never receive demonstration requests.
4. Record task completion, time, misunderstandings, defects and requested improvements. Suggested usability target: locate/read the relevant reply within two minutes after sign-in and explain the next action without developer coaching; validate or revise this with actual users.
5. Inventory host cost, operator ownership, backup/recovery responsibility, source schedule, Gmail consent status and where to report a failure. Obtain team preferences on the next useful state/data gap rather than assuming expansion.

**Done when**

- Internal acceptance checks pass and external user feedback is explicitly recorded or marked pending. No invented “Ryan approved” status.
- Source, configuration and dataset versions are reproducible; handoff text matches actual behavior and limits.
- The release has a clear stop/continue decision. A useful pilot can stop here without a public product launch.

**Rollback:** keep the last accepted release and report; if user testing reveals a blocker, return to the responsible milestone.

## Milestone 32: Decide on institutional ownership and wider access

**Outcome:** only if Irenic wants ongoing use, choose an operating model the firm can own and support.

This is a decision-gated extension, not a prerequisite for Ryan's original assignment.

1. Confirm whether Irenic wants a small invited team tool, personal self-service workspaces, or a broader service. Public registration is already enabled by prior user choice; do not silently disable it. Propose the simplest appropriate access policy and obtain a decision.
2. Confirm actual company identity/mailbox requirements with its designated contact. Google identity and Gmail access are different. Do not assume an `irenicmgmt.com` address has Gmail, or assume Microsoft integration is needed without evidence.
3. If institutionalized, plan firm-controlled hosting, repository, domain, identity configuration, support contact, key custody and business mailbox. Any ownership/access transfer is a separate explicit action.
4. If retaining Gmail access, complete the applicable branding/domain/privacy/demo requirements and determine Google's restricted-scope assessment obligations. Do not promise approval dates, waive requirements, purchase assessments, or encourage dismissing warnings as proof of safety.
5. Add managed membership/revocation, retention/deletion and export procedures appropriate to the chosen model. Keep operator access disclosed. Avoid unlimited registrations until operational capacity is tested.
6. Reconsider SQLite/single-process only if measured concurrency, recovery or organizational needs justify PostgreSQL/a separate worker. Migration must preserve job deduplication and review history.

**Done when:** the chosen model, costs, owner and access policy are accepted; only that scope is implemented and verified. External verification remains pending until the provider actually approves it.

## Implementation checklist for each release

- [ ] Record the user-selected milestone and acceptance criteria.
- [ ] Inspect the deployed commit, current worktree and dataset; isolate unrelated work.
- [ ] Preserve current permissions, data and exact-message approval behavior unless this milestone explicitly changes them.
- [ ] Implement the smallest coherent change, with meaningful regression tests for affected behavior.
- [ ] Verify desktop/keyboard and relevant narrow-screen/error states; inspect actual rendered colors.
- [ ] Check private/public boundaries and exported content.
- [ ] Document migration and rollback; use isolated fixtures for destructive/failure tests.
- [ ] Deploy only when authorized, then verify live health, roles, schedule state and release/snapshot identity without sending demonstration mail.
- [ ] Update the current status page and mark this milestone complete only with the required evidence.

**Recommended next implementation:** milestone 26.
