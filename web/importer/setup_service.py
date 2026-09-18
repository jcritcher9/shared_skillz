"""Setup-draft persistence, role presentation, and create-time route checks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from django.db import transaction
from django.utils import timezone

from .constants import (
    SETUP_ROLE_LABELS,
    SourceRole,
    display_setup_intent_label,
)
from .models import ApiMutation, ImportSession, SourceFile
from .setup_router import (
    CONNECTED_CRM_ENABLED_FOR_IMPORT_SETUP,
    DETACHED_ROLE_PREFIX,
    OperatorIntent,
    RouteDecision,
    RouteError,
    derive_route,
    parse_operator_intent,
    validate_uploads_against_route,
)
from .vocabulary_intent import (
    DEFAULT_VOCABULARY,
    VocabularyIntentError,
    normalize_vocabulary,
    validate_vocabulary_for_setup,
)
from .workflow_state import FormTokenError


class SetupValidationError(ValueError):
    """Draft or create-time validation failed with a customer-facing message."""


# MAP-3: products that require a confirmed column-mapping plan on create.
_COLUMN_MAPPING_BIND_PRODUCTS = frozenset(
    {
        "easyimports.list_import",
        "easyimports.account_list_import",
        "easyimports.single_dataset_import",
    }
)
_MAP_OPTIONS_KEY = "column_mapping_map2"
_BIND_DIGEST_KEYS = (
    "plan_content_digest",
    "source_schema_digest",
    "destination_digest",
    "target_contract_digest",
)
_STALE_MAPPING_MESSAGE = (
    "Your confirmed column mapping no longer matches this upload or setup. "
    "Open Map columns, map again, confirm, then start the import."
)


def catalog_id_for_setup_context(
    *,
    entity: str | None,
    operation: str | None,
    people_output: str | None,
    vocabulary: str | None = None,
) -> str:
    """Resolve catalog id for setup + vocabulary (MAP-R1 product or CMX-2 ingest)."""

    from .vocabulary_intent import resolve_catalog_id_for_setup_vocabulary

    try:
        return resolve_catalog_id_for_setup_vocabulary(
            entity=entity,
            operation=operation,
            people_output=people_output,
            vocabulary=vocabulary,
        )
    except ValueError as exc:
        raise SetupValidationError(str(exc)) from exc


# R19: plan create always reads this role, never first-by-id / last POST.
AUTHORITATIVE_MAPPING_SOURCE_ROLES = {
    "easyimports.single_dataset_import": "dataset",
    "easyimports.list_import": "raw_list",
    "easyimports.account_list_import": "raw_list",
}


def _source_has_mapping_headers(source: SourceFile) -> bool:
    columns = source.columns if isinstance(source.columns, list) else []
    headers = [str(col).strip() for col in columns if str(col).strip()]
    return bool(headers) and bool(str(source.api_upload_id or "").strip())


def _first_header_source(session: ImportSession) -> SourceFile | None:
    candidates = list(active_source_queryset(session).order_by("id"))
    if not candidates:
        candidates = list(
            session.files.filter(detached_at__isnull=True)
            .exclude(role__startswith=DETACHED_ROLE_PREFIX)
            .order_by("id")
        )
    for source in candidates:
        if _source_has_mapping_headers(source):
            return source
    return None


def authoritative_mapping_role(session: ImportSession) -> str | None:
    """Mapping source role for the current route product, if known."""

    route = route_for_session(session)
    product_key = route.product_key if route is not None else session.product_key
    return AUTHORITATIVE_MAPPING_SOURCE_ROLES.get(str(product_key or "").strip())


def authoritative_mapping_source(session: ImportSession) -> SourceFile | None:
    """Completed SourceFile for the route's mapping role (R19).

    Never ``accounts`` / ``contacts`` / ``leads``. Pre-migration sessions
    without a known product fall back to first-by-id.
    """

    role = authoritative_mapping_role(session)
    if not role:
        return _first_header_source(session)
    candidates = list(
        active_source_queryset(session)
        .filter(role=role)
        .exclude(api_upload_id="")
        .order_by("id")
    )
    if not candidates:
        candidates = list(
            session.files.filter(detached_at__isnull=True, role=role)
            .exclude(role__startswith=DETACHED_ROLE_PREFIX)
            .exclude(api_upload_id="")
            .order_by("id")
        )
    for source in candidates:
        mutation = getattr(source, "upload_mutation", None)
        if mutation is not None and mutation.state != ApiMutation.State.COMPLETED:
            continue
        if _source_has_mapping_headers(source):
            return source
    return None


def primary_mapping_source(session: ImportSession) -> SourceFile | None:
    """Active upload that supplies headers for column mapping (if any)."""

    return authoritative_mapping_source(session)


def mapping_auto_start_ready(session: ImportSession) -> bool:
    """True when required uploads are complete and the mapping source exists."""

    if not session_requires_column_mapping(session):
        return False
    route = route_for_session(session)
    if route is None:
        return False
    problems = validate_uploads_against_route(
        route, completed_roles=completed_upload_roles(session)
    )
    if problems:
        return False
    return authoritative_mapping_source(session) is not None


def expected_mapping_destination(session: ImportSession) -> dict[str, Any] | None:
    """Destination descriptor expected for the current setup (catalog or CRM).

    Catalog mode uses setup intent only (no network). Connected-CRM mode keys on
    connection identity; provider inventory details remain on the saved draft.
    Returns None when setup cannot resolve a destination.
    """

    intent = read_operator_intent(session)
    connection_id = str(session.setup_connection_id or "").strip()
    vocabulary = str(getattr(session, "setup_vocabulary", "") or "").strip() or None
    # CMX-0/M1: connected-match MAP plan destination is catalog/ingest.
    # Currency still keys connection_id when match+connected.
    try:
        if intent is not None:
            catalog_id = catalog_id_for_setup_context(
                entity=intent.entity,
                operation=intent.operation,
                people_output=intent.people_output,
                vocabulary=vocabulary,
            )
        else:
            catalog_id = catalog_id_for_setup_context(
                entity=session.setup_entity,
                operation=session.setup_operation,
                people_output=session.setup_people_output,
                vocabulary=vocabulary,
            )
    except SetupValidationError:
        catalog_id = None
    if (
        intent is not None
        and intent.operation == "crm_matching"
        and intent.reference_source == "connected_crm"
        and connection_id
    ):
        # Without a live connection resolve, require draft catalog + connection
        # identity. Full ingest mint is validated at plan create via
        # _destination_for_session.
        body: dict[str, Any] = {
            "destination_mode": "catalog",
            "connection_id": connection_id,
        }
        if catalog_id:
            # Product vocabulary on connected match still yields MAP-R1 id here;
            # draft from plan create holds the true ingest catalog_id.
            body["catalog_id"] = catalog_id
        return body
    if not catalog_id:
        return None
    return {
        "destination_mode": "catalog",
        "catalog_id": catalog_id,
    }


def mapping_destination_matches_current(
    saved: dict[str, Any] | None,
    expected: dict[str, Any] | None,
) -> bool:
    """True when saved draft destination still matches current setup binding."""

    if not isinstance(saved, dict) or not isinstance(expected, dict):
        return False
    mode = str(expected.get("destination_mode") or "").strip()
    if mode == "catalog":
        if str(saved.get("destination_mode") or "").strip() != "catalog":
            return False
        # Connected-match (M1): connection_id must match when expected supplies it.
        exp_conn = str(expected.get("connection_id") or "").strip()
        if exp_conn:
            saved_conn = str(saved.get("connection_id") or "").strip()
            # Plan destination may not embed connection_id; accept draft sidecar.
            if saved_conn and saved_conn != exp_conn:
                return False
            write_side = saved.get("m1_write_destination")
            if isinstance(write_side, dict):
                side_conn = str(write_side.get("connection_id") or "").strip()
                if side_conn and side_conn != exp_conn:
                    return False
        exp_catalog = str(expected.get("catalog_id") or "").strip()
        saved_catalog = str(saved.get("catalog_id") or "").strip()
        if exp_catalog and saved_catalog and exp_catalog != saved_catalog:
            # Connected path expected may be product catalog while draft is ingest.
            if exp_conn:
                return bool(saved_catalog)
            return False
        return bool(saved_catalog)
    if mode == "crm":
        return (
            str(saved.get("destination_mode") or "").strip() == "crm"
            and str(saved.get("connection_id") or "").strip()
            == str(expected.get("connection_id") or "").strip()
            and bool(str(expected.get("connection_id") or "").strip())
        )
    return False


def mapping_draft_binding_is_current(
    session: ImportSession, draft: dict[str, Any]
) -> bool:
    """True when draft upload_id, headers, and destination match the session now.

    MAP-R5 gate: a confirmed plan for a prior setup/upload must not enable
    Start import. Checks are local (no API call).
    """

    upload_id = str(draft.get("upload_id") or "").strip()
    if not upload_id:
        return False
    source = primary_mapping_source(session)
    if source is None:
        return False
    if str(source.api_upload_id or "").strip() != upload_id:
        return False
    saved_headers = draft.get("source_headers")
    if isinstance(saved_headers, list) and saved_headers:
        current = [
            str(c) for c in (source.columns if isinstance(source.columns, list) else [])
        ]
        if [str(h) for h in saved_headers] != current:
            return False
    expected = expected_mapping_destination(session)
    saved_dest = draft.get("destination")
    if expected is None:
        # Incomplete setup that should route → fail closed.
        if session.setup_entity or session.setup_operation:
            return False
        # Pre-migration product_key-only sessions: require a saved destination
        # blob plus the upload match above (cannot re-derive catalog).
        return isinstance(saved_dest, dict) and bool(saved_dest)
    return mapping_destination_matches_current(
        saved_dest if isinstance(saved_dest, dict) else None,
        expected,
    )


def confirmed_column_mapping_bind(session: ImportSession) -> dict[str, str]:
    """Return wire column_mapping object from session MAP draft, or raise.

    Requires confirmed digests **and** that the draft still matches the current
    upload and setup destination (MAP-R5 currency). Stale confirmed plans fail
    closed before workflow create is attempted.
    """

    options = session.options if isinstance(session.options, dict) else {}
    draft = options.get(_MAP_OPTIONS_KEY)
    if not isinstance(draft, dict):
        raise SetupValidationError("Confirm column mapping before starting the import.")
    if str(draft.get("status") or "").strip() != "confirmed":
        raise SetupValidationError("Confirm column mapping before starting the import.")
    plan_id = str(draft.get("plan_id") or "").strip()
    if not plan_id:
        raise SetupValidationError("Confirm column mapping before starting the import.")
    digests = draft.get("confirmed_digests")
    if not isinstance(digests, dict):
        digests = {}
    payload: dict[str, str] = {"plan_id": plan_id}
    for key in _BIND_DIGEST_KEYS:
        value = str(digests.get(key) or draft.get(key) or "").strip()
        if len(value) != 64:
            raise SetupValidationError(
                "Confirmed column mapping is incomplete. Open Map columns, "
                "confirm again, then start the import."
            )
        payload[key] = value
    if not mapping_draft_binding_is_current(session, draft):
        raise SetupValidationError(_STALE_MAPPING_MESSAGE)
    return payload


def product_requires_column_mapping(product_key: str | None) -> bool:
    """True when workflow create must bind a confirmed column-mapping plan."""

    return str(product_key or "").strip() in _COLUMN_MAPPING_BIND_PRODUCTS


def session_requires_column_mapping(session: ImportSession) -> bool:
    """True when this import session's product requires confirmed mapping.

    Prefer the draft route product when present; fall back to a frozen product
    key (resumed / pre-migration sessions).
    """

    route = route_for_session(session)
    if route is not None:
        return product_requires_column_mapping(route.product_key)
    return product_requires_column_mapping(session.product_key)


def column_mapping_draft(session: ImportSession) -> dict[str, Any]:
    """Return the session MAP draft dict (empty when absent)."""

    options = session.options if isinstance(session.options, dict) else {}
    raw = options.get(_MAP_OPTIONS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def session_has_confirmed_column_mapping(session: ImportSession) -> bool:
    """True when bind payload is complete **and** still matches current setup."""

    try:
        confirmed_column_mapping_bind(session)
        return True
    except SetupValidationError:
        return False


def column_mapping_gate_state(session: ImportSession) -> str:
    """Operator-facing mapping gate for the linear happy path.

    Returns one of:
    - ``not_required`` — product does not bind column mapping
    - ``missing`` — required, no usable draft yet
    - ``draft`` — plan exists but is not confirmed (or digests incomplete)
    - ``stale`` — confirmed digests present but upload/destination no longer match
    - ``confirmed`` — bind-ready for the current upload and setup
    """

    if not session_requires_column_mapping(session):
        return "not_required"
    draft = column_mapping_draft(session)
    if not draft:
        return "missing"
    if str(draft.get("status") or "").strip() == "confirmed":
        digests = draft.get("confirmed_digests")
        if not isinstance(digests, dict):
            digests = {}
        digests_ok = all(
            len(str(digests.get(key) or draft.get(key) or "").strip()) == 64
            for key in _BIND_DIGEST_KEYS
        )
        plan_ok = bool(str(draft.get("plan_id") or "").strip())
        if digests_ok and plan_ok and mapping_draft_binding_is_current(session, draft):
            return "confirmed"
        if digests_ok and plan_ok:
            return "stale"
        return "draft"
    return "draft"


@dataclass(frozen=True, slots=True)
class UploadCompatibility:
    role: str
    usable: bool
    reason: str
    source: SourceFile | None
    state: str


def session_has_frozen_workflow(session: ImportSession) -> bool:
    if session.active_workflow_id is not None:
        return True
    return session.api_mutations.filter(mutation_kind="create_workflow").exists()


def read_operator_intent(session: ImportSession) -> OperatorIntent | None:
    if not session.setup_entity or not session.setup_operation:
        return None
    try:
        return parse_operator_intent(
            entity=session.setup_entity,
            operation=session.setup_operation,
            reference_source=session.setup_reference_source or None,
            people_output=session.setup_people_output or None,
        )
    except RouteError:
        return None


def route_for_session(session: ImportSession) -> RouteDecision | None:
    intent = read_operator_intent(session)
    if intent is None:
        return None
    try:
        return derive_route(intent)
    except RouteError:
        return None


def session_display_label(session: ImportSession) -> str:
    if session.product_key and session.active_workflow_id is not None:
        # Resumed runs: frozen API product identity wins.
        from .constants import display_product_label

        return display_product_label(session.product_key)
    route = route_for_session(session)
    if route is not None and route.customer_label:
        return route.customer_label
    return display_setup_intent_label(
        entity=session.setup_entity,
        operation=session.setup_operation,
        reference_source=session.setup_reference_source,
        people_output=session.setup_people_output,
        product_key=session.product_key,
    )


def source_roles_for_route(route: RouteDecision) -> tuple[SourceRole, ...]:
    """Ordered upload slots for the current draft route."""

    roles: list[SourceRole] = []
    ordered = sorted(route.required_upload_roles) + sorted(route.optional_upload_roles)
    # Prefer primary list roles first.
    priority = ("dataset", "raw_list", "accounts", "contacts", "leads")
    ordered = [key for key in priority if key in set(ordered)] + [
        key for key in ordered if key not in priority
    ]
    # de-dupe preserving order
    seen: set[str] = set()
    for key in ordered:
        if key in seen:
            continue
        seen.add(key)
        label, help_text = SETUP_ROLE_LABELS.get(
            key, (key.replace("_", " ").title(), "")
        )
        # Entity-specific primary label
        if key == "dataset":
            if route.target_object == "account":
                label = "Accounts to prepare"
            elif route.target_object in {"contact", "lead"}:
                label = "People list to prepare"
        if key == "raw_list":
            if route.product_key == "easyimports.account_list_import":
                label = "Accounts to prepare"
            elif route.product_key == "easyimports.list_import":
                label = "People list to prepare"
        roles.append(
            SourceRole(
                key=key,
                label=label,
                required=key in route.required_upload_roles,
                help_text=help_text,
            )
        )
    return tuple(roles)


def active_source_queryset(session: ImportSession):
    """Sources that still occupy an active upload slot for setup/create."""

    return session.files.filter(
        upload_mutation__isnull=False,
        detached_at__isnull=True,
    ).exclude(role__startswith=DETACHED_ROLE_PREFIX)


def is_detached_role(role: str) -> bool:
    return str(role).startswith(DETACHED_ROLE_PREFIX)


def completed_upload_roles(session: ImportSession) -> set[str]:
    roles: set[str] = set()
    for source in (
        active_source_queryset(session)
        .filter(
            upload_mutation__state=ApiMutation.State.COMPLETED,
        )
        .exclude(api_upload_id="")
    ):
        roles.add(source.role)
    return roles


def completed_upload_ids(session: ImportSession) -> dict[str, str]:
    return {
        item.role: item.api_upload_id
        for item in active_source_queryset(session)
        .filter(upload_mutation__state=ApiMutation.State.COMPLETED)
        .exclude(api_upload_id="")
    }


def lock_session_for_setup(session: ImportSession) -> ImportSession:
    """Acquire the session row lock used by every upload/setup transaction."""

    return ImportSession.objects.select_for_update().get(
        pk=session.pk,
        owner_id=session.owner_id,
        is_legacy=False,
        archived_at__isnull=True,
    )


def lock_session_sources(session: ImportSession) -> list[SourceFile]:
    """Lock every SourceFile for a session in deterministic primary-key order.

    Call only after ``lock_session_for_setup`` / session ``select_for_update`` so
    all setup/upload/detach/create paths share:

    ImportSession → SourceFile (by pk) → ApiMutation
    """

    return list(
        SourceFile.objects.select_for_update()
        .filter(session_id=session.pk)
        .select_related("upload_mutation")
        .order_by("pk")
    )


def latest_detached_source(
    session: ImportSession, role: str, *, slot_generation: int
) -> SourceFile | None:
    """Return the most recent intentionally detached source for a role generation."""

    return (
        session.files.filter(
            original_role=role,
            detached_at__isnull=False,
            slot_generation=slot_generation,
            upload_mutation__isnull=False,
        )
        .select_related("upload_mutation")
        .order_by("-detached_at", "-pk")
        .first()
    )


def next_upload_generation(session: ImportSession, role: str) -> int:
    """Next logical upload generation for a role's active or empty slot.

    After intentional detach of a completed/rejected upload, the original role
    has no active SourceFile but retains historical mutations. Reuse requires
    generation = max(historical) + 1 so the journal does not collide with or
    replay the detached receipt.
    """

    if is_detached_role(role):
        raise SetupValidationError("Detached roles cannot receive new uploads.")
    active = (
        active_source_queryset(session)
        .filter(role=role)
        .select_related("upload_mutation")
        .first()
    )
    if active is not None:
        if (
            active.upload_mutation.state == ApiMutation.State.REJECTED
            and active.replacement_authorized_at is not None
        ):
            return int(active.slot_generation) + 1
        return int(active.slot_generation)
    identity = f"upload:{session.id}:{role}"
    latest = (
        session.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest is None:
        return 0
    return int(latest.logical_action_generation) + 1


@transaction.atomic
def detach_active_upload(
    source: SourceFile,
    *,
    expected_setup_revision: int | None = None,
) -> SourceFile:
    """Remove a terminal upload from the active setup role set.

    Preserves retained bytes and mutation history. Pending/unknown uploads
    continue to reserve their slots and cannot be detached. The original role
    is freed for a later upload at a new authorized generation.

    Lock order: ImportSession → SourceFile rows (by pk).
    """

    session = ImportSession.objects.select_for_update().get(pk=source.session_id)
    locked_sources = lock_session_sources(session)
    locked = next((item for item in locked_sources if item.pk == source.pk), None)
    if locked is None:
        raise SetupValidationError("Upload slot not found.")
    if expected_setup_revision is not None and int(expected_setup_revision) != int(
        session.setup_revision
    ):
        raise FormTokenError(
            "This setup form is stale. Reload it before detaching files."
        )
    if locked.upload_mutation_id is None:
        raise SetupValidationError("Only registered uploads can be detached.")
    if locked.detached_at is not None or is_detached_role(locked.role):
        return locked
    if session_has_frozen_workflow(session):
        raise SetupValidationError(
            "Uploads cannot be detached after the import workflow was created."
        )
    state = locked.upload_mutation.state
    if state in {ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN}:
        raise SetupValidationError(
            "This upload is still in progress. Resolve or retry it before "
            "removing it from the setup."
        )
    if state not in {ApiMutation.State.COMPLETED, ApiMutation.State.REJECTED}:
        raise SetupValidationError(
            "Only completed or rejected uploads can be detached from setup."
        )
    original_role = locked.original_role or locked.role
    locked.original_role = original_role
    locked.detached_at = timezone.now()
    locked.role = f"{DETACHED_ROLE_PREFIX}{original_role}__{locked.pk}"
    locked.save(update_fields=["role", "original_role", "detached_at"])
    return locked


def classify_uploads(
    session: ImportSession, route: RouteDecision
) -> list[UploadCompatibility]:
    rows: list[UploadCompatibility] = []
    sources = (
        active_source_queryset(session)
        .select_related("upload_mutation")
        .order_by("role", "uploaded_at")
    )
    allowed = route.allowed_upload_roles()
    for source in sources:
        state = source.upload_mutation.state if source.upload_mutation_id else ""
        completed = state == ApiMutation.State.COMPLETED and bool(source.api_upload_id)
        if source.role in route.prohibited_upload_roles:
            rows.append(
                UploadCompatibility(
                    role=source.role,
                    usable=False,
                    reason=(
                        "Not used for the current setup. Replace or leave it; "
                        "it blocks import start until removed from the active slot."
                    ),
                    source=source,
                    state=state,
                )
            )
        elif source.role not in allowed:
            rows.append(
                UploadCompatibility(
                    role=source.role,
                    usable=False,
                    reason="This file role is outside the current setup contract.",
                    source=source,
                    state=state,
                )
            )
        elif not completed:
            rows.append(
                UploadCompatibility(
                    role=source.role,
                    usable=False,
                    reason="Only completed uploads count as ready.",
                    source=source,
                    state=state,
                )
            )
        else:
            rows.append(
                UploadCompatibility(
                    role=source.role,
                    usable=True,
                    reason="",
                    source=source,
                    state=state,
                )
            )
    return rows


def draft_change_messages(
    previous: OperatorIntent | None,
    new_intent: OperatorIntent,
    session: ImportSession,
) -> list[str]:
    """Explain which existing uploads become unusable after a draft change."""

    if previous is None:
        return []
    try:
        old_route = derive_route(previous)
        new_route = derive_route(new_intent)
    except RouteError:
        return []
    if old_route == new_route:
        return []
    messages: list[str] = []
    completed = completed_upload_roles(session)
    became_prohibited = sorted(completed & new_route.prohibited_upload_roles)
    if became_prohibited:
        messages.append(
            "These completed uploads are no longer used for the new setup and "
            "must be replaced or cleared from the active slot before starting: "
            + ", ".join(became_prohibited)
            + "."
        )
    newly_required = sorted(new_route.required_upload_roles - completed)
    if newly_required:
        messages.append(
            "Add these required files for the new setup: "
            + ", ".join(newly_required)
            + "."
        )
    if new_route.required_any_of_role_groups and not any(
        completed & group for group in new_route.required_any_of_role_groups
    ):
        for group in new_route.required_any_of_role_groups:
            messages.append(
                "Matching with uploaded CRM exports still needs at least one of: "
                + ", ".join(sorted(group))
                + "."
            )
    return messages


@transaction.atomic
def save_setup_draft(
    session: ImportSession,
    *,
    entity: str,
    operation: str,
    reference_source: str,
    people_output: str,
    target_provider_id: str,
    operator_label: str,
    expected_revision: int,
    connection_id: str = "",
    vocabulary: str = "",
    match_provider_key: str = "",
) -> tuple[ImportSession, list[str]]:
    """Persist operator intent draft and bump revision when intent changes."""

    try:
        locked = ImportSession.objects.select_for_update().get(
            pk=session.pk,
            owner_id=session.owner_id,
            is_legacy=False,
            archived_at__isnull=True,
        )
    except ImportSession.DoesNotExist as exc:
        raise FormTokenError("This import session is no longer available.") from exc

    if session_has_frozen_workflow(locked):
        raise FormTokenError("Setup is frozen after the import workflow was created.")
    if int(expected_revision) != int(locked.setup_revision):
        raise FormTokenError("This setup form is stale. Reload it before continuing.")

    try:
        intent = parse_operator_intent(
            entity=entity,
            operation=operation,
            reference_source=reference_source or None,
            people_output=people_output or None,
        )
        # Ensure the intent is fully routable.
        derive_route(intent)
    except RouteError as exc:
        raise SetupValidationError(str(exc)) from exc

    if intent.reference_source == "connected_crm":
        if not CONNECTED_CRM_ENABLED_FOR_IMPORT_SETUP:
            raise SetupValidationError(
                "Matching with a connected CRM is not available for import "
                "setup yet. Upload CRM exports instead."
            )
        if not (connection_id or "").strip():
            raise SetupValidationError(
                "Choose an active CRM connection, or connect one first."
            )
    if intent.reference_source != "connected_crm":
        connection_id = ""

    # CMX-1: vocabulary intent (independent of export; no connection invent).
    try:
        normalized_vocabulary = validate_vocabulary_for_setup(
            operation=intent.operation,
            vocabulary=vocabulary or DEFAULT_VOCABULARY,
            connection_id=connection_id or "",
            match_provider_key=match_provider_key or None,
        )
    except VocabularyIntentError as exc:
        raise SetupValidationError(str(exc)) from exc

    previous = read_operator_intent(locked)
    notices = draft_change_messages(previous, intent, locked)

    submitted_operator = operator_label.strip()
    if locked.operator_label_frozen_at is not None:
        if submitted_operator != locked.operator_label:
            raise FormTokenError(
                "The operator label was frozen while this form was open. Reload it."
            )
    else:
        locked.operator_label = submitted_operator

    prior_vocabulary = normalize_vocabulary(locked.setup_vocabulary or None)
    intent_changed = (
        locked.setup_entity != intent.entity
        or locked.setup_operation != intent.operation
        or locked.setup_reference_source != intent.reference_source
        or locked.setup_people_output != (intent.people_output or "")
        or locked.setup_connection_id != (connection_id or "")
        or prior_vocabulary != normalized_vocabulary
        or locked.target_provider_id != target_provider_id
    )
    locked.setup_entity = intent.entity
    locked.setup_operation = intent.operation
    locked.setup_reference_source = intent.reference_source
    locked.setup_people_output = intent.people_output or ""
    locked.setup_connection_id = connection_id or ""
    locked.setup_vocabulary = normalized_vocabulary
    locked.target_provider_id = target_provider_id
    # product_key stays empty until successful workflow create.
    if intent_changed:
        locked.setup_revision = int(locked.setup_revision) + 1
    locked.save(
        update_fields=[
            "setup_entity",
            "setup_operation",
            "setup_reference_source",
            "setup_people_output",
            "setup_connection_id",
            "setup_vocabulary",
            "setup_revision",
            "target_provider_id",
            "operator_label",
            "updated_at",
        ]
    )
    return locked, notices


@transaction.atomic
def freeze_product_key(session: ImportSession, product_key: str) -> ImportSession:
    """Idempotently freeze the API product identity onto the session.

    Empty ``product_key`` is filled from an accepted projection. A non-empty
    key that disagrees with the projection is integrity evidence and fails
    closed rather than being silently overwritten.
    """

    key = (product_key or "").strip()
    if not key:
        return session
    locked = ImportSession.objects.select_for_update().get(pk=session.pk)
    if locked.product_key == key:
        return locked
    if locked.product_key:
        raise SetupValidationError(
            "Frozen product identity conflicts with the recovered API workflow. "
            f"session={locked.product_key!r} projection={key!r}."
        )
    locked.product_key = key
    locked.save(update_fields=["product_key", "updated_at"])
    return locked


@transaction.atomic
def assert_setup_revision(
    session: ImportSession, expected_revision: int
) -> ImportSession:
    """Prove the draft revision inside a row lock before create."""

    locked = ImportSession.objects.select_for_update().get(
        pk=session.pk,
        owner_id=session.owner_id,
        is_legacy=False,
        archived_at__isnull=True,
    )
    if int(expected_revision) != int(locked.setup_revision):
        raise FormTokenError("This setup form is stale. Reload setup and try again.")
    return locked


@transaction.atomic
def preclaim_create_workflow(
    session: ImportSession,
    *,
    expected_revision: int | None,
    products: list[dict[str, Any]],
    targets: list[dict[str, Any]],
    connections: list[dict[str, Any]] | None,
    form,
    form_instance,
    form_payload_digest: str,
    logical_action_identity: str,
    logical_action_generation: int,
) -> tuple[ImportSession, str, dict[str, Any], Any]:
    """Lock draft + sources, derive route, and preclaim create mutation.

    Network dispatch must happen after this transaction commits.
    Returns ``(session, derived_product_key, body, mutation)``.
    """

    from .api_client import create_or_reuse_mutation
    from .api_contract import validate_workflow_create
    from .command_service import _replacement_for

    # Lock order: session → every SourceFile (pk order) → create mutation.
    locked = ImportSession.objects.select_for_update().get(
        pk=session.pk,
        owner_id=session.owner_id,
        is_legacy=False,
        archived_at__isnull=True,
    )
    lock_session_sources(locked)
    if session_has_frozen_workflow(locked) and locked.active_workflow_id:
        raise SetupValidationError("This import workflow was already created.")

    route = route_for_session(locked)
    if route is not None:
        if expected_revision is None:
            raise FormTokenError(
                "This setup form is incomplete. Reload setup and try again."
            )
        if int(expected_revision) != int(locked.setup_revision):
            raise FormTokenError(
                "This setup form is stale. Reload setup and try again."
            )
        # Resolve target before preflight so product_key is known for connected
        # synthetic targets (not present in /v1/catalog/targets).
        provisional_route = route
        target = resolve_configure_target(
            session=locked,
            targets=targets,
            product_key=provisional_route.product_key,
            connections=connections,
        )
        _intent, route, uploads = create_time_preflight(
            locked,
            products=products,
            target=target,
            connections=connections,
            expected_revision=int(expected_revision),
        )
        derived_product_key = route.product_key
        raw_body = form.workflow_request(
            upload_ids=uploads,
            target_provider_id=locked.target_provider_id,
        )
        if derived_product_key in _COLUMN_MAPPING_BIND_PRODUCTS:
            raw_body = dict(raw_body)
            raw_body["column_mapping"] = confirmed_column_mapping_bind(locked)
        body = validate_workflow_create(raw_body)
        if body.get("product_key") != derived_product_key:
            raise SetupValidationError(
                "Derived product did not match the create request."
            )
    else:
        # Pre-migration / product-key-only sessions: frozen key without draft.
        if not locked.product_key:
            raise SetupValidationError(
                "Finish setup choices before starting the import."
            )
        uploads = completed_upload_ids(locked)
        derived_product_key = locked.product_key
        raw_body = form.workflow_request(
            upload_ids=uploads,
            target_provider_id=locked.target_provider_id,
        )
        if derived_product_key in _COLUMN_MAPPING_BIND_PRODUCTS:
            raw_body = dict(raw_body)
            raw_body["column_mapping"] = confirmed_column_mapping_bind(locked)
        body = validate_workflow_create(raw_body)
        if body.get("product_key") != derived_product_key:
            raise SetupValidationError(
                "Configuration product does not match this session."
            )

    mutation = create_or_reuse_mutation(
        session=locked,
        form_instance=form_instance,
        mutation_kind="create_workflow",
        route="/v1/workflows",
        logical_action_identity=logical_action_identity,
        logical_action_generation=logical_action_generation,
        request_json=body,
        form_payload_digest=form_payload_digest,
        resource_identity=str(locked.id),
        replacement_of=_replacement_for(
            locked, logical_action_identity, logical_action_generation
        ),
    )
    return locked, derived_product_key, body, mutation


def validate_route_against_catalog(
    route: RouteDecision,
    *,
    products: list[dict[str, Any]],
    target: dict[str, Any],
) -> None:
    product_entry = next(
        (item for item in products if item["product_key"] == route.product_key),
        None,
    )
    if product_entry is None:
        raise SetupValidationError(
            "The selected setup is not available in the current API product catalog."
        )
    target_maximums = target.get("maximum_modes") or {}
    for track in product_entry.get("tracks") or {}:
        if track not in target_maximums:
            raise SetupValidationError(
                f"The destination does not support the {track!r} track required "
                "for this setup."
            )
    if route.reference_acquisition_requirement == "execute":
        product_modes = (product_entry.get("tracks") or {}).get(
            "reference_acquisition", []
        )
        ceiling = target_maximums.get("reference_acquisition")
        if "execute" not in product_modes:
            raise SetupValidationError("This product cannot acquire CRM references.")
        if ceiling not in {"execute"} and ceiling != "execute":
            # ceiling is a maximum mode name; execute requires ceiling >= execute
            rank = {
                "disabled": 0,
                "preview": 1,
                "plan_only": 1,
                "dry_run": 2,
                "execute": 3,
            }
            if rank.get(str(ceiling), -1) < rank["execute"]:
                raise SetupValidationError(
                    "The selected destination cannot load CRM references."
                )


def connected_import_target_projection(
    *,
    target_provider_id: str,
    product_key: str,
    connection: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Synthetic catalog target for connection-derived journey providers.

    Connected CRM stacks are not listed under ``/v1/catalog/targets``; the
    wizard freezes ``execution_target_provider_id`` from the connection instead.
    Configure UI still needs a maximum-modes map so track fields can be built.

    Phase 1C: track ceilings come from API-projected product-scoped
    ``maximum_authorization.by_product[product_key]`` when present. Flat
    ``maximum_authorization`` fields are the provider-global fallback. Django
    must not invent write/provision ceilings from provider-name inference as
    authority. Operator default *selection* remains disabled until opt-in (1D).
    """

    from .campaign_member_setup import campaign_member_ceiling_from_connection

    # Safe local defaults only when the API has not projected a ceiling yet.
    base_modes = {
        "reference_acquisition": "execute",
        "account_provisioning": "disabled",
        "person_duplicate_resolution": "disabled",
        "people_writes": "disabled",
        "campaign_member_writes": "disabled",
        "account_writes": "disabled",
        "dataset_writes": "disabled",
        "duplicate_execution": "disabled",
        "delivery": "preview",
        "crm_query": "disabled",
    }
    max_auth = (connection or {}).get("maximum_authorization") or {}
    if not isinstance(max_auth, dict):
        max_auth = {}

    # Provider-global flat fields (no product context).
    for track in (
        "reference_acquisition",
        "account_provisioning",
        "people_writes",
        "person_duplicate_resolution",
        "campaign_member_writes",
        "delivery",
        "duplicate_execution",
    ):
        if track in max_auth and max_auth[track] is not None:
            base_modes[track] = str(max_auth[track])

    # Product-scoped effective ceilings win when present (Phase 1C / D11).
    by_product = max_auth.get("by_product") or {}
    product_auth = None
    if isinstance(by_product, dict):
        product_auth = by_product.get(product_key)
    if isinstance(product_auth, dict) and product_auth:
        for track, mode in product_auth.items():
            if track in base_modes and mode is not None:
                base_modes[str(track)] = str(mode)

    # Legacy CM helper only when API never projected campaign_member_writes.
    if "campaign_member_writes" not in max_auth and not (
        isinstance(product_auth, dict) and "campaign_member_writes" in product_auth
    ):
        base_modes["campaign_member_writes"] = campaign_member_ceiling_from_connection(
            connection
        )

    return {
        "target_provider_id": target_provider_id,
        "maximum_modes": base_modes,
    }


