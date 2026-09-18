"""Owner-scoped browser views for the EasyImports v1.1 HTTP API."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import re
import tempfile

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, Http404, JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .api_client import (
    ApiConsistencyError,
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationDispatchResult,
    MutationExplicitRetryRequired,
    MutationReuseError,
    canonical_digest,
    create_or_reuse_mutation,
)
from .api_contract import (
    ApiContractError,
    validate_decision_command,
    validate_decision_set_binding,
    validate_decision_set_handoff,
    validate_effect_authorization,
    validate_effect_resume,
    validate_duplicate_execution_cycle,
    validate_start_review,
    validate_workflow_create,
)
from .api_contract_generated import (
    AUTHORIZATION_MODES,
    DECISION_TYPES,
    WORKFLOW_STATUSES,
)
from .field_merge_display import field_merge_display
from .pending_copy import attach_pending_copy
from .review_display import select_existing_review_columns
from .command_service import (
    action_generation,
    dispatch_command,
    materialize_stored_receipt,
)
from .constants import (
    MODE_LABELS,
    PRODUCT_SOURCE_ROLES,
    SESSION_STATUS_LABELS,
    TRACK_LABELS,
    display_product_label,
    display_target_label,
)
from .forms import (
    CatalogCompatibilityError,
    ProductSelectionForm,
    SourceUploadForm,
    WorkflowConfigurationForm,
)
from .models import (
    ApiArtifact,
    ApiMutation,
    ApiWorkflow,
    CrmDuplicateMergePlanLease,
    ImportSession,
    SourceFile,
)
from .setup_service import (
    SetupValidationError,
    active_source_queryset,
    column_mapping_gate_state,
    completed_upload_ids,
    completed_upload_roles,
    create_time_preflight,
    detach_active_upload,
    freeze_product_key,
    is_detached_role,
    next_upload_generation,
    preclaim_create_workflow,
    read_operator_intent,
    resolve_configure_target,
    route_for_session,
    save_setup_draft,
    session_display_label,
    session_has_confirmed_column_mapping,
    session_has_frozen_workflow,
    session_requires_column_mapping,
    source_roles_for_route,
)
from .upload_service import (
    authorize_rejected_upload_replacement,
    materialize_upload_result,
    retry_upload,
    save_and_register_upload,
)
from .vocabulary_intent import DEFAULT_VOCABULARY
from .workflow_state import (
    FormTokenError,
    archive_session,
    decode_form_token,
    freeze_operator_label,
    issue_form_token,
    owned_session_or_404,
    owned_sessions,
    owner_id_for_request,
    owner_session_for_import_session,
    refresh_workflow,
    store_artifacts,
    validate_form_token,
)

CLIENT_ERRORS = (
    ApiOperationInProgressError,
    ApiUnavailableError,
    ApiRejectedError,
    ApiConsistencyError,
    ApiContractError,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    FormTokenError,
    SetupValidationError,
)

def _resolved_campaign_member_statuses(
    *,
    connection_id: str,
    owner_session: str,
    verified_resolutions_json: str,
    default_campaign_binding: str,
    product_key: str,
) -> dict[str, list[str]]:
    """Best-effort FE-CM-1 member-status browse for resolved Campaigns (Phase 7A).

    Returns ``{campaign_id: [ordered statuses]}`` for the default Campaign and
    every verified name resolution. Network reads are ephemeral browse only (no
    freeze); any failure yields a partial/empty map so the picker stays
    unbounded rather than blocking configure.
    """

    if product_key != "easyimports.list_import" or not connection_id:
        return {}
    from .campaign_member_setup import (
        CampaignMemberSetupError,
        parse_campaign_match_resolutions,
        resolved_default_campaign_ids,
    )

    try:
        resolutions = parse_campaign_match_resolutions(
            verified_resolutions_json, connection_id=connection_id
        )
    except CampaignMemberSetupError:
        resolutions = []
    campaign_ids = resolved_default_campaign_ids(
        default_campaign_binding=default_campaign_binding,
        resolutions=resolutions,
    )
    if not campaign_ids:
        return {}
    api = EasyImportsApiClient()
    statuses: dict[str, list[str]] = {}
    for campaign_id in campaign_ids:
        try:
            payload = api.crm_campaign_member_statuses(
                connection_id, campaign_id, owner_session=owner_session
            )
        except CLIENT_ERRORS:
            continue
        rows = payload.get("statuses") if isinstance(payload, dict) else None
        if isinstance(rows, list):
            statuses[campaign_id] = [str(s) for s in rows]
    return statuses


# Desire #5 / Phase 6B: whole-run package for terminal product presentations.
RUN_OUTPUT_PACKAGE_LAYOUT = "run_output_package.v1"
RUN_OUTPUT_PACKAGE_LAYOUT_V2 = "run_output_package.v2"
RUN_OUTPUT_PACKAGE_ELIGIBLE_KEYS = frozenset(
    {
        "easyimports.list_import",
        "easyimports.account_list_import",
        "easyimports.single_dataset_import",
        "easyimports.duplicate_resolution",
    }
)
DUPLICATE_RESOLUTION_PRODUCT_KEY = "easyimports.duplicate_resolution"
_REVIEW_PREPARING_STATUSES = frozenset({"running", "ready"})
_REVIEW_DISPLAY_BLOCKED_MUTATIONS = frozenset(
    {
        "authorize_effect",
        "bind_decision_set_handoff",
        "finalize_duplicate_merge_plan_handoff",
    }
)
_POST_AUTHORIZE_CONTINUATION_STATUSES = frozenset(
    {
        "running",
        "succeeded",
        "failed",
        "cancelled",
        "paused_unknown",
        "paused_verification",
    }
)
_REVIEW_PREPARATION_COPY = (
    "EasyImports is preparing your review. This page updates automatically."
)

WORKFLOW_STATUS_PRESENTATION = {
    "ready": (
        "Import ready",
        "Your import is ready to continue.",
    ),
    "running": (
        "Import in progress",
        "EasyImports is preparing your data.",
    ),
    "needs_decision": (
        "Review required",
        "Review the highlighted items before the import can continue.",
    ),
    "awaiting_review": (
        "Duplicate review required",
        "Review the possible duplicate records before continuing.",
    ),
    "awaiting_effect_authorization": (
        "Ready for your approval",
        "Choose how EasyImports should complete the next step.",
    ),
    "paused_unknown": (
        "Import paused",
        "The last operation needs to be checked before it can safely continue.",
    ),
    "paused_verification": (
        "Verification required",
        "EasyImports needs to verify the last operation before continuing.",
    ),
    "succeeded": (
        "Import complete",
        "Your results are ready to review and download.",
    ),
    "failed": (
        "Import could not be completed",
        "Review the message below before trying again.",
    ),
}

OUTCOME_LABELS = {
    "skipped": "Not performed",
    "planned": "Preview ready",
    "dry_run": "Dry run complete",
    "succeeded": "Completed",
    "executed": "Completed",
    "completed_with_failures": "Completed with issues",
    "quarantined": "Needs review",
}

REASON_LABELS = {
    "disabled_by_request": "Skipped by this run's settings.",
    "disabled_by_authorization": "Skipped when the run was approved.",
    "supplied_references": "Used the reference data supplied with this run.",
    "no_actions": "No changes were needed.",
}

UPLOAD_STATE_PRESENTATION = {
    "": ("Not added", "created"),
    "pending": ("Uploading", "running"),
    "unknown": ("Needs confirmation", "failed"),
    "rejected": ("Needs a new file", "failed"),
    "completed": ("Ready", "completed"),
}

ARTIFACT_PRESENTATION = {
    "prepared_source.csv": (
        "Prepared data",
        "The cleaned dataset ready for its next destination.",
    ),
    "row_accountability.csv": (
        "Row processing report",
        "Shows how every uploaded row was accounted for.",
    ),
    "run_metadata_summary.csv": (
        "Run summary",
        "High-level details about this import.",
    ),
    "detection_summary.csv": (
        "Duplicate detection summary",
        "A summary of duplicate checks performed during the run.",
    ),
    "row_routing_summary.csv": (
        "Row routing summary",
        "Shows where rows were routed during preparation.",
    ),
    "exclusion_ledger.csv": (
        "Excluded rows",
        "Rows intentionally left out of the prepared output.",
    ),
    "terminal_failure_ledger.csv": (
        "Failed rows",
        "Rows that could not be completed.",
    ),
    "uploaded_list_duplicate_groups.csv": (
        "Duplicate groups",
        "Groups of possible duplicates found in the uploaded data.",
    ),
    "uploaded_list_duplicate_rows.csv": (
        "Duplicate rows",
        "Uploaded rows included in possible duplicate groups.",
    ),
    "uploaded_list_duplicates.csv": (
        "Duplicate decisions",
        "The final duplicate handling outcomes.",
    ),
    "uploaded_list_duplicate_evidence.csv": (
        "Duplicate evidence",
        "Supporting evidence for duplicate detection.",
    ),
    "deferred_duplicate_groups.csv": (
        "Remaining duplicate groups",
        "Frozen groups you deferred with Finish for now. Upload this CSV to resume later.",
    ),
}

PRIMARY_ARTIFACT_FILENAMES = {"prepared_source.csv"}

ACTIONABLE_ARTIFACT_FILENAMES = {
    "exclusion_ledger.csv",
    "terminal_failure_ledger.csv",
    "uploaded_list_duplicate_groups.csv",
    "deferred_duplicate_groups.csv",
}

CRM_MUTATING_TRACKS = {
    "account_provisioning",
    "people_writes",
    "person_duplicate_resolution",
    "duplicate_execution",
    "campaign_member_writes",
    "dataset_writes",
}

GROUPED_SELECTION_DECISION_TYPES = {
    "list_duplicates",
    "multiple_crm_matches",
    "multiple_crm_account_matches",
    "uploaded_account_id_disagreement",
    "uploaded_person_id_disagreement",
}

GROUPED_SELECTION_HIDDEN_COLUMNS = {
    "option_kind",
    "option_label",
    "selected",
    "was_selected_match",
    "is_recommended",
    "recommended",
}

KNOWN_GROUPED_OPTION_KINDS = {
    "uploaded_row",
    "exclude_group",
    "candidate",
    "quarantine",
    "force_lead_without_account",
    "continue_net_new_account",
    "continue_unmatched",
    "keep_uploaded_id",
    "use_matched_id",
    "quarantine_write",
}

GROUPED_WHOLE_GROUP_OPTION_KINDS = {
    "exclude_group",
}

GROUPED_SELECTION_FLASH_KEY = "easyimports_grouped_selection_after_error"

GROUPED_SELECTION_TITLES = {
    "list_duplicates": "Duplicate group",
    "multiple_crm_matches": "Person match",
    "multiple_crm_account_matches": "Account match",
    "uploaded_account_id_disagreement": "Uploaded Account Id",
    "uploaded_person_id_disagreement": "Uploaded Contact Id",
}

GROUPED_SELECTION_COLUMN_PRIORITY = {
    "list_duplicates": (
        "contact_full_name_final",
        "contact_email_final",
        "account_name_final",
        "acct_name_final",  # dual-window fallback
        "account_domain_final",
        "acct_domain_final",
        "contact_title",
        "account_owner",
        "acct_owner",
        "account_billing_city",
        "acct_billing_city",
        "account_billing_state",
        "acct_billing_state",
        "list_duplicate_rules",
        "list_duplicate_evidence_rules",
        "name",
        "match_score",
    ),
    "multiple_crm_matches": (
        "candidate_person_full_name",
        "name",
        "candidate_person_email",
        "candidate_person_type",
        "candidate_person_title",
        "candidate_account_name",
        "candidate_acct_name",
        "candidate_source",
        "match_score",
    ),
    "multiple_crm_account_matches": (
        "candidate_account_name",
        "candidate_acct_name",
        "name",
        "candidate_account_domain",
        "candidate_acct_domain",
        "candidate_account_city",
        "candidate_acct_city",
        "candidate_account_state",
        "candidate_acct_state",
        "candidate_account_owner",
        "candidate_acct_owner",
        "candidate_sources",
        "match_score",
    ),
    "uploaded_account_id_disagreement": (
        "uploaded_id",
        "found_id",
        "found_name",
        "account_name",
        "option_label",
    ),
    "uploaded_person_id_disagreement": (
        "uploaded_id",
        "found_id",
        "found_name",
        "contact_first_name",
        "contact_last_name",
        "contact_email",
        "account_name",
        "option_label",
    ),
}

GROUPED_SELECTION_CONTEXT_FIELDS = {
    "multiple_crm_matches": (
        ("Uploaded person", ("incoming_contact_full_name",)),
        ("Email", ("incoming_contact_email",)),
        ("Account", ("incoming_account_name", "incoming_acct_name")),
    ),
    "multiple_crm_account_matches": (
        ("Uploaded person", ("incoming_contact_full_name",)),
        ("Uploaded account", ("account_name_final", "acct_name_final")),
        ("Account domain", ("account_domain_final", "acct_domain_final")),
        ("Email domain", ("contact_domain_final",)),
    ),
    "uploaded_account_id_disagreement": (
        ("Id from the file", ("uploaded_id",)),
        ("Id matching found", ("found_id",)),
        ("Found name", ("found_name",)),
    ),
    "uploaded_person_id_disagreement": (
        ("Id from the file", ("uploaded_id",)),
        ("Id matching found", ("found_id",)),
        ("Found name", ("found_name",)),
    ),
}

# OUT-7 retired leftover LIST_CANON back-fill after a 7-col cap.
# Display is exactly review-core ∪ typed extras that exist on the frame.
GROUPED_SELECTION_MAX_COLUMNS = 7

DUPLICATE_GROUP_REVIEW_DECISION_TYPES = {
    "account_duplicate_group_review",
    "person_duplicate_group_review",
}

DUPLICATE_REVIEW_SKIP_VALUE = "skip"
DUPLICATE_REVIEW_QUARANTINE_VALUE = "quarantine"
DUPLICATE_REVIEW_SURVIVOR_PREFIX = "survivor:"


def _humanize_identifier(value: object) -> str:
    return str(value or "").replace("_", " ").strip().capitalize()


def _has_display_value(value: object) -> bool:
    if value is None or value == "":
        return False
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _display_value(value: object, *, evidence: bool = False) -> object:
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (list, tuple, set)):
        values = [
            _humanize_identifier(item) if evidence else str(item)
            for item in value
            if _has_display_value(item)
        ]
        return ", ".join(values)
    if evidence and isinstance(value, str):
        return ", ".join(
            _humanize_identifier(item)
            for item in value.split(",")
            if item.strip()
        )
    return value


def _is_technical_identifier(column: str) -> bool:
    normalized = str(column or "").lower()
    return (
        normalized == "id"
        or normalized.endswith("_id")
        or "canon_row_id" in normalized
        or normalized.startswith("job_")
        or normalized.startswith("decision_")
    )


def _workflow_status_presentation(status: str) -> dict[str, str]:
    heading, description = WORKFLOW_STATUS_PRESENTATION.get(
        status,
        ("Import status", "This run has a status this web app does not recognize."),
    )
    return {"heading": heading, "description": description}


def _child_review_workflow(
    session: ImportSession,
    current: ApiWorkflow,
    latest_accepted: ApiMutation | None,
) -> ApiWorkflow | None:
    """Identify the child review run without changing the active primary."""

    if current.role == ApiWorkflow.Role.REVIEW:
        return current
    if (
        latest_accepted is not None
        and latest_accepted.mutation_kind == "start_review_workflow"
        and isinstance(latest_accepted.response_json, dict)
    ):
        run_id = str(latest_accepted.response_json.get("run_id") or "").strip()
        if run_id:
            found = session.api_workflows.filter(run_id=run_id).first()
            if found is not None:
                return found
    return (
        session.api_workflows.filter(role=ApiWorkflow.Role.REVIEW)
        .exclude(pk=current.pk)
        .order_by("-updated_at")
        .first()
    )


def _review_is_preparing(workflow: ApiWorkflow | None) -> bool:
    return (
        workflow is not None
        and workflow.role == ApiWorkflow.Role.REVIEW
        and workflow.status in _REVIEW_PREPARING_STATUSES
    )


def _awaiting_first_review_window(workflow: ApiWorkflow) -> bool:
    """True while the source may still publish or replace the first handoff.

    GFC-6 already auto-reloads after a child review exists. This covers the
    earlier wait: no child yet, and ``review_window_ready`` may flip or the
    incremental ``handoff_id`` may change. Does not hide the start-review
    control. Stops once ``review_ready`` is true (complete-population
    handoff is then stable).
    """

    if workflow.role in {ApiWorkflow.Role.REVIEW, ApiWorkflow.Role.CONTINUATION}:
        return False
    projection = workflow.projection if isinstance(workflow.projection, dict) else {}
    key = str(workflow.workflow_key or projection.get("workflow_key") or "")
    if key != DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return False
    progress = projection.get("duplicate_analysis_progress")
    if not isinstance(progress, dict):
        return False
    if workflow.status in {"failed", "cancelled"}:
        return False
    if progress.get("review_ready") is True:
        return False
    return True


def _should_display_review_child(
    current: ApiWorkflow,
    latest_accepted: ApiMutation | None,
) -> bool:
    """Review display must not supersede a newer continuation/effect workflow."""

    if current.role == ApiWorkflow.Role.CONTINUATION:
        return False
    if (
        latest_accepted is not None
        and latest_accepted.mutation_kind in _REVIEW_DISPLAY_BLOCKED_MUTATIONS
    ):
        return False
    return True


def _auto_merge_configured(session: ImportSession) -> bool:
    """True when the session froze an auto-merge threshold.

    Used only to choose the review surface. Eligibility itself stays on the
    window API. A sequential child card does not apply that filter.
    """

    options = session.options if isinstance(session.options, dict) else {}
    raw = options.get("auto_merge_min_confidence")
    return raw is not None and raw != ""


def _latest_accepted_non_upload(session: ImportSession) -> ApiMutation | None:
    return (
        session.api_mutations.filter(state=ApiMutation.State.COMPLETED)
        .exclude(mutation_kind="register_upload")
        .order_by("-created_at")
        .first()
    )


def _workflow_for_displayed_decision(
    session: ImportSession,
    workflow: ApiWorkflow,
) -> ApiWorkflow:
    """Use the review child GET displayed, without changing active.

    GFC-6 shows the child's sequential card while ``session.active_workflow``
    stays the source. Confirm winner posts to ``submit_decision``; that POST
    must validate and dispatch against the child the token was issued for.
    Auto-merge sessions do not render that card (ARW-3B).
    """

    latest_accepted = _latest_accepted_non_upload(session)
    if not _should_display_review_child(workflow, latest_accepted):
        return workflow
    if _auto_merge_configured(session):
        return workflow
    child = _child_review_workflow(session, workflow, latest_accepted)
    if child is None:
        return workflow
    return child


def _journey_review_redirect(session: ImportSession):
    """Send a CRM-dupe journey to the filtered window page, or None."""

    options = session.options if isinstance(session.options, dict) else {}
    if options.get("crm_journey_id") or options.get("redesign_phase") == "4a":
        return redirect(
            "importer:crm_duplicate_journey_review",
            session_id=session.id,
        )
    return None


def _duplicate_execution_authorize_for_lease(
    mutation: ApiMutation, leased_run_id: str
) -> bool:
    """True when this authorize_effect is the leased merge continuation."""

    if not leased_run_id or mutation.mutation_kind != "authorize_effect":
        return False
    body = mutation.request_json if isinstance(mutation.request_json, dict) else {}
    if str(body.get("track") or "").strip() != "duplicate_execution":
        return False
    resource = str(mutation.resource_identity or "").strip()
    if resource == leased_run_id:
        return True
    route = str(mutation.route or "")
    return f"/workflows/{leased_run_id}/" in route


def _merge_continuation_post_authorize(session: ImportSession) -> bool:
    """True once the leased merge continuation is authorized or terminal.

    Earlier session authorizations (for example reference_acquisition) must
    not suppress the pre-authorize auto-merge review redirect.
    """

    continuation = _duplicate_continuation_workflow(session)
    leased_run_id = str(getattr(continuation, "run_id", "") or "").strip()
    if leased_run_id and any(
        _duplicate_execution_authorize_for_lease(mutation, leased_run_id)
        for mutation in session.api_mutations.filter(
            mutation_kind="authorize_effect"
        )
    ):
        return True
    if continuation is None:
        return False
    return continuation.status in _POST_AUTHORIZE_CONTINUATION_STATUSES


def _review_run_id_from_materialized_child(
    session: ImportSession, *, source_run_id: str
) -> str:
    """Return a REVIEW child's run_id already stored for this source, or ''."""

    wanted = str(source_run_id or "").strip()
    rows = session.api_workflows.filter(role=ApiWorkflow.Role.REVIEW).order_by(
        "-updated_at"
    )
    for row in rows:
        run_id = str(row.run_id or "").strip()
        if not run_id:
            continue
        source = row.source_workflow
        if source is None:
            continue
        if wanted and str(source.run_id or "").strip() != wanted:
            continue
        return run_id
    return ""


