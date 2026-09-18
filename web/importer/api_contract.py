"""Strict generated API validation with narrow forward-compatible envelopes."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError

from .api_contract_generated import (
    API_VERSION,
    AUTHORIZATION_MODES,
    DECISION_TYPES,
    PRODUCT_KEYS,
    PRODUCT_TRACK_MODES,
    WORKFLOW_STATUSES,
)
from .api_models_generated import (
    AccountListImportCreate,
    AccountListTerminalEvidence,
    AccountReviewDecisionCommand,
    AccountReviewDecisionResource,
    ApiMutationStatusResource,
    ArtifactCollection,
    AutoDispositionResultBody,
    BindDecisionSetHandoffRequest,
    CommandReceipt,
    CreateDecisionSetHandoffRequest,
    CrmAccountMatchDecisionCommand,
    CrmAccountMatchDecisionResource,
    CrmCampaignGetResult,
    CrmCampaignMemberStatusesResult,
    CrmCampaignSearchResult,
    CrmConnectionCollection,
    CrmConnectionResource,
    CrmDuplicateJourneyCollection,
    CrmDuplicateJourneyResource,
    CrmQueryCollection,
    CrmQueryResource,
    CrmQueryRowsPage,
    CrmPersonMatchDecisionCommand,
    CrmPersonMatchDecisionResource,
    UploadedAccountIdDisagreementCommand,
    UploadedAccountIdDisagreementResource,
    UploadedPersonIdDisagreementCommand,
    UploadedPersonIdDisagreementResource,
    CrmProviderCatalog,
    DuplicateReadGrantResource,
    DuplicateResolutionCreate,
    DuplicateResolutionTerminalEvidence,
    EffectAuthorizationGrantResource,
    DuplicateExecutionCycleCommandRequest,
    EffectAuthorizationCommandRequest,
    EffectIntentResource,
    EffectResumeCommandRequest,
    ErrorEnvelope,
    AmendReviewedDispositionWindowResult,
    FinalizeMergePlanHandoffResult,
    ImpliedReferenceAuthorizationResult,
    ListDuplicateDecisionCommand,
    ListDuplicateDecisionResource,
    ListImportCreate,
    ListImportTerminalEvidence,
    PersonReviewDecisionCommand,
    PersonReviewDecisionResource,
    PrepareReviewedResultResult,
    ProductCatalog,
    RawPopulationUploadResource,
    RecordIdMappingResource,
    ReviewCompleteResource,
    ReviewHandoffResource,
    ReviewedDispositionWindowResource,
    ReviewedResultResource,
    ReviewedResultSummaryResource,
    ReviewWindowResource,
    ReviewWindowSubmitResult,
    RunOutputPackageResource,
    SingleDatasetImportCreate,
    SingleDatasetTerminalEvidence,
    StartReviewWorkflowRequest,
    TargetCatalog,
    UploadResource,
    ValidationDecisionCommand,
    ValidationDecisionResource,
    WorkflowFailureResource,
    WorkflowResource,
    DuplicateExecutionExceptionPageResource,
)


class ApiContractError(ValueError):
    pass


class _UnknownDecision(BaseModel):
    model_config = ConfigDict(extra="allow")

    decision_id: str
    decision_type: str
    phase_id: str
    body: dict[str, Any]


class _CompatibleWorkflow(BaseModel):
    """Common envelope used only when status or decision type is future v1."""

    model_config = ConfigDict(extra="allow")

    run_id: str
    revision: int
    workflow_key: str
    workflow_version: int
    target_provider_id: str | None
    status: str
    stage: str
    decision: dict[str, Any] | None
    effect_intent: EffectIntentResource | None
    effect_grants: list[EffectAuthorizationGrantResource]
    review_handoff: ReviewHandoffResource | None
    terminal_evidence: dict[str, Any] | None
    summary: dict[str, int]
    error: WorkflowFailureResource | None
    links: dict[str, str]


_CREATE_MODELS = {
    "easyimports.list_import": ListImportCreate,
    "easyimports.account_list_import": AccountListImportCreate,
    "easyimports.single_dataset_import": SingleDatasetImportCreate,
    "easyimports.duplicate_resolution": DuplicateResolutionCreate,
}
_DECISION_COMMAND_MODELS = {
    "validation_failed": ValidationDecisionCommand,
    "list_duplicates": ListDuplicateDecisionCommand,
    "multiple_crm_matches": CrmPersonMatchDecisionCommand,
    "multiple_crm_account_matches": CrmAccountMatchDecisionCommand,
    "uploaded_account_id_disagreement": UploadedAccountIdDisagreementCommand,
    "uploaded_person_id_disagreement": UploadedPersonIdDisagreementCommand,
    "account_duplicate_group_review": AccountReviewDecisionCommand,
    "person_duplicate_group_review": PersonReviewDecisionCommand,
}
_DECISION_RESOURCE_ADAPTER = TypeAdapter(
    ValidationDecisionResource
    | ListDuplicateDecisionResource
    | CrmPersonMatchDecisionResource
    | CrmAccountMatchDecisionResource
    | UploadedAccountIdDisagreementResource
    | UploadedPersonIdDisagreementResource
    | AccountReviewDecisionResource
    | PersonReviewDecisionResource
)
_TERMINAL_ADAPTER = TypeAdapter(
    ListImportTerminalEvidence
    | AccountListTerminalEvidence
    | SingleDatasetTerminalEvidence
    | DuplicateResolutionTerminalEvidence
)


def _validate(model, value: Any, name: str) -> dict[str, Any]:
    try:
        return model.model_validate(value).model_dump(mode="json")
    except (ValidationError, TypeError, ValueError) as exc:
        raise ApiContractError(f"Malformed {name}: {exc}") from exc


def validate_health(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"status", "api_version"}:
        raise ApiContractError("The health response has an incompatible shape.")
    if value["status"] != "ok" or value["api_version"] != API_VERSION:
        raise ApiContractError("The EasyImports API version is incompatible.")
    return dict(value)


def validate_error_envelope(value: Any) -> dict[str, Any]:
    return _validate(ErrorEnvelope, value, "error envelope")


def validate_api_mutation_status(value: Any) -> dict[str, Any]:
    expected_keys = {
        "mutation_id",
        "mutation_kind",
        "status",
        "http_status",
        "response",
        "retryable",
        "retry_after_seconds",
    }
    if not isinstance(value, dict) or set(value) != expected_keys:
        raise ApiContractError("The API mutation status has an incompatible shape.")
    result = _validate(ApiMutationStatusResource, value, "API mutation status")
    if result["status"] == "pending" and result["response"] is not None:
        raise ApiContractError("A pending API mutation exposed a response body.")
    if result["status"] == "completed" and (
        result["http_status"] is None or result["response"] is None
    ):
        raise ApiContractError(
            "A completed API mutation is missing its frozen response."
        )
    if not 1 <= result["retry_after_seconds"] <= 30:
        raise ApiContractError("API mutation retry delay is out of range.")
    return result


def validate_upload_resource(value: Any) -> dict[str, Any]:
    return _validate(UploadResource, value, "upload resource")


def validate_crm_provider_catalog(value: Any) -> dict[str, Any]:
    return _validate(CrmProviderCatalog, value, "crm provider catalog")


def validate_crm_connection_resource(value: Any) -> dict[str, Any]:
    return _validate(CrmConnectionResource, value, "crm connection resource")


def validate_crm_connection_collection(value: Any) -> dict[str, Any]:
    return _validate(CrmConnectionCollection, value, "crm connection collection")


def validate_crm_duplicate_journey_resource(value: Any) -> dict[str, Any]:
    return _validate(
        CrmDuplicateJourneyResource, value, "crm duplicate journey resource"
    )


def validate_crm_duplicate_journey_collection(value: Any) -> dict[str, Any]:
    return _validate(
        CrmDuplicateJourneyCollection, value, "crm duplicate journey collection"
    )


def validate_raw_population_upload_resource(value: Any) -> dict[str, Any]:
    return _validate(
        RawPopulationUploadResource, value, "raw population upload resource"
    )


def validate_record_id_mapping_resource(value: Any) -> dict[str, Any]:
    return _validate(RecordIdMappingResource, value, "record id mapping resource")


def validate_duplicate_read_grant_resource(value: Any) -> dict[str, Any]:
    return _validate(
        DuplicateReadGrantResource, value, "duplicate read grant resource"
    )


def validate_implied_reference_authorization_result(value: Any) -> dict[str, Any]:
    return _validate(
        ImpliedReferenceAuthorizationResult,
        value,
        "implied reference authorization result",
    )


def validate_duplicate_review_window(value: Any) -> dict[str, Any]:
    """GET next window or review-complete terminal (Phase 3A/4B)."""

    if not isinstance(value, dict):
        raise ApiContractError("duplicate review window must be an object")
    contract = str(value.get("review_contract") or "")
    if contract.endswith("duplicate_review_complete.v1"):
        return _validate(
            ReviewCompleteResource, value, "duplicate review complete resource"
        )
    return _validate(ReviewWindowResource, value, "duplicate review window resource")


def validate_duplicate_review_window_submit_result(value: Any) -> dict[str, Any]:
    return _validate(
        ReviewWindowSubmitResult,
        value,
        "duplicate review window submit result",
    )


def validate_duplicate_review_end_early_result(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiContractError("end-early result must be an object")
    if str(value.get("command_kind") or "") != "submit_duplicate_review_end_early":
        raise ApiContractError("end-early result has the wrong command_kind")
    if str(value.get("outcome") or "") != "accepted":
        raise ApiContractError("end-early result was not accepted")
    nested = value.get("result")
    if not isinstance(nested, dict) or "review_window" not in nested:
        raise ApiContractError("end-early result is missing result.review_window")
    return value


def validate_auto_disposition_result(value: Any) -> dict[str, Any]:
    """Phase 7B/7C public root body for submit_duplicate_auto_disposition."""

    return _validate(
        AutoDispositionResultBody,
        value,
        "duplicate auto-disposition result",
    )


def validate_prepare_reviewed_result(value: Any) -> dict[str, Any]:
    return _validate(
        PrepareReviewedResultResult,
        value,
        "prepare reviewed result",
    )


def validate_duplicate_reviewed_result(value: Any) -> dict[str, Any]:
    return _validate(
        ReviewedResultResource,
        value,
        "duplicate reviewed result resource",
    )


def validate_reviewed_result_materialization_progress(value: Any) -> dict[str, Any]:
    """GET-only reviewed-result prepare progress (introduced in API 1.57.0)."""

    if not isinstance(value, dict):
        raise ApiContractError("reviewed-result progress is not an object")
    status = str(value.get("status") or "")
    if status not in {
        "not_started",
        "queued",
        "running",
        "complete",
        "failed",
    }:
        raise ApiContractError("reviewed-result progress status is invalid")
    contract = str(value.get("review_contract") or "")
    if contract != "easyimports.crm.duplicate_reviewed_result_progress.v1":
        raise ApiContractError("reviewed-result progress contract is invalid")
    return dict(value)


def validate_finalize_merge_plan_handoff_result(value: Any) -> dict[str, Any]:
    return _validate(
        FinalizeMergePlanHandoffResult,
        value,
        "finalize merge plan handoff result",
    )


def validate_reviewed_disposition_window(value: Any) -> dict[str, Any]:
    return _validate(
        ReviewedDispositionWindowResource,
        value,
        "reviewed disposition window",
    )


def validate_duplicate_reviewed_result_summary(value: Any) -> dict[str, Any]:
    return _validate(
        ReviewedResultSummaryResource,
        value,
        "duplicate reviewed result summary resource",
    )


def validate_amend_reviewed_disposition_window_result(
    value: Any,
) -> dict[str, Any]:
    return _validate(
        AmendReviewedDispositionWindowResult,
        value,
        "amend reviewed disposition window result",
    )


def validate_crm_query_resource(value: Any) -> dict[str, Any]:
    return _validate(CrmQueryResource, value, "crm query resource")


def validate_crm_query_collection(value: Any) -> dict[str, Any]:
    return _validate(CrmQueryCollection, value, "crm query collection")


def validate_crm_query_rows_page(value: Any) -> dict[str, Any]:
    return _validate(CrmQueryRowsPage, value, "crm query rows page")


def validate_crm_campaign_search_result(value: Any) -> dict[str, Any]:
    return _validate(CrmCampaignSearchResult, value, "crm campaign search result")


def validate_crm_campaign_get_result(value: Any) -> dict[str, Any]:
    return _validate(CrmCampaignGetResult, value, "crm campaign get result")


def validate_crm_campaign_member_statuses_result(value: Any) -> dict[str, Any]:
    return _validate(
        CrmCampaignMemberStatusesResult,
        value,
        "crm campaign member statuses result",
    )


def validate_command_receipt(value: Any) -> dict[str, Any]:
    return _validate(CommandReceipt, value, "command receipt")


def validate_duplicate_execution_exception_page(value: Any) -> dict[str, Any]:
    return _validate(
        DuplicateExecutionExceptionPageResource,
        value,
        "duplicate execution exception page",
    )


def validate_workflow_resource(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ApiContractError("Workflow resource must be an object.")
    status = value.get("status")
    decision = value.get("decision")
    decision_type = (
        decision.get("decision_type") if isinstance(decision, dict) else None
    )
    if status in WORKFLOW_STATUSES and (
        decision is None or decision_type in DECISION_TYPES
    ):
        return _validate(WorkflowResource, value, "workflow resource")

    compatible = _validate(_CompatibleWorkflow, value, "workflow envelope")
    try:
        if decision is not None:
            if decision_type in DECISION_TYPES:
                _DECISION_RESOURCE_ADAPTER.validate_python(decision)
            else:
                _UnknownDecision.model_validate(decision)
        if value.get("terminal_evidence") is not None:
            _TERMINAL_ADAPTER.validate_python(value["terminal_evidence"])
    except ValidationError as exc:
        raise ApiContractError(f"Malformed compatible workflow content: {exc}") from exc
    return compatible


def validate_artifact_collection(value: Any) -> dict[str, Any]:
    return _validate(ArtifactCollection, value, "artifact collection")


def validate_run_output_package_resource(value: Any) -> dict[str, Any]:
    return _validate(RunOutputPackageResource, value, "run-output package resource")


def validate_product_catalog(value: Any) -> dict[str, Any]:
    result = _validate(ProductCatalog, value, "product catalog")
    products = result["products"]
    if {item["product_key"] for item in products} != set(PRODUCT_KEYS):
        raise ApiContractError("Product catalog does not expose the exact v1 products.")
    for item in products:
        expected = PRODUCT_TRACK_MODES[item["product_key"]]
        tracks = item["tracks"]
        if set(tracks) != set(expected):
            raise ApiContractError("Product catalog track names are incompatible.")
        for track, modes in tracks.items():
            if list(modes) != list(expected[track]):
                raise ApiContractError(
                    f"Product catalog modes for {track!r} are incompatible."
                )
    return result


def validate_target_catalog(value: Any) -> dict[str, Any]:
    result = _validate(TargetCatalog, value, "target catalog")
    known_tracks = {
        track for tracks in PRODUCT_TRACK_MODES.values() for track in tracks
    }
    for target in result["targets"]:
        maximums = target["maximum_modes"]
        if set(maximums) != known_tracks:
            raise ApiContractError("Target catalog track names are incompatible.")
        if not set(maximums.values()) <= set(AUTHORIZATION_MODES):
            raise ApiContractError("Target catalog contains an unknown mode.")
    return result


def validate_workflow_create(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict) or value.get("product_key") not in _CREATE_MODELS:
        raise ApiContractError("Workflow creation product is unsupported.")
    return _validate(_CREATE_MODELS[value["product_key"]], value, "workflow creation")


def validate_decision_command(value: Any) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or value.get("decision_type") not in _DECISION_COMMAND_MODELS
    ):
        raise ApiContractError("Decision command type is unsupported.")
    return _validate(
        _DECISION_COMMAND_MODELS[value["decision_type"]],
        value,
        "decision command",
    )


def validate_effect_authorization(value: Any) -> dict[str, Any]:
    return _validate(
        EffectAuthorizationCommandRequest, value, "effect authorization command"
    )


def validate_effect_resume(value: Any) -> dict[str, Any]:
    return _validate(EffectResumeCommandRequest, value, "effect resume command")


def validate_duplicate_execution_cycle(value: Any) -> dict[str, Any]:
    return _validate(
        DuplicateExecutionCycleCommandRequest,
        value,
        "duplicate execution cycle command",
    )


def validate_start_review(value: Any) -> dict[str, Any]:
    return _validate(StartReviewWorkflowRequest, value, "review workflow command")


def validate_decision_set_handoff(value: Any) -> dict[str, Any]:
    return _validate(
        CreateDecisionSetHandoffRequest, value, "decision-set handoff command"
    )


def validate_decision_set_binding(value: Any) -> dict[str, Any]:
    return _validate(
        BindDecisionSetHandoffRequest, value, "decision-set continuation command"
    )
