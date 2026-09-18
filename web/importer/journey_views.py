"""Django CRM duplicate-resolution journey UI (Phase 4A–5B + 7C redesigned path).

Primary operator flow:
  start (crm scan | upload; optional auto-merge T≥90) -> (upload: map Record ID)
  -> progress -> once-only auto-disposition when T frozen
  -> five-group review (manual remaining) or all-auto merge summary
  -> merge summary -> separate write authorization

Exact-retry safe: form-scoped signed tokens freeze form_instance; multi-step
mutations share stable logical_action_identity values per attempt.

Does not import mappings_2.
"""

from __future__ import annotations

from hashlib import sha256
from types import SimpleNamespace
from typing import Any, Callable, TypeVar
from urllib.parse import quote, urlencode
from uuid import UUID, uuid4, uuid5

import time

from django import forms
from django.contrib import messages
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from django.views.decorators.cache import never_cache

from .api_client import (
    ApiConsistencyError,
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    api_error_technical_details,
    create_or_reuse_mutation,
)
from .command_service import action_generation
from .crm_duplicate_merge_copy import EDIT_DISPOSITIONS_LABEL
from .crm_duplicate_score_helper import (
    AUTO_MERGE_INFO_TIP,
    COMPANY_SCORE_BANDS,
    PERSON_AUDIT_NOTE,
    PERSON_SCORE_BANDS,
    THRESHOLD_INFO_TIP,
)
from .crm_duplicate_review_display import (
    review_cell_value,
    review_field_columns,
    winner_reason,
)
from .field_merge_display import FIELD_FILL_EMPTY_SENTENCE, field_merge_display
from .forms import CrmDuplicateJourneyForm
from .models import (
    ApiMutation,
    ApiWorkflow,
    CrmDuplicateJourneyAttemptClaim,
    CrmDuplicateJourneyDismissal,
    CrmDuplicateMergePlanLease,
    ImportSession,
)
from .upload_service import save_and_register_upload
from .workflow_state import (
    FormTokenError,
    archive_session,
    crm_duplicate_lineage_source_workflow,
    decode_form_token,
    issue_form_token,
    owner_id_for_request,
    projection_digest,
    store_workflow_projection,
    validate_form_token,
)

# Namespace for deterministic per-step form instances (distinct from root token).
_STEP_FORM_NS = UUID("a4b4c4d4-e4f4-4004-a004-b004c004d004")

CLIENT_ERRORS = (
    ApiUnavailableError,
    ApiRejectedError,
    ApiConsistencyError,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    FormTokenError,
    KeyError,
    TypeError,
    ValueError,
)

# Successful analysis terminals that may leave progress for review/workflow.
_REVIEW_READY_STATUSES = frozenset(
    {
        "awaiting_review",
        "needs_decision",
        "succeeded",
    }
)
_REVIEW_READY_STAGES = frozenset(
    {
        "review_ready",
        "awaiting_review",
        "no_duplicate_groups",
    }
)
_DUPLICATE_EXECUTION_MODES = frozenset({"disabled", "preview", "dry_run", "execute"})

START_ACTION = "crm_duplicate_journey_start_4a"
MAP_ACTION = "crm_duplicate_population_map_4a"
PROGRESS_ACTION = "crm_duplicate_journey_progress_4a"
REVIEW_ACTION = "crm_duplicate_journey_review_4b"
MERGE_ACTION = "crm_duplicate_journey_merge_5a"
AMEND_ACTION = "crm_duplicate_journey_amend_5a"
ABANDON_ACTION = "crm_duplicate_journey_abandon_2"
REFERENCE_ACQUISITION_RECOVERY_ACTION = (
    "crm_duplicate_reference_acquisition_recovery_scr3c"
)
REFERENCE_ACQUISITION_RECOVERY_KIND = "recover_reference_acquisition"
REFERENCE_ACQUISITION_RESET_REQUIRED = (
    "reference_acquisition_saved_progress_reset_required"
)
REFERENCE_ACQUISITION_RECOVERY_CONTRACT = (
    "easyimports.reference_acquisition.recovery.v1"
)
REFERENCE_ACQUISITION_RECOVERY_SUCCESS = (
    "Saved scan data was cleared and the scan was retried."
)
AUTO_DISPOSITION_COMMAND_KIND = "submit_duplicate_auto_disposition"
AUTO_DISPOSITION_RETRY_ACTION = "crm_duplicate_auto_disposition_retry_arw3a"
AUTO_DISPOSITION_INCOMPLETE_CODE = "duplicate_analysis_incomplete"
AUTO_DISPOSITION_CTA_FIELD = "auto_disposition_cta"
DISMISSED_JOURNEY_IDS_OPTION = "dismissed_crm_duplicate_journey_ids"

_REVIEW_ACTIONS = frozenset({"approve", "override_survivor", "decline", "quarantine"})
_NON_TERMINAL_WORKFLOW_STATUSES = frozenset(
    {
        "ready",
        "running",
        "needs_decision",
        "awaiting_effect_authorization",
        "awaiting_review",
        "awaiting_execution_continuation",
        "paused_unknown",
        "paused_verification",
    }
)
_CRM_WRITE_TRACKS = frozenset(
    {
        "account_provisioning",
        "account_writes",
        "person_duplicate_resolution",
        "people_writes",
        "campaign_member_writes",
        "dataset_writes",
        "duplicate_execution",
    }
)
_NON_CRM_WRITE_TRACKS = frozenset({"reference_acquisition", "crm_query", "delivery"})
_ABANDON_SAFE_CONFIRMATION = (
    "This will permanently stop this analysis. No groups will be merged. "
    "This cannot be undone."
)
_ABANDON_UNCERTAIN_CONFIRMATION = (
    "This will permanently stop this analysis on this install. If a CRM merge "
    "or write was already sent, EasyImports cannot recall it. The remote outcome "
    "may be uncertain. This cannot be undone."
)
_CONNECTION_REMOVAL_QUARANTINE_COPY = (
    "This analysis was stopped because its CRM connection was removed. "
    "EasyImports could not record a normal stopped state."
)


def _owner_session(request) -> str:
    owner = owner_id_for_request(request)
    return f"django-{owner}"