def _review_run_id_from_start_review_mutation(
    session: ImportSession, *, source_run_id: str
) -> str:
    """Return run_id from a completed start_review_workflow receipt, or ''."""

    wanted = str(source_run_id or "").strip()
    rows = session.api_mutations.filter(
        mutation_kind="start_review_workflow",
        state=ApiMutation.State.COMPLETED,
    ).order_by("-created_at")
    for mutation in rows:
        if wanted and str(mutation.resource_identity or "").strip() not in {
            "",
            wanted,
        }:
            continue
        response = (
            mutation.response_json
            if isinstance(mutation.response_json, dict)
            else {}
        )
        run_id = str(response.get("run_id") or "").strip()
        if run_id:
            return run_id
    return ""


def _bind_review_run_from_existing_child(
    session: ImportSession, *, handoff_id: str = ""
) -> None:
    """Freeze options.review_run_id from the child start_review just created.

    The journey page reads only that option. Binding here is a POST write so
    the following GET does not dispatch a second start_review_workflow.
    """

    options = dict(session.options or {})
    existing = str(options.get("review_run_id") or "").strip()
    source_run_id = str(
        options.get("run_id") or options.get("source_run_id") or ""
    ).strip()
    review_run_id = existing or _review_run_id_from_materialized_child(
        session, source_run_id=source_run_id
    )
    if not review_run_id:
        review_run_id = _review_run_id_from_start_review_mutation(
            session, source_run_id=source_run_id
        )
    if not review_run_id:
        return
    changed = False
    if existing != review_run_id:
        options["review_run_id"] = review_run_id
        changed = True
    token = str(handoff_id or "").strip()
    if token and str(options.get("review_handoff_id") or "").strip() != token:
        options["review_handoff_id"] = token
        changed = True
    if source_run_id and str(options.get("source_run_id") or "").strip() != source_run_id:
        options["source_run_id"] = source_run_id
        changed = True
    if not changed:
        return
    session.options = options
    session.save(update_fields=["options", "updated_at"])


def _effect_mode_label(track: str, mode: str) -> str:
    """Operator-facing labels; merge track names the CRM write explicitly."""

    if track == "duplicate_execution":
        return {
            "disabled": "Do not run merges",
            "preview": "Preview merge plan only (no CRM changes)",
            "plan_only": "Preview merge plan only (no CRM changes)",
            "dry_run": "Test merges without changing CRM",
            "execute": "Apply approved merges in CRM",
        }.get(mode, MODE_LABELS.get(mode, _humanize_identifier(mode)))
    if track == "reference_acquisition":
        return {
            "disabled": "Do not load reference data",
            "execute": "Load CRM reference data",
        }.get(mode, MODE_LABELS.get(mode, _humanize_identifier(mode)))
    # Phase 4D: Account provision authorize surface — decline → Lead fallback.
    if track == "account_provisioning":
        return {
            "disabled": "Skip Account creation (route as Leads)",
            "preview": "Preview Account creation plan",
            "plan_only": "Preview Account creation plan",
            "dry_run": "Test Account creation without CRM changes",
            "execute": "Apply changes",
        }.get(mode, MODE_LABELS.get(mode, _humanize_identifier(mode)))
    return MODE_LABELS.get(mode, _humanize_identifier(mode))


def _effect_presentation(intent: dict, summary: dict | None) -> dict:
    track = intent.get("track", "")
    track_label = TRACK_LABELS.get(track, _humanize_identifier(track))
    if track == "delivery":
        heading = "Generate output files"
        description = (
            "Create downloadable preview files for this run. "
            "This step does not change CRM records."
        )
        submit_label = "Generate files"
    elif track == "reference_acquisition":
        heading = "Load CRM records for duplicate analysis"
        description = (
            "Read the connected CRM records needed to find and rank "
            "duplicate groups. This step does not merge or change records."
        )
        submit_label = "Load records"
    elif track == "duplicate_execution":
        heading = "Authorize CRM merges"
        description = (
            "Your confirmed winners are ready. Choose a dry run to verify "
            "the merge plan with zero CRM changes, or apply merges only when "
            "that option is explicitly available. Recommendation was never "
            "approval — only the survivors you confirmed will be merged."
        )
        submit_label = "Authorize merge step"
    elif track == "account_provisioning":
        # Phase 4D: optional net-new Account provision review (decline → Lead).
        heading = "Optional: create net-new Accounts"
        description = (
            "Review the companies listed below (one row per company). You may "
            "skip Account creation entirely (those people become Leads), "
            "preview the plan, test without CRM changes, or apply creates when "
            "authorized. Account creation is optional."
        )
        submit_label = "Continue"
    else:
        heading = f"Choose how to handle {track_label.lower()}"
        description = (
            "Select a preview or dry run to review the result without CRM changes, "
            "or apply changes only when that option is explicitly available."
        )
        submit_label = "Continue"
    return {
        "heading": heading,
        "description": description,
        "track_label": track_label,
        "submit_label": submit_label,
        "mode_options": [
            {"value": mode, "label": _effect_mode_label(track, mode)}
            for mode in intent.get("supported_modes", ())
            if mode in AUTHORIZATION_MODES
        ],
        "summary_items": [
            {
                "label": _humanize_identifier(key),
                "value": value,
            }
            for key, value in (summary or {}).items()
        ],
    }


def _duplicate_review_progress_presentation(
    progress: dict | None, decision_body: dict | None
) -> dict | None:
    """Human progress for sequential group review (every group, one at a time)."""

    if not progress:
        return None
    decided = int(progress.get("decided_group_count") or 0)
    remaining = int(progress.get("remaining_group_count") or 0)
    total = decided + remaining
    # Remaining includes the open group when a decision is present.
    current_index = decided + 1 if remaining > 0 else decided
    group_id = ""
    if decision_body:
        group_id = str(decision_body.get("duplicate_group_id") or "").strip()
    recommended = ""
    selected = ""
    if decision_body:
        recommended = str(decision_body.get("recommended_survivor_id") or "").strip()
        selected = str(
            decision_body.get("selected_survivor_id") or recommended
        ).strip()
    return {
        "decided_group_count": decided,
        "remaining_group_count": remaining,
        "total_group_count": total,
        "current_index": current_index,
        "group_id": group_id,
        "recommended_survivor_id": recommended,
        "selected_survivor_id": selected,
        "headline": (
            f"Group {current_index} of {total}"
            if total > 0 and remaining > 0
            else f"{decided} group{'s' if decided != 1 else ''} reviewed"
        ),
    }


def _terminal_presentation(terminal: dict | None) -> dict:
    if not terminal:
        return {"cards": [], "receipts": [], "no_crm_changes": False}

    accountability = terminal.get("accountability") or {}
    cards = []
    original_ids = accountability.get("original_row_ids")
    output_ids = accountability.get("output_row_ids")
    failed_ids = accountability.get("failed_row_ids")
    dispositions = accountability.get("dispositions")
    if isinstance(original_ids, list):
        cards.append({"label": "Rows processed", "value": len(original_ids)})
    if isinstance(output_ids, list):
        cards.append({"label": "Rows ready", "value": len(output_ids)})
    if "excluded_row_count" in accountability:
        cards.append(
            {
                "label": "Rows excluded",
                "value": accountability.get("excluded_row_count", 0),
            }
        )
    if isinstance(failed_ids, list):
        cards.append({"label": "Rows failed", "value": len(failed_ids)})
    if not cards and isinstance(dispositions, list):
        cards.append({"label": "Groups processed", "value": len(dispositions)})

    receipts = []
    raw_receipts = terminal.get("receipts") or []
    for receipt in raw_receipts:
        outcome = receipt.get("outcome", "")
        reason = receipt.get("reason", "")
        receipts.append(
            {
                "track": TRACK_LABELS.get(
                    receipt.get("track", ""),
                    _humanize_identifier(receipt.get("track", "")),
                ),
                "outcome": OUTCOME_LABELS.get(outcome, _humanize_identifier(outcome)),
                "description": REASON_LABELS.get(reason, ""),
            }
        )

    crm_change_was_authorized = any(
        receipt.get("track") in CRM_MUTATING_TRACKS
        and receipt.get("authorization") == "execute"
        for receipt in raw_receipts
    )
    return {
        "cards": cards,
        "receipts": receipts,
        "no_crm_changes": not crm_change_was_authorized,
        "reconciles": accountability.get("reconciles"),
    }


_PYTHON_EXCEPTION_FORM = re.compile(
    r"^(?:[A-Za-z_][A-Za-z0-9_]*\.)*"
    r"(?:[A-Za-z_][A-Za-z0-9_]*)?(?:Error|Exception|Warning):"
)
_UNSAFE_MERGE_REASON_MARKERS = (
    "traceback (most recent call last)",
    'file "',
    ".py\", line",
    "bearer ",
    "authorization:",
    "api_key",
    "secret=",
    "object has no attribute",
)
_REDACTED_OPERATOR_VISIBLE = "Unavailable for display."
# Fail-closed display: only these fields may appear in operator-visible dumps.
_OPERATOR_SAFE_EVIDENCE_KEYS = frozenset(
    {
        "accountability",
        "actioncount",
        "actionids",
        "applicableredirectdigest",
        "artifacts",
        "authorization",
        "availability",
        "body",
        "capability",
        "code",
        "columns",
        "completedcount",
        "completioncontract",
        "completiondigest",
        "componentid",
        "confirmation",
        "contentdigest",
        "countunit",
        "decidedgroupcount",
        "decision",
        "decisionid",
        "decisiontype",
        "deferredgroupcount",
        "deliverymanifest",
        "dispositions",
        "downloadurl",
        "duplicateanalysisprogress",
        "duplicategroupid",
        "effectgrants",
        "effectintent",
        "effectphaseid",
        "effectreview",
        "entity",
        "error",
        "errorcode",
        "errormessage",
        "evidence",
        "excludedrowcount",
        "failed",
        "failedlosercount",
        "failedrowids",
        "filename",
        "finishfornowavailable",
        "finishedfornow",
        "gatephaseid",
        "groupcount",
        "groupexecutioncount",
        "groupexecutionoutcomes",
        "groupid",
        "groupmembersdf",
        "handoffid",
        "intentid",
        "links",
        "loserid",
        "maximummode",
        "memberids",
        "merged",
        "mergedlosercount",
        "message",
        "originalrowids",
        "outcome",
        "outputrowids",
        "oversizedquarantinecomponents",
        "pausedeffectdiagnostic",
        "phaseid",
        "plandigest",
        "reason",
        "receipts",
        "reconciles",
        "recordcount",
        "recordedredirectdigest",
        "referenceacquisitionprogress",
        "remaininggroupcount",
        "remaininggroupsexported",
        "remoteoutcome",
        "reviewgroupsready",
        "reviewgroupstotal",
        "reviewhandoff",
        "reviewprogress",
        "reviewready",
        "reviewwindowready",
        "revision",
        "rowcount",
        "rows",
        "runid",
        "selectedsurvivorid",
        "self",
        "stage",
        "status",
        "statuscounts",
        "summary",
        "supportedmodes",
        "survivorid",
        "targetfingerprint",
        "targetproviderid",
        "terminalevidence",
        "title",
        "totalcount",
        "track",
        "verifiedactionids",
        "workdigest",
        "workflowkey",
        "workflowversion",
    }
)
_OPERATOR_SAFE_ENUM_VALUES = {
    "authorization": frozenset(AUTHORIZATION_MODES)
    | frozenset({"planned", "skipped", "executed"}),
    "outcome": frozenset(OUTCOME_LABELS),
    "track": frozenset(TRACK_LABELS),
    "status": frozenset(WORKFLOW_STATUSES)
    | frozenset(
        {
            "verified",
            "incomplete",
            "cancelled",
            "complete",
            "unavailable",
            "queued",
            "planned",
        }
    ),
    "maximummode": frozenset(AUTHORIZATION_MODES),
}


def _normalized_evidence_key(key: object) -> str:
    return "".join(ch for ch in str(key).lower() if ch.isalnum())


def _is_operator_safe_evidence_key(key: object) -> bool:
    """True only for allowlisted operator-visible field names."""

    return _normalized_evidence_key(key) in _OPERATOR_SAFE_EVIDENCE_KEYS


def _operator_visible_string_is_unsafe(text: str) -> bool:
    if not text:
        return False
    lowered = text.lower()
    if _PYTHON_EXCEPTION_FORM.match(text):
        return True
    return any(marker in lowered for marker in _UNSAFE_MERGE_REASON_MARKERS)


def _operator_safe_merge_reason(reason: object) -> str:
    """Operator-visible merge failure copy; never raw exceptions or secrets."""

    text = " ".join(str(reason or "").split())
    if not text or _operator_visible_string_is_unsafe(text):
        return "This merge could not be completed."
    if len(text) > 240:
        return text[:237] + "..."
    return text


def _redact_operator_visible_string(text: str) -> str:
    collapsed = " ".join(text.split())
    if _operator_visible_string_is_unsafe(collapsed):
        return _REDACTED_OPERATOR_VISIBLE
    return text


def _sanitize_operator_visible_mapping(
    value: object, *, key: object | None = None
) -> object:
    """Allowlisted copy for page output.

    Unknown fields are omitted. Remaining strings still fail closed on
    exception/secret forms and on enum keys with unknown values.
    """

    if isinstance(value, dict):
        out: dict = {}
        for child_key, item in value.items():
            if not _is_operator_safe_evidence_key(child_key):
                continue
            out[child_key] = _sanitize_operator_visible_mapping(
                item, key=child_key
            )
        return out
    if isinstance(value, (list, tuple)):
        return [
            _sanitize_operator_visible_mapping(item, key=key) for item in value
        ]
    if isinstance(value, str):
        normalized = _normalized_evidence_key(key) if key is not None else ""
        allowed = _OPERATOR_SAFE_ENUM_VALUES.get(normalized)
        if allowed is not None and value not in allowed:
            return _REDACTED_OPERATOR_VISIBLE
        return _redact_operator_visible_string(value)
    return value


def _group_execution_outcome_rows(projection: dict) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()

    def _accept(row: object) -> None:
        if not isinstance(row, dict):
            return
        key = (
            str(row.get("duplicate_group_id") or ""),
            str(row.get("survivor_id") or ""),
            tuple(str(item) for item in (row.get("merged") or ())),
        )
        if key in seen:
            return
        seen.add(key)
        rows.append(row)

    top = projection.get("group_execution_outcomes")
    if isinstance(top, (list, tuple)):
        for row in top:
            _accept(row)
    terminal = projection.get("terminal_evidence")
    if isinstance(terminal, dict):
        for receipt in terminal.get("receipts") or ():
            if not isinstance(receipt, dict):
                continue
            if receipt.get("track") != "duplicate_execution":
                continue
            evidence = receipt.get("evidence")
            if not isinstance(evidence, dict):
                continue
            for row in evidence.get("group_execution_outcomes") or ():
                _accept(row)
    return rows


def _duplicate_execution_receipts(projection: dict) -> list[dict]:
    terminal = projection.get("terminal_evidence")
    if not isinstance(terminal, dict):
        return []
    rows: list[dict] = []
    for receipt in terminal.get("receipts") or ():
        if isinstance(receipt, dict) and receipt.get("track") == "duplicate_execution":
            rows.append(receipt)
    return rows


def _duplicate_execution_authorization_mode(projection: dict) -> str:
    """dry_run or execute from durable duplicate_execution evidence."""

    for receipt in _duplicate_execution_receipts(projection):
        mode = str(receipt.get("authorization") or "").strip()
        if mode in {"dry_run", "execute"}:
            return mode
    grants = projection.get("effect_grants") or ()
    for grant in grants:
        if not isinstance(grant, dict):
            continue
        if grant.get("track") != "duplicate_execution":
            continue
        mode = str(grant.get("selected_mode") or "").strip()
        if mode in {"dry_run", "execute"}:
            return mode
    return ""


def _duplicate_execution_planned_count(projection: dict, merged: int, failed: int) -> int:
    """Planned merge-action total for in-progress 'N of M' copy."""

    for receipt in _duplicate_execution_receipts(projection):
        evidence = receipt.get("evidence")
        if not isinstance(evidence, dict):
            continue
        planned = int(evidence.get("action_count") or 0)
        if planned > 0:
            return planned
    summary = projection.get("summary")
    if isinstance(summary, dict):
        planned = int(summary.get("action_count") or 0)
        if planned > 0:
            return planned
    return merged + failed


