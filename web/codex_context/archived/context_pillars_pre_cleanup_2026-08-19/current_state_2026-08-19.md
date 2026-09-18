> [!WARNING]
> **Historical, non-authoritative snapshot.**
> Captured from `web/codex_context/current_state.md` at baseline `a44572328` on 2026-08-19.
> The living `web/codex_context/current_state.md` overrides this file. Read this snapshot only for optional history.

---

# EasyImports Web — Current State

Last updated: 2026-08-19. Reviewed-result materialization **Phase 1
Grade A− ACCEPT `8ddc6e004`** / pin **`dff43aff7`.** **Phase 2
Grade A− ACCEPT `4c4f690dc`** / pin **`ebd89134d`** is backend-only
(one content compile per finalize). Django
still does not import `mappings_2`. **Phase 3 Grade A− ACCEPT
`075210d60`** / pin **`b6a289ed7`** is backend-only
(membership-complete finalize gate). **Phase 4 Grade A− ACCEPT `d8e19ef5d`** / pin **`a082b40e9`** (rem2
**`df8237a4e`**, rem **`6ff58118f`**, and land **`89d07764c`** are B+
history) is backend-only (digest-bound reviewed-result artifact).
**Phase 5 Grade A− ACCEPT `760e01205`** / pin **`a7549f5f3`** (rem
**`c80b2948b`** and land **`6b7100ff3`** are B+ history) is backend-only
(journaled `POST …/duplicate-reviewed-result-prepare` returns `202` and
compiles in a child from the in-memory run; prepare CAS-checks stored
revision; merge GET polls `GET …/duplicate-reviewed-result-progress` and
never writes). API **1.57.0**. Django still does not import `mappings_2`.
Track next on that initiative: **none**.
Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_reviewed_result_materialization.md`.
Does **not** displace **FE-CM-4**. Exact-first grouping redesign is **design
Grade A− ACCEPT `05c133c67`** / pin **`5815eec8b`**. **0A Grade A ACCEPT**
**`c338f177d`** / pin **`b5ec14fe0`**: `crm_duplicate_journey_review.html`
frontier-wait and auto-merge wait no longer auto-reload every 2.5 s; wait
copy is still-working plus a GET Refresh status. Progress auto-disposition
wait does not emit that 2.5 s reload. GET never writes. Django still does
not import `mappings_2`. **0B Grade A ACCEPT** **`d00039664`** / pin **`e66c5d031`.**
`08d70b157` / `d6b2d5445` are **B+** history. Review-window GET queries
digest-bound eligibility columns. Django still does not import
`mappings_2`. **P1** (1A+1B) **Grade A ACCEPT `017bfcc75`** / pin
**`f5669c04`.** `358fa0e12` / `a08b7dc8c` are **B+** history.
Exact-only Account working groups and local partition live beside the
old path. **P2** (2A+2B) **Grade A ACCEPT `ec104bdd`** / pin **`60f02cb1`.** **`63bfae48`** is **B+** history.
Exact-only Person working groups and local quarantine live beside the
old path. **P3** (3A+3B) **Grade A ACCEPT `af541921f`** / pin **`011961912`.**
`f62280d17` / `057613d00` / `4c808834d` are **B+** history.
Freeze/materialize and the immutable review store sit beside the old
path. **4A+4B+4C Grade A− ACCEPT `a13c754dc`** / pin **`b051e3c2c`.**
rem10 **`6c755fa6f`** is **B+** history. rem9 **`df2361e6d`** is **B+**
history. Existing-row replay verifies the stored blob digest before
unpickling. Review cursor state is per review run. Django still
consumes generated contracts only and does not import `mappings_2`.
API **1.56.0**. **Created-date review-display rem Grade A− ACCEPT `41736afc0`**
/ pin **`4b2b64786`.** **`7794622fd`** is **B+** history. Review
comparison puts Created date after Name; cells read Salesforce/HubSpot
aliases and ranking evidence as a calendar day.
`exact_first.normalization.v2` participates in `work_digest`;
`commit_batch` fails closed on a v1 checkpoint. **Created-date scoring
rem Grade A− ACCEPT `bab1297c3`.** **`ece89f549`** is **B+** history.
**Review End early rem Grade A− ACCEPT `86f059951`**
/ pin **`09e94f5cf`.** Land **`c32f5d690`** is **B** history. End early
requires five operator-saved decisions and binds the live window.
**Phase 5 Grade A− ACCEPT `7be8bdb82`** / pin
**`cce4cef6c`.** rem2 **`68f74e9dc`** / rem **`e58c1d8c3`** are **B+**
history; original **`4a63c11c2`** is **B** history. **4C auto-disposition
rem2 Grade A− ACCEPT `2e451ff11`** / pin **`00b76fff7`.**
**`21da0960c`** is **B+** history. Receipt totals come from the
hydrated/final review state; persist stays the slim store-ref. Local
Phase **6** stays
not authorized. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_exact_first_grouping_redesign.md`.
Does **not** displace **FE-CM-4**. Track next on this initiative:
**Phase 6** only when separately authorized.
Prior (`codex_end`: tracked **ARW** is **closed /
archived** **`a223e880`**. **ARW-1…ARW-5 accepted.** **ARW-5 Grade A
ACCEPT** **`20d502b4`**. Skip and quarantine render below the comparison table
with the same `duplicate_choice` values. **ARW-4 Grade A− ACCEPT** rem
**`3ba6c12b`** / pin **`c4249f53`**. Original **`50f7b9a1`** is **B+**
history. Confirm winner posts the displayed child; accepting path
records COMPLETED approve. **ARW-3B Grade A− ACCEPT** rem **`42a625e1`**
/ pin **`8a6ed62c`**. Auto-merge journeys use the filtered window. GET
adopts the already-created child. **ARW-3A Grade A ACCEPT** rem
**`ee7c2037`** / pin **`588a9a3e`**. Dispatches only when source
`review_ready` is True; a review-page CTA retries at a new generation.
GET never writes. **ARW-2 Grade A ACCEPT** **`7390402f`** / pin
**`4473150b`**. First-window handoff stays fresh. **ARW-1 Grade A−
ACCEPT** rem **`f235859a`** / pin **`30cbb339`**. Django still does not
import `mappings_2`. Track next on ARW: **none**. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_window_integrity_and_identity_cost.md`.
Does **not** displace **FE-CM-4**. Prior **RSF closed / archived.**
**RSF-1…RSF-5 Grade A ACCEPT.** Incremental Company review opens while
auto-merge is pending; auto-queued wait is count-free. Django still does
not import `mappings_2`. Track next on RSF: **none**. **GFC-9 Grade A ACCEPT** rem4
**`899639f5`**. Implementation pin **`e53ce3b4`** is not acceptance.
API **1.54.0**. **GFC-8 Grade A ACCEPT** **`911c11cd`** / pin
**`bd73d0f9`**. Interleaved rec+rev slices; public
`building_review_materials`; sibling `review_window_ready`.
Track next on GFC: **none**. Does **not** displace
**FE-CM-4**. Prior **GFC-7B Grade A−
ACCEPT** rem
**`9b3e2424`** / pin **`7929eed7`**. Original **`690c98de`** is **B+**
history. Bulk domain routing; residual pair visits;
`EvidencePolicy.version` **5**; public `routing_domain_groups`; API
**1.52.0**; `proton.me` / `pm.me` excluded. Oversized membership is
not walked as an implicit clique. Prior **GFC-3 Grade A−
ACCEPT** rem3 **`4709a803`** / pin **`e2f0b6ba`**. Prior **GFC-6 Grade A ACCEPT** rem2
**`e08cf83c`** / pin **`13bc0f48`**. `53aa2d6a` / rem **`b79a3c0a`**
are **B+** history. Prior **GFC-5 Grade A ACCEPT** code
**`15d6b7f1`** / pin **`1f7b4f12`**. Prior **GFC-2 Grade A ACCEPT**
code **`9d3fbd6c`** / pin **`749a4e66`**. Prior **GFC-1 Grade A
ACCEPT** code **`6b191fdc`** / pin **`7d4a0b25`**. Prior **GFC-4 Grade A
ACCEPT** code **`055a9dff`** / pin **`225b42e6`**.
Prior reconnect-in-place **Phase 3 Grade A− ACCEPT** rem3
**`f3307af5`** / pin **`ace3c352`**. Lineage **`d185b833` +
`09073680` + `d8496f35` + `f3307af5`**. Isolated upload POST journals plan create from
`dataset` / `raw_list` when ready. Adoption runs only after a
validated retry token whose setup revision matches a locked session
row. Garbage or stale-revision tokens cannot adopt a completed
mutation. GET mutation status never writes the mapping draft. Django
still does not import `mappings_2`. Phase **2 Grade A− ACCEPT** rem
**`06cc6c23`** / pin **`67589593`**. Track next on this parallel:
**none** (archived). Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_reconnect_in_place_and_direct_file_load.md`.
Prior **Phase 1 Grade A− ACCEPT** rem **`84ee665f`** / pin
**`3b05d8d0`**. Hub **Reconnect** posts
`POST /v1/crm/connections/{id}/reconnect`; pending shows **Continue
reconnect** / **Abandon reconnect**. API **1.50.0**. Does **not**
displace **FE-CM-4**. Prior BHR **closed / archived**
(**BHR-0…2 Grade A− ACCEPT** rem **`c7c910d2`**; Django unchanged). Prior abandon **Phase 4 Grade A ACCEPT**
**`d0974004`** / acceptance pin **`d72bedc9`**; initiative closed / archived
by **`b44832ef`**. Phase **3 Grade A− ACCEPT** remediation **`f6ac6fc8`**
(acceptance docs **`34aea7ea`**; API **1.44.0**; review-pending pin
**`faa659be`**). Original **`ae8cfa62`** is **Grade B — NOT ACCEPTED**
history; original docs pin **`b9a6e47c`**. Phase **2 Grade A ACCEPT**
**`46febe5b`** / acceptance pin **`acebcd41`**. Abandon-track next **none**.
Prior **abandon Phase 1
Grade A ACCEPT** rem2 **`5ec83178`**.
Django still does not import `mappings_2`. List-import **OUT-9 Grade A —
ACCEPT** code **`07937a10`** / implementation docs pin **`9c80f6a0`** /
acceptance docs **`811916ae`** closes that track. Tracked UX-improvement
**RAP** (CRM-dupe live read count) **closed / archived**. **RAP-0 Grade A−
ACCEPT** **`4a502784`** / pin **`663bd20d`**; **RAP-1 Grade A− ACCEPT
`aab6091a`** / pin **`62ef0abc`**; **RAP-2 Grade A− ACCEPT `a198efe9`**
/ rem **`a10ea858`** / pin **`a238e12a`**; **RAP-3 Grade A− ACCEPT** rem
**`94ef2778`** / pin **`d35b0efd`** (`991d67cd` B+ history; retain
`pages_per_cycle=5`). Archive **`e004f0d6`**. Track next: **none**. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_reference_acquisition_live_read_progress.md`.
Does **not** displace **FE-CM-4**. Tracked performance/reliability **DDR** (CRM-dupe discovery/review) is **closed / archived**.
**DDR-0 Grade A− ACCEPT** **`014eef54`** / pin **`7f6e73bf`**; **DDR-1
Grade A− ACCEPT** rem **`b8b7d09f`** / pin **`8ff24211`** (`e735b5af` is
**Grade B — NOT ACCEPTED** history). **DDR-3** rem **`31e95416`** is **Grade A− ACCEPT** (`dda41ce3` is
**Grade B+ — NOT ACCEPTED** history; pin **`348e66bb`**). Django is
unchanged for DDR-3. **DDR-4** rem **`6f85c537`** is **Grade A− ACCEPT** (`142fe2a1` is
**Grade B+ — NOT ACCEPTED** history; pin **`e92aedc8`**): entity-neutral
Matching detail on Find duplicates; API **1.48.0**; Django does not import
`mappings_2`. **DDR-2** rem **`c3006f9c`** is **Grade A− ACCEPT**
(`5adb653b` is **Grade D — NOT ACCEPTED** history): Django polls persisted
`duplicate_analysis_progress` and does not dispatch or resume work on GET.
API **1.49.0**. **DDR-5** rem **`c0d00cfb`** / acceptance **`0adbc526`** is **Grade A− ACCEPT**
(`3c5c1f6d` is **Grade B+ — NOT ACCEPTED** history): ReadTimeout copy uses the
measured wait; persisted diagnostics require an exact reconstructed
sentence; HTTP 5xx flash/persist stay on the generic uncertain sentence.
Timeout values
and exact-retry mechanics are unchanged. Track next: **none**. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_discovery_and_review_performance.md`.
Does **not** displace **FE-CM-4**. Tracked efficiency **GFC** (CRM-dupe
group-pipeline / incremental review) is closed / archived. **GFC-9 Grade A
ACCEPT** rem4 **`899639f5`**. Implementation pin **`e53ce3b4`** is
not acceptance. Original **`d721ab4b`** / **`9ddc9900`** / rem
**`1c9f926f`** / rem2 **`2d315bd5`** are **Grade D** history; rem3
**`56e9781f`** is **Grade B+** history. **GFC-8 Grade A
ACCEPT** **`911c11cd`** / pin **`bd73d0f9`**. Sibling
`review_window_ready`; interleaved 100-group slices; windows of 5;
409 frontier wait; public `building_review_materials`; API
**1.53.0**. Django still does not import `mappings_2`. **GFC-7B Grade A− ACCEPT** rem **`9b3e2424`** / pin **`7929eed7`**
(`690c98de` is **B+** history): exact domain is a routing key;
working-group membership plus audit-star evidence persist as
`working_group_membership` / same-transaction `evidence`; residual
producers only; `EvidencePolicy.version` **5**; public stage
`routing_domain_groups`; Django copy “Routing duplicate groups by
domain”; API **1.52.0**. Django does not import `mappings_2`. **GFC-3
Grade A− ACCEPT** rem3 **`4709a803`** / pin **`e2f0b6ba`**: ceiling
**128** remains in force for membership partition. **GFC-7A Grade A−
ACCEPT** pin **`0fdf7e86`**. **GFC-4 Grade A ACCEPT** code **`055a9dff`** / pin **`225b42e6`**:
Django distinguishes `building_recommendations` vs
`building_approval_bundle` copy; `complete` keeps the existing
terminal sentence. **GFC-1 Grade A ACCEPT** code **`6b191fdc`** / pin
**`7d4a0b25`**: `_compatible_partition` uses an incremental member
index and conflict adjacency. No API/version change. Django still
does not import `mappings_2`. **GFC-2 Grade A ACCEPT** code **`9d3fbd6c`** / pin **`749a4e66`**:
`finalizing_groups` reuses the pre-commit discovery on CAS win and
reconstructs from the winner cursor on CAS loss. **GFC-5 Grade A ACCEPT** code **`15d6b7f1`** / pin **`1f7b4f12`**:
`build_group_revisions` indexes evidence/conflict/edge rows once per
call. **GFC-6 Grade A ACCEPT** rem2 **`e08cf83c`** / pin **`13bc0f48`**
(`53aa2d6a` / rem **`b79a3c0a`** are **B+** history): GET-polls the
child review; displays it at `needs_decision` without replacing a
newer continuation or the active primary; GET never dispatches.
Django still does not import `mappings_2` for this surface. Track
next: **none** (archived). Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_group_pipeline_efficiency.md`.
Does **not** displace **FE-CM-4**. Tracked efficiency **BHR** (CRM)
batch hydration / pagination reuse) **closed / archived**. **BHR-0…2
Grade A− ACCEPT** rem **`c7c910d2`** (BHR-1 **`2a52bdf2`**; original
BHR-2 **`fc1978ab`** is **B+** history). Backend-only; Django still does
not import `mappings_2`. RAP progress display is unchanged. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_reference_acquisition_batch_hydration_and_pagination_reuse.md`.
Does **not** displace **FE-CM-4**. Tracked reliability **SCR** is closed /
archived; it has **SCR-0
Grade A− ACCEPT `7c5d04a9`** / pin **`eb33a171`** and **SCR-1A remediation
Grade A− ACCEPT `93dacdf1`** / pin **`2194b371`**. **SCR-1B** is implemented
in **`05433be3`** and is **Grade A− ACCEPT** / pin **`7b01e723`**: generated importer models/constants
now publish the three typed reference-acquisition diagnostics at API
**1.46.0**. **SCR-2** generically renders that existing public message on the
CRM duplicate progress page and is **Grade A− ACCEPT `7db0d14b`** / pin
**`aa4e8294`**, with no recovery control. Original **SCR-3A `855dbad7`** is
**Grade B+ — NOT ACCEPTED**; generation-CAS remediation **`daea3674`** is
also **Grade B+ — NOT ACCEPTED** because its connection scope over-translates
provider/body `OSError`s. Exception-scope remediation **`dee5f238`** is
**Grade A− ACCEPT** / pin **`2f846ed2`**. Original SCR-3B **`a81756b2`** is
**Grade B+ — NOT ACCEPTED**; recovery-saga remediation **`0f78bd47`** is
**Grade A− ACCEPT** / pin **`04ddd65a`**. Generated importer contracts remain
API **1.47.0**. SCR-3C is **Grade A ACCEPT `faf42f58`** (implementation
record **`d80dcfc6`**; acceptance/archive record **`740ac9e0`**):
the exact clearable diagnostic alone receives the confirmed action, GET never
dispatches, pending/unknown retries reuse one local mutation, and the real
API/Django dual-process proof resumes the same run with fresh progress.
The SCR track is complete / archived. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_reference_acquisition_stale_cursor_recovery.md`.
Does **not** displace **FE-CM-4**. Product-default next remains
**FE-CM-4**. List-import full-path this-track next is **5B**. **5A Grade
A- ACCEPT** rem2 **`837f4d5f`** / pin **`7f187620`** (backend identity
authority + digest-bound redirect; plan **v3**). Django still does not
import `mappings_2`. Prior
**OUT-8 rem2 Grade A- ACCEPT**
**`1f1eed82`** / pin **`42966ae1`**. Lineage **`4dcb1fff`** → rem
**`9ad4762d`** → rem2 **`1f1eed82`**. **`4dcb1fff`** is **B+ — NOT
ACCEPTED** history. **`9ad4762d`** is **C+ — NOT ACCEPTED** history. Prior
**OUT-7 rem Grade A- ACCEPT**
**`1379b805`** / pin **`eca46846`**. **`c6743368`** is **B+ — NOT
ACCEPTED** history. Prior
**OUT-6B rem2 Grade A- ACCEPT**
**`93114142`** / pin **`6223a57c`**. Prior
**OUT-6A Grade A- ACCEPT** **`0124edc9`**. Pin **`3c31b2cd`** is
**B+ — NOT ACCEPTED** history. Prior
**OUT-5A/5B Grade A- ACCEPT** **`bb501983`** / pin **`cbb6d748`**. Prior
**OUT-4 Grade A- ACCEPT** **`35a1119e`** / pin **`67b7506d`**. Prior
**OUT-3 Grade A- ACCEPT** **`6aef21e0`** / pin **`67d8c0d1`**. Prior
**OUT-2 rem3 Grade A- ACCEPT** **`6b4b49ed`**. Lineage **`edf0aa6b`** →
**`b0445adb`** → **`c5751115`** → **`6b4b49ed`**. Prior **OUT-H Grade A-
ACCEPT** **`9fc3fa71`** / pin **`6005fe28`**. Prior
**OUT-1 rem Grade A- ACCEPT** **`21967ca6`** / pin **`9eb4ac80`**.
Prior **OUT-I2 rem2 Grade A- ACCEPT** **`81bf3bce`** / pin
**`aa160c1c`**. Django
grouped-stop surface for `uploaded_*_id_disagreement`; Django still
does not import `mappings_2`. GET never verifies. API **1.37.0**.
Product-default next remains **FE-CM-4**. Prior
**CRM-dupe first-batch hang archived** **`59ab64bc`** — Phase **3
Grade A ACCEPT** **`53846172`** /
pin **`039d9c07`**. Prior **2-EXPORT Grade A- ACCEPT** **`f802668e`** /
pin **`e01b6742`**.
Prior **CRM connection-removal status-aware dependents 0–6 Grade A
ACCEPT / archived** — Phase **6** **`fad0d2d6`**, pin **`3dd916a5`**.
Prior **Phase 5 Grade A ACCEPT**
**`19a2c70b`**.
Prior **Phase 4B Grade A ACCEPT**
**`9b8c80c5`**. Prior
**CRM-dupe first-batch 2-PKG Grade A ACCEPT** **`da02b11a`** / pin
**`7ce2981e`**. Prior **2-OAUTH
Grade A- ACCEPT** **`ddd9a3b0`** / pin **`577da807`**, API **1.35.0**.
Prior **CRM connection-removal
status-aware dependents** Phases **0–3** + **4A Grade A ACCEPT**
**`520f88d6`**. Prior **CRM-dupe first-batch 2-KNOWN Grade A- ACCEPT**
code **`7942c7c3`**, pin **`ac3f538a`**.
Prior **CRM disconnected-connection
removal Phase 1 Grade A- ACCEPT** rem7 nicety **`885c3a87`** (rem6
**`11fc6f90`**, code **`c5dbef3b`**). Prior **CRM connection reclaim
Phase 2 Grade A- ACCEPT** rem **`b707ec4a`**. **DRC-1…4 closed / archived** at
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_clarity.md`
(**DRC-1 Grade A- ACCEPT** tip **`cce61414`**, lineage
**`34eab0ae`** → **`f6c89d07`** → **`cce61414`**; **DRC-2 Grade A- ACCEPT** tip **`4820fa47`**; **DRC-3 Grade A- ACCEPT** tip **`e0bf4529`**, docs pin **`ee15f077`**). **HSR-0…HSR-L complete** — deferred
handshake **`dc575e3b`**, HSR-2 rem **`deeaa18b`**, **HSR-L** **`93cec166`**
Grade **A-**; prior production path reliability **0–5** retained; **FE-CM-4**
product-default next). **DRUX** CRM-dupe review/merge UX + results package is **archived**
(`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_merge_ux_and_results.md`).
**DRUX-0 rem2 Grade A- ACCEPT** tip **`9ffd167e`**. **DRUX-1 Grade A
ACCEPT** tip **`eae1c9de`** (code **`b2f3215b`**): review/summary groups
use a distinct card surface. **DRUX-2 Grade A- ACCEPT** tip **`ddd5d1cb`**
(code **`5864c5bb`**, rem **`b5cccf6f`**, rem2 **`87bf4cbe`**): local merge
freeze is a 1:1 `CrmDuplicateMergePlanLease` (frozen iff
`continuation_run_id` is non-empty); go-back then re-approve is a new
epoch/journal attempt; invalidate requires an epoch-bound signed token;
`options["merge_plan_finalized"]` is retired. **DRUX-3 Grade A ACCEPT** tip **`f35e787c`** (docs pin **`03c09eae`**):
Approve merge plan / Go back to reviewing groups, large `.btn-cta`, stacked
write-mode radios. **DRUX-4 Grade A- ACCEPT** tip **`317e9d16`** (code **`3c122ad9`**, rem
**`317e9d16`**, docs pin **`492e30fa`**): continuation-stamped
`run_output_package.v2` duplicate CSVs on the existing package routes;
run-scoped discovery is layout-bound so a retained v1 ZIP cannot masquerade
as v2 (API **1.31.0**). **DRUX-5** pillars closeout (this slice). Track
**DRUX-1…5** closed. Does **not** displace **FE-CM-4**.

