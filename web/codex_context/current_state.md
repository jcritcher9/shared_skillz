# EasyImports Web — Current State

The root `web/` tree is a server-rendered Django consumer of the
EasyImports HTTP API. Django and the API run as separate processes.
Django has no runtime path injection, no supported in-process workflow
fallback, and does not import `mappings_2`. Local defaults are API
`127.0.0.1:8000` and Django `127.0.0.1:8001`.

Last updated: 2026-09-12 (lean context cut: this file is the only living
context document under `web/codex_context/`. This area has no scoping
documents, so it keeps no queue and no catalog).

Product and cross-layer authority for CRM duplicate, list-import, and
Campaign Member work lives under `mappings_2/codex_context/`. This file
describes Django/web behavior only.

## Context model

Lean. Context root is `web/codex_context/`. Living files are
`current_state.md`, `thin_strategy.md`, and `archived/`. Classify on
`origin/main` is **NO_QUEUE** once the cut has landed: handoff gone and
no queue file.

## Intent wizard

Import setup does not ask operators to pick internal product keys.
Operators choose:

1. **What are you preparing?** Accounts or People
2. **What should EasyImports do?** Defaults to **Match it against my CRM**;
   secondary **Clean and prepare my file (no matching)**
3. **If matching:** upload CRM exports, or use a connected CRM (connected
   when any connection exists, else upload). CRM-data field is hidden on
   clean-only
4. **People + clean-only:** Contacts or Leads output
5. Destination target is not an operator field; catalog default or
   connected execution target is applied internally

A pure router (`web/importer/setup_router.py`) derives backend product
boxes. `product_key` freezes only after workflow create / recovery from
an accepted API projection. Setup intent lives on dedicated draft fields
(`setup_entity`, `setup_operation`, `setup_reference_source`,
`setup_people_output`, `setup_connection_id`, `setup_revision`);
pre-migration product-key-only sessions still configure/create.

Lifecycle invariants:

- Lock order: `ImportSession` → `SourceFile` (pk order) → `ApiMutation`
- Create preclaim under one draft/source lock; network dispatch outside
- Detach completed/rejected incompatible uploads without deleting bytes;
  pending/unknown cannot detach and retain retry controls
- Post-detach reuse requires an intentionally detached predecessor plus
  `replacement_of`
- Product identity conflicts fail closed; empty key may freeze from projection
- Technical details show operator intent, draft-derived product key, and
  frozen product key
- Connected-CRM import mutations use form-scoped signed tokens and
  journal exact-retry (double-submit safe)

## Column mapping and list import

Session routes under `/sessions/<id>/column-mapping/`
(`column_mapping_views.py`; no `mappings_2` import). Operator maps messy
headers → target once; confirm is journaled. Workflow create binds plan
digests.

- Catalog ids for Account/Contact/Lead/People-matching are selected from
  setup intent and **fail closed** when setup is incomplete/unfrozen or
  the CRM destination cannot resolve (no silent catalog invent).
- Map columns is primary after required upload. Journey is Upload → Map
  columns → Configure → Review & download. Start import requires a
  **current** confirmed bind. Accounts clean-only hides Type of people.
- Map columns has **Ignore all unmapped**; ignoring the last unresolved
  row confirms and lands on Configure with a persisted bind. Required
  destination gaps still fail closed.
- The API drops blank / whitespace source headers at ingest, so Map
  columns sees only remaining named headers.
- Connected CRM mapping destinations resolve fake / Salesforce / HubSpot
  offline field inventories (Accounts → Account or Company; People
  matching → Contact). Service-owned Salesforce AccountId is excluded;
  unknown providers fail closed.
- List-import / clean-only review tables show the review-core ∪ typed
  extras. Narrow viewports scroll horizontally. Full frame names stay in
  Technical details.
- List-duplicate keep-row radios stay inside each comparison table; the
  `exclude_group` radio renders immediately below the table with the same
  `selected__N` name.
