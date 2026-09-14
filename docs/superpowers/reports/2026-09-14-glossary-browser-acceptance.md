# Browser acceptance: glossary, 2026-09-14

## Scope and verdict

Real browser smoke used the disposable frontend `http://127.0.0.1:16301/admin/glossary` and backend `http://127.0.0.1:18001/api/admin/glossary`. All test fixtures were created through UI. API was used only for readback and one read-only merge-preview diagnosis. No app code, production database, primary services, Git state, or service lifecycle was changed by this reviewer.

Core create, conflict cancellation, edited-source merge, explicit disabled-state selection, kind reclassification, four-digit rule matching, rule editing and safe draft rule merge passed. Two small UI issues remain: a missing loading translation and an unhelpful explanation of unsafe rule merges. This is a browser smoke, not a claim of unrestricted regression-suite acceptance.

## Findings and recommendations

### P3: literal `common.loading` during merge-target lookup

Reproduction: create two terms, merge them, then click the surviving PA30 row to reopen its editor. While the merge-target list loads, the accessibility/DOM snapshot contains `status: common.loading`. The literal appeared in the real Russian UI; it was transient, not a permanent failure.

Relevant call sites: `frontend/src/components/GlossaryPanel.jsx:195` (observed merge-search status), also loading text at line 181 and `frontend/src/components/GlossaryRulesEditor.jsx:97`. `common.loading` is absent from both bundled `frontend/src/i18n/locales/ru.js` and `en.js` (focused search). Add the shared key in both dictionaries or reuse an existing suitable loading key. Stable editor, rules and merge screens rendered their Russian/English labels correctly.

### P3: unsafe rule merge error does not explain how to resolve it

Reproduction:

1. Existing rule `Browser 0..999`: range `0..998`, prefixes `IT, ИТ, Infotyp, инфо-типа, ZINFO`.
2. Enter new draft `Browser overlap draft`: range `900..1100`, prefixes `IT, NEWPREFIX`.
3. Click Add. A merge dialog opens with the existing rule as target.
4. Click Preview. The UI says “Правило пересекается с существующим правилом или формой”, without explaining why the offered merge cannot proceed.

The refusal is **correct**, not a merge-exclusion defect. `backend/app/services/glossary/rule_registry.py:321` implements `_safe_merge_range`: differing ranges plus differing prefix sets cannot be combined without creating new forms, e.g. `NEWPREFIX0003`. The read-only API preview returned `glossary_rule_overlap` with source draft and target in `conflicts`. The client displays the generic error through `frontend/src/components/GlossaryRulesEditor.jsx:83`; translations are at `frontend/src/i18n/locales/ru.js:849` and `en.js:849`.

Recommend a distinct reason/code and actionable explanation: use equal ranges, or equal prefix sets with connected ranges. Do not weaken the backend preservation rule.

## Actual browser steps and results

### Create and cancellation

- Created SAP transaction `PA30`, description `Browser source description`, first alias `Управление персоналом`. Table and opened editor showed v1 and one alias.
- Attempted new business term named `PA30`, with first alias `Keep browser alias`. Name conflict opened merge. Cancel preserved the unrelated first alias exactly.
- Changed attempted name to `Browser source`, first alias to the existing `Управление персоналом`. Cancel cleared only the conflicting alias; source name remained `Browser source`.
- Created `Browser source` with alias `Browser original alias`.

### Editor draft → merge → readback

In the source editor, without saving individual sections:

- Changed source name to `Browser edited source` and description to `Unsaved browser description`.
- Unchecked source “Участвует в расширении”.
- Changed existing alias to `Browser edited alias`, kept Recognize=true and changed Add to search=false.
- Added conflicting `Управление персоналом` with Recognize=true, Add to search=false, then clicked `+`.

Merge displayed all unsaved values. Explicit selections were target name PA30, source description, target transaction kind, source Disabled state, source flags for the colliding alias. Preview retained these selections and showed disabled PA30 with the edited description and aliases. Merge completed.

Reopening the surviving PA30 row verified `sap_transaction · v2`, unchecked enabled state, edited description, `Browser edited alias` with flags true/false, and `Управление персоналом` with flags true/false. Table contained only the surviving term at that stage; source row was removed. API readback confirmed id=1, enabled=false, source_revision=2, and the same values. Source name was retained as an alias `Browser edited source` with flags true/true.

### Reclassification

Created transaction `IT0003` without a rule occupying its namespace. Changed Kind to SAP infotype; Save was disabled until the explicit Infotype number field was filled with `0003`. Saved successfully. UI showed `sap_infotype · v2`, source name `IT0003`, number `0003`. API readback confirmed `kind=sap_infotype`, `infotype_number="0003"`; opaque canonical identifier was retained.