**CRM-dupe first-batch hang (present, archived):** Find-in-CRM
first-batch apply remains pending-safe. CRM OAuth complete is
non-blocking. Django also sends `Prefer: respond-async` for
`create_run_output_package` and `create_export_artifacts`. A 202 package
create shows in-progress on the existing workflow page, not a
prep-failed error. No export-artifact UI. API **1.35.0**. Phase **3
Grade A ACCEPT** dual-process barrier proofs cover implied-RA
first-batch and OAuth complete. Code **`53846172`**, pin **`039d9c07`**.
Track next: **none** (archived). Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_first_batch_synchronous_authorization_hang.md`.
Does **not** displace **FE-CM-4**.

**Secret-store DPAPI diagnostics (VDR-1…VDR-5 closed):**
A paused workflow's Technical Details show only
`projection.paused_effect_diagnostic` (allowlisted Win32 / secret-store
sentence or the generic fallback). Rem `ca72574e` / original `1064dc04`.
Templates do not print checkpoint `evidence.message`. Vault writes use
an exclusive unique candidate then an index commit (**VDR-3 Grade A-
ACCEPT** rem2 `3b819fd0` / rem `422b062d` / original `9f8ab7db`).
**VDR-4** rem `4ba69137` / original `a8430350` adds
`credential_unavailable`, post-lock observer/CAS, Connect CRM reconnect copy,
and Settings vault-health counts. Original `a8430350` was **Grade B — NOT
ACCEPTED**; rem preserves the HubSpot CRM_QUERY structured error and adds the
mandatory operator-journey and bounded lock-stall tests, **Grade A- ACCEPT**.
**VDR-5 Grade A ACCEPT** decision `d9aa8508` / pin `c400c5e5` retains
user-scope DPAPI with no code change because Connect CRM clearly renders
**Credentials unavailable** and provides the in-app **Reconnect** authorization
path. API **1.43.0** (VDR-2 was **1.41.0**; abandon Phase 1 was **1.42.0**).
Django still does not import `mappings_2` for this surface. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/secret_store_dpapi_diagnostics_and_recovery.md`.
Does **not** displace **FE-CM-4**.

