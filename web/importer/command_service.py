"""Workflow command orchestration after authoritative receipt persistence."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable
from uuid import UUID

from .api_client import (
    EasyImportsApiClient,
    MutationDispatchResult,
    create_or_reuse_mutation,
)
from .models import ApiMutation, ApiWorkflow, ImportSession
from .workflow_state import store_workflow_projection


def dispatch_command(
    *,
    session: ImportSession,
    form_instance: UUID,
    mutation_kind: str,
    route: str,
    logical_action_identity: str,
    logical_action_generation: int,
    form_payload_digest: str,
    body: dict[str, Any] | None = None,
    body_builder: Callable[[datetime], dict[str, Any]] | None = None,
    workflow: ApiWorkflow | None = None,
    client: EasyImportsApiClient | None = None,
    explicit_retry: bool = False,
) -> MutationDispatchResult:
    """Persist and process a receipt before fetching its workflow resource."""
    mutation = create_or_reuse_mutation(
        session=session,
        workflow=workflow,
        form_instance=form_instance,
        mutation_kind=mutation_kind,
        route=route,
        logical_action_identity=logical_action_identity,
        logical_action_generation=logical_action_generation,
        request_json=body,
        request_builder=body_builder,
        form_payload_digest=form_payload_digest,
        resource_identity=(workflow.run_id if workflow else str(session.id)),
        replacement_of=_replacement_for(
            session, logical_action_identity, logical_action_generation
        ),
    )
    return (client or EasyImportsApiClient()).dispatch(
        mutation, explicit_retry=explicit_retry
    )


def action_generation(session: ImportSession, identity: str) -> int:
    """Generation for workflow decision/upload style retries.

    Advances only after an acknowledged non-conflict rejection so a corrected
    resubmit can proceed. Completed successes keep the same generation so a
    double-submit replays the frozen receipt.
    """
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


def crm_connect_action_generation(
    session: ImportSession,
    *,
    provider_key: str,
    owner_session: str,
) -> int:
    """Generation for the full Django CRM connect action (start + OAuth complete).

    R4 residual: the connect action is not terminal when only the start mutation
    is completed. Generation stays open while:

    - start is pending / unknown, or
    - start completed but OAuth complete is missing, pending, or unknown.

    Generation advances only after the paired OAuth-complete mutation reaches a
    terminal state (completed or rejected), or when start itself is rejected.
    That lets retries resume the same connection attempt instead of abandoning
    a half-finished connect.
    """
    connect_identity = f"crm-connect:{provider_key}:{owner_session}"
    latest_start = (
        session.api_mutations.filter(logical_action_identity=connect_identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest_start is None:
        return 0
    if latest_start.state == ApiMutation.State.REJECTED:
        # Start failed terminally; a new Connect is a new attempt.
        return latest_start.logical_action_generation + 1
    if latest_start.state != ApiMutation.State.COMPLETED:
        # pending / unknown start — resume same generation
        return latest_start.logical_action_generation

    connection_id = ""
    if isinstance(latest_start.response_json, dict):
        connection_id = str(latest_start.response_json.get("connection_id") or "").strip()
    if not connection_id:
        # Completed start without a recoverable connection id: keep open.
        return latest_start.logical_action_generation

    complete_identity = f"crm-complete:{connection_id}:{owner_session}"
    complete = (
        session.api_mutations.filter(logical_action_identity=complete_identity)
        .order_by("-created_at")
        .first()
    )
    if complete is None:
        return latest_start.logical_action_generation
    if complete.state in {
        ApiMutation.State.PENDING,
        ApiMutation.State.UNKNOWN,
    }:
        return latest_start.logical_action_generation
    # complete COMPLETED or REJECTED → full connect action is terminal
    return latest_start.logical_action_generation + 1


def crm_reconnect_action_generation(
    session: ImportSession,
    *,
    connection_id: str,
    owner_session: str,
) -> int:
    """Generation for reconnect-start on one connection + owner.

    Stays open while start is pending/unknown, or while start completed
    but the paired complete/abandon for that attempt is still open.
    Advances after that epoch is terminal so the next Reconnect cannot
    replay the prior start or complete receipt.
    """

    identity = f"crm-reconnect:{connection_id}:{owner_session}"
    latest_start = (
        session.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest_start is None:
        return 0
    if latest_start.state == ApiMutation.State.REJECTED:
        return latest_start.logical_action_generation + 1
    if latest_start.state != ApiMutation.State.COMPLETED:
        return latest_start.logical_action_generation
    attempt_id = ""
    if isinstance(latest_start.response_json, dict):
        attempt_id = str(
            latest_start.response_json.get("reconnect_attempt_id") or ""
        ).strip()
    if not attempt_id:
        return latest_start.logical_action_generation
    complete = (
        session.api_mutations.filter(
            logical_action_identity=(
                f"crm-complete:{connection_id}:{owner_session}:{attempt_id}"
            )
        )
        .order_by("-created_at")
        .first()
    )
    abandon = (
        session.api_mutations.filter(
            logical_action_identity=(
                f"crm-reconnect-abandon:{connection_id}:{owner_session}:{attempt_id}"
            )
        )
        .order_by("-created_at")
        .first()
    )
    open_states = {ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN}
    if complete is None and abandon is None:
        return latest_start.logical_action_generation
    if complete is not None and complete.state in open_states:
        return latest_start.logical_action_generation
    if abandon is not None and abandon.state in open_states:
        return latest_start.logical_action_generation
    return latest_start.logical_action_generation + 1


def action_mutation(
    session: ImportSession, identity: str, generation: int
) -> ApiMutation | None:
    return session.api_mutations.filter(
        logical_action_identity=identity,
        logical_action_generation=generation,
    ).first()


def _replacement_for(
    session: ImportSession, identity: str, generation: int
) -> ApiMutation | None:
    if generation < 1:
        return None
    return session.api_mutations.filter(
        logical_action_identity=identity,
        logical_action_generation=generation - 1,
        state=ApiMutation.State.REJECTED,
        acknowledged_at__isnull=False,
    ).first()


def materialize_accepted_workflow(
    session: ImportSession,
    dispatch: MutationDispatchResult,
    *,
    role: str = ApiWorkflow.Role.PRIMARY,
    source_workflow: ApiWorkflow | None = None,
    client: EasyImportsApiClient | None = None,
) -> ApiWorkflow | None:
    """Refresh only after the frozen receipt has been classified as accepted."""
    if dispatch.mutation.state != ApiMutation.State.COMPLETED:
        return None
    receipt = dispatch.response
    if receipt.get("outcome") != "accepted":
        return None
    api = client or EasyImportsApiClient()
    from .workflow_state import owner_session_for_import_session

    receipt_run = str(receipt.get("run_id") or "").strip()
    # Prefer an already-stored role/lineage for this run (CRM-dupe continuation
    # often materializes authorize_effect without a mutation.workflow FK).
    existing = (
        session.api_workflows.filter(run_id=receipt_run).first()
        if receipt_run
        else None
    )
    if existing is not None:
        if role == ApiWorkflow.Role.PRIMARY and existing.role in {
            ApiWorkflow.Role.REVIEW,
            ApiWorkflow.Role.CONTINUATION,
        }:
            role = existing.role
        if source_workflow is None and existing.source_workflow_id is not None:
            source_workflow = existing.source_workflow
    projection = api.workflow(
        receipt_run or receipt["run_id"],
        owner_session=owner_session_for_import_session(session),
    )
    # Review/continuation materialization must not steal the active primary.
    active = session.active_workflow
    already_active = (
        active is not None and str(active.run_id or "").strip() == receipt_run
    )
    if role in {ApiWorkflow.Role.REVIEW, ApiWorkflow.Role.CONTINUATION}:
        # Phase 3: never steal the analysis primary. Terminal visibility is
        # handled by the workflow view preferring the latest authorize/bind
        # projection for display without changing session.active_workflow.
        make_active = already_active
    else:
        make_active = True
    workflow = store_workflow_projection(
        session,
        projection,
        resource_url=receipt["resource"],
        role=role,
        source_workflow=source_workflow,
        make_active=make_active,
    )
    # Crash/replay recovery freezes session product identity via the closed
    # resolver (base product for duplicate review/continuation boxes).
    from .setup_service import freeze_product_key
    from .workflow_state import resolve_session_product_key_for_projection

    resolved_product = resolve_session_product_key_for_projection(
        role=role,
        workflow_key=workflow.workflow_key,
        source_workflow=source_workflow,
    )
    if resolved_product is not None:
        freeze_product_key(session, resolved_product)
    return workflow


def materialize_stored_receipt(
    session: ImportSession,
    mutation: ApiMutation,
    *,
    client: EasyImportsApiClient | None = None,
) -> ApiWorkflow | None:
    """Recover a projection from a completed receipt without replaying POST."""
    if not isinstance(mutation.response_json, dict):
        return None
    if mutation.mutation_kind == "start_review_workflow":
        role = ApiWorkflow.Role.REVIEW
        source = mutation.workflow
    elif mutation.mutation_kind in {
        "bind_decision_set_handoff",
        "finalize_duplicate_merge_plan_handoff",
    }:
        role = ApiWorkflow.Role.CONTINUATION
        source = mutation.workflow
    elif mutation.mutation_kind == "authorize_effect":
        # Prefer the stored continuation/review role and lineage when present so
        # recovery cannot re-freeze an internal box as a new top-level product
        # or steal the active primary pointer.
        if mutation.workflow is not None:
            role = mutation.workflow.role
            source = mutation.workflow.source_workflow
        else:
            receipt_run = ""
            if isinstance(mutation.response_json, dict):
                receipt_run = str(mutation.response_json.get("run_id") or "").strip()
            existing = (
                session.api_workflows.filter(run_id=receipt_run).first()
                if receipt_run
                else None
            )
            if existing is not None:
                role = existing.role
                source = existing.source_workflow
            else:
                role = ApiWorkflow.Role.PRIMARY
                source = None
    else:
        role = mutation.workflow.role if mutation.workflow else ApiWorkflow.Role.PRIMARY
        source = mutation.workflow.source_workflow if mutation.workflow else None
    return materialize_accepted_workflow(
        session,
        MutationDispatchResult(mutation, mutation.response_json),
        role=role,
        source_workflow=source,
        client=client,
    )
