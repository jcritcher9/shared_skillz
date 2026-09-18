> [!WARNING]
> **Historical, non-authoritative snapshot.**
> Captured from `web/codex_context/handoff_doc.md` at baseline `a44572328` on 2026-08-19.
> That living file was retired. `web/codex_context/current_state.md` is the only living context document. Read this snapshot only for optional history.

---

# EasyImports Web — Handoff

Last updated: 2026-08-19 (`codex_end`: **Reviewed-result materialization
Phase 5 Grade A− ACCEPT `760e01205`** / pin **`a7549f5f3`.** rem
**`c80b2948b`** and land **`6b7100ff3`** are **B+** history. Track next
on that initiative: **none**. Product-default remains **FE-CM-4**.
Exact-first **Phase 6** stays not authorized. Prior **Created-date
review-display rem Grade A− ACCEPT `41736afc0`** / pin **`4b2b64786`.**
**`7794622fd`** is
**B+** history. **Created-date scoring rem Grade A− ACCEPT
`bab1297c3`.** **`ece89f549`** is **B+** history. **Review End early rem Grade A−
ACCEPT `86f059951`** / pin **`09e94f5cf`.** Land **`c32f5d690`** is
**B** history. Prior exact-first 4A+4B+4C Grade A−
ACCEPT `a13c754dc`** / pin **`b051e3c2c`.** rem10 **`6c755fa6f`** is
**B+** history. rem9 **`df2361e6d`** remains **B+** history. **Phase 5
Grade A− ACCEPT `7be8bdb82`** / pin **`cce4cef6c`.** rem2 **`68f74e9dc`**
/ rem **`e58c1d8c3`** are **B+** history; original **`4a63c11c2`** is
**B** history. **4C auto-disposition rem2 Grade A− ACCEPT `2e451ff11`**
/ pin **`00b76fff7`.** **`21da0960c`** is **B+** history. Local
Phase **6** stays not authorized. Product-default
remains **FE-CM-4**. Prior **ARW closed / archived.**
**ARW-5 Grade A ACCEPT** **`20d502b4`**. **ARW-1…ARW-5 accepted.**
Track next on ARW: **none**. Product-default remains **FE-CM-4**. Prior
**RSF closed / archived.** **RSF-5 Grade A ACCEPT** **`96caece2`**.
**RSF-3 Grade A ACCEPT** rem **`e3bf0045`** / pin **`162a2906`**.
Original **`b2c792d9`** is **B+** history. **RSF-4 Grade A ACCEPT**
**`db001c78`** / pin **`db364a58`**. **RSF-1 Grade A ACCEPT** rem2
**`ab528195`**. Rem **`ad355a7e`** is **B+** history. Original
**`27122bd6`** is **D** history. Track next on RSF: **none**. Prior
**GFC-9 Grade A ACCEPT** rem4
**`899639f5`**. Implementation pin **`e53ce3b4`** is not acceptance.
API **1.54.0**. **GFC-8 Grade A ACCEPT** **`911c11cd`** / pin
**`bd73d0f9`**. Track next on GFC: **none** (archived). Does **not** displace **FE-CM-4**. Prior **GFC-7B Grade A−
ACCEPT** rem
**`9b3e2424`** / pin **`7929eed7`**. API **1.52.0**.
Django does not import `mappings_2`. Prior **GFC-6 Grade A ACCEPT** rem2
**`e08cf83c`** / pin **`13bc0f48`**.
Prior DDR **closed / archived**.
**DDR-0…5 Grade A− ACCEPT** rem **`c0d00cfb`** / acceptance **`0adbc526`**.
Prior BHR **closed / archived** rem
**`c7c910d2`**. Parallel SCR-1A remediation **Grade A− ACCEPT
`93dacdf1`** / pin **`2194b371`**; SCR-1B generated API client **1.46.0**
**Grade A− ACCEPT `05433be3`** / pin **`7b01e723`**. SCR-2 operator rendering
is **Grade A− ACCEPT `7db0d14b`** / pin **`aa4e8294`**. SCR-3A is implemented
in **`855dbad7`** and is **Grade B+ — NOT ACCEPTED**; generation-CAS
remediation **`daea3674`** is also **Grade B+ — NOT ACCEPTED** because its
connection scope over-translates provider/body `OSError`s. Exception-scope
remediation **`dee5f238`** is **Grade A− ACCEPT** / pin **`2f846ed2`**.
Original SCR-3B **`a81756b2`** is **Grade B+ — NOT ACCEPTED**. Recovery-saga
remediation **`0f78bd47`** is **Grade A− ACCEPT** / pin **`04ddd65a`** and
retains generated API **1.47.0** contracts. SCR-3C is **Grade A ACCEPT
`faf42f58`** (implementation record **`d80dcfc6`**; acceptance/archive record
**`740ac9e0`**). SCR is complete / archived.
Product-default next remains **FE-CM-4**. List-import full-path this-track
next is **5B**. **5A Grade A- ACCEPT** rem2 **`837f4d5f`** / pin
**`7f187620`**.)