**CRM disconnected-connection removal (present):** Disconnected rows on
Connect CRM can be **Remove**d. The POST journals `crm_connection_delete`
(`DELETE /v1/crm/connections/{id}`, HTTP **204**). A still-connected row,
owner mismatch, or in-flight named lease still fails closed. Finished
journeys, terminal workflow checkpoints, and unclaimed or
terminal-claimed read grants no longer block Remove. A completed
zero-group “Find duplicates in CRM” scan can be Removed. Unused
population uploads and column-mapping plans no longer block (Phase
**4B**); an in-progress connected workflow still 409s via the
checkpoint scan. A finished or failed CRM query no longer blocks; an
in-progress query still 409s `CRM query`. Deleting an active query
does **not** terminalize its run. A rejected Remove names the
in-progress dependent type in the flash and carries Technical details
across POST/redirect/GET. Upload/plan and unknown blockers keep the
generic sentence. Query copy does not instruct delete-then-remove.
Authority: parent
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_connection_disconnected_record_removal.md`
(Phase **1 Grade A- ACCEPT** rem6 **`11fc6f90`**; rem7 **`885c3a87`**);
follow-on
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_connection_removal_status_aware_dependents.md`
(Phases **0–6 Grade A ACCEPT**; Phase **6** **`fad0d2d6`**, pin
**`3dd916a5`**). Does **not** displace **FE-CM-4**.

