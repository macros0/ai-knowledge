# SDD ledger — plan: docs/superpowers/plans/2026-10-01-document-actions-ux.md

Implementation approved in this chat; execution is inline.

Pre-flight: Tasks 1/2 share DocumentList upload slots; detach first, remove when the dialog is ready. Tasks 1/3 share SelectionBar; preserve working tag controls until BulkTagsModal replaces them. Tasks 1/4 share interrupted recovery; preserve separate access until notice replaces it.

Ruling: Work in the current checkout as approved by the plan and inline handoff; preserve existing changes and do not commit or push without a separate request. A separate checkout would not update the running local UI used for acceptance.

Ruling: Use this durable report as the ledger instead of the skill's Bash scratch helpers on Windows. No commits are authorized, so retain validation evidence here.

Task 1: in progress. Initial working tree: only the untracked approved plan, branch main. Baseline focused tests run before edits.

Task 1: targeted logic tests 16/16 green after RED (missing selection module). UI integration validation pending.
Task 2: batch/controller tests RED -> GREEN; uploadWithReview behavior retained; pure UploadZone separated from persistent DocumentsPanel controller.
Task 3: tag payload tests RED -> GREEN; both deltas sent in one existing API request, tag language explicit.
Task 4: API characterization distinguishes empty recovery preview from HTTP error; separate admin notice and fresh preview dialog integrated.
Ruling: Integrate the final SelectionBar with BulkTagsModal during the same execution before browser acceptance; the temporary tag fields and recovery button need not ship as intermediate states. Final functionality remains the approved scope.

User correction: move Upload documents to the same row as Documents / Trash; implemented in DocumentsPanel. Queue summary remains on its own compact row.

User correction: Upload documents must be a tab. Replaced the upload modal with DocumentUploadPanel in the third tab. The controller stays in DocumentsPanel; DocumentList stays mounted in a hidden/inert view to preserve selection across tabs. Only the similar-file decision remains modal.

User correction: tab order is Documents / Upload documents / Trash. Preserve DocumentQueueStatus placement and copy from the original interface.

Independent final review: no Critical findings; three Important findings and one accessibility issue identified. Fixed in one pass: refreshKey increments on return to Documents; TagCombobox consumes Escape for an open popup (new RED -> GREEN test); regenerate preview always opens and its limit uses server eligible IDs/config (new RED -> GREEN test); limit explanation is keyboard focusable. Delete preview now receives a stable ID snapshot. Selection scope/version update runs in layout effect before late responses can apply; deletion and selection-cap changes invalidate pending selection requests.

Validation before final fixes: check-project passed (npm audit: 0 vulnerabilities, ESLint: no errors, frontend: 282/282, Ruff: passed); production build passed. Final checks are rerun after review fixes.

Browser limitation: the dev tab showed only the header after earlier Fast Refresh errors. A separate production preview rendered its SSO login screen correctly. Automatic approval review rejected access to the corporate authentication origin during sign-in because explicit account sign-in authorization was absent. No authentication workaround was attempted; authenticated UI acceptance remains unverified.

Final automated verification: check-project passed (0 audited vulnerabilities, no ESLint errors, 284/284 frontend tests, Ruff passed); Next production build passed; git diff --check passed. Existing ESLint warnings remain. No backend runtime changes, no migrations, no commits/push. Tasks 1–4 implementation and automated validation complete; Task 5 authenticated browser acceptance pending explicit sign-in authorization.