Setup detail: a previously created rule was first disabled, but still reserved its namespace and correctly prevented transaction IT0003 creation. That disposable rule was deleted through UI to establish the transaction fixture, then recreated after reclassification. Disabled namespace ownership is expected behavior, not a bug.

### Rule matching and editing

Created `Browser 0..999` through UI with range `0..999`, prefixes `IT, ИТ, Infotyp, инфо-типа`. Rule list read back normalized prefixes and range `0000–0999`.

The editor Check query button was actually clicked for each query:

| Query | UI result |
|---|---|
| `IT0003` | `limited`, system-rule match, added forms |
| `ИТ 0003` | `limited`, system-rule match, added forms |
| `Infotyp 0003` | `limited`, system-rule match, added forms |
| `инфо-типа 0003` | `limited`, system-rule match, added forms |
| `инфотип 3` | `no_match`, no added forms |
| `IT00037` | `no_match`, no added forms |
| `XIT0003Y` | `no_match`, no added forms |

The four positive checks ran at range 0..999; the final exact three negatives ran after editing the upper bound to 998 and adding ZINFO. That edit does not change these examples' expected result. Additional UI checks `ИТ 3`, `Infotyp 3`, `инфо-типа 3`, `IT1000`, `PA3000`, `абв3` returned no_match.

Edited the existing rule through Edit / Save rule to upper bound 998 and appended ZINFO. Rule list and API readback showed range 0..998 and prefix zinfo. This smoke verifies saved fields, not a fresh matching probe for ZINFO or the 998/999 boundary.

### Safe draft rule merge and English UI

After the correctly rejected unsafe union above, switched interface to English through the language menu. Stable glossary form, term editor, flags, preview labels and rule labels rendered translated text.

Changed the new draft to the same range as the target (`0..998`), retained prefixes `IT, NEWPREFIX`, clicked Add → Preview → Merge. English preview displayed `0000–0998` and the union `it, ит, infotyp, инфо-типа, zinfo, newprefix`. Merge succeeded. UI showed one rule; API readback showed exactly one rule, id=1, version=3, original name retained, union prefixes. No draft/temporary row survived. Interface was returned to Russian.

## Limits and final state

- Browser errors observed were controlled domain conflicts; no 500 screen or unhandled UI error was observed. The captured browser console error list was empty. This does not certify every HTTP request or every error path; no full network trace was collected.
- No unit tests were run by this reviewer. Unit-only behaviors are not presented as browser evidence.
- No screenshot artifact was saved; evidence is the real DOM/accessibility snapshots from click/fill/preview flows and API readback.
- Remaining untested variants include every merge field combination, browser merge-based kind reclassification (ordinary editor reclassification was tested), per-alias deletion before merge, stale-version races, and exhaustive range-boundary checks.
- Final disposable state: PA30 id1 disabled with three aliases; IT0003 id2 as sap_infotype 0003; one rule id1 version3 range 0..998 with six prefixes. The root task manages disposal of the services.

Browser smoke completed; core checked paths pass with the two UI recommendations above.

## Follow-up after UI revisions: API and source review only

The disposable backend `/health` and frontend `/admin/glossary` both returned HTTP 200. A **new browser retest could not run**: documented CUA inventory returned `apps: [], browsers: []`; creating the IAB tab returned `Browser is not available: iab`; the documented browser-selection recovery returned `No browser is available`. No alternative browser automation was used. Thus the original browser observations above are historical and do not verify the revised focus/stale interactions.

This follow-up made no fixture mutations and no commit request. Read-only preview requests and source inspection established:

- RU/EN dictionaries now contain `common.loading` at line 12 and actionable `apiError.glossary_rule_merge_unsafe` at line 13. The messages explain equal ranges, or equal prefix sets with connected ranges, and equal enabled state. This verifies source text, not browser rendering.
- A real read-only unsafe-rule preview for target id1 v3 and draft range 900..1100 / IT,OTHERPREFIX returned `code=glossary_rule_merge_unsafe`, replacing the generic overlap code.
- A real term preview with draft `QA preserved draft`, description `QA unsaved content`, target PA30 id1 v2 returned all compatibility and new fields: `source,target,merged,digest,glossary_revision,unresolved_fields,result,removed_term_id,revision,preview_digest,conflicts,requires_confirmation`. `merged` and `result` retained the selected draft description, target disabled state, existing aliases and draft-name alias. `conflicts` and `unresolved_fields` were empty.
- `GlossaryMergeDialog.jsx:148` disables commit when unresolved fields or conflicts remain; lines 152–154 display conflict feedback.
- Static stale-path trace: `registry.py:1227` attaches a fresh preview to stale version/revision/digest failures before mutation, `api/glossary.py:99` forwards it, and `GlossaryMergeDialog.jsx:78` refreshes source/target, clears the old proposal and decisions, and requires another preview. `buildGlossaryMergeRequest` retains the original `draft` / `sourceEdit` props. No live stale-commit race or browser draft-preservation check was performed in this follow-up.
- Both merge dialogs call `useGlossaryDialogFocus`. Its code focuses the first visible enabled control, wraps Tab/Shift+Tab at the boundaries, ignores Escape while busy, and restores a connected opener on cleanup. These are source-level observations; actual keyboard/focus behavior remains unverified.