## Latest tests (reviewed-result Phase 5 rem2 Grade A− ACCEPT)

Independent review of rem2 **`760e01205`**: Grade **A− ACCEPT**. rem
**`c80b2948b`** and land **`6b7100ff3`** remain **B+** history. Cumulative
Phase 5 change is accepted at **`760e01205`**. Docs pin **`a7549f5f3`**.
API **1.57.0**. Django still does not import `mappings_2`.

```text
# Independent review validation of 760e01205
.\.venv\Scripts\python.exe -m pytest tests/mappings_2/duplicate_resolution/test_reviewed_result_phase5_materialize.py -q --tb=line
# 9 passed, 6 warnings in 33.82s
git diff --check
# clean
```

Product-default next remains **FE-CM-4**. This-track reviewed-result next
**none**. Exact-first **Phase 6** only when separately authorized.

## Latest tests (created-date review-display rem Grade A− ACCEPT)

Independent review of rem **`41736afc0`**: Grade **A− ACCEPT**.
**`7794622fd`** remains **B+** history. Cumulative Created-date
review-display change is accepted at **`41736afc0`**. Docs pin
**`4b2b64786`**.

```text
# Independent review validation of 41736afc0
# 91 pytest cases passed
# 37 Django tests passed
git diff --check
# clean
```

Product-default next remains **FE-CM-4**. This-track exact-first **Phase 6**
only when separately authorized.

## Latest baseline