- People list-import **Create Contacts only** help is visible.
  Salesforce / product / fake say unmatched people become Leads;
  HubSpot says there is no Lead. Accounts / clean-only do not show that
  Salesforce Lead sentence.
- Configure grouping applies only to the three import products.
  Duplicate-resolution still shows entity, date, and execution.
- List-import / account-list / single-dataset configure hides
  `mode__delivery` and defaults it to disabled. Cleaned preview members
  still ship on the API run-output-package.
- Pending cards are mutation-kind specific. Package create says
  Preparing your results and does not change CRM records. CRM-write
  authorize does not use the cleaning sentence. UNKNOWN still offers
  Retry saved action.
- Grouped decisions submit a complete list of exact
  `{group_id, selected_option_id}` pairs. Each group must appear once,
  including when the operator accepts the backend-selected default.
- Choosing “Continue without an Account and route this person as a Lead”
  strictly prevents Account provisioning and later Contact selection for
  that person.
- Campaign Member: list-import configure can freeze
  `campaign_member_policy.v2` plus journaled `campaign_match_resolutions`;
  default mode is disabled. Connected import CM ceiling is projected from
  the connection stack (Salesforce/fake up to execute; HubSpot disabled
  only). The default member status is a Campaign-scoped picker, not free
  text: the configure view ephemerally fetches each resolved Campaign's
  member statuses (FE-CM-1) and the form bounds the default-status
  ChoiceField to that Campaign's allowed statuses (single Campaign), the
  validated intersection (multiple Campaigns), or requires a per-row
  member-status column when the intersection is empty or a Campaign Id
  column is mapped. The Campaign axis and status axis stay independent.
  FE-CM-1 Campaign lookup and member-status browse are proxied over HTTP
  only (no Salesforce SDK in Django). On the fake stack, Django drives the
  residual CampaignMember operator path across a real API process: freeze
  `campaign_member_policy.v2`, `authorize_effect` dry_run (zero mutation)
  then execute after verified Person IDs, exact replay with no double
  mutation, stale-revision and disconnect fail-closed on execute, and
  unmocked name search refuses `too_many_matches` / truncated freeze.
  Terminal `run_output_package.v1` is reachable after CM execute.

The landing, product selection, upload, configuration, workflow, and
service-unavailable pages use task-oriented labels. Raw product keys,
target-provider IDs, upload IDs, run/revision identity, fingerprints,
digests, receipt payloads, manifests, the command journal, and the stored
workflow projection remain inside closed Technical details. The terminal
view states when no live CRM changes were authorized and summarizes
processed, ready, excluded, and failed rows.

## CRM duplicate journey

The redesigned journey (`crm_duplicate_journey`) is operator-usable on
the fake-stack path:

- **Where should EasyImports look?** — **Find duplicates in CRM**
  (`acquire_all`) or **Upload records to analyze** (`uploaded_population`).
  Pre-grouped / selected-ID primary controls are not on the start page.
- Upload path: ordinary file → Record ID map → progress → five-group review.
  CRM scan path: submit → progress → five-group review (no mapping).
- Progress auto-redirects into review when analysis is ready; failed
  analysis stays on progress with exact retry of the journaled implied-read
  apply. GET never writes and does not dispatch or resume work.
- Five-group review: comparison tables, recommended survivor defaults,
  quarantine/blocked allowed-actions only, explicit per-group choices,
  evidence/conflicts, save-and-exit after the first saved window,
  recent-journey resume links. Window submit is journaled
  (`submit_duplicate_review_window`) with stripped `owner_session`,
  specialized receipt validation, posted-window binding for exact retry,
  and UNKNOWN explicit-retry recovery.