def _journey_session(request) -> ImportSession:
    """Shared mutation journal for CRM journey start actions (not upload bytes)."""

    owner = owner_id_for_request(request)
    existing = (
        ImportSession.objects.filter(owner_id=owner, product_key="crm.journey")
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return ImportSession.objects.create(
        owner_id=owner,
        operator_label="CRM duplicate journey",
        product_key="crm.journey",
        target_provider_id="",
    )


def _dismissed_journey_ids(journal: ImportSession) -> set[str]:
    """Return locally dismissed API journey IDs from the owner journal."""

    raw = (journal.options or {}).get(DISMISSED_JOURNEY_IDS_OPTION) or []
    legacy = (
        {str(value).strip() for value in raw if str(value).strip()}
        if isinstance(raw, list)
        else set()
    )
    durable = set(
        CrmDuplicateJourneyDismissal.objects.filter(journal=journal).values_list(
            "journey_id", flat=True
        )
    )
    return legacy | durable


def _remember_dismissed_journey(journal: ImportSession, journey_id: str) -> None:
    """Hide one journey locally without deleting API journals/checkpoints."""

    value = str(journey_id or "").strip()
    if not value:
        raise ValueError("Journey ID is required.")
    _retry_on_sqlite_lock(
        lambda: CrmDuplicateJourneyDismissal.objects.get_or_create(
            journal=journal,
            journey_id=value,
        )
    )


def _entity_label(entity_family: str) -> str:
    return "Companies/Accounts" if entity_family == "company" else "People"


def _source_label(source_mode: str) -> str:
    if source_mode in {"uploaded_population", "candidate_upload"}:
        return "Upload records to analyze"
    if source_mode == "selected_ids":
        return "Selected CRM record IDs"
    return "Find duplicates in CRM"


def _workflow_session_for_request(request, session_id) -> ImportSession:
    return get_object_or_404(
        ImportSession,
        pk=session_id,
        owner_id=owner_id_for_request(request),
        archived_at__isnull=True,
    )


def _duplicate_execution_ceiling(connection: dict | None) -> str:
    """Freeze journey ceiling from the connected provider capability."""

    if not isinstance(connection, dict):
        return "dry_run"
    max_auth = connection.get("maximum_authorization") or {}
    mode = str(max_auth.get("duplicate_execution") or "").strip()
    if mode in _DUPLICATE_EXECUTION_MODES:
        return mode
    # Fail closed to dry_run when capability is missing/unknown (still no write).
    return "dry_run"


def _session_auto_merge_threshold(options: dict | None) -> int | None:
    """Return frozen T (90–100) from session options, or None when disabled.

    Corrupted durable values fail closed (raise) rather than silently disabling
    auto-merge or clamping into range.
    """

    if not isinstance(options, dict):
        return None
    raw = options.get("auto_merge_min_confidence")
    if raw is None or raw == "" or raw == _AUTO_MERGE_DISABLED_INTENT:
        return None
    encoded = _normalize_auto_merge_intent(raw, missing_ok=False)
    return int(encoded)


def _form_matching_mode(form: CrmDuplicateJourneyForm) -> str:
    cleaned = getattr(form, "cleaned_data", None) or {}
    raw = cleaned.get("matching_mode")
    if raw in (None, ""):
        return _MATCHING_MODE_DEFAULT
    return _normalize_matching_mode_intent(raw)


def _session_matching_mode(options: dict | None) -> str:
    if not isinstance(options, dict):
        return _MATCHING_MODE_DEFAULT
    raw = options.get("matching_mode")
    if raw in (None, ""):
        return _MATCHING_MODE_DEFAULT
    return _normalize_matching_mode_intent(raw)


def _form_auto_merge_threshold(form: CrmDuplicateJourneyForm) -> int | None:
    """Read cleaned auto-merge threshold (None when toggle off)."""

    cleaned = getattr(form, "cleaned_data", None) or {}
    raw = cleaned.get("auto_merge_min_confidence")
    if raw is None:
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    if 90 <= value <= 100:
        return value
    return None


def _connections_by_id(connections: list[dict]) -> dict[str, dict]:
    return {
        str(item.get("connection_id") or ""): item
        for item in connections
        if item.get("status") == "connected"
    }


class RecordIdMappingForm(forms.Form):
    """Phase 4A Screen 2: map one uploaded column to Record ID."""

    source_column = forms.ChoiceField(
        label="Which column contains the CRM record ID?",
        help_text="EasyImports will reread those IDs from the connected CRM.",
    )
    form_token = forms.CharField(widget=forms.HiddenInput)

    def __init__(
        self, *args, headers: list[str], suggested: str | None = None, **kwargs
    ):
        super().__init__(*args, **kwargs)
        choices = [(header, header) for header in headers if str(header).strip()]
        if not choices:
            choices = [("", "No headers found")]
            self.fields["source_column"].disabled = True
        self.fields["source_column"].choices = choices
        if suggested and any(header == suggested for header, _ in choices):
            self.fields["source_column"].initial = suggested


def _step_form_instance(root_form_instance: UUID, step: str) -> UUID:
    """Distinct, deterministic form_instance per multi-mutation step.

    ``create_or_reuse_mutation`` treats matching form_instance as the same
    mutation row. Reusing the root token form_instance for journey+grant+apply
    therefore conflicts. Derive one stable UUID per step from the root so exact
    retries of that step converge without colliding with sibling steps.
    """

    return uuid5(_STEP_FORM_NS, f"{root_form_instance}:{step}")


def _mutation_journal_for_session(workflow_session: ImportSession) -> ImportSession:
    """Return the session that owns RA/journey mutations for progress retry."""

    options = dict(workflow_session.options or {})
    journal_id = options.get("mutation_journal_id")
    if not journal_id:
        return workflow_session
    try:
        return ImportSession.objects.get(pk=journal_id)
    except (ImportSession.DoesNotExist, ValueError, TypeError):
        return workflow_session


_ATTEMPT_INTENT_KEYS = (
    "source_mode",
    "connection_id",
    "entity_family",
    "duplicate_execution_maximum",
    "auto_merge_min_confidence",
    "matching_mode",
)

_CHANGED_ATTEMPT_REUSE_MSG = (
    "This CRM duplicate attempt was already started with a different source, "
    "connection, entity, capability ceiling, matching detail, or auto-merge "
    "threshold. Start a new attempt from the beginning (do not reuse the same "
    "form)."
)

_AUTO_MERGE_DISABLED_INTENT = "none"
_MATCHING_MODE_DEFAULT = "default"
_MATCHING_MODES = frozenset({"default", "exact_only"})


class InvalidMatchingModeError(ValueError):
    """Durable claim/session matching_mode is present but invalid."""


def _normalize_matching_mode_intent(value: Any) -> str:
    if value is None or value == "":
        return _MATCHING_MODE_DEFAULT
    if not isinstance(value, str):
        raise InvalidMatchingModeError(
            "matching_mode must be 'default' or 'exact_only'."
        )
    if value not in _MATCHING_MODES:
        raise InvalidMatchingModeError(
            "matching_mode must be 'default' or 'exact_only'."
        )
    return value


class InvalidAutoMergeThresholdError(ValueError):
    """Durable claim/session auto-merge threshold is present but invalid."""


def _normalize_auto_merge_intent(
    value: Any,
    *,
    missing_ok: bool = True,
) -> str:
    """Canonical claim/intent encoding: 'none' or integer string 90–100.

    Missing / null / explicit ``none`` → disabled when ``missing_ok``.
    Out-of-range, non-integer (e.g. 90.5), bool, or malformed values **fail
    closed** (never silent clamp, truncate, or disable).
    """

    if value is None or value == "" or value == _AUTO_MERGE_DISABLED_INTENT:
        if missing_ok:
            return _AUTO_MERGE_DISABLED_INTENT
        raise InvalidAutoMergeThresholdError(
            "auto_merge_min_confidence is required and must be an integer 90–100."
        )
    if isinstance(value, bool):
        raise InvalidAutoMergeThresholdError(
            "auto_merge_min_confidence must be an integer from 90 to 100 "
            f"(or 'none' when disabled); got {value!r}."
        )
    if isinstance(value, float):
        if not value.is_integer():
            raise InvalidAutoMergeThresholdError(
                "auto_merge_min_confidence must be a whole integer from 90 to 100 "
                f"(not a fractional value); got {value!r}."
            )
        number = int(value)
    elif isinstance(value, int):
        number = value
    else:
        text = str(value).strip()
        if text == _AUTO_MERGE_DISABLED_INTENT:
            if missing_ok:
                return _AUTO_MERGE_DISABLED_INTENT
            raise InvalidAutoMergeThresholdError(
                "auto_merge_min_confidence is required and must be an integer 90–100."
            )
        # Reject "90.5", scientific notation, and other non-digit text.
        if not text.lstrip("-").isdigit():
            raise InvalidAutoMergeThresholdError(
                "auto_merge_min_confidence must be an integer from 90 to 100 "
                f"(or 'none' when disabled); got {value!r}."
            )
        number = int(text)
    if 90 <= number <= 100:
        return str(number)
    raise InvalidAutoMergeThresholdError(
        "auto_merge_min_confidence must be an integer from 90 to 100 "
        f"(values outside that range fail closed); got {number}."
    )


def _root_attempt_intent(
    *,
    source_mode: str,
    connection_id: str,
    entity_family: str,
    duplicate_execution_maximum: str,
    auto_merge_min_confidence: Any = None,
    matching_mode: Any = None,
) -> dict[str, str]:
    """Canonical root-attempt intent frozen at claim time."""

    return {
        "source_mode": str(source_mode or "").strip(),
        "connection_id": str(connection_id or "").strip(),
        "entity_family": str(entity_family or "").strip(),
        "duplicate_execution_maximum": str(duplicate_execution_maximum or "").strip(),
        "auto_merge_min_confidence": _normalize_auto_merge_intent(
            auto_merge_min_confidence
        ),
        "matching_mode": _normalize_matching_mode_intent(matching_mode),
    }


def _coerce_attempt_intent(raw: Any) -> dict[str, str] | None:
    """Normalize a stored/posted intent dict; None if incomplete or corrupt."""

    if not isinstance(raw, dict):
        return None
    required = (
        "source_mode",
        "connection_id",
        "entity_family",
        "duplicate_execution_maximum",
    )
    intent = {
        "source_mode": str(raw.get("source_mode") or "").strip(),
        "connection_id": str(raw.get("connection_id") or "").strip(),
        "entity_family": str(raw.get("entity_family") or "").strip(),
        "duplicate_execution_maximum": str(
            raw.get("duplicate_execution_maximum") or ""
        ).strip(),
    }
    if not all(intent[key] for key in required):
        return None
    try:
        intent["auto_merge_min_confidence"] = _normalize_auto_merge_intent(
            raw.get("auto_merge_min_confidence")
        )
        intent["matching_mode"] = _normalize_matching_mode_intent(
            raw.get("matching_mode")
        )
    except (InvalidAutoMergeThresholdError, InvalidMatchingModeError):
        # Corrupt durable threshold or matching mode is not a valid attempt identity.
        return None
    return intent


def _intent_from_session_options(session: ImportSession) -> dict[str, str] | None:
    """Best-effort intent from a previously claimed session's options."""

    options = dict(session.options or {})
    return _coerce_attempt_intent(
        {
            "source_mode": options.get("source_mode"),
            "connection_id": options.get("connection_id"),
            "entity_family": options.get("entity_family"),
            "duplicate_execution_maximum": options.get("duplicate_execution_maximum"),
            "auto_merge_min_confidence": options.get("auto_merge_min_confidence"),
            "matching_mode": options.get("matching_mode"),
        }
    )


def _reject_changed_attempt_reuse(
    frozen: dict[str, str] | None,
    posted: dict[str, str],
) -> None:
    """Raise when a signed root form is reused with a different attempt intent."""

    if frozen is None:
        return
    if frozen != posted:
        raise MutationReuseError(_CHANGED_ATTEMPT_REUSE_MSG)


def _intent_from_claim(claim: CrmDuplicateJourneyAttemptClaim) -> dict[str, str]:
    return _root_attempt_intent(
        source_mode=claim.source_mode,
        connection_id=claim.connection_id,
        entity_family=claim.entity_family,
        duplicate_execution_maximum=claim.duplicate_execution_maximum,
        auto_merge_min_confidence=getattr(
            claim, "auto_merge_min_confidence", _AUTO_MERGE_DISABLED_INTENT
        ),
        matching_mode=getattr(claim, "matching_mode", _MATCHING_MODE_DEFAULT),
    )


def _auto_merge_pending(options: dict | None) -> bool:
    """True when T is frozen and auto-disposition has not been applied yet."""

    if not isinstance(options, dict):
        return False
    if options.get("auto_disposition_applied"):
        return False
    return _session_auto_merge_threshold(options) is not None


def _auto_queued_review_copy(withheld_count: int | None = None) -> str:
    """Copy when no manual groups are ready and auto-eligible groups remain.

    ``withheld_count`` must be the current pending auto-eligible count, never
    ``review_groups_ready`` (that is the cumulative materials prefix). When the
    exact withheld count is unknown, use the count-free sentence.
    """

    if (
        isinstance(withheld_count, int)
        and not isinstance(withheld_count, bool)
        and withheld_count > 0
    ):
        if withheld_count == 1:
            return (
                "No groups need your review yet — 1 high-confidence group is "
                "queued for automatic approval."
            )
        return (
            f"No groups need your review yet — {withheld_count:,} high-confidence "
            "groups are queued for automatic approval."
        )
    return (
        "No groups need your review yet — high-confidence groups are queued "
        "for automatic approval."
    )


def _analysis_complete_for_auto_disposition(projection: dict | None) -> bool:
    """True when auto-disposition may run (complete population, GFC-8.10)."""

    if not isinstance(projection, dict) or _is_failed(projection):
        return False
    progress = projection.get("duplicate_analysis_progress") or {}
    if isinstance(progress, dict) and progress.get("review_ready") is True:
        return True
    status = str(projection.get("status") or "")
    stage = str(projection.get("stage") or "")
    # Incremental handoff must not count as complete-population (GFC-8.10).
    return status in _REVIEW_READY_STATUSES or stage in _REVIEW_READY_STAGES


def _source_is_review_ready_for_auto_disposition(projection: dict | None) -> bool:
    """True only when public progress affirmatively reports review_ready."""

    if not isinstance(projection, dict):
        return False
    progress = projection.get("duplicate_analysis_progress")
    if not isinstance(progress, dict):
        return False
    return progress.get("review_ready") is True


def _cta_root_form_instance(options: dict | None) -> UUID | None:
    if not isinstance(options, dict):
        return None
    raw = options.get("apply_root_form_instance") or options.get(
        "orchestrator_form_instance"
    )
    if raw is None or str(raw).strip() == "":
        return None
    try:
        return UUID(str(raw))
    except (TypeError, ValueError):
        return None


def _auto_disposition_identity(
    *,
    owner_session: str,
    root_form_instance: UUID,
    review_run_id: str,
    threshold: int,
) -> str:
    return _step_identity(
        step="auto-disposition",
        owner_session=owner_session,
        form_instance=root_form_instance,
        resource=f"{review_run_id}:{threshold}",
    )


def _mutation_error_code(mutation: ApiMutation) -> str:
    code = str(mutation.error_code or "").strip()
    if code:
        return code
    response = mutation.response_json if isinstance(mutation.response_json, dict) else {}
    error = response.get("error")
    if isinstance(error, dict):
        details = error.get("details")
        if isinstance(details, dict) and details.get("error_code"):
            return str(details["error_code"])
        if error.get("code"):
            return str(error["code"])
    return str(response.get("error_code") or "")


def _latest_auto_disposition_mutation(
    journal: ImportSession, identity: str
) -> ApiMutation | None:
    return (
        journal.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at", "-id")
        .first()
    )


def _generation_g_mutation(
    journal: ImportSession, identity: str, generation: int
) -> ApiMutation | None:
    return (
        journal.api_mutations.filter(
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
        .order_by("created_at", "id")
        .first()
    )


def _is_retryable_incomplete_rejection(mutation: ApiMutation | None) -> bool:
    if mutation is None or mutation.state != ApiMutation.State.REJECTED:
        return False
    if mutation.is_idempotency_conflict:
        return False
    return _mutation_error_code(mutation) == AUTO_DISPOSITION_INCOMPLETE_CODE


_LEGACY_AUTO_DISPOSITION_REQUEST_KEYS = frozenset(
    {
        "command_kind",
        "expected_revision",
        "auto_merge_min_confidence",
        "owner_session",
    }
)


_DOCUMENTED_LEGACY_AUTO_DISPOSITION_GENERATION = 0


def _is_legacy_pre_source_binding_request(request: dict[str, Any] | None) -> bool:
    """True only for the documented pre-remediation captured payload.

    That row stored command_kind, expected_revision, auto_merge_min_confidence,
    and owner_session — and no source_run_id. Any other missing-source shape
    stays fail-closed.
    """

    if not isinstance(request, dict):
        return False
    if "source_run_id" in request:
        return False
    if set(request.keys()) != _LEGACY_AUTO_DISPOSITION_REQUEST_KEYS:
        return False
    return str(request.get("command_kind") or "") == AUTO_DISPOSITION_COMMAND_KIND


def _auto_disposition_bindings_match(
    mutation: ApiMutation,
    *,
    review_run_id: str,
    threshold: int,
    source_run_id: str,
) -> bool:
    if str(mutation.resource_identity or "") != review_run_id:
        return False
    request = mutation.request_json if isinstance(mutation.request_json, dict) else {}
    raw_threshold = request.get("auto_merge_min_confidence")
    raw_source = request.get("source_run_id")
    try:
        stored_threshold = int(raw_threshold)
    except (TypeError, ValueError):
        return False
    if stored_threshold != int(threshold):
        return False
    if not raw_source or str(raw_source) != source_run_id:
        return False
    return True


def _is_documented_legacy_auto_disposition_predecessor(
    mutation: ApiMutation,
    *,
    review_run_id: str,
    threshold: int,
) -> bool:
    """True only for the live captured generation-0 incomplete predecessor.

    Replay and every other generation stay on the strict source-bound matcher.
    """

    if int(mutation.logical_action_generation) != (
        _DOCUMENTED_LEGACY_AUTO_DISPOSITION_GENERATION
    ):
        return False
    if not _is_retryable_incomplete_rejection(mutation):
        return False
    if str(mutation.resource_identity or "") != review_run_id:
        return False
    request = mutation.request_json if isinstance(mutation.request_json, dict) else {}
    raw_threshold = request.get("auto_merge_min_confidence")
    try:
        stored_threshold = int(raw_threshold)
    except (TypeError, ValueError):
        return False
    if stored_threshold != int(threshold):
        return False
    return _is_legacy_pre_source_binding_request(request)


def _prospective_auto_disposition_generation(
    latest: ApiMutation | None,
) -> int | None:
    if latest is None:
        return 0
    if _is_retryable_incomplete_rejection(latest):
        return int(latest.logical_action_generation) + 1
    return None


def _auto_disposition_cta_context(
    *,
    owner_id,
    workflow_session: ImportSession,
    journal: ImportSession,
    owner_session: str,
    client: EasyImportsApiClient,
    source_run_id: str,
    review_run_id: str,
    options: dict[str, Any],
) -> dict[str, Any]:
    """Read-only GET context for the auto-disposition retry CTA. Never writes."""

    empty = {
        "auto_disposition_cta_token": "",
        "auto_disposition_needs_attention": False,
        "auto_disposition_root_missing": False,
    }
    if not _auto_merge_pending(options) or not review_run_id:
        return empty
    threshold = _session_auto_merge_threshold(options)
    if threshold is None:
        return empty
    root = _cta_root_form_instance(options)
    if root is None:
        return {
            "auto_disposition_cta_token": "",
            "auto_disposition_needs_attention": False,
            "auto_disposition_root_missing": True,
        }
    identity = _auto_disposition_identity(
        owner_session=owner_session,
        root_form_instance=root,
        review_run_id=review_run_id,
        threshold=threshold,
    )
    latest = _latest_auto_disposition_mutation(journal, identity)
    if latest is not None and not _is_retryable_incomplete_rejection(latest):
        if latest.state == ApiMutation.State.COMPLETED:
            return empty
        return {
            "auto_disposition_cta_token": "",
            "auto_disposition_needs_attention": True,
            "auto_disposition_root_missing": False,
        }
    if latest is None:
        source_projection: dict[str, Any] = {}
        try:
            source_projection = client.workflow(
                source_run_id, owner_session=owner_session
            )
        except CLIENT_ERRORS:
            source_projection = {}
        if not _source_is_review_ready_for_auto_disposition(source_projection):
            return empty
    generation = _prospective_auto_disposition_generation(latest)
    if generation is None:
        return {
            "auto_disposition_cta_token": "",
            "auto_disposition_needs_attention": True,
            "auto_disposition_root_missing": False,
        }
    review_projection: dict[str, Any] = {}
    try:
        review_projection = client.workflow(review_run_id, owner_session=owner_session)
    except CLIENT_ERRORS:
        review_projection = {}
    token_workflow = SimpleNamespace(
        run_id=review_run_id,
        revision=int(review_projection.get("revision") or 0),
        projection_digest=projection_digest(review_projection)
        if review_projection
        else "",
    )
    token = issue_form_token(
        owner_id=owner_id,
        session=workflow_session,
        action_kind=AUTO_DISPOSITION_RETRY_ACTION,
        workflow=token_workflow,
        action_id=f"auto-disposition:{review_run_id}:{threshold}",
        logical_action_identity=identity,
        logical_action_generation=generation,
    )
    return {
        "auto_disposition_cta_token": token,
        "auto_disposition_needs_attention": False,
        "auto_disposition_root_missing": False,
    }


def _acknowledge_incomplete_predecessor(
    *,
    journal: ImportSession,
    identity: str,
    generation: int,
    review_run_id: str,
    threshold: int,
    source_run_id: str,
) -> None:
    predecessor_generation = generation - 1
    if predecessor_generation < 0:
        if action_generation(journal, identity) != generation:
            raise ValueError("Automatic approval cannot start from this form.")
        return
    with transaction.atomic():
        predecessor = (
            journal.api_mutations.select_for_update()
            .filter(
                logical_action_identity=identity,
                logical_action_generation=predecessor_generation,
            )
            .order_by("created_at", "id")
            .first()
        )
        if not _is_retryable_incomplete_rejection(predecessor):
            raise ValueError("Automatic approval cannot retry this saved action.")
        assert predecessor is not None
        if not (
            _auto_disposition_bindings_match(
                predecessor,
                review_run_id=review_run_id,
                threshold=threshold,
                source_run_id=source_run_id,
            )
            or _is_documented_legacy_auto_disposition_predecessor(
                predecessor,
                review_run_id=review_run_id,
                threshold=threshold,
            )
        ):
            raise ValueError("Automatic approval cannot retry this saved action.")
        if predecessor.acknowledged_at is None:
            predecessor.acknowledged_at = timezone.now()
            predecessor.save(update_fields=["acknowledged_at", "updated_at"])
        if action_generation(journal, identity) != generation:
            raise ValueError("Automatic approval cannot retry this saved action.")


def _replay_or_dispatch_auto_disposition(
    *,
    journal: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    review_run_id: str,
    threshold: int,
    identity: str,
    generation: int,
    form_instance: UUID,
    expected_revision: int,
    source_run_id: str,
) -> dict[str, Any]:
    body = {
        "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
        "expected_revision": expected_revision,
        "auto_merge_min_confidence": threshold,
        "source_run_id": source_run_id,
    }
    return _dispatch_json_mutation(
        journal=journal,
        mutation_kind="submit_duplicate_auto_disposition",
        route=f"/v1/workflows/{review_run_id}/duplicate-auto-disposition",
        body=body,
        owner_session=owner_session,
        client=client,
        form_instance=form_instance,
        logical_action_identity=identity,
        logical_action_generation=generation,
        resource_identity=review_run_id,
    )


def _handle_auto_disposition_cta_post(
    request,
    *,
    workflow_session: ImportSession,
    journal: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    owner_id,
    source_run_id: str,
    review_run_id: str,
    options: dict[str, Any],
):
    root = _cta_root_form_instance(options)
    if root is None:
        raise ValueError(
            "This session cannot retry automatic approval because its form "
            "identity is missing. Start this journey again."
        )
    threshold = _session_auto_merge_threshold(options)
    if threshold is None or not review_run_id:
        raise ValueError("Automatic approval is not available on this journey.")
    identity = _auto_disposition_identity(
        owner_session=owner_session,
        root_form_instance=root,
        review_run_id=review_run_id,
        threshold=threshold,
    )
    latest = _latest_auto_disposition_mutation(journal, identity)
    prospective_generation = _prospective_auto_disposition_generation(latest)
    token = str(request.POST.get("form_token") or "").strip()
    decoded = decode_form_token(
        token,
        owner_id=owner_id,
        session=workflow_session,
        action_kind=AUTO_DISPOSITION_RETRY_ACTION,
    )
    expected_action_id = f"auto-disposition:{review_run_id}:{threshold}"
    try:
        claimed_generation = int(decoded.get("logical_action_generation"))
    except (TypeError, ValueError):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    existing_claimed = _generation_g_mutation(journal, identity, claimed_generation)
    if (
        str(decoded.get("logical_action_identity") or "") != identity
        or str(decoded.get("action_id") or "") != expected_action_id
        or (
            claimed_generation != prospective_generation
            and existing_claimed is None
        )
    ):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    claims = decoded
    generation = claimed_generation
    existing = _generation_g_mutation(journal, identity, generation)
    if existing is not None:
        if not _auto_disposition_bindings_match(
            existing,
            review_run_id=review_run_id,
            threshold=threshold,
            source_run_id=source_run_id,
        ):
            raise ValueError("Automatic approval cannot replay this saved action.")
        frozen_rev = 0
        req = existing.request_json if isinstance(existing.request_json, dict) else {}
        if "expected_revision" in req:
            try:
                frozen_rev = int(req["expected_revision"])
            except (TypeError, ValueError):
                frozen_rev = 0
        result = _replay_or_dispatch_auto_disposition(
            journal=journal,
            client=client,
            owner_session=owner_session,
            review_run_id=review_run_id,
            threshold=threshold,
            identity=identity,
            generation=generation,
            form_instance=claims["form_instance"],
            expected_revision=frozen_rev,
            source_run_id=source_run_id,
        )
        options = _record_auto_disposition_local(
            workflow_session,
            result=result,
            threshold=threshold,
            review_run_id=review_run_id,
            handoff_id=str(options.get("review_handoff_id") or ""),
            source_run_id=source_run_id,
            expected_revision=frozen_rev,
        )
        return _progress_after_auto_disposition(
            request, workflow_session=workflow_session, options=options
        )

    source_projection = client.workflow(source_run_id, owner_session=owner_session)
    review_projection = client.workflow(review_run_id, owner_session=owner_session)
    if not _source_is_review_ready_for_auto_disposition(source_projection):
        raise ValueError("Duplicate analysis is not complete.")
    token_workflow = SimpleNamespace(
        run_id=review_run_id,
        revision=int(review_projection.get("revision") or 0),
        projection_digest=projection_digest(review_projection)
        if review_projection
        else "",
    )
    try:
        validate_form_token(
            token,
            owner_id=owner_id,
            session=workflow_session,
            action_kind=AUTO_DISPOSITION_RETRY_ACTION,
            workflow=token_workflow,
            action_id=f"auto-disposition:{review_run_id}:{threshold}",
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
    except FormTokenError:
        raced = _generation_g_mutation(journal, identity, generation)
        if raced is None:
            raise
        return _handle_auto_disposition_cta_post(
            request,
            workflow_session=workflow_session,
            journal=journal,
            client=client,
            owner_session=owner_session,
            owner_id=owner_id,
            source_run_id=source_run_id,
            review_run_id=review_run_id,
            options=options,
        )
    _acknowledge_incomplete_predecessor(
        journal=journal,
        identity=identity,
        generation=generation,
        review_run_id=review_run_id,
        threshold=threshold,
        source_run_id=source_run_id,
    )
    expected_revision = int(review_projection.get("revision") or 0)
    result = _replay_or_dispatch_auto_disposition(
        journal=journal,
        client=client,
        owner_session=owner_session,
        review_run_id=review_run_id,
        threshold=threshold,
        identity=identity,
        generation=generation,
        form_instance=claims["form_instance"],
        expected_revision=expected_revision,
        source_run_id=source_run_id,
    )
    options = _record_auto_disposition_local(
        workflow_session,
        result=result,
        threshold=threshold,
        review_run_id=review_run_id,
        handoff_id=str(options.get("review_handoff_id") or ""),
        source_run_id=source_run_id,
        expected_revision=expected_revision,
    )
    return _progress_after_auto_disposition(
        request, workflow_session=workflow_session, options=options
    )


def _root_form_uuid(form_instance: UUID | str) -> UUID:
    if isinstance(form_instance, UUID):
        return form_instance
    return UUID(str(form_instance))


_SQLITE_LOCK_ATTEMPTS = 16
_SQLITE_LOCK_SLEEP_BASE_S = 0.05
_T = TypeVar("_T")


def _get_attempt_claim(
    journal: ImportSession,
    *,
    form_instance: UUID | str,
) -> CrmDuplicateJourneyAttemptClaim | None:
    """Read the unique claim row for this journal + root form instance."""

    try:
        return CrmDuplicateJourneyAttemptClaim.objects.select_related(
            "claimed_session"
        ).get(
            journal_id=journal.pk,
            root_form_instance=_root_form_uuid(form_instance),
        )
    except (
        CrmDuplicateJourneyAttemptClaim.DoesNotExist,
        ValueError,
        TypeError,
    ):
        return None


def _retry_on_sqlite_lock(
    operation: Callable[[], _T],
    *,
    attempts: int = _SQLITE_LOCK_ATTEMPTS,
    sleep_base_s: float = _SQLITE_LOCK_SLEEP_BASE_S,
) -> _T:
    """Retry ``operation`` when SQLite raises table-locked OperationalError."""

    last_error: BaseException | None = None
    for attempt_i in range(attempts):
        try:
            return operation()
        except OperationalError as exc:
            last_error = exc
            time.sleep(sleep_base_s * (attempt_i + 1))
    if last_error is not None:
        raise last_error
    raise OperationalError("sqlite lock retry exhausted")


def _get_attempt_claim_retryable(
    journal: ImportSession,
    *,
    form_instance: UUID | str,
) -> CrmDuplicateJourneyAttemptClaim | None:
    """Peer lookup that treats SQLite lock errors as retryable."""

    return _retry_on_sqlite_lock(
        lambda: _get_attempt_claim(journal, form_instance=form_instance)
    )


def _delete_owned_session_retryable(
    session: ImportSession,
    *,
    owner_id,
) -> None:
    """Best-effort orphan delete under brief SQLite write locks."""

    def _delete() -> None:
        ImportSession.objects.filter(pk=session.pk, owner_id=owner_id).delete()

    _retry_on_sqlite_lock(_delete)


def _owned_claimed_session(
    claim: CrmDuplicateJourneyAttemptClaim,
    *,
    owner_id,
) -> ImportSession | None:
    session = claim.claimed_session
    if session.owner_id != owner_id or session.archived_at is not None:
        return None
    return session


def _require_compatible_root_attempt(
    journal: ImportSession,
    *,
    form_instance: UUID | str,
    intent: dict[str, str],
) -> None:
    """Fail fast when a prior claim already froze a different root intent.

    Claim re-validates on the unique table row; this avoids wasted API work on
    the scan path when the attempt is already bound.
    """

    claim = _get_attempt_claim_retryable(journal, form_instance=form_instance)
    if claim is None:
        return
    _reject_changed_attempt_reuse(_intent_from_claim(claim), intent)


def _adopt_winning_claim(
    *,
    orphan: ImportSession,
    owner_id,
    journal: ImportSession,
    root: UUID,
    posted: dict[str, str],
) -> tuple[ImportSession, bool]:
    """Drop a losing factory draft and return the unique claim winner.

    Peer lookup and orphan delete both retry SQLite lock errors so a locked
    claim-table read cannot escape before cleanup.
    """

    _delete_owned_session_retryable(orphan, owner_id=owner_id)
    winner = _get_attempt_claim_retryable(journal, form_instance=root)
    if winner is None:
        raise MutationReuseError(
            "This CRM duplicate attempt could not be claimed. "
            "Start a new attempt from the beginning."
        )
    session = _owned_claimed_session(winner, owner_id=owner_id)
    if session is None:
        raise MutationReuseError(
            "This CRM duplicate attempt is no longer available. "
            "Start a new attempt from the beginning."
        )
    _reject_changed_attempt_reuse(_intent_from_claim(winner), posted)
    return session, False


def _claim_or_get_attempt_session(
    request,
    journal: ImportSession,
    *,
    form_instance: UUID | str,
    intent: dict[str, str],
    factory: Callable[[], ImportSession],
) -> tuple[ImportSession, bool]:
    """Resolve or create the local session for a root form attempt.

    Convergence is enforced by a unique DB constraint on
    ``(journal, root_form_instance)`` (``CrmDuplicateJourneyAttemptClaim``),
    not by ``select_for_update``. Concurrent claimers may both run the factory;
    only one INSERT wins. Losers catch ``IntegrityError`` (or observe a peer
    after SQLite write contention), delete their orphan session, and return the
    winner. Works on SQLite and PostgreSQL.

    Frozen intent columns reject changed-payload token reuse
    (source mode, connection, entity, execution ceiling). Peer lookups after
    lock errors are themselves retryable so SQLite contention cannot leak past
    orphan cleanup.
    """

    posted = _coerce_attempt_intent(intent)
    if posted is None:
        raise ValueError("root attempt intent is incomplete")

    owner_id = owner_id_for_request(request)
    root = _root_form_uuid(form_instance)

    existing_claim = _get_attempt_claim_retryable(journal, form_instance=root)
    if existing_claim is not None:
        existing = _owned_claimed_session(existing_claim, owner_id=owner_id)
        if existing is not None:
            _reject_changed_attempt_reuse(_intent_from_claim(existing_claim), posted)
            return existing, False

        # Stale claim (session archived/missing ownership) — fall through to
        # re-claim after deleting the broken row.
        def _drop_stale() -> None:
            CrmDuplicateJourneyAttemptClaim.objects.filter(
                pk=existing_claim.pk
            ).delete()

        _retry_on_sqlite_lock(_drop_stale)

    # Factory may itself hit brief SQLite write locks under concurrent requests.
    # Run each factory attempt in an atomic block so multi-write factories
    # (create + save) cannot leave a committed session when a later write fails
    # and this loop retries.
    created: ImportSession | None = None
    last_error: BaseException | None = None
    for attempt_i in range(_SQLITE_LOCK_ATTEMPTS):
        try:
            with transaction.atomic():
                created = factory()
            break
        except OperationalError as exc:
            last_error = exc
            created = None
            # Peer may have claimed while we waited on factory I/O. Lookup is
            # retryable — a locked peer read must not escape the retry loop.
            try:
                peer = _get_attempt_claim_retryable(journal, form_instance=root)
            except OperationalError as lookup_exc:
                last_error = lookup_exc
                peer = None
            if peer is not None:
                session = _owned_claimed_session(peer, owner_id=owner_id)
                if session is not None:
                    _reject_changed_attempt_reuse(_intent_from_claim(peer), posted)
                    return session, False
            time.sleep(_SQLITE_LOCK_SLEEP_BASE_S * (attempt_i + 1))
    if created is None:
        if last_error is not None:
            raise last_error
        raise OperationalError("crm dupe attempt factory failed")

    # Unique INSERT is the authority. On SQLite, concurrent unique INSERT may
    # surface as OperationalError ("database is locked") instead of
    # IntegrityError until the winner commits — retry and re-read the claim row
    # so losers always drop their orphan rather than raising with two sessions.
    for attempt_i in range(_SQLITE_LOCK_ATTEMPTS):
        try:
            with transaction.atomic():
                CrmDuplicateJourneyAttemptClaim.objects.create(
                    journal=journal,
                    root_form_instance=root,
                    claimed_session=created,
                    source_mode=posted["source_mode"],
                    connection_id=posted["connection_id"],
                    entity_family=posted["entity_family"],
                    duplicate_execution_maximum=posted["duplicate_execution_maximum"],
                    auto_merge_min_confidence=posted.get(
                        "auto_merge_min_confidence", _AUTO_MERGE_DISABLED_INTENT
                    )
                    or _AUTO_MERGE_DISABLED_INTENT,
                    matching_mode=posted.get(
                        "matching_mode", _MATCHING_MODE_DEFAULT
                    )
                    or _MATCHING_MODE_DEFAULT,
                )
            return created, True
        except IntegrityError:
            # Unique (journal, root_form_instance) — peer won the claim race.
            return _adopt_winning_claim(
                orphan=created,
                owner_id=owner_id,
                journal=journal,
                root=root,
                posted=posted,
            )
        except OperationalError as exc:
            last_error = exc
            try:
                peer = _get_attempt_claim_retryable(journal, form_instance=root)
            except OperationalError as lookup_exc:
                # Locked peer read: stay in the install loop and retry INSERT
                # / lookup rather than leaking the lock error with an orphan.
                last_error = lookup_exc
                peer = None
            if peer is not None:
                return _adopt_winning_claim(
                    orphan=created,
                    owner_id=owner_id,
                    journal=journal,
                    root=root,
                    posted=posted,
                )
            time.sleep(_SQLITE_LOCK_SLEEP_BASE_S * (attempt_i + 1))

    # Final peer check after retries (winner may have just committed). Lookup
    # and orphan delete both retry locks so cleanup always runs.
    try:
        peer = _get_attempt_claim_retryable(journal, form_instance=root)
    except OperationalError as lookup_exc:
        last_error = lookup_exc
        peer = None
    if peer is not None:
        return _adopt_winning_claim(
            orphan=created,
            owner_id=owner_id,
            journal=journal,
            root=root,
            posted=posted,
        )

    # Still no peer — drop orphan so a client retry can re-factory cleanly.
    try:
        _delete_owned_session_retryable(created, owner_id=owner_id)
    except OperationalError as delete_exc:
        last_error = delete_exc
    if last_error is not None:
        raise last_error
    raise OperationalError("crm dupe attempt claim install failed")


def _lookup_attempt_session(
    request,
    journal: ImportSession,
    *,
    form_instance: UUID | str,
) -> ImportSession | None:
    """Read-only attempt claim lookup. Prefer claim for write paths."""

    claim = _get_attempt_claim(journal, form_instance=form_instance)
    if claim is None:
        return None
    return _owned_claimed_session(claim, owner_id=owner_id_for_request(request))


def _create_workflow_session_shell(
    request,
    *,
    journey: dict,
    extra_options: dict | None = None,
) -> ImportSession:
    """Insert the local workflow session without calling the API.

    Attempt mapping must be claimable before projection fetch so a failed
    ``workflow()`` still leaves a discoverable session for exact retry.
    """

    options = {
        "crm_journey_id": journey["journey_id"],
        "connection_id": journey["connection_id"],
        "provider_key": journey.get("provider_key"),
        "entity_family": journey["entity_family"],
        "source_mode": journey["source_mode"],
        "journey_status": journey.get("status"),
        "redesign_phase": "4a",
        "run_id": journey["run_id"],
    }
    if extra_options:
        options.update(extra_options)
    return ImportSession.objects.create(
        owner_id=owner_id_for_request(request),
        operator_label=(
            f"CRM duplicates · {_entity_label(journey['entity_family'])} · "
            f"{_source_label(journey['source_mode'])}"
        ),
        product_key="easyimports.duplicate_resolution",
        target_provider_id=journey.get("target_provider_id") or "",
        status=ImportSession.Status.RUNNING,
        options=options,
    )


def _refresh_workflow_projection(
    workflow_session: ImportSession,
    *,
    run_id: str,
    owner_session: str,
    client: EasyImportsApiClient,
    role: str = ApiWorkflow.Role.PRIMARY,
    source_workflow: ApiWorkflow | None = None,
) -> dict:
    projection = client.workflow(run_id, owner_session=owner_session)
    store_workflow_projection(
        workflow_session,
        projection,
        resource_url=f"/v1/workflows/{run_id}",
        role=role,
        source_workflow=source_workflow,
    )
    return projection


def _store_continuation_projection(
    workflow_session: ImportSession,
    projection: dict[str, Any],
    *,
    run_id: str,
    make_active: bool = False,
) -> ApiWorkflow:
    """Persist a merge-plan/effect continuation with explicit role + lineage.

    Read-only merge-summary reloads pass ``make_active=False`` so they never
    steal the analysis primary. Post-authorize terminal paths may set
    ``make_active=True`` so the operator sees processing/terminal state (R6)
    while the continuation retains role=CONTINUATION + source lineage (R5).
    """

    # Refresh reverse relation so lineage sees primaries stored earlier in-request.
    workflow_session.refresh_from_db()
    source = crm_duplicate_lineage_source_workflow(workflow_session)
    if source is None:
        source = (
            ApiWorkflow.objects.filter(
                session=workflow_session, role=ApiWorkflow.Role.PRIMARY
            )
            .order_by("-updated_at", "-id")
            .first()
        )
    return store_workflow_projection(
        workflow_session,
        projection,
        resource_url=f"/v1/workflows/{run_id}",
        role=ApiWorkflow.Role.CONTINUATION,
        source_workflow=source,
        make_active=make_active,
    )


def _store_review_projection(
    workflow_session: ImportSession,
    projection: dict[str, Any],
    *,
    run_id: str,
) -> ApiWorkflow:
    """Persist an Account/Person review box with explicit role + lineage.

    Does not replace the session's active primary analysis workflow.
    """

    source = crm_duplicate_lineage_source_workflow(workflow_session)
    return store_workflow_projection(
        workflow_session,
        projection,
        resource_url=f"/v1/workflows/{run_id}",
        role=ApiWorkflow.Role.REVIEW,
        source_workflow=source,
        make_active=False,
    )


def _dispatch_json_mutation(
    *,
    journal: ImportSession,
    mutation_kind: str,
    route: str,
    body: dict,
    owner_session: str,
    client: EasyImportsApiClient,
    form_instance: UUID,
    logical_action_identity: str,
    logical_action_generation: int = 0,
    resource_identity: str | None = None,
) -> dict:
    payload = dict(body)
    payload["owner_session"] = owner_session
    mutation = create_or_reuse_mutation(
        session=journal,
        form_instance=form_instance,
        mutation_kind=mutation_kind,
        route=route,
        logical_action_identity=logical_action_identity,
        logical_action_generation=logical_action_generation,
        request_json=payload,
        resource_identity=resource_identity or str(journal.id),
    )
    # Lost-response recovery: UNKNOWN mutations require explicit_retry=True or
    # every exact form resubmit raises MutationExplicitRetryRequired forever.
    mutation.refresh_from_db()
    result = client.dispatch(
        mutation,
        explicit_retry=mutation.state == ApiMutation.State.UNKNOWN,
    )
    if result.mutation.state == ApiMutation.State.REJECTED:
        code = str(result.mutation.error_code or "rejected")
        message = str(result.mutation.error_message or "The API rejected this action.")
        raise ApiRejectedError(code, message, details=result.response)
    response = result.response
    if not isinstance(response, dict):
        raise ApiConsistencyError("Mutation response is not a JSON object.")
    if mutation_kind == "submit_duplicate_review_end_early":
        if response.get("outcome") != "accepted":
            raise ApiRejectedError(
                str(response.get("error_code") or "rejected"),
                str(response.get("message") or "End early was not accepted."),
                details=response,
            )
        nested = response.get("result")
        if not isinstance(nested, dict) or "review_window" not in nested:
            raise ApiConsistencyError(
                "End-early receipt is missing result.review_window."
            )
    if mutation_kind == "submit_duplicate_review_window":
        if response.get("outcome") != "accepted":
            raise ApiRejectedError(
                str(response.get("error_code") or "rejected"),
                str(
                    response.get("message") or "Review window submit was not accepted."
                ),
                details=response,
            )
        nested = response.get("result")
        if not isinstance(nested, dict) or "review_window" not in nested:
            raise ApiConsistencyError(
                "Review window submit receipt is missing result.review_window."
            )
    if mutation_kind == "submit_duplicate_auto_disposition":
        # Public root body is AutoDispositionResultBody (no receipt wrapper).
        if str(response.get("command_kind") or "") != AUTO_DISPOSITION_COMMAND_KIND:
            raise ApiConsistencyError(
                "Auto-disposition response is missing command_kind."
            )
        if "auto_approved_group_count" not in response:
            raise ApiConsistencyError(
                "Auto-disposition response is missing auto_approved_group_count."
            )
    if mutation_kind == "finalize_duplicate_merge_plan_handoff":
        if response.get("outcome") != "accepted":
            raise ApiRejectedError(
                str(response.get("error_code") or "rejected"),
                str(response.get("message") or "Merge-plan handoff was not accepted."),
                details=response,
            )
        nested = response.get("result")
        if not isinstance(nested, dict) or "merge_plan_handoff" not in nested:
            raise ApiConsistencyError(
                "Merge-plan handoff receipt is missing result.merge_plan_handoff."
            )
    if mutation_kind == "amend_duplicate_reviewed_disposition_window":
        if response.get("outcome") != "accepted":
            raise ApiRejectedError(
                str(response.get("error_code") or "rejected"),
                str(response.get("message") or "Disposition amend was not accepted."),
                details=response,
            )
        nested = response.get("result")
        if not isinstance(nested, dict) or set(nested) != {
            "expected_revision",
            "decision_set_content_digest",
            "reviewed_result_content_digest",
            "prior_decision_set_invalidated",
        }:
            raise ApiConsistencyError(
                "Disposition-window amend receipt has an invalid result body."
            )
    return response


def _step_identity(
    *,
    step: str,
    owner_session: str,
    form_instance: UUID,
    resource: str = "",
) -> str:
    base = f"crm-dupe-4a:{step}:{owner_session}:{form_instance}"
    if resource:
        return f"{base}:{resource}"
    return base


def _create_read_grant(
    *,
    journal: ImportSession,
    owner_session: str,
    client: EasyImportsApiClient,
    root_form_instance: UUID,
    population_source: str,
    connection_id: str | None = None,
    entity_family: str | None = None,
    population_upload_id: str | None = None,
    auto_merge_min_confidence: int | None = None,
) -> dict:
    body: dict[str, Any] = {"population_source": population_source}
    if population_source == "crm_scan":
        body["connection_id"] = connection_id
        body["entity_family"] = entity_family
        resource = f"{connection_id}:{entity_family}"
    else:
        body["population_upload_id"] = population_upload_id
        resource = str(population_upload_id)
    if auto_merge_min_confidence is not None:
        body["auto_merge_min_confidence"] = int(auto_merge_min_confidence)
        resource = f"{resource}:t{int(auto_merge_min_confidence)}"
    step_instance = _step_form_instance(root_form_instance, "grant")
    return _dispatch_json_mutation(
        journal=journal,
        mutation_kind="crm_duplicate_read_grant_create",
        route="/v1/crm/duplicate-read-grants",
        body=body,
        owner_session=owner_session,
        client=client,
        form_instance=step_instance,
        logical_action_identity=_step_identity(
            step="grant",
            owner_session=owner_session,
            form_instance=root_form_instance,
            resource=resource,
        ),
        resource_identity=resource,
    )


def _apply_implied_read(
    *,
    journal: ImportSession,
    owner_session: str,
    client: EasyImportsApiClient,
    root_form_instance: UUID,
    grant_id: str,
    run_id: str,
) -> dict:
    step_instance = _step_form_instance(root_form_instance, "apply")
    return _dispatch_json_mutation(
        journal=journal,
        mutation_kind="crm_duplicate_implied_reference_authorization",
        route=(
            f"/v1/crm/duplicate-read-grants/{grant_id}/implied-reference-authorization"
        ),
        body={"run_id": run_id},
        owner_session=owner_session,
        client=client,
        form_instance=step_instance,
        logical_action_identity=_step_identity(
            step="apply",
            owner_session=owner_session,
            form_instance=root_form_instance,
            resource=f"{grant_id}:{run_id}",
        ),
        resource_identity=run_id,
    )


def _is_failed(projection: dict) -> bool:
    return str(projection.get("status") or "") == "failed"


_OVERSIZED_SOURCE_UNAVAILABLE_COPY = (
    "Could not load oversized-component quarantine details from the "
    "source analysis. Reload this page; do not assume there are no "
    "quarantined components."
)


def _oversized_quarantine_components_from_projection(projection: dict | None) -> list:
    if not isinstance(projection, dict):
        return []
    items = projection.get("oversized_quarantine_components")
    if isinstance(items, list):
        return list(items)
    terminal = projection.get("terminal_evidence") or {}
    extra = terminal.get("oversized_quarantine_components") if isinstance(terminal, dict) else None
    return list(extra or [])


def _stored_source_workflow_projection(
    workflow_session: ImportSession, source_run_id: str
) -> dict | None:
    run_id = str(source_run_id or "").strip()
    if not run_id:
        return None
    stored = workflow_session.api_workflows.filter(run_id=run_id).first()
    if stored is not None and isinstance(stored.projection, dict):
        return stored.projection
    source = crm_duplicate_lineage_source_workflow(workflow_session)
    if (
        source is not None
        and str(source.run_id) == run_id
        and isinstance(source.projection, dict)
    ):
        return source.projection
    return None


def _authoritative_oversized_quarantine_components(
    projection: dict | None,
) -> list | None:
    """Return the list only when the projection includes the field."""

    if not isinstance(projection, dict):
        return None
    items = projection.get("oversized_quarantine_components")
    if isinstance(items, list):
        return list(items)
    terminal = projection.get("terminal_evidence") or {}
    if isinstance(terminal, dict) and "oversized_quarantine_components" in terminal:
        extra = terminal.get("oversized_quarantine_components")
        return list(extra or [])
    return None


def _load_source_oversized_quarantine_components(
    *,
    workflow_session: ImportSession,
    client,
    owner_session: str,
    source_run_id: str,
) -> tuple[list, str]:
    """Load oversized components from stored source projection, else live GET.

    Returns ``(components, error)``. A failed required load is a visible
    error string, never an empty list that looks like “no quarantines.”
    """

    run_id = str(source_run_id or "").strip()
    if not run_id:
        return [], ""
    stored = _authoritative_oversized_quarantine_components(
        _stored_source_workflow_projection(workflow_session, run_id)
    )
    if stored is not None:
        return stored, ""
    try:
        live = client.workflow(run_id, owner_session=owner_session)
    except CLIENT_ERRORS:
        return [], _OVERSIZED_SOURCE_UNAVAILABLE_COPY
    authoritative = _authoritative_oversized_quarantine_components(live)
    if authoritative is not None:
        return authoritative, ""
    return _oversized_quarantine_components_from_projection(live), ""


def _oversized_quarantine_only(projection: dict) -> bool:
    """Succeeded analysis with oversized quarantines and no reviewable groups."""

    summary = projection.get("summary") or {}
    if int(summary.get("oversized_quarantine_component_count") or 0) <= 0:
        return False
    if int(summary.get("reviewable_group_count") or 0) > 0:
        return False
    if int(summary.get("duplicate_group_count") or 0) > 0:
        return False
    return True


def _is_review_ready(projection: dict) -> bool:
    """True when analysis succeeded into review or a no-groups terminal."""

    if _is_failed(projection):
        return False
    status = str(projection.get("status") or "")
    stage = str(projection.get("stage") or "")
    summary = projection.get("summary") or {}
    if stage == "no_duplicate_groups" or summary.get("no_duplicate_groups"):
        return True
    if status in _REVIEW_READY_STATUSES:
        return True
    if stage in _REVIEW_READY_STAGES:
        return True
    if projection.get("review_handoff") is not None:
        return True
    if projection.get("decision") is not None:
        return True
    return False


def _progress_message(projection: dict) -> str:
    status = str(projection.get("status") or "")
    stage = str(projection.get("stage") or "")
    summary = projection.get("summary") or {}
    if projection.get("connection_removal_quarantine") is True:
        return _CONNECTION_REMOVAL_QUARANTINE_COPY
    if _is_failed(projection):
        err = projection.get("error") or {}
        code = str(err.get("code") or "workflow_failed")
        detail = str(err.get("message") or "").strip()
        if code == "abandoned_by_operator":
            return detail or "This analysis was stopped."
        if code == "crm_connection_removed_by_operator":
            return detail or "Stopped because its CRM connection was removed."
        if detail:
            return f"Analysis failed: {detail}"
        return (
            "Analysis failed. Return to the source step or open workflow details "
            "for the error, then start a new attempt."
        )
    if _oversized_quarantine_only(projection):
        records = int(summary.get("oversized_quarantine_record_count") or 0)
        components = int(summary.get("oversized_quarantine_component_count") or 0)
        if records and components:
            return (
                f"{records} records in {components} components were too large "
                "to auto-resolve and were quarantined."
            )
        return "Some records were too large to auto-resolve and were quarantined."
    if stage == "no_duplicate_groups" or summary.get("no_duplicate_groups"):
        count = summary.get("analyzed_record_count")
        if count is not None:
            return f"No duplicate groups found after analyzing {count} records."
        return "No duplicate groups found."
    if _is_review_ready(projection):
        return "Analysis complete. Opening review…"
    if status == "paused_verification" or stage == "paused_verification":
        analyzed = summary.get("analyzed_record_count")
        if analyzed is not None:
            return (
                "Finished a safe CRM read batch. Continuing with the next batch… "
                f"({analyzed} records collected so far)"
            )
        return "Finished a safe CRM read batch. Continuing with the next batch…"
    if status == "paused_unknown" or stage == "paused_unknown":
        return (
            "The last CRM read response was uncertain. Continue to reconcile the "
            "same saved read without starting over."
        )
    if status == "awaiting_effect_authorization":
        intent = projection.get("effect_intent") or {}
        track = intent.get("track") or ""
        if track == "reference_acquisition":
            return "Reading records from the selected CRM…"
        return "Waiting for the next authorization step…"
    if status in {"running", "ready"}:
        analyzed = summary.get("analyzed_record_count")
        if analyzed is not None:
            return f"Analyzing possible duplicates… ({analyzed} records read so far)"
        return "Analyzing possible duplicates…"
    if status == "unavailable":
        return "Could not refresh analysis status. Retry when the API is available."
    return "Working…"


def _read_progress_noun(entity_family: str) -> str | None:
    """RAP-2 operator noun from the journey entity; None if unknown."""

    family = str(entity_family or "").strip().lower()
    if family in {"company", "account", "accounts", "companies"}:
        return "Companies"
    if family in {"person", "people", "contact", "contacts"}:
        return "People"
    return None


def _read_progress_collected_count(projection: dict[str, Any]) -> int | None:
    raw = (projection or {}).get("reference_acquisition_progress")
    if not isinstance(raw, dict):
        return None
    count = raw.get("collected_count")
    if isinstance(count, bool) or not isinstance(count, int) or count < 0:
        return None
    return count


def _read_progress_context(
    projection: dict[str, Any], options: dict[str, Any]
) -> dict[str, Any]:
    """Template extras for the live CRM-read count. Absent when either half fails."""

    count = _read_progress_collected_count(projection)
    noun = _read_progress_noun((options or {}).get("entity_family"))
    analysis = _analysis_progress_context(projection)
    if count is None or noun is None:
        return {
            "read_progress_count": None,
            "read_progress_count_display": None,
            "read_progress_noun": None,
            "read_progress_copy": None,
            **analysis,
        }
    display = f"{count:,}"
    copy = f"{display} {noun} read so far"
    return {
        "read_progress_count": count,
        "read_progress_count_display": display,
        "read_progress_noun": noun,
        "read_progress_copy": copy,
        **analysis,
    }


def _analysis_progress_context(projection: dict[str, Any]) -> dict[str, Any]:
    raw = (projection or {}).get("duplicate_analysis_progress")
    empty = {
        "analysis_progress_stage": None,
        "analysis_progress_copy": None,
        "analysis_progress_review_ready": None,
        "analysis_progress_review_window_ready": None,
        "analysis_progress_review_groups_ready": None,
        "analysis_progress_review_groups_total": None,
    }
    if not isinstance(raw, dict):
        return empty
    stage = raw.get("stage")
    completed = raw.get("completed_count")
    total = raw.get("total_count")
    unit = raw.get("count_unit")
    if stage not in {
        "routing_domain_groups",
        "building_evidence",
        "finalizing_groups",
        "ranking_survivors",
        "building_recommendations",
        "building_approval_bundle",
        "building_review_materials",
        "complete",
    }:
        return empty
    if (
        isinstance(completed, bool)
        or isinstance(total, bool)
        or not isinstance(completed, int)
        or not isinstance(total, int)
        or completed < 0
        or total < 0
        or completed > total
    ):
        return empty
    if stage == "building_evidence" and unit == "candidate_pairs":
        copy = (
            f"Comparing possible Company matches — {completed:,} of {total:,} "
            "comparisons checked"
        )
    elif stage == "routing_domain_groups":
        copy = "Routing duplicate groups by domain"
    elif stage in {"finalizing_groups", "ranking_survivors"}:
        copy = (
            "Finalizing duplicate groups"
            if stage == "finalizing_groups"
            else "Ranking survivors"
        )
    elif stage == "building_recommendations":
        copy = f"Building merge recommendations — {completed:,} of {total:,} groups"
    elif stage == "building_approval_bundle":
        copy = f"Preparing group approvals — {completed:,} of {total:,} groups"
    elif stage == "building_review_materials":
        ready = raw.get("review_groups_ready", completed)
        ready_total = raw.get("review_groups_total", total)
        if (
            isinstance(ready, int)
            and isinstance(ready_total, int)
            and not isinstance(ready, bool)
            and not isinstance(ready_total, bool)
        ):
            copy = (
                f"Preparing groups for review — {ready:,} of {ready_total:,} groups"
            )
        else:
            copy = (
                f"Preparing groups for review — {completed:,} of {total:,} groups"
            )
    elif stage == "complete":
        copy = f"Preparing duplicate groups — {completed:,} of {total:,} groups"
    else:
        return empty
    return {
        "analysis_progress_stage": stage,
        "analysis_progress_copy": copy,
        "analysis_progress_review_ready": raw.get("review_ready") is True,
        "analysis_progress_review_window_ready": raw.get("review_window_ready")
        is True,
        "analysis_progress_review_groups_ready": raw.get("review_groups_ready"),
        "analysis_progress_review_groups_total": raw.get("review_groups_total"),
    }


def _journey_workflow_display(
    journey: dict[str, Any], projection: dict[str, Any] | None
) -> tuple[str, str]:
    """Return customer-facing workflow copy and the public failure code."""

    if journey.get("connection_removal_quarantine") is True or (
        isinstance(projection, dict)
        and projection.get("connection_removal_quarantine") is True
    ):
        return _CONNECTION_REMOVAL_QUARANTINE_COPY, "connection_removal_quarantine"
    error = projection.get("error") if isinstance(projection, dict) else None
    error = error if isinstance(error, dict) else {}
    code = str(error.get("code") or "")
    message = str(error.get("message") or "").strip()
    if code == "abandoned_by_operator":
        return message or "This analysis was stopped.", code
    if code == "crm_connection_removed_by_operator":
        return message or "Stopped because its CRM connection was removed.", code
    status = str(journey.get("workflow_status") or journey.get("run_id") or "")
    if status == "failed":
        return message or "Analysis failed.", code or "workflow_failed"
    if status == "unavailable":
        return "Could not refresh analysis status.", ""
    return status, ""


def _local_session_for_journey(
    *, owner_id: UUID, journey_id: str, run_id: str
) -> ImportSession | None:
    sessions = ImportSession.objects.filter(
        owner_id=owner_id,
        archived_at__isnull=True,
        product_key="easyimports.duplicate_resolution",
    ).order_by("-updated_at")
    for session in sessions:
        options = dict(session.options or {})
        if str(options.get("crm_journey_id") or "").strip() == journey_id:
            return session
        known_runs = {
            str(options.get(name) or "").strip()
            for name in ("run_id", "source_run_id", "review_run_id")
        }
        lease = getattr(session, "merge_plan_lease", None)
        if lease is not None:
            known_runs.add(str(lease.continuation_run_id or "").strip())
        if run_id and run_id in known_runs:
            return session
    return None


def _has_crm_write_authority(projection: dict[str, Any]) -> bool:
    intent = projection.get("effect_intent")
    if intent is not None:
        if not isinstance(intent, dict):
            return True
        intent_track = str(intent.get("track") or "")
        if intent_track in _CRM_WRITE_TRACKS:
            return True
        if intent_track not in _NON_CRM_WRITE_TRACKS:
            return True
    grants = projection.get("effect_grants")
    if not isinstance(grants, list):
        return True
    for grant in grants:
        if not isinstance(grant, dict):
            return True
        track = str(grant.get("track") or "")
        if track in _CRM_WRITE_TRACKS or track not in _NON_CRM_WRITE_TRACKS:
            return True
    return False


def _is_proven_non_continuation(
    *, local_session: ImportSession | None, run_id: str
) -> bool:
    if local_session is None:
        return False
    options = dict(local_session.options or {})
    source_run_id = str(
        options.get("source_run_id") or options.get("run_id") or ""
    ).strip()
    if not source_run_id or source_run_id != run_id:
        return False
    lease = getattr(local_session, "merge_plan_lease", None)
    continuation = (
        str(lease.continuation_run_id or "").strip() if lease is not None else ""
    )
    return not continuation or continuation != run_id


def _abandon_confirmation_copy(
    projection: dict[str, Any],
    *,
    local_session: ImportSession | None,
    run_id: str,
) -> tuple[str, bool]:
    proven_safe = (
        projection.get("remote_outcome") == "none"
        and _is_proven_non_continuation(local_session=local_session, run_id=run_id)
        and not _has_crm_write_authority(projection)
    )
    if proven_safe:
        return _ABANDON_SAFE_CONFIRMATION, False
    return _ABANDON_UNCERTAIN_CONFIRMATION, True


def _reconcile_pending_implied_authorization(
    *,
    journal: ImportSession,
    client: EasyImportsApiClient,
    run_id: str,
) -> tuple[bool, bool]:
    """Reconcile PENDING first-batch implied-authorization mutations.

    Returns ``(processing, reconciled_success)``. GET-safe: polls mutation
    status only; never dispatches a new apply.
    """

    apply_mutations = journal.api_mutations.filter(
        mutation_kind="crm_duplicate_implied_reference_authorization",
        resource_identity=run_id,
    )
    processing = False
    reconciled_success = False
    for apply_mutation in apply_mutations.filter(
        state=ApiMutation.State.PENDING,
    ).order_by("created_at", "id"):
        if apply_mutation.http_status not in {202, 409}:
            if apply_mutation.retry_available:
                recovered = ApiMutation.objects.filter(
                    pk=apply_mutation.pk,
                    state=ApiMutation.State.PENDING,
                    lease_token=apply_mutation.lease_token,
                    lease_expires_at=apply_mutation.lease_expires_at,
                ).update(
                    state=ApiMutation.State.UNKNOWN,
                    http_status=None,
                    error_message=(
                        "The CRM read stopped before dispatch completed. "
                        "Retrying the saved exact request."
                    ),
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=timezone.now(),
                )
                if not recovered:
                    processing = True
            else:
                processing = True
            continue
        operation_status = client.mutation_status(apply_mutation.idempotency_key)
        if operation_status["mutation_kind"] != apply_mutation.mutation_kind:
            raise ApiConsistencyError(
                "The API mutation status named a different command."
            )
        if operation_status["status"] == "pending":
            if operation_status["retryable"]:
                ApiMutation.objects.filter(
                    pk=apply_mutation.pk,
                    state=ApiMutation.State.PENDING,
                ).update(
                    state=ApiMutation.State.UNKNOWN,
                    http_status=None,
                    response_json=operation_status,
                    error_message=(
                        "The API worker no longer owns this CRM read. "
                        "Retrying the saved exact request."
                    ),
                    lease_token=None,
                    lease_expires_at=None,
                    updated_at=timezone.now(),
                )
            else:
                processing = True
            continue
        result = client.reconcile_mutation_status(
            apply_mutation,
            operation_status,
        )
        if result is None:
            processing = True
        elif result.mutation.state == ApiMutation.State.COMPLETED:
            reconciled_success = True
    latest = apply_mutations.order_by("-created_at", "-id").first()
    if latest is not None and latest.state == ApiMutation.State.PENDING:
        processing = True
    return processing, reconciled_success


def _resume_reference_scan(
    *,
    journal: ImportSession,
    owner_session: str,
    client: EasyImportsApiClient,
    form_instance: UUID,
    run_id: str,
    expected_revision: int,
) -> dict:
    """Resume one bounded read-only acquisition cycle at a verification pause."""

    route = f"/v1/workflows/{run_id}/effect-resumptions"
    recoverable = (
        journal.api_mutations.filter(
            mutation_kind="resume_effect",
            resource_identity=run_id,
            state=ApiMutation.State.UNKNOWN,
        )
        .order_by("created_at", "id")
        .first()
    )
    if recoverable is not None:
        frozen_request = recoverable.request_json
        if (
            recoverable.route != route
            or not isinstance(frozen_request, dict)
            or str(frozen_request.get("owner_session") or "") != owner_session
        ):
            raise ApiConsistencyError(
                "The saved CRM read retry does not match this workflow."
            )
        result = client.dispatch(recoverable, explicit_retry=True)
        if result.mutation.state == ApiMutation.State.REJECTED:
            raise ApiRejectedError(
                str(result.mutation.error_code or "rejected"),
                str(
                    result.mutation.error_message
                    or "The API rejected this CRM batch read."
                ),
                details=result.response,
            )
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("CRM batch retry response is malformed.")
        return result.response

    step = f"resume-read-r{int(expected_revision)}"
    return _dispatch_json_mutation(
        journal=journal,
        # Use the canonical kind so EasyImportsApiClient sends
        # Prefer: respond-async. One bounded provider batch then runs outside
        # the browser request and is observed through workflow polling.
        mutation_kind="resume_effect",
        route=route,
        body={"expected_revision": int(expected_revision)},
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(form_instance, step),
        logical_action_identity=_reference_resume_identity(
            owner_session=owner_session,
            run_id=run_id,
            expected_revision=expected_revision,
        ),
        resource_identity=run_id,
    )


def _reference_resume_identity(
    *, owner_session: str, run_id: str, expected_revision: int
) -> str:
    return (
        f"crm-dupe-4a:resume-read-r{int(expected_revision)}:"
        f"{owner_session}:{run_id}"
    )


def _issue_start_token(
    *,
    owner_id: UUID,
    journal: ImportSession,
    owner_session: str,
) -> str:
    identity = f"crm-dupe-4a:start:{owner_session}"
    return issue_form_token(
        owner_id=owner_id,
        session=journal,
        action_kind=START_ACTION,
        logical_action_identity=identity,
        logical_action_generation=action_generation(journal, identity),
    )


@never_cache
@require_http_methods(["GET", "POST"])
def crm_duplicate_journey(request):
    """Screen 1: connection, entity, source branch; start redesigned journey."""

    client = EasyImportsApiClient()
    owner_session = _owner_session(request)
    owner_id = owner_id_for_request(request)
    journal = _journey_session(request)
    providers: list[dict] = []
    connections: list[dict] = []
    journeys: list[dict] = []
    load_error = None
    api_technical = None
    replacement_form_token = ""
    try:
        providers = client.crm_providers().get("providers", [])
        connections = client.crm_connections(owner_session=owner_session).get(
            "connections", []
        )
        journeys = client.crm_duplicate_journeys(owner_session=owner_session).get(
            "journeys", []
        )
        dismissed = _dismissed_journey_ids(journal)
        journeys = [
            item
            for item in journeys
            if str(item.get("journey_id") or "").strip() not in dismissed
        ]
        journeys = _annotate_journeys_for_resume(
            request,
            journeys,
            client=client,
            owner_session=owner_session,
        )
    except (ApiUnavailableError, ApiRejectedError) as exc:
        load_error = str(exc)
        messages.error(request, str(exc))
        api_technical = api_error_technical_details(exc)

    connected = [item for item in connections if item.get("status") == "connected"]
    by_id = _connections_by_id(connected)
    form_token = _issue_start_token(
        owner_id=owner_id,
        journal=journal,
        owner_session=owner_session,
    )
    form = CrmDuplicateJourneyForm(
        request.POST or None,
        request.FILES or None,
        connections=connected,
        initial={
            "entity_family": "company",
            "source_mode": "acquire_all",
            "form_token": form_token,
        },
    )
    if request.method != "POST":
        form.fields["form_token"].initial = form_token

    if request.method == "POST" and form.is_valid():
        mode = form.cleaned_data["source_mode"]
        try:
            start_identity = f"crm-dupe-4a:start:{owner_session}"
            decoded = decode_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=journal,
                action_kind=START_ACTION,
            )
            claims = validate_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=journal,
                action_kind=START_ACTION,
                logical_action_identity=start_identity,
                logical_action_generation=int(
                    decoded.get("logical_action_generation") or 0
                ),
            )
            form_instance = claims["form_instance"]
            connection = by_id.get(str(form.cleaned_data["connection_id"]))
            ceiling = _duplicate_execution_ceiling(connection)
            if mode == "uploaded_population":
                return _handle_uploaded_population_start(
                    request,
                    form=form,
                    journal=journal,
                    client=client,
                    owner_session=owner_session,
                    form_instance=form_instance,
                    execution_ceiling=ceiling,
                )
            if mode == "acquire_all":
                return _handle_crm_scan_start(
                    request,
                    form=form,
                    journal=journal,
                    client=client,
                    owner_session=owner_session,
                    form_instance=form_instance,
                    execution_ceiling=ceiling,
                )
            return _handle_legacy_journey_start(
                request,
                form=form,
                journal=journal,
                client=client,
                owner_session=owner_session,
                form_instance=form_instance,
                execution_ceiling=ceiling,
            )
        except MutationReuseError:
            # A desktop/browser restore can revive a previously submitted DOM
            # with its old root form instance. Keep the operator's selections,
            # but replace the exhausted token so the next click is an explicit
            # new attempt instead of an endless changed-payload collision.
            replacement_form_token = _issue_start_token(
                owner_id=owner_id,
                journal=journal,
                owner_session=owner_session,
            )
            messages.warning(
                request,
                "This page belonged to an earlier duplicate attempt. "
                "EasyImports refreshed it; review the selections and click "
                "Find duplicates again.",
            )
        except CLIENT_ERRORS as exc:
            messages.error(request, str(exc))
            # Keep the submitted token so exact retry of the same attempt works.
            # A new token is only minted on GET or intentional start-over.

    submitted_token = ""
    if request.method == "POST" and form.data is not None:
        submitted_token = str(form.data.get("form_token") or "")
    return render(
        request,
        "importer/crm_duplicate_journey.html",
        {
            "form": form,
            "form_token": (
                replacement_form_token
                or submitted_token
                or form["form_token"].value()
                or form_token
            ),
            "providers": providers,
            "connections": connections,
            "connected_count": len(connected),
            "journeys": journeys,
            "load_error": load_error,
            "api_technical": api_technical,
            "auto_merge_info_tip": AUTO_MERGE_INFO_TIP,
            "threshold_info_tip": THRESHOLD_INFO_TIP,
            "company_score_bands": COMPANY_SCORE_BANDS,
            "person_score_bands": PERSON_SCORE_BANDS,
            "person_audit_note": PERSON_AUDIT_NOTE,
        },
    )


@require_POST
def crm_duplicate_journey_clear(request, journey_id):
    """Clear one journey locally while preserving API audit evidence."""

    value = str(journey_id or "").strip()
    journal = _journey_session(request)
    _remember_dismissed_journey(journal, value)

    owner_id = owner_id_for_request(request)
    for session in ImportSession.objects.filter(
        owner_id=owner_id,
        archived_at__isnull=True,
        product_key="easyimports.duplicate_resolution",
    ):
        options = dict(session.options or {})
        if str(options.get("crm_journey_id") or "").strip() == value:
            archive_session(session)

    messages.success(
        request,
        "Cleared this journey from the recent list. Its API audit trail and "
        "any CRM results were not deleted.",
    )
    return redirect("importer:crm_duplicate_journey")


def _abandon_logical_identity(
    *, owner_session: str, journey_id: str, run_id: str, revision: int
) -> str:
    return f"crm-dupe-abandon:{owner_session}:{journey_id}:{run_id}:r{revision}"


def _reference_acquisition_recovery_identity(
    *, owner_session: str, run_id: str, revision: int
) -> str:
    return (
        f"crm-dupe-reference-acquisition-recovery:{owner_session}:{run_id}:r{revision}"
    )


def _clearable_reference_acquisition_pause(projection: dict[str, Any]) -> bool:
    if str(projection.get("status") or "") not in {
        "paused_unknown",
        "paused_verification",
    }:
        return False
    diagnostic = projection.get("paused_effect_diagnostic")
    return isinstance(diagnostic, dict) and str(diagnostic.get("code") or "") == (
        REFERENCE_ACQUISITION_RESET_REQUIRED
    )


def _pending_reference_acquisition_recovery(
    journal: ImportSession,
    *,
    run_id: str,
) -> ApiMutation | None:
    return (
        journal.api_mutations.filter(
            mutation_kind=REFERENCE_ACQUISITION_RECOVERY_KIND,
            resource_identity=run_id,
            state__in=(ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN),
        )
        .order_by("-created_at", "-id")
        .first()
    )


@never_cache
@require_http_methods(["GET", "POST"])
def crm_duplicate_journey_abandon(request, journey_id):
    """Explicitly confirm and journal Phase 1's per-run abandon command."""

    value = str(journey_id or "").strip()
    client = EasyImportsApiClient()
    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    journal = _journey_session(request)
    try:
        journey = client.crm_duplicate_journey(value, owner_session=owner_session)
    except (ApiUnavailableError, ApiRejectedError) as exc:
        messages.error(request, str(exc))
        return redirect("importer:crm_duplicate_journey")
    run_id = str(journey.get("run_id") or "").strip()
    if not run_id:
        messages.error(request, "This journey has no analysis run to stop.")
        return redirect("importer:crm_duplicate_journey")

    local_session = _local_session_for_journey(
        owner_id=owner_id,
        journey_id=value,
        run_id=run_id,
    )
    projection: dict[str, Any] | None = None

    if request.method == "GET":
        try:
            projection = client.workflow(run_id, owner_session=owner_session)
        except (ApiUnavailableError, ApiRejectedError) as exc:
            messages.error(request, str(exc))
            return redirect("importer:crm_duplicate_journey")
        status = str(projection.get("status") or "")
        if status not in _NON_TERMINAL_WORKFLOW_STATUSES:
            display, _code = _journey_workflow_display(journey, projection)
            messages.info(request, display or "This analysis is already finished.")
            return redirect("importer:crm_duplicate_journey")
        revision = int(projection.get("revision") or 0)
        if revision < 1:
            messages.error(request, "This analysis has no valid revision to stop.")
            return redirect("importer:crm_duplicate_journey")
        logical_identity = _abandon_logical_identity(
            owner_session=owner_session,
            journey_id=value,
            run_id=run_id,
            revision=revision,
        )
        generation = action_generation(journal, logical_identity)
        token_workflow = SimpleNamespace(
            run_id=run_id,
            revision=revision,
            projection_digest=projection_digest(projection),
        )
        form_token = issue_form_token(
            owner_id=owner_id,
            session=journal,
            action_kind=ABANDON_ACTION,
            workflow=token_workflow,
            action_id=value,
            logical_action_identity=logical_identity,
            logical_action_generation=generation,
        )
    else:
        form_token = str(request.POST.get("form_token") or "")
        try:
            claims = decode_form_token(
                form_token,
                owner_id=owner_id,
                session=journal,
                action_kind=ABANDON_ACTION,
            )
            claimed_run_id = str(claims.get("run_id") or "").strip()
            revision = int(claims.get("revision") or 0)
            if claims.get("action_id") != value or claimed_run_id != run_id:
                raise FormTokenError("This form is stale. Reload it before continuing.")
            if revision < 1:
                raise FormTokenError("This form has no valid workflow revision.")
            logical_identity = _abandon_logical_identity(
                owner_session=owner_session,
                journey_id=value,
                run_id=run_id,
                revision=revision,
            )
            generation = action_generation(journal, logical_identity)
            token_workflow = SimpleNamespace(
                run_id=run_id,
                revision=revision,
                projection_digest=str(claims.get("projection_digest") or ""),
            )
            validated = validate_form_token(
                form_token,
                owner_id=owner_id,
                session=journal,
                action_kind=ABANDON_ACTION,
                workflow=token_workflow,
                action_id=value,
                logical_action_identity=logical_identity,
                logical_action_generation=generation,
            )
            receipt = _dispatch_json_mutation(
                journal=journal,
                mutation_kind="abandon_run",
                route=f"/v1/workflows/{quote(run_id, safe='')}/abandon",
                body={"expected_revision": revision},
                owner_session=owner_session,
                client=client,
                form_instance=validated["form_instance"],
                logical_action_identity=logical_identity,
                logical_action_generation=generation,
                resource_identity=run_id,
            )
            if receipt.get("outcome") != "accepted":
                raise ApiRejectedError(
                    str(receipt.get("error_code") or "rejected"),
                    str(
                        receipt.get("message") or "This analysis could not be stopped."
                    ),
                    details=receipt,
                )
        except CLIENT_ERRORS + (ApiOperationInProgressError,) as exc:
            messages.error(request, str(exc))
            return redirect("importer:crm_duplicate_journey_abandon", journey_id=value)
        messages.success(request, "This analysis was stopped.")
        return redirect("importer:crm_duplicate_journey")

    confirmation_copy, remote_outcome_uncertain = _abandon_confirmation_copy(
        projection,
        local_session=local_session,
        run_id=run_id,
    )
    return render(
        request,
        "importer/crm_duplicate_journey_abandon.html",
        {
            "journey": journey,
            "projection": projection,
            "form_token": form_token,
            "confirmation_copy": confirmation_copy,
            "remote_outcome_uncertain": remote_outcome_uncertain,
        },
    )


@never_cache
@require_http_methods(["GET", "POST"])
def crm_duplicate_reference_acquisition_recovery(request, session_id):
    """Confirm and exactly retry one clearable saved-progress recovery."""

    workflow_session = _workflow_session_for_request(request, session_id)
    options = dict(workflow_session.options or {})
    active = getattr(workflow_session, "active_workflow", None)
    run_id = str(getattr(active, "run_id", "") or "") or str(
        options.get("run_id") or ""
    )
    if not run_id:
        messages.error(request, "This journey has no analysis run to recover.")
        return redirect("importer:crm_duplicate_journey")

    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    journal = _mutation_journal_for_session(workflow_session)
    client = EasyImportsApiClient()
    projection: dict[str, Any] | None = None
    retry_mutation = _pending_reference_acquisition_recovery(
        journal,
        run_id=run_id,
    )

    if request.method == "GET":
        if retry_mutation is None:
            try:
                projection = client.workflow(run_id, owner_session=owner_session)
            except (ApiUnavailableError, ApiRejectedError) as exc:
                messages.error(request, str(exc))
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
            if not _clearable_reference_acquisition_pause(projection):
                messages.error(
                    request,
                    "This analysis no longer has clearable saved scan data.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
            revision = int(projection.get("revision") or 0)
            if revision < 1:
                messages.error(
                    request, "This analysis has no valid revision to recover."
                )
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
            logical_identity = _reference_acquisition_recovery_identity(
                owner_session=owner_session,
                run_id=run_id,
                revision=revision,
            )
            generation = action_generation(journal, logical_identity)
            projection_digest_value = projection_digest(projection)
        else:
            request_json = (
                retry_mutation.request_json
                if isinstance(retry_mutation.request_json, dict)
                else {}
            )
            revision = int(request_json.get("expected_revision") or 0)
            logical_identity = str(retry_mutation.logical_action_identity or "")
            generation = int(retry_mutation.logical_action_generation)
            projection_digest_value = ""
            if revision < 1 or not logical_identity:
                messages.error(request, "Saved recovery retry state is invalid.")
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
        token_workflow = SimpleNamespace(
            run_id=run_id,
            revision=revision,
            projection_digest=projection_digest_value,
        )
        form_token = issue_form_token(
            owner_id=owner_id,
            session=journal,
            action_kind=REFERENCE_ACQUISITION_RECOVERY_ACTION,
            workflow=token_workflow,
            action_id=str(workflow_session.id),
            logical_action_identity=logical_identity,
            logical_action_generation=generation,
        )
        return render(
            request,
            "importer/crm_duplicate_reference_acquisition_recovery.html",
            {
                "session": workflow_session,
                "projection": projection,
                "run_id": run_id,
                "revision": revision,
                "form_token": form_token,
                "retrying": retry_mutation is not None,
            },
        )

    form_token = str(request.POST.get("form_token") or "")
    try:
        claims = decode_form_token(
            form_token,
            owner_id=owner_id,
            session=journal,
            action_kind=REFERENCE_ACQUISITION_RECOVERY_ACTION,
        )
        claimed_run_id = str(claims.get("run_id") or "").strip()
        revision = int(claims.get("revision") or 0)
        if (
            claims.get("action_id") != str(workflow_session.id)
            or claimed_run_id != run_id
            or revision < 1
        ):
            raise FormTokenError("This form is stale. Reload it before continuing.")
        logical_identity = _reference_acquisition_recovery_identity(
            owner_session=owner_session,
            run_id=run_id,
            revision=revision,
        )
        generation = action_generation(journal, logical_identity)
        token_workflow = SimpleNamespace(
            run_id=run_id,
            revision=revision,
            projection_digest=str(claims.get("projection_digest") or ""),
        )
        validated = validate_form_token(
            form_token,
            owner_id=owner_id,
            session=journal,
            action_kind=REFERENCE_ACQUISITION_RECOVERY_ACTION,
            workflow=token_workflow,
            action_id=str(workflow_session.id),
            logical_action_identity=logical_identity,
            logical_action_generation=generation,
        )
        receipt = _dispatch_json_mutation(
            journal=journal,
            mutation_kind=REFERENCE_ACQUISITION_RECOVERY_KIND,
            route=(
                f"/v1/workflows/{quote(run_id, safe='')}"
                "/reference-acquisition-recoveries"
            ),
            body={"expected_revision": revision},
            owner_session=owner_session,
            client=client,
            form_instance=validated["form_instance"],
            logical_action_identity=logical_identity,
            logical_action_generation=generation,
            resource_identity=run_id,
        )
        result = receipt.get("result")
        if (
            receipt.get("outcome") != "accepted"
            or receipt.get("command_kind") != REFERENCE_ACQUISITION_RECOVERY_KIND
            or result
            != {
                "recovery_contract": REFERENCE_ACQUISITION_RECOVERY_CONTRACT,
                "state_reset": True,
                "resume_dispatched": True,
            }
        ):
            raise ApiConsistencyError(
                "Reference-acquisition recovery returned an invalid receipt."
            )
    except CLIENT_ERRORS + (ApiOperationInProgressError,) as exc:
        messages.error(request, str(exc))
        return redirect(
            "importer:crm_duplicate_reference_acquisition_recovery",
            session_id=workflow_session.id,
        )

    messages.success(request, REFERENCE_ACQUISITION_RECOVERY_SUCCESS)
    return redirect(
        "importer:crm_duplicate_journey_progress",
        session_id=workflow_session.id,
    )


def _handle_crm_scan_start(
    request,
    *,
    form: CrmDuplicateJourneyForm,
    journal: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    form_instance: UUID,
    execution_ceiling: str,
):
    """Find-in-CRM: start journey + freeze read grant + auto-apply RA."""

    auto_merge_t = _form_auto_merge_threshold(form)
    matching_mode = _form_matching_mode(form)
    body: dict[str, Any] = {
        "connection_id": form.cleaned_data["connection_id"],
        "entity_family": form.cleaned_data["entity_family"],
        "source_mode": "acquire_all",
        "duplicate_execution_maximum": execution_ceiling,
        "matching_mode": matching_mode,
    }
    if auto_merge_t is not None:
        body["auto_merge_min_confidence"] = auto_merge_t
    attempt_intent = _root_attempt_intent(
        source_mode="acquire_all",
        connection_id=body["connection_id"],
        entity_family=body["entity_family"],
        duplicate_execution_maximum=execution_ceiling,
        auto_merge_min_confidence=auto_merge_t,
        matching_mode=matching_mode,
    )
    # Fail closed before API work when this root form already claimed a
    # different source/connection/entity/ceiling.
    _require_compatible_root_attempt(
        journal, form_instance=form_instance, intent=attempt_intent
    )
    journey_resource = (
        f"scan:{body['connection_id']}:{body['entity_family']}:m{matching_mode}"
    )
    if auto_merge_t is not None:
        journey_resource = f"{journey_resource}:t{auto_merge_t}"
    # Idempotent API steps first (safe exact retry via per-step form instances).
    journey = _dispatch_json_mutation(
        journal=journal,
        mutation_kind="crm_duplicate_journey_start",
        route="/v1/crm/duplicate-journeys",
        body=body,
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(form_instance, "journey"),
        logical_action_identity=_step_identity(
            step="journey",
            owner_session=owner_session,
            form_instance=form_instance,
            resource=journey_resource,
        ),
    )
    grant = _create_read_grant(
        journal=journal,
        owner_session=owner_session,
        client=client,
        root_form_instance=form_instance,
        population_source="crm_scan",
        connection_id=body["connection_id"],
        entity_family=body["entity_family"],
        auto_merge_min_confidence=auto_merge_t,
    )
    extra = {
        "read_grant_id": grant["grant_id"],
        "population_source": "crm_scan",
        "orchestrator_form_instance": str(form_instance),
        "duplicate_execution_maximum": execution_ceiling,
        "mutation_journal_id": str(journal.id),
        "apply_root_form_instance": str(form_instance),
        "auto_merge_min_confidence": auto_merge_t,
        "matching_mode": matching_mode,
    }

    def _factory() -> ImportSession:
        return _create_workflow_session_shell(
            request, journey=journey, extra_options=extra
        )

    # Claim the local workflow shell BEFORE async implied-authorization so a
    # 202 / pending apply still leaves a discoverable progress page.
    workflow_session, _created = _claim_or_get_attempt_session(
        request,
        journal,
        form_instance=form_instance,
        intent=attempt_intent,
        factory=_factory,
    )
    # Fill missing option fields on a same-intent resumed shell only.
    # Never overwrite frozen attempt identity fields with different values
    # (claim already rejected intent mismatches).
    opts = dict(workflow_session.options or {})
    changed = False
    for key, value in extra.items():
        if opts.get(key) in (None, "") and value not in (None, ""):
            opts[key] = value
            changed = True
        elif opts.get(key) != value and key not in _ATTEMPT_INTENT_KEYS:
            opts[key] = value
            changed = True
    if not opts.get("run_id"):
        opts["run_id"] = journey["run_id"]
        changed = True
    if changed:
        workflow_session.options = opts
        workflow_session.save(update_fields=["options", "updated_at"])

    apply_pending = False
    try:
        _apply_implied_read(
            journal=journal,
            owner_session=owner_session,
            client=client,
            root_form_instance=form_instance,
            grant_id=grant["grant_id"],
            run_id=journey["run_id"],
        )
    except (ApiOperationInProgressError, MutationBusyError):
        apply_pending = True

    if not apply_pending:
        _refresh_workflow_projection(
            workflow_session,
            run_id=journey["run_id"],
            owner_session=owner_session,
            client=client,
        )
        messages.success(
            request,
            "Duplicate analysis started. Reading records from the connected CRM…",
        )
    else:
        messages.info(
            request,
            "EasyImports is reading records from the connected CRM in the "
            "background. This page will update automatically.",
        )
    return redirect(
        "importer:crm_duplicate_journey_progress",
        session_id=workflow_session.id,
    )


def _handle_uploaded_population_start(
    request,
    *,
    form: CrmDuplicateJourneyForm,
    journal: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    form_instance: UUID,
    execution_ceiling: str,
):
    """Upload branch: atomic claim of draft, then upload I/O, then map."""

    auto_merge_t = _form_auto_merge_threshold(form)
    matching_mode = _form_matching_mode(form)
    attempt_intent = _root_attempt_intent(
        source_mode="uploaded_population",
        connection_id=form.cleaned_data["connection_id"],
        entity_family=form.cleaned_data["entity_family"],
        duplicate_execution_maximum=execution_ceiling,
        auto_merge_min_confidence=auto_merge_t,
        matching_mode=matching_mode,
    )
    _require_compatible_root_attempt(
        journal, form_instance=form_instance, intent=attempt_intent
    )

    def _factory() -> ImportSession:
        # Single INSERT: pre-assign id so mutation_journal_id can freeze without
        # a second save(). Claim-level factory retries must not orphan a draft
        # that survived a failed follow-up write under SQLite locks.
        draft_id = uuid4()
        return ImportSession.objects.create(
            id=draft_id,
            owner_id=owner_id_for_request(request),
            operator_label=(
                f"CRM duplicates · {_entity_label(form.cleaned_data['entity_family'])} · "
                "Map Record ID"
            ),
            product_key="easyimports.duplicate_resolution",
            target_provider_id="",
            status=ImportSession.Status.CREATED,
            options={
                "redesign_phase": "4a",
                "population_source": "uploaded_population",
                "connection_id": form.cleaned_data["connection_id"],
                "entity_family": form.cleaned_data["entity_family"],
                "source_mode": "uploaded_population",
                "duplicate_execution_maximum": execution_ceiling,
                "orchestrator_form_instance": str(form_instance),
                "mutation_journal_id": str(draft_id),
                "auto_merge_min_confidence": auto_merge_t,
                "matching_mode": matching_mode,
            },
        )

    draft, _created = _claim_or_get_attempt_session(
        request,
        journal,
        form_instance=form_instance,
        intent=attempt_intent,
        factory=_factory,
    )

    opts = dict(draft.options or {})
    if opts.get("run_id") or draft.active_workflow_id:
        return redirect(
            "importer:crm_duplicate_journey_progress",
            session_id=draft.id,
        )
    if opts.get("population_upload_id"):
        return redirect(
            "importer:crm_duplicate_population_map",
            session_id=draft.id,
        )

    uploaded = form.cleaned_data["population_file"]
    upload_identity = f"upload:crm_duplicate_population:{draft.id}"
    generation = action_generation(draft, upload_identity)
    registration = save_and_register_upload(
        session=draft,
        role="crm_duplicate_population",
        uploaded=uploaded,
        form_instance=_step_form_instance(form_instance, "upload"),
        logical_action_generation=generation,
        csv_encoding="utf-8",
        xlsx_sheet_index=0,
        client=client,
    )
    if registration.response is None:
        raise ApiRejectedError(
            "upload_incomplete",
            "The population upload could not be registered.",
        )
    upload_id = registration.response["upload_id"]
    population = _dispatch_json_mutation(
        journal=draft,
        mutation_kind="crm_duplicate_population_upload",
        route="/v1/crm/duplicate-population-uploads",
        body={
            "upload_id": upload_id,
            "entity_family": form.cleaned_data["entity_family"],
            "connection_id": form.cleaned_data["connection_id"],
        },
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(form_instance, "population"),
        logical_action_identity=_step_identity(
            step="population",
            owner_session=owner_session,
            form_instance=form_instance,
            resource=str(draft.id),
        ),
        resource_identity=str(draft.id),
    )
    draft.options = {
        **dict(draft.options or {}),
        "population_upload_id": population["population_upload_id"],
        "upload_id": upload_id,
        "source_headers": list(population.get("source_headers") or []),
        "preview_rows": list(population.get("preview_rows") or []),
        "suggested_source_column": population.get("suggested_source_column"),
        "filename": population.get("filename"),
        "row_count": population.get("row_count"),
        "mutation_journal_id": str(draft.id),
        "duplicate_execution_maximum": execution_ceiling,
    }
    draft.save(update_fields=["options", "updated_at"])
    messages.success(
        request,
        "File uploaded. Map the CRM record ID column to continue.",
    )
    return redirect(
        "importer:crm_duplicate_population_map",
        session_id=draft.id,
    )


def _handle_legacy_journey_start(
    request,
    *,
    form: CrmDuplicateJourneyForm,
    journal: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    form_instance: UUID,
    execution_ceiling: str,
):
    """candidate_upload / selected_ids compatibility path → existing workflow."""

    matching_mode = _form_matching_mode(form)
    body = {
        "connection_id": form.cleaned_data["connection_id"],
        "entity_family": form.cleaned_data["entity_family"],
        "source_mode": form.cleaned_data["source_mode"],
        "duplicate_execution_maximum": execution_ceiling,
        "matching_mode": matching_mode,
    }
    attempt_intent = _root_attempt_intent(
        source_mode=body["source_mode"],
        connection_id=body["connection_id"],
        entity_family=body["entity_family"],
        duplicate_execution_maximum=execution_ceiling,
        auto_merge_min_confidence=_form_auto_merge_threshold(form),
        matching_mode=matching_mode,
    )
    _require_compatible_root_attempt(
        journal, form_instance=form_instance, intent=attempt_intent
    )
    if form.cleaned_data.get("candidate_groups") is not None:
        body["candidate_groups"] = form.cleaned_data["candidate_groups"]
    if form.cleaned_data.get("source_mode") == "selected_ids":
        body["selected_ids"] = form.cleaned_data.get("selected_ids_list") or []
        if form.cleaned_data.get("query_id"):
            body["query_id"] = form.cleaned_data["query_id"]
            body["query_result_digest"] = form.cleaned_data["query_result_digest"]
    journey = _dispatch_json_mutation(
        journal=journal,
        mutation_kind="crm_duplicate_journey_start",
        route="/v1/crm/duplicate-journeys",
        body=body,
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(form_instance, "legacy-journey"),
        logical_action_identity=_step_identity(
            step="legacy-journey",
            owner_session=owner_session,
            form_instance=form_instance,
        ),
    )
    extra = {
        "orchestrator_form_instance": str(form_instance),
        "duplicate_execution_maximum": execution_ceiling,
        "mutation_journal_id": str(journal.id),
    }

    def _factory() -> ImportSession:
        return _create_workflow_session_shell(
            request, journey=journey, extra_options=extra
        )

    workflow_session, _created = _claim_or_get_attempt_session(
        request,
        journal,
        form_instance=form_instance,
        intent=attempt_intent,
        factory=_factory,
    )
    try:
        _refresh_workflow_projection(
            workflow_session,
            run_id=journey["run_id"],
            owner_session=owner_session,
            client=client,
        )
    except CLIENT_ERRORS:
        # Shell is claimed; operator can open workflow/progress after recovery.
        pass
    messages.success(
        request,
        "CRM duplicate journey started. Authorize the next workflow step.",
    )
    return redirect("importer:workflow", session_id=workflow_session.id)


@require_http_methods(["GET", "POST"])
def crm_duplicate_population_map(request, session_id):
    """Screen 2: map one column to Record ID, then start analysis."""

    draft = _workflow_session_for_request(request, session_id)
    options = dict(draft.options or {})
    if options.get("population_source") != "uploaded_population":
        messages.error(request, "This session is not waiting for Record ID mapping.")
        return redirect("importer:crm_duplicate_journey")
    if options.get("run_id"):
        return redirect(
            "importer:crm_duplicate_journey_progress",
            session_id=draft.id,
        )

    headers = [str(h) for h in (options.get("source_headers") or [])]
    suggested = options.get("suggested_source_column")
    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    map_identity = f"crm-dupe-4a:map:{owner_session}:{draft.id}"
    form_token = issue_form_token(
        owner_id=owner_id,
        session=draft,
        action_kind=MAP_ACTION,
        action_id=str(draft.id),
        logical_action_identity=map_identity,
        logical_action_generation=action_generation(draft, map_identity),
    )
    form = RecordIdMappingForm(
        request.POST or None,
        headers=headers,
        suggested=str(suggested) if suggested else None,
        initial={"form_token": form_token},
    )
    if request.method != "POST":
        form.fields["form_token"].initial = form_token

    client = EasyImportsApiClient()

    if request.method == "POST" and form.is_valid():
        try:
            decoded = decode_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=draft,
                action_kind=MAP_ACTION,
            )
            claims = validate_form_token(
                form.cleaned_data["form_token"],
                owner_id=owner_id,
                session=draft,
                action_kind=MAP_ACTION,
                action_id=str(draft.id),
                logical_action_identity=map_identity,
                logical_action_generation=int(
                    decoded.get("logical_action_generation") or 0
                ),
            )
            root_form_instance = claims["form_instance"]
            population_upload_id = str(options["population_upload_id"])
            mapping = _dispatch_json_mutation(
                journal=draft,
                mutation_kind="crm_duplicate_record_id_mapping",
                route=(
                    f"/v1/crm/duplicate-population-uploads/"
                    f"{population_upload_id}/record-id-mapping"
                ),
                body={"source_column": form.cleaned_data["source_column"]},
                owner_session=owner_session,
                client=client,
                form_instance=_step_form_instance(root_form_instance, "mapping"),
                logical_action_identity=_step_identity(
                    step="mapping",
                    owner_session=owner_session,
                    form_instance=root_form_instance,
                    resource=population_upload_id,
                ),
                resource_identity=population_upload_id,
            )
            auto_merge_t = _session_auto_merge_threshold(options)
            grant = _create_read_grant(
                journal=draft,
                owner_session=owner_session,
                client=client,
                root_form_instance=root_form_instance,
                population_source="uploaded_population",
                population_upload_id=population_upload_id,
                auto_merge_min_confidence=auto_merge_t,
            )
            journey_body: dict[str, Any] = {
                "connection_id": options["connection_id"],
                "entity_family": options["entity_family"],
                "source_mode": "uploaded_population",
                "population_upload_id": population_upload_id,
                "duplicate_execution_maximum": options.get(
                    "duplicate_execution_maximum"
                )
                or "dry_run",
            }
            if auto_merge_t is not None:
                journey_body["auto_merge_min_confidence"] = auto_merge_t
            matching_mode = _session_matching_mode(options)
            journey_body["matching_mode"] = matching_mode
            journey_resource = f"upload:{population_upload_id}:m{matching_mode}"
            if auto_merge_t is not None:
                journey_resource = f"{journey_resource}:t{auto_merge_t}"
            journey = _dispatch_json_mutation(
                journal=draft,
                mutation_kind="crm_duplicate_journey_start",
                route="/v1/crm/duplicate-journeys",
                body=journey_body,
                owner_session=owner_session,
                client=client,
                form_instance=_step_form_instance(root_form_instance, "journey"),
                logical_action_identity=_step_identity(
                    step="journey",
                    owner_session=owner_session,
                    form_instance=root_form_instance,
                    resource=journey_resource,
                ),
            )
            draft.operator_label = (
                f"CRM duplicates · {_entity_label(journey['entity_family'])} · "
                f"{_source_label(journey['source_mode'])}"
            )
            draft.target_provider_id = journey.get("target_provider_id") or ""
            draft.status = ImportSession.Status.RUNNING
            draft.options = {
                **options,
                "crm_journey_id": journey["journey_id"],
                "run_id": journey["run_id"],
                "read_grant_id": grant["grant_id"],
                "source_column": mapping.get("source_column"),
                "record_id_count": mapping.get("record_id_count"),
                "record_id_digest": mapping.get("record_id_digest"),
                "journey_status": journey.get("status"),
                "provider_key": journey.get("provider_key"),
                "mutation_journal_id": str(draft.id),
                "apply_root_form_instance": str(root_form_instance),
                "auto_merge_min_confidence": auto_merge_t,
                "matching_mode": matching_mode,
            }
            draft.save(
                update_fields=[
                    "operator_label",
                    "target_provider_id",
                    "status",
                    "options",
                    "updated_at",
                ]
            )
            apply_pending = False
            try:
                _apply_implied_read(
                    journal=draft,
                    owner_session=owner_session,
                    client=client,
                    root_form_instance=root_form_instance,
                    grant_id=grant["grant_id"],
                    run_id=journey["run_id"],
                )
            except (ApiOperationInProgressError, MutationBusyError):
                apply_pending = True
            if apply_pending:
                messages.info(
                    request,
                    "EasyImports is reading those records from the connected CRM "
                    "in the background. This page will update automatically.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=draft.id,
                )
            projection = client.workflow(journey["run_id"], owner_session=owner_session)
            store_workflow_projection(
                draft,
                projection,
                resource_url=f"/v1/workflows/{journey['run_id']}",
                role=ApiWorkflow.Role.PRIMARY,
            )
            messages.success(
                request,
                "Record ID mapped. Reading those records from the connected CRM…",
            )
            return redirect(
                "importer:crm_duplicate_journey_progress",
                session_id=draft.id,
            )
        except CLIENT_ERRORS as exc:
            messages.error(request, str(exc))
            # Keep submitted map token for exact retry of the same mapping attempt.

    return render(
        request,
        "importer/crm_duplicate_population_map.html",
        {
            "form": form,
            "form_token": form["form_token"].value()
            or form.data.get("form_token")
            or form_token,
            "session": draft,
            "headers": headers,
            "preview_rows": options.get("preview_rows") or [],
            "filename": options.get("filename"),
            "row_count": options.get("row_count"),
            "entity_family": options.get("entity_family"),
            "connection_id": options.get("connection_id"),
            "suggested_source_column": suggested,
        },
    )


@require_http_methods(["GET", "POST"])
def crm_duplicate_journey_progress(request, session_id):
    """Progress screen: status and public pause diagnostic; redirect when ready."""

    workflow_session = _workflow_session_for_request(request, session_id)
    options = dict(workflow_session.options or {})
    client = EasyImportsApiClient()
    owner_session = _owner_session(request)
    owner_id = owner_id_for_request(request)

    active = getattr(workflow_session, "active_workflow", None)
    run_id = str(getattr(active, "run_id", "") or "") or str(
        options.get("run_id") or ""
    )
    if not run_id:
        messages.error(request, "This journey has no analysis run yet.")
        return redirect("importer:crm_duplicate_journey")

    grant_id = str(options.get("read_grant_id") or "")
    progress_identity = f"crm-dupe-4a:progress:{owner_session}:{run_id}"
    form_token = issue_form_token(
        owner_id=owner_id,
        session=workflow_session,
        action_kind=PROGRESS_ACTION,
        action_id=run_id,
        logical_action_identity=progress_identity,
        logical_action_generation=action_generation(
            workflow_session, progress_identity
        ),
    )

    progress_form_instance: UUID | None = None
    resume_processing = False
    resume_blocked = False
    try:
        if request.method == "POST":
            token = (request.POST.get("form_token") or "").strip()
            decoded = decode_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=PROGRESS_ACTION,
            )
            claims = validate_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=PROGRESS_ACTION,
                action_id=run_id,
                logical_action_identity=progress_identity,
                logical_action_generation=int(
                    decoded.get("logical_action_generation") or 0
                ),
            )
            progress_form_instance = claims["form_instance"]

        projection = client.workflow(run_id, owner_session=owner_session)

        projection_status = str(projection.get("status") or "")
        projection_stage = str(projection.get("stage") or "")
        if request.method == "GET" and (
            projection_status in {"paused_verification", "paused_unknown"}
            or projection_stage in {"paused_verification", "paused_unknown"}
        ):
            mutation_journal = _mutation_journal_for_session(workflow_session)
            resume_mutations = mutation_journal.api_mutations.filter(
                mutation_kind="resume_effect",
                resource_identity=run_id,
            )
            reconciled_success = False
            retry_notice_added = False
            # Preclaim advances the workflow revision before the provider read
            # finishes. Reconcile by stable run ownership, never by the
            # projection's newer revision-scoped logical identity.
            for resume_mutation in resume_mutations.filter(
                state=ApiMutation.State.PENDING,
            ).order_by("created_at", "id"):
                if resume_mutation.http_status not in {202, 409}:
                    if resume_mutation.retry_available:
                        # Crash window: the durable mutation row exists, but
                        # dispatch never recorded an API response. Claim the
                        # exact frozen row for retry only if its local lease is
                        # still the stale snapshot we inspected.
                        recovered = ApiMutation.objects.filter(
                            pk=resume_mutation.pk,
                            state=ApiMutation.State.PENDING,
                            lease_token=resume_mutation.lease_token,
                            lease_expires_at=resume_mutation.lease_expires_at,
                        ).update(
                            state=ApiMutation.State.UNKNOWN,
                            http_status=None,
                            error_message=(
                                "The CRM read stopped before dispatch completed. "
                                "Retrying the saved exact request."
                            ),
                            lease_token=None,
                            lease_expires_at=None,
                            updated_at=timezone.now(),
                        )
                        if recovered and not retry_notice_added:
                            messages.warning(
                                request,
                                "The previous CRM batch read stopped before it "
                                "was dispatched. EasyImports will retry the "
                                "saved exact request.",
                            )
                            retry_notice_added = True
                        elif not recovered:
                            resume_processing = True
                    else:
                        resume_processing = True
                    continue
                operation_status = client.mutation_status(
                    resume_mutation.idempotency_key
                )
                if operation_status["mutation_kind"] != resume_mutation.mutation_kind:
                    raise ApiConsistencyError(
                        "The API mutation status named a different command."
                    )
                if operation_status["status"] == "pending":
                    if operation_status["retryable"]:
                        # The API worker no longer owns this command. Preserve
                        # its frozen key/body; POST will prefer this exact
                        # UNKNOWN mutation over creating one for a newer
                        # preclaim revision.
                        ApiMutation.objects.filter(
                            pk=resume_mutation.pk,
                            state=ApiMutation.State.PENDING,
                        ).update(
                            state=ApiMutation.State.UNKNOWN,
                            http_status=None,
                            response_json=operation_status,
                            error_message=(
                                "The API worker no longer owns this CRM read. "
                                "Retrying the saved exact request."
                            ),
                            lease_token=None,
                            lease_expires_at=None,
                            updated_at=timezone.now(),
                        )
                        if not retry_notice_added:
                            messages.warning(
                                request,
                                "The previous CRM batch read stopped before it "
                                "finished. EasyImports will retry the saved exact "
                                "request.",
                            )
                            retry_notice_added = True
                    else:
                        resume_processing = True
                    continue
                result = client.reconcile_mutation_status(
                    resume_mutation,
                    operation_status,
                )
                if result is None:
                    resume_processing = True
                elif result.mutation.state == ApiMutation.State.COMPLETED:
                    reconciled_success = True

            if reconciled_success:
                # Re-read after frozen success so the next bounded batch,
                # review handoff, or terminal state renders immediately.
                projection = client.workflow(
                    run_id,
                    owner_session=owner_session,
                )

            latest_resume = resume_mutations.order_by("-created_at", "-id").first()
            if (
                latest_resume is not None
                and latest_resume.state == ApiMutation.State.REJECTED
            ):
                resume_blocked = True
                frozen_error = str(
                    latest_resume.error_message
                    or latest_resume.error_code
                    or "The API rejected the CRM batch read."
                )
                messages.error(request, f"CRM batch read failed: {frozen_error}")
            elif (
                latest_resume is not None
                and latest_resume.state == ApiMutation.State.COMPLETED
            ):
                latest_identity = _reference_resume_identity(
                    owner_session=owner_session,
                    run_id=run_id,
                    expected_revision=int(projection.get("revision") or 0),
                )
                if latest_resume.logical_action_identity == latest_identity and (
                    str(projection.get("status") or "")
                    in {"paused_verification", "paused_unknown"}
                    or str(projection.get("stage") or "")
                    in {"paused_verification", "paused_unknown"}
                ):
                    resume_blocked = True
                    messages.error(
                        request,
                        "The CRM batch read completed without advancing the "
                        "workflow. Start over or inspect the workflow details "
                        "before retrying.",
                    )

        if request.method == "GET" and grant_id and (
            projection_status == "awaiting_effect_authorization"
            or projection_stage == "awaiting_effect_authorization"
        ):
            mutation_journal = _mutation_journal_for_session(workflow_session)
            apply_processing, apply_reconciled = (
                _reconcile_pending_implied_authorization(
                    journal=mutation_journal,
                    client=client,
                    run_id=run_id,
                )
            )
            if apply_processing:
                resume_processing = True
            if apply_reconciled:
                projection = client.workflow(
                    run_id,
                    owner_session=owner_session,
                )
            latest_apply = (
                mutation_journal.api_mutations.filter(
                    mutation_kind="crm_duplicate_implied_reference_authorization",
                    resource_identity=run_id,
                )
                .order_by("-created_at", "-id")
                .first()
            )
            if (
                latest_apply is not None
                and latest_apply.state == ApiMutation.State.REJECTED
            ):
                messages.error(
                    request,
                    "CRM read failed: "
                    + str(
                        latest_apply.error_message
                        or latest_apply.error_code
                        or "The API rejected the first CRM read."
                    ),
                )

        if request.method == "POST" and progress_form_instance is not None:
            mutation_journal = _mutation_journal_for_session(workflow_session)
            status = str(projection.get("status") or "")
            stage = str(projection.get("stage") or "")
            if status in {"paused_verification", "paused_unknown"} or stage in {
                "paused_verification",
                "paused_unknown",
            }:
                try:
                    _resume_reference_scan(
                        journal=mutation_journal,
                        owner_session=owner_session,
                        client=client,
                        form_instance=progress_form_instance,
                        run_id=run_id,
                        expected_revision=int(projection.get("revision") or 0),
                    )
                except (ApiOperationInProgressError, MutationBusyError):
                    resume_processing = True
                    messages.info(
                        request,
                        "EasyImports is reading the next CRM batch in the "
                        "background. This page will update automatically.",
                    )
                else:
                    projection = client.workflow(run_id, owner_session=owner_session)
            elif grant_id and status == "awaiting_effect_authorization":
                raw_root = options.get("apply_root_form_instance") or options.get(
                    "orchestrator_form_instance"
                )
                try:
                    root_form_instance = (
                        UUID(str(raw_root)) if raw_root else progress_form_instance
                    )
                except (TypeError, ValueError):
                    root_form_instance = progress_form_instance
                try:
                    _apply_implied_read(
                        journal=mutation_journal,
                        owner_session=owner_session,
                        client=client,
                        root_form_instance=root_form_instance,
                        grant_id=grant_id,
                        run_id=run_id,
                    )
                except (ApiOperationInProgressError, MutationBusyError):
                    resume_processing = True
                    messages.info(
                        request,
                        "EasyImports is reading records from the connected CRM "
                        "in the background. This page will update automatically.",
                    )
                else:
                    projection = client.workflow(run_id, owner_session=owner_session)

        store_workflow_projection(
            workflow_session,
            projection,
            resource_url=f"/v1/workflows/{run_id}",
            role=ApiWorkflow.Role.PRIMARY,
        )
    except CLIENT_ERRORS as exc:
        messages.error(request, str(exc))
        projection = {
            "status": "unavailable",
            "stage": "",
            "summary": {},
            "run_id": run_id,
        }

    if _is_failed(projection):
        messages.error(request, _progress_message(projection))
        return render(
            request,
            "importer/crm_duplicate_journey_progress.html",
            {
                "session": workflow_session,
                "projection": projection,
                "progress_message": _progress_message(projection),
                "run_id": run_id,
                "options": options,
                "form_token": form_token,
                "is_failed": True,
                "refresh_url": reverse(
                    "importer:crm_duplicate_journey_progress",
                    kwargs={"session_id": workflow_session.id},
                ),
                "workflow_url": reverse(
                    "importer:workflow",
                    kwargs={"session_id": workflow_session.id},
                ),
                "start_url": reverse("importer:crm_duplicate_journey"),
                "journey_id": str(options.get("crm_journey_id") or ""),
                **_read_progress_context(projection, options),
            },
        )

    progress = projection.get("duplicate_analysis_progress") or {}
    window_ready = (
        isinstance(progress, dict) and progress.get("review_window_ready") is True
    )
    if _is_review_ready(projection) or window_ready:
        stage = str(projection.get("stage") or "")
        summary = projection.get("summary") or {}
        if (
            stage == "no_duplicate_groups"
            or summary.get("no_duplicate_groups")
            or _oversized_quarantine_only(projection)
        ):
            messages.info(request, _progress_message(projection))
            return redirect("importer:workflow", session_id=workflow_session.id)

        options = dict(workflow_session.options or {})
        mutation_journal = _mutation_journal_for_session(workflow_session)
        raw_root = options.get("apply_root_form_instance") or options.get(
            "orchestrator_form_instance"
        )
        try:
            root_form_instance = UUID(str(raw_root)) if raw_root else uuid4()
        except (TypeError, ValueError):
            root_form_instance = uuid4()

        # Recover local flag from a completed journaled mutation without a new
        # wire dispatch (commit-before-local-save). Safe on GET.
        if _auto_merge_pending(options):
            recovered = _recover_auto_disposition_from_mutation(
                workflow_session,
                journal=mutation_journal,
                root_form_instance=root_form_instance,
                threshold=int(_session_auto_merge_threshold(options) or 90),
                source_run_id=run_id,
            )
            if recovered is not None:
                options = recovered

        # Phase 7C: auto-disposition is a journaled POST only — never on GET.
        if request.method == "POST" and _auto_merge_pending(options):
            try:
                options = _maybe_apply_auto_disposition(
                    workflow_session=workflow_session,
                    client=client,
                    owner_session=owner_session,
                    source_run_id=run_id,
                    journal=mutation_journal,
                    root_form_instance=root_form_instance,
                )
            except (*CLIENT_ERRORS, ValueError) as exc:
                messages.error(request, str(exc))
                return render(
                    request,
                    "importer/crm_duplicate_journey_progress.html",
                    {
                        "session": workflow_session,
                        "projection": projection,
                        "progress_message": (
                            "Analysis is ready, but auto-disposition could not "
                            f"complete: {exc}. Use Continue to retry the exact "
                            "journaled action."
                        ),
                        "run_id": run_id,
                        "options": options,
                        "form_token": form_token,
                        "is_failed": False,
                        "needs_auto_disposition": True,
                        "auto_approved_group_count": options.get(
                            "auto_approved_group_count"
                        ),
                        "auto_merge_min_confidence": _session_auto_merge_threshold(
                            options
                        ),
                        "refresh_url": reverse(
                            "importer:crm_duplicate_journey_progress",
                            kwargs={"session_id": workflow_session.id},
                        ),
                        "workflow_url": reverse(
                            "importer:workflow",
                            kwargs={"session_id": workflow_session.id},
                        ),
                        "start_url": reverse("importer:crm_duplicate_journey"),
                        "journey_id": str(options.get("crm_journey_id") or ""),
                        **_read_progress_context(projection, options),
                    },
                )
            if options.get("auto_disposition_applied"):
                return _progress_after_auto_disposition(
                    request,
                    workflow_session=workflow_session,
                    options=options,
                )
            return redirect(
                "importer:crm_duplicate_journey_review",
                session_id=workflow_session.id,
            )

        if options.get("auto_disposition_applied"):
            return _progress_after_auto_disposition(
                request,
                workflow_session=workflow_session,
                options=options,
            )

        if (
            _auto_merge_pending(options)
            and _analysis_complete_for_auto_disposition(projection)
            and str(request.GET.get("auto_queued") or "") == "1"
        ):
            # Complete population, only eligible groups remain: stay here so
            # Continue can POST auto-disposition. Incremental review already
            # opened when window_ready; this is the loop-breaker.
            # Do not use review_groups_ready as the queued count — that is the
            # materials prefix, not remaining withheld groups.
            return render(
                request,
                "importer/crm_duplicate_journey_progress.html",
                {
                    "session": workflow_session,
                    "projection": projection,
                    "progress_message": _auto_queued_review_copy(),
                    "run_id": run_id,
                    "options": options,
                    "form_token": form_token,
                    "is_failed": False,
                    "needs_auto_disposition": True,
                    "auto_approved_group_count": options.get(
                        "auto_approved_group_count"
                    ),
                    "auto_merge_min_confidence": _session_auto_merge_threshold(options),
                    "refresh_url": reverse(
                        "importer:crm_duplicate_journey_progress",
                        kwargs={"session_id": workflow_session.id},
                    ),
                    "workflow_url": reverse(
                        "importer:workflow",
                        kwargs={"session_id": workflow_session.id},
                    ),
                    "start_url": reverse("importer:crm_duplicate_journey"),
                    "journey_id": str(options.get("crm_journey_id") or ""),
                    **_read_progress_context(projection, options),
                },
            )

        # No auto-merge: GET/POST both may advance to review (no auto mutation).
        messages.success(request, "Analysis complete. Review the first groups.")
        return redirect(
            "importer:crm_duplicate_journey_review",
            session_id=workflow_session.id,
        )

    return render(
        request,
        "importer/crm_duplicate_journey_progress.html",
        {
            "session": workflow_session,
            "projection": projection,
            "progress_message": (
                "Reading the next CRM batch in the background..."
                if resume_processing
                else _progress_message(projection)
            ),
            "run_id": run_id,
            "options": options,
            "form_token": form_token,
            "is_failed": False,
            "needs_auto_disposition": False,
            "auto_approved_group_count": options.get("auto_approved_group_count"),
            "auto_merge_min_confidence": _session_auto_merge_threshold(options),
            "refresh_url": reverse(
                "importer:crm_duplicate_journey_progress",
                kwargs={"session_id": workflow_session.id},
            ),
            "workflow_url": reverse(
                "importer:workflow",
                kwargs={"session_id": workflow_session.id},
            ),
            "start_url": reverse("importer:crm_duplicate_journey"),
            "journey_id": str(options.get("crm_journey_id") or ""),
            "resume_processing": resume_processing,
            "auto_continue_read": (not resume_processing)
            and (not resume_blocked)
            and (
                str(projection.get("status") or "") == "paused_verification"
                or str(projection.get("stage") or "") == "paused_verification"
            ),
            "uncertain_read": (
                str(projection.get("status") or "") == "paused_unknown"
                or str(projection.get("stage") or "") == "paused_unknown"
            ),
            **_read_progress_context(projection, options),
        },
    )