| Item | Value |
|---|---|
| **Branch** | `main` |
| **Latest local commit** | **Reviewed-result materialization Phase 5 Grade A− ACCEPT `760e01205`** / pin **`a7549f5f3`**. rem **`c80b2948b`** and land **`6b7100ff3`** are **B+** history |
| **Session focus** | Record reviewed-result Phase 5 rem2 **Grade A− ACCEPT `760e01205`**. rem **`c80b2948b`** / land **`6b7100ff3`** remain **B+** history. Product-default next remains **FE-CM-4**. Exact-first **Phase 6** stays unauthorized. |
| **Latest tests** | This closeout: `test_reviewed_result_phase5_materialize.py` **9 passed** in 33.82s. `git diff --check` clean. |
| **ARW review-window integrity (parallel, closed)** | **ARW-1…ARW-5 accepted.** **ARW-5 Grade A ACCEPT** **`20d502b4`**. Track next: **none** (archived). Authority: `mappings_2/.../completed_projects/crm_duplicate_review_window_integrity_and_identity_cost.md`. Does **not** displace **FE-CM-4**. |
| **GFC group-pipeline (parallel)** | **GFC-9 Grade A ACCEPT** rem4 **`899639f5`**. Implementation pin **`e53ce3b4`** is not acceptance. **GFC-8 Grade A ACCEPT** **`911c11cd`** / pin **`bd73d0f9`**. Interleaved slices, `building_review_materials`, `review_window_ready`, API **1.54.0**. Track next: **none**. Authority: `mappings_2/.../completed_projects/crm_duplicate_group_pipeline_efficiency.md`. Does **not** displace **FE-CM-4**. |
| **List-import operator output** | **OUT-9 Grade A — ACCEPT** **`07937a10`** / implementation docs pin **`9c80f6a0`** / acceptance docs **`811916ae`**. `exclude_group` renders after the comparison table with the same `selected__N` radio name; keep rows remain in `tbody`; Django→API command shape is unchanged. Track next: **none (closed)**. |
| **Status-aware Remove (parallel, closed)** | Phases **0–6 Grade A ACCEPT**. Phase **6** **`fad0d2d6`**, pin **`3dd916a5`**. Authority: `mappings_2/.../completed_projects/crm_connection_removal_status_aware_dependents.md`. Track next: **none** (archived). |
| **DRC (parallel)** | **DRC-1…4 closed / archived.** **DRC-1** **`cce61414`**, **DRC-2** **`4820fa47`**, **DRC-3 Grade A- ACCEPT** tip **`e0bf4529`** (docs pin **`ee15f077`**). Authority: `mappings_2/.../completed_projects/crm_duplicate_review_clarity.md`. Does **not** displace **FE-CM-4**. |
| **HSR-1 / HSR-2 / HSR-L tips** | **`dc575e3b`** / **`deeaa18b`** / **`93cec166`** Grade **A-** |
| **4A / 4B accept tips (retained)** | **`935d3e5b`** / **`655e495e`** |
| **Phase 5 pin (retained)** | **`9192da2d`** |
| **API consumer pin (FE-CM /health)** | **1.12.0** (FE-CM track) |
| **Live API / generated importer client** | **1.47.0** (SCR-3B reference-acquisition recovery command); regenerated with project **`.venv`** |
| **CRM-dupe abandon + Remove cascade** | Phases **0–4 accepted / archived** by **`b44832ef`**. Phase **4 Grade A ACCEPT** **`d0974004`** / pin **`d72bedc9`**; Phase **3 Grade A− ACCEPT** rem **`f6ac6fc8`** / acceptance docs **`34aea7ea`**; original **`ae8cfa62`** remains **Grade B — NOT ACCEPTED** history. Track next: **none**. Authority: `mappings_2/.../completed_projects/crm_duplicate_journey_abandon_and_connection_removal_cascade.md`. Does **not** displace **FE-CM-4**. |
| **CRM-dupe first-batch hang (parallel, closed)** | **Phase 3 Grade A ACCEPT** **`53846172`** / pin **`039d9c07`**. Track next: **none** (archived). Does **not** displace **FE-CM-4**. |
| **Reconnect-in-place + file load (parallel, closed)** | Phases **1–3 Grade A− ACCEPT**. Phase **3** rem3 **`f3307af5`** / pin **`ace3c352`**. Lineage **`d185b833` + `09073680` + `d8496f35` + `f3307af5`**. Track next: **none** (archived). Authority: `mappings_2/.../completed_projects/crm_reconnect_in_place_and_direct_file_load.md`. Does **not** displace **FE-CM-4**. |
| **FE-CM-4** | **Product-default next** (unchanged; ARW archive does not displace it) |
| **DDR discovery/review performance (parallel, closed)** | **DDR-0…5 Grade A− ACCEPT** rem **`c0d00cfb`** / acceptance **`0adbc526`** (`3c5c1f6d` B+ history). Track next: **none** (archived). Authority: `mappings_2/.../completed_projects/crm_duplicate_discovery_and_review_performance.md`. Does **not** displace **FE-CM-4**. |
| **RAP live read progress (parallel UX, closed)** | **RAP-0…3 Grade A− ACCEPT**. **RAP-0** **`4a502784`** / pin **`663bd20d`**. **RAP-1** **`aab6091a`** / pin **`62ef0abc`**. **RAP-2** **`a198efe9`** / rem **`a10ea858`** / pin **`a238e12a`**. **RAP-3 Grade A− ACCEPT** rem **`94ef2778`** / pin **`d35b0efd`** (`991d67cd` B+ history; retain `pages_per_cycle=5`). Archive **`e004f0d6`**. Track next: **none** (archived). Authority: `mappings_2/.../completed_projects/crm_reference_acquisition_live_read_progress.md`. Does **not** displace **FE-CM-4**. |
| **BHR batch hydration (parallel, closed)** | **BHR-0…2 Grade A− ACCEPT** rem **`c7c910d2`**. Backend-only; Django unchanged. Track next: **none**. Authority: `mappings_2/.../completed_projects/crm_reference_acquisition_batch_hydration_and_pagination_reuse.md`. Does **not** displace **FE-CM-4**. |
| **SCR stale-cursor recovery (parallel, closed / archived)** | **SCR-1A remediation Grade A− ACCEPT `93dacdf1`** / pin **`2194b371`**. **SCR-1B Grade A− ACCEPT `05433be3`** / pin **`7b01e723`**, API **1.46.0**. **SCR-2 Grade A− ACCEPT `7db0d14b`** / pin **`aa4e8294`**. **SCR-3A Grade A− ACCEPT `dee5f238`** / pin **`2f846ed2`** (`daea3674` and original `855dbad7` are B+ — NOT ACCEPTED). **SCR-3B remediation `0f78bd47` Grade A− ACCEPT / pin `04ddd65a`, API 1.47.0** (original `a81756b2` B+). **SCR-3C Grade A ACCEPT `faf42f58` / implementation record `d80dcfc6` / acceptance/archive record `740ac9e0`.** Authority: `mappings_2/.../completed_projects/crm_reference_acquisition_stale_cursor_recovery.md`. Track next: **none**. Does **not** displace **FE-CM-4**. |
| **CRM disconnected-row removal (parallel, closed)** | Phase **1** rem6 **`11fc6f90`** Grade **A- ACCEPT**, rem7 **`885c3a87`**; API **1.33.0** |
| **CRM reclaim (repaired, archived)** | Phase **2** rem **`b707ec4a`** Grade **A- ACCEPT** shipped, then regressed by `ddd9a3b0`. Hardened A repair: Phase **0** **`ff15af8e`**, Phase **1** **`ffaa2d61`**, rem **`e5987926`** Grade **A− ACCEPT**. Authority: `mappings_2/.../completed_projects/crm_connection_reclaim_async_dispatch_regression.md`. API **1.38.0** |
| **DRUX (parallel)** | **DRUX-1…5 closed / archived** (DRUX-4 Grade A- ACCEPT **`317e9d16`**) |
| **HSR track next** | **none** (closed) |
| **Secret-store DPAPI (parallel defect-fix)** | **VDR-3 Grade A- ACCEPT** rem2 `3b819fd0` / rem `422b062d` / original `9f8ab7db`. Track next **VDR-4** (not started). Does **not** displace **FE-CM-4**. |
| **Reliability track next** | **none** (closed) |
| **Storage E2E track next** | Phase **8** closeout, or **7D** only with explicit SF Person live auth |