**CRM-dupe abandon + connection-removal cascade (completed / archived):** Phase
**0 Grade A ACCEPT** **`1391a200`** / pin **`95c81f81`**. Phase **1
Grade A ACCEPT** rem2 **`5ec83178`** (rem **`90101aa2`**, code
**`149f19de`**): `POST /v1/workflows/{run_id}/abandon`, public failure
codes, and `remote_outcome`. API **1.42.0**. Phase **2 Grade A ACCEPT** **`46febe5b`**
adds a separate, explicitly-confirmed **Abandon this journey** action using a
signed revision-bound form token and Django `ApiMutation`. Proven-safe source
runs may say no groups will be merged; merge continuations, write authority,
unknown remote outcomes, and unreadable evidence use the uncertainty warning.
The recent list and progress page distinguish operator stop,
connection-removal stop, quarantine, and engine failure. Clear from list stays
cosmetic. Ordinary unconfirmed Remove and Disconnect stay as accepted. Phase
**3 Grade A− ACCEPT** remediation **`f6ac6fc8`** / acceptance docs
**`34aea7ea`** (original **`ae8cfa62`**
is **Grade B — NOT ACCEPTED** history; review-pending pin **`faa659be`**)
adds the server-digest warning page listing every grouped run/dependent and
orphan, uncertainty-aware copy, and a separately journaled confirmation POST.
The API owns the fenced resumable cascade and quarantine bridge at **1.44.0**;
Django still does not import `mappings_2`. Phase **4 Grade A ACCEPT**
**`d0974004`** / acceptance pin **`d72bedc9`** adds the network-free regression
closeout, including the exact three-run warning page and uncertainty-copy
proofs plus the mandatory spawned-worker fake-capability CAS test.
Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_journey_abandon_and_connection_removal_cascade.md`.
Abandon-track next **none** (archived). Does **not** displace **FE-CM-4**.

**CRM connection reclaim (repaired, archived):** Phase **2**
rem **`b707ec4a`** Grade **A- ACCEPT** shipped **Reclaim this connection
on this browser**, then same-day `ddd9a3b0` (2-OAUTH) regressed it.
Hardened Option A repaired the async path: Phase **0** **`ff15af8e`**
Grade **A− ACCEPT**; Phase **1** **`ffaa2d61`**, rem **`e5987926`**
Grade **A− ACCEPT**. First claimant-visible handle is the live 422 or
exactly one owner-authenticated `GET /v1/mutations/{key}`; Django
handle-bearing HTML sends `Cache-Control: no-store`; the stage expires
at the offer `expires_at`. API **1.38.0**. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_connection_reclaim_async_dispatch_regression.md`.
Parent:
`mappings_2/codex_context/cross_agent_eval/project_implementations/crm_connection_session_ownership_honesty.md`.
Does **not** displace **FE-CM-4**.