def _merge_execution_presentation(
    projection: dict | None,
    *,
    session: ImportSession,
) -> dict | None:
    """Counts and per-group failures from durable merge-execution evidence."""

    if not isinstance(projection, dict):
        return None
    key = str(
        projection.get("workflow_key")
        or session.product_key
        or ""
    )
    if key != DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return None
    exception_page = projection.get("duplicate_execution_exception_page")
    has_exception_page = isinstance(exception_page, dict)
    # Exceptional groups belong on the bounded exception page. Walk structural
    # outcomes only for legacy projections that lack that page, and never
    # silently truncate past 50.
    outcomes: list[dict] = []
    if not has_exception_page:
        outcomes = _group_execution_outcome_rows(projection)
        if len(outcomes) > 50:
            outcomes = []
    summary = projection.get("summary")
    summary = summary if isinstance(summary, dict) else {}
    merged = int(summary.get("merged_loser_count") or 0)
    failed = int(summary.get("failed_loser_count") or 0)
    if merged == 0 and failed == 0:
        for row in outcomes:
            merged += len(row.get("merged") or ())
            failed += len(row.get("failed") or ())
    mode = _duplicate_execution_authorization_mode(projection)
    status = str(projection.get("status") or "")
    in_progress = status == "running"
    continuation = status == "awaiting_execution_continuation"
    terminal = status in {"succeeded", "failed", "cancelled"}
    if merged == 0 and failed == 0 and not outcomes and not has_exception_page:
        if mode not in {"dry_run", "execute"} or not (
            in_progress or terminal or continuation
        ):
            return None
    failure_rows: list[dict[str, str]] = []
    overflow = False
    for row in outcomes:
        group_id = str(row.get("duplicate_group_id") or "").strip()
        survivor_id = str(row.get("survivor_id") or "").strip()
        raw_failed = row.get("failed") or ()
        for item in raw_failed:
            if isinstance(item, dict):
                loser_id = str(item.get("loser_id") or "").strip()
                reason = _operator_safe_merge_reason(item.get("reason"))
            else:
                loser_id = str(item).strip()
                reason = "This merge could not be completed."
            if not loser_id:
                continue
            failure_rows.append(
                {
                    "group_id": group_id,
                    "survivor_id": survivor_id,
                    "loser_id": loser_id,
                    "reason": reason,
                }
            )
            if len(failure_rows) > 50:
                overflow = True
                break
        if overflow:
            break
    if overflow:
        failure_rows = []
    retry_url = ""
    if failed > 0 or failure_rows:
        retry_url = reverse(
            "importer:crm_duplicate_journey_merge",
            kwargs={"session_id": session.id},
        )
    exception_rows: list[dict[str, str]] = []
    exception_aggregates: dict[str, int] = {}
    more_url = ""
    if isinstance(exception_page, dict):
        raw_exceptions = exception_page.get("exceptions") or ()
        for item in raw_exceptions:
            if not isinstance(item, dict):
                continue
            exception_rows.append(
                {
                    "group_id": str(item.get("group_id") or "").strip(),
                    "reason_code": str(item.get("reason_code") or "").strip(),
                    "sanitized_display": str(
                        item.get("sanitized_display") or "This group needs attention."
                    ),
                    "populate_status": str(item.get("populate_status") or "").strip(),
                    "group_execution_status": str(
                        item.get("group_execution_status") or ""
                    ).strip(),
                }
            )
            if len(exception_rows) >= 50:
                break
        raw_aggregates = exception_page.get("aggregates")
        if isinstance(raw_aggregates, dict):
            exception_aggregates = {
                key: int(raw_aggregates.get(key) or 0)
                for key in (
                    "merged_count",
                    "needs_attention_count",
                    "deferred_count",
                    "failed_count",
                    "verified_unmerged_count",
                )
            }
        next_cursor = str(exception_page.get("next_cursor") or "").strip()
        if next_cursor:
            more_url = (
                reverse(
                    "importer:duplicate_execution_exceptions",
                    kwargs={"session_id": session.id},
                )
                + "?cursor="
                + next_cursor
            )
    planned = _duplicate_execution_planned_count(projection, merged, failed)
    if planned < merged:
        planned = merged
    progress_copy = ""
    if mode == "execute" and in_progress and planned > 0:
        progress_copy = f"{merged} of {planned} merged so far"
    dry_run_copy = ""
    if mode == "dry_run" and status == "succeeded":
        dry_run_copy = "verified, zero CRM changes"
    run_failure_copy = ""
    if status == "failed":
        run_failure_copy = "This merge run could not be completed."
    return {
        "headline": f"{merged} merged, {failed} failed",
        "merged_count": merged,
        "failed_count": failed,
        "failures": failure_rows,
        "exceptions": exception_rows,
        "exception_aggregates": exception_aggregates,
        "more_url": more_url,
        "retry_url": retry_url,
        "retryable": bool(retry_url),
        "continuation_available": continuation or bool(
            projection.get("continuation_available")
        ),
        "continuation_generation": int(
            projection.get("continuation_generation") or 0
        ),
        "expected_revision": int(projection.get("revision") or 0),
        "mode": mode,
        "mode_label": (
            _effect_mode_label("duplicate_execution", mode) if mode else ""
        ),
        "progress_copy": progress_copy,
        "dry_run_copy": dry_run_copy,
        "run_failure_copy": run_failure_copy,
    }


def _error(request, exc: Exception) -> None:
    if isinstance(exc, ApiOperationInProgressError):
        messages.info(request, str(exc))
    else:
        messages.error(request, str(exc))


def _posted_payload_digest(request) -> str:
    """Digest only browser-editable POST values, never the signed token or CSRF."""
    values = {
        key: list(items)
        for key, items in sorted(request.POST.lists())
        if key not in {"csrfmiddlewaretoken", "form_token"}
    }
    return canonical_digest(values)


def _workflow_action_identity(
    workflow: ApiWorkflow,
    action_kind: str,
    action_id: str = "",
    group_id: str = "",
) -> str:
    return (
        f"run:{workflow.run_id}:revision:{workflow.revision}:"
        f"{action_kind}:{action_id}:group:{group_id}"
    )


def _upload_action_identity(session: ImportSession, role: str) -> str:
    return f"upload:{session.id}:{role}"


def _creation_action_identity(session: ImportSession) -> str:
    return f"session:{session.id}:create_workflow"


def _recorded_mutation(
    *,
    session: ImportSession,
    claims: dict,
    mutation_kind: str,
    form_payload_digest: str,
) -> ApiMutation | None:
    identity = claims.get("logical_action_identity", "")
    generation = claims.get("logical_action_generation", 0)
    mutation = (
        session.api_mutations.filter(
            Q(form_instance=claims["form_instance"])
            | Q(
                logical_action_identity=identity,
                logical_action_generation=generation,
            )
        )
        .order_by("created_at", "id")
        .first()
    )
    if mutation is None:
        return None
    if (
        mutation.mutation_kind != mutation_kind
        or mutation.logical_action_identity != identity
        or mutation.logical_action_generation != generation
        or mutation.form_payload_digest != form_payload_digest
    ):
        raise MutationReuseError(
            "This logical action was already submitted with different values."
        )
    return mutation


def _decode_submission(request, session: ImportSession, action_kind: str):
    claims = decode_form_token(
        request.POST.get("form_token", ""),
        owner_id=owner_id_for_request(request),
        session=session,
        action_kind=action_kind,
    )
    return claims, _posted_payload_digest(request)


def _describe_rejection(mutation: ApiMutation) -> str:
    message = mutation.error_message or "EasyImports could not complete that action."
    return f"We couldn't complete that action: {message}"


def _command_receipt_presentation(mutation: ApiMutation) -> dict:
    response = (
        mutation.response_json if isinstance(mutation.response_json, dict) else {}
    )
    nested_error = (
        response.get("error") if isinstance(response.get("error"), dict) else {}
    )
    return {
        "mutation_kind": mutation.mutation_kind,
        "state": mutation.state,
        "created_at": mutation.created_at,
        "idempotency_key": mutation.idempotency_key,
        "outcome": response.get("outcome") or "",
        "error_code": (
            mutation.error_code
            or response.get("error_code")
            or nested_error.get("code")
            or ""
        ),
        "message": (
            mutation.error_message
            or response.get("message")
            or nested_error.get("message")
            or ""
        ),
        "resource": response.get("resource") or "",
        "result": response.get("result"),
    }


def _record_command_outcome(request, session: ImportSession, result):
    """Expose the frozen receipt before any optional resource refresh."""
    if result.mutation.state == ApiMutation.State.REJECTED:
        messages.error(request, _describe_rejection(result.mutation))
        return None
    workflow = materialize_stored_receipt(session, result.mutation)
    messages.success(request, "The action completed.")
    return workflow


def _replay_if_recorded(
    request,
    *,
    session: ImportSession,
    claims: dict,
    mutation_kind: str,
    form_payload_digest: str,
):
    mutation = _recorded_mutation(
        session=session,
        claims=claims,
        mutation_kind=mutation_kind,
        form_payload_digest=form_payload_digest,
    )
    if mutation is None:
        return None
    # Finished results are returned without I/O. Expired pending attempts may
    # reacquire their exact lease. Unknown outcomes require the explicit retry route.
    result = EasyImportsApiClient().dispatch(mutation)
    _record_command_outcome(request, session, result)
    return result


def _retry_token(request, session: ImportSession, mutation: ApiMutation) -> str:
    return issue_form_token(
        owner_id=owner_id_for_request(request),
        session=session,
        action_kind="retry",
        action_id=str(mutation.id),
        logical_action_identity=mutation.logical_action_identity,
        logical_action_generation=mutation.logical_action_generation,
    )


def _ack_token(request, session: ImportSession, mutation: ApiMutation) -> str:
    return issue_form_token(
        owner_id=owner_id_for_request(request),
        session=session,
        action_kind="acknowledge_rejection",
        action_id=str(mutation.id),
        logical_action_identity=mutation.logical_action_identity,
        logical_action_generation=mutation.logical_action_generation,
    )


def landing(request):
    recent_sessions = []
    for session in owned_sessions(request)[:10]:
        status_label, status_tone = SESSION_STATUS_LABELS.get(
            session.status,
            (_humanize_identifier(session.status), "created"),
        )
        has_setup = bool(session.setup_entity) or bool(session.product_key)
        recent_sessions.append(
            {
                "session": session,
                "product_label": session_display_label(session),
                "status_label": status_label,
                "status_tone": status_tone,
                "action_label": (
                    "Review results"
                    if session.active_workflow_id
                    else "Add files" if has_setup else "Continue setup"
                ),
            }
        )
    return render(
        request,
        "importer/landing.html",
        {"recent_sessions": recent_sessions},
    )


@require_POST
def start_session(request):
    session = ImportSession.objects.create(owner_id=owner_id_for_request(request))
    return redirect("importer:product", session_id=session.id)


@require_POST
def archive(request, session_id):
    session = owned_session_or_404(request, session_id)
    archive_session(session)
    messages.success(
        request, "Archived this local session. API resources were not deleted."
    )
    return redirect("importer:landing")


@require_http_methods(["GET", "POST"])
def product(request, session_id):
    session = owned_session_or_404(request, session_id)
    owner_id = owner_id_for_request(request)
    connections: list[dict] = []
    try:
        api = EasyImportsApiClient()
        targets = api.targets()["targets"]
        try:
            connections = api.crm_connections(
                owner_session=f"django-{owner_id}"
            ).get("connections", [])
        except CLIENT_ERRORS:
            connections = []
    except CLIENT_ERRORS as exc:
        return render(
            request,
            "importer/api_unavailable.html",
            {"session": session, "technical_error": str(exc)},
        )

    token = issue_form_token(
        owner_id=owner_id, session=session, action_kind="select_product"
    )
    # Phase 7A / D8: blank setup defaults to match + connected_crm when available.
    connected_for_default = [
        item for item in connections if item.get("status") == "connected"
    ]
    default_reference = (
        "connected_crm" if connected_for_default else "uploaded"
    )
    if session.setup_reference_source in {"uploaded", "connected_crm"}:
        initial_reference = session.setup_reference_source
    elif session.setup_operation == "clean_only":
        # Draft already clean-only: leave matching default for UI when operator
        # flips back to matching (field hidden while clean-only).
        initial_reference = default_reference
    else:
        initial_reference = default_reference
    catalog_default_target = (
        str(targets[0].get("target_provider_id") or "").strip() if targets else ""
    )
    form = ProductSelectionForm(
        request.POST or None,
        targets=targets,
        connections=connections,
        initial={
            "form_token": token,
            "operator_label": session.operator_label,
            "entity": session.setup_entity or None,
            # Desire #6: match is the blank-setup default.
            "operation": session.setup_operation or "crm_matching",
            "reference_source": initial_reference,
            "connection_id": session.setup_connection_id or "",
            "people_output": session.setup_people_output or "",
            # CMX-1: vocabulary intent (product default when unset).
            "vocabulary": session.setup_vocabulary or DEFAULT_VOCABULARY,
            # D6 / 7B: internal only; default sole catalog target when unset.
            "target_provider_id": (
                session.target_provider_id or catalog_default_target or None
            ),
            "setup_revision": session.setup_revision,
        },
    )
    contract_locked = session_has_frozen_workflow(session)
    if contract_locked:
        for name in (
            "entity",
            "operation",
            "reference_source",
            "people_output",
            "vocabulary",
            "target_provider_id",
        ):
            form.fields[name].disabled = True
    if session.operator_label_frozen_at is not None:
        form.fields["operator_label"].disabled = True
        form.fields["operator_label"].help_text = (
            "Frozen because review activity has begun."
        )

    if request.method == "POST" and form.is_valid():
        try:
            validate_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=session,
                action_kind="select_product",
            )
            match_provider_key = ""
            conn_id = form.cleaned_data.get("connection_id") or ""
            if conn_id:
                for item in connections:
                    if (
                        str(item.get("connection_id") or "") == conn_id
                        and item.get("status") == "connected"
                    ):
                        match_provider_key = str(item.get("provider_key") or "")
                        break
            session, notices = save_setup_draft(
                session,
                entity=form.cleaned_data["entity"],
                operation=form.cleaned_data["operation"],
                reference_source=form.cleaned_data.get("reference_source") or "none",
                people_output=form.cleaned_data.get("people_output") or "",
                target_provider_id=form.cleaned_data["target_provider_id"],
                operator_label=form.cleaned_data["operator_label"],
                expected_revision=int(form.cleaned_data["setup_revision"]),
                connection_id=conn_id,
                vocabulary=form.cleaned_data.get("vocabulary") or DEFAULT_VOCABULARY,
                match_provider_key=match_provider_key,
            )
            for notice in notices:
                messages.warning(request, notice)
            return redirect("importer:upload", session_id=session.id)
        except CLIENT_ERRORS as exc:
            _error(request, exc)
    return render(
        request,
        "importer/product.html",
        {
            "session": session,
            "form": form,
            "step": 1,
            "selected_product_label": session_display_label(session),
            "selected_target_label": (
                display_target_label(session.target_provider_id)
                if session.target_provider_id
                else ""
            ),
            "setup_revision": session.setup_revision,
        },
    )


def _active_source(session: ImportSession, role: str) -> SourceFile | None:
    if is_detached_role(role):
        return None
    source = (
        active_source_queryset(session)
        .filter(role=role)
        .select_related("upload_mutation")
        .first()
    )
    if (
        source is not None
        and source.upload_mutation.state == ApiMutation.State.COMPLETED
        and not source.api_upload_id
    ):
        source = materialize_upload_result(source)
    return source


@require_http_methods(["GET", "POST"])
def upload(request, session_id):
    session = owned_session_or_404(request, session_id)
    route = route_for_session(session)
    if route is None and not session.product_key:
        return redirect("importer:product", session_id=session.id)
    # Prefer draft route for setup; frozen product only after create.
    if route is not None:
        role_specs = source_roles_for_route(route)
    else:
        role_specs = PRODUCT_SOURCE_ROLES.get(session.product_key, ())
    owner_id = owner_id_for_request(request)

    if request.method == "POST":
        form = SourceUploadForm(
            request.POST,
            request.FILES,
            allowed_roles=role_specs,
        )
        if form.is_valid():
            role = form.cleaned_data["role"]
            if route is not None and role in route.prohibited_upload_roles:
                messages.error(
                    request,
                    "That file role is not part of the current setup.",
                )
                return redirect("importer:upload", session_id=session.id)
            try:
                claims = decode_form_token(
                    form.cleaned_data["form_token"],
                    owner_id=owner_id,
                    session=session,
                    action_kind="upload",
                )
                identity = _upload_action_identity(session, role)
                existing = session.api_mutations.filter(
                    Q(form_instance=claims["form_instance"])
                    | Q(
                        logical_action_identity=claims.get(
                            "logical_action_identity", ""
                        ),
                        logical_action_generation=claims.get(
                            "logical_action_generation", 0
                        ),
                    )
                ).first()
                if existing is None:
                    generation = next_upload_generation(session, role)
                    validate_form_token(
                        form.cleaned_data["form_token"],
                        owner_id=owner_id,
                        session=session,
                        action_kind="upload",
                        action_id=role,
                        logical_action_identity=identity,
                        logical_action_generation=generation,
                    )
                registration = save_and_register_upload(
                    session=session,
                    role=role,
                    uploaded=form.cleaned_data["file"],
                    form_instance=claims["form_instance"],
                    logical_action_generation=claims["logical_action_generation"],
                    csv_encoding=form.cleaned_data.get("csv_encoding") or "utf-8-sig",
                    xlsx_sheet_index=form.cleaned_data.get("xlsx_sheet_index") or 0,
                )
                if registration.mutation.state == ApiMutation.State.COMPLETED:
                    name = (
                        registration.source.original_name
                        if registration.source is not None
                        else "the exact upload"
                    )
                    messages.success(request, f"Added {name}.")
                    from .column_mapping_views import (
                        try_autostart_mapping_after_upload,
                    )

                    autostart = try_autostart_mapping_after_upload(
                        request, session
                    )
                    if autostart is not None:
                        return autostart
                else:
                    messages.error(request, _describe_rejection(registration.mutation))
            except CLIENT_ERRORS as exc:
                _error(request, exc)
        else:
            messages.error(request, "Correct the upload form and try again.")
        return redirect("importer:upload", session_id=session.id)

    rows = []
    for spec in role_specs:
        source = _active_source(session, spec.key)
        state = source.upload_mutation.state if source is not None else ""
        generation = next_upload_generation(session, spec.key)
        allow_upload = source is None or (
            state == ApiMutation.State.REJECTED
            and source.replacement_authorized_at is not None
        )
        identity = _upload_action_identity(session, spec.key)
        incompatible = (
            route is not None
            and source is not None
            and spec.key in route.prohibited_upload_roles
        )
        can_detach = (
            source is not None
            and incompatible
            and state
            in {ApiMutation.State.COMPLETED, ApiMutation.State.REJECTED}
        )
        row = {
            "spec": spec,
            "source": source,
            "state": state,
            "state_label": UPLOAD_STATE_PRESENTATION.get(
                state, (_humanize_identifier(state), "created")
            )[0],
            "state_tone": UPLOAD_STATE_PRESENTATION.get(
                state, (_humanize_identifier(state), "created")
            )[1],
            "allow_upload": allow_upload and not incompatible,
            "incompatible": incompatible,
            "setup_revision": session.setup_revision,
            "detach_token": (
                issue_form_token(
                    owner_id=owner_id,
                    session=session,
                    action_kind="detach_upload",
                    action_id=f"{source.id}:{session.setup_revision}",
                    logical_action_identity=identity,
                    logical_action_generation=source.slot_generation,
                )
                if can_detach
                else ""
            ),
            "token": issue_form_token(
                owner_id=owner_id,
                session=session,
                action_kind="upload",
                action_id=spec.key,
                logical_action_identity=identity,
                logical_action_generation=generation,
            ),
            "retry_token": (
                _retry_token(request, session, source.upload_mutation)
                if source is not None
                and state in {ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN}
                else ""
            ),
            "replacement_token": (
                issue_form_token(
                    owner_id=owner_id,
                    session=session,
                    action_kind="replace_upload",
                    action_id=str(source.id),
                    logical_action_identity=identity,
                    logical_action_generation=source.slot_generation,
                )
                if source is not None
                and state == ApiMutation.State.REJECTED
                and source.replacement_authorized_at is None
                else ""
            ),
        }
        rows.append(row)
    # Surface completed uploads that are no longer part of the draft route.
    if route is not None:
        allowed_keys = {spec.key for spec in role_specs}
        for source in active_source_queryset(session).select_related(
            "upload_mutation"
        ):
            if source.role in allowed_keys:
                continue
            state = source.upload_mutation.state
            identity = _upload_action_identity(session, source.role)
            can_detach = state in {
                ApiMutation.State.COMPLETED,
                ApiMutation.State.REJECTED,
            }
            rows.append(
                {
                    "spec": type(
                        "Spec",
                        (),
                        {
                            "key": source.role,
                            "label": source.role.replace("_", " ").title(),
                            "required": False,
                            "help_text": (
                                "Not used for the current setup. Retained bytes "
                                "were not deleted. Detach it to continue."
                            ),
                        },
                    )(),
                    "source": source,
                    "state": state,
                    "state_label": "Not used for this setup",
                    "state_tone": "failed",
                    "allow_upload": False,
                    "incompatible": True,
                    "setup_revision": session.setup_revision,
                    "detach_token": (
                        issue_form_token(
                            owner_id=owner_id,
                            session=session,
                            action_kind="detach_upload",
                            action_id=f"{source.id}:{session.setup_revision}",
                            logical_action_identity=identity,
                            logical_action_generation=source.slot_generation,
                        )
                        if can_detach
                        else ""
                    ),
                    "token": "",
                    "retry_token": (
                        _retry_token(request, session, source.upload_mutation)
                        if state
                        in {ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN}
                        else ""
                    ),
                    "replacement_token": "",
                }
            )
    completed_roles = completed_upload_roles(session)
    required_ready = all(
        not spec.required or spec.key in completed_roles for spec in role_specs
    )
    if route is not None:
        from .setup_router import validate_uploads_against_route

        # Include every completed active role — not only currently offered ones.
        required_ready = (
            required_ready
            and not validate_uploads_against_route(
                route, completed_roles=completed_roles
            )
        )
    derived_key = route.product_key if route is not None else session.product_key
    mapping_required = session_requires_column_mapping(session)
    mapping_confirmed = (
        session_has_confirmed_column_mapping(session) if mapping_required else True
    )
    return render(
        request,
        "importer/api_upload.html",
        {
            "session": session,
            "rows": rows,
            "required_ready": required_ready,
            "product_label": session_display_label(session),
            "derived_product_key": derived_key,
            "mapping_required": mapping_required,
            "mapping_confirmed": mapping_confirmed,
            "mapping_gate": column_mapping_gate_state(session),
            "step": 1,
        },
    )


