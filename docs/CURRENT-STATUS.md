# Current project status

Updated September 18, 2026. This file supersedes historical deployment/status claims in milestone reports; it does not overwrite their dated evidence.

## Release

- Milestone 23 implementation: explicit owner/approver/reviewer action matrix; unknown commands denied; classification controls owner-only; existing classification/evidence notes readable by reviewers; legacy local-console status placeholders removed from hosted responses.
- Deployment: milestone 26 verified operational September 18 after 7:08 PM EDT; application source `e2e27205409c54763edaff7b7a0d02fdaa8ed3af`, Render `dep-dams891ntn7c73d74p2g`. All 75 backend tests pass. Scheduled preparation was suspended for deployment and restored afterward; no message was sent. See the milestone 26 receipt.
- GitHub main contains the deployed milestone 26 application; subsequent documentation-only receipts do not change runtime behavior.
- Backend repository: private `ethiyor/irenic`. Release checkout: `private/m23-release`, branch `codex/milestone-26`. Root `pipeline/` mirrors reviewed implementation, except its sign-in retains unrelated pending Google branding changes. `private/render-source` must not be blindly deployed.
- Dashboard: Sites version 8, source `fd13738f5cdb37e77c89303fef80a4ea6c2631e9`; 31 existing tests and TypeScript checks pass. Shared visual tokens, stronger chart labels, labeled scroll regions, focus targets and bounded loading are live. Native screen-reader and true 200% zoom acceptance remain manual checks.
- Dashboard: https://road-salt-contract-monitor.ytk2108.chatgpt.site/
- Analyst: https://road-salt-analyst.onrender.com/

## Data and operation

- Active dashboard snapshot: `3e946cbceb28d366c33fcab17ad9231924e9a6ee64a72a6ade8b27fb1c67945f`, 10,106 rows. No dataset changes in milestone 23.
- Original live campaign: five initial requests, six provider-accepted campaign jobs including one Lancaster forward, one matched Lancaster response. Actual new arrivals can change this baseline. Authentication emails are separate.
- Render is the live worker host. Scheduled checks prepare drafts; every procurement request, reminder and forward requires exact-message approval. Sending acceptance is not proof of recipient reading.
- Google public registration is enabled; sign-in is separate from Gmail authorization. Google scope verification remains incomplete. Actual Ryan/Shafkat sign-in is not independently confirmed.

## Shared campaign roles after milestone 23 deployment

| Account | Role | Allowed campaign work |
| --- | --- | --- |
| ytk2108@columbia.edu | Owner | Configuration, classification, draft edits, approvals and schedule |
| rfusaro@irenicmgmt.com | Approver | Read; approve/reject exact prepared messages |
| yordanostiruneh65@gmail.com | Approver | Read; approve/reject exact prepared messages |
| shafkat@irenicmgmt.com | Reviewer | Read only; no campaign mutations |

Mailbox connection is owner-only and must match the workspace's sender. Separate personal ownership never grants extra shared permissions. Invited reviewers/approvers can use email-link sign-in without connecting Gmail; they still need to choose the LionMail campaign under the current default navigation.

## Unresolved work

- Milestone 24 provides text-only HTML reply reading and authenticated PDF downloads. Inline previews and comprehensive document malware validation are not claimed.
- Milestone 25 defaults invited members to shared work, reconciles held/disabled/suppressed states and adds navigation, request timelines and protected polling. Human usability feedback remains outstanding.
- Lancaster annual quantity remains held; no new public rows are justified by its two-year range alone.
- Seasonal source monitoring and full disaster recovery remain later milestones. A passing health endpoint is not comprehensive operational acceptance.
- Previously pasted OAuth client secret rotation status remains unverified; do not reproduce credentials in documents.

See [milestone 25 report](milestone-25/README.md), [milestone 24 report](milestone-24/README.md), [milestone 23 report](milestone-23/README.md), [audit](PRODUCT-WORKFLOW-AUDIT-2026-09-18.md), and [next milestones](IMPROVEMENT-MILESTONES-23-32.md).
