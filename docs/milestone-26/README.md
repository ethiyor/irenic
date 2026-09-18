# Milestone 26: shared visual system and accessibility

September 18, 2026. Presentation changes for the dashboard, analyst workspace and analyst sign-in. No data migration, sender change, permission expansion, automatic sending, or publication acceptance.

## Visual reference

The canonical token source is `design/visual-system.css`. The dashboard imports its verbatim copy at `app/visual-system.css`; the two analyst templates embed the same block to preserve their existing CSP and single-file deployment. Future token updates must be copied to all three consumers. Product-specific rules follow the shared block.

| Role | Value / rule |
| --- | --- |
| Canvas / surface / subtle | `#f4f7fa` / `#ffffff` / `#edf2f7` |
| Main / secondary text | `#23344a` / `#526478` |
| Dashboard / analyst accent | `#192f50` navy / `#216759` teal |
| Links / focus | `#245f95` / `#125da6`; 3px outline, 3px white separation |
| Control border / panel divider | `#718196` / `#d6dfe8` |
| Warning | `#78530e` on `#fff7e5` |
| Error | `#9a3128` on `#fff1ef` |
| Typography | Segoe UI, Arial, sans-serif; 14–16px ordinary text; 12px metadata; tabular figures |
| Spacing | 4, 8, 12, 16, 20, 24px |
| Shape | 8px controls, 12px panels |
| Targets | 44px ordinary buttons/selects/inputs and row expanders; 32px disclosures |

The clean browser rendered the original workspace in light colors. Both products now explicitly declare a light color scheme. No browser inversion or untested dark theme is introduced.

Routine live status uses a neutral surface; simulation and substantive source discrepancies retain warning treatment. Successful actions use neutral feedback, failures use error feedback. Approval requirements remain visible. Metric caveats remain beside the values, with additional explanations available in keyboard-accessible disclosures.

## Validation

- Dashboard: 31 existing calculation/export/share tests and TypeScript checks pass. Production build succeeds. The Sites build wrapper failed before compilation because its Windows npm shim resolved incorrectly; the unchanged project `npm run build` completed successfully. The official packaging helper succeeded under the user account after sandbox Git Bash traversal failed.
- Backend: all 75 existing tests pass, including role, workspace isolation, evidence, scheduler and approval protections. Expected scheduler failure logging in its negative test is not a suite failure.
- Browser checks used disposable local data, not live outgoing approvals: empty queue; pending request/forward queue; named subject/message fields; disabled approval until review; expanded evidence with a long recipient and filename; visible keyboard focus; supplier-map selection; loading and invalid-dataset error with retry.
- At 390px both products contain horizontal overflow. Wide tables remain within labeled, keyboard-focusable regions. Regular controls are at least 44px high. Checkboxes retain native glyphs inside larger labels. Inline prose links and geographic map shapes are documented target-size exceptions; map selects provide ordinary-size alternatives.
- Analyst expanded-evidence HTML text scan: 45 visible text-bearing elements, minimum measured contrast 5.66:1. The initial dashboard scan identified table “Not stated” text at 4.495:1 on alternating rows; corrected to the shared secondary-text token.
- Focus ring observed at `rgb(18, 93, 166)`, 3px. Keyboard navigation reached the skip link and named message field. Native table/detail semantics and form labels are retained.
- Reduced-motion CSS disables animation, transitions and smooth scrolling. A native screen-reader session and forced operating-system reduced-motion setting were not available; this is not a full WCAG conformance audit.
- The invalid-data fixture was restored from an exact backup. Generated public data and built data have the same SHA-256: `344c08f9f5f8b89c9a980d9bad8896ae011bc46d5e85424c58213781dfdd697a`. Active snapshot remains 10,106 rows.

The dashboard now aborts dataset requests after 20 seconds and offers retry. Workspace reads retain their existing 20-second timeout; writes retain 120 seconds with no automatic retry. No timeout is interpreted as proof that an email was not sent.

## Release

Dashboard source: `c4e65c829ff3d77b82f24ea4f41a000489025d0d`.
Sites version 7, deployment `appgdep_6aadc16f0d7c8191ad0288508c7527be`, succeeded at 22:55:50 UTC.
URL: https://road-salt-contract-monitor.ytk2108.chatgpt.site/

Analyst deployment receipt and remaining browser measurements are recorded below after verification.

## Rollback

Redeploy Sites version 6 and the preceding analyst commit `9080c18def1f3f34a301be4a23a400d02de1facf` if necessary. There is no database rollback. Before restarting Render, suspend scheduled preparation and confirm the worker is idle; after the authenticated application is verified, restore its prior schedule state. A maintenance `/healthz` response alone is insufficient acceptance.