@require_POST
def detach_upload(request, session_id, source_id):
    """Detach an incompatible active upload from setup without deleting bytes."""

    session = owned_session_or_404(request, session_id)
    source = (
        active_source_queryset(session)
        .filter(pk=source_id)
        .select_related("upload_mutation")
        .first()
    )
    if source is None:
        raise Http404("Upload slot not found.")
    try:
        try:
            expected_revision = int(request.POST.get("setup_revision", ""))
        except (TypeError, ValueError) as exc:
            raise FormTokenError(
                "This setup form is incomplete. Reload it before detaching files."
            ) from exc
        identity = _upload_action_identity(session, source.role)
        validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id_for_request(request),
            session=session,
            action_kind="detach_upload",
            action_id=f"{source.id}:{expected_revision}",
            logical_action_identity=identity,
            logical_action_generation=source.slot_generation,
        )
        # Re-check incompatibility against the current locked draft.
        route = route_for_session(session)
        if route is None or source.role not in route.prohibited_upload_roles:
            raise SetupValidationError(
                "This file is required for the current setup and cannot be "
                "detached. Change setup first if you no longer need it."
            )
        detach_active_upload(
            source, expected_setup_revision=expected_revision
        )
        messages.success(
            request,
            "Removed this file from the current setup. Stored bytes and history "
            "were kept for recovery.",
        )
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:upload", session_id=session.id)


@require_POST
def replace_upload(request, session_id, source_id):
    session = owned_session_or_404(request, session_id)
    source = (
        session.files.filter(pk=source_id, upload_mutation__isnull=False)
        .select_related("upload_mutation")
        .first()
    )
    if source is None:
        raise Http404("Upload slot not found.")
    try:
        identity = _upload_action_identity(session, source.role)
        validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id_for_request(request),
            session=session,
            action_kind="replace_upload",
            action_id=str(source.id),
            logical_action_identity=identity,
            logical_action_generation=source.slot_generation,
        )
        authorize_rejected_upload_replacement(source)
        messages.success(
            request,
            "Replacement is authorized. Choose the file for the new logical upload action.",
        )
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:upload", session_id=session.id)


@require_POST
def retry_mutation(request, session_id, mutation_id):
    session = owned_session_or_404(request, session_id)
    mutation = (
        session.api_mutations.filter(pk=mutation_id).select_related("workflow").first()
    )
    if mutation is None:
        raise Http404("Mutation not found.")
    retry_claims = None
    try:
        retry_action_id = str(mutation.id)
        if mutation.mutation_kind == "column_mapping_plan_create":
            from .column_mapping_views import plan_create_retry_action_id

            retry_action_id = plan_create_retry_action_id(session, mutation)
        retry_claims = validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id_for_request(request),
            session=session,
            action_kind="retry",
            action_id=retry_action_id,
            logical_action_identity=mutation.logical_action_identity,
            logical_action_generation=mutation.logical_action_generation,
        )
        if mutation.mutation_kind == "register_upload":
            result = retry_upload(mutation)
            final_mutation = result.mutation
        else:
            dispatch = EasyImportsApiClient().dispatch(mutation, explicit_retry=True)
            final_mutation = dispatch.mutation
            _record_command_outcome(request, session, dispatch)
        if final_mutation.state == ApiMutation.State.COMPLETED:
            messages.success(request, "The action completed.")
        elif final_mutation.state == ApiMutation.State.REJECTED:
            messages.error(request, _describe_rejection(final_mutation))
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        retry_claims = None
    mutation.refresh_from_db()
    if mutation.mutation_kind == "column_mapping_plan_create":
        if retry_claims is not None and mutation.state == ApiMutation.State.COMPLETED:
            from .column_mapping_views import (
                adopt_completed_plan_create,
                setup_revision_from_plan_create_retry_claims,
            )

            token_revision = setup_revision_from_plan_create_retry_claims(
                retry_claims, mutation
            )
            with transaction.atomic():
                locked = ImportSession.objects.select_for_update().get(
                    pk=session.pk,
                    owner_id=session.owner_id,
                )
                plan_id = adopt_completed_plan_create(
                    locked,
                    mutation,
                    expected_setup_revision=token_revision,
                )
            if plan_id:
                return redirect(
                    "importer:column_mapping_review",
                    session_id=session.id,
                    plan_id=plan_id,
                )
            messages.error(
                request,
                "This mapping result no longer matches the current file or setup.",
            )
        return redirect("importer:column_mapping_session", session_id=session.id)
    if session.active_workflow_id:
        return redirect("importer:workflow", session_id=session.id)
    if mutation.mutation_kind == "create_workflow":
        return redirect("importer:configure", session_id=session.id)
    return redirect("importer:upload", session_id=session.id)


@require_GET
def mutation_status(request, session_id, mutation_id):
    """Poll one owner-scoped API mutation and reconcile its frozen receipt."""

    session = owned_session_or_404(request, session_id)
    mutation = (
        session.api_mutations.filter(pk=mutation_id)
        .select_related("workflow")
        .first()
    )
    if mutation is None:
        raise Http404("Mutation not found.")
    if mutation.mutation_kind == "column_mapping_plan_create":
        redirect_url = reverse("importer:column_mapping_session", args=[session.id])
    elif mutation.mutation_kind == "prepare_duplicate_reviewed_result":
        redirect_url = reverse(
            "importer:crm_duplicate_journey_merge", args=[session.id]
        )
    else:
        redirect_url = reverse("importer:workflow", args=[session.id])
    if mutation.state == ApiMutation.State.COMPLETED:
        if mutation.mutation_kind != "column_mapping_plan_create":
            try:
                materialize_stored_receipt(session, mutation)
            except CLIENT_ERRORS:
                pass
        return JsonResponse({"status": "completed", "redirect_url": redirect_url})
    if mutation.state == ApiMutation.State.REJECTED:
        return JsonResponse({"status": "rejected", "redirect_url": redirect_url})
    try:
        status = EasyImportsApiClient().mutation_status(mutation.idempotency_key)
        if status["status"] == "pending":
            if status["retryable"]:
                ApiMutation.objects.filter(
                    pk=mutation.pk,
                    state=ApiMutation.State.PENDING,
                ).update(
                    state=ApiMutation.State.UNKNOWN,
                    http_status=None,
                    response_json=status,
                    error_message=(
                        "The API worker no longer owns this action. "
                        "Use the saved exact retry."
                    ),
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=timezone.now(),
                )
                return JsonResponse(
                    {
                        "status": "retryable",
                        "redirect_url": redirect_url,
                    }
                )
            return JsonResponse(
                {
                    "status": "pending",
                    "retry_after_seconds": status["retry_after_seconds"],
                }
            )
        result = EasyImportsApiClient().reconcile_mutation_status(mutation, status)
        if result is None:
            return JsonResponse(
                {
                    "status": "pending",
                    "retry_after_seconds": status["retry_after_seconds"],
                }
            )
        _record_command_outcome(request, session, result)
        return JsonResponse(
            {
                "status": (
                    "completed"
                    if result.mutation.state == ApiMutation.State.COMPLETED
                    else "rejected"
                ),
                "redirect_url": redirect_url,
            }
        )
    except CLIENT_ERRORS:
        return JsonResponse(
            {
                "status": "unavailable",
                "retry_after_seconds": 5,
            },
            status=503,
        )


@require_POST
def acknowledge_rejection(request, session_id, mutation_id):
    session = owned_session_or_404(request, session_id)
    mutation = session.api_mutations.filter(pk=mutation_id).first()
    if mutation is None:
        raise Http404("Mutation not found.")
    try:
        validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id_for_request(request),
            session=session,
            action_kind="acknowledge_rejection",
            action_id=str(mutation.id),
            logical_action_identity=mutation.logical_action_identity,
            logical_action_generation=mutation.logical_action_generation,
        )
        with transaction.atomic():
            locked = ApiMutation.objects.select_for_update().get(
                pk=mutation.pk, session=session
            )
            if locked.state != ApiMutation.State.REJECTED:
                raise MutationReuseError("Only a frozen rejection can be acknowledged.")
            if locked.is_idempotency_conflict:
                raise MutationReuseError(
                    "This idempotency-conflicted key requires explicit reconciliation; "
                    "the client will not create a replacement automatically."
                )
            if locked.acknowledged_at is None:
                locked.acknowledged_at = timezone.now()
                locked.save(update_fields=["acknowledged_at", "updated_at"])
        messages.success(
            request,
            "You can review the settings and try again.",
        )
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    if mutation.mutation_kind == "column_mapping_plan_create":
        return redirect("importer:column_mapping_session", session_id=session.id)
    if session.active_workflow_id:
        return redirect("importer:workflow", session_id=session.id)
    return redirect("importer:configure", session_id=session.id)


@require_http_methods(["GET", "POST"])
def configure(request, session_id):
    session = owned_session_or_404(request, session_id)
    owner_id = owner_id_for_request(request)
    identity = _creation_action_identity(session)
    route = route_for_session(session)
    if route is None and not session.product_key:
        return redirect("importer:product", session_id=session.id)

    mapping_gate = column_mapping_gate_state(session)
    mapping_required = session_requires_column_mapping(session)
    mapping_confirmed = (
        session_has_confirmed_column_mapping(session) if mapping_required else True
    )

    if request.method == "POST":
        # MAP-R5: do not surface or accept Start import without a confirmed bind.
        if mapping_required and not mapping_confirmed:
            messages.error(
                request,
                "Confirm column mapping before starting the import. "
                "Open Map columns to finish mapping, then return here.",
            )
            return redirect("importer:configure", session_id=session.id)
        try:
            claims, payload_digest = _decode_submission(
                request, session, "create_workflow"
            )
            replay = _replay_if_recorded(
                request,
                session=session,
                claims=claims,
                mutation_kind="create_workflow",
                form_payload_digest=payload_digest,
            )
            if replay is not None:
                if replay.mutation.state == ApiMutation.State.COMPLETED:
                    session.refresh_from_db()
                    return redirect("importer:workflow", session_id=session.id)
                return redirect("importer:configure", session_id=session.id)
        except CLIENT_ERRORS as exc:
            _error(request, exc)
            return redirect("importer:configure", session_id=session.id)
    else:
        claims = None
        payload_digest = ""

    completed_creation = (
        session.api_mutations.filter(
            mutation_kind="create_workflow", state=ApiMutation.State.COMPLETED
        )
        .order_by("-created_at")
        .first()
    )
    if session.active_workflow_id is None and completed_creation is not None:
        try:
            if materialize_stored_receipt(session, completed_creation) is not None:
                session.refresh_from_db()
                return redirect("importer:workflow", session_id=session.id)
        except CLIENT_ERRORS as exc:
            _error(request, exc)
    # If a primary workflow already exists without product_key, reconcile from it.
    if session.active_workflow_id and not session.product_key:
        try:
            from .workflow_state import resolve_session_product_key_for_projection

            active = session.active_workflow
            resolved = resolve_session_product_key_for_projection(
                role=active.role,
                workflow_key=active.workflow_key,
                source_workflow=active.source_workflow,
            )
            if resolved is not None:
                freeze_product_key(session, resolved)
                session.refresh_from_db()
        except CLIENT_ERRORS:
            pass
    outstanding = (
        session.api_mutations.filter(
            mutation_kind="create_workflow",
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
        )
        .order_by("-created_at")
        .first()
    )
    rejected = (
        session.api_mutations.filter(
            mutation_kind="create_workflow",
            state=ApiMutation.State.REJECTED,
            acknowledged_at__isnull=True,
        )
        .order_by("-created_at")
        .first()
    )
    # Recover upload projections without HTTP when needed.
    if route is not None:
        for role in route.allowed_upload_roles():
            _active_source(session, role)
    elif session.product_key:
        for spec in PRODUCT_SOURCE_ROLES.get(session.product_key, ()):
            _active_source(session, spec.key)

    try:
        api = EasyImportsApiClient()
        products = api.products()["products"]
        targets = api.targets()["targets"]
        connections: list[dict] = []
        try:
            connections = api.crm_connections(
                owner_session=f"django-{owner_id}"
            ).get("connections", [])
        except CLIENT_ERRORS:
            connections = []

        if route is not None and not session.product_key:
            provisional_product = route.product_key
            target = resolve_configure_target(
                session=session,
                targets=targets,
                product_key=provisional_product,
                connections=connections,
            )
            intent, route, uploads = create_time_preflight(
                session,
                products=products,
                target=target,
                connections=connections,
            )
            derived_product_key = route.product_key
            route_defaults = {
                "target_object": route.target_object,
                "content_type": route.content_type,
                "canon_profile": route.canon_profile,
                "person_kind": route.person_kind,
            }
            acquisition_requirement = route.reference_acquisition_requirement
        else:
            # Frozen / resumed runs: API product identity controls.
            derived_product_key = session.product_key or (
                route.product_key if route is not None else ""
            )
            uploads = completed_upload_ids(session)
            route_defaults = {}
            acquisition_requirement = None
            intent = read_operator_intent(session)
            if route is not None and not session.product_key:
                route_defaults = {
                    "target_object": route.target_object,
                    "content_type": route.content_type,
                    "canon_profile": route.canon_profile,
                    "person_kind": route.person_kind,
                }
                acquisition_requirement = route.reference_acquisition_requirement
            target = resolve_configure_target(
                session=session,
                targets=targets,
                product_key=derived_product_key,
                connections=connections,
            )

        product_entry = next(
            item for item in products if item["product_key"] == derived_product_key
        )
        generation = action_generation(session, identity)
        token = issue_form_token(
            owner_id=owner_id,
            session=session,
            action_kind="create_workflow",
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
        from .campaign_member_setup import load_session_resolutions_json

        verified_resolutions = load_session_resolutions_json(session)
        form_initial = {
            "form_token": token,
            "setup_revision": session.setup_revision,
            "campaign_match_resolutions_json": verified_resolutions,
        }
        raw_list_source = (
            active_source_queryset(session)
            .filter(
                role="raw_list",
                upload_mutation__state=ApiMutation.State.COMPLETED,
            )
            .exclude(api_upload_id="")
            .first()
        )
        connection_id = str(session.setup_connection_id or "").strip()
        provider_key = ""
        if connection_id:
            for item in connections:
                if str(item.get("connection_id") or "") == connection_id:
                    provider_key = str(item.get("provider_key") or "")
                    break
        # Phase 7A: ephemerally fetch member statuses (FE-CM-1) for each Campaign
        # already resolved (default binding + verified name resolutions) so the
        # configure form can bound the default-status picker (D12). Best effort:
        # a browse failure or missing status simply leaves the picker unbounded.
        campaign_member_statuses = _resolved_campaign_member_statuses(
            connection_id=connection_id,
            owner_session=f"django-{owner_id}",
            verified_resolutions_json=verified_resolutions,
            default_campaign_binding=str(
                (request.POST.get("default_campaign_binding") or "")
                if request.method == "POST"
                else (form_initial.get("default_campaign_binding") or "")
            ).strip(),
            product_key=derived_product_key,
        )
        form = WorkflowConfigurationForm(
            request.POST or None,
            product_entry=product_entry,
            target=target,
            uploaded_roles=set(uploads),
            route_defaults=route_defaults,
            reference_acquisition_requirement=acquisition_requirement,
            connection_id=connection_id,
            verified_resolutions_json=verified_resolutions,
            raw_list_source=raw_list_source,
            vocabulary=session.setup_vocabulary or "",
            provider_key=provider_key,
            campaign_member_statuses=campaign_member_statuses,
            initial=form_initial,
        )
    except (StopIteration, CatalogCompatibilityError, *CLIENT_ERRORS) as exc:
        if isinstance(exc, SetupValidationError):
            messages.error(request, str(exc))
            return redirect("importer:upload", session_id=session.id)
        return render(
            request,
            "importer/api_unavailable.html",
            {"session": session, "technical_error": str(exc)},
        )

    if (
        request.method == "POST"
        and form.is_valid()
        and outstanding is None
        and rejected is None
        and completed_creation is None
    ):
        try:
            assert claims is not None
            validate_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=session,
                action_kind="create_workflow",
                logical_action_identity=identity,
                logical_action_generation=generation,
            )
            expected_revision = form.cleaned_data.get("setup_revision")
            if expected_revision is not None:
                expected_revision = int(expected_revision)
            # Single transaction: lock draft, rederive, lock sources, preclaim.
            session, derived_product_key, _body, mutation = preclaim_create_workflow(
                session,
                expected_revision=expected_revision,
                products=products,
                targets=targets,
                connections=connections,
                form=form,
                form_instance=claims["form_instance"],
                form_payload_digest=payload_digest,
                logical_action_identity=identity,
                logical_action_generation=generation,
            )
            # Network I/O only after the preclaim transaction commits.
            result = EasyImportsApiClient().dispatch(mutation)
            if result.mutation.state == ApiMutation.State.COMPLETED:
                freeze_product_key(session, derived_product_key)
                # Resolutions are frozen in the create body; drop draft store.
                from .campaign_member_setup import clear_session_resolutions

                clear_session_resolutions(session)
            projected = _record_command_outcome(
                request, session, MutationDispatchResult(result.mutation, result.response)
            )
            if projected is not None:
                session.refresh_from_db()
                return redirect("importer:workflow", session_id=session.id)
            return redirect("importer:configure", session_id=session.id)
        except CLIENT_ERRORS as exc:
            _error(request, exc)
    role_specs = (
        source_roles_for_route(route)
        if route is not None
        else PRODUCT_SOURCE_ROLES.get(derived_product_key, ())
    )
    cm_context = _campaign_member_configure_context(
        request,
        session=session,
        product_key=derived_product_key,
        target=target,
        connections=connections,
        form=form,
    )
    return render(
        request,
        "importer/api_configure.html",
        {
            "session": session,
            "form": form,
            "uploads": uploads,
            "uploaded_rows": [
                {
                    "role": spec.key,
                    "label": spec.label,
                    "upload_id": uploads[spec.key],
                }
                for spec in role_specs
                if spec.key in uploads
            ],
            "product_label": session_display_label(session),
            "derived_product_key": derived_product_key,
            "setup_intent": (
                intent.as_dict() if intent is not None else None
            ),
            "target_label": display_target_label(session.target_provider_id),
            "step": 3,
            "mapping_gate": mapping_gate,
            "mapping_required": mapping_required,
            "mapping_confirmed": mapping_confirmed,
            "outstanding": outstanding,
            "rejected": rejected,
            "rejection_ack_token": (
                _ack_token(request, session, rejected) if rejected else ""
            ),
            "awaiting_projection": completed_creation is not None
            and session.active_workflow_id is None,
            "retry_token": (
                _retry_token(request, session, outstanding) if outstanding else ""
            ),
            **cm_context,
            **_configure_form_section_context(
                form,
                product_key=derived_product_key,
                reference_requirement=acquisition_requirement,
                campaign_ceiling=cm_context.get("cm_ceiling") or "disabled",
            ),
        },
    )


def _configure_form_section_context(
    form,
    *,
    product_key: str,
    reference_requirement: str | None,
    campaign_ceiling: str,
) -> dict:
    """OUT-6B: grouped configure surfaces. Django does not import mappings_2."""

    from .configure_layout import configure_form_sections

    return configure_form_sections(
        form,
        product_key=product_key,
        reference_requirement=reference_requirement,
        campaign_ceiling=campaign_ceiling,
    )