## Latest tests (ARW-5 Grade A ACCEPT / archive)

Independent review of **`20d502b4`**: Grade **A ACCEPT**. This closeout
archives ARW-1…ARW-5.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_crm_duplicate_review_layout_arw5 importer.test_crm_duplicate_review_decision_arw4 importer.test_crm_duplicate_review_window_arw3b importer.test_crm_duplicate_review_handoff_arw2 importer.test_crm_duplicate_review_progress_gfc6 importer.tests.WorkflowInterfaceTests.test_all_six_decision_types_render_and_submit_strict_commands importer.tests.WorkflowInterfaceTests.test_duplicate_group_review_skip_and_override_survivor_choices importer.tests.WorkflowInterfaceTests.test_duplicate_group_review_surfaces_progress_and_default_winner importer.test_crm_field_merge_1b_ui
# Found 35 test(s). OK
git diff --check
# clean
```

ARW this-track next: **none**. Product-default remains **FE-CM-4**.

## Latest tests (reconnect-in-place Phase 3 Grade A− ACCEPT)

Independent review of rem3 **`f3307af5`**: Grade **A− ACCEPT**. No
blocking findings.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_file_drop_upload_phase3 importer.test_column_mapping_map2
# Independent review: 56/56 passed
# This session: 37 tests, OK
.\.venv\Scripts\python.exe web\manage.py check
# System check identified no issues (0 silenced).
```

Django still does not import `mappings_2`. Reconnect-track next:
**none**. Product-default remains **FE-CM-4**.

## Prior tests (GFC-9 Grade A ACCEPT)

Independent review of rem4 **`899639f5`**: Grade A ACCEPT. No findings.
Implementation pin **`e53ce3b4`** is not acceptance.

```text
python web/manage.py test importer.test_crm_duplicate_analysis_progress_gfc9
# Found 7 test(s). OK — includes live dual-process GET/POST
```

API **1.54.0**. Django does not import `mappings_2`.

## Prior tests (GFC-7B Grade A− ACCEPT)

Independent review of rem **`9b3e2424`** / pin **`7929eed7`**:
Grade A− ACCEPT. No findings. Original **`690c98de`** remains
**B+ — NOT ACCEPTED** history. Django does not import
`mappings_2`.

