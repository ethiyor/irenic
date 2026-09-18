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

Dashboard source: `fd13738f5cdb37e77c89303fef80a4ea6c2631e9`.
Sites version 8, deployment `appgdep_6aadc43a3fcc8191990d59a7c292413d`, succeeded at 23:07:44 UTC. This includes the final chart-value contrast correction following version 7.
URL: https://road-salt-contract-monitor.ytk2108.chatgpt.site/

Analyst source: `e2e27205409c54763edaff7b7a0d02fdaa8ed3af`.
Render deployment `dep-dams891ntn7c73d74p2g` succeeded at 7:08:33 PM EDT. Authenticated workspace subsequently loaded with the new style tokens and loading-state presentation. Schedule restored to enabled, with next eligibility displayed as 7:24:44 PM EDT. Mailbox authorization remains stored. Five initial requests, zero reminders and one forwarded reply remain provider-accepted; no live message was approved or sent during this milestone. No lock recovery or database repair was needed.

Final dashboard HTML contrast scan: 604 visible text-bearing elements, minimum 4.65:1, no failing pairs. SVG chart value labels were separately inspected and changed from the library's default gray to `#526478`; all rendered chart text uses this token. Measured token pairs: body/white 12.64:1; secondary text/white 6.08:1; secondary text/canvas 5.66:1; white/teal 6.67:1; warning pair 6.47:1; error pair 6.71:1; focus/white 6.68:1; control-border/white 3.98:1. Disabled controls were excluded from text contrast requirements.

Browser screenshots and DOM checks covered 1280px laptop and 390px mobile views, and dashboard reflow at 640 CSS pixels (the layout width corresponding to a 1280px viewport at 200% zoom). Actual browser zoom shortcuts did not provide a reliable native zoom test in this browser interface, so true 200% zoom remains a manual acceptance item rather than a claimed pass. Likewise, no native screen-reader certification is claimed. Local invalid classification produced the expected “Review note required” status. The missing-data proxy confirmed a failed fetch; a malformed generated dataset exercised the styled error screen and retry recovery. Neither fixture was published.

The initial workspace now shows overview placeholders instead of briefly displaying every section while its session loads, and exposes `aria-busy` until initialization ends. A session-load failure provides a recovery message. Visual checks were performed in the browser and displayed in this task; no standalone screenshot archive was saved.

## Rollback

Redeploy Sites version 6 and the preceding analyst commit `9080c18def1f3f34a301be4a23a400d02de1facf` if necessary. There is no database rollback. Before restarting Render, suspend scheduled preparation and confirm the worker is idle; after the authenticated application is verified, restore its prior schedule state. A maintenance `/healthz` response alone is insufficient acceptance.