### Follow-up code finding: focus hook conflicts with the portaled language selector

`frontend/src/lib/useGlossaryDialogFocus.js:19` handles Escape in a document-level capture listener (registered at line 35) and stops propagation. The same hook considers only DOM descendants of the merge dialog (`dialog.contains` at line 28).

However, `GlossaryMergeDialog.jsx:140` includes `ReferenceLocaleSelect`; its `SearchableSelect` renders the language-search popup with `createPortal` (`SearchableSelect.jsx:141`, target `document.body` at line 178) and focuses that popup input (line 62). The popup is outside the dialog DOM subtree.

Consequent source-level behavior: opening an alias-language selector and pressing Escape reaches the parent hook before the selector's own Escape handler, closing the entire merge instead of only the popup. Pressing Tab inside that popup is classified as outside-dialog focus and jumps to the first merge control. This is a concrete event/DOM ownership conflict, **not yet reproduced in the unavailable browser**.

Recommendation: include owned popups in the dialog focus boundary, or render this selector within the dialog; let the active popup consume Escape before closing the parent. After that, run actual keyboard acceptance for both ordinary controls and the nested language selector, including opener focus after dismissal.

Follow-up status: API/schema/message checks passed; browser retest, screenshot/DOM evidence for the revised UI, and stale-interaction acceptance remain open. The root task controls the disposable services.

## Implementation follow-up: name preflight and full-draft check

The accepted follow-up scope added name checking during typing in `GlossaryPanel` and `GlossaryEditor`. The shared checker debounces for 350 ms, cancels superseded asynchronous results, and keys results by the entered name, current owner, draft kind/number and saved version. Known conflicts disable ordinary Create/Save, including the submit handler; a separate “Merge with: owner” action opens the existing merge flow with the current source and alias drafts. Cancelling a name-conflict merge keeps the entered source fields and unrelated first alias. A rule reservation without another term is displayed without an impossible merge action. Backend save validation remains authoritative while preflight is pending or failed.

`POST /aliases/check` now accepts optional `kind` and `infotype_number`, with its previous request shape still supported. The preflight uses proposed classification rather than the saved kind. It permits a new infotype to use its own unclaimed configured form, catches changing an existing infotype to a transaction, and retains conflicts with a different saved owner. No frontend prefix parsing or fixed SAP vocabulary was added.

The explicit API-plan gap was closed with read-only `POST /conflicts/check`: flattened full term draft plus optional `term_id`; response contains `conflicts` and `redundant_forms`. It checks source name, aliases, canonical infotype-number ownership, duplicate draft aliases, rule reservations, redundant forms and aliases referring to a different infotype number. It builds detached metadata and performs no insert, audit write or revision bump. Frontend helper: `checkGlossaryConflicts(draft, termId)`. The live name UI uses the lighter aliases/check endpoint with draft context; this does not claim that the entire editor automatically calls the full-draft endpoint.

Validation after implementation:

- New tests failed before implementation: missing debounce/context forwarding; proposed infotype ownership incorrectly rejected; changed draft kind incorrectly trusted saved classification; missing full-draft endpoint/helper.
- Backend: `test_glossary_name_preflight.py`, `test_glossary_exact_registry.py`, `test_glossary_remaining_contracts.py`: **19 passed**, 22.93 s, isolated SQLite. Full-draft tests call the actual route function with validated DTO and real registry; they are not HTTP/auth-middleware acceptance tests.
- Frontend: `glossaryNameCheck.test.mjs`, `glossary.test.mjs`, `glossaryConflicts.test.mjs`, `glossaryApi.test.mjs`: **40 passed**. New coverage exercises debounce/cancellation, stale-result context, and JSON request contracts.
- Focused Ruff passed. Focused ESLint for edited components/helpers/tests passed; including RU/EN produced the two existing anonymous-default-export warnings and no errors. `git diff --check` reported no whitespace errors.
- No browser proof is claimed for this implementation section; the root task owns subsequent browser acceptance and disposable-backend restart.

Changed implementation files in this follow-up: backend `app/models/glossary.py`, `app/api/glossary.py`, `app/services/glossary/registry.py`; frontend `GlossaryAliasCheck.jsx`, `GlossaryPanel.jsx`, `GlossaryEditor.jsx`, `glossaryUi.mjs`, `api.js`, and the two locale dictionaries. Added tests: `backend/tests/test_glossary_name_preflight.py`, `frontend/test/glossaryNameCheck.test.mjs`. No commit or service lifecycle action was performed by this worker.