def _campaign_member_configure_context(
    request,
    *,
    session: ImportSession,
    product_key: str,
    target: dict,
    connections: list[dict],
    form,
) -> dict:
    """FE-CM-3: Campaign search/pick surface for list-import configure."""

    from .campaign_member_setup import (
        CampaignMemberSetupError,
        campaign_member_ceiling_from_connection,
        resolution_technical_rows,
    )

    empty = {
        "cm_enabled_for_product": False,
        "cm_connection_id": "",
        "cm_ceiling": "disabled",
        "cm_search_q": "",
        "cm_search_result": None,
        "cm_search_error": "",
        "cm_status_result": None,
        "cm_resolution_rows": [],
        "cm_pick_token": "",
        "cm_clear_token": "",
    }
    if product_key != "easyimports.list_import":
        return empty

    connection_id = str(session.setup_connection_id or "").strip()
    connection = next(
        (
            item
            for item in connections
            if str(item.get("connection_id") or "") == connection_id
        ),
        None,
    )
    ceiling = str(
        (target.get("maximum_modes") or {}).get("campaign_member_writes")
        or campaign_member_ceiling_from_connection(connection)
    )
    from .campaign_member_setup import load_session_resolutions

    # Durable session store (survives multi-search); not one-shot flash.
    parsed_for_rows = load_session_resolutions(session) if connection_id else []

    context = {
        "cm_enabled_for_product": True,
        "cm_connection_id": connection_id,
        "cm_ceiling": ceiling,
        "cm_search_q": str(request.GET.get("cm_q") or "").strip(),
        "cm_search_result": None,
        "cm_search_error": "",
        "cm_status_result": None,
        "cm_resolution_rows": resolution_technical_rows(
            {"campaign_match_resolutions": parsed_for_rows}
        ),
        "cm_pick_token": "",
        "cm_clear_token": "",
    }
    if not connection_id:
        return context

    owner_session = f"django-{owner_id_for_request(request)}"
    owner_id = owner_id_for_request(request)
    pick_identity = f"session:{session.id}:cm_resolution_pick"
    pick_generation = action_generation(session, pick_identity)
    context["cm_pick_token"] = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind="cm_resolution_pick",
        logical_action_identity=pick_identity,
        logical_action_generation=pick_generation,
    )
    clear_identity = f"session:{session.id}:cm_resolution_clear"
    clear_generation = action_generation(session, clear_identity)
    context["cm_clear_token"] = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind="cm_resolution_clear",
        logical_action_identity=clear_identity,
        logical_action_generation=clear_generation,
    )

    # Ephemeral search (GET) — does not freeze authority; FE-CM-1 proxy only.
    search_q = context["cm_search_q"]
    if search_q:
        try:
            context["cm_search_result"] = EasyImportsApiClient().crm_campaigns_search(
                connection_id,
                owner_session=owner_session,
                mode="name_exact",
                q=search_q,
            )
        except CLIENT_ERRORS as exc:
            context["cm_search_error"] = str(exc)

    campaign_id = str(request.GET.get("cm_campaign_id") or "").strip()
    if campaign_id:
        try:
            context["cm_status_result"] = (
                EasyImportsApiClient().crm_campaign_member_statuses(
                    connection_id,
                    campaign_id,
                    owner_session=owner_session,
                )
            )
        except CLIENT_ERRORS:
            context["cm_status_result"] = None

    return context


@require_http_methods(["POST"])
def campaign_resolution_pick(request, session_id):
    """FE-CM-3 rem: lookup-bound pick into durable ImportSession.options store.

    Re-runs FE-CM-1 name search server-side; only campaign Ids present in that
    response may be saved. selection_source is derived from returned count.
    Multi-search accumulates entries (not a one-shot flash).
    """

    from .campaign_member_setup import (
        CampaignMemberSetupError,
        append_resolution_entry,
        load_session_resolutions,
        resolution_from_lookup_evidence,
        save_session_resolutions,
    )

    session = owned_session_or_404(request, session_id)
    owner_id = owner_id_for_request(request)
    identity = f"session:{session.id}:cm_resolution_pick"
    try:
        generation = action_generation(session, identity)
        validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id,
            session=session,
            action_kind="cm_resolution_pick",
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
        connection_id = str(session.setup_connection_id or "").strip()
        if not connection_id:
            raise CampaignMemberSetupError(
                "Connect a CRM before picking a Campaign."
            )
        # Operator may pass the search q used to produce the candidate list.
        # The Id must still appear in a fresh server-side search for that q.
        match_q = str(
            request.POST.get("match_key")
            or request.POST.get("cm_q")
            or ""
        ).strip()
        campaign_id = str(request.POST.get("campaign_id") or "").strip()
        if not match_q:
            raise CampaignMemberSetupError(
                "Search for a Campaign name before picking a resolution."
            )
        if len(match_q) < 2:
            raise CampaignMemberSetupError(
                "Campaign name search requires at least 2 characters."
            )
        owner_session = f"django-{owner_id}"
        search_result = EasyImportsApiClient().crm_campaigns_search(
            connection_id,
            owner_session=owner_session,
            mode="name_exact",
            q=match_q,
        )
        entry = resolution_from_lookup_evidence(
            connection_id=connection_id,
            match_key_raw=match_q,
            campaign_id=campaign_id,
            search_result=search_result,
        )
        existing = load_session_resolutions(session)
        updated = append_resolution_entry(
            existing,
            connection_id=connection_id,
            entry=entry,
        )
        save_session_resolutions(session, updated)
        messages.success(
            request,
            "Campaign name resolution saved. Search another name if needed; "
            "all picks freeze when you start the import.",
        )
    except (*CLIENT_ERRORS, CampaignMemberSetupError, FormTokenError) as exc:
        _error(request, exc)
    # Preserve search query so multi-Campaign workflow can continue cleanly.
    q = str(request.POST.get("cm_q") or request.POST.get("match_key") or "").strip()
    if q:
        return redirect(
            f"{reverse('importer:configure', args=[session.id])}?cm_q={q}"
        )
    return redirect("importer:configure", session_id=session.id)