def resolve_configure_target(
    *,
    session: ImportSession,
    targets: list[dict[str, Any]],
    product_key: str,
    connections: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Resolve the configure-form target catalog entry (catalog or connected)."""

    for item in targets:
        if item.get("target_provider_id") == session.target_provider_id:
            return item
    if (
        session.setup_reference_source == "connected_crm"
        and session.setup_connection_id
    ):
        connection_id = str(session.setup_connection_id or "").strip()
        connection = next(
            (
                item
                for item in (connections or [])
                if str(item.get("connection_id") or "") == connection_id
            ),
            None,
        )
        return connected_import_target_projection(
            target_provider_id=session.target_provider_id,
            product_key=product_key,
            connection=connection,
        )
    raise SetupValidationError(
        "The selected destination is not available in the current target catalog."
    )


def validate_connection_for_draft(
    *,
    intent: OperatorIntent,
    connection_id: str,
    connections: list[dict[str, Any]],
) -> None:
    if intent.reference_source != "connected_crm":
        return
    if not connection_id:
        raise SetupValidationError("Choose an active CRM connection for this setup.")
    match = next(
        (
            item
            for item in connections
            if str(item.get("connection_id") or "") == connection_id
        ),
        None,
    )
    if match is None:
        raise SetupValidationError(
            "The selected CRM connection was not found for this browser session."
        )
    status = str(match.get("status") or "").lower()
    if status not in {"connected", "active", "ready"}:
        # Accept common connected statuses; reject explicit disconnected/failed.
        if status in {
            "disconnected",
            "credential_unavailable",
            "failed",
            "error",
            "expired",
            "revoked",
        }:
            raise SetupValidationError(
                "The selected CRM connection is not active. Connect again first."
            )
        # If status is missing, require presence only (fake catalog variance).
        if status and status not in {"connected", "active", "ready", "available"}:
            raise SetupValidationError(
                "The selected CRM connection is not ready for reference loading."
            )


def create_time_preflight(
    session: ImportSession,
    *,
    products: list[dict[str, Any]],
    target: dict[str, Any],
    connections: list[dict[str, Any]] | None = None,
    expected_revision: int | None = None,
) -> tuple[OperatorIntent, RouteDecision, dict[str, str]]:
    """Derive route and validate draft + completed uploads before API create."""

    if expected_revision is not None:
        session = assert_setup_revision(session, expected_revision)
    intent = read_operator_intent(session)
    if intent is None:
        raise SetupValidationError("Finish setup choices before starting the import.")
    if (
        intent.reference_source == "connected_crm"
        and not CONNECTED_CRM_ENABLED_FOR_IMPORT_SETUP
    ):
        raise SetupValidationError(
            "Matching with a connected CRM is not available for import setup "
            "yet. Upload CRM exports instead."
        )
    route = derive_route(intent)
    validate_route_against_catalog(route, products=products, target=target)
    if intent.reference_source == "connected_crm":
        validate_connection_for_draft(
            intent=intent,
            connection_id=session.setup_connection_id,
            connections=connections or [],
        )
    uploads = completed_upload_ids(session)
    problems = validate_uploads_against_route(route, completed_roles=set(uploads))
    if problems:
        raise SetupValidationError(" ".join(problems))
    return intent, route, uploads