**Local two-process launch (present):** Django and API start via
`scripts/start_local.ps1` / `start_local_https.ps1` with one parent-resolved
absolute Python (repo `.venv` preferred). Service failures before readiness
surface service name, exit code, interpreter path, and sanitized diagnostic
tail (no bare ambient `python` roulette). Details and proofs under mappings_2
authority
`production_hubspot_crm_duplicate_operator_path_reliability.md` Phases **1A/1B**.

**HTTPS loopback TLS (present, HSR):** `manage.py runserver_https` accepts plain
TCP and completes TLS handshakes in the per-connection worker thread (never
inside the accept loop), so stalled browser preconnects cannot wedge later
tabs after readiness. Per-machine TLS under `%LOCALAPPDATA%\EasyImports\tls`;
session **Secure + SameSite=Lax**. Network-free proofs:
`web/importer/test_https_loopback_handshake_hsr0.py` … `…_hsr2.py` and
`test_https_loopback_https2.py`. Authority:
`mappings_2/codex_context/cross_agent_eval/project_implementations/https_loopback_tls_handshake_reliability.md`.
**HSR-L** Grade **A- ACCEPT** tip **`93cec166`**: opt-in dual-process External
Client App **authorization-code** flow on **127.0.0.1:8001** (real Salesforce
authorize → product callback; not Phase 7C refresh pilot). Default CI skips live.

**CRM-dupe session product identity (present):** Phase **3 Grade A- ACCEPT** tip
**`ce914f0a`**. Review/continuation projections store with explicit role +
source and do not replace the active primary; closed product resolver maps only
Account/Person review boxes to `easyimports.duplicate_resolution`; foreign
keys fail closed. Proof:
`web/importer/test_crm_duplicate_product_identity_phase3.py`.

**Production path reliability (present):** Phase **4A** dual-process network-free
full path (`web/importer/test_production_path_reliability_phase4a.py`). Phase
**4B** controlled production HubSpot Company canary under explicit per-run flags
only (`test_production_path_reliability_phase4b.py` +
`production_canary_phase4b.py`); accepted tip **`655e495e`**. The ordinary
frontend path can connect to a production HubSpot portal and merge synthetic
Companies when authorized — not a claim that historical storage **7B** is
production, and not blanket customer-data authorization.

**Local storage / registration recovery (present):** Settings + Connect wizard
support **Start over with different credentials** for stuck **UNKNOWN**
registration puts: read-side reconcile of registration inventory **and**
provider connectability; refuse Start over while a dispatch lease is active;
terminalize only via explicit operator action (`operator_start_over`). Technical
details (closed disclosure) show failing **route**, HTTP status, and API
**error_id** on Settings, Connect hub (including registration-inventory load
failures), setup wizards, and CRM duplicate start page load failures. Secrets
never appear in technical details. **Phase 3 Grade A- ACCEPT (O1):**
dual-process proof that a Practice/fake connection for the same browser owner
remains visible on CRM Duplicates after a real API process restart against
durable isolated storage
(`web/importer/test_local_crm_dupe_e2e_storage_phase3.py`).
**Phase 4 Grade A- ACCEPT (O2/O3):** dual-process Companies CRM scan from a
connected fake CRM reaches review-ready with ≥1 group; unique singles yield
“No duplicate groups found”
(`web/importer/test_local_crm_dupe_e2e_storage_phase4.py`).
**Phase 5 Grade A- ACCEPT (O4):** dual-process multi-window review (1–5 of 6,
then 6–6); genuine person quarantined group (no survivor radios); lands on
merge plan (`web/importer/test_local_crm_dupe_e2e_storage_phase5.py`).
**Phase 6 Grade A- ACCEPT (O5):** dual-process auto-merge Continue (all-auto →
merge plan; mixed residual manual; T=89 rejected on start; no CRM write from
auto alone) (`web/importer/test_local_crm_dupe_e2e_storage_phase6.py`).
**Phase 7A Grade A- ACCEPT (O6 fake):** dual-process Company + **People** freeze
→ dry_run (zero CRM mutation) / execute (losers only); terminal Groups
processed + record-level accountability
(`web/importer/test_local_crm_dupe_e2e_storage_phase7a.py`).
**Phase 7B Grade A- ACCEPT (O6 HS Company live FE):** tip **`8ed8da6b`**.
**Phase 7C Grade A- ACCEPT (O6 SF Account live FE):** tip **`bfad6a9d`** (docs
pin **`c2c62d7c`**); dual-process Connect wizard → register/connect Salesforce
→ upload Accounts → review → freeze → dry_run / execute with independent SOQL
verify; opt-in `EASYIMPORTS_CRM_DUPE_STORAGE_7C_LIVE=1` + `ORG_CLASS`
(`web/importer/test_local_crm_dupe_e2e_storage_phase7c.py`;
`web/tools/run_api_sf_live_refresh_gate.py`).

## Intent wizard (accepted, local stack through `f595497b`; **7A/7B** defaults)

Import setup no longer asks operators to pick internal product keys such as
“single dataset.” Operators choose:

1. **What are you preparing?** Accounts or People
2. **What should EasyImports do?** Defaults to **Match it against my CRM**;
   secondary **Clean and prepare my file (no matching)** (Phase **7A**)
3. **If matching:** Upload CRM exports, **or** use a connected CRM (D8 default:
   connected when any connection exists, else upload). No “not needed for
   clean-only” choice; CRM-data field hidden on clean-only
4. **People + clean-only:** Contacts or Leads output
5. Destination target is **not** an operator field (Phase **7B** / D6); catalog
   default or connected execution target is applied internally


A pure router (`web/importer/setup_router.py`) derives the existing backend
product boxes. `product_key` freezes only after workflow create / recovery from
an accepted API projection. Setup intent lives on dedicated draft fields
(`setup_entity`, `setup_operation`, `setup_reference_source`,
`setup_people_output`, `setup_connection_id`, `setup_revision`); pre-migration
product-key-only sessions still configure/create.

Lifecycle invariants accepted with `f595497b` (and Phase **5** connection
binding):

- Lock order: `ImportSession` → `SourceFile` (pk order) → `ApiMutation`
- Create preclaim under one draft/source lock; network dispatch outside
- Detach completed/rejected incompatible uploads without deleting bytes;
  pending/unknown cannot detach and retain retry controls
- Post-detach reuse requires intentionally detached predecessor +
  `replacement_of`
- Product identity conflicts fail closed; empty key may freeze from projection
- Technical details show operator intent, draft-derived product key, and frozen
  product key
- Connected-CRM import and H1-A handoff mutations use form-scoped signed tokens
  and journal exact-retry (double-submit safe)