```text
python -m pytest tests/mappings_2/duplicate_resolution/test_domain_routing_gfc7b.py tests/mappings_2/duplicate_resolution/test_groups_gfc3.py -q
# Independent review: 31 passed
# Related regressions: 88 passed, 4 subtests passed
python web/manage.py test importer.test_crm_duplicate_analysis_progress_gfc7b
# Found 2 test(s). OK
python -m pytest tests/mappings_2/test_v1_openapi_contract.py -q
# 3 passed
python web/manage.py check  # clean
```

API **1.52.0**. Policy **5**. Public stage
`routing_domain_groups`. Oversized membership is not walked as an
implicit clique.

**GFC-track next:** **none** (closed / archived). See `open_work.md` §
CRM duplicate-group pipeline efficiency (GFC).

**Product-default next (unchanged):** **FE-CM-4**.

Prior GFC-track next was **GFC-7B** (blocked, then implemented). It
is now **completed** with rem **`9b3e2424`** + pin **`7929eed7`**.
`690c98de` is **B+** history. Product-default next remains
**FE-CM-4** (not displaced).

## Prior tests (DDR archive closeout)

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_crm_duplicate_uncertain_mutation_ddr5 importer.test_list_import_operator_output_out_8 importer.tests.MutationJournalTests.test_malformed_success_becomes_unknown_and_only_exact_retry_is_possible --verbosity=1
# Result: Found 26 test(s). OK (4.401s)

.\.venv\Scripts\python.exe web\manage.py check
# Result: System check identified no issues (0 silenced).
```

**DDR-track next:** none (closed / archived).

**Product-default next (unchanged):** **FE-CM-4**.

## Prior tests (SCR-3C confirmed operator recovery)

SCR-3C proves exact diagnostic gating, read-only confirmation, one durable
pending/unknown exact-retry mutation, same-run resumption, and fresh pagination
through a real separate-process API/Django boundary. The accepted backend
recovery and fencing contracts remain covered.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_crm_reference_acquisition_stale_cursor_scr2 importer.test_crm_reference_acquisition_stale_cursor_scr3c importer.test_crm_reference_acquisition_stale_cursor_scr3c_dual_process --verbosity 1
# Result: 9 passed; Django system check clean.

.\.venv\Scripts\python.exe -m pytest -q tests\mappings_2\test_reference_acquisition_scr3b_recovery.py tests\mappings_2\test_reference_acquisition_scr3a_state_coordination.py tests\mappings_2\test_reference_acquisition_scr1b_diagnostics.py tests\mappings_2\test_reference_acquisition_bhr2_reuse.py
# Result: 58 passed.
```

SCR-3C focused Django coverage, including the real API/Django subprocess
boundary, passed **9/9**. Backend SCR-3B/SCR-3A/diagnostic/pagination
regression coverage passed **58/58**. A broader adjacent Django selection
passed **76/77**; its one failure is an unrelated existing Phase 4B fixture
asserting `Name L0` while rendering a record-ID-only fixture.

**SCR-track next:** none (closed / archived).

## Prior tests (OUT-9 Grade A — ACCEPT / closeout)

Reviewer reported no blocking findings: focused acceptance **10/10 passed**
and `git diff --check` was clean. The full importer suite was non-gating
because its isolated worktree omitted the repository root from Python's import
path; the first failure was unrelated to OUT-9. Local focused and adjacent
network-free results:

```text
..\.venv\Scripts\python.exe manage.py test importer.test_list_import_operator_output_out_9 importer.test_list_import_full_operator_path_phase4b --verbosity 1
# Result: Found 10 test(s). Ran 10 tests in 10.960s — OK

..\.venv\Scripts\python.exe manage.py test importer.test_list_import_full_operator_path_phase4c importer.test_list_import_full_operator_path_phase4d importer.test_list_import_full_operator_path_phase4e importer.test_list_import_operator_output_out_7 importer.test_list_import_operator_output_out_i2 --verbosity 1
# Result: Found 30 test(s). Ran 30 tests in 35.167s — OK
```

**List-import track next:** none (closed).

**Product-default next (unchanged):** **FE-CM-4**. See
`mappings_2/codex_context/open_work.md` § Next recommended (product-default)
for expected behavior, example, safety, and tests.

## Prior tests (abandon/removal Phase 4 Grade A ACCEPT / archive)

Reviewer accepted **`d0974004`** at Grade **A** with no findings; acceptance
pin **`d72bedc9`**. The dual-process proof blocks inside the fake CRM
capability and proves the stale worker loses checkpoint CAS after removal.
Django still does not import `mappings_2`; no live CRM was used.