- After the final five-group window, Django lands on the merge summary.
  The API freezes digest-bound reviewed results and a merge-plan handoff.
  The summary shows counts and only connection-supported
  preview/dry-run/execute modes; write authorization remains a separate
  journaled `authorize_effect` (source-read grant never authorizes writes).
  After a mixed execute cycle the workflow parks at
  `awaiting_execution_continuation`. Continue remaining merges and Finish
  with remaining exceptions POST
  `continue_duplicate_execution` /
  `finish_duplicate_execution` (new form tokens, new idempotency keys);
  they are not `authorize_effect` and not the merge-page GET retry link.
- Completed review renders one window and posts only that bound window.
  An amendment preclaim is a freeze fence, so real finalize cannot bind
  old decisions while a new artifact is being published.
- Optional start control **Auto-merge high-confidence groups** with
  integer threshold **T (90–100)**; values below 90 and non-integer
  durable values fail closed (no clamp). After analysis is ready, Continue
  journals `POST …/duplicate-auto-disposition` once (progress only;
  browser GET never dispatches). All-auto lands on merge summary; mixed
  auto shows remaining manual groups only.
- After invalidate / go-back, the operator can approve the merge plan
  again. Django persists a 1:1 `CrmDuplicateMergePlanLease`. Finalize and
  invalidate bind `form_instance` and `logical_action_generation` to the
  lease epoch. Stale invalidate cannot clear a newer continuation.
- Visible buttons say Approve merge plan / Go back to reviewing groups.
  Terminal **Download results package** on a CRM-dupe continuation is
  `run_output_package.v2`.
- Start-page score helpers are entity-specific. Companies: **95** same
  name + same domain; **90** same domain; **80** exact name when every
  account in the pair has no meaningful domain; **70** exact name with
  exactly one meaningful domain. People group-confidence is **100 / 90 /
  75** only. Auto-merge floor stays **90**. Review comparison uses locked
  operator labels with blanks as em dashes. Empty survivor fields fill
  from losers; populated values stay. `EvidencePolicy.version` is **5**.
- Incremental Company review can open while auto-merge is pending;
  auto-queued wait is count-free.
- Exact-only Account and Person working groups, local partition, and the
  immutable review store sit beside the older path. Review cursor state
  is per review run. Existing-row replay verifies the stored blob digest
  before unpickling. Created date sits after Name in the comparison
  table. End early requires five operator-saved decisions and binds the
  live window.
- After merge authorization of a leased `duplicate_execution`
  continuation (pending, running, or succeeded), auto-merge journeys stay
  on the workflow terminal surface instead of looping back to
  `/crm-duplicates/review/`. Terminal merge-outcome shows execution mode,
  in-progress `"N of M merged so far"`, dry-run
  `"verified, zero CRM changes"`, and failed-run
  `"This merge run could not be completed."` without replacing count,
  failure, or retry copy. Declined groups and the declined-count tile are
  hidden from summary and merge presentation; persisted dispositions and
  digests are unchanged.
- Operator HTTPS launcher runs with `DEBUG=False`, loopback hosts, and
  collectstatic. 404/500 and decision-set dead ends render a closed page
  with no traceback or URLconf listing. Moved-past steps return HTTP 410;
  500 uses a static-free template.

Exact-first Phase **6** remains unauthorized. Live Person acquire stays
unauthorized. Step 14 Account-list live dry-run remains deferred.

## Connections, settings, and removal

- Local Settings (`/settings/`) is loopback-only and proxies registration
  / storage mutations to `/v1/settings/*`. Registration create/rotate/delete
  use signed form tokens, Django `ApiMutation`, and the API mutation
  journal. Secrets are validated before mutation create, staged
  ephemerally for dispatch only, and never durable in Django or journal
  bodies. Registration delete is HTTP **204**.
- HubSpot private-app / OAuth and Salesforce Connected App registration
  share that Settings shell. HubSpot product `CRM_QUERY` is Connect-bound
  live transport. People write for HubSpot and Salesforce is
  connection-bound; loopback `REMOTE_ADDR` only.