Importer gate after CRM duplicate journey UX remediations (2026-08-03 tip
`89dd01da`; prior FE-EXEC accept `c9d4d017`):

```text
python web/manage.py test importer
# Found 307 test(s). OK
python web/manage.py check  # clean
git diff --check 25b0ffae^..89dd01da -- web/importer  # clean
```

Migrations through `0007_sourcefile_detach_state`. Phase **5** human-accepted
tip `cdff89c5` (API was **1.7.0** at accept). **FE-EXEC** human-accepted tip
`c9d4d017` (real-process browser → Django → API execute on fake stack; private
effect registry; subprocess seed helper `web/tools/seed_fake_crm_org.py`).
Phase **6A** human-accepted tip `ab01b9b4` (CampaignMember neutral plan;
list_import **v6** / checkpoint `crm_neutral_intent.v2`). Phase **6B**
**human-accepted** tip `0c7459f4` (base `ed3354ef`; rem1 `1c64a689`): public
CM modes `disabled|preview|dry_run|execute`; fake + SF capabilities;
network-free only. Live CM Phase **7E** **human-accepted** tip **`b6d4beeb`**
Grade **A-**. Phase **7A-P** (**human-accepted** tip **`72746bc9`**) adds local **Settings**
UI (`/settings/`) for storage location and Salesforce Connected App
registration; Phase **7B-P** (**human-accepted** tip **`54c41083`**, pin
**`0c09e3d9`**) adds HubSpot private-app / OAuth registration on the same
Settings shell; Phase **7B-Q** (**human-accepted** tip **`b420b521`** Grade **A**,
base **`ee3f10a7`**) adds HubSpot product `CRM_QUERY` (Connect-bound live
transport; REST contract rem); Phase **7C-HS** (**human-accepted** tip
**`e8c09f36`** Grade **A**, pin **`396992dc`**) adds HubSpot Contact
create/update people write (SQLite claim ledger; live canary proven);
Phase **7C-SF** (**human-accepted** tip **`afdaf6a3`** Grade **A**, pin
**`317aba73`**) adds Salesforce people write (connection-bound live transport;
real Contact create/update/cleanup canary); loopback `REMOTE_ADDR` only; API
**1.10.0**; OpenAPI checksum `2d7344fb…`; Django generated client regenerated.
Published baseline: see `mappings_2/codex_context/handoff_doc.md` after
`origin/main` sync.

**Column-mapping preflight (MAP-2/3 Grade A; MAP-R1–R5):** session routes under
`/sessions/<id>/column-mapping/` (views `column_mapping_views.py`; no
`mappings_2` import). Operator maps messy headers → target once; confirm is
journaled. Workflow create binds plan digests (MAP-3). **MAP-R1** selects
Account/Contact/Lead/People-matching catalog ids from setup intent and **fails
closed** when setup is incomplete/unfrozen or CRM destination cannot resolve
(no silent catalog invent; create blocked with recovery to setup/files).
**MAP-R2** improves plan-create auto-detect; **MAP-R3 Grade A** attaches
bounded row-aligned sample previews on create and exposes journaled atomic
multi-row review on the API. **MAP-R4** ships the Django stable mapping table
(Action / Maps to / Source / examples), filter counts, accessible field picker
(progressive enhancement), and Save draft / Confirm mapping via atomic review
with a no-JS multi-row form fallback. **MAP-R5 Grade A-** tip **`3190f7fe`** linearizes the journey: Upload → Map
columns → Configure → Review & download; Map columns is primary after required
upload; Start import requires a **current** confirmed bind (upload/destination
currency); confirm continues to settings; Accounts clean-only hides Type of
people; dual-process accept proves samples, manual Ignore reload, and terminal
no-live-CRM copy (`test_column_mapping_map_r5_journey.py`). **OUT-5A/5B Grade A- ACCEPT**
**`bb501983`** / pin **`cbb6d748`**: Map columns has **Ignore all
unmapped**; ignore that clears the last unresolved row confirms and
lands on Configure with a persisted bind (not a redirect-only
save_draft). Required destination gaps still fail closed. Django
still does not import `mappings_2`. **OUT-6A Grade A- ACCEPT**
**`0124edc9`**: People list-import **Create Contacts only** help
is visible. Salesforce / product / fake say unmatched people become
Leads; HubSpot says there is no Lead. Accounts / clean-only do not
show that Salesforce Lead sentence. Django still does not import
`mappings_2`. **OUT-6B rem2 Grade A- ACCEPT** **`93114142`** / pin
**`6223a57c`**: configure grouping applies only to the three import
products. Duplicate-resolution still shows entity, date, and
execution. **`8d951cba`** / **`0808b926`** remain **NOT ACCEPTED**
history. Django still does not import `mappings_2`. **OUT-7 rem Grade A- ACCEPT**
**`1379b805`** / pin **`eca46846`** (**`c6743368`** is **B+ — NOT ACCEPTED**): list-import / clean-only review
tables show OUT-1 review-core ∪ typed extras. No 7-col LIST_CANON
back-fill. Full frame names stay in Technical details. Narrow
viewports scroll horizontally. Django still does not import
`mappings_2`. **OUT-8 rem2 Grade A- ACCEPT** **`1f1eed82`** / pin
**`42966ae1`**: pending cards are mutation-kind specific. Start
import says Setup is all done. Package create says Preparing your
results and does not change CRM records. CRM-write authorize does
not use the cleaning sentence. UNKNOWN still offers Retry saved
action. Dual-process probes use `data-mutation-pending` /
`data-mutation-kind`. **`4dcb1fff`** / **`9ad4762d`** remain **NOT
ACCEPTED** history. Django still does not import `mappings_2`.
**OUT-9 Grade A — ACCEPT** **`07937a10`** / implementation docs pin
**`9c80f6a0`** / acceptance docs **`811916ae`**: list-duplicate
keep-row radios remain inside each comparison table, while the
`exclude_group` radio renders immediately below the table with the same
`selected__N` name and unchanged Django→API command shape. The layout also
applies wherever an existing grouped decision supplies that whole-group option.
Proofs: `web/importer/test_list_import_operator_output_out_9.py` and
`web/importer/test_list_import_full_operator_path_phase4b.py`. Django still
does not import `mappings_2`.
**OUT-4 Grade A- ACCEPT** **`35a1119e`** / pin **`67b7506d`**: the API
drops blank / whitespace source headers at ingest, so Map columns
sees only remaining named headers. Django still does not import
`mappings_2`. **MAP-R6 Grade A-** tip **`0bbb17ed`**: connected CRM mapping destinations
resolve fake / Salesforce / HubSpot offline field inventories (Accounts →
Account or Company; People matching → Contact); service-owned SF AccountId
excluded; unknown providers still fail closed. OpenAPI client regenerations
must use project **`.venv`**.

**Mapping-track next (mappings_2):** R0–R6 offline boundary complete (live
describe canary only if separately authorized). **Available parallel:**
**FE-CM-4** — network-free browser→Django→API CM E2E.
**FE-CM-3** **human-accepted** Grade **A-** tip **`eea8e40f`**: list-import
configure freezes `campaign_member_policy.v2` + journaled
`campaign_match_resolutions`; stack ceiling projection; default mode disabled
(ignores exploratory drafts); search/pick proxy; clear-draft action; CM
technical details. FE-CM-2 **human-accepted** Grade **A-** tip **`cb69bd39`**
(API **1.12.0**). FE-CM-1 **human-accepted** Grade **A-** tip **`682a9689`**.
**R5** preserved product-parallel. Step 14 remains **deferred**.