```text
..\.venv\Scripts\python.exe manage.py test importer.test_crm_duplicate_journey_abandon_phase2 importer.test_crm_connection_remove_cascade_phase3 importer.test_crm_abandon_and_removal_cascade_phase4 importer.test_crm_connection_remove --verbosity 1
# Result: Ran 22 tests in 18.819s — OK

..\.venv\Scripts\python.exe manage.py check
# Result: System check identified no issues (0 silenced).
```

Independent review also reported dedicated Phase 4 API/store **11 passed**,
dedicated Django **2 passed**, full Phase 0/1/3/4 matrix **44 passed**, and
`git diff --check` clean.

**Abandon-track next:** **none** (closed / archived).

**Product-default next (unchanged):** **FE-CM-4** — network-free browser →
Django → API CampaignMember E2E. Expected behavior and example remain in
`mappings_2/codex_context/open_work.md` § Next recommended (product-default).

**List-import later disposition:** OUT-9 completed at Grade **A — ACCEPT**;
track closed.

## Prior tests (abandon Phase 1 Grade A ACCEPT / closeout)

Reviewer: rem2 **`5ec83178`** Grade **A ACCEPT**. API **1.42.0**
`POST /v1/workflows/{run_id}/abandon` is live. This historical Phase 1
entry predates the subsequently delivered Django UI. Django still does not
import `mappings_2`. No live CRM.

```text
.\.venv\Scripts\python.exe -m pytest tests/mappings_2/test_crm_abandon_and_removal_cascade_phase0.py tests/mappings_2/test_crm_abandon_and_removal_cascade_phase1.py tests/mappings_2/test_v1_openapi_contract.py -q
# Result: 29 passed in 9.45s
```

**Later disposition:** Phase **2** and the remaining cascade phases completed;
the initiative is now archived at the authority above.

**List-import later disposition:** OUT-9 completed at Grade **A — ACCEPT**;
track closed.

**Product-default next (unchanged):** **FE-CM-4**.

## Latest tests (OUT-8 rem2 Grade A- ACCEPT / closeout)

Reviewer: rem2 **`1f1eed82`** / pin **`42966ae1`** Grade **A- ACCEPT**.
Lineage **`4dcb1fff`** (B+ NOT ACCEPTED) → rem **`9ad4762d`** (C+
NOT ACCEPTED) → rem2 **`1f1eed82`**. Pending cards are mutation-kind
specific. Dual-process probes use mutation hooks. Django still does
not import `mappings_2`. No live CRM.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_8 -v 1
# Result: Found 10 test(s). OK
```

Grader also reported nine modified polling modules compile, focused
OUT-8/package/browser gate **29 passed**, formerly broken Phase 7A
and Phase 4B **19 passed** (1 live test skipped), `manage.py check`
clean, lineage `git diff --check` clean.

**This-track next:** **OUT-9** — list-dupe exclude sits under the
group. Authority: `mappings_2/codex_context/open_work.md` § This-track
next (session-authorized). Start from accepted OUT-8 rem2
**`1f1eed82`**. This closeout does **not** start **OUT-9**.

**Product-default next (unchanged):** **FE-CM-4**.

## Latest tests (OUT-7 rem Grade A- ACCEPT / closeout)

Reviewer: rem **`1379b805`** / pin **`eca46846`** Grade **A- ACCEPT**.
**`c6743368`** is **B+ — NOT ACCEPTED** (legacy grouped-display
assertions). Review tables show OUT-1 review-core plus typed extras.
Django still does not import `mappings_2`. No live CRM.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_7 importer.tests.WorkflowInterfaceTests.test_grouped_match_decisions_render_labeled_radio_tables importer.tests.WorkflowInterfaceTests.test_crm_match_comparison_separates_uploaded_context_from_candidate_evidence -v 1
# Result: Found 14 test(s). OK
```

Grader also reported focused OUT-7 + Phase 4A–4E **38 passed**,
broader relevant **81 passed** (one pre-existing unrelated mapping
preclaim error), `manage.py check` clean, `git diff --check` clean.

**This-track next:** **OUT-8** — mutation-kind pending copy.
Authority: `mappings_2/codex_context/open_work.md` § This-track next
(session-authorized). Start from accepted OUT-7 rem **`1379b805`**.