- Disconnected rows on Connect CRM can be **Remove**d. The POST journals
  `crm_connection_delete` (`DELETE /v1/crm/connections/{id}`, HTTP **204**).
  A still-connected row, owner mismatch, or in-flight named lease fails
  closed. Finished journeys, terminal workflow checkpoints, and unclaimed
  or terminal-claimed read grants no longer block Remove. An in-progress
  connected workflow or CRM query still 409s. A rejected Remove names the
  in-progress dependent type.
- **Abandon this journey** is a separate, explicitly-confirmed action
  using a signed revision-bound form token and Django `ApiMutation`. The
  recent list and progress page distinguish operator stop,
  connection-removal stop, quarantine, and engine failure.
- **Reclaim this connection on this browser:** first claimant-visible
  handle is the live 422 or exactly one owner-authenticated
  `GET /v1/mutations/{key}`; Django handle-bearing HTML sends
  `Cache-Control: no-store`.
- Hub **Reconnect** posts `POST /v1/crm/connections/{id}/reconnect`;
  pending shows **Continue reconnect** / **Abandon reconnect**. Isolated
  upload POST journals plan create from `dataset` / `raw_list` when ready.
  Adoption runs only after a validated retry token whose setup revision
  matches a locked session row. GET mutation status never writes the
  mapping draft.
- Settings + Connect wizard support **Start over with different
  credentials** for stuck UNKNOWN registration puts. Start over is
  refused while a dispatch lease is active. Technical details (closed
  disclosure) show failing route, HTTP status, and API `error_id`.
  Secrets never appear in technical details.

## Local launch and HTTPS

Django and API start via `scripts/start_local.ps1` /
`start_local_https.ps1` with one parent-resolved absolute Python (repo
`.venv` preferred). Service failures before readiness surface service
name, exit code, interpreter path, and sanitized diagnostic tail.

`manage.py runserver_https` accepts plain TCP and completes TLS
handshakes in the per-connection worker thread (never inside the accept
loop). Per-machine TLS under `%LOCALAPPDATA%\EasyImports\tls`; session
**Secure + SameSite=Lax**. Opt-in dual-process External Client App
authorization-code flow on **127.0.0.1:8001** is skipped in default CI.

## Implemented boundary

- Browser requests terminate at Django and remain CSRF protected.
- Every session, workflow, mutation, source, and artifact lookup is
  scoped through the anonymous Django-session owner. Archived sessions
  return 404 from every normal workflow, mutation, upload, and artifact
  route.
- FE-CM track `/health` pin remains `1.12.0` for that suite; ordinary
  importer generated contract tracks the live API. API-backed actions
  fail closed on version mismatch for the active client pin.
- Under explicit `developer-dry-run-v1`, Django rejects `DATABASE_URL`
  and requires `EASYIMPORTS_DB_PATH` plus `EASYIMPORTS_MEDIA_ROOT` to
  resolve outside the repository. It still receives no Salesforce
  credential, token, instance URL, or real org identity.
- The four public products and target mode maxima come from strictly
  validated API catalogs. Workflow creation uses generated OpenAPI
  models plus the product-specific cross-field rules.
- Single-dataset creation first validates effective source
  content/profile/kind metadata and then proves that the source can
  produce the selected Account, Contact, or Lead output. Omitted people
  kind freezes as `generic`.
- Unknown future workflow statuses and decision types remain visible and
  read-only. Known variants validate strictly.
- The three row-selection decision types use grouped, labeled comparison
  tables and enforce exactly one submitted option for every stored group
  before dispatch.
- Account and Person duplicate review shows members, evidence, conflicts,
  ranking, recommendations, and projected survivor tables.
- Effect authorization shows intent, gate phase, effect phase, target
  identity, fingerprint, work digest, confirmation, supported modes, and
  the current work summary.
- Review/continuation projections store with explicit role + source and
  do not replace the active primary; the closed product resolver maps
  only Account/Person review boxes to `easyimports.duplicate_resolution`;
  foreign keys fail closed.

