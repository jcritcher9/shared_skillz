"""Owner-scoped workflow projection, artifact, and signed-form helpers."""

from __future__ import annotations

from hashlib import sha256
import json
from typing import Any
from uuid import UUID, uuid4

from django.core import signing
from django.db import transaction
from django.http import Http404
from django.utils import timezone

from .api_client import EasyImportsApiClient
from .models import ApiArtifact, ApiWorkflow, ImportSession

OWNER_SESSION_KEY = "easyimports_owner_id"
FORM_TOKEN_SALT = "easyimports.workflow-form.v1"

# Closed product / internal-box identities for CRM duplicate resolution.
# Session product stays the base product; review boxes are implementation
# continuations, not separately selected products (no prefix matching).
DUPLICATE_RESOLUTION_PRODUCT_KEY = "easyimports.duplicate_resolution"
DUPLICATE_RESOLUTION_INTERNAL_WORKFLOW_KEYS = frozenset(
    {
        "easyimports.duplicate_resolution.account_review",
        "easyimports.duplicate_resolution.person_review",
    }
)


class FormTokenError(ValueError):
    pass


def resolve_session_product_key_for_projection(
    *,
    role: str,
    workflow_key: str,
    source_workflow: ApiWorkflow | None = None,
) -> str | None:
    """Resolve the session product identity from a workflow projection write.

    Closed mapping only:

    - base ``easyimports.duplicate_resolution`` stays the session product;
    - Account/Person review boxes map to that base product;
    - every other workflow key is returned as-is so ``freeze_product_key``
      fails closed on genuine cross-product conflicts.

    Source lineage is **not** used to invent or rebrand a foreign product key
    (e.g. a ``list_import`` continuation whose source points at a duplicate
    primary must still freeze as ``list_import`` and conflict).
    """

    # role/source_workflow bind storage lineage elsewhere; they must not invent
    # a session product from foreign workflow keys.
    _ = (role, source_workflow)
    key = str(workflow_key or "").strip()
    if not key:
        return None
    if key in DUPLICATE_RESOLUTION_INTERNAL_WORKFLOW_KEYS:
        return DUPLICATE_RESOLUTION_PRODUCT_KEY
    return key


def crm_duplicate_lineage_source_workflow(
    session: ImportSession,
) -> ApiWorkflow | None:
    """Locate the base duplicate-resolution primary workflow for lineage binds."""

    # Prefer explicit primary with base product key.
    primary = (
        session.api_workflows.filter(
            role=ApiWorkflow.Role.PRIMARY,
            workflow_key=DUPLICATE_RESOLUTION_PRODUCT_KEY,
        )
        .order_by("-updated_at", "-id")
        .first()
    )
    if primary is not None:
        return primary
    options = dict(session.options or {})
    source_run_id = str(
        options.get("source_run_id") or options.get("run_id") or ""
    ).strip()
    if source_run_id:
        by_run = session.api_workflows.filter(run_id=source_run_id).first()
        if by_run is not None:
            return by_run
    # Any primary on the session (product key may still be freezing).
    primary_any = (
        session.api_workflows.filter(role=ApiWorkflow.Role.PRIMARY)
        .order_by("-updated_at", "-id")
        .first()
    )
    if primary_any is not None:
        return primary_any
    # Last resort: active workflow if it is not already a continuation/review.
    active = getattr(session, "active_workflow", None)
    if active is not None and active.role == ApiWorkflow.Role.PRIMARY:
        return active
    return None


def owner_id_for_request(request, *, create: bool = True) -> UUID | None:
    raw = request.session.get(OWNER_SESSION_KEY)
    if raw:
        try:
            return UUID(str(raw))
        except ValueError:
            request.session.pop(OWNER_SESSION_KEY, None)
    if not create:
        return None
    owner_id = uuid4()
    request.session[OWNER_SESSION_KEY] = str(owner_id)
    request.session.modified = True
    return owner_id


def owned_sessions(request, *, include_archived: bool = False):
    owner_id = owner_id_for_request(request, create=False)
    if owner_id is None:
        return ImportSession.objects.none()
    values = ImportSession.objects.filter(owner_id=owner_id, is_legacy=False)
    if not include_archived:
        values = values.exclude(status=ImportSession.Status.ARCHIVED)
    return values


def owned_session_or_404(request, session_id) -> ImportSession:
    owner_id = owner_id_for_request(request, create=False)
    if owner_id is None:
        raise Http404("Import session not found.")
    try:
        return ImportSession.objects.select_related("active_workflow").get(
            pk=session_id,
            owner_id=owner_id,
            is_legacy=False,
            archived_at__isnull=True,
        )
    except ImportSession.DoesNotExist as exc:
        raise Http404("Import session not found.") from exc