def _is_review_complete_payload(window: dict) -> bool:
    contract = str(window.get("review_contract") or "")
    if "duplicate_review_complete" in contract:
        return True
    if str(window.get("outcome") or "") in {"complete", "review_complete"}:
        return True
    groups = window.get("groups")
    if groups is None and int(window.get("remaining_group_count") or -1) == 0:
        return True
    return False


def _default_group_choice(group: dict) -> tuple[str | None, str | None]:
    """Return (action, survivor_id) defaults from stored allowed_actions."""

    allowed = {str(item) for item in (group.get("allowed_actions") or []) if str(item)}
    recommended = (
        str(
            group.get("selected_survivor_id")
            or group.get("recommended_survivor_id")
            or ""
        ).strip()
        or None
    )
    if "approve" in allowed and recommended:
        return "approve", recommended
    if "decline" in allowed and "approve" not in allowed:
        return "decline", None
    if "quarantine" in allowed and "approve" not in allowed:
        return "quarantine", None
    if "decline" in allowed:
        return "decline", None
    if "quarantine" in allowed:
        return "quarantine", None
    return None, None


def _review_group_cards(
    window: dict,
    *,
    posted: dict | None = None,
) -> list[dict]:
    """Presentation cards for the five-group review template."""

    cards: list[dict] = []
    groups = list(window.get("groups") or [])
    for group in groups:
        if not isinstance(group, dict):
            continue
        group_id = str(group.get("group_id") or "").strip()
        if not group_id:
            continue
        allowed = [
            str(item)
            for item in (group.get("allowed_actions") or [])
            if str(item) in _REVIEW_ACTIONS
        ]
        default_action, default_survivor = _default_group_choice(group)
        if posted is not None:
            # POST must include an explicit choice per group — never silently
            # substitute stored defaults for a missing radio (fail closed).
            raw_action = str(posted.get(f"action_{group_id}") or "").strip()
            action = raw_action if raw_action in _REVIEW_ACTIONS else None
            raw_survivor = str(posted.get(f"survivor_{group_id}") or "").strip()
            survivor = raw_survivor or None
            if action in {"decline", "quarantine"}:
                survivor = None
        else:
            action = default_action
            survivor = default_survivor
        field_columns = review_field_columns(group)
        reason_id, reason_sentence = winner_reason(group)
        members_out: list[dict] = []
        for member in group.get("members") or []:
            if not isinstance(member, dict):
                continue
            record_id = str(member.get("record_id") or "").strip()
            if not record_id:
                continue
            members_out.append(
                {
                    "record_id": record_id,
                    "recommended": bool(member.get("recommended")),
                    "survivor_eligible": bool(member.get("survivor_eligible")),
                    "selected": (
                        survivor == record_id
                        if survivor
                        else bool(member.get("selected"))
                    ),
                    "cells": [
                        review_cell_value(member, column) for column in field_columns
                    ],
                    "ranking_evidence": member.get("ranking_evidence"),
                }
            )
        survivor_choices = [
            m
            for m in members_out
            if m["survivor_eligible"]
            and ("approve" in allowed or "override_survivor" in allowed)
        ]
        cards.append(
            {
                "group_id": group_id,
                "group_revision": str(group.get("group_revision") or ""),
                "entity_family": str(group.get("entity_family") or ""),
                "group_status": str(group.get("group_status") or ""),
                "review_lane": str(group.get("review_lane") or ""),
                "confidence_band": str(group.get("confidence_band") or ""),
                "confidence_score": group.get("confidence_score"),
                "advanced_review_required": bool(group.get("advanced_review_required")),
                "execution_blockers": [
                    str(item) for item in (group.get("execution_blockers") or [])
                ],
                "conflicts": group.get("conflicts") or [],
                "evidence": group.get("evidence") or [],
                "allowed_actions": allowed,
                "action": action,
                "survivor_id": survivor,
                "recommended_survivor_id": str(
                    group.get("recommended_survivor_id") or ""
                ).strip()
                or None,
                "members": members_out,
                "survivor_choices": survivor_choices,
                "show_survivor_radios": bool(
                    survivor_choices
                    and ("approve" in allowed or "override_survivor" in allowed)
                ),
                "field_columns": field_columns,
                "winner_reason_id": reason_id,
                "winner_sentence": reason_sentence,
                "field_merge": field_merge_display(group.get("field_merge_plan")),
            }
        )
    return cards