## Persistence and replay

Every outbound API POST, uploads included, uses one `ApiMutation` journal
with a globally unique idempotency key. The journal freezes method,
route, resource identity, JSON or multipart metadata, editable-form
digest, response, resource, and result.

Logical action uniqueness is enforced independently of the signed
form-instance ID. Concurrent tokens for the same run, revision,
action/intent, and duplicate group converge on one mutation when their
editable values match. Changed values fail locally.

Response classification is fixed:

- accepted receipt, upload resource, or command-specific projection result → `completed`;
- `CommandReceipt(outcome="rejected")` or a contract-valid deterministic
  404/409/422 → `rejected`;
- connection loss, timeout, 5xx, malformed success, or an interrupted
  dispatch → `unknown`, or an expiring `pending` lease after process loss.

Completed and rejected forms return their frozen result even after the
workflow projection advances. An expired pending mutation may reacquire a
lease with its exact key and payload. An unknown mutation requires an
explicit exact-retry POST. GET never dispatches. Idempotency-conflicted
keys cannot automatically create a replacement; other rejections require
explicit acknowledgement first.

Leases use a random fencing token, database-derived expiry longer than
the HTTP timeout, a committed acquisition before I/O, and conditional
response persistence. A late worker cannot overwrite a newer frozen
result.

## Upload, review, and artifact integrity

- Each `(session, role)` has one active upload slot. Pending, unknown,
  and completed uploads reserve it. Rejected uploads require an explicit,
  transactional replacement authorization.
- Upload bytes are atomically written to a stable local path before the
  mutation and one-to-one `SourceFile` are committed together. Retained
  bytes are never removed for pending or unknown mutations.
- A session operator label freezes when review activity starts. Duplicate
  `decided_by` and `decided_at` are server-owned.
- Duplicate review follows source → review → decision-set receipt →
  continuation lineage. The completed decision-set handoff comes only
  from `CommandReceipt.result`.
- Artifact links appear only for `preview` or `delivered` metadata with a
  stored URL. The proxy requires the exact stored run/artifact content
  path under the configured API origin, rejects redirects, streams
  through a temporary file, verifies byte count and SHA-256, and
  preserves only an ETag that agrees with the metadata digest.

## Generated contract and legacy behavior

`api_models_generated.py` and `api_contract_generated.py` are generated
from the canonical OpenAPI document by pinned
`datamodel-code-generator/0.48.0`, with checksum matching
`web/tools/generate_api_contract.py` `EXPECTED_SHA256` /
`tests/mappings_2/fixtures/v1_openapi.sha256`. Manual edits are
prohibited. `web/tools/generate_api_contract.py --check` performs
deterministic regeneration and a clean-diff check.
API **1.62.0** adds `GET /v1/workflows/{run_id}/duplicate-execution-exceptions`
and a first-page exception projection on `WorkflowResource`.
`GET /v1/workflows/{run_id}` does not embed `group_execution_outcomes`.
Django renders at most 50 exception rows from that projection; further
pages use the generated client GET. Django does not silently truncate a
structural failures list. API **1.61.0** added duplicate-execution
continue/finish command schemas and `awaiting_execution_continuation` on
`WorkflowResource`. API **1.60.0**
added `PausedEffectDiagnostic` code `preflight_bound_exceeded` and error
type `DuplicateExecutionError` so a HubSpot merge-execute unique-id bound
pause is operator-visible on `GET /v1/workflows/{run_id}` instead of
generic `paused_effect_unavailable`.

Pre-migration sessions are `is_legacy` and admin-only. Public deletion is
local archival and never claims to remove API resources. The legacy demo
UI is explicitly deferred; sample CSV assets are retained but cannot
bypass the ordinary upload mutation journal. Pricing and optional
WooCommerce checkout remain available and have regression coverage.

## Verification

```text
python web/manage.py test importer
python web/manage.py check
```

OpenAPI regenerations must use project `.venv`.