def projection_digest(projection: dict[str, Any]) -> str:
    encoded = json.dumps(
        projection, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


@transaction.atomic
def store_workflow_projection(
    session: ImportSession,
    projection: dict[str, Any],
    *,
    resource_url: str | None = None,
    role: str = ApiWorkflow.Role.PRIMARY,
    source_workflow: ApiWorkflow | None = None,
    make_active: bool = True,
) -> ApiWorkflow:
    run_id = projection["run_id"]
    existing = ApiWorkflow.objects.select_for_update().filter(run_id=run_id).first()
    if existing is not None and existing.session_id != session.id:
        raise FormTokenError("API workflow identity belongs to another session.")
    # Never wipe an established continuation/review lineage with a null source
    # on read-only reloads (Phase 3/4A).
    effective_source = source_workflow
    if (
        effective_source is None
        and existing is not None
        and existing.source_workflow_id is not None
        and role
        in {ApiWorkflow.Role.REVIEW, ApiWorkflow.Role.CONTINUATION, existing.role}
    ):
        effective_source = existing.source_workflow
    defaults = {
        "session": session,
        "source_workflow": effective_source,
        "role": role,
        "workflow_key": projection["workflow_key"],
        "workflow_version": projection["workflow_version"],
        "target_provider_id": projection.get("target_provider_id") or "",
        "status": projection["status"],
        "stage": projection["stage"],
        "revision": projection["revision"],
        "resource_url": resource_url or f"/v1/workflows/{run_id}",
        "projection": projection,
        "projection_digest": projection_digest(projection),
    }
    if existing is None:
        workflow = ApiWorkflow.objects.create(run_id=run_id, **defaults)
    else:
        for name, value in defaults.items():
            setattr(existing, name, value)
        existing.save()
        workflow = existing
    if make_active:
        session.active_workflow = workflow
        session.status = _session_status(projection["status"])
        session.error_message = (
            (projection.get("error") or {}).get("message", "")
            if projection["status"] == "failed"
            else ""
        )
        session.summary = projection.get("summary") or {}
        session.save(
            update_fields=[
                "active_workflow",
                "status",
                "error_message",
                "summary",
                "updated_at",
            ]
        )
    return workflow


def owner_session_for_import_session(session: ImportSession) -> str | None:
    """Django owner binding used as API ``X-Owner-Session`` when present."""

    owner = getattr(session, "owner_id", None)
    if owner is None:
        return None
    return f"django-{owner}"


def refresh_workflow(
    session: ImportSession,
    workflow: ApiWorkflow,
    *,
    client: EasyImportsApiClient | None = None,
) -> ApiWorkflow:
    if workflow.session_id != session.id:
        raise FormTokenError("Workflow does not belong to this session.")
    api = client or EasyImportsApiClient()
    # CRM query (and other owner-bound) runs require X-Owner-Session on GET.
    projection = api.workflow(
        workflow.run_id,
        owner_session=owner_session_for_import_session(session),
    )
    return store_workflow_projection(
        session,
        projection,
        resource_url=workflow.resource_url,
        role=workflow.role,
        source_workflow=workflow.source_workflow,
        make_active=session.active_workflow_id == workflow.id,
    )


@transaction.atomic
def store_artifacts(workflow: ApiWorkflow, collection: dict[str, Any]):
    seen: set[str] = set()
    for metadata in collection["artifacts"]:
        artifact_id = metadata["artifact_id"]
        seen.add(artifact_id)
        ApiArtifact.objects.update_or_create(
            workflow=workflow,
            artifact_id=artifact_id,
            defaults={"metadata": metadata},
        )
    workflow.artifacts.exclude(artifact_id__in=seen).delete()
    return list(workflow.artifacts.all())


def issue_form_token(
    *,
    owner_id: UUID,
    session: ImportSession,
    action_kind: str,
    workflow: ApiWorkflow | None = None,
    action_id: str = "",
    logical_action_identity: str = "",
    logical_action_generation: int = 0,
) -> str:
    return signing.dumps(
        {
            "owner_id": str(owner_id),
            "session_id": str(session.id),
            "run_id": "" if workflow is None else workflow.run_id,
            "revision": None if workflow is None else workflow.revision,
            "projection_digest": "" if workflow is None else workflow.projection_digest,
            "action_kind": action_kind,
            "action_id": action_id,
            "logical_action_identity": logical_action_identity,
            "logical_action_generation": logical_action_generation,
            "form_instance": str(uuid4()),
            "issued_at": timezone.now().isoformat(),
        },
        salt=FORM_TOKEN_SALT,
        compress=True,
    )


def decode_form_token(
    token: str,
    *,
    owner_id: UUID,
    session: ImportSession,
    action_kind: str,
) -> dict[str, Any]:
    try:
        claims = signing.loads(token, salt=FORM_TOKEN_SALT, max_age=24 * 60 * 60)
    except signing.BadSignature as exc:
        raise FormTokenError("This form is invalid or expired. Reload it.") from exc
    expected = {
        "owner_id": str(owner_id),
        "session_id": str(session.id),
        "action_kind": action_kind,
    }
    if any(claims.get(name) != value for name, value in expected.items()):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    try:
        claims["form_instance"] = UUID(claims["form_instance"])
    except (KeyError, TypeError, ValueError) as exc:
        raise FormTokenError("This form has no valid action identity.") from exc
    try:
        claims["logical_action_generation"] = int(
            claims.get("logical_action_generation", 0)
        )
    except (TypeError, ValueError) as exc:
        raise FormTokenError("This form has an invalid action generation.") from exc
    return claims


def validate_form_token(
    token: str,
    *,
    owner_id: UUID,
    session: ImportSession,
    action_kind: str,
    workflow: ApiWorkflow | None = None,
    action_id: str = "",
    logical_action_identity: str = "",
    logical_action_generation: int = 0,
) -> dict[str, Any]:
    claims = decode_form_token(
        token,
        owner_id=owner_id,
        session=session,
        action_kind=action_kind,
    )
    expected = {
        "run_id": "" if workflow is None else workflow.run_id,
        "revision": None if workflow is None else workflow.revision,
        "projection_digest": "" if workflow is None else workflow.projection_digest,
        "action_id": action_id,
        "logical_action_identity": logical_action_identity,
        "logical_action_generation": logical_action_generation,
    }
    if any(claims.get(name) != value for name, value in expected.items()):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    return claims


@transaction.atomic
def save_product_selection(
    session: ImportSession,
    *,
    product_key: str,
    target_provider_id: str,
    operator_label: str,
) -> ImportSession:
    """Persist configuration only after rechecking mutable state under a lock."""
    try:
        locked = ImportSession.objects.select_for_update().get(
            pk=session.pk,
            owner_id=session.owner_id,
            is_legacy=False,
            archived_at__isnull=True,
        )
    except ImportSession.DoesNotExist as exc:
        raise FormTokenError("This import session is no longer available.") from exc

    selection_changed = (
        locked.product_key != product_key
        or locked.target_provider_id != target_provider_id
    )
    if (
        locked.api_mutations.exists() or locked.active_workflow_id is not None
    ) and selection_changed:
        raise FormTokenError(
            "Product and target are frozen after the first API mutation."
        )
    if (
        locked.files.filter(upload_mutation__isnull=False).exists()
        and selection_changed
    ):
        raise FormTokenError(
            "Archive this session to change product or target after uploading."
        )

    submitted_operator = operator_label.strip()
    if locked.operator_label_frozen_at is not None:
        if submitted_operator != locked.operator_label:
            raise FormTokenError(
                "The operator label was frozen while this form was open. Reload it."
            )
    else:
        locked.operator_label = submitted_operator

    locked.product_key = product_key
    locked.target_provider_id = target_provider_id
    locked.save(
        update_fields=[
            "product_key",
            "target_provider_id",
            "operator_label",
            "updated_at",
        ]
    )
    return locked


@transaction.atomic
def freeze_operator_label(session: ImportSession) -> ImportSession:
    locked = ImportSession.objects.select_for_update().get(pk=session.pk)
    if not locked.operator_label.strip():
        raise FormTokenError("Set an operator label before beginning review activity.")
    if locked.operator_label_frozen_at is None:
        locked.operator_label_frozen_at = timezone.now()
        locked.save(update_fields=["operator_label_frozen_at", "updated_at"])
    return locked


def archive_session(session: ImportSession) -> None:
    session.status = ImportSession.Status.ARCHIVED
    session.archived_at = timezone.now()
    session.save(update_fields=["status", "archived_at", "updated_at"])


def _session_status(workflow_status: str) -> str:
    if workflow_status == "succeeded":
        return ImportSession.Status.COMPLETED
    if workflow_status == "failed":
        return ImportSession.Status.FAILED
    return ImportSession.Status.RUNNING