**Product-default next (unchanged):** **FE-CM-4**.

## Latest tests (OUT-6A Grade A- ACCEPT / rem pin)

Reviewer: code **`0124edc9`** Grade **A- ACCEPT**. Pin **`3c31b2cd`**
is **B+ — NOT ACCEPTED** (reclaim `completed_projects/` paths before
the file existed). Archive move is **`04b615a3`**, not this track.
Contacts-only help is provider-specific and visible. Django still
does not import `mappings_2`.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_6 -v 1
# Result: 7 passed
```

**This-track next:** **OUT-6B** (thin two-column configure page).
Authority: `mappings_2/codex_context/open_work.md` § This-track next
(session-authorized). Start from OUT-6A **`0124edc9`**.

**Product-default next (unchanged):** **FE-CM-4**.

**Prior this-track next:** **OUT-6A accept** — **completed** Grade
**A- ACCEPT** **`0124edc9`**. Pin **`3c31b2cd`** remains **NOT
ACCEPTED** history. Django still does not import `mappings_2`.

## Latest tests (OUT-5A/5B Grade A- ACCEPT / closeout)

Reviewer: Grade **A- ACCEPT** **`bb501983`**, pin **`cbb6d748`**.
Map columns has **Ignore all unmapped**. Ignore that clears the last
unresolved row confirms (`intent="confirm"`), persists the confirmed
bind, and redirects to Configure. A selected subset stays on mapping
as `save_draft`. Confirm rejection stays unconfirmed. Save draft is
unchanged. Django still does not import `mappings_2`.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_5 importer.test_column_mapping_map_r4_ui -v 1
# Result: 20 passed
```

## Latest tests (OUT-3 Grade A- ACCEPT / closeout)

Reviewer: Grade **A- ACCEPT** **`6aef21e0`**, pin **`67d8c0d1`**.
Django hides `mode__delivery` on list-import / account-list /
single-dataset configure and defaults it to disabled. The
choose-whether sentence is gone. Django still does not import
`mappings_2`. Package membership is an API concern.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_3 importer.tests.WorkflowInterfaceTests.test_configuration_uses_plain_language_and_closed_contract_details -v 2
# Result: OK
```

## Latest tests (OUT-I2 rem2 Grade A- ACCEPT / closeout)

Reviewer: Grade **A- ACCEPT** rem2 **`81bf3bce`**, pin **`aa160c1c`**.
Django grouped-stop surface for `uploaded_*_id_disagreement`. GET
never verifies. Django does not import `mappings_2`.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_list_import_operator_output_out_i2 -v 2
# Result: Ran 2 tests in 0.002s — OK
```

**This-track next:** **OUT-1** (mappings_2 freeze; not a Django
slice). Expected behavior: `mappings_2/codex_context/open_work.md` §
This-track next (session-authorized).

**Product-default next (unchanged):** **FE-CM-4**.

## Latest tests (Phase 3 Grade A ACCEPT / archive)

Reviewer: Grade **A ACCEPT** code **`53846172`**, pin **`039d9c07`**.
Barrier unit **3** passed; dual-process **2** passed; `manage.py check`
clean.

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_crm_duplicate_first_batch_hang_phase3 -v 2
# Result: Ran 2 tests in ~35s — OK
.\.venv\Scripts\python.exe web\manage.py check
# Result: System check identified no issues (0 silenced).
```

**Next recommended (unchanged product-default):** **FE-CM-4**. Hang-track
is archived. Does **not** displace **FE-CM-4**.

## Latest tests (2-EXPORT Grade A- ACCEPT)

```text
.\.venv\Scripts\python.exe web\manage.py test `
  importer.tests.MutationJournalTests.test_export_artifacts_dispatch_prefers_async
# Result: focused 2-EXPORT Prefer test OK
```

```text
.\.venv\Scripts\python.exe web\manage.py check
# Result: System check identified no issues (0 silenced).
```

Reviewer: Grade **A- ACCEPT** code **`f802668e`**, pin **`e01b6742`**.
Five adjacent async-dispatch tests passed; `manage.py check` clean.
Hang-track later closed at Phase **3 Grade A ACCEPT**. Product-default
remains **FE-CM-4**.