The current implementation advances the frontend and backend together to
EasyImports API v1.12.0 (FE-CM-2 row-capable CampaignMember policy/plan;
FE-CM-1 Campaign lookup retained). Grouped decisions now submit a complete list of exact
`{group_id, selected_option_id}` pairs. Each group must appear once,
including when the operator accepts the backend-selected default. Uploaded-list
duplicate tables include an explicit whole-group exclusion option, and CRM
no-match outcomes are explicit options. Choosing “Continue without an Account
and route this person as a Lead” strictly prevents Account provisioning and
later Contact selection for that person.

The CRM duplicate-resolution journey (redesigned Phases **4A/4B** via
crm_duplicate_journey) is operator-usable for the fake-stack path:

- **Where should EasyImports look?** — **Find duplicates in CRM** (cquire_all)
  or **Upload records to analyze** (uploaded_population). Pre-grouped /
  selected-ID primary controls are not on the redesigned start page.
- Upload path: ordinary file → Record ID map → progress → five-group review.
  CRM scan path: submit → progress → five-group review (no mapping).
- Progress auto-redirects into review when analysis is ready; failed analysis
  stays on progress with exact retry of journaled implied-read apply.
- Five-group review: comparison tables, recommended survivor defaults,
  quarantine/blocked allowed-actions only, explicit per-group choices,
  evidence/conflicts content, save-and-exit after the first saved window,
  recent-journey resume links. Window submit is journaled
  (submit_duplicate_review_window) with stripped owner_session, specialized
  receipt validation, rejection handling, posted window binding for exact
  retry, and UNKNOWN explicit_retry recovery.
- **Phase 5A:** after the final five-group window, Django lands on the merge
  summary (`crm_duplicate_journey_merge`). The API freezes digest-bound reviewed
  results and a merge-plan handoff (`GET …/duplicate-reviewed-result`, journaled
  `POST …/duplicate-merge-plan-handoff` → decision-set + continuation bind). The
  summary shows counts and only connection-supported preview/dry-run/execute
  modes; write authorization remains a separate journaled `authorize_effect`
  (source-read grant never authorizes writes).
- **Phase 5B:** true dual-process HTTP acceptance (API + Django runserver;
  `requests` browser — not TestClient) proves both redesigned branches through
  multi-window review (6 groups → windows 1–5 and 6–6), freeze, dry_run (zero
  CRM mutation) / execute (frozen losers only), and terminal reporting. API
  suite also proves decision/plan digest convergence, decline + quarantine
  never merge, and every-record accounting. Seed helper `--pair-count`. Merge
  authorize journals on the workflow session and tolerates async Prefer
  respond-async. JSON mutations strip journaled `owner_session` before wire POST.
- **Phase 7C (Grade A- tip `344b5354` on `main`):** optional start control
  **Auto-merge high-confidence groups** with integer threshold **T (90–100)**;
  values below 90 and non-integer/corrupt durable values fail closed (no clamp).
  T freezes on journey start + read-grant bodies and on the durable root-attempt
  claim (migrations 0010 field + 0011 backfill). After analysis is ready, the
  operator **Continue** POST journals `POST …/duplicate-auto-disposition`
  **once** (progress only; browser GET never dispatches auto-disposition;
  `expected_revision` freezes before wire so exact-retry survives
  commit-before-local-save). All-auto lands on merge summary; mixed auto shows
  remaining manual groups only (incl. high-score blocked ineligible). Merge
  summary lists **Auto-approved (high confidence)** vs operator-approved
  counts and may show T / `decision_origin` / review contract under Technical
  details. Tests: `test_crm_duplicate_journey_phase7c.py` + dual-process +
  migration suites.
- **DRUX-2 (present):** after invalidate / go-back, the operator can approve
  the merge plan again. Django persists a 1:1 `CrmDuplicateMergePlanLease`
  (migration `0013`). Freeze is `bool(lease.continuation_run_id)`. Finalize
  and invalidate bind `form_instance` and `logical_action_generation` to the
  lease epoch. Both invalidate forms POST an invalidate-specific signed
  token (`crm-dupe-5a:invalidate:{epoch}:…`); missing tokens fail closed.
  Exact retry of the same approve still replays. A stale invalidate cannot
  clear a newer continuation. Proofs:
  `web/importer/test_crm_duplicate_journey_drux2_reapprove.py`. Visible
  buttons say Approve merge plan / Go back to reviewing groups (**DRUX-3**
  **`f35e787c`**). Terminal **Download results package** on a CRM-dupe
  continuation is **`run_output_package.v2`** (groups / members / optional
  merge outcomes / run summary); list-import v1 membership is unchanged
  (**DRUX-4** **`317e9d16`**).
- **DRC-1…3 (present):** start-page score helpers are entity-specific
  (Companies ≠ People; unselected list hidden). Companies: **95** same
  name + same domain; **90** same domain; **80** exact name when every
  account in the pair has no meaningful domain; **70** exact name with
  exactly one meaningful domain. People group-confidence is **100 / 90 /
  75** only. Auto-merge floor stays **90**. Review comparison uses locked
  operator labels with blanks as em dashes, plus one winner sentence.
  Field-fill: empty survivor fields fill from losers; populated values
  stay. `EvidencePolicy.version` is **5** (GFC-7B). Authority:
  `mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_review_clarity.md`.
  Does **not** displace **FE-CM-4**.

Full Salesforce duplicate-resolution lifecycle detail and remaining live/R5
work remain in
`mappings_2/codex_context/DUPLICATE_RESOLUTION_FRONTEND_LIFECYCLE.md`.
The older Step 14 Account-list live dry-run remains deferred with its complete
zero-write acceptance contract preserved; it is not obsolete.

The full import journey now defaults to a customer-facing presentation layer.
The landing, product selection, upload, configuration, workflow, and
service-unavailable pages use task-oriented labels and summaries. Raw product
keys, target-provider IDs, upload IDs, run/revision identity, intent and
handoff IDs, fingerprints, digests, receipt payloads, manifests, the command
journal, and the stored workflow projection remain available only inside
closed `Technical details` disclosures. The terminal view explicitly states
when no live CRM changes were authorized and summarizes processed, ready,
excluded, and failed rows. Default downloads prioritize prepared data and
non-empty exception files; diagnostic artifacts stay in technical details.
Uploaded-list duplicates, ambiguous CRM person matches, and ambiguous CRM
Account matches render as one comparison table per backend group with one radio
selection per group. Backend `selected` values alone control the checked
default; recommendation markers are displayed but never converted into a
frontend-selected default. Synthetic outcomes use backend labels, including
whole-group exclusion, continue-unmatched, net-new Account, quarantine, and
force-Lead-without-Account choices.

The root `web/` tree is a server-rendered Django consumer of the authoritative
EasyImports HTTP API v1.12.0. Django and the API run as separate processes;
Django has no runtime path injection or supported in-process workflow fallback.
The approved local defaults are API `127.0.0.1:8000` and Django
`127.0.0.1:8001`.

## Closeout status