@require_http_methods(["POST"])
def campaign_resolution_clear(request, session_id):
    """Clear exploratory draft Campaign name resolutions without enabling CM."""

    from .campaign_member_setup import clear_session_resolutions

    session = owned_session_or_404(request, session_id)
    owner_id = owner_id_for_request(request)
    identity = f"session:{session.id}:cm_resolution_clear"
    try:
        generation = action_generation(session, identity)
        validate_form_token(
            request.POST.get("form_token", ""),
            owner_id=owner_id,
            session=session,
            action_kind="cm_resolution_clear",
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
        clear_session_resolutions(session)
        messages.success(
            request,
            "Draft Campaign name resolutions cleared. Campaign membership remains "
            "off until you opt in.",
        )
    except (*CLIENT_ERRORS, FormTokenError) as exc:
        _error(request, exc)
    return redirect("importer:configure", session_id=session.id)


@require_GET
def campaign_lookup_proxy(request, session_id):
    """FE-CM-1 proxy under session ownership (JSON for optional clients)."""

    session = owned_session_or_404(request, session_id)
    connection_id = str(session.setup_connection_id or "").strip()
    if not connection_id:
        return JsonResponse(
            {"error": "no_connection", "message": "No CRM connection on this setup."},
            status=422,
        )
    owner_session = f"django-{owner_id_for_request(request)}"
    mode = str(request.GET.get("mode") or "name_exact").strip()
    q = str(request.GET.get("q") or "").strip()
    campaign_id = str(request.GET.get("campaign_id") or "").strip()
    want_statuses = str(request.GET.get("member_statuses") or "").strip() in {
        "1",
        "true",
        "yes",
    }
    try:
        api = EasyImportsApiClient()
        if campaign_id and want_statuses:
            payload = api.crm_campaign_member_statuses(
                connection_id,
                campaign_id,
                owner_session=owner_session,
            )
        elif campaign_id:
            payload = api.crm_campaign(connection_id, campaign_id, owner_session=owner_session)
        else:
            if not q:
                return JsonResponse(
                    {"error": "q_required", "message": "Search query q is required."},
                    status=422,
                )
            payload = api.crm_campaigns_search(
                connection_id,
                owner_session=owner_session,
                mode=mode,
                q=q,
            )
        return JsonResponse(payload)
    except CLIENT_ERRORS as exc:
        status = 503 if isinstance(exc, ApiUnavailableError) else 422
        return JsonResponse({"error": "lookup_failed", "message": str(exc)}, status=status)


def _token_for_workflow_action(
    request,
    session: ImportSession,
    workflow: ApiWorkflow,
    *,
    action_kind: str,
    action_id: str = "",
    group_id: str = "",
) -> str:
    identity = _workflow_action_identity(workflow, action_kind, action_id, group_id)
    generation = action_generation(session, identity)
    return issue_form_token(
        owner_id=owner_id_for_request(request),
        session=session,
        workflow=workflow,
        action_kind=action_kind,
        action_id=action_id,
        logical_action_identity=identity,
        logical_action_generation=generation,
    )


def _workflow_tokens(request, session, workflow):
    tokens: dict[str, str] = {}
    projection = workflow.projection
    decision = projection.get("decision")
    if decision:
        group_id = str((decision.get("body") or {}).get("duplicate_group_id", ""))
        tokens["decision"] = _token_for_workflow_action(
            request,
            session,
            workflow,
            action_kind="decision",
            action_id=decision["decision_id"],
            group_id=group_id,
        )
    intent = projection.get("effect_intent")
    if intent:
        tokens["effect"] = _token_for_workflow_action(
            request,
            session,
            workflow,
            action_kind="effect",
            action_id=intent["intent_id"],
        )
    handoff = projection.get("review_handoff")
    if handoff:
        tokens["review"] = _token_for_workflow_action(
            request,
            session,
            workflow,
            action_kind="start_review",
            action_id=handoff["handoff_id"],
        )
    if projection.get("status") in {"paused_unknown", "paused_verification"}:
        tokens["resume"] = _token_for_workflow_action(
            request, session, workflow, action_kind="resume"
        )
    if projection.get("status") == "awaiting_execution_continuation" or projection.get(
        "continuation_available"
    ):
        tokens["continue_duplicate_execution"] = _token_for_workflow_action(
            request,
            session,
            workflow,
            action_kind="continue_duplicate_execution",
        )
        tokens["finish_duplicate_execution"] = _token_for_workflow_action(
            request,
            session,
            workflow,
            action_kind="finish_duplicate_execution",
        )
    package_workflow = _package_target_workflow(session, workflow)
    if package_workflow is not None and _run_output_package_eligible(package_workflow):
        identity = _run_output_package_action_identity(package_workflow)
        # Transient metadata/API failure must not 500 the terminal page.
        try:
            generation = run_output_package_create_generation(
                session, package_workflow
            )
        except CLIENT_ERRORS:
            generation = _local_package_create_generation(session, identity)
        tokens["run_output_package"] = issue_form_token(
            owner_id=owner_id_for_request(request),
            session=session,
            workflow=package_workflow,
            action_kind="create_run_output_package",
            action_id=f"{package_workflow.run_id}:{package_workflow.revision}",
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
    return tokens


def _run_output_package_eligible(workflow: ApiWorkflow) -> bool:
    """Terminal package CTA only for freeze §5.2 workflow keys at succeeded."""

    if workflow.status != "succeeded":
        return False
    key = str(workflow.workflow_key or workflow.projection.get("workflow_key") or "")
    return key in RUN_OUTPUT_PACKAGE_ELIGIBLE_KEYS


def _duplicate_continuation_workflow(session: ImportSession) -> ApiWorkflow | None:
    """Lease-bound merge continuation, or None when the CTA must stay hidden."""

    if session.product_key != DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return None
    try:
        lease = CrmDuplicateMergePlanLease.objects.get(session=session)
    except CrmDuplicateMergePlanLease.DoesNotExist:
        return None
    continuation_run_id = str(lease.continuation_run_id or "").strip()
    if not continuation_run_id:
        return None
    return session.api_workflows.filter(run_id=continuation_run_id).first()


def _package_target_workflow(
    session: ImportSession, current: ApiWorkflow
) -> ApiWorkflow | None:
    """List-import uses the displayed run; CRM-dupe uses the lease continuation."""

    if session.product_key == DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return _duplicate_continuation_workflow(session)
    return current


def _package_layout_for_session(session: ImportSession) -> str:
    if session.product_key == DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return RUN_OUTPUT_PACKAGE_LAYOUT_V2
    return RUN_OUTPUT_PACKAGE_LAYOUT


def _package_layout_for_workflow(workflow: ApiWorkflow) -> str:
    key = str(workflow.workflow_key or workflow.projection.get("workflow_key") or "")
    if key == DUPLICATE_RESOLUTION_PRODUCT_KEY:
        return RUN_OUTPUT_PACKAGE_LAYOUT_V2
    return RUN_OUTPUT_PACKAGE_LAYOUT


def _run_output_package_action_identity(workflow: ApiWorkflow) -> str:
    return _workflow_action_identity(
        workflow,
        "create_run_output_package",
        f"{workflow.run_id}:{workflow.revision}",
    )


def _package_matches_workflow(
    package: dict,
    workflow: ApiWorkflow,
    *,
    expected_layout: str | None = None,
) -> bool:
    """Exact run_id / revision / workflow_key binding for package resources.

    Pass ``expected_layout`` for terminal discovery/create so a retained v1
    package cannot masquerade as a v2 duplicate-results ZIP. Package-ID
    downloads omit it so old v1 bytes stay readable until TTL.
    """

    if not isinstance(package, dict):
        return False
    try:
        revision = int(package.get("run_revision"))
    except (TypeError, ValueError):
        return False
    if expected_layout is not None and str(package.get("layout_version") or "") != (
        expected_layout
    ):
        return False
    return (
        str(package.get("run_id") or "") == str(workflow.run_id)
        and revision == int(workflow.revision)
        and str(package.get("workflow_key") or "")
        == str(workflow.workflow_key or "")
        and bool(str(package.get("package_id") or "").strip())
        and bool(str(package.get("content_digest") or "").strip())
    )


def _package_missing_codes() -> frozenset[str]:
    return frozenset(
        {
            "run_output_package_not_found",
            "run_output_package_expired",
            "resource_not_found",
        }
    )


def _live_package_for_id(
    *,
    package_id: str,
    workflow: ApiWorkflow,
    owner_session: str,
    client: EasyImportsApiClient,
    expected_layout: str | None = None,
) -> dict | None:
    """Read-only package metadata when still available and identity-bound."""

    try:
        live = client.run_output_package(
            package_id, owner_session=owner_session
        )
    except ApiRejectedError as exc:
        if getattr(exc, "code", "") in _package_missing_codes():
            return None
        raise
    if not _package_matches_workflow(
        live, workflow, expected_layout=expected_layout
    ):
        return None
    if str(live.get("package_id") or "") != str(package_id):
        return None
    return live


def _completed_run_output_package_mutation(
    session: ImportSession, workflow: ApiWorkflow
) -> ApiMutation | None:
    identity = _run_output_package_action_identity(workflow)
    return (
        session.api_mutations.filter(
            mutation_kind="create_run_output_package",
            state=ApiMutation.State.COMPLETED,
            logical_action_identity=identity,
            workflow=workflow,
        )
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )


def _local_package_create_generation(
    session: ImportSession, identity: str
) -> int:
    """Network-free generation: rejection advance only; never TTL advance."""

    latest = (
        session.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest is None:
        return 0
    if (
        latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is not None
        and not latest.is_idempotency_conflict
    ):
        return latest.logical_action_generation + 1
    return latest.logical_action_generation


def run_output_package_create_generation(
    session: ImportSession,
    workflow: ApiWorkflow,
    *,
    client: EasyImportsApiClient | None = None,
) -> int:
    """Generation for package create; advances when prior package is gone/expired.

    Completed creates normally freeze generation for exact-retry. After TTL
    delete/expiry the operator must open a new generation (new Idempotency-Key)
    so recreate is possible.

    Transient API/metadata failures do **not** advance generation (keep
    exact-retry of the frozen create receipt).
    """

    identity = _run_output_package_action_identity(workflow)
    latest = (
        session.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest is None:
        return 0
    if (
        latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is not None
        and not latest.is_idempotency_conflict
    ):
        return latest.logical_action_generation + 1
    if latest.state == ApiMutation.State.COMPLETED:
        owner_session = owner_session_for_import_session(session)
        package = (
            latest.response_json if isinstance(latest.response_json, dict) else {}
        )
        package_id = str(package.get("package_id") or "").strip()
        if not owner_session or not package_id:
            return latest.logical_action_generation + 1
        api = client or EasyImportsApiClient()
        try:
            live = _live_package_for_id(
                package_id=package_id,
                workflow=workflow,
                owner_session=owner_session,
                client=api,
                expected_layout=_package_layout_for_workflow(workflow),
            )
        except CLIENT_ERRORS:
            # Outage/consistency: preserve generation so terminal can still render.
            return latest.logical_action_generation
        if live is None:
            return latest.logical_action_generation + 1
    return latest.logical_action_generation


def _resolve_run_output_package(
    session: ImportSession,
    workflow: ApiWorkflow,
    *,
    client: EasyImportsApiClient | None = None,
) -> dict | None:
    """Live package only: verify completed receipts via read-only metadata GET."""

    if not _run_output_package_eligible(workflow):
        return None
    owner_session = owner_session_for_import_session(session)
    if not owner_session:
        return None
    api = client or EasyImportsApiClient()

    expected_layout = _package_layout_for_session(session)
    mutation = _completed_run_output_package_mutation(session, workflow)
    if mutation is not None and isinstance(mutation.response_json, dict):
        package_id = str(mutation.response_json.get("package_id") or "").strip()
        if package_id:
            live = _live_package_for_id(
                package_id=package_id,
                workflow=workflow,
                owner_session=owner_session,
                client=api,
                expected_layout=expected_layout,
            )
            if live is not None:
                return live
            # Receipt is stale (deleted/expired) — do not surface a dead link.

    try:
        by_run = api.run_output_package_for_run(
            workflow.run_id, owner_session=owner_session
        )
    except ApiRejectedError as exc:
        if getattr(exc, "code", "") in _package_missing_codes():
            return None
        raise
    if _package_matches_workflow(
        by_run, workflow, expected_layout=expected_layout
    ):
        return by_run
    return None


def _dataframe(name: str, frame: dict) -> dict:
    columns = list(frame.get("columns") or [])
    return {
        "name": name.replace("_df", "").replace("_", " ").title(),
        "columns": columns,
        "rows": [
            {
                "raw": row,
                "cells": [row.get(column) for column in columns],
            }
            for row in (frame.get("rows") or [])
        ],
    }


def _operator_visible_columns(
    *,
    decision_type: str | None,
    columns: list[str],
    column_metadata: dict[str, dict],
    option_column: str,
    group_column: str | None,
) -> list[str]:
    available = []
    context_names = set()
    for _label, names in GROUPED_SELECTION_CONTEXT_FIELDS.get(str(decision_type), ()):
        if isinstance(names, str):
            context_names.add(names)
        else:
            context_names.update(names)
    for column in columns:
        metadata = column_metadata.get(column, {})
        if column in {option_column, group_column}:
            continue
        if column in GROUPED_SELECTION_HIDDEN_COLUMNS or column in context_names:
            continue
        if _is_technical_identifier(column):
            continue
        if metadata and not metadata.get("visible", True):
            continue
        available.append(column)
    return available


def _grouped_display_columns(
    *,
    decision_type: str | None,
    columns: list[str],
    column_metadata: dict[str, dict],
    option_column: str,
    group_column: str | None,
    product_key: str = "",
    setup_entity: str = "",
) -> list[dict[str, str]]:
    available = _operator_visible_columns(
        decision_type=decision_type,
        columns=columns,
        column_metadata=column_metadata,
        option_column=option_column,
        group_column=group_column,
    )
    ordered = select_existing_review_columns(
        decision_type=decision_type,
        available=available,
        product_key=product_key,
        setup_entity=setup_entity,
    )
    return [
        {
            "name": column,
            "label": (
                column_metadata.get(column, {}).get("label")
                or _humanize_identifier(column)
            ),
        }
        for column in ordered
    ]


def _skip_legacy_account_alias_when_current_present(
    column: str,
    available: list[str] | set[str],
    ordered: list[str],
) -> bool:
    """Prefer account_* / candidate_account_* over dual-published legacy twins."""

    try:
        from mappings_2.canon.account_column_aliases import (
            is_legacy_account_token,
            legacy_to_current,
        )
    except Exception:  # pragma: no cover - web without mappings_2 in rare envs
        return False
    if not is_legacy_account_token(column):
        return False
    current = legacy_to_current(column)
    return current in available or current in ordered


def _grouped_context_items(decision_type: str | None, row: dict) -> list[dict]:
    items = []
    for entry in GROUPED_SELECTION_CONTEXT_FIELDS.get(str(decision_type), ()):
        label = entry[0]
        names = entry[1]
        if isinstance(names, str):
            names = (names,)
        value = None
        for name in names:
            raw = row.get(name)
            if _has_display_value(raw):
                value = _display_value(raw)
                break
        # Omit context fields with no displayable value (do not emit blanks).
        if value is None or not _has_display_value(value):
            continue
        items.append({"label": label, "value": value})
    return items


def _grouped_evidence_highlights(
    decision_type: str | None,
    row: dict,
) -> list[str]:
    if str(row.get("option_kind") or "") not in {"uploaded_row", "candidate"}:
        return []
    highlights = []
    if decision_type == "list_duplicates":
        rank = row.get("list_duplicate_recommendation_rank")
        if _has_display_value(rank):
            highlights.append(f"Completeness rank {rank}")
        useful = row.get("list_duplicate_filled_field_count")
        if _has_display_value(useful):
            highlights.append(f"{useful} useful fields")
        if row.get("list_duplicate_has_email"):
            highlights.append("Has email")
        if row.get("list_duplicate_has_account_identity"):
            highlights.append("Has account identity")
        rules = (
            row.get("list_duplicate_evidence_rules")
            or row.get("list_duplicate_rules")
        )
        if _has_display_value(rules):
            highlights.append(
                "Matched on "
                + str(_display_value(rules, evidence=True)).lower()
            )
    else:
        score = row.get("match_score")
        if _has_display_value(score):
            highlights.append(f"Match score {score}")
        source = row.get("candidate_sources") or row.get("candidate_source")
        if _has_display_value(source):
            highlights.append(
                "Matched by "
                + str(_display_value(source, evidence=True)).lower()
            )
        candidate_rank = row.get("candidate_rank")
        if _has_display_value(candidate_rank):
            highlights.append(f"Match tier {candidate_rank}")
        if (
            decision_type == "multiple_crm_matches"
            and str(row.get("candidate_person_type") or "") == "Contact"
        ):
            highlights.append("Existing Contact")
    return highlights[:4]


def _duplicate_review_member_key(decision_type: str | None) -> str:
    return (
        "account_id"
        if decision_type == "account_duplicate_group_review"
        else "person_id"
    )


def _first_display_value(*sources: tuple[dict, tuple[str, ...]]) -> object:
    for row, names in sources:
        for name in names:
            value = row.get(name)
            if _has_display_value(value):
                return _display_value(value)
    return ""


def _duplicate_review_identity(
    decision_type: str,
    detail: dict,
    ranking: dict,
) -> object:
    if decision_type == "account_duplicate_group_review":
        return _first_display_value(
            (
                detail,
                (
                    "account_name_final",
                    "acct_name_final",
                    "account_name",
                    "acct_name",
                    "__dr_account_name",
                    "Name",
                    "name",
                ),
            ),
        )
    full_name = _first_display_value(
        (
            detail,
            (
                "full_name",
                "contact_full_name_final",
                "person_full_name",
                "Name",
                "name",
            ),
        ),
    )
    if _has_display_value(full_name):
        return full_name
    first_name = _first_display_value(
        (detail, ("first_name", "firstname", "FirstName"))
    )
    last_name = _first_display_value(
        (detail, ("last_name", "lastname", "LastName"))
    )
    return " ".join(
        str(value) for value in (first_name, last_name) if _has_display_value(value)
    )


def _duplicate_review_location(detail: dict) -> str:
    city = _first_display_value(
        (
            detail,
            (
                "acct_billing_city",
                "__dr_city",
                "city",
                "City",
            ),
        )
    )
    state = _first_display_value(
        (
            detail,
            (
                "acct_billing_state",
                "__dr_state",
                "state",
                "State",
            ),
        )
    )
    return ", ".join(
        str(value) for value in (city, state) if _has_display_value(value)
    )


def _duplicate_review_display_specs(decision_type: str) -> tuple[dict, ...]:
    if decision_type == "account_duplicate_group_review":
        return (
            {"name": "identity", "label": "Account"},
            {"name": "created_date", "label": "Created date"},
            {"name": "domain", "label": "Domain"},
            {"name": "account_type", "label": "Type"},
            {"name": "owner", "label": "Owner"},
            {"name": "location", "label": "Location"},
        )
    return (
        {"name": "identity", "label": "Person"},
        {"name": "created_date", "label": "Created date"},
        {"name": "person_type", "label": "CRM record"},
        {"name": "email", "label": "Email"},
        {"name": "title", "label": "Title"},
        {"name": "owner", "label": "Owner"},
    )


def _duplicate_review_display_cell(
    name: str,
    *,
    decision_type: str,
    detail: dict,
    ranking: dict,
) -> object:
    if name == "identity":
        return _duplicate_review_identity(decision_type, detail, ranking)
    if name == "domain":
        return _first_display_value(
            (
                detail,
                (
                    "acct_domain_final",
                    "__dr_domain",
                    "domain",
                    "Domain",
                ),
            ),
        )
    if name == "account_type":
        return _first_display_value(
            (detail, ("acct_type", "__dr_account_type", "type", "Type")),
            (ranking, ("type_value",)),
        )
    if name == "person_type":
        return _first_display_value(
            (
                detail,
                (
                    "person_type",
                    "__person_duplicate_resolution_person_type",
                    "object_type",
                ),
            ),
            (ranking, ("person_type", "object_type")),
        )
    if name == "email":
        return _first_display_value(
            (
                detail,
                (
                    "email",
                    "contact_email_final",
                    "__person_duplicate_resolution_email",
                    "Email",
                ),
            ),
        )
    if name == "title":
        return _first_display_value(
            (
                detail,
                (
                    "title",
                    "contact_title",
                    "__person_duplicate_resolution_title",
                    "jobtitle",
                    "Title",
                ),
            ),
        )
    if name == "owner":
        return _first_display_value(
            (ranking, ("owner_display_value",)),
            (
                detail,
                (
                    "acct_owner",
                    "person_owner",
                    "owner_name",
                    "Owner.Name",
                    "owner",
                ),
            ),
        )
    if name == "location":
        return _duplicate_review_location(detail)
    if name == "created_date":
        raw = _first_display_value(
            (
                ranking,
                ("created_date_raw", "created_at", "created_date", "CreatedDate"),
            ),
            (
                detail,
                (
                    "created_date",
                    "CreatedDate",
                    "createdate",
                    "account_created_date",
                    "contact_created_date",
                    "acct_created_date",
                    "__dr_created_at",
                ),
            ),
        )
        if not _has_display_value(raw):
            return ""
        text = str(raw).strip()
        if len(text) >= 10 and text[4] == "-" and text[7] == "-":
            return text[:10]
        return text
    return ""


def _duplicate_review_highlights(
    *,
    decision_type: str,
    ranking: dict,
) -> list[str]:
    highlights = []
    rank = ranking.get("rank")
    if _has_display_value(rank):
        highlights.append(f"Rank {rank}")
    total = ranking.get("total_health_score")
    if _has_display_value(total):
        highlights.append(f"Health score {total}")
    if decision_type == "account_duplicate_group_review":
        if ranking.get("is_customer"):
            highlights.append("Customer record")
        opportunity_count = ranking.get("open_opportunity_count")
        if _has_display_value(opportunity_count):
            highlights.append(f"{opportunity_count} open opportunities")
    else:
        if ranking.get("contact_priority_applied"):
            highlights.append("Contact preferred over Lead")
        if ranking.get("has_usable_email"):
            highlights.append("Usable email")
        if ranking.get("has_account"):
            highlights.append("Linked to an account")
    activity_days = ranking.get("last_activity_age_days")
    if _has_display_value(activity_days):
        highlights.append(f"Activity {activity_days} days ago")
    elif _has_display_value(ranking.get("last_activity_at")):
        highlights.append(f"Last activity {ranking['last_activity_at']}")
    if ranking.get("is_preferred_owner"):
        highlights.append("Preferred owner")
    completeness = ranking.get("completeness_score")
    if _has_display_value(completeness):
        highlights.append(f"Completeness score {completeness}")
    return highlights[:6]


def _duplicate_review_presentation(
    body: dict,
    *,
    decision_type: str,
    submitted_choice: str | None = None,
) -> dict:
    """Build survivor/change/skip radio rows for CRM duplicate group review."""

    member_key = _duplicate_review_member_key(decision_type)
    recommended = str(body.get("recommended_survivor_id") or "")
    selected = str(body.get("selected_survivor_id") or recommended)
    members_frame = body.get("group_members_df") or {}
    member_rows = list(members_frame.get("rows") or [])
    detail_name = (
        "accounts_df"
        if decision_type == "account_duplicate_group_review"
        else "people_df"
    )
    detail_frame = body.get(detail_name) or {}
    detail_id_names = (
        (
            "account_id",
            "acct_id",
            "__dr_account_id",
            "Id",
            "id",
        )
        if decision_type == "account_duplicate_group_review"
        else (
            "person_id",
            "contact_id",
            "lead_id",
            "__person_duplicate_resolution_person_id",
            "Id",
            "id",
        )
    )
    detail_by_id = {}
    for row in detail_frame.get("rows") or []:
        detail_id = _first_display_value((row, detail_id_names))
        if _has_display_value(detail_id):
            detail_by_id[str(detail_id)] = row
    ranking_frame = body.get("ranking_df") or {}
    ranking_by_id = {
        str(row.get(member_key)): row
        for row in (ranking_frame.get("rows") or [])
        if _has_display_value(row.get(member_key))
    }

    options = []
    seen: set[str] = set()
    for row in member_rows:
        member_id = str(row.get(member_key) or "").strip()
        if not member_id or member_id in seen:
            continue
        seen.add(member_id)
        detail = detail_by_id.get(member_id) or row
        ranking = ranking_by_id.get(member_id) or {}
        choice_value = f"{DUPLICATE_REVIEW_SURVIVOR_PREFIX}{member_id}"
        options.append(
            {
                "choice_value": choice_value,
                "member_id": member_id,
                "kind": "survivor",
                "label": "Keep as survivor",
                "recommended": member_id == recommended,
                "selected": (
                    submitted_choice == choice_value
                    if submitted_choice is not None
                    else member_id == selected
                ),
                "synthetic": False,
                "detail": detail,
                "ranking": ranking,
                "highlights": _duplicate_review_highlights(
                    decision_type=decision_type,
                    ranking=ranking,
                ),
            }
        )
    for value in (recommended, selected):
        if value and value not in seen:
            seen.add(value)
            choice_value = f"{DUPLICATE_REVIEW_SURVIVOR_PREFIX}{value}"
            options.append(
                {
                    "choice_value": choice_value,
                    "member_id": value,
                    "kind": "survivor",
                    "label": "Keep as survivor",
                    "recommended": value == recommended,
                    "selected": (
                        submitted_choice == choice_value
                        if submitted_choice is not None
                        else value == selected
                    ),
                    "synthetic": False,
                    "detail": detail_by_id.get(value) or {},
                    "ranking": ranking_by_id.get(value) or {},
                    "highlights": _duplicate_review_highlights(
                        decision_type=decision_type,
                        ranking=ranking_by_id.get(value) or {},
                    ),
                }
            )

    display_columns = []
    for spec in _duplicate_review_display_specs(decision_type):
        if any(
            _has_display_value(
                _duplicate_review_display_cell(
                    spec["name"],
                    decision_type=decision_type,
                    detail=option["detail"],
                    ranking=option["ranking"],
                )
            )
            for option in options
        ):
            display_columns.append(spec)
    for option in options:
        detail = option.pop("detail")
        ranking = option.pop("ranking")
        option["cells"] = [
            _duplicate_review_display_cell(
                spec["name"],
                decision_type=decision_type,
                detail=detail,
                ranking=ranking,
            )
            for spec in display_columns
        ]

    skip_selected = submitted_choice == DUPLICATE_REVIEW_SKIP_VALUE
    quarantine_selected = submitted_choice == DUPLICATE_REVIEW_QUARANTINE_VALUE
    if submitted_choice is None:
        # Default remains the projected survivor; never auto-select skip.
        skip_selected = False
        quarantine_selected = False

    outcome_rows = list(options)
    outcome_rows.append(
        {
            "choice_value": DUPLICATE_REVIEW_SKIP_VALUE,
            "member_id": "",
            "kind": "skip",
            "label": "Skip this group",
            "recommended": False,
            "selected": skip_selected,
            "synthetic": True,
            "highlights": [],
            "cells": ["Do not merge these records. Recommendation is never approval."]
            + [""] * max(0, len(display_columns) - 1),
        }
    )
    allowed = set(body.get("allowed_actions") or ())
    if "quarantine" in allowed:
        outcome_rows.append(
            {
                "choice_value": DUPLICATE_REVIEW_QUARANTINE_VALUE,
                "member_id": "",
                "kind": "quarantine",
                "label": "Quarantine this group",
                "recommended": False,
                "selected": quarantine_selected,
                "synthetic": True,
                "highlights": [],
                "cells": ["Hold the group for a later specialist review."]
                + [""] * max(0, len(display_columns) - 1),
            }
        )
    return {
        "duplicate_review": True,
        "duplicate_choice_columns": display_columns,
        "duplicate_choice_rows": outcome_rows,
        "duplicate_member_key": member_key,
    }


def _decision_presentation(
    decision: dict | None,
    *,
    submitted_group_choices: dict[str, list[str]] | None = None,
    submitted_duplicate_choice: str | None = None,
    product_key: str = "",
    setup_entity: str = "",
) -> dict:
    if not decision:
        return {
            "tables": [],
            "rows": [],
            "groups": [],
            "survivor_options": [],
            "duplicate_review": False,
            "duplicate_choice_columns": [],
            "duplicate_choice_rows": [],
            "source_columns": [],
        }
    body = decision.get("body") or {}
    decision_type = decision.get("decision_type")
    tables = [
        _dataframe(name, value)
        for name, value in body.items()
        if name.endswith("_df")
        and isinstance(value, dict)
        and not (
            decision_type in GROUPED_SELECTION_DECISION_TYPES and name == "rows_df"
        )
        and not (
            decision_type in DUPLICATE_GROUP_REVIEW_DECISION_TYPES
            and name
            in {
                "group_members_df",
                "accounts_df",
                "people_df",
            }
        )
    ]
    rows_df = body.get("rows_df") or {}
    columns = list(rows_df.get("columns") or [])
    option_column = body.get("option_id_col", "option_id")
    group_column = body.get("group_id_col")
    column_metadata = {
        str(item.get("name")): item
        for item in (body.get("columns") or [])
        if isinstance(item, dict) and item.get("name")
    }
    grouped_columns = _grouped_display_columns(
        decision_type=decision_type,
        columns=columns,
        column_metadata=column_metadata,
        option_column=option_column,
        group_column=group_column,
        product_key=product_key,
        setup_entity=setup_entity,
    )
    if decision_type == "validation_failed":
        visible = _operator_visible_columns(
            decision_type=decision_type,
            columns=columns,
            column_metadata=column_metadata,
            option_column=option_column,
            group_column=group_column,
        )
        display_columns = select_existing_review_columns(
            decision_type=decision_type,
            available=visible,
            product_key=product_key,
            setup_entity=setup_entity,
        )
    else:
        display_columns = columns

    action_rows = []
    for row in rows_df.get("rows") or []:
        option_id = str(row.get(option_column, ""))
        row_group_id = str(row.get(group_column, "")) if group_column else ""
        option_kind = str(row.get("option_kind") or "")
        selected = bool(row.get("selected"))
        if (
            submitted_group_choices is not None
            and row_group_id in submitted_group_choices
            and option_kind in KNOWN_GROUPED_OPTION_KINDS
        ):
            selected = option_id in submitted_group_choices[row_group_id]
        action_rows.append(
            {
                "option_id": option_id,
                "cells": [row.get(column) for column in display_columns],
                "group_id": row_group_id,
                "group_cells": [
                    _display_value(
                        row.get(column["name"]),
                        evidence=(
                            column["name"].endswith("_rules")
                            or column["name"].endswith("_source")
                            or column["name"].endswith("_sources")
                        ),
                    )
                    for column in grouped_columns
                ],
                "context_items": _grouped_context_items(decision_type, row),
                "highlights": _grouped_evidence_highlights(decision_type, row),
                "email": row.get("contact_email_final") or "",
                "selected": selected,
                "recommended": bool(row.get("is_recommended")),
                "option_kind": option_kind,
                "option_label": str(row.get("option_label") or ""),
                "synthetic": option_kind
                not in {
                    "uploaded_row",
                    "candidate",
                },
                "read_only": option_kind not in KNOWN_GROUPED_OPTION_KINDS,
            }
        )

    groups_by_id = {}
    for row in action_rows:
        group_id = row["group_id"] if group_column else "__single_group__"
        if group_id not in groups_by_id:
            group_index = len(groups_by_id)
            groups_by_id[group_id] = {
                "group_id": row["group_id"],
                "input_name": f"selected__{group_index}",
                "title": (
                    f"{GROUPED_SELECTION_TITLES.get(decision_type, 'Match group')} "
                    f"{group_index + 1}"
                ),
                "rows": [],
                "whole_group_options": [],
                "context_items": row["context_items"],
            }
        elif not groups_by_id[group_id]["context_items"] and row["context_items"]:
            groups_by_id[group_id]["context_items"] = row["context_items"]
        destination = (
            "whole_group_options"
            if row["option_kind"] in GROUPED_WHOLE_GROUP_OPTION_KINDS
            else "rows"
        )
        groups_by_id[group_id][destination].append(row)

    member_key = _duplicate_review_member_key(decision_type)
    survivor_options = []
    for row in (body.get("group_members_df") or {}).get("rows") or []:
        value = row.get(member_key)
        if value not in {None, ""}:
            survivor_options.append(str(value))
    for value in (
        body.get("recommended_survivor_id"),
        body.get("selected_survivor_id"),
    ):
        if value and str(value) not in survivor_options:
            survivor_options.append(str(value))
    result = {
        "tables": tables,
        "rows": action_rows,
        "columns": display_columns,
        "source_columns": columns,
        "group_columns": grouped_columns,
        "groups": list(groups_by_id.values()),
        "survivor_options": survivor_options,
        "duplicate_review": False,
        "duplicate_choice_columns": [],
        "duplicate_choice_rows": [],
    }
    if decision_type in DUPLICATE_GROUP_REVIEW_DECISION_TYPES:
        result.update(
            _duplicate_review_presentation(
                body,
                decision_type=str(decision_type),
                submitted_choice=submitted_duplicate_choice,
            )
        )
    return result


@require_GET
def workflow(request, session_id):
    session = owned_session_or_404(request, session_id)
    current = session.active_workflow
    if current is None:
        return redirect("importer:configure", session_id=session.id)

    latest_accepted = (
        session.api_mutations.filter(state=ApiMutation.State.COMPLETED)
        .exclude(mutation_kind="register_upload")
        .order_by("-created_at")
        .first()
    )
    if latest_accepted and isinstance(latest_accepted.response_json, dict):
        receipt = latest_accepted.response_json
        receipt_run = str(receipt.get("run_id") or "").strip()
        projected = (
            session.api_workflows.filter(run_id=receipt_run).first()
            if receipt_run
            else None
        )
        need_refresh = projected is None or projected.revision < int(
            receipt.get("revision", 0) or 0
        )
        if need_refresh:
            try:
                recovered = materialize_stored_receipt(session, latest_accepted)
                if recovered is not None:
                    # Display recovered projection; materialize must not steal
                    # active primary for review/continuation (Phase 3).
                    current = recovered
            except CLIENT_ERRORS as exc:
                _error(request, exc)
        elif (
            projected is not None
            and projected.id != getattr(current, "id", None)
            and latest_accepted.mutation_kind
            in {
                "authorize_effect",
                "bind_decision_set_handoff",
                "finalize_duplicate_merge_plan_handoff",
            }
        ):
            # R6: show terminal/effect continuation after authorize without
            # changing session.active_workflow away from the analysis primary.
            current = projected
    if _merge_continuation_post_authorize(session):
        continuation = _duplicate_continuation_workflow(session)
        if continuation is None:
            continuation = (
                session.api_workflows.filter(role=ApiWorkflow.Role.CONTINUATION)
                .order_by("-updated_at")
                .first()
            )
        if continuation is not None:
            current = continuation
    try:
        current = refresh_workflow(session, current)
        if current.status == "succeeded":
            store_artifacts(current, EasyImportsApiClient().artifacts(current.run_id))
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        current.refresh_from_db()

    review_preparing = False
    review_child = None
    source_for_first_window = current
    if _should_display_review_child(current, latest_accepted):
        review_child = _child_review_workflow(session, current, latest_accepted)
        if (
            review_child is not None
            and review_child.pk != current.pk
        ):
            try:
                review_child = refresh_workflow(session, review_child)
            except CLIENT_ERRORS as exc:
                _error(request, exc)
                review_child.refresh_from_db()
        review_preparing = _review_is_preparing(review_child)
        if review_child is not None:
            # GFC-6 still displays the child for preparing / needs_decision
            # without replacing session.active_workflow — except when
            # auto-merge is on. The child's sequential card is the first
            # pending group and does not apply _manual_pending_group_ids, so
            # a score-T group would otherwise surface as a one-group review.
            if _auto_merge_configured(session):
                if not _merge_continuation_post_authorize(session):
                    journey_redirect = _journey_review_redirect(session)
                    if journey_redirect is not None:
                        return journey_redirect
                if review_preparing:
                    current = review_child
            else:
                current = review_child
    review_handoff_refreshing = (
        not review_preparing
        and review_child is None
        and _awaiting_first_review_window(source_for_first_window)
    )

    projection = current.projection
    status_known = projection.get("status") in WORKFLOW_STATUSES
    decision = projection.get("decision")
    decision_known = not decision or decision.get("decision_type") in DECISION_TYPES
    pending = [
        attach_pending_copy(mutation)
        for mutation in session.api_mutations.filter(
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN]
        ).exclude(mutation_kind="register_upload")
    ]
    rejected = list(
        session.api_mutations.filter(
            state=ApiMutation.State.REJECTED,
            acknowledged_at__isnull=True,
        ).exclude(mutation_kind="register_upload")
    )
    command_receipts = [
        _command_receipt_presentation(mutation)
        for mutation in session.api_mutations.filter(
            state__in=[ApiMutation.State.COMPLETED, ApiMutation.State.REJECTED]
        )
        .exclude(mutation_kind="register_upload")
        .order_by("-created_at")[:20]
    ]
    awaiting_projection_refresh = False
    if latest_accepted and isinstance(latest_accepted.response_json, dict):
        receipt = latest_accepted.response_json
        projected = session.api_workflows.filter(
            run_id=receipt.get("run_id", "")
        ).first()
        awaiting_projection_refresh = projected is None or projected.revision < int(
            receipt.get("revision", 0)
        )

    retry_tokens = {
        str(item.id): _retry_token(request, session, item) for item in pending
    }
    rejection_tokens = {
        str(item.id): _ack_token(request, session, item) for item in rejected
    }
    tokens = _workflow_tokens(request, session, current)
    decision_set_handoff = None
    if current.role == ApiWorkflow.Role.REVIEW and current.source_workflow_id:
        source_handoff = (
            current.source_workflow.projection.get("review_handoff") or {}
        ).get("handoff_id", "")
        tokens["decision_set"] = _token_for_workflow_action(
            request,
            session,
            current,
            action_kind="decision_set",
            action_id=source_handoff,
        )
        handoff_mutation = (
            session.api_mutations.filter(
                workflow=current,
                mutation_kind="create_decision_set_handoff",
                state=ApiMutation.State.COMPLETED,
            )
            .order_by("-created_at")
            .first()
        )
        if handoff_mutation:
            decision_set_handoff = (
                (handoff_mutation.response_json or {}).get("result") or {}
            ).get("decision_set_handoff")
        if decision_set_handoff:
            tokens["continue"] = _token_for_workflow_action(
                request,
                session,
                current,
                action_kind="continue",
                action_id=decision_set_handoff["handoff_id"],
            )

    submitted_group_choices = None
    submitted_duplicate_choice = None
    preserved = request.session.pop(GROUPED_SELECTION_FLASH_KEY, None)
    if (
        isinstance(preserved, dict)
        and str(preserved.get("session_id", "")) == str(session.id)
        and decision
        and str(preserved.get("decision_id", ""))
        == str(decision.get("decision_id", ""))
    ):
        if isinstance(preserved.get("choices"), dict):
            submitted_group_choices = {
                str(group_id): [str(option_id) for option_id in option_ids]
                for group_id, option_ids in preserved["choices"].items()
                if isinstance(option_ids, list)
            }
        if preserved.get("duplicate_choice") is not None:
            submitted_duplicate_choice = str(preserved.get("duplicate_choice") or "")
    presentation = _decision_presentation(
        decision,
        submitted_group_choices=submitted_group_choices,
        submitted_duplicate_choice=submitted_duplicate_choice,
        product_key=session.product_key,
        setup_entity=session.setup_entity,
    )
    artifacts = []
    customer_artifacts = []
    for item in current.artifacts.all():
        metadata = item.metadata
        filename = metadata.get("filename", "")
        display_name, description = ARTIFACT_PRESENTATION.get(
            filename,
            (
                _humanize_identifier(str(filename).removesuffix(".csv")),
                "A result file produced by this import.",
            ),
        )
        row_count = metadata.get("row_count")
        artifact = {
            "item": item,
            "metadata": metadata,
            "display_name": display_name,
            "description": description,
            "row_label": f"{row_count} {'row' if row_count == 1 else 'rows'}",
            "downloadable": bool(metadata.get("download_url"))
            and metadata.get("availability") in {"preview", "delivered"},
        }
        artifacts.append(artifact)
        if filename in PRIMARY_ARTIFACT_FILENAMES or (
            filename in ACTIONABLE_ARTIFACT_FILENAMES
            and isinstance(row_count, int)
            and row_count > 0
        ):
            customer_artifacts.append(artifact)
    effect_intent = projection.get("effect_intent")
    terminal = projection.get("terminal_evidence")
    cm_policy_details = _campaign_member_policy_from_create(session)
    decision_body = (projection.get("decision") or {}).get("body") or {}
    duplicate_review_progress = _duplicate_review_progress_presentation(
        projection.get("review_progress"),
        decision_body if presentation.get("duplicate_review") else None,
    )
    # Standalone CRM duplicate journeys freeze this product on the Django
    # session; nested review under list-import keeps the import product key.
    is_duplicate_resolution = (
        session.product_key == DUPLICATE_RESOLUTION_PRODUCT_KEY
    )
    run_output_package = None
    package_workflow = _package_target_workflow(session, current)
    if (
        package_workflow is not None
        and package_workflow.id != current.id
    ):
        try:
            package_workflow = refresh_workflow(session, package_workflow)
        except CLIENT_ERRORS as exc:
            _error(request, exc)
            package_workflow = _duplicate_continuation_workflow(session)
    run_output_package_eligible = bool(
        package_workflow is not None
        and _run_output_package_eligible(package_workflow)
    )
    if run_output_package_eligible:
        try:
            run_output_package = _resolve_run_output_package(
                session, package_workflow
            )
        except CLIENT_ERRORS as exc:
            # Non-fatal: still offer create CTA; surface message if useful.
            _error(request, exc)
    return render(
        request,
        "importer/workflow.html",
        {
            "session": session,
            "workflow": current,
            "projection": projection,
            "tokens": tokens,
            "decision_set_handoff": decision_set_handoff,
            "decision_tables": presentation["tables"],
            "decision_rows": presentation["rows"],
            "decision_columns": presentation.get("columns", []),
            "decision_source_columns": presentation.get("source_columns", []),
            "decision_group_columns": presentation.get("group_columns", []),
            "decision_groups": presentation.get("groups", []),
            "survivor_options": presentation["survivor_options"],
            "duplicate_review": presentation.get("duplicate_review", False),
            "duplicate_choice_columns": presentation.get(
                "duplicate_choice_columns", []
            ),
            "duplicate_choice_rows": presentation.get("duplicate_choice_rows", []),
            "field_merge_display": field_merge_display(
                decision_body.get("field_merge_plan")
                if isinstance(decision_body, dict)
                else None
            ),
            "duplicate_review_progress": duplicate_review_progress,
            "is_duplicate_resolution": is_duplicate_resolution,
            "status_known": status_known,
            "decision_known": decision_known,
            "pending_mutations": pending,
            "rejected_mutations": rejected,
            "command_receipts": command_receipts,
            "retry_tokens": retry_tokens,
            "rejection_tokens": rejection_tokens,
            "customer_artifacts": customer_artifacts,
            "actions_blocked": bool(pending)
            or bool(rejected)
            or not status_known
            or not decision_known
            or awaiting_projection_refresh,
            "awaiting_projection_refresh": awaiting_projection_refresh,
            "status_presentation": (
                {
                    "heading": "Preparing your review",
                    "description": _REVIEW_PREPARATION_COPY,
                }
                if review_preparing
                else _workflow_status_presentation(current.status)
            ),
            "review_preparing": review_preparing,
            "review_handoff_refreshing": review_handoff_refreshing,
            "review_preparation_copy": (
                _REVIEW_PREPARATION_COPY if review_preparing else None
            ),
            "effect_presentation": (
                _effect_presentation(effect_intent, projection.get("summary"))
                if effect_intent
                else None
            ),
            # Phase 4D rem: digest-bound Account provision candidate review.
            "effect_review": projection.get("effect_review"),
            "terminal": terminal,
            "terminal_presentation": _terminal_presentation(terminal),
            "merge_execution_presentation": _merge_execution_presentation(
                projection,
                session=session,
            ),
            "sanitized_terminal": (
                _sanitize_operator_visible_mapping(terminal) if terminal else None
            ),
            "sanitized_projection": _sanitize_operator_visible_mapping(projection),
            "artifacts": artifacts,
            "cm_policy_details": cm_policy_details,
            "run_output_package_eligible": run_output_package_eligible,
            "run_output_package": run_output_package,
            "step": 4,
        },
    )


def _campaign_member_policy_from_create(session: ImportSession) -> dict | None:
    """Surface frozen CM policy Ids in technical details (no secrets)."""

    from .campaign_member_setup import resolution_technical_rows

    mutation = (
        session.api_mutations.filter(
            mutation_kind="create_workflow",
            state=ApiMutation.State.COMPLETED,
        )
        .order_by("-created_at")
        .first()
    )
    if mutation is None or not isinstance(mutation.request_json, dict):
        return None
    policy = mutation.request_json.get("campaign_member_policy")
    if not isinstance(policy, dict):
        modes = mutation.request_json.get("maximum_modes") or {}
        cm_mode = (
            modes.get("campaign_member_writes")
            if isinstance(modes, dict)
            else "disabled"
        )
        return {
            "mode": cm_mode or "disabled",
            "policy_version": "",
            "default_campaign_binding": "",
            "default_desired_status": "",
            "campaign_id_column": "",
            "campaign_match_column": "",
            "member_status_column": "",
            "resolutions": [],
        }
    modes = mutation.request_json.get("maximum_modes") or {}
    cm_mode = (
        modes.get("campaign_member_writes") if isinstance(modes, dict) else "disabled"
    )
    return {
        "mode": cm_mode or "disabled",
        "policy_version": str(policy.get("policy_version") or ""),
        "default_campaign_binding": str(policy.get("default_campaign_binding") or ""),
        "default_desired_status": str(policy.get("default_desired_status") or ""),
        "campaign_id_column": str(policy.get("campaign_id_column") or ""),
        "campaign_match_column": str(policy.get("campaign_match_column") or ""),
        "member_status_column": str(policy.get("member_status_column") or ""),
        "resolutions": resolution_technical_rows(policy),
    }


def _begin_workflow_action(
    request,
    session_id,
    *,
    action_kind: str,
    mutation_kind: str,
):
    session = owned_session_or_404(request, session_id)
    claims, payload_digest = _decode_submission(request, session, action_kind)
    replay = _replay_if_recorded(
        request,
        session=session,
        claims=claims,
        mutation_kind=mutation_kind,
        form_payload_digest=payload_digest,
    )
    if replay is not None:
        return session, None, claims, payload_digest, replay
    workflow = session.active_workflow
    if workflow is None:
        raise Http404("Workflow not found.")
    return session, workflow, claims, payload_digest, None


def _validate_current_action_token(
    request,
    *,
    session: ImportSession,
    workflow: ApiWorkflow,
    claims: dict,
    action_kind: str,
    action_id: str = "",
    group_id: str = "",
) -> tuple[str, int]:
    identity = _workflow_action_identity(workflow, action_kind, action_id, group_id)
    generation = action_generation(session, identity)
    validate_form_token(
        request.POST.get("form_token", ""),
        owner_id=owner_id_for_request(request),
        session=session,
        workflow=workflow,
        action_kind=action_kind,
        action_id=action_id,
        logical_action_identity=identity,
        logical_action_generation=generation,
    )
    return identity, generation


@require_POST
def submit_decision(request, session_id):
    preserved_group_choices = None
    preserved_duplicate_choice = None
    preserved_decision_id = ""
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="decision",
            mutation_kind="submit_decision",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        workflow = _workflow_for_displayed_decision(session, workflow)
        decision = workflow.projection.get("decision") or {}
        preserved_decision_id = str(decision.get("decision_id", ""))
        dtype = decision.get("decision_type")
        body = decision.get("body") or {}
        if dtype not in DECISION_TYPES:
            raise FormTokenError(
                "This decision type is not supported by this web version."
            )
        group_id = str(body.get("duplicate_group_id", ""))
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="decision",
            action_id=decision["decision_id"],
            group_id=group_id,
        )

        action = request.POST.get("action", "")
        body_builder = None
        command = None
        if dtype == "validation_failed":
            if action not in body.get("actions", []):
                raise FormTokenError("Invalid decision action.")
            valid_options = {
                str(row.get(body["option_id_col"])) for row in body["rows_df"]["rows"]
            }
            rows = []
            for option_id in sorted(valid_options):
                value = request.POST.get(f"email__{option_id}")
                if value is not None:
                    rows.append(
                        {
                            "option_id": option_id,
                            "changes": {"contact_email_final": value or None},
                        }
                    )
            command = {
                "expected_revision": workflow.revision,
                "decision_id": decision["decision_id"],
                "decision_type": dtype,
                "response": {"action": action, "rows": rows},
            }
        elif dtype in GROUPED_SELECTION_DECISION_TYPES:
            valid_rows = body["rows_df"]["rows"]
            option_col = body["option_id_col"]
            valid_options = {str(row.get(option_col)) for row in valid_rows}
            group_col = body.get("group_id_col")
            grouped_options = {}
            for row in valid_rows:
                stored_group = str(row.get(group_col, "")) if group_col else ""
                group_options = grouped_options.setdefault(stored_group, set())
                if str(row.get("option_kind") or "") in KNOWN_GROUPED_OPTION_KINDS:
                    group_options.add(str(row.get(option_col)))

            submitted_groups = []
            preserved_group_choices = {}
            for group_index, (group_id, options) in enumerate(grouped_options.items()):
                submitted = request.POST.getlist(f"selected__{group_index}")
                preserved_group_choices[group_id] = list(submitted)
                if len(submitted) != 1:
                    raise FormTokenError(
                        "Exactly one option must be selected in each group."
                    )
                if submitted[0] not in options:
                    raise FormTokenError(
                        "Submitted options are not in the stored decision group."
                    )
                submitted_groups.append(
                    {
                        "group_id": group_id,
                        "selected_option_id": submitted[0],
                    }
                )
            command = {
                "expected_revision": workflow.revision,
                "decision_id": decision["decision_id"],
                "decision_type": dtype,
                "response": {
                    "action": "submit_selections",
                    "groups": submitted_groups,
                },
            }
        elif dtype in DUPLICATE_GROUP_REVIEW_DECISION_TYPES:
            review_progress = workflow.projection.get("review_progress") or {}
            if action == "finish_for_now":
                if not review_progress.get("finish_for_now_available"):
                    raise FormTokenError(
                        "Finish for now is not available for this review."
                    )
                disposition = (
                    request.POST.get("remaining_group_disposition") or ""
                ).strip()
                if disposition not in {"abandon", "export"}:
                    raise FormTokenError(
                        "Choose whether to abandon or export remaining groups."
                    )
                operator = freeze_operator_label(session)

                def build_duplicate_command(first_submitted_at):
                    return validate_decision_command(
                        {
                            "expected_revision": workflow.revision,
                            "decision_id": decision["decision_id"],
                            "decision_type": dtype,
                            "response": {
                                "duplicate_group_id": body["duplicate_group_id"],
                                "group_revision": body["group_revision"],
                                "action": "finish_for_now",
                                "selected_survivor_id": (
                                    body.get("selected_survivor_id")
                                    or body.get("recommended_survivor_id")
                                ),
                                "decided_by": operator.operator_label,
                                "decided_at": first_submitted_at.isoformat(),
                                "advanced_review": False,
                                "remaining_group_disposition": disposition,
                            },
                        }
                    )

                body_builder = build_duplicate_command
            else:
                member_key = _duplicate_review_member_key(dtype)
                members = (body.get("group_members_df") or {}).get("rows") or []
                valid_survivors = {
                    str(row.get(member_key))
                    for row in members
                    if row.get(member_key) not in {None, ""}
                }
                valid_survivors.update(
                    str(value)
                    for value in (
                        body.get("recommended_survivor_id"),
                        body.get("selected_survivor_id"),
                    )
                    if value
                )
                projected_survivor = str(
                    body.get("selected_survivor_id")
                    or body.get("recommended_survivor_id")
                    or ""
                )
                choice = (request.POST.get("duplicate_choice") or "").strip()
                # Preserve rejected submissions so the operator keeps their choice.
                preserved_duplicate_choice = choice
                if choice == DUPLICATE_REVIEW_SKIP_VALUE:
                    if "decline" not in (body.get("allowed_actions") or []):
                        raise FormTokenError("Skip is not allowed for this group.")
                    action = "decline"
                    survivor = projected_survivor or None
                elif choice == DUPLICATE_REVIEW_QUARANTINE_VALUE:
                    if "quarantine" not in (body.get("allowed_actions") or []):
                        raise FormTokenError(
                            "Quarantine is not allowed for this group."
                        )
                    action = "quarantine"
                    survivor = projected_survivor or None
                elif choice.startswith(DUPLICATE_REVIEW_SURVIVOR_PREFIX):
                    survivor = choice[len(DUPLICATE_REVIEW_SURVIVOR_PREFIX) :]
                    if survivor not in valid_survivors:
                        raise FormTokenError(
                            "Selected survivor is not in the stored duplicate group."
                        )
                    if survivor == projected_survivor:
                        action = "approve"
                    else:
                        action = "override_survivor"
                else:
                    # Backward-compatible dual-dropdown posts from older templates.
                    if action not in body.get("allowed_actions", []):
                        raise FormTokenError(
                            "Choose a survivor, skip this group, or quarantine it."
                        )
                    survivor = request.POST.get("selected_survivor_id") or None
                    if survivor and survivor not in valid_survivors:
                        raise FormTokenError(
                            "Selected survivor is not in the stored duplicate group."
                        )
                operator = freeze_operator_label(session)

                def build_duplicate_command(first_submitted_at):
                    return validate_decision_command(
                        {
                            "expected_revision": workflow.revision,
                            "decision_id": decision["decision_id"],
                            "decision_type": dtype,
                            "response": {
                                "duplicate_group_id": body["duplicate_group_id"],
                                "group_revision": body["group_revision"],
                                "action": action,
                                "selected_survivor_id": survivor,
                                "decided_by": operator.operator_label,
                                "decided_at": first_submitted_at.isoformat(),
                                "advanced_review": bool(
                                    body.get("advanced_review_required")
                                ),
                            },
                        }
                    )

                body_builder = build_duplicate_command
        else:
            if action not in body.get("allowed_actions", []):
                raise FormTokenError("Invalid decision action.")
            raise FormTokenError(
                "This decision type is not supported by this web version."
            )

        if command is not None:
            command = validate_decision_command(command)
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="submit_decision",
            route=f"/v1/workflows/{workflow.run_id}/decisions",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=command,
            body_builder=body_builder,
        )
        _record_command_outcome(request, session, result)
        if result.mutation.state == ApiMutation.State.REJECTED and (
            preserved_group_choices is not None or preserved_duplicate_choice is not None
        ):
            flash = {
                "session_id": str(session.id),
                "decision_id": preserved_decision_id,
            }
            if preserved_group_choices is not None:
                flash["choices"] = preserved_group_choices
            if preserved_duplicate_choice is not None:
                flash["duplicate_choice"] = preserved_duplicate_choice
            request.session[GROUPED_SELECTION_FLASH_KEY] = flash
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        if (
            preserved_group_choices is not None
            or preserved_duplicate_choice is not None
        ):
            flash = {
                "session_id": str(session_id),
                "decision_id": preserved_decision_id,
            }
            if preserved_group_choices is not None:
                flash["choices"] = preserved_group_choices
            if preserved_duplicate_choice is not None:
                flash["duplicate_choice"] = preserved_duplicate_choice
            request.session[GROUPED_SELECTION_FLASH_KEY] = flash
    return redirect("importer:workflow", session_id=session_id)


@require_POST
def authorize_effect(request, session_id):
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="effect",
            mutation_kind="authorize_effect",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        intent = workflow.projection.get("effect_intent") or {}
        action_id = intent.get("intent_id", "")
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="effect",
            action_id=action_id,
        )
        selected = request.POST.get("selected_mode", "")
        if (
            selected not in intent.get("supported_modes", [])
            or selected not in AUTHORIZATION_MODES
        ):
            raise FormTokenError("Selected mode is not allowed by the stored intent.")
        command = {"expected_revision": workflow.revision, "selected_mode": selected}
        for key in (
            "intent_id",
            "track",
            "maximum_mode",
            "target_provider_id",
            "target_fingerprint",
            "work_digest",
            "confirmation",
        ):
            command[key] = intent[key]
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="authorize_effect",
            route=f"/v1/workflows/{workflow.run_id}/effect-authorizations",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_effect_authorization(command),
        )
        _record_command_outcome(request, session, result)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_POST