def _decisions_from_posted_cards(cards: list[dict]) -> list[dict]:
    decisions: list[dict] = []
    for card in cards:
        action = card.get("action")
        if action not in _REVIEW_ACTIONS:
            raise ValueError(f"Choose one outcome for group {card.get('group_id')}.")
        if action not in (card.get("allowed_actions") or []):
            raise ValueError(
                f"That outcome is not allowed for group {card.get('group_id')}."
            )
        survivor = card.get("survivor_id")
        if action in {"approve", "override_survivor"}:
            if not survivor:
                raise ValueError(f"Select a survivor for group {card.get('group_id')}.")
            eligible_ids = {m["record_id"] for m in card.get("survivor_choices") or []}
            if survivor not in eligible_ids:
                raise ValueError(
                    f"The selected survivor is not eligible for group "
                    f"{card.get('group_id')}."
                )
            # Backend distinguishes approve vs override by survivor identity.
            recommended = card.get("recommended_survivor_id")
            if (
                action == "approve"
                and recommended
                and survivor != recommended
                and "override_survivor" in (card.get("allowed_actions") or [])
            ):
                action = "override_survivor"
            elif (
                action == "override_survivor"
                and recommended
                and survivor == recommended
                and "approve" in (card.get("allowed_actions") or [])
            ):
                action = "approve"
        else:
            survivor = None
        entry: dict[str, Any] = {
            "group_id": card["group_id"],
            "group_revision": card["group_revision"],
            "action": action,
            "advanced_review": bool(card.get("advanced_review_required")),
        }
        if survivor is not None:
            entry["selected_survivor_id"] = survivor
        decisions.append(entry)
    return decisions