## Latest tests (status-aware Remove 0–6 Grade A ACCEPT / archive)

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_crm_connection_remove -v 1
# Result: Ran 10 tests in 0.258s — OK
```

```text
.\.venv\Scripts\python.exe web\manage.py check
# Result: System check identified no issues (0 silenced).
```

Reviewer (Phase **6 Grade A ACCEPT**): Django **10** passed;
`manage.py check` clean. Full importer suite was not usable as
acceptance evidence (timeout + unrelated `mappings_2` PYTHONPATH
import).

**Next recommended (unchanged product-default):** **FE-CM-4**. See
`open_work.md` and `mappings_2/codex_context/open_work.md`. This
archive does **not** displace that task.

## Latest tests (2-PKG Grade A ACCEPT closeout)

```text
.\.venv\Scripts\python.exe web\manage.py test importer.test_run_output_package_phase6b
# Result: Ran 18 tests in 0.938s — OK
```

```text
.\.venv\Scripts\python.exe web\manage.py test `
  importer.tests.MutationJournalTests.test_run_output_package_dispatch_prefers_async `
  importer.test_run_output_package_phase6b.CrmConnectionSetupUxPhase6BTests.test_create_package_pending_shows_in_progress_not_prep_error
# Result: OK (Prefer header + pending is not “could not be prepared”)
```

Reviewer: **43** focused/adjacent passed; `manage.py check` and
`git diff --check` clean.

## Latest tests (status-aware Remove 4A closeout, retained)

See `mappings_2/codex_context/handoff_doc.md` for the Phase **0–4A**
pytest command.

```text
.\.venv\Scripts\python.exe -m pytest -q tests/mappings_2/test_crm_connection_forget_phase1.py
# Prior parent-removal closeout: 16 passed (superseded by the combined
# status-aware suite in mappings_2 handoff)
```

Retained reclaim closeout (not re-run this archive):

```text
.\.venv\Scripts\python.exe web\manage.py test `
  importer.tests.CrmConnectionReclaimPhase2Tests `
  importer.tests.CrmConnectionSetupUxPhase1Tests
# Prior: Ran 12 tests in 0.320s — OK
```

HSR suite retained (not re-run this closeout):

```text
.\.venv\Scripts\python.exe web\manage.py test `
  importer.test_https_loopback_handshake_hsr0 `
  importer.test_https_loopback_handshake_hsr1 `
  importer.test_https_loopback_handshake_hsr2 `
  importer.test_https_loopback_handshake_hsrl `
  importer.test_https_loopback_https2 -v 1
# Prior: Ran 25 tests in ~8.7s — OK (skipped=1 live HSR-L)
```

Retained reliability (not re-run this closeout):

```text
.\.venv\Scripts\python.exe web\manage.py test `
  importer.test_production_path_reliability_phase4a `
  importer.test_production_path_reliability_phase4b -v 1
```

Storage 7C retained; live opt-in not default CI. Details:
`mappings_2/codex_context/handoff_doc.md`.

## Next recommended task

**List-import output/settings track:** **none (closed)** after OUT-9 Grade
**A — ACCEPT** **`07937a10`**.

**Product-default:** **FE-CM-4** — network-free browser→Django→API CampaignMember
E2E (product authority in mappings_2).

**Expected behavior:** dual-process browser journey proves configure → CM
policy/resolutions → create/replay/disconnect/ambiguity/dry_run zero mutation
on fake stack.

**Example:** Playwright against local Django+API; journal idempotency and
source-read-only grants hold; no live Salesforce.

**Parallel DRUX track:** **closed** (DRUX-1…5). Operator verbs are Approve
merge plan / Go back to reviewing groups; terminal ZIP is
`run_output_package.v2`. Reliability track **closed** (0–5); storage E2E
Phase **8** docs closeout or **7D** with explicit SF Person live auth;
field-merge **5A** only with fresh SF DE/sandbox authorization
(mappings_2 authority — not implied by HS canary).

**Parallel DRC track:** **closed** (DRC-1…4). Companies helper lists **80**
for exact name when both sides lack a domain; People stays **100 / 90 /
75**; auto-merge floor **90**.

**Prior next-task disposition:** list-import **OUT-9 review** moved to
**completed** (Grade **A — ACCEPT** **`07937a10`**); that track is closed.
Product-default **FE-CM-4** remains next (not displaced). Status-aware Remove **0–6**
is **closed / archived**. **Disconnected-connection removal Phase 1**,
**DRC-1…4**, and **DRUX-1…5** stay **completed**.