Migration Step 13 is complete. Step 14's network-free Account-list Developer
dry-run implementation is pushed through `1d7d489`, but the live
two-process acceptance has not been authorized or run. Step 14 itself added no
importer feature: its only Django production change is fail-closed external
SQLite/media path enforcement when `EASYIMPORTS_API_TARGET_PROFILE` is
explicitly `developer-dry-run-v1`. The separate customer-facing presentation
and grouped review refinement is pushed in
`4807cd2a Refine EasyImports frontend review experience`. Normal startup
remains unchanged.

## Implemented boundary

- Browser requests terminate at Django and remain CSRF protected.
- Every session, workflow, mutation, source, and artifact lookup is scoped
  through the anonymous Django-session owner. Archived sessions return 404 from
  every normal workflow, mutation, upload, and artifact route.
- FE-CM track `/health` pin remains `1.12.0` for that suite; ordinary importer
  generated contract tracks live API (**1.43.0** after VDR-4; abandon Phase
  **1** was **1.42.0**; VDR-2 was **1.41.0**; prior **1.37.0** OUT-I2 uploaded-id disagreement types;
  prior **1.35.0** CRM-dupe first-batch **2-OAUTH**,
  **1.34.0** **2-KNOWN**, **1.33.0** disconnected-connection removal,
  **1.32.0** CRM connection reclaim, **1.31.0** DRUX-4, **1.27.0**
  CRM-dupe Phase **7B** auto-disposition).
  API-backed actions fail closed on version mismatch for the active client pin.
- FE-CM-1 Campaign lookup is proxied via
  `EasyImportsApiClient.crm_campaigns_search` / `crm_campaign` /
  `crm_campaign_member_statuses` (HTTP only; no Salesforce SDK in Django).
- **FE-CM-3** list-import configure freezes optional `campaign_member_policy.v2`
  on journaled create_workflow: column maps, default Campaign/status, durable
  lookup-bound `campaign_match_resolutions` on `ImportSession.options` (survives
  multi-search; clear-draft action; ignored when CM mode is disabled so the
  required disabled default remains usable). Connected import CM ceiling is
  projected from the connection stack (SF/fake up to execute; HubSpot disabled
  only; prefers API `maximum_authorization.campaign_member_writes` when present).
  Technical details show resolved Campaign Ids without secrets.
- **OUT-3** list-import / account-list / single-dataset configure hides
  `mode__delivery` and defaults it to disabled. The page does not ask
  "Choose whether downloadable result files should be created." Cleaned
  preview members still ship on the API run-output-package. Grade
  **A- ACCEPT** **`6aef21e0`** / pin **`67d8c0d1`**. Django still does
  not import `mappings_2`.
- Local Settings (`/settings/`) is loopback-only and proxies registration /
  storage mutations to `/v1/settings/*`; registration create/rotate/delete use
  signed form tokens, Django `ApiMutation`, and the API mutation journal
  (`Idempotency-Key`). Secrets are validated before mutation create, staged
  ephemerally for dispatch only, and never durable in Django or journal bodies.
  Registration delete is HTTP **204**.
- Under explicit `developer-dry-run-v1`, Django rejects `DATABASE_URL` and
  requires `EASYIMPORTS_DB_PATH` plus `EASYIMPORTS_MEDIA_ROOT` to resolve
  outside the repository. It still receives no Salesforce credential, token,
  instance URL, or real org identity.
- The four public products and target mode maxima come from strictly validated
  API catalogs. Workflow creation uses generated OpenAPI models plus the
  product-specific cross-field rules.
- Single-dataset creation first validates effective source content/profile/kind
  metadata and then proves that the source can produce the selected Account,
  Contact, or Lead output. Omitted people kind freezes as `generic`.
- Unknown future workflow statuses and decision types remain visible and
  read-only. Known variants validate strictly.
- The three row-selection decision types use grouped, labeled comparison tables
  and enforce exactly one submitted option for every stored group before
  dispatch.
- All six current decision types are rendered and submitted. Account and Person
  duplicate review shows members, evidence, conflicts, ranking, recommendations,
  and projected survivor tables, with sequential progress and default-winner
  confirmation for CRM group review.
- Effect authorization shows intent, gate phase, effect phase, target identity,
  fingerprint, work digest, confirmation, supported modes, and the current work
  summary. Track-specific mode labels name CRM merges for
  `duplicate_execution` and reference load for `reference_acquisition`.
- Terminal track receipts, row/group accountability, delivery manifests,
  effect grants, and the decision-set handoff result are rendered explicitly.

## Persistence and replay

Every outbound API POST, uploads included, uses one `ApiMutation` journal with a
globally unique idempotency key. The journal freezes method, route, resource
identity, JSON or multipart metadata, editable-form digest, response, resource,
and result.

Logical action uniqueness is enforced independently of the signed form-instance
ID. Concurrent tokens for the same run, revision, action/intent, and duplicate
group therefore converge on one mutation when their editable values match.
Changed values fail locally.

Response classification is fixed:

- accepted receipt or upload resource → `completed`;
- `CommandReceipt(outcome="rejected")` or a contract-valid deterministic
  404/409/422 → `rejected`;
- connection loss, timeout, 5xx, malformed success, or an interrupted dispatch
  → `unknown`, or an expiring `pending` lease after process loss.

Completed and rejected forms return their frozen result even after the workflow
projection advances. An expired pending mutation may reacquire a lease with its
exact key and payload. An unknown mutation requires an explicit exact-retry POST.
GET never dispatches. Idempotency-conflicted keys cannot automatically create a
replacement; other rejections require explicit acknowledgement first.

Leases use a random fencing token, database-derived expiry longer than the HTTP
timeout, a committed acquisition before I/O, and conditional response
persistence. A late worker cannot overwrite a newer frozen result.

## Upload, review, and artifact integrity

- Each `(session, role)` has one active upload slot. Pending, unknown, and
  completed uploads reserve it. Rejected uploads require an explicit,
  transactional replacement authorization.
- Upload bytes are atomically written to a stable local path before the mutation
  and one-to-one `SourceFile` are committed together. Retained bytes are never
  removed for pending or unknown mutations. Orphan discovery is separate from
  mutation dispatch.
- A session operator label freezes when review activity starts. Duplicate
  `decided_by` and `decided_at` are server-owned; the timestamp is allocated by
  the first winning mutation and remains stable on retry.
- Duplicate review follows source → review → decision-set receipt → continuation
  lineage. The completed decision-set handoff comes only from
  `CommandReceipt.result`.
- Artifact links appear only for `preview` or `delivered` metadata with a stored
  URL. The proxy requires the exact stored run/artifact content path under the
  configured API origin, rejects redirects, streams through a temporary file,
  verifies byte count and SHA-256, and preserves only an ETag that agrees with
  the metadata digest.

## Generated contract and legacy behavior

`api_models_generated.py` and `api_contract_generated.py` are generated from the
canonical OpenAPI document by pinned `datamodel-code-generator/0.48.0`, with
checksum matching `web/tools/generate_api_contract.py` `EXPECTED_SHA256` /
`tests/mappings_2/fixtures/v1_openapi.sha256` (API **1.42.0** after
abandon Phase **1**; VDR-2 was **1.41.0**). Manual edits are prohibited.
`web/tools/generate_api_contract.py --check` performs deterministic regeneration
and a clean-diff check.

Pre-migration sessions are `is_legacy` and admin-only. Public deletion is local
archival and never claims to remove API resources. The legacy demo UI is
explicitly deferred; sample CSV assets are retained but cannot bypass the
ordinary upload mutation journal. Pricing and optional WooCommerce checkout
remain available and have regression coverage.