def _decisions_from_posted_form(posted: dict) -> list[dict]:
    """Build API decisions purely from the posted form (exact-retry safe).

    Does not depend on a freshly fetched window, so browser-back / lost-response
    replay still submits the frozen window_id/digest/revisions.
    """

    order_raw = str(posted.get("group_order") or "").strip()
    group_ids = [part for part in order_raw.split(",") if part.strip()]
    if not group_ids:
        raise ValueError("This review form is missing its group list.")
    decisions: list[dict] = []
    for group_id in group_ids:
        action = str(posted.get(f"action_{group_id}") or "").strip()
        if action not in _REVIEW_ACTIONS:
            raise ValueError(f"Choose one outcome for group {group_id}.")
        allowed = {
            part.strip()
            for part in str(posted.get(f"allowed_{group_id}") or "").split(",")
            if part.strip()
        }
        if action not in allowed:
            raise ValueError(f"That outcome is not allowed for group {group_id}.")
        revision = str(posted.get(f"group_revision_{group_id}") or "").strip()
        if not revision:
            raise ValueError(f"Missing group revision for group {group_id}.")
        recommended = str(posted.get(f"recommended_{group_id}") or "").strip() or None
        eligible = {
            part.strip()
            for part in str(posted.get(f"eligible_{group_id}") or "").split(",")
            if part.strip()
        }
        advanced = str(posted.get(f"advanced_{group_id}") or "").strip() in {
            "1",
            "true",
            "True",
        }
        survivor = str(posted.get(f"survivor_{group_id}") or "").strip() or None
        if action in {"approve", "override_survivor"}:
            if not survivor:
                raise ValueError(f"Select a survivor for group {group_id}.")
            if survivor not in eligible:
                raise ValueError(
                    f"The selected survivor is not eligible for group {group_id}."
                )
            if (
                action == "approve"
                and recommended
                and survivor != recommended
                and "override_survivor" in allowed
            ):
                action = "override_survivor"
            elif (
                action == "override_survivor"
                and recommended
                and survivor == recommended
                and "approve" in allowed
            ):
                action = "approve"
        else:
            survivor = None
        entry: dict[str, Any] = {
            "group_id": group_id,
            "group_revision": revision,
            "action": action,
            "advanced_review": advanced,
        }
        if survivor is not None:
            entry["selected_survivor_id"] = survivor
        decisions.append(entry)
    return decisions