def continue_duplicate_execution(request, session_id):
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="continue_duplicate_execution",
            mutation_kind="continue_duplicate_execution",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        if workflow.status != "awaiting_execution_continuation" and not (
            workflow.projection or {}
        ).get("continuation_available"):
            raise FormTokenError(
                "This workflow is not awaiting duplicate execution continuation."
            )
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="continue_duplicate_execution",
        )
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="continue_duplicate_execution",
            route=(
                f"/v1/workflows/{workflow.run_id}/duplicate-execution-continuations"
            ),
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_duplicate_execution_cycle(
                {
                    "expected_revision": workflow.revision,
                    "expected_continuation_generation": int(
                        (workflow.projection or {}).get("continuation_generation") or 0
                    ),
                }
            ),
        )
        _record_command_outcome(request, session, result)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_POST
def finish_duplicate_execution(request, session_id):
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="finish_duplicate_execution",
            mutation_kind="finish_duplicate_execution",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        if workflow.status != "awaiting_execution_continuation" and not (
            workflow.projection or {}
        ).get("continuation_available"):
            raise FormTokenError(
                "This workflow is not awaiting duplicate execution continuation."
            )
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="finish_duplicate_execution",
        )
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="finish_duplicate_execution",
            route=(
                f"/v1/workflows/{workflow.run_id}/duplicate-execution-completion"
            ),
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_duplicate_execution_cycle(
                {
                    "expected_revision": workflow.revision,
                    "expected_continuation_generation": int(
                        (workflow.projection or {}).get("continuation_generation") or 0
                    ),
                }
            ),
        )
        _record_command_outcome(request, session, result)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_GET
