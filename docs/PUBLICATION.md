# Private document review and public release

Do not run these steps on Lancaster's present contract-range packet. Its annual quantity and population are unresolved. The steps below apply only to a newly supported annual observation and an explicitly authorized publication.

## Review in the workspace

1. Open Research. Read the facts, source pages, extraction coverage and blockers. Use Inbox & documents for the matched original reply and PDF downloads.
2. The owner records a fact decision with evidence. Hold when a required interpretation is unresolved; exclude losing bids/out-of-scope products. Supersede requires an identified predecessor and explicit resolution of every overlap.
3. Only an accepted annual candidate without blockers can receive a separate publication-eligibility decision. That decision does not publish or approve an email.
4. Immediately before release review, download a fresh private handoff. Any newer fact decision invalidates the prior eligibility. Keep the export private and check the workspace again if release is delayed.

Approvers/reviewers can inspect all these stages in the shared campaign. Research mutations remain owner-only. To request clarification from an office, use the existing fixed-recipient draft and exact-message approval flow; this milestone does not create or send clarification emails automatically.

## Prepare the private mapping

Use the data Python runtime with pypdf/pdfplumber and Node on PATH. From the project root:

```text
python pipeline/outreach_intake.py import-live --run <private-intake> --live-run <private-campaign-copy>
python pipeline/outreach_intake.py extract --run <private-intake> --sha256 <pdf-hash>
python pipeline/outreach_intake.py stage --run <private-intake> --file <private-mapping.json>
python pipeline/research_bridge.py annual-packet --intake <private-intake> --candidate <staged-candidate.json> --output <private-packet.json>
```

Mapping uses the existing full line-item schema and exact page quotes; stage one observation per packet. Include explicit vendor, season, quantity, price, program and units evidence. Inspect extraction quality visually where appropriate. The stage command checks duplicates/overlaps against the accepted snapshot. Import the packet in Research, then perform both human reviews.

The live Render mailbox is not automatically mirrored to this computer. Use the existing private intake transfer process and exact source identities. Do not put private MIME/PDFs, tokens or review exports into the public dashboard project. Do not copy a live SQLite file while it is being written; use the established consistent export/backup procedure.

## Prepare a public release

```text
python pipeline/research_bridge.py consume-review --intake <private-intake> --candidate <staged-candidate.json> --review <fresh-workspace-export.json> --output <private-handoff-result.json>
```

Use the handoff path returned by that command. Register the genuinely public, hash-identical government source in the existing source catalog and page index. A `source-map.json` maps `outreach-pdf:<hash>` to the registered public document-version ID. Never replace private-source evidence with an unrelated public URL.

```text
python pipeline/publication_release.py preview --handoff <handoff.json> --workspace-review <fresh-workspace-export.json> --source-map <source-map.json> --reviewed-on YYYY-MM-DD --output <private-projection-review.json>
```

Inspect every proposed public field for factual accuracy and private contact/address/signature/correspondence material. The generated decision is pending. After review, set `decision` to `approve_public_projection`, provide reviewer and reason, and preserve the exact input hashes. The release bridge does not infer this approval.

```text
python pipeline/publication_release.py prepare --handoff <handoff.json> --workspace-review <fresh-workspace-export.json> --approval <private-projection-review.json> --output <private-preparation-receipt.json>
python pipeline/publication_release.py build --release <release-id>
```

Review the preparation receipt's additions, predecessor removals, missing values and state-specific volume/quote impacts. Inspect the staged snapshot and public projection. Supply a separate snapshot acceptance file with `decision: accept`, `snapshot_id`, exact `candidate_sha256`, `reviewer`, `reviewed_on` and `notes`.

```text
python pipeline/publication_release.py accept --candidate <snapshot-candidate.json> --approval <snapshot-approval.json>
```

This reruns the build and validators before changing the accepted manifest. Reimporting the same release is refused. Changed extraction inputs, baseline or conflicting logical identities require renewed review.

## Build, deploy and record

Use the existing dashboard build/test and Sites publication procedure after acceptance and publication authorization. Verify the deployed snapshot and representative source rows. Do not call a local build a deployment.

```text
python pipeline/publication_release.py receipt --snapshot <accepted-snapshot-id> --build-output <public-build-directory> --url <verified-https-dashboard-url> --deployment <actual-deployment-id> --actor <operator> --preparation <private-preparation-receipt.json>
```

The resulting receipt is written under `private/publication-receipts`. Import it through Research to link the private review to the public snapshot. The record hashes the actual build files and identifies who recorded the remote verification; it does not independently attest the remote host.

## Roll back without deleting evidence

```text
python pipeline/publication_release.py rollback --acceptance <acceptance-receipt.json> --expected-current <current-snapshot-id> --reason <reason> --actor <operator>
```

This preserves sources, candidates, decisions, accepted releases and historical deployment receipts. Rebuild from the restored pointer and redeploy the previously accepted public output if the public site needs rollback. Restore application code separately if necessary. Public site status and local accepted state must be checked independently.