def _amend_decisions_from_posted_summary(posted) -> list[dict[str, Any]]:
    """Build amend-API decisions from the completed-review summary form.

    Honors allowed_actions and ranking-eligible survivors from the form. Server
    rebinds group_revision on rebuild; recommended_* is the backend recommendation
    (not the currently selected survivor).
    """

    order_raw = str(posted.get("group_order") or "").strip()
    group_ids = [part for part in order_raw.split(",") if part.strip()]
    if not group_ids:
        raise ValueError("This summary form is missing its group list.")
    decisions: list[dict[str, Any]] = []
    for group_id in group_ids:
        allowed = {
            part.strip()
            for part in str(posted.get(f"allowed_{group_id}") or "").split(",")
            if part.strip()
        }
        if not allowed:
            continue
        action = str(posted.get(f"action_{group_id}") or "").strip()
        if action not in _REVIEW_ACTIONS:
            raise ValueError(f"Choose one outcome for group {group_id}.")
        if action not in allowed:
            raise ValueError(f"That outcome is not allowed for group {group_id}.")
        # Backend recommendation (ranking), not current selection.
        recommended = str(posted.get(f"recommended_{group_id}") or "").strip() or None
        eligible = {
            part.strip()
            for part in str(posted.get(f"eligible_{group_id}") or "").split(",")
            if part.strip()
        }
        advanced = str(posted.get(f"advanced_{group_id}") or "").strip() in {
            "1",
            "true",
            "True",
        }
        survivor = str(posted.get(f"survivor_{group_id}") or "").strip() or None
        if action in {"approve", "override_survivor"}:
            if not survivor:
                raise ValueError(f"Select a survivor for group {group_id}.")
            if eligible and survivor not in eligible:
                raise ValueError(
                    f"The selected survivor is not eligible for group {group_id}."
                )
            # Map to approve vs override against the *recommended* survivor.
            if recommended and survivor == recommended:
                action = "approve" if "approve" in (allowed or {"approve"}) else action
            elif recommended and survivor != recommended:
                if "override_survivor" not in (allowed or {"override_survivor"}):
                    raise ValueError(f"Cannot override survivor for group {group_id}.")
                action = "override_survivor"
        else:
            survivor = None
        entry: dict[str, Any] = {
            "group_id": group_id,
            "action": action,
            "advanced_review": advanced,
        }
        if survivor is not None:
            entry["selected_survivor_id"] = survivor
        decisions.append(entry)
    return decisions


def _reviewed_disposition_window_amend_identity(
    *,
    owner_session: str,
    review_run_id: str,
    expected_revision: int,
    reviewed_result_content_digest: str,
    decision_set_content_digest: str,
    window_token: str,
) -> str:
    material = ":".join(
        (
            reviewed_result_content_digest,
            decision_set_content_digest,
            window_token,
        )
    )
    binding = sha256(material.encode("utf-8")).hexdigest()
    return (
        "crm-dupe-r1:amend-window:"
        f"{owner_session}:{review_run_id}:{int(expected_revision)}:{binding}"
    )


def _review_cursor_session_key(run_id: str) -> str:
    return f"efg_review_cursor:{run_id}"


def _review_next_cursor_session_key(run_id: str) -> str:
    return f"efg_review_next_cursor:{run_id}"


def _maybe_prepare_reviewed_result_after_complete(
    request,
    *,
    workflow_session: ImportSession,
    review_run_id: str,
    expected_revision: int,
) -> None:
    """POST the journaled prepare mutation. GET never calls this."""

    run_id = str(review_run_id or "").strip()
    if not run_id:
        return
    owner_session = _owner_session(request)
    client = EasyImportsApiClient()
    options = dict(workflow_session.options or {})
    root_raw = options.get("apply_root_form_instance") or options.get(
        "orchestrator_form_instance"
    )
    try:
        root_form_instance = UUID(str(root_raw)) if root_raw else uuid4()
    except (TypeError, ValueError):
        root_form_instance = uuid4()
    try:
        _dispatch_json_mutation(
            journal=_mutation_journal_for_session(workflow_session),
            mutation_kind="prepare_duplicate_reviewed_result",
            route=(
                f"/v1/workflows/{run_id}/duplicate-reviewed-result-prepare"
            ),
            body={"expected_revision": int(expected_revision or 0)},
            owner_session=owner_session,
            client=client,
            form_instance=_step_form_instance(
                root_form_instance,
                f"prepare:{run_id}:{int(expected_revision or 0)}",
            ),
            logical_action_identity=_step_identity(
                step="prepare-reviewed-result",
                owner_session=owner_session,
                form_instance=root_form_instance,
                resource=f"{run_id}:{int(expected_revision or 0)}",
            ),
            resource_identity=run_id,
        )
    except (ApiOperationInProgressError, *CLIENT_ERRORS):
        return


def _review_window_redirect_after_submit(
    request,
    *,
    workflow_session: ImportSession,
    receipt: dict,
    run_id: str = "",
):
    """Redirect after an accepted window submit (including exact replay)."""

    result = receipt.get("result") if isinstance(receipt, dict) else None
    next_body = None
    if isinstance(result, dict):
        next_body = result.get("review_window")
    posted = getattr(request, "POST", None)
    posted_next = ""
    if posted is not None:
        posted_next = str(posted.get("review_next_cursor") or "").strip()
    cursor_key = _review_cursor_session_key(run_id) if run_id else ""
    next_key = _review_next_cursor_session_key(run_id) if run_id else ""
    if isinstance(next_body, dict) and _is_review_complete_payload(next_body):
        if cursor_key:
            request.session.pop(cursor_key, None)
        if next_key:
            request.session.pop(next_key, None)
        request.session.pop("efg_review_cursor", None)
        request.session.pop("efg_review_next_cursor", None)
        messages.success(
            request,
            next_body.get("terminal_message")
            or "Final groups saved. Continue to the merge plan.",
        )
        expected_revision = int(
            receipt.get("revision")
            or next_body.get("expected_revision")
            or 0
        )
        _maybe_prepare_reviewed_result_after_complete(
            request,
            workflow_session=workflow_session,
            review_run_id=run_id,
            expected_revision=expected_revision,
        )
        return redirect(
            "importer:crm_duplicate_journey_merge",
            session_id=workflow_session.id,
        )
    messages.success(
        request,
        "Saved these groups. Continue with the next set.",
    )
    next_cursor = posted_next
    if not next_cursor and next_key:
        next_cursor = str(request.session.pop(next_key, "") or "").strip()
    if next_cursor and cursor_key:
        request.session[cursor_key] = next_cursor
    elif cursor_key:
        request.session.pop(cursor_key, None)
    target = reverse(
        "importer:crm_duplicate_journey_review",
        kwargs={"session_id": workflow_session.id},
    )
    if next_cursor:
        from urllib.parse import urlencode

        target = f"{target}?{urlencode({'cursor': next_cursor})}"
    return redirect(target)


def _source_and_review_run_ids(workflow_session: ImportSession) -> tuple[str, str]:
    """Return (source_run_id, review_run_id) from session options / active workflow."""

    options = dict(workflow_session.options or {})
    active = getattr(workflow_session, "active_workflow", None)
    source_run_id = str(options.get("run_id") or "") or str(
        getattr(active, "run_id", "") or ""
    )
    review_run_id = str(options.get("review_run_id") or "")
    return source_run_id, review_run_id