def duplicate_execution_exceptions(request, session_id):
    """More-pages GET via the generated client. Template context holds ≤50 rows."""

    session = owned_session_or_404(request, session_id)
    workflow = session.active_workflow
    continuation = _duplicate_continuation_workflow(session)
    if continuation is not None:
        workflow = continuation
    if workflow is None:
        raise Http404("No duplicate-execution workflow is available.")
    cursor = str(request.GET.get("cursor") or "").strip() or None
    owner_session = owner_session_for_import_session(session)
    page = {"exceptions": [], "aggregates": {}, "next_cursor": None, "run_id": ""}
    try:
        workflow = refresh_workflow(session, workflow)
        page = EasyImportsApiClient().duplicate_execution_exceptions(
            workflow.run_id,
            owner_session=owner_session or "",
            cursor=cursor,
            limit=50,
        )
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    exceptions = []
    for item in page.get("exceptions") or ():
        if not isinstance(item, dict):
            continue
        exceptions.append(item)
        if len(exceptions) >= 50:
            break
    next_cursor = str(page.get("next_cursor") or "").strip()
    more_url = ""
    if next_cursor:
        more_url = (
            reverse(
                "importer:duplicate_execution_exceptions",
                kwargs={"session_id": session.id},
            )
            + "?cursor="
            + next_cursor
        )
    return render(
        request,
        "importer/duplicate_execution_exceptions.html",
        {
            "session": session,
            "exceptions": exceptions,
            "aggregates": page.get("aggregates") or {},
            "more_url": more_url,
            "outcome_generation": page.get("outcome_generation") or 0,
        },
    )


@require_POST
def resume_effect(request, session_id):
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="resume",
            mutation_kind="resume_effect",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        if workflow.status not in {"paused_unknown", "paused_verification"}:
            raise FormTokenError("This workflow has no resumable effect checkpoint.")
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="resume",
        )
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="resume_effect",
            route=f"/v1/workflows/{workflow.run_id}/effect-resumptions",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_effect_resume({"expected_revision": workflow.revision}),
        )
        _record_command_outcome(request, session, result)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_POST
def start_review(request, session_id):
    try:
        session, workflow, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="start_review",
            mutation_kind="start_review_workflow",
        )
        if replay is not None:
            if _auto_merge_configured(session):
                _bind_review_run_from_existing_child(
                    session,
                    handoff_id=str(
                        (workflow.projection.get("review_handoff") or {}).get(
                            "handoff_id"
                        )
                        if workflow is not None
                        and isinstance(workflow.projection, dict)
                        else ""
                    ),
                )
                journey_redirect = _journey_review_redirect(session)
                if journey_redirect is not None:
                    return journey_redirect
            return redirect("importer:workflow", session_id=session_id)
        assert workflow is not None
        handoff = workflow.projection.get("review_handoff") or {}
        action_id = handoff.get("handoff_id", "")
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=workflow,
            claims=claims,
            action_kind="start_review",
            action_id=action_id,
        )
        freeze_operator_label(session)
        result = dispatch_command(
            session=session,
            workflow=workflow,
            form_instance=claims["form_instance"],
            mutation_kind="start_review_workflow",
            route=f"/v1/workflows/{workflow.run_id}/review-workflows",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_start_review({"review_handoff_id": action_id}),
        )
        _record_command_outcome(request, session, result)
        if _auto_merge_configured(session):
            _bind_review_run_from_existing_child(session, handoff_id=action_id)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        return redirect("importer:workflow", session_id=session_id)
    if _auto_merge_configured(session):
        journey_redirect = _journey_review_redirect(session)
        if journey_redirect is not None:
            return journey_redirect
    return redirect("importer:workflow", session_id=session_id)


def _closed_step_unavailable(request, session: ImportSession):
    """Friendly closed page when a merge/review control is past its state."""

    return render(
        request,
        "importer/closed_error.html",
        {
            "heading": "This step isn't available",
            "lead": (
                "That action is no longer available. Return to your import to "
                "continue."
            ),
            "session": session,
        },
        status=410,
    )


@require_POST
def create_decision_set(request, session_id):
    try:
        session, review, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="decision_set",
            mutation_kind="create_decision_set_handoff",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        if (
            review is None
            or review.role != ApiWorkflow.Role.REVIEW
            or review.source_workflow is None
        ):
            return _closed_step_unavailable(request, session)
        source = review.source_workflow
        handoff = source.projection.get("review_handoff") or {}
        action_id = handoff.get("handoff_id", "")
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=review,
            claims=claims,
            action_kind="decision_set",
            action_id=action_id,
        )
        command = validate_decision_set_handoff(
            {
                "source_run_id": source.run_id,
                "review_handoff_id": action_id,
                "expected_revision": review.revision,
            }
        )
        result = dispatch_command(
            session=session,
            workflow=review,
            form_instance=claims["form_instance"],
            mutation_kind="create_decision_set_handoff",
            route=f"/v1/workflows/{review.run_id}/decision-set-handoffs",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=command,
        )
        _record_command_outcome(request, session, result)
        if result.mutation.state == ApiMutation.State.COMPLETED:
            messages.success(
                request,
                "The decision-set handoff is frozen in the command receipt.",
            )
    except Http404:
        session = owned_session_or_404(request, session_id)
        return _closed_step_unavailable(request, session)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_POST
def continue_source(request, session_id):
    try:
        session, review, claims, payload_digest, replay = _begin_workflow_action(
            request,
            session_id,
            action_kind="continue",
            mutation_kind="bind_decision_set_handoff",
        )
        if replay is not None:
            return redirect("importer:workflow", session_id=session_id)
        if review is None or review.source_workflow is None:
            return _closed_step_unavailable(request, session)
        mutation = (
            session.api_mutations.filter(
                workflow=review,
                mutation_kind="create_decision_set_handoff",
                state=ApiMutation.State.COMPLETED,
            )
            .order_by("-created_at")
            .first()
        )
        handoff = (
            ((mutation.response_json or {}).get("result") or {}).get(
                "decision_set_handoff"
            )
            if mutation
            else None
        )
        if not handoff:
            return _closed_step_unavailable(request, session)
        source = review.source_workflow
        identity, generation = _validate_current_action_token(
            request,
            session=session,
            workflow=review,
            claims=claims,
            action_kind="continue",
            action_id=handoff["handoff_id"],
        )
        result = dispatch_command(
            session=session,
            workflow=source,
            form_instance=claims["form_instance"],
            mutation_kind="bind_decision_set_handoff",
            route=f"/v1/workflows/{source.run_id}/continuations",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=payload_digest,
            body=validate_decision_set_binding(
                {
                    "review_run_id": review.run_id,
                    "decision_set_handoff_id": handoff["handoff_id"],
                }
            ),
        )
        _record_command_outcome(request, session, result)
    except Http404:
        session = owned_session_or_404(request, session_id)
        return _closed_step_unavailable(request, session)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
    return redirect("importer:workflow", session_id=session_id)


@require_GET
def artifact(request, session_id, run_id, artifact_id):
    session = owned_session_or_404(request, session_id)
    item = (
        ApiArtifact.objects.filter(
            workflow__session=session,
            workflow__run_id=run_id,
            artifact_id=artifact_id,
        )
        .select_related("workflow")
        .first()
    )
    if item is None:
        raise Http404("Artifact not found.")
    metadata = item.metadata
    download_url = metadata.get("download_url")
    expected_digest = metadata.get("content_digest", "")
    if (
        not download_url
        or metadata.get("availability") not in {"preview", "delivered"}
        or not expected_digest
    ):
        raise Http404("Artifact is not downloadable.")

    temporary = tempfile.TemporaryFile()
    upstream = None
    digest = sha256()
    byte_count = 0
    try:
        upstream = EasyImportsApiClient().stream_artifact(
            download_url, run_id=run_id, artifact_id=artifact_id
        )
        expected_etag = f'"sha256:{expected_digest}"'
        upstream_etag = upstream.headers.get("ETag", "")
        if upstream_etag != expected_etag:
            raise ApiConsistencyError(
                "Artifact ETag does not agree with its stored digest."
            )
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            temporary.write(chunk)
            digest.update(chunk)
            byte_count += len(chunk)
        if digest.hexdigest() != expected_digest:
            raise ApiConsistencyError("Artifact content digest mismatch.")
        expected_bytes = metadata.get("byte_count")
        if expected_bytes is not None and byte_count != int(expected_bytes):
            raise ApiConsistencyError("Artifact byte count mismatch.")
        temporary.seek(0)
        response = FileResponse(
            temporary,
            as_attachment=True,
            filename=Path(metadata["filename"]).name,
            content_type=upstream.headers.get(
                "Content-Type", "application/octet-stream"
            ),
        )
        response["ETag"] = upstream_etag
        response["Content-Length"] = str(byte_count)
        return response
    except (*CLIENT_ERRORS, KeyError, TypeError, ValueError, OSError) as exc:
        temporary.close()
        raise Http404(str(exc)) from exc
    finally:
        if upstream is not None:
            upstream.close()


@require_POST
def create_run_output_package(request, session_id):
    """Journaled package create (freeze §5.3); redirects to content proxy on success."""

    session = owned_session_or_404(request, session_id)
    workflow = session.active_workflow
    if workflow is None:
        return redirect("importer:configure", session_id=session.id)
    package_workflow = _package_target_workflow(session, workflow)
    if package_workflow is None or not _run_output_package_eligible(package_workflow):
        messages.error(
            request,
            "A results package is only available after a completed import run.",
        )
        return redirect("importer:workflow", session_id=session.id)
    workflow = package_workflow

    action_id = f"{workflow.run_id}:{workflow.revision}"
    identity = _run_output_package_action_identity(workflow)
    form_token = str(request.POST.get("form_token") or "")
    owner_session = owner_session_for_import_session(session)
    if not owner_session:
        messages.error(request, "This session has no owner binding for package download.")
        return redirect("importer:workflow", session_id=session.id)

    try:
        generation = run_output_package_create_generation(session, workflow)
        claims = validate_form_token(
            form_token,
            owner_id=owner_id_for_request(request),
            session=session,
            workflow=workflow,
            action_kind="create_run_output_package",
            action_id=action_id,
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
    except FormTokenError as exc:
        messages.error(request, str(exc))
        return redirect("importer:workflow", session_id=session.id)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        return redirect("importer:workflow", session_id=session.id)

    request_json = {
        "layout_version": _package_layout_for_session(session),
        "owner_session": owner_session,
    }
    try:
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=claims["form_instance"],
            mutation_kind="create_run_output_package",
            route=f"/v1/workflows/{workflow.run_id}/run-output-packages",
            logical_action_identity=identity,
            logical_action_generation=generation,
            request_json=request_json,
            workflow=workflow,
            resource_identity=workflow.run_id,
        )
        result = EasyImportsApiClient().dispatch(mutation)
    except CLIENT_ERRORS as exc:
        _error(request, exc)
        return redirect("importer:workflow", session_id=session.id)

    result.mutation.refresh_from_db()
    if (
        result.mutation.state == ApiMutation.State.PENDING
        or result.mutation.operation_in_progress
    ):
        messages.info(
            request,
            "EasyImports is preparing the results package. "
            "This page will update automatically.",
        )
        return redirect("importer:workflow", session_id=session.id)

    if result.mutation.state != ApiMutation.State.COMPLETED:
        messages.error(
            request,
            result.mutation.error_message
            or "The results package could not be prepared.",
        )
        return redirect("importer:workflow", session_id=session.id)

    package = result.response if isinstance(result.response, dict) else {}
    package_id = str(package.get("package_id") or "").strip()
    if not package_id or not _package_matches_workflow(
        package,
        workflow,
        expected_layout=_package_layout_for_session(session),
    ):
        messages.error(request, "The results package response was incomplete.")
        return redirect("importer:workflow", session_id=session.id)
    return redirect(
        "importer:run_output_package_content",
        session_id=session.id,
        package_id=package_id,
    )


@require_GET
def run_output_package_content(request, session_id, package_id):
    """Django content proxy: stream ZIP with digest/byte integrity (freeze §5.8)."""

    session = owned_session_or_404(request, session_id)
    package_id = str(package_id or "").strip()
    if not package_id or "/" in package_id or "\\" in package_id or ".." in package_id:
        raise Http404("Package not found.")

    owner_session = owner_session_for_import_session(session)
    if not owner_session:
        raise Http404("Package not found.")

    # Origin: prefer local create receipt workflow; else session workflow that
    # matches live package identity (read-only GET discovery path, freeze §5.3).
    api = EasyImportsApiClient()
    mutation = (
        session.api_mutations.filter(
            mutation_kind="create_run_output_package",
            state=ApiMutation.State.COMPLETED,
            response_json__package_id=package_id,
            workflow__isnull=False,
        )
        .select_related("workflow")
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    origin: ApiWorkflow | None = None
    package_meta: dict | None = None
    try:
        if mutation is not None and mutation.workflow is not None:
            origin = mutation.workflow
            package_meta = _live_package_for_id(
                package_id=package_id,
                workflow=origin,
                owner_session=owner_session,
                client=api,
            )
        else:
            live = api.run_output_package(
                package_id, owner_session=owner_session
            )
            origin = (
                session.api_workflows.filter(
                    run_id=str(live.get("run_id") or ""),
                    revision=int(live.get("run_revision")),
                    workflow_key=str(live.get("workflow_key") or ""),
                ).first()
            )
            if origin is not None and _package_matches_workflow(live, origin):
                package_meta = live
    except (CLIENT_ERRORS, TypeError, ValueError) as exc:
        raise Http404(str(exc)) from exc
    if origin is None or package_meta is None:
        raise Http404("Package not found.")

    run_id = str(package_meta.get("run_id") or "")
    expected_digest = str(package_meta.get("content_digest") or "")
    expected_bytes = package_meta.get("byte_count")
    download_url = package_meta.get("download_url")
    if not expected_digest:
        raise Http404("Package is not downloadable.")

    temporary = tempfile.TemporaryFile()
    upstream = None
    digest = sha256()
    byte_count = 0
    try:
        upstream = EasyImportsApiClient().stream_run_output_package(
            package_id,
            owner_session=owner_session,
            download_url=download_url if isinstance(download_url, str) else None,
        )
        expected_etag = f'"sha256:{expected_digest}"'
        upstream_etag = upstream.headers.get("ETag", "")
        if upstream_etag != expected_etag:
            raise ApiConsistencyError(
                "Package ETag does not agree with its stored digest."
            )
        for chunk in upstream.iter_content(chunk_size=64 * 1024):
            if not chunk:
                continue
            temporary.write(chunk)
            digest.update(chunk)
            byte_count += len(chunk)
        if digest.hexdigest() != expected_digest:
            raise ApiConsistencyError("Package content digest mismatch.")
        if expected_bytes is not None and byte_count != int(expected_bytes):
            raise ApiConsistencyError("Package byte count mismatch.")
        temporary.seek(0)
        filename = f"easyimports-run-{run_id or 'results'}-results.zip"
        response = FileResponse(
            temporary,
            as_attachment=True,
            filename=filename,
            content_type=upstream.headers.get("Content-Type", "application/zip"),
        )
        response["ETag"] = upstream_etag
        response["Content-Length"] = str(byte_count)
        return response
    except (*CLIENT_ERRORS, KeyError, TypeError, ValueError, OSError) as exc:
        temporary.close()
        raise Http404(str(exc)) from exc
    finally:
        if upstream is not None:
            upstream.close()
