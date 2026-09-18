"""Django CRM query UI (Phase 4B Q-CAT-B)."""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods

from .api_client import (
    ApiOperationInProgressError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationDispatchResult,
    MutationExplicitRetryRequired,
    MutationReuseError,
    create_or_reuse_mutation,
)
from .forms import CrmQueryForm
from .models import ApiMutation, ImportSession
from .workflow_state import (
    FormTokenError,
    issue_form_token,
    owner_id_for_request,
    store_workflow_projection,
    validate_form_token,
)

CLIENT_ERRORS = (
    ApiUnavailableError,
    ApiOperationInProgressError,
    ApiRejectedError,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    FormTokenError,
    KeyError,
    TypeError,
    ValueError,
)

# States where re-submitting the same signed form is the correct recovery.
_PRESERVE_TOKEN_STATES = frozenset(
    {
        ApiMutation.State.UNKNOWN,
        ApiMutation.State.COMPLETED,
        ApiMutation.State.PENDING,
    }
)


def _owner_session(request) -> str:
    owner = owner_id_for_request(request)
    return f"django-{owner}"


def _query_journal_session(request) -> ImportSession:
    """Session used only for the durable CRM query mutation journal."""

    owner = owner_id_for_request(request)
    existing = (
        ImportSession.objects.filter(owner_id=owner, product_key="crm.query")
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return ImportSession.objects.create(
        owner_id=owner,
        operator_label="CRM query",
        product_key="crm.query",
        target_provider_id="",
    )


def _form_logical_identity(*, owner_session: str, journal_id) -> str:
    return f"crm-query-form:{owner_session}:{journal_id}"


def _start_identity_for_form(
    *, owner_session: str, form_instance
) -> str:
    """Scope start identity to one signed form instance.

    Retries of the same submitted form converge; a fresh form (new form_instance)
    is a new intentional start even when the query body is identical.
    """

    return f"crm-query-start:{owner_session}:{form_instance}"


def _delete_form_identity(*, query_id: str, owner_session: str) -> str:
    """Form-token identity for the delete control (not the mutation identity)."""

    return f"crm-query-delete-form:{query_id}:{owner_session}"


def _delete_identity_for_form(
    *, query_id: str, owner_session: str, form_instance
) -> str:
    """Mutation identity for one delete attempt (form-instance scoped).

    Same-token retries converge. A fresh token after rejection creates a new
    cleanup attempt with a new idempotency key.
    """

    return f"crm-query-delete:{query_id}:{owner_session}:{form_instance}"


def _handoff_form_identity(*, query_id: str, owner_session: str) -> str:
    """Form-token identity for the H1-A selected-ID handoff control."""

    return f"crm-h1a-form:{query_id}:{owner_session}"


def _handoff_identity_for_form(
    *, query_id: str, owner_session: str, form_instance
) -> str:
    """Mutation identity for one H1-A handoff attempt (form-instance scoped).

    Same signed form converges (double-submit / UNKNOWN exact-retry). A fresh
    token after rejection is a new intentional handoff with a new identity.
    """

    return f"crm-h1a:{query_id}:{owner_session}:{form_instance}"


def _dispatch_query_mutation(
    client: EasyImportsApiClient, mutation: ApiMutation
) -> MutationDispatchResult:
    """Dispatch a CRM query mutation, enabling exact retry when UNKNOWN.

    Mirrors connection-mutation exact-retry: UNKNOWN is refused unless
    ``explicit_retry=True``. Re-submitting the same signed form is the
    operator's exact retry for that frozen attempt.
    """

    mutation.refresh_from_db()
    return client.dispatch(
        mutation,
        explicit_retry=mutation.state == ApiMutation.State.UNKNOWN,
    )


def _rejection_message(mutation: ApiMutation) -> str:
    code = (mutation.error_code or "").strip()
    message = (mutation.error_message or "").strip()
    if code and message:
        return f"{message} ({code})"
    if message:
        return message
    if code:
        return f"The API rejected this action ({code})."
    return "The API rejected this action."


def _should_preserve_token(mutation: ApiMutation | None) -> bool:
    if mutation is None:
        return False
    mutation.refresh_from_db()
    return mutation.state in _PRESERVE_TOKEN_STATES


def _start_form_initial(
    *,
    form_token: str,
    cleaned_or_data: dict | None = None,
    post_data=None,
) -> dict:
    """Build form initial, preserving field values after a failure."""

    cleaned_or_data = cleaned_or_data or {}
    return {
        "form_token": form_token,
        "mode": cleaned_or_data.get("mode") or "by_ids",
        "object_family": cleaned_or_data.get("object_family") or "people",
        "template_version": cleaned_or_data.get("template_version") or 1,
        "connection_id": cleaned_or_data.get("connection_id"),
        "template_id": cleaned_or_data.get("template_id") or "",
        "template_params_json": cleaned_or_data.get("template_params_json")
        or (post_data.get("template_params_json") if post_data is not None else "")
        or "",
        "field_projection": cleaned_or_data.get("field_projection")
        or (post_data.get("field_projection") if post_data is not None else "")
        or "",
        "record_ids": cleaned_or_data.get("record_ids")
        or (post_data.get("record_ids") if post_data is not None else "")
        or "",
    }


def _redisplay_start_form(
    *,
    connections: list,
    owner_id,
    journal: ImportSession,
    form_identity: str,
    cleaned_or_data: dict | None,
    post_data,
    preserve_token: str | None,
) -> CrmQueryForm:
    """Rebuild the start form after an error.

    Preserve the signed token only for UNKNOWN exact-retry, PENDING/in-progress
    recovery, or post-dispatch recovery after a completed start. Terminal
    rejection / invalid token / reuse conflict get a fresh form identity with
    field values retained.
    """

    if preserve_token:
        token = preserve_token
    else:
        token = issue_form_token(
            owner_id=owner_id,
            session=journal,
            action_kind="crm_query_start",
            logical_action_identity=form_identity,
        )
    return CrmQueryForm(
        None,
        connections=connections,
        initial=_start_form_initial(
            form_token=token,
            cleaned_or_data=cleaned_or_data,
            post_data=post_data,
        ),
    )


@require_http_methods(["GET", "POST"])
def crm_query(request):
    """Start a connection-bound CRM query and open the workflow for authorize."""

    client = EasyImportsApiClient()
    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    journal = _query_journal_session(request)
    providers: list[dict] = []
    connections: list[dict] = []
    queries: list[dict] = []
    load_error = None
    try:
        providers = client.crm_providers().get("providers", [])
        connections = client.crm_connections(owner_session=owner_session).get(
            "connections", []
        )
        queries = client.crm_queries(owner_session=owner_session).get("queries", [])
    except (ApiUnavailableError, ApiRejectedError) as exc:
        load_error = str(exc)
        messages.error(request, str(exc))

    connected = [item for item in connections if item.get("status") == "connected"]
    form_identity = _form_logical_identity(
        owner_session=owner_session, journal_id=journal.id
    )
    start_token = issue_form_token(
        owner_id=owner_id,
        session=journal,
        action_kind="crm_query_start",
        logical_action_identity=form_identity,
    )
    form = CrmQueryForm(
        request.POST or None,
        connections=connected,
        initial=_start_form_initial(form_token=start_token),
    )

    if request.method == "POST" and form.is_valid():
        body: dict = {
            "connection_id": form.cleaned_data["connection_id"],
            "mode": form.cleaned_data["mode"],
            "object_family": form.cleaned_data["object_family"],
            "owner_session": owner_session,
        }
        if form.cleaned_data["mode"] == "by_ids":
            body["record_ids"] = form.cleaned_data["record_ids_list"]
        else:
            body["template_id"] = form.cleaned_data["template_id"]
            body["template_version"] = form.cleaned_data["template_version"]
            if form.cleaned_data.get("template_params"):
                body["template_params"] = form.cleaned_data["template_params"]
        if form.cleaned_data.get("field_projection_list"):
            body["field_projection"] = form.cleaned_data["field_projection_list"]
        submitted_token = form.cleaned_data["form_token"]
        mutation: ApiMutation | None = None
        try:
            claims = validate_form_token(
                submitted_token,
                owner_id=owner_id,
                session=journal,
                action_kind="crm_query_start",
                logical_action_identity=form_identity,
            )
            identity = _start_identity_for_form(
                owner_session=owner_session,
                form_instance=claims["form_instance"],
            )
            mutation = create_or_reuse_mutation(
                session=journal,
                form_instance=claims["form_instance"],
                mutation_kind="crm_query_start",
                route="/v1/crm/queries",
                logical_action_identity=identity,
                request_json=body,
            )
            result = _dispatch_query_mutation(client, mutation)
            mutation = result.mutation
            mutation.refresh_from_db()
            if mutation.state == ApiMutation.State.REJECTED:
                messages.error(request, _rejection_message(mutation))
                form = _redisplay_start_form(
                    connections=connected,
                    owner_id=owner_id,
                    journal=journal,
                    form_identity=form_identity,
                    cleaned_or_data=form.cleaned_data,
                    post_data=form.data,
                    preserve_token=None,
                )
            elif mutation.state == ApiMutation.State.PENDING:
                messages.error(
                    request,
                    "The query start is still in progress. Retry with the same form "
                    "to finish the exact action.",
                )
                form = _redisplay_start_form(
                    connections=connected,
                    owner_id=owner_id,
                    journal=journal,
                    form_identity=form_identity,
                    cleaned_or_data=form.cleaned_data,
                    post_data=form.data,
                    preserve_token=submitted_token,
                )
            elif mutation.state != ApiMutation.State.COMPLETED:
                messages.error(
                    request,
                    "The query start outcome is uncertain. Retry with the same form "
                    "to finish the exact action.",
                )
                form = _redisplay_start_form(
                    connections=connected,
                    owner_id=owner_id,
                    journal=journal,
                    form_identity=form_identity,
                    cleaned_or_data=form.cleaned_data,
                    post_data=form.data,
                    preserve_token=submitted_token,
                )
            else:
                query = result.response
                if not isinstance(query, dict) or not query.get("query_id"):
                    messages.error(
                        request,
                        "The API returned an unexpected query start response.",
                    )
                    form = _redisplay_start_form(
                        connections=connected,
                        owner_id=owner_id,
                        journal=journal,
                        form_identity=form_identity,
                        cleaned_or_data=form.cleaned_data,
                        post_data=form.data,
                        preserve_token=submitted_token,
                    )
                else:
                    # Post-dispatch recovery: if workflow GET fails after a
                    # completed start, keep the form identity so retry replays
                    # the completed mutation instead of minting a second query.
                    try:
                        existing_workflow_session = (
                            ImportSession.objects.filter(
                                owner_id=owner_id,
                                product_key="easyimports.crm_query",
                                options__crm_query_id=query["query_id"],
                            )
                            .order_by("-created_at")
                            .first()
                        )
                        if existing_workflow_session is not None:
                            workflow_session = existing_workflow_session
                        else:
                            workflow_session = ImportSession.objects.create(
                                owner_id=owner_id,
                                operator_label=(
                                    f"CRM query · {query.get('object_family')} · "
                                    f"{query.get('mode')}"
                                ),
                                product_key="easyimports.crm_query",
                                target_provider_id=query.get("target_provider_id")
                                or "",
                                status=ImportSession.Status.RUNNING,
                                options={
                                    "crm_query_id": query["query_id"],
                                    "connection_id": query["connection_id"],
                                    "provider_key": query.get("provider_key"),
                                    "object_family": query.get("object_family"),
                                    "mode": query.get("mode"),
                                    "query_status": query.get("status"),
                                },
                            )
                        projection = client.workflow(
                            query["run_id"], owner_session=owner_session
                        )
                        store_workflow_projection(
                            workflow_session,
                            projection,
                            resource_url=f"/v1/workflows/{query['run_id']}",
                        )
                        messages.success(
                            request,
                            "CRM query started. Authorize CRM_QUERY execute on the "
                            "workflow page, then open results from CRM query list.",
                        )
                        return redirect(
                            "importer:workflow", session_id=workflow_session.id
                        )
                    except CLIENT_ERRORS as post_exc:
                        messages.error(request, str(post_exc))
                        form = _redisplay_start_form(
                            connections=connected,
                            owner_id=owner_id,
                            journal=journal,
                            form_identity=form_identity,
                            cleaned_or_data=form.cleaned_data,
                            post_data=form.data,
                            preserve_token=submitted_token,
                        )
        except (FormTokenError, MutationReuseError) as exc:
            messages.error(request, str(exc))
            form = _redisplay_start_form(
                connections=connected,
                owner_id=owner_id,
                journal=journal,
                form_identity=form_identity,
                cleaned_or_data=form.cleaned_data,
                post_data=form.data,
                preserve_token=None,
            )
        except CLIENT_ERRORS as exc:
            # PENDING lease busy, API 202 / mutation_in_progress, UNKNOWN, etc.
            messages.error(request, str(exc))
            preserve = submitted_token if _should_preserve_token(mutation) else None
            # MutationBusyError / in-progress without a row yet: still preserve
            # the submitted form so we do not start a duplicate while another
            # claim holds the same form_instance lease path.
            if preserve is None and isinstance(
                exc, (MutationBusyError, ApiOperationInProgressError)
            ):
                preserve = submitted_token
            form = _redisplay_start_form(
                connections=connected,
                owner_id=owner_id,
                journal=journal,
                form_identity=form_identity,
                cleaned_or_data=form.cleaned_data,
                post_data=form.data,
                preserve_token=preserve,
            )

    return render(
        request,
        "importer/crm_query.html",
        {
            "form": form,
            "providers": providers,
            "connections": connections,
            "connected_count": len(connected),
            "queries": queries,
            "load_error": load_error,
        },
    )


def _issue_handoff_token(
    *,
    owner_id,
    journal: ImportSession,
    query_id: str,
    owner_session: str,
) -> str:
    return issue_form_token(
        owner_id=owner_id,
        session=journal,
        action_kind="crm_h1a_handoff",
        action_id=query_id,
        logical_action_identity=_handoff_form_identity(
            query_id=query_id, owner_session=owner_session
        ),
    )


def _attach_journey_workflow_session(
    *,
    client: EasyImportsApiClient,
    owner_id,
    owner_session: str,
    journey: dict,
    query_id: str,
) -> ImportSession:
    """Idempotently bind a completed handoff journey to a Django workflow session."""

    journey_id = str(journey.get("journey_id") or "")
    existing = (
        ImportSession.objects.filter(
            owner_id=owner_id,
            product_key="easyimports.duplicate_resolution",
            options__crm_journey_id=journey_id,
        )
        .order_by("-created_at")
        .first()
        if journey_id
        else None
    )
    if existing is not None:
        workflow_session = existing
    else:
        workflow_session = ImportSession.objects.create(
            owner_id=owner_id,
            operator_label=f"CRM duplicates · selected IDs · {query_id}",
            product_key="easyimports.duplicate_resolution",
            target_provider_id=journey.get("target_provider_id") or "",
            status=ImportSession.Status.RUNNING,
            options={
                "crm_journey_id": journey.get("journey_id"),
                "connection_id": journey.get("connection_id"),
                "provider_key": journey.get("provider_key"),
                "entity_family": journey.get("entity_family"),
                "source_mode": journey.get("source_mode"),
                "query_id": query_id,
                "journey_status": journey.get("status"),
            },
        )
    projection = client.workflow(journey["run_id"], owner_session=owner_session)
    store_workflow_projection(
        workflow_session,
        projection,
        resource_url=f"/v1/workflows/{journey['run_id']}",
    )
    return workflow_session


@require_http_methods(["GET", "POST"])
def crm_query_detail(request, query_id: str):
    """Show frozen query rows (when ready) and allow journaled delete / H1-A handoff."""

    client = EasyImportsApiClient()
    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    journal = _query_journal_session(request)
    query = None
    page = None
    load_error = None
    handoff_selected_ids = ""
    handoff_max_mode = "dry_run"
    delete_form_identity = _delete_form_identity(
        query_id=query_id, owner_session=owner_session
    )
    delete_token = issue_form_token(
        owner_id=owner_id,
        session=journal,
        action_kind="crm_query_delete",
        action_id=query_id,
        logical_action_identity=delete_form_identity,
    )
    handoff_token = _issue_handoff_token(
        owner_id=owner_id,
        journal=journal,
        query_id=query_id,
        owner_session=owner_session,
    )

    if request.method == "POST" and request.POST.get("action") == "handoff":
        # H1-A: operator-confirmed selected-ID handoff into duplicate journey.
        # Form-scoped signed token + form-instance logical identity: double-submit
        # and UNKNOWN exact-retry converge; rejection mints a fresh token.
        submitted_handoff_token = request.POST.get("form_token", "")
        handoff_selected_ids = str(request.POST.get("selected_ids") or "")
        handoff_max_mode = (
            str(request.POST.get("duplicate_execution_maximum") or "dry_run").strip()
            or "dry_run"
        )
        selected = [
            part.strip()
            for part in handoff_selected_ids.replace("\n", ",").split(",")
            if part.strip()
        ]
        object_family = str(request.POST.get("object_family") or "").strip()
        entity_family = (
            "person"
            if object_family == "people"
            else "company"
            if object_family == "companies"
            else ""
        )
        handoff_query_id = str(request.POST.get("query_id") or query_id).strip()
        body = {
            "connection_id": str(request.POST.get("connection_id") or "").strip(),
            "entity_family": entity_family,
            "source_mode": "selected_ids",
            "selected_ids": selected,
            "query_id": handoff_query_id,
            "query_result_digest": str(
                request.POST.get("query_result_digest") or ""
            ).strip(),
            "duplicate_execution_maximum": handoff_max_mode,
            "owner_session": owner_session,
        }
        mutation: ApiMutation | None = None
        try:
            if not selected:
                raise ValueError("Select at least one record ID from this query.")
            if not body["query_result_digest"]:
                raise ValueError(
                    "Query result digest is required for selected-ID handoff."
                )
            if entity_family not in {"company", "person"}:
                raise ValueError("Query object family is not handoff-compatible.")
            if handoff_query_id != query_id:
                raise ValueError(
                    "Handoff query_id must match the query results page."
                )
            claims = validate_form_token(
                submitted_handoff_token,
                owner_id=owner_id,
                session=journal,
                action_kind="crm_h1a_handoff",
                action_id=query_id,
                logical_action_identity=_handoff_form_identity(
                    query_id=query_id, owner_session=owner_session
                ),
            )
            handoff_identity = _handoff_identity_for_form(
                query_id=query_id,
                owner_session=owner_session,
                form_instance=claims["form_instance"],
            )
            mutation = create_or_reuse_mutation(
                session=journal,
                form_instance=claims["form_instance"],
                mutation_kind="crm_duplicate_journey_start",
                route="/v1/crm/duplicate-journeys",
                logical_action_identity=handoff_identity,
                request_json=body,
            )
            result = _dispatch_query_mutation(client, mutation)
            mutation = result.mutation
            mutation.refresh_from_db()
            if mutation.state == ApiMutation.State.REJECTED:
                messages.error(request, _rejection_message(mutation))
                # Fresh form_instance → new handoff identity for a new attempt.
                handoff_token = _issue_handoff_token(
                    owner_id=owner_id,
                    journal=journal,
                    query_id=query_id,
                    owner_session=owner_session,
                )
            elif mutation.state == ApiMutation.State.PENDING:
                messages.error(
                    request,
                    "Handoff is still in progress. Retry with the same form to "
                    "finish the exact action.",
                )
                handoff_token = submitted_handoff_token or handoff_token
            elif mutation.state != ApiMutation.State.COMPLETED:
                messages.error(
                    request,
                    "Handoff outcome is uncertain. Retry with the same form to "
                    "finish the exact action.",
                )
                handoff_token = submitted_handoff_token or handoff_token
            else:
                journey = result.response
                if not isinstance(journey, dict) or not journey.get("run_id"):
                    messages.error(
                        request,
                        "The API returned an unexpected handoff response.",
                    )
                    handoff_token = submitted_handoff_token or handoff_token
                else:
                    try:
                        workflow_session = _attach_journey_workflow_session(
                            client=client,
                            owner_id=owner_id,
                            owner_session=owner_session,
                            journey=journey,
                            query_id=handoff_query_id,
                        )
                        messages.success(
                            request,
                            "Duplicate journey started from selected query IDs. "
                            "Authorize the next workflow step.",
                        )
                        return redirect(
                            "importer:workflow", session_id=workflow_session.id
                        )
                    except CLIENT_ERRORS as post_exc:
                        # Completed API receipt: preserve token so retry replays
                        # the same mutation instead of minting a second journey.
                        messages.error(request, str(post_exc))
                        handoff_token = submitted_handoff_token or handoff_token
        except (FormTokenError, MutationReuseError) as exc:
            messages.error(request, str(exc))
            handoff_token = _issue_handoff_token(
                owner_id=owner_id,
                journal=journal,
                query_id=query_id,
                owner_session=owner_session,
            )
        except ValueError as exc:
            messages.error(request, str(exc))
            handoff_token = _issue_handoff_token(
                owner_id=owner_id,
                journal=journal,
                query_id=query_id,
                owner_session=owner_session,
            )
        except CLIENT_ERRORS as exc:
            messages.error(request, str(exc))
            if _should_preserve_token(mutation) or isinstance(
                exc, (MutationBusyError, ApiOperationInProgressError)
            ):
                handoff_token = submitted_handoff_token or handoff_token
            else:
                handoff_token = _issue_handoff_token(
                    owner_id=owner_id,
                    journal=journal,
                    query_id=query_id,
                    owner_session=owner_session,
                )

    if request.method == "POST" and request.POST.get("action") != "handoff":
        submitted_delete_token = request.POST.get("form_token", "")
        mutation: ApiMutation | None = None
        try:
            claims = validate_form_token(
                submitted_delete_token,
                owner_id=owner_id,
                session=journal,
                action_kind="crm_query_delete",
                action_id=query_id,
                logical_action_identity=delete_form_identity,
            )
            delete_identity = _delete_identity_for_form(
                query_id=query_id,
                owner_session=owner_session,
                form_instance=claims["form_instance"],
            )
            mutation = create_or_reuse_mutation(
                session=journal,
                form_instance=claims["form_instance"],
                mutation_kind="crm_query_delete",
                route=f"/v1/crm/queries/{query_id}",
                logical_action_identity=delete_identity,
                request_json={
                    "query_id": query_id,
                    "owner_session": owner_session,
                },
            )
            result = _dispatch_query_mutation(client, mutation)
            mutation = result.mutation
            mutation.refresh_from_db()
            if mutation.state == ApiMutation.State.COMPLETED:
                messages.success(request, f"Query {query_id} deleted.")
                return redirect("importer:crm_query")
            if mutation.state == ApiMutation.State.REJECTED:
                messages.error(request, _rejection_message(mutation))
                # Fresh form_instance → new delete identity → new cleanup attempt.
                delete_token = issue_form_token(
                    owner_id=owner_id,
                    session=journal,
                    action_kind="crm_query_delete",
                    action_id=query_id,
                    logical_action_identity=delete_form_identity,
                )
            elif mutation.state == ApiMutation.State.PENDING:
                messages.error(
                    request,
                    "Delete is still in progress. Retry with the same form to "
                    "finish the exact action.",
                )
                delete_token = submitted_delete_token or delete_token
            else:
                messages.error(
                    request,
                    "Delete outcome is uncertain. Retry with the same form to "
                    "finish the exact action.",
                )
                delete_token = submitted_delete_token or delete_token
        except (FormTokenError, MutationReuseError) as exc:
            messages.error(request, str(exc))
            delete_token = issue_form_token(
                owner_id=owner_id,
                session=journal,
                action_kind="crm_query_delete",
                action_id=query_id,
                logical_action_identity=delete_form_identity,
            )
        except CLIENT_ERRORS as exc:
            messages.error(request, str(exc))
            if _should_preserve_token(mutation) or isinstance(
                exc, (MutationBusyError, ApiOperationInProgressError)
            ):
                delete_token = submitted_delete_token or delete_token
            else:
                delete_token = issue_form_token(
                    owner_id=owner_id,
                    session=journal,
                    action_kind="crm_query_delete",
                    action_id=query_id,
                    logical_action_identity=delete_form_identity,
                )

    try:
        query = client.crm_query(query_id, owner_session=owner_session)
        if query.get("status") == "snapshot_ready" or query.get("snapshot_id"):
            cursor = request.GET.get("cursor") or None
            try:
                page_size = int(request.GET.get("page_size") or 50)
            except (TypeError, ValueError):
                page_size = 50
            page = client.crm_query_rows(
                query_id,
                owner_session=owner_session,
                cursor=cursor,
                page_size=page_size,
            )
    except (ApiUnavailableError, ApiRejectedError) as exc:
        load_error = str(exc)
        messages.error(request, str(exc))

    return render(
        request,
        "importer/crm_query_detail.html",
        {
            "query": query,
            "page": page,
            "query_id": query_id,
            "delete_form_token": delete_token,
            "handoff_form_token": handoff_token,
            "load_error": load_error,
            "handoff_selected_ids": handoff_selected_ids,
            "handoff_max_mode": handoff_max_mode,
        },
    )