def _discover_existing_review_run(
    workflow_session: ImportSession,
    *,
    journal: ImportSession,
    source_run_id: str,
) -> str:
    """Return an already-created review run_id without dispatching.

    GET-safe: reads the session's REVIEW child and completed
    start_review_workflow receipts only. Does not write options or POST.
    """

    wanted = str(source_run_id or "").strip()
    children = workflow_session.api_workflows.filter(
        role=ApiWorkflow.Role.REVIEW
    ).order_by("-updated_at")
    for row in children:
        run_id = str(row.run_id or "").strip()
        if not run_id:
            continue
        source = row.source_workflow
        if source is None:
            continue
        if wanted and str(source.run_id or "").strip() != wanted:
            continue
        return run_id

    seen: set[int] = set()
    for owner in (workflow_session, journal):
        if owner is None or id(owner) in seen:
            continue
        seen.add(id(owner))
        rows = owner.api_mutations.filter(
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


def _ensure_review_workflow(
    *,
    workflow_session: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    source_run_id: str,
    journal: ImportSession,
    root_form_instance: UUID,
) -> tuple[str, str]:
    """Start the internal review run when needed; return (review_run_id, handoff_id).

    When options already freeze a review run, reuse it. When a REVIEW child or
    completed start_review_workflow receipt already exists (the workflow-page
    POST may have created it), adopt that run without dispatching. When the
    source projection exposes a review handoff and nothing exists yet, start
    the internal review workflow. When neither is available (common in
    network-free UI tests that mock the review-window on the analysis
    run_id), fall back to the source run_id so GET/POST still target the
    mocked endpoint.
    """

    options = dict(workflow_session.options or {})
    existing = str(options.get("review_run_id") or "").strip()
    handoff_id = str(options.get("review_handoff_id") or "").strip()
    if existing:
        return existing, handoff_id
    adopted = _discover_existing_review_run(
        workflow_session, journal=journal, source_run_id=source_run_id
    )
    if adopted:
        return adopted, handoff_id

    try:
        projection = client.workflow(source_run_id, owner_session=owner_session)
        store_workflow_projection(
            workflow_session,
            projection,
            resource_url=f"/v1/workflows/{source_run_id}",
            role=ApiWorkflow.Role.PRIMARY,
        )
    except CLIENT_ERRORS:
        # UI tests often mock only the review-window route.
        return source_run_id, handoff_id

    handoff = projection.get("review_handoff") or {}
    handoff_id = str(handoff.get("handoff_id") or "").strip()
    if not handoff_id:
        # Analysis run may already host review payloads in advanced/test paths.
        return source_run_id, handoff_id

    receipt = _dispatch_json_mutation(
        journal=journal,
        mutation_kind="start_review_workflow",
        route=f"/v1/workflows/{source_run_id}/review-workflows",
        body={"review_handoff_id": handoff_id},
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(root_form_instance, "start-review"),
        logical_action_identity=_step_identity(
            step="start-review",
            owner_session=owner_session,
            form_instance=root_form_instance,
            resource=f"{source_run_id}:{handoff_id}",
        ),
        resource_identity=source_run_id,
    )
    review_run_id = str(receipt.get("run_id") or "").strip()
    if not review_run_id:
        raise ValueError("Starting group review did not return a review run.")
    options["review_run_id"] = review_run_id
    options["review_handoff_id"] = handoff_id
    options["source_run_id"] = source_run_id
    workflow_session.options = options
    workflow_session.save(update_fields=["options", "updated_at"])
    # Bind the review box with explicit REVIEW role + source lineage (not primary).
    try:
        review_proj = client.workflow(review_run_id, owner_session=owner_session)
        _store_review_projection(workflow_session, review_proj, run_id=review_run_id)
    except CLIENT_ERRORS:
        pass
    return review_run_id, handoff_id


def _record_auto_disposition_local(
    workflow_session: ImportSession,
    *,
    result: dict[str, Any],
    threshold: int,
    review_run_id: str,
    handoff_id: str,
    source_run_id: str,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    """Persist auto-disposition outcome on the Django session (local only)."""

    options = dict(workflow_session.options or {})
    options["auto_disposition_applied"] = True
    options["auto_merge_min_confidence"] = threshold
    options["auto_approved_group_count"] = int(
        result.get("auto_approved_group_count") or 0
    )
    options["manual_remaining_group_count"] = int(
        result.get("manual_remaining_group_count") or 0
    )
    options["manual_queue_empty"] = bool(result.get("manual_queue_empty"))
    options["auto_disposition_result_digest"] = str(result.get("result_digest") or "")
    options["review_run_id"] = review_run_id
    if handoff_id:
        options["review_handoff_id"] = handoff_id
    if not options.get("source_run_id"):
        options["source_run_id"] = source_run_id
    if expected_revision is not None:
        options["auto_disposition_expected_revision"] = int(expected_revision)
    workflow_session.options = options
    workflow_session.save(update_fields=["options", "updated_at"])
    return options


def _recover_auto_disposition_from_mutation(
    workflow_session: ImportSession,
    *,
    journal: ImportSession,
    root_form_instance: UUID,
    threshold: int,
    source_run_id: str,
) -> dict[str, Any] | None:
    """If the API already accepted auto-disposition, rehydrate local options.

    Covers commit-before-local-save: the journaled mutation completed but
    ``auto_disposition_applied`` was never saved.
    """

    form_instance = _step_form_instance(root_form_instance, "auto-disposition")
    existing = (
        ApiMutation.objects.filter(
            session=journal,
            form_instance=form_instance,
            mutation_kind="submit_duplicate_auto_disposition",
            state=ApiMutation.State.COMPLETED,
        )
        .order_by("created_at", "id")
        .first()
    )
    if existing is None or not isinstance(existing.response_json, dict):
        return None
    result = existing.response_json
    if str(result.get("command_kind") or "") != AUTO_DISPOSITION_COMMAND_KIND:
        return None
    review_run_id = str(
        result.get("run_id")
        or (workflow_session.options or {}).get("review_run_id")
        or ""
    ).strip()
    handoff_id = str(
        (workflow_session.options or {}).get("review_handoff_id") or ""
    ).strip()
    frozen_rev = None
    req = existing.request_json if isinstance(existing.request_json, dict) else {}
    if "expected_revision" in req:
        try:
            frozen_rev = int(req["expected_revision"])
        except (TypeError, ValueError):
            frozen_rev = None
    return _record_auto_disposition_local(
        workflow_session,
        result=result,
        threshold=int(result.get("auto_merge_min_confidence") or threshold),
        review_run_id=review_run_id,
        handoff_id=handoff_id,
        source_run_id=source_run_id,
        expected_revision=frozen_rev,
    )


def _maybe_apply_auto_disposition(
    *,
    workflow_session: ImportSession,
    client: EasyImportsApiClient,
    owner_session: str,
    source_run_id: str,
    journal: ImportSession,
    root_form_instance: UUID,
) -> dict[str, Any]:
    """Journal once-only auto-disposition when T is frozen on the attempt.

    **POST-only caller contract:** must not be invoked from browser GET.
    Freezes ``expected_revision`` in session options before the first wire
    dispatch so commit-before-local-save retries reuse the same mutation body.
    """

    options = dict(workflow_session.options or {})
    if options.get("auto_disposition_applied"):
        return options
    threshold = _session_auto_merge_threshold(options)
    if threshold is None:
        return options

    recovered = _recover_auto_disposition_from_mutation(
        workflow_session,
        journal=journal,
        root_form_instance=root_form_instance,
        threshold=threshold,
        source_run_id=source_run_id,
    )
    if recovered is not None:
        return recovered

    try:
        source_projection = client.workflow(
            source_run_id, owner_session=owner_session
        )
    except CLIENT_ERRORS:
        return options
    if not _source_is_review_ready_for_auto_disposition(source_projection):
        return options

    review_run_id, handoff_id = _ensure_review_workflow(
        workflow_session=workflow_session,
        client=client,
        owner_session=owner_session,
        source_run_id=source_run_id,
        journal=journal,
        root_form_instance=root_form_instance,
    )
    options = dict(workflow_session.options or {})

    # Freeze expected_revision once before dispatch so a later local-save loss
    # still exact-retries the same body (CAS token must not advance on retry).
    frozen_raw = options.get("auto_disposition_expected_revision")
    if frozen_raw is None or frozen_raw == "":
        review_wf = client.workflow(review_run_id, owner_session=owner_session)
        expected_revision = int(review_wf.get("revision") or 0)
        options["auto_disposition_expected_revision"] = expected_revision
        options["review_run_id"] = review_run_id
        if handoff_id:
            options["review_handoff_id"] = handoff_id
        if not options.get("source_run_id"):
            options["source_run_id"] = source_run_id
        workflow_session.options = options
        workflow_session.save(update_fields=["options", "updated_at"])
    else:
        expected_revision = int(frozen_raw)

    body = {
        "command_kind": AUTO_DISPOSITION_COMMAND_KIND,
        "expected_revision": expected_revision,
        "auto_merge_min_confidence": threshold,
        "source_run_id": source_run_id,
    }
    result = _dispatch_json_mutation(
        journal=journal,
        mutation_kind="submit_duplicate_auto_disposition",
        route=f"/v1/workflows/{review_run_id}/duplicate-auto-disposition",
        body=body,
        owner_session=owner_session,
        client=client,
        form_instance=_step_form_instance(root_form_instance, "auto-disposition"),
        logical_action_identity=_step_identity(
            step="auto-disposition",
            owner_session=owner_session,
            form_instance=root_form_instance,
            resource=f"{review_run_id}:{threshold}",
        ),
        resource_identity=review_run_id,
    )
    return _record_auto_disposition_local(
        workflow_session,
        result=result,
        threshold=threshold,
        review_run_id=review_run_id,
        handoff_id=handoff_id
        or str((workflow_session.options or {}).get("review_handoff_id") or ""),
        source_run_id=source_run_id,
        expected_revision=expected_revision,
    )


def _progress_after_auto_disposition(
    request,
    *,
    workflow_session: ImportSession,
    options: dict[str, Any],
):
    """Redirect after a successful (or already-applied) auto-disposition."""

    auto_count = int(options.get("auto_approved_group_count") or 0)
    if options.get("manual_queue_empty"):
        if auto_count:
            messages.success(
                request,
                f"Analysis complete. Auto-approved {auto_count} "
                "high-confidence group"
                f"{'s' if auto_count != 1 else ''}. "
                "Review the merge plan (writes still require separate "
                "authorization).",
            )
        else:
            messages.success(
                request,
                "Analysis complete. No groups need manual review. "
                "Continue to the merge plan.",
            )
        _source_run_id, review_run_id = _source_and_review_run_ids(workflow_session)
        _maybe_prepare_reviewed_result_after_complete(
            request,
            workflow_session=workflow_session,
            review_run_id=str(review_run_id or ""),
            expected_revision=int(options.get("expected_revision") or 0),
        )
        return redirect(
            "importer:crm_duplicate_journey_merge",
            session_id=workflow_session.id,
        )
    if auto_count:
        messages.success(
            request,
            f"Analysis complete. Auto-approved {auto_count} "
            "high-confidence group"
            f"{'s' if auto_count != 1 else ''}. "
            "Review the remaining groups.",
        )
    else:
        messages.success(request, "Analysis complete. Review the first groups.")
    return redirect(
        "importer:crm_duplicate_journey_review",
        session_id=workflow_session.id,
    )


def _annotate_journeys_for_resume(
    request,
    journeys: list[dict],
    *,
    client: EasyImportsApiClient | None = None,
    owner_session: str | None = None,
) -> list[dict]:
    """Attach resume/action URLs and customer-facing workflow state."""

    owner_id = owner_id_for_request(request)
    if not journeys:
        return []
    run_ids = {
        str(item.get("run_id") or "").strip() for item in journeys if item.get("run_id")
    }
    journey_ids = {
        str(item.get("journey_id") or "").strip()
        for item in journeys
        if item.get("journey_id")
    }
    by_run: dict[str, ImportSession] = {}
    by_journey: dict[str, ImportSession] = {}
    for session in ImportSession.objects.filter(
        owner_id=owner_id,
        archived_at__isnull=True,
        product_key="easyimports.duplicate_resolution",
    ).order_by("-updated_at"):
        options = dict(session.options or {})
        rid = str(options.get("run_id") or "").strip()
        jid = str(options.get("crm_journey_id") or "").strip()
        if rid in run_ids and rid not in by_run:
            by_run[rid] = session
        if jid in journey_ids and jid not in by_journey:
            by_journey[jid] = session
    annotated: list[dict] = []
    for item in journeys:
        row = dict(item)
        rid = str(row.get("run_id") or "").strip()
        jid = str(row.get("journey_id") or "").strip()
        local = by_journey.get(jid) or by_run.get(rid)
        if local is not None:
            row["resume_session_id"] = str(local.id)
            row["resume_url"] = reverse(
                "importer:crm_duplicate_journey_progress",
                kwargs={"session_id": local.id},
            )
            row["review_url"] = reverse(
                "importer:crm_duplicate_journey_review",
                kwargs={"session_id": local.id},
            )
        projection = None
        if local is not None and rid:
            stored = local.api_workflows.filter(run_id=rid).first()
            if stored is not None and isinstance(stored.projection, dict):
                projection = stored.projection
        if (
            str(row.get("workflow_status") or "") == "failed"
            and (
                not isinstance(projection, dict)
                or str(projection.get("status") or "") != "failed"
            )
            and client is not None
            and owner_session
            and rid
        ):
            try:
                projection = client.workflow(rid, owner_session=owner_session)
            except (ApiUnavailableError, ApiRejectedError):
                projection = None
        display, failure_code = _journey_workflow_display(row, projection)
        row["workflow_display"] = display
        row["workflow_failure_code"] = failure_code
        row["can_abandon"] = (
            bool(rid)
            and str(row.get("workflow_status") or "") in _NON_TERMINAL_WORKFLOW_STATUSES
            and failure_code != "connection_removal_quarantine"
        )
        if row["can_abandon"]:
            row["abandon_url"] = reverse(
                "importer:crm_duplicate_journey_abandon",
                kwargs={"journey_id": jid},
            )
        annotated.append(row)
    return annotated


@require_http_methods(["GET", "POST"])
def crm_duplicate_journey_review(request, session_id):
    """Phase 4B: five-group review window GET/POST (review run, not source)."""

    workflow_session = _workflow_session_for_request(request, session_id)
    options = dict(workflow_session.options or {})
    client = EasyImportsApiClient()
    owner_session = _owner_session(request)
    owner_id = owner_id_for_request(request)
    source_run_id, review_run_id = _source_and_review_run_ids(workflow_session)
    if not source_run_id:
        messages.error(request, "This journey has no analysis run yet.")
        return redirect("importer:crm_duplicate_journey")

    mutation_journal = _mutation_journal_for_session(workflow_session)
    root_raw = options.get("apply_root_form_instance") or options.get(
        "orchestrator_form_instance"
    )
    try:
        root_form_instance = UUID(str(root_raw)) if root_raw else uuid4()
    except (TypeError, ValueError):
        root_form_instance = uuid4()

    # Auto-disposition is POST-only on progress. GET review never journals it.
    # Recover a completed mutation, but do not block surfacing of below-threshold
    # groups while auto-merge is still pending.
    options = dict(workflow_session.options or {})
    if _auto_merge_pending(options):
        recovered = _recover_auto_disposition_from_mutation(
            workflow_session,
            journal=mutation_journal,
            root_form_instance=root_form_instance,
            threshold=int(_session_auto_merge_threshold(options) or 90),
            source_run_id=source_run_id,
        )
        if recovered is not None:
            options = recovered
        if options.get("manual_queue_empty") and not _auto_merge_pending(options):
            return redirect(
                "importer:crm_duplicate_journey_merge",
                session_id=workflow_session.id,
            )

    try:
        review_run_id, _handoff_id = _ensure_review_workflow(
            workflow_session=workflow_session,
            client=client,
            owner_session=owner_session,
            source_run_id=source_run_id,
            journal=mutation_journal,
            root_form_instance=root_form_instance,
        )
        options = dict(workflow_session.options or {})
        review_run_id = str(options.get("review_run_id") or review_run_id or "").strip()
    except (*CLIENT_ERRORS, ValueError) as exc:
        messages.error(request, str(exc))
        return redirect(
            "importer:crm_duplicate_journey_progress",
            session_id=workflow_session.id,
        )

    run_id = review_run_id
    cursor = None
    form_errors: list[str] = []
    posted = request.POST if request.method == "POST" else None
    summary_amend_post = (
        request.method == "POST"
        and posted is not None
        and str(posted.get("summary_action") or "").strip() == "amend"
    )
    end_early_post = (
        request.method == "POST"
        and posted is not None
        and str(posted.get("review_submit") or "").strip() == "end_early"
    )
    auto_disposition_cta_post = (
        request.method == "POST"
        and posted is not None
        and str(posted.get(AUTO_DISPOSITION_CTA_FIELD) or "").strip() == "1"
    )
    if auto_disposition_cta_post:
        try:
            return _retry_on_sqlite_lock(
                lambda: _handle_auto_disposition_cta_post(
                    request,
                    workflow_session=workflow_session,
                    journal=mutation_journal,
                    client=client,
                    owner_session=owner_session,
                    owner_id=owner_id,
                    source_run_id=source_run_id,
                    review_run_id=str(run_id or ""),
                    options=options,
                )
            )
        except (*CLIENT_ERRORS, FormTokenError, ValueError) as exc:
            form_errors.append(str(exc))
            messages.error(request, str(exc))

    cta_ctx = _auto_disposition_cta_context(
        owner_id=owner_id,
        workflow_session=workflow_session,
        journal=mutation_journal,
        owner_session=owner_session,
        client=client,
        source_run_id=source_run_id,
        review_run_id=str(run_id or ""),
        options=options,
    )

    # POST: validate the frozen form token and posted window binding *before*
    # fetching the next undecided window, so exact retry still replays the
    # journaled submit after the backend has advanced.
    # Completed-review disposition amend is handled after the window GET.
    if (
        request.method == "POST"
        and posted is not None
        and end_early_post
        and not summary_amend_post
        and not auto_disposition_cta_post
    ):
        try:
            posted_revision = int(posted.get("expected_revision") or 0)
            posted_window_id = str(posted.get("window_id") or "").strip()
            posted_window_digest = str(posted.get("window_digest") or "").strip()
            if not posted_window_id or not posted_window_digest:
                raise ValueError("This review form is missing its window binding.")
            review_identity = (
                f"crm-dupe-4b:review:{owner_session}:{run_id}:"
                f"{posted_window_id}:{posted_window_digest}"
            )
            token = str(posted.get("form_token") or "").strip()
            decoded = decode_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=REVIEW_ACTION,
            )
            claims = validate_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=REVIEW_ACTION,
                action_id=posted_window_id,
                logical_action_identity=review_identity,
                logical_action_generation=int(
                    decoded.get("logical_action_generation") or 0
                ),
            )
            try:
                form_root = UUID(str(root_raw)) if root_raw else claims["form_instance"]
            except (TypeError, ValueError):
                form_root = claims["form_instance"]
            receipt = _dispatch_json_mutation(
                journal=mutation_journal,
                mutation_kind="submit_duplicate_review_end_early",
                route=f"/v1/workflows/{run_id}/duplicate-review-end-early",
                body={
                    "window_id": posted_window_id,
                    "window_digest": posted_window_digest,
                    "expected_revision": posted_revision,
                },
                owner_session=owner_session,
                client=client,
                form_instance=_step_form_instance(
                    form_root, f"review-end-early:{posted_window_id}"
                ),
                logical_action_identity=_step_identity(
                    step="review-end-early",
                    owner_session=owner_session,
                    form_instance=form_root,
                    resource=f"{run_id}:{posted_window_id}:{posted_window_digest}",
                ),
                resource_identity=run_id,
            )
            cursor_key = _review_cursor_session_key(run_id)
            next_key = _review_next_cursor_session_key(run_id)
            request.session.pop(cursor_key, None)
            request.session.pop(next_key, None)
            request.session.pop("efg_review_cursor", None)
            request.session.pop("efg_review_next_cursor", None)
            messages.success(
                request,
                "Review ended early. Remaining groups will not be merged. "
                "Inspect the plan before writing to your CRM.",
            )
            return redirect(
                "importer:crm_duplicate_journey_review",
                session_id=workflow_session.id,
            )
        except (*CLIENT_ERRORS, ValueError) as exc:
            form_errors.append(str(exc))
            messages.error(request, str(exc))

    if (
        request.method == "POST"
        and posted is not None
        and not summary_amend_post
        and not auto_disposition_cta_post
        and not end_early_post
    ):
        try:
            posted_window_id = str(posted.get("window_id") or "").strip()
            posted_window_digest = str(posted.get("window_digest") or "").strip()
            posted_revision = int(posted.get("expected_revision") or 0)
            if not posted_window_id or not posted_window_digest:
                raise ValueError("This review form is missing its window binding.")
            review_identity = (
                f"crm-dupe-4b:review:{owner_session}:{run_id}:"
                f"{posted_window_id}:{posted_window_digest}"
            )
            token = str(posted.get("form_token") or "").strip()
            decoded = decode_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=REVIEW_ACTION,
            )
            claims = validate_form_token(
                token,
                owner_id=owner_id,
                session=workflow_session,
                action_kind=REVIEW_ACTION,
                action_id=posted_window_id,
                logical_action_identity=review_identity,
                logical_action_generation=int(
                    decoded.get("logical_action_generation") or 0
                ),
            )
            decisions = _decisions_from_posted_form(posted)
            body = {
                "window_id": posted_window_id,
                "window_digest": posted_window_digest,
                "expected_revision": posted_revision,
                "decisions": decisions,
            }
            try:
                form_root = UUID(str(root_raw)) if root_raw else claims["form_instance"]
            except (TypeError, ValueError):
                form_root = claims["form_instance"]
            receipt = _dispatch_json_mutation(
                journal=mutation_journal,
                mutation_kind="submit_duplicate_review_window",
                route=f"/v1/workflows/{run_id}/duplicate-review-window",
                body=body,
                owner_session=owner_session,
                client=client,
                form_instance=_step_form_instance(
                    form_root, f"review:{posted_window_id}"
                ),
                logical_action_identity=_step_identity(
                    step="review-window",
                    owner_session=owner_session,
                    form_instance=form_root,
                    resource=(f"{run_id}:{posted_window_id}:{posted_window_digest}"),
                ),
                resource_identity=run_id,
            )
            return _review_window_redirect_after_submit(
                request,
                workflow_session=workflow_session,
                receipt=receipt,
                run_id=run_id,
            )
        except (*CLIENT_ERRORS, ValueError) as exc:
            form_errors.append(str(exc))
            messages.error(request, str(exc))

    try:
        cursor = str(request.GET.get("cursor") or "").strip() or None
        if cursor is None:
            cursor = (
                str(request.session.get(_review_cursor_session_key(run_id)) or "").strip()
                or None
            )
        window = client.duplicate_review_window(
            run_id, owner_session=owner_session, cursor=cursor
        )
        if isinstance(window, dict) and window.get("outcome") == "next_window":
            request.session[_review_next_cursor_session_key(run_id)] = (
                window.get("next_cursor") or ""
            )
    except CLIENT_ERRORS as exc:
        code = str(getattr(exc, "error_code", None) or getattr(exc, "code", "") or "")
        if code == "duplicate_review_window_not_ready":
            source_projection = {}
            try:
                source_projection = client.workflow(
                    source_run_id, owner_session=owner_session
                )
            except CLIENT_ERRORS:
                source_projection = {}
            source_status = str(
                (source_projection or {}).get("status")
                or (source_projection or {}).get("workflow_status")
                or ""
            )
            source_stage = str((source_projection or {}).get("stage") or "")
            if source_status == "failed" or _is_failed(source_projection):
                messages.error(
                    request,
                    _progress_message(source_projection)
                    or "Analysis failed. Open progress to see the saved error.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
            if source_status == "paused_unknown" or source_stage == "paused_unknown":
                messages.info(
                    request,
                    _progress_message(source_projection)
                    or "Analysis is paused. Continue from progress to resume.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_progress",
                    session_id=workflow_session.id,
                )
            progress_ctx = _analysis_progress_context(source_projection)
            ready = progress_ctx.get("analysis_progress_review_groups_ready")
            total = progress_ctx.get("analysis_progress_review_groups_total")
            window_ready = (
                progress_ctx.get("analysis_progress_review_window_ready") is True
            )
            if _auto_merge_pending(options) and window_ready:
                wait_copy = _auto_queued_review_copy()
                if _analysis_complete_for_auto_disposition(source_projection):
                    progress_url = reverse(
                        "importer:crm_duplicate_journey_progress",
                        kwargs={"session_id": workflow_session.id},
                    )
                    return redirect(f"{progress_url}?auto_queued=1")
                return render(
                    request,
                    "importer/crm_duplicate_journey_review.html",
                    {
                        "session": workflow_session,
                        "heading": "Review duplicate groups",
                        "run_id": run_id,
                        "frontier_wait": True,
                        "auto_queued_wait": True,
                        "frontier_wait_copy": wait_copy,
                        "refresh_url": reverse(
                            "importer:crm_duplicate_journey_review",
                            kwargs={"session_id": workflow_session.id},
                        ),
                        "progress_url": reverse(
                            "importer:crm_duplicate_journey_progress",
                            kwargs={"session_id": workflow_session.id},
                        ),
                        "workflow_url": reverse(
                            "importer:workflow",
                            kwargs={"session_id": workflow_session.id},
                        ),
                        "start_url": reverse("importer:crm_duplicate_journey"),
                        "form_errors": form_errors,
                        "cards": [],
                        "window": {},
                        "decided_group_count": 0,
                        "remaining_group_count": 0,
                        "form_token": "",
                        "group_order": "",
                        **progress_ctx,
                        **cta_ctx,
                    },
                )
            if (
                isinstance(ready, int)
                and isinstance(total, int)
                and not isinstance(ready, bool)
                and not isinstance(total, bool)
            ):
                wait_copy = (
                    f"Preparing the next groups to review — {ready:,} of "
                    f"{total:,} ready"
                )
            else:
                wait_copy = "Preparing the next groups to review."
            return render(
                request,
                "importer/crm_duplicate_journey_review.html",
                {
                    "session": workflow_session,
                    "heading": "Review duplicate groups",
                    "run_id": run_id,
                    "frontier_wait": True,
                    "frontier_wait_copy": wait_copy,
                    "refresh_url": reverse(
                        "importer:crm_duplicate_journey_review",
                        kwargs={"session_id": workflow_session.id},
                    ),
                    "progress_url": reverse(
                        "importer:crm_duplicate_journey_progress",
                        kwargs={"session_id": workflow_session.id},
                    ),
                    "workflow_url": reverse(
                        "importer:workflow",
                        kwargs={"session_id": workflow_session.id},
                    ),
                    "start_url": reverse("importer:crm_duplicate_journey"),
                    "form_errors": form_errors,
                    "cards": [],
                    "window": {},
                    "decided_group_count": 0,
                    "remaining_group_count": 0,
                    "form_token": "",
                    "group_order": "",
                    **progress_ctx,
                    **cta_ctx,
                },
            )
        messages.error(request, str(exc))
        return redirect(
            "importer:crm_duplicate_journey_progress",
            session_id=workflow_session.id,
        )

    if _is_review_complete_payload(window) or not list(window.get("groups") or []):
        # Completed review: inspect + optionally amend dispositions (POST).
        # Never freezes the merge plan here (that is a separate merge POST).
        summary_errors: list[str] = list(form_errors)
        if (
            request.method == "POST"
            and str(request.POST.get("summary_action") or "").strip() == "amend"
        ):
            try:
                expected_revision = int(
                    str(request.POST.get("expected_revision") or "0")
                )
                expected_decision_digest = str(
                    request.POST.get("expected_decision_set_content_digest") or ""
                ).strip()
                reviewed_result_digest = str(
                    request.POST.get("reviewed_result_content_digest") or ""
                ).strip()
                window_token = str(
                    request.POST.get("window_token") or ""
                ).strip()
                if not (
                    expected_decision_digest
                    and reviewed_result_digest
                    and window_token
                ):
                    raise ValueError(
                        "This disposition window is missing its bound identity."
                    )
                amend_identity = _reviewed_disposition_window_amend_identity(
                    owner_session=owner_session,
                    review_run_id=run_id,
                    expected_revision=expected_revision,
                    reviewed_result_content_digest=reviewed_result_digest,
                    decision_set_content_digest=expected_decision_digest,
                    window_token=window_token,
                )
                token = str(request.POST.get("form_token") or "").strip()
                decoded = decode_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=AMEND_ACTION,
                )
                claims = validate_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=AMEND_ACTION,
                    action_id=f"amend:{run_id}",
                    logical_action_identity=amend_identity,
                    logical_action_generation=int(
                        decoded.get("logical_action_generation") or 0
                    ),
                )
                decisions = _amend_decisions_from_posted_summary(request.POST)
                try:
                    form_root = (
                        UUID(str(root_raw)) if root_raw else claims["form_instance"]
                    )
                except (TypeError, ValueError):
                    form_root = claims["form_instance"]
                amend_body = {
                    "expected_revision": expected_revision,
                    "expected_decision_set_content_digest": (
                        expected_decision_digest
                    ),
                    "reviewed_result_content_digest": reviewed_result_digest,
                    "window_token": window_token,
                    "decisions": decisions,
                }
                amend_form_instance = _step_form_instance(
                    form_root,
                    f"amend-window:{amend_identity.rsplit(':', 1)[-1]}",
                )
                amend_mutation = _preclaim_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="amend_duplicate_reviewed_disposition_window",
                    route=(
                        f"/v1/workflows/{run_id}"
                        "/duplicate-reviewed-dispositions-window"
                    ),
                    body=amend_body,
                    owner_session=owner_session,
                    form_instance=amend_form_instance,
                    logical_action_identity=amend_identity,
                    resource_identity=run_id,
                )
                amend_receipt = _dispatch_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="amend_duplicate_reviewed_disposition_window",
                    route=(
                        f"/v1/workflows/{run_id}"
                        "/duplicate-reviewed-dispositions-window"
                    ),
                    body=amend_body,
                    owner_session=owner_session,
                    client=client,
                    form_instance=amend_form_instance,
                    logical_action_identity=amend_identity,
                    resource_identity=run_id,
                )
                # Same CAS helper as invalidate. Amend receipts have no
                # revoked ids, so this is a no-op unless a just-applied
                # invalidate already listed this continuation.
                _cas_apply_invalidate_lease(
                    workflow_session=workflow_session,
                    mutation_id=str(amend_mutation.id),
                    revoked_continuation_run_ids=_revoked_continuation_run_ids(
                        amend_receipt
                    ),
                )
                messages.success(
                    request,
                    "Dispositions updated. Approve the merge plan when ready.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_review",
                    session_id=workflow_session.id,
                )
            except (*CLIENT_ERRORS, ValueError) as exc:
                summary_errors.append(str(exc))
                messages.error(request, str(exc))

        reviewed_result = None
        try:
            reviewed_result = client.duplicate_reviewed_result_summary(
                run_id, owner_session=owner_session
            )
        except CLIENT_ERRORS as exc:
            summary_errors.append(str(exc))
        counts = _merge_summary_counts(reviewed_result)
        if counts.get("auto_merge_min_confidence") is None:
            counts["auto_merge_min_confidence"] = _session_auto_merge_threshold(
                dict(workflow_session.options or {})
            )
        edit_dispositions = _review_summary_edit_requested(request)
        disposition_window = None
        requested_cursor = str(
            (
                request.POST.get("window_cursor")
                if request.method == "POST"
                else request.GET.get("disposition_cursor")
            )
            or ""
        ).strip()
        review_wf_rev = int((reviewed_result or {}).get("expected_revision") or 0)
        if edit_dispositions:
            try:
                disposition_window = client.duplicate_reviewed_disposition_window(
                    run_id,
                    owner_session=owner_session,
                    cursor=requested_cursor or None,
                )
            except CLIENT_ERRORS as exc:
                summary_errors.append(str(exc))
            review_wf_rev = int(
                (disposition_window or {}).get("expected_revision") or review_wf_rev
            )
        oversized_components, oversized_error = (
            _load_source_oversized_quarantine_components(
                workflow_session=workflow_session,
                client=client,
                owner_session=owner_session,
                source_run_id=source_run_id,
            )
        )
        if oversized_error:
            summary_errors.append(oversized_error)
            messages.error(request, oversized_error)
        amend_token = ""
        review_url = reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": workflow_session.id},
        )

        def disposition_page_url(cursor, *, edit_group: str = ""):
            token = str(cursor or "").strip()
            query = {"edit": "1"}
            if token:
                query["disposition_cursor"] = token
            if edit_group:
                query["edit_group"] = edit_group
            return f"{review_url}?{urlencode(query)}"

        requested_group = str(
            (
                request.POST.get("expanded_group_id")
                if request.method == "POST"
                else request.GET.get("edit_group")
            )
            or ""
        ).strip()
        if edit_dispositions and disposition_window is not None:
            amend_identity = _reviewed_disposition_window_amend_identity(
                owner_session=owner_session,
                review_run_id=run_id,
                expected_revision=review_wf_rev,
                reviewed_result_content_digest=str(
                    (disposition_window or {}).get("reviewed_result_content_digest")
                    or (reviewed_result or {}).get("reviewed_result_content_digest")
                    or ""
                ),
                decision_set_content_digest=str(
                    (disposition_window or {}).get("decision_set_content_digest")
                    or (reviewed_result or {}).get("decision_set_content_digest")
                    or ""
                ),
                window_token=str(
                    (disposition_window or {}).get("window_token") or ""
                ),
            )
            amend_token = issue_form_token(
                owner_id=owner_id,
                session=workflow_session,
                action_kind=AMEND_ACTION,
                action_id=f"amend:{run_id}",
                logical_action_identity=amend_identity,
                logical_action_generation=action_generation(
                    workflow_session, amend_identity
                ),
            )
            local_window = dict(disposition_window)
            local_dispositions = []
            active_group_ids = {
                str(item.get("group_id") or "")
                for item in disposition_window.get("dispositions") or []
            }
            if requested_group not in active_group_ids:
                requested_group = ""
            for raw in disposition_window.get("dispositions") or []:
                item = dict(raw)
                item["edit_url"] = disposition_page_url(
                    requested_cursor, edit_group=str(item.get("group_id") or "")
                )
                local_dispositions.append(item)
            local_window["dispositions"] = local_dispositions
            disposition_window = local_window

        return render(
            request,
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "session": workflow_session,
                "run_id": run_id,
                "window": window,
                "reviewed_result": reviewed_result,
                "visible_dispositions": _visible_disposition_rows(
                    (disposition_window or {}).get("dispositions")
                    or (reviewed_result or {}).get("dispositions")
                ),
                "hide_declined_group_count": True,
                "auto_approved_group_count": counts["auto_approved_group_count"],
                "operator_approved_group_count": counts[
                    "operator_approved_group_count"
                ],
                "edit_dispositions": edit_dispositions,
                "edit_dispositions_label": EDIT_DISPOSITIONS_LABEL,
                "edit_dispositions_url": f"{review_url}?{urlencode({'edit': '1'})}",
                "disposition_window": disposition_window,
                "oversized_quarantine_components": oversized_components,
                "oversized_quarantine_unavailable": oversized_error,
                "form_token": amend_token,
                "form_errors": summary_errors,
                "expected_revision": review_wf_rev,
                "window_cursor": requested_cursor,
                "expanded_group_id": requested_group,
                "next_window_url": (
                    disposition_page_url(
                        (disposition_window or {}).get("next_cursor")
                    )
                    if edit_dispositions and (disposition_window or {}).get("next_cursor")
                    else None
                ),
                "previous_window_url": (
                    disposition_page_url(
                        (disposition_window or {}).get("previous_cursor")
                    )
                    if edit_dispositions
                    and (disposition_window or {}).get("previous_cursor")
                    else None
                ),
                "terminal_message": (
                    (
                        window.get("terminal_message")
                        if isinstance(window, dict)
                        else None
                    )
                    or "All duplicate groups have been reviewed."
                ),
                "merge_url": reverse(
                    "importer:crm_duplicate_journey_merge",
                    kwargs={"session_id": workflow_session.id},
                ),
                "progress_url": reverse(
                    "importer:crm_duplicate_journey_progress",
                    kwargs={"session_id": workflow_session.id},
                ),
                "workflow_url": reverse(
                    "importer:workflow",
                    kwargs={"session_id": workflow_session.id},
                ),
                "start_url": reverse("importer:crm_duplicate_journey"),
            },
        )

    groups = list(window.get("groups") or [])

    remaining = int(window.get("remaining_group_count") or 0)
    is_final_window = remaining <= len(groups)
    window_id = str(window.get("window_id") or "")
    window_digest = str(window.get("window_digest") or "")
    expected_revision = int(window.get("expected_revision") or 0)
    review_identity = (
        f"crm-dupe-4b:review:{owner_session}:{run_id}:{window_id}:{window_digest}"
    )
    form_token = issue_form_token(
        owner_id=owner_id,
        session=workflow_session,
        action_kind=REVIEW_ACTION,
        action_id=window_id,
        logical_action_identity=review_identity,
        logical_action_generation=action_generation(workflow_session, review_identity),
    )
    # After a failed POST, preserve the submitted token when it still targets
    # this window so the operator can fix choices without losing exact-retry.
    if posted is not None and form_errors:
        submitted = str(posted.get("form_token") or "").strip()
        posted_wid = str(posted.get("window_id") or "").strip()
        if submitted and posted_wid == window_id:
            form_token = submitted

    cards = _review_group_cards(
        window,
        posted=posted if form_errors else None,
    )

    group_start = int(window.get("group_start") or 1)
    group_end = int(window.get("group_end") or group_start)
    total = int(window.get("total_group_count") or group_end)
    cta_label = (
        "Save final groups and review merge plan"
        if is_final_window
        else "Save these 5 and review next 5"
    )
    if not is_final_window and len(groups) != 5:
        cta_label = f"Save these {len(groups)} and review next groups"

    return render(
        request,
        "importer/crm_duplicate_journey_review.html",
        {
            "session": workflow_session,
            "run_id": run_id,
            "window": window,
            "cards": cards,
            "form_token": form_token,
            "form_errors": form_errors,
            "group_start": group_start,
            "group_end": group_end,
            "total_group_count": total,
            "decided_group_count": int(window.get("decided_group_count") or 0),
            "remaining_group_count": remaining,
            "is_final_window": is_final_window,
            "cta_label": cta_label,
            "heading": f"Review duplicate groups {group_start}–{group_end} of {total}",
            "group_order": ",".join(card["group_id"] for card in cards),
            "review_cursor": cursor or "",
            "review_next_cursor": (
                (window.get("next_cursor") if isinstance(window, dict) else "")
                or ""
            ),
            # Secondary exit after at least one window is already decided.
            "show_save_and_exit": int(window.get("decided_group_count") or 0) > 0,
            "show_end_early": (
                max(
                    0,
                    int(window.get("decided_group_count") or 0)
                    - int(options.get("auto_approved_group_count") or 0),
                )
                >= 5
                and int(window.get("remaining_group_count") or 0) > 0
            ),
            "auto_approved_group_count": int(
                options.get("auto_approved_group_count") or 0
            ),
            "auto_merge_min_confidence": _session_auto_merge_threshold(options),
            "progress_url": reverse(
                "importer:crm_duplicate_journey_progress",
                kwargs={"session_id": workflow_session.id},
            ),
            "workflow_url": reverse(
                "importer:workflow",
                kwargs={"session_id": workflow_session.id},
            ),
            "start_url": reverse("importer:crm_duplicate_journey"),
            **cta_ctx,
        },
    )


def _mode_labels() -> dict[str, str]:
    return {
        "preview": "Preview merge plan (no CRM changes)",
        "dry_run": "Dry run (test merges without applying)",
        "execute": "Apply merges in the connected CRM",
        "disabled": "Do not change CRM records",
    }


def _merge_plan_lease(workflow_session: ImportSession) -> CrmDuplicateMergePlanLease:
    """Return the 1:1 merge-plan lease, creating an empty epoch-0 row if needed."""

    def _load() -> CrmDuplicateMergePlanLease:
        lease, _created = CrmDuplicateMergePlanLease.objects.get_or_create(
            session=workflow_session
        )
        return lease

    return _retry_on_sqlite_lock(_load)


def _forget_legacy_merge_plan_options(workflow_session: ImportSession) -> None:
    """Stop reading/writing retired options keys; drop leftovers after CAS."""

    options = dict(workflow_session.options or {})
    changed = False
    for key in (
        "merge_plan_finalized",
        "continuation_run_id",
        "decision_set_content_digest",
    ):
        if key in options:
            options.pop(key, None)
            changed = True
    if not changed:
        return
    workflow_session.options = options
    workflow_session.save(update_fields=["options", "updated_at"])


def _revoked_continuation_run_ids(receipt: dict | None) -> list[str]:
    if not isinstance(receipt, dict):
        return []
    raw = receipt.get("revoked_continuation_run_ids")
    nested = receipt.get("result")
    if raw is None and isinstance(nested, dict):
        raw = nested.get("revoked_continuation_run_ids")
    if not isinstance(raw, list):
        return []
    return [str(item).strip() for item in raw if str(item).strip()]


def _preclaim_json_mutation(
    *,
    journal: ImportSession,
    mutation_kind: str,
    route: str,
    body: dict,
    owner_session: str,
    form_instance: UUID,
    logical_action_identity: str,
    resource_identity: str,
    logical_action_generation: int = 0,
) -> ApiMutation:
    """Create or reuse the journal row before dispatch so CAS can bind its id."""

    payload = dict(body)
    payload["owner_session"] = owner_session

    def _create() -> ApiMutation:
        return create_or_reuse_mutation(
            session=journal,
            form_instance=form_instance,
            mutation_kind=mutation_kind,
            route=route,
            logical_action_identity=logical_action_identity,
            logical_action_generation=logical_action_generation,
            request_json=payload,
            resource_identity=resource_identity,
        )

    return _retry_on_sqlite_lock(_create)


def _cas_apply_invalidate_lease(
    *,
    workflow_session: ImportSession,
    mutation_id: str,
    revoked_continuation_run_ids: list[str],
) -> bool:
    """Once-only CAS: clear the exact continuation this mutation revoked."""

    _merge_plan_lease(workflow_session)
    mutation_key = str(mutation_id or "").strip()
    revoked = [item for item in revoked_continuation_run_ids if item]
    if not mutation_key or not revoked:
        return False

    def _update() -> int:
        return (
            CrmDuplicateMergePlanLease.objects.filter(session=workflow_session)
            .exclude(applied_invalidate_mutation_id=mutation_key)
            .filter(continuation_run_id__in=revoked)
            .exclude(continuation_run_id="")
            .update(
                continuation_run_id="",
                decision_set_content_digest="",
                applied_finalize_mutation_id="",
                applied_invalidate_mutation_id=mutation_key,
                epoch=F("epoch") + 1,
            )
        )

    updated = _retry_on_sqlite_lock(_update)
    if updated == 1:
        _retry_on_sqlite_lock(
            lambda: _forget_legacy_merge_plan_options(workflow_session)
        )
    return updated == 1


def _cas_persist_finalize_lease(
    *,
    workflow_session: ImportSession,
    mutation_id: str,
    token_epoch: int,
    continuation_run_id: str,
    decision_set_content_digest: str,
) -> bool:
    """CAS: bind this finalize mutation's continuation at the token epoch."""

    _merge_plan_lease(workflow_session)
    mutation_key = str(mutation_id or "").strip()
    continuation = str(continuation_run_id or "").strip()
    digest = str(decision_set_content_digest or "").strip()
    if not mutation_key or not continuation:
        return False

    def _update() -> int:
        return (
            CrmDuplicateMergePlanLease.objects.filter(
                session=workflow_session,
                epoch=int(token_epoch),
            )
            .filter(Q(continuation_run_id="") | Q(continuation_run_id=continuation))
            .filter(
                Q(applied_finalize_mutation_id="")
                | Q(applied_finalize_mutation_id=mutation_key)
            )
            .update(
                continuation_run_id=continuation,
                decision_set_content_digest=digest,
                applied_finalize_mutation_id=mutation_key,
            )
        )

    updated = _retry_on_sqlite_lock(_update)
    if updated == 1:
        _retry_on_sqlite_lock(
            lambda: _forget_legacy_merge_plan_options(workflow_session)
        )
    return updated == 1


def _visible_disposition_rows(items) -> list[dict[str, Any]]:
    """Presentation-only copy: drop declined rows. Persistence is unchanged."""

    visible: list[dict[str, Any]] = []
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("disposition") or "") == "declined":
            continue
        visible.append(raw)
    return visible


def _review_summary_edit_requested(request) -> bool:
    """True when the completed-review GET/POST should show the amend editor."""

    if request.method == "POST" and str(
        request.POST.get("summary_action") or ""
    ).strip() == "amend":
        return True
    if str(request.GET.get("edit") or "").strip() == "1":
        return True
    if str(request.GET.get("edit_group") or "").strip():
        return True
    if str(request.GET.get("disposition_cursor") or "").strip():
        return True
    return False


def _merge_summary_counts(reviewed_result: dict | None) -> dict[str, Any]:
    """Derive auto vs operator-approved counts for the merge summary UI."""

    if not isinstance(reviewed_result, dict):
        return {
            "auto_approved_group_count": 0,
            "operator_approved_group_count": 0,
            "auto_merge_min_confidence": None,
            "review_contract": None,
        }
    auto_count = int(reviewed_result.get("auto_approved_group_count") or 0)
    approved = int(reviewed_result.get("approved_merge_group_count") or 0)
    # Prefer disposition origins when present (v2); fall back to total−auto.
    origin_auto = 0
    origin_operator = 0
    saw_origin = False
    for item in reviewed_result.get("dispositions") or []:
        if not isinstance(item, dict):
            continue
        if str(item.get("disposition") or "") != "approved":
            continue
        origin = str(item.get("decision_origin") or "").strip()
        if origin:
            saw_origin = True
        if origin == "high_confidence_threshold":
            origin_auto += 1
        elif origin == "operator" or not origin:
            origin_operator += 1
    if saw_origin:
        auto_count = origin_auto
        operator_count = origin_operator
    else:
        operator_count = max(0, approved - auto_count)
    return {
        "auto_approved_group_count": auto_count,
        "operator_approved_group_count": operator_count,
        "auto_merge_min_confidence": None,
        "review_contract": reviewed_result.get("review_contract"),
    }


def _invalidate_merge_identity(
    *,
    epoch: int,
    owner_session: str,
    review_run_id: str,
) -> str:
    return f"crm-dupe-5a:invalidate:{int(epoch)}:{owner_session}:{review_run_id}"


def _merge_context_base(
    *,
    workflow_session: ImportSession,
    reviewed_result: dict | None,
    handoff: dict | None,
    supported_modes: list[str],
    form_token: str,
    form_errors: list[str],
    continuation_run_id: str,
    continuation_status: str,
    already_authorized: bool,
    effect_intent: dict | None,
    plan_frozen: bool,
    decision_set_content_digest: str | None,
    invalidate_form_token: str = "",
    oversized_quarantine_components: list | None = None,
    oversized_quarantine_unavailable: str = "",
    materialization_wait: bool = False,
    materialization_progress: dict | None = None,
) -> dict[str, Any]:
    options = dict(workflow_session.options or {})
    counts = _merge_summary_counts(reviewed_result)
    if counts.get("auto_merge_min_confidence") is None:
        counts["auto_merge_min_confidence"] = _session_auto_merge_threshold(options)
    return {
        "session": workflow_session,
        "reviewed_result": reviewed_result,
        "visible_dispositions": _visible_disposition_rows(
            (reviewed_result or {}).get("dispositions")
        ),
        "hide_declined_group_count": True,
        "oversized_quarantine_components": list(
            oversized_quarantine_components or []
        ),
        "oversized_quarantine_unavailable": oversized_quarantine_unavailable,
        "handoff": handoff,
        "supported_modes": supported_modes,
        "mode_labels": _mode_labels(),
        "form_token": form_token,
        "invalidate_form_token": invalidate_form_token,
        "form_errors": form_errors,
        "continuation_run_id": continuation_run_id,
        "continuation_status": continuation_status,
        "already_authorized": already_authorized,
        "effect_intent": effect_intent,
        "plan_frozen": plan_frozen,
        "decision_set_content_digest": decision_set_content_digest,
        "materialization_wait": materialization_wait,
        "materialization_progress": materialization_progress,
        "auto_approved_group_count": counts["auto_approved_group_count"],
        "operator_approved_group_count": counts["operator_approved_group_count"],
        "auto_merge_min_confidence": counts["auto_merge_min_confidence"],
        "review_contract": counts.get("review_contract"),
        "review_url": reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": workflow_session.id},
        ),
        "workflow_url": reverse(
            "importer:workflow",
            kwargs={"session_id": workflow_session.id},
        ),
        "start_url": reverse("importer:crm_duplicate_journey"),
        "merge_url": reverse(
            "importer:crm_duplicate_journey_merge",
            kwargs={"session_id": workflow_session.id},
        ),
        "field_fill_empty_sentence": FIELD_FILL_EMPTY_SENTENCE,
    }


@require_http_methods(["GET", "POST"])
def crm_duplicate_journey_merge(request, session_id):
    """Phase 5A: review summary, explicit finalize POST, then write authorization.

    GET is read-only (never dispatches mutations). Finalization and write
    authorization are separate CSRF-protected POSTs. Operators can inspect the
    reviewed result and invalidate a frozen plan before authorizing writes.
    """

    workflow_session = _workflow_session_for_request(request, session_id)
    options = dict(workflow_session.options or {})
    client = EasyImportsApiClient()
    owner_session = _owner_session(request)
    owner_id = owner_id_for_request(request)
    source_run_id, review_run_id = _source_and_review_run_ids(workflow_session)
    if not source_run_id:
        messages.error(request, "This journey has no analysis run yet.")
        return redirect("importer:crm_duplicate_journey")

    mutation_journal = _mutation_journal_for_session(workflow_session)
    root_raw = options.get("apply_root_form_instance") or options.get(
        "orchestrator_form_instance"
    )
    try:
        root_form_instance = UUID(str(root_raw)) if root_raw else uuid4()
    except (TypeError, ValueError):
        root_form_instance = uuid4()

    # Resolve review run without dispatching when ids are already frozen.
    # Starting a review workflow is a mutation and must not run on GET.
    handoff_id = str(options.get("review_handoff_id") or "").strip()
    if not review_run_id:
        if request.method == "GET":
            messages.info(
                request,
                "Open group review first so EasyImports can load the decided outcomes.",
            )
            return redirect(
                "importer:crm_duplicate_journey_review",
                session_id=workflow_session.id,
            )
        try:
            review_run_id, handoff_id = _ensure_review_workflow(
                workflow_session=workflow_session,
                client=client,
                owner_session=owner_session,
                source_run_id=source_run_id,
                journal=mutation_journal,
                root_form_instance=root_form_instance,
            )
            options = dict(workflow_session.options or {})
            handoff_id = str(options.get("review_handoff_id") or handoff_id or "")
        except (*CLIENT_ERRORS, ValueError) as exc:
            messages.error(request, str(exc))
            return redirect(
                "importer:crm_duplicate_journey_progress",
                session_id=workflow_session.id,
            )

    form_errors: list[str] = []
    lease = _merge_plan_lease(workflow_session)
    continuation_run_id = str(lease.continuation_run_id or "").strip()
    plan_frozen = bool(continuation_run_id)
    lease_epoch = int(lease.epoch or 0)

    # POST actions: finalize | authorize | invalidate
    if request.method == "POST":
        action = str(request.POST.get("merge_action") or "").strip()
        try:
            token = str(request.POST.get("form_token") or "").strip()
            if action == "invalidate":
                # Durably revoke freeze + mark continuation unusable so
                # amend-before-re-finalize cannot orphan a live continuation.
                if not handoff_id:
                    raise ValueError(
                        "Missing review handoff; cannot invalidate the freeze."
                    )
                review_wf = client.workflow(review_run_id, owner_session=owner_session)
                expected_revision = int(review_wf.get("revision") or 0)
                if not token:
                    raise FormTokenError(
                        "This form is stale. Reload it before continuing."
                    )
                decoded = decode_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                )
                token_epoch = int(decoded.get("logical_action_generation") or 0)
                invalidate_identity = _invalidate_merge_identity(
                    epoch=token_epoch,
                    owner_session=owner_session,
                    review_run_id=review_run_id,
                )
                claims = validate_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                    action_id=f"invalidate:{review_run_id}",
                    logical_action_identity=invalidate_identity,
                    logical_action_generation=token_epoch,
                )
                try:
                    form_root = (
                        UUID(str(root_raw)) if root_raw else claims["form_instance"]
                    )
                except (TypeError, ValueError):
                    form_root = claims["form_instance"]
                invalidate_form_instance = _step_form_instance(
                    form_root, f"invalidate:{token_epoch}"
                )
                invalidate_body = {
                    # Continuation is derived server-side from freeze stamps.
                    "expected_revision": expected_revision,
                }
                existing_invalidate = _retry_on_sqlite_lock(
                    lambda: (
                        ApiMutation.objects.filter(
                            session=mutation_journal,
                            form_instance=invalidate_form_instance,
                        )
                        .order_by("created_at", "id")
                        .first()
                    )
                )
                if existing_invalidate is not None and isinstance(
                    existing_invalidate.request_json, dict
                ):
                    # Exact-retry / stale replay: keep the frozen body so a
                    # later review revision cannot collide on form_instance.
                    invalidate_body = {
                        key: value
                        for key, value in existing_invalidate.request_json.items()
                        if key != "owner_session"
                    }
                invalidate_mutation = _preclaim_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="invalidate_duplicate_merge_plan_freeze",
                    route=(
                        f"/v1/workflows/{review_run_id}/"
                        "duplicate-merge-plan-invalidate"
                    ),
                    body=invalidate_body,
                    owner_session=owner_session,
                    form_instance=invalidate_form_instance,
                    logical_action_identity=invalidate_identity,
                    logical_action_generation=token_epoch,
                    resource_identity=review_run_id,
                )
                invalidate_receipt = _dispatch_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="invalidate_duplicate_merge_plan_freeze",
                    route=(
                        f"/v1/workflows/{review_run_id}/"
                        "duplicate-merge-plan-invalidate"
                    ),
                    body=invalidate_body,
                    owner_session=owner_session,
                    client=client,
                    form_instance=invalidate_form_instance,
                    logical_action_identity=invalidate_identity,
                    logical_action_generation=token_epoch,
                    resource_identity=review_run_id,
                )
                applied = _cas_apply_invalidate_lease(
                    workflow_session=workflow_session,
                    mutation_id=str(invalidate_mutation.id),
                    revoked_continuation_run_ids=_revoked_continuation_run_ids(
                        invalidate_receipt
                    ),
                )
                if applied:
                    messages.success(
                        request,
                        "Approved merge plan cleared. You may edit dispositions, "
                        "then approve again to regenerate digests.",
                    )
                else:
                    messages.info(
                        request,
                        "Returned to group review. You can approve the merge "
                        "plan again when ready.",
                    )
                return redirect(
                    "importer:crm_duplicate_journey_review",
                    session_id=workflow_session.id,
                )

            if action == "finalize":
                if not handoff_id:
                    raise ValueError(
                        "Missing review handoff binding; return to progress and reopen review."
                    )
                posted_summary_revision = int(
                    request.POST.get("summary_expected_revision") or -1
                )
                posted_summary_digest = str(
                    request.POST.get("summary_decision_set_content_digest") or ""
                ).strip()
                current_summary = client.duplicate_reviewed_result_summary(
                    review_run_id, owner_session=owner_session
                )
                expected_revision = int(current_summary.get("expected_revision") or -1)
                current_digest = str(
                    current_summary.get("decision_set_content_digest") or ""
                ).strip()
                if (
                    posted_summary_revision != expected_revision
                    or posted_summary_digest != current_digest
                ):
                    raise ValueError(
                        "The reviewed-result summary is stale. Reload it before approving "
                        "the merge plan."
                    )
                finalize_identity = (
                    f"crm-dupe-5a:finalize:{lease_epoch}:{owner_session}:"
                    f"{source_run_id}:{review_run_id}:{handoff_id or 'none'}:"
                    f"{expected_revision}:{current_digest}"
                )
                decoded = decode_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                )
                claims = validate_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                    action_id=f"finalize:{review_run_id}",
                    logical_action_identity=finalize_identity,
                    logical_action_generation=lease_epoch,
                )
                try:
                    form_root = (
                        UUID(str(root_raw)) if root_raw else claims["form_instance"]
                    )
                except (TypeError, ValueError):
                    form_root = claims["form_instance"]
                finalize_form_instance = _step_form_instance(
                    form_root, f"finalize:{lease_epoch}"
                )
                finalize_body = {
                    "source_run_id": source_run_id,
                    "review_handoff_id": handoff_id,
                    "expected_revision": expected_revision,
                }
                finalize_mutation = _preclaim_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="finalize_duplicate_merge_plan_handoff",
                    route=(
                        f"/v1/workflows/{review_run_id}/duplicate-merge-plan-handoff"
                    ),
                    body=finalize_body,
                    owner_session=owner_session,
                    form_instance=finalize_form_instance,
                    logical_action_identity=finalize_identity,
                    logical_action_generation=lease_epoch,
                    resource_identity=review_run_id,
                )
                receipt = _dispatch_json_mutation(
                    journal=mutation_journal,
                    mutation_kind="finalize_duplicate_merge_plan_handoff",
                    route=(
                        f"/v1/workflows/{review_run_id}/duplicate-merge-plan-handoff"
                    ),
                    body=finalize_body,
                    owner_session=owner_session,
                    client=client,
                    form_instance=finalize_form_instance,
                    logical_action_identity=finalize_identity,
                    logical_action_generation=lease_epoch,
                    resource_identity=review_run_id,
                )
                handoff_body = (receipt.get("result") or {}).get("merge_plan_handoff")
                if not isinstance(handoff_body, dict):
                    raise ValueError("Merge-plan handoff was not returned.")
                new_continuation = str(
                    handoff_body.get("continuation_run_id") or ""
                ).strip()
                new_digest = str(
                    (handoff_body.get("reviewed_result") or {}).get(
                        "decision_set_content_digest"
                    )
                    or ""
                ).strip()
                persisted = _cas_persist_finalize_lease(
                    workflow_session=workflow_session,
                    mutation_id=str(finalize_mutation.id),
                    token_epoch=lease_epoch,
                    continuation_run_id=new_continuation,
                    decision_set_content_digest=new_digest,
                )
                if not persisted:
                    lease.refresh_from_db()
                    replay = (
                        str(lease.applied_finalize_mutation_id)
                        == str(finalize_mutation.id)
                        and str(lease.continuation_run_id or "").strip()
                        == new_continuation
                    )
                    if not replay:
                        raise ValueError(
                            "The merge plan could not be approved. Reload this "
                            "page and try again."
                        )
                handoff_id_ds = str(
                    (handoff_body.get("decision_set_handoff") or {}).get("handoff_id")
                    or ""
                ).strip()
                if handoff_id_ds:
                    options = dict(workflow_session.options or {})
                    options["decision_set_handoff_id"] = handoff_id_ds
                    workflow_session.options = options
                    workflow_session.save(update_fields=["options", "updated_at"])
                messages.success(
                    request,
                    "Reviewed outcomes approved into the merge plan. "
                    "Choose a write mode to continue.",
                )
                return redirect(
                    "importer:crm_duplicate_journey_merge",
                    session_id=workflow_session.id,
                )

            if action == "authorize":
                if not continuation_run_id:
                    raise ValueError(
                        "Approve the merge plan before authorizing CRM writes."
                    )
                cont_wf = client.workflow(
                    continuation_run_id, owner_session=owner_session
                )
                intent = cont_wf.get("effect_intent") or {}
                if not isinstance(intent, dict) or not intent.get("intent_id"):
                    raise ValueError(
                        "Merge authorization is not available for this journey yet."
                    )
                supported_modes = [
                    str(mode)
                    for mode in (intent.get("supported_modes") or [])
                    if str(mode) in _DUPLICATE_EXECUTION_MODES
                    and str(mode) != "disabled"
                ]
                selected_mode = str(request.POST.get("selected_mode") or "").strip()
                if selected_mode not in supported_modes:
                    raise ValueError(
                        "Choose a merge mode supported by this CRM connection."
                    )
                # Must match GET-issued token identity (mode is editable form data,
                # not part of the signed logical action identity).
                auth_identity = (
                    f"crm-dupe-5a:authorize:{owner_session}:{continuation_run_id}:"
                    f"{str(intent.get('intent_id') or 'none')}"
                )
                decoded = decode_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                )
                claims = validate_form_token(
                    token,
                    owner_id=owner_id,
                    session=workflow_session,
                    action_kind=MERGE_ACTION,
                    action_id=f"authorize:{continuation_run_id}",
                    logical_action_identity=auth_identity,
                    logical_action_generation=int(
                        decoded.get("logical_action_generation") or 0
                    ),
                )
                try:
                    form_root = (
                        UUID(str(root_raw)) if root_raw else claims["form_instance"]
                    )
                except (TypeError, ValueError):
                    form_root = claims["form_instance"]
                body = {
                    "expected_revision": int(cont_wf.get("revision") or 0),
                    "intent_id": intent["intent_id"],
                    "track": intent["track"],
                    "maximum_mode": intent["maximum_mode"],
                    "selected_mode": selected_mode,
                    "target_provider_id": intent["target_provider_id"],
                    "target_fingerprint": intent["target_fingerprint"],
                    "work_digest": intent["work_digest"],
                    "confirmation": intent["confirmation"],
                }
                # Journal authorize on the workflow session (not the RA journal)
                # so workflow mutation_status polling can reconcile async Prefer:
                # respond-async effects (same boundary as importer:authorize_effect).
                try:
                    receipt = _dispatch_json_mutation(
                        journal=workflow_session,
                        mutation_kind="authorize_effect",
                        route=(
                            f"/v1/workflows/{continuation_run_id}/"
                            "effect-authorizations"
                        ),
                        body=body,
                        owner_session=owner_session,
                        client=client,
                        form_instance=_step_form_instance(
                            form_root, f"authorize:{selected_mode}"
                        ),
                        logical_action_identity=_step_identity(
                            step="authorize-merge",
                            owner_session=owner_session,
                            form_instance=form_root,
                            resource=(
                                f"{continuation_run_id}:{intent['intent_id']}:"
                                f"{selected_mode}"
                            ),
                        ),
                        resource_identity=continuation_run_id,
                    )
                except ApiOperationInProgressError as exc:
                    messages.info(
                        request,
                        str(exc)
                        or "EasyImports is processing this merge authorization.",
                    )
                    return redirect("importer:workflow", session_id=workflow_session.id)
                # Sync completion path (tests / non-async clients): refresh
                # continuation projection so terminal evidence is visible.
                if isinstance(receipt, dict) and receipt.get("run_id"):
                    try:
                        cont_proj = client.workflow(
                            str(receipt["run_id"]),
                            owner_session=owner_session,
                        )
                        _store_continuation_projection(
                            workflow_session,
                            cont_proj,
                            run_id=str(receipt["run_id"]),
                            # Keep analysis primary active (Phase 3); terminal
                            # display prefers this continuation without steal.
                            make_active=False,
                        )
                    except CLIENT_ERRORS:
                        pass
                messages.success(
                    request,
                    f"Merge authorization recorded as "
                    f"{selected_mode.replace('_', ' ')}.",
                )
                return redirect("importer:workflow", session_id=workflow_session.id)

            raise ValueError("Unknown merge action.")
        except (*CLIENT_ERRORS, ValueError) as exc:
            form_errors.append(str(exc))
            messages.error(request, str(exc))
            options = dict(workflow_session.options or {})
            lease = _merge_plan_lease(workflow_session)
            continuation_run_id = str(lease.continuation_run_id or "").strip()
            plan_frozen = bool(continuation_run_id)
            lease_epoch = int(lease.epoch or 0)

    # GET (and POST error redisplay): read-only loads only.
    reviewed = None
    materialization_wait = False
    materialization_progress = None
    try:
        reviewed = client.duplicate_reviewed_result_summary(
            review_run_id, owner_session=owner_session
        )
    except CLIENT_ERRORS as exc:
        try:
            materialization_progress = client.duplicate_reviewed_result_progress(
                review_run_id, owner_session=owner_session
            )
        except CLIENT_ERRORS:
            materialization_progress = None
        status = str(
            (materialization_progress or {}).get("status") or ""
        )
        if status in {"queued", "running"}:
            materialization_wait = True
        else:
            form_errors.append(str(exc))

    cont_wf: dict[str, Any] = {}
    intent = None
    supported_modes: list[str] = []
    continuation_status = ""
    if continuation_run_id:
        try:
            cont_wf = client.workflow(continuation_run_id, owner_session=owner_session)
            # Read-only reload must retain CONTINUATION role + source lineage;
            # never default the merge continuation to primary.
            _store_continuation_projection(
                workflow_session,
                cont_wf,
                run_id=continuation_run_id,
            )
            intent = cont_wf.get("effect_intent")
            continuation_status = str(cont_wf.get("status") or "")
            if isinstance(intent, dict):
                supported_modes = [
                    str(mode)
                    for mode in (intent.get("supported_modes") or [])
                    if str(mode) in _DUPLICATE_EXECUTION_MODES
                    and str(mode) != "disabled"
                ]
        except CLIENT_ERRORS as exc:
            form_errors.append(str(exc))
            cont_wf = {"status": "unavailable", "effect_intent": None}

    already_authorized = continuation_status in {
        "succeeded",
        "running",
        "paused_unknown",
        "paused_verification",
    }

    # Form token scopes the next allowed action. Generation is the lease
    # epoch so GET identity and finalize/invalidate dispatch share it.
    if plan_frozen and not already_authorized:
        action_id = f"authorize:{continuation_run_id}"
        merge_identity = (
            f"crm-dupe-5a:authorize:{owner_session}:{continuation_run_id}:"
            f"{str((intent or {}).get('intent_id') or 'none')}"
        )
    else:
        action_id = f"finalize:{review_run_id}"
        merge_identity = (
            f"crm-dupe-5a:finalize:{lease_epoch}:{owner_session}:{source_run_id}:"
            f"{review_run_id}:{handoff_id or 'none'}:"
            f"{int((reviewed or {}).get('expected_revision') or -1)}:"
            f"{str((reviewed or {}).get('decision_set_content_digest') or '').strip()}"
        )
    form_token = issue_form_token(
        owner_id=owner_id,
        session=workflow_session,
        action_kind=MERGE_ACTION,
        action_id=action_id,
        logical_action_identity=merge_identity,
        logical_action_generation=lease_epoch,
    )
    invalidate_form_token = ""
    if plan_frozen and not already_authorized:
        invalidate_form_token = issue_form_token(
            owner_id=owner_id,
            session=workflow_session,
            action_kind=MERGE_ACTION,
            action_id=f"invalidate:{review_run_id}",
            logical_action_identity=_invalidate_merge_identity(
                epoch=lease_epoch,
                owner_session=owner_session,
                review_run_id=review_run_id,
            ),
            logical_action_generation=lease_epoch,
        )

    oversized_components, oversized_error = (
        _load_source_oversized_quarantine_components(
            workflow_session=workflow_session,
            client=client,
            owner_session=owner_session,
            source_run_id=source_run_id,
        )
    )
    if oversized_error:
        form_errors.append(oversized_error)
        messages.error(request, oversized_error)

    return render(
        request,
        "importer/crm_duplicate_journey_merge.html",
        _merge_context_base(
            workflow_session=workflow_session,
            reviewed_result=reviewed,
            handoff=None,
            supported_modes=supported_modes,
            form_token=form_token,
            form_errors=form_errors,
            continuation_run_id=continuation_run_id,
            continuation_status=continuation_status,
            already_authorized=already_authorized,
            effect_intent=intent if isinstance(intent, dict) else None,
            plan_frozen=plan_frozen,
            materialization_wait=materialization_wait,
            materialization_progress=materialization_progress,
            decision_set_content_digest=lease.decision_set_content_digest
            or (reviewed or {}).get("decision_set_content_digest"),
            invalidate_form_token=invalidate_form_token,
            oversized_quarantine_components=oversized_components,
            oversized_quarantine_unavailable=oversized_error,
        ),
    )
