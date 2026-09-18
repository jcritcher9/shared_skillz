"""Django CRM connection journey and Connect CRM setup wizard shell."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, NamedTuple
from uuid import UUID, uuid4

from django.contrib import messages
from django.db.models import Q
from django.db.models.functions import Coalesce, Now
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

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
    RegistrationSecretError,
    api_error_technical_details,
    authorization_code_digest,
    canonical_digest,
    clear_ephemeral_oauth_code,
    clear_ephemeral_reclaim_handle,
    create_or_reuse_mutation,
    registration_secret_digest,
    reclaim_handle_digest,
    stage_ephemeral_oauth_code,
    stage_ephemeral_reclaim_handle,
    stage_ephemeral_registration_secrets,
)
from .command_service import (
    crm_connect_action_generation,
    crm_reconnect_action_generation,
)
from .models import ApiMutation, ImportSession
from .oauth_redirect import (
    resolve_hubspot_oauth_redirect_uri,
    resolve_salesforce_oauth_redirect_uri,
)
from .settings_views import (
    _REG_ACTION_START_OVER,
    _dispatch_registration,
    _is_loopback_request,
    _issue_reg_form_token,
    _registration_form_digest,
    _registration_generation,
    _require_registration_secret,
    _settings_session,
    list_stuck_unknown_registration_puts,
)
from .workflow_state import (
    FormTokenError,
    decode_form_token,
    issue_form_token,
    owner_id_for_request,
    validate_form_token,
)

# Phase 2+ setup wizard — server draft on ImportSession (D3).
SETUP_PRODUCT_KEY = "crm.setup"
SETUP_OPTIONS_KEY = "crm_setup"
SETUP_PROVIDER_CHOICES: tuple[dict[str, str], ...] = (
    {
        "provider_key": "fake",
        "provider_label": "Practice CRM",
        "blurb": "Try EasyImports without a live org. No app credentials required.",
    },
    {
        "provider_key": "salesforce",
        "provider_label": "Salesforce",
        "blurb": "Connected App credentials, then connect a production or sandbox org.",
    },
    {
        "provider_key": "hubspot",
        "provider_label": "HubSpot",
        "blurb": "Private app token (recommended) or OAuth app, then connect a portal.",
    },
)
# Salesforce = Phase 3A; HubSpot = Phase 3B. No pending guided shells remain.
SETUP_GUIDED_PENDING: frozenset[str] = frozenset()
SETUP_ACTION_CHOOSE = "crm_setup_choose"
SETUP_ACTION_PRACTICE_CONNECT = "crm_setup_practice_connect"
SETUP_ACTION_SF_SAVE = "crm_setup_salesforce_save"
SETUP_ACTION_SF_CONNECT = "crm_setup_salesforce_connect"
SETUP_SF_REG_IDENTITY = "crm-reg:salesforce:put:wizard"
SETUP_ACTION_HS_SAVE = "crm_setup_hubspot_save"
SETUP_ACTION_HS_CONNECT = "crm_setup_hubspot_connect"
SETUP_HS_REG_IDENTITY = "crm-reg:hubspot:put:wizard"


def _owner_session(request) -> str:
    owner = owner_id_for_request(request)
    return f"django-{owner}"


def _connection_session(request) -> ImportSession:
    """Session-scoped web mutation journal ownership for CRM connection actions."""

    owner = owner_id_for_request(request)
    existing = (
        ImportSession.objects.filter(owner_id=owner, product_key="crm.connection")
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return ImportSession.objects.create(
        owner_id=owner,
        operator_label="CRM connection",
        product_key="crm.connection",
        target_provider_id="",
    )


def _setup_session(request) -> ImportSession:
    """Server draft session for the guided Connect CRM setup wizard (D3)."""

    owner = owner_id_for_request(request)
    existing = (
        ImportSession.objects.filter(owner_id=owner, product_key=SETUP_PRODUCT_KEY)
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return ImportSession.objects.create(
        owner_id=owner,
        operator_label="CRM setup wizard",
        product_key=SETUP_PRODUCT_KEY,
        target_provider_id="",
    )


def _setup_draft(session: ImportSession) -> dict[str, Any]:
    options = session.options if isinstance(session.options, dict) else {}
    draft = options.get(SETUP_OPTIONS_KEY)
    return dict(draft) if isinstance(draft, dict) else {}


def _save_setup_draft(session: ImportSession, draft: dict[str, Any]) -> None:
    options = dict(session.options) if isinstance(session.options, dict) else {}
    # Never persist secrets in the wizard draft (client_secret / tokens forbidden).
    safe = {
        "step": str(draft.get("step") or "pick"),
        "provider_key": str(draft.get("provider_key") or ""),
        "provider_label": str(draft.get("provider_label") or ""),
        "connection_id": str(draft.get("connection_id") or ""),
        "outcome": str(draft.get("outcome") or ""),
        "label": str(draft.get("label") or ""),
        "login_environment": str(draft.get("login_environment") or ""),
        "client_id": str(draft.get("client_id") or ""),
        "my_domain_host": str(draft.get("my_domain_host") or ""),
        "expected_org_id": str(draft.get("expected_org_id") or ""),
        # HubSpot guided path (Phase 3B) — non-secret registration metadata only.
        "auth_mode": str(draft.get("auth_mode") or ""),
        "expected_hub_id": str(draft.get("expected_hub_id") or ""),
    }
    options[SETUP_OPTIONS_KEY] = safe
    session.options = options
    session.save(update_fields=["options", "updated_at"])


def _provider_choice(provider_key: str) -> dict[str, str] | None:
    for choice in SETUP_PROVIDER_CHOICES:
        if choice["provider_key"] == provider_key:
            return choice
    return None


def _connect_provider_result(
    request,
    *,
    provider_key: str,
    form_instance: UUID | None = None,
    logical_action_generation: int | None = None,
) -> tuple[str, str | None]:
    """Run journaled CRM connect for one provider.

    When ``form_instance`` is supplied (wizard practice connect), the start
    mutation is form-scoped for exact-retry (D3): same form reuses the frozen
    start generation instead of advancing to a new connect attempt.

    Returns ``(status, detail)`` where status is:
    - ``ok`` — connection established; detail is connection_id
    - ``oauth_redirect`` — detail is external authorization URL
    - ``pending`` — oauth/complete accepted async; detail is connection_id
    - ``error`` — detail is operator-facing message
    """

    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    # R4: generation covers the full start+complete action. It advances only
    # after OAuth complete is terminal (or start rejects), so a crash between
    # start and complete resumes the same generation instead of orphaning it.
    #
    # Fake provider: Django immediately completes the OAuth loop with a one-time
    # code held only for this request, then POSTed server-to-server to the API.
    # Live HubSpot (Path C): start returns a public authorization_url; redirect
    # the browser and complete on callback without storing the code in Django
    # durable storage beyond the API mutation journal body.
    connect_identity = f"crm-connect:{provider_key}:{owner_session}"
    if form_instance is not None:
        prior_for_form = (
            session.api_mutations.filter(form_instance=form_instance)
            .order_by("-created_at")
            .first()
        )
        if prior_for_form is not None:
            connect_generation = int(prior_for_form.logical_action_generation)
        elif logical_action_generation is not None:
            connect_generation = int(logical_action_generation)
        else:
            connect_generation = crm_connect_action_generation(
                session, provider_key=provider_key, owner_session=owner_session
            )
        start_form_instance = form_instance
    else:
        start_form_instance = uuid4()
        connect_generation = crm_connect_action_generation(
            session, provider_key=provider_key, owner_session=owner_session
        )
    try:
        start_mutation = create_or_reuse_mutation(
            session=session,
            form_instance=start_form_instance,
            mutation_kind="crm_connection_start",
            route="/v1/crm/connections",
            logical_action_identity=connect_identity,
            logical_action_generation=connect_generation,
            request_json={
                "provider_key": provider_key,
                "owner_session": owner_session,
            },
        )
        started = _dispatch_connection_mutation(client, start_mutation).response
        connection_id = started["connection_id"]
        authorization = started.get("authorization") or {}
        authorization_url = authorization.get("authorization_url")
        if authorization_url and provider_key != "fake":
            request.session["crm_oauth_pending"] = {
                "connection_id": connection_id,
                "provider_key": provider_key,
                "state": authorization.get("state"),
                "owner_session": owner_session,
                "connect_identity": connect_identity,
            }
            return "oauth_redirect", str(authorization_url)

        complete_identity = f"crm-complete:{connection_id}:{owner_session}"
        existing_complete = session.api_mutations.filter(
            logical_action_identity=complete_identity
        ).first()
        if existing_complete is not None:
            complete_mutation = existing_complete
            complete_mutation.refresh_from_db()
            if complete_mutation.state == ApiMutation.State.COMPLETED:
                return "ok", _completed_connection_id(
                    complete_mutation, fallback=connection_id
                )
            if complete_mutation.state == ApiMutation.State.REJECTED:
                offer = _reclaim_offer_from_response(complete_mutation.response_json)
                if offer:
                    request.crm_reclaim_offer = offer
                return "error", (
                    complete_mutation.error_message or "CRM connection was rejected."
                )
            already_accepted = (
                complete_mutation.operation_in_progress
                or _lease_is_active(complete_mutation)
            )
            if complete_mutation.state == ApiMutation.State.UNKNOWN or already_accepted:
                settled = _settle_oauth_complete_from_connection(
                    client,
                    complete_mutation,
                    connection_id=connection_id,
                    owner_session=owner_session,
                )
                if settled.outcome == "completed":
                    return "ok", _completed_connection_id(
                        complete_mutation, fallback=connection_id
                    )
                if settled.outcome == "failed":
                    if settled.reclaim_offer:
                        request.crm_reclaim_offer = settled.reclaim_offer
                    return "error", (
                        complete_mutation.error_message
                        or "CRM connection was rejected."
                    )
                if already_accepted:
                    return "pending", connection_id
        else:
            complete_mutation = create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="crm_connection_oauth_complete",
                route=f"/v1/crm/connections/{connection_id}/oauth/complete",
                logical_action_identity=complete_identity,
                request_json={
                    "authorization_code": f"django-fake-code-{uuid4().hex}",
                    "state": authorization.get("state")
                    or started["authorization"]["state"],
                    "owner_session": owner_session,
                },
            )
        try:
            completed = _dispatch_connection_mutation(client, complete_mutation)
        except ApiOperationInProgressError:
            return "pending", connection_id
        if completed.mutation.state == ApiMutation.State.REJECTED:
            offer = _reclaim_offer_from_response(completed.response)
            if offer:
                request.crm_reclaim_offer = offer
            return "error", (
                completed.mutation.error_message or "CRM connection was rejected."
            )
        if completed.mutation.state == ApiMutation.State.PENDING:
            return "pending", connection_id
        resolved_connection_id = str(
            (completed.response or {}).get("connection_id") or connection_id
        )
        return "ok", resolved_connection_id
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        KeyError,
        TypeError,
    ) as exc:
        return "error", str(exc)


def _dispatch_connection_mutation(
    client: EasyImportsApiClient, mutation: ApiMutation
) -> MutationDispatchResult:
    """Dispatch a CRM connection mutation, enabling exact retry when UNKNOWN.

    Production ``EasyImportsApiClient.dispatch`` refuses UNKNOWN mutations unless
    ``explicit_retry=True``. Intentional Connect/Disconnect after an uncertain
    outcome is the operator exact-retry for that frozen attempt, so resume the
    same mutation with the guard enabled instead of staying stuck.
    """

    mutation.refresh_from_db()
    return client.dispatch(
        mutation,
        explicit_retry=mutation.state == ApiMutation.State.UNKNOWN,
    )


# Operator may abandon an uncertain OAuth complete only after this age and
# only when no dispatch lease is active (prevents racing an in-flight callback).
OAUTH_ABANDON_MIN_AGE_SECONDS = 60


def _lease_is_active(mutation: ApiMutation) -> bool:
    """True when another dispatch currently owns this mutation."""

    mutation.refresh_from_db()
    if mutation.lease_token is None or mutation.lease_expires_at is None:
        return False
    return mutation.lease_expires_at > timezone.now()


def _connection_id_from_complete_identity(
    identity: str, owner_session: str
) -> str:
    prefix = "crm-complete:"
    text = str(identity or "")
    if not text.startswith(prefix):
        return ""
    rest = text[len(prefix) :]
    mid = f":{owner_session}:"
    if mid in rest:
        return rest.split(mid, 1)[0]
    suffix = f":{owner_session}"
    if rest.endswith(suffix):
        return rest[: -len(suffix)]
    return ""


def _oauth_complete_identity(
    connection_id: str,
    owner_session: str,
    *,
    reconnect_attempt_id: str | None = None,
) -> str:
    if reconnect_attempt_id:
        return (
            f"crm-complete:{connection_id}:{owner_session}:{reconnect_attempt_id}"
        )
    return f"crm-complete:{connection_id}:{owner_session}"


def _oauth_complete_mutation(
    session: ImportSession,
    *,
    connection_id: str,
    owner_session: str,
    reconnect_attempt_id: str | None = None,
) -> ApiMutation | None:
    return (
        session.api_mutations.filter(
            logical_action_identity=_oauth_complete_identity(
                connection_id,
                owner_session,
                reconnect_attempt_id=reconnect_attempt_id,
            )
        )
        .order_by("-created_at")
        .first()
    )


def _completed_connection_id(mutation: ApiMutation | None, *, fallback: str) -> str:
    """Return the authoritative connection id from a completed receipt.

    A second authorization for the same CRM tenant can converge on the already
    connected resource, so the response id may intentionally differ from the
    pending id in the callback URL.
    """

    if mutation is not None:
        mutation.refresh_from_db()
        body = mutation.response_json
        if isinstance(body, dict):
            resolved = str(body.get("connection_id") or "").strip()
            if resolved:
                return resolved
    return str(fallback)


def _mutation_age_seconds(mutation: ApiMutation) -> float:
    """Age used for abandon eligibility (prefer last dispatch clock)."""

    stamp = mutation.dispatched_at or mutation.updated_at or mutation.first_submitted_at
    if stamp is None:
        return 0.0
    return max(0.0, (timezone.now() - stamp).total_seconds())


def _can_abandon_uncertain_oauth(
    mutation: ApiMutation | None,
) -> tuple[bool, str]:
    """Whether the operator may start abandon for this OAuth complete."""

    if mutation is None:
        return False, "No uncertain CRM authorization attempt was found."
    mutation.refresh_from_db()
    if mutation.state not in {
        ApiMutation.State.UNKNOWN,
        ApiMutation.State.PENDING,
    }:
        return False, "This CRM authorization is already finished."
    if _lease_is_active(mutation):
        # In-progress abandon claims also hold a lease; surface a clear wait.
        if mutation.error_code == "oauth_abandon_in_progress":
            return (
                False,
                "Abandon is already in progress. Wait and refresh.",
            )
        return (
            False,
            "CRM authorization is still in progress. Wait for it to finish.",
        )
    age = _mutation_age_seconds(mutation)
    if age < OAUTH_ABANDON_MIN_AGE_SECONDS:
        remaining = int(OAUTH_ABANDON_MIN_AGE_SECONDS - age) + 1
        return (
            False,
            f"Wait about {remaining}s before abandoning this uncertain authorization.",
        )
    return True, ""


def _claim_oauth_abandon_lease(mutation: ApiMutation) -> UUID | None:
    """Atomically claim an unleased, aged UNKNOWN/PENDING complete for abandon.

    Returns the claim token on success, or None if the row is no longer eligible
    (including when a callback acquired a lease between check and claim).
    """

    claim = uuid4()
    age_cutoff = timezone.now() - timedelta(seconds=OAUTH_ABANDON_MIN_AGE_SECONDS)
    claimed = (
        ApiMutation.objects.filter(pk=mutation.pk)
        .filter(
            state__in=[
                ApiMutation.State.UNKNOWN,
                ApiMutation.State.PENDING,
            ]
        )
        .filter(Q(lease_token__isnull=True) | Q(lease_expires_at__lte=timezone.now()))
        .annotate(
            age_stamp=Coalesce(
                "dispatched_at",
                "updated_at",
                "first_submitted_at",
            )
        )
        .filter(age_stamp__lte=age_cutoff)
        .update(
            lease_token=claim,
            lease_expires_at=timezone.now() + timedelta(minutes=2),
            error_code="oauth_abandon_in_progress",
            error_message=(
                "Operator abandon in progress; waiting for remote disconnect "
                "confirmation before unlocking reconnect."
            ),
            updated_at=Now(),
        )
    )
    if claimed != 1:
        return None
    return claim


def _finalize_oauth_abandon(
    mutation: ApiMutation,
    *,
    claim: UUID,
) -> bool:
    """Terminal REJECT only while still holding the abandon claim and non-terminal."""

    updated = ApiMutation.objects.filter(
        pk=mutation.pk,
        lease_token=claim,
        state__in=[
            ApiMutation.State.UNKNOWN,
            ApiMutation.State.PENDING,
        ],
    ).update(
        state=ApiMutation.State.REJECTED,
        error_code="oauth_abandoned_by_operator",
        error_message=(
            "Operator abandoned an uncertain CRM authorization after remote "
            "disconnect confirmation. Start a new connection."
        ),
        lease_token=None,
        lease_expires_at=None,
        updated_at=Now(),
    )
    mutation.refresh_from_db()
    return updated == 1


def _release_oauth_abandon_claim(
    mutation: ApiMutation,
    *,
    claim: UUID,
    error_code: str,
    error_message: str,
) -> None:
    """Drop the abandon claim without terminalizing (disconnect still uncertain)."""

    ApiMutation.objects.filter(
        pk=mutation.pk,
        lease_token=claim,
        state__in=[
            ApiMutation.State.UNKNOWN,
            ApiMutation.State.PENDING,
        ],
    ).update(
        lease_token=None,
        lease_expires_at=None,
        error_code=error_code,
        error_message=error_message,
        updated_at=Now(),
    )
    mutation.refresh_from_db()


def _connection_remote_status(
    client: EasyImportsApiClient,
    *,
    connection_id: str,
    owner_session: str,
) -> str:
    """Return connected|pending|failed|absent|unknown for the API connection."""

    try:
        resource = client.crm_connection(connection_id, owner_session=owner_session)
    except ApiRejectedError as exc:
        if getattr(exc, "code", "") in {
            "crm_connection_not_found",
            "not_found",
            "invalid_crm_connection",
        }:
            return "absent"
        return "unknown"
    except (ApiUnavailableError, ApiConsistencyError, TypeError, KeyError):
        return "unknown"
    status = str(resource.get("status") or "").strip().lower()
    if status == "connected":
        return "connected"
    if status in {"failed", "disconnected", "credential_unavailable"}:
        return "failed"
    if status == "pending":
        return "pending"
    return "unknown"


def _confirm_remote_disconnect(
    client: EasyImportsApiClient,
    session: ImportSession,
    *,
    connection_id: str,
    owner_session: str,
) -> str:
    """Attempt durable disconnect and re-query.

    Returns:
      - ``scrubbed``: connection is failed/disconnected/absent
      - ``connected``: still connected after attempt (do not abandon)
      - ``uncertain``: pending/unreachable after attempt
    """

    remote = _connection_remote_status(
        client, connection_id=connection_id, owner_session=owner_session
    )
    if remote in {"failed", "absent"}:
        return "scrubbed"
    if remote == "connected":
        # Still try disconnect; OAuth may have completed.
        pass
    try:
        disconnect = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_disconnect",
            route=f"/v1/crm/connections/{connection_id}/disconnect",
            logical_action_identity=(
                f"crm-abandon-disconnect:{connection_id}:{owner_session}"
            ),
            request_json={"owner_session": owner_session},
        )
        result = _dispatch_connection_mutation(client, disconnect)
        body = result.response if isinstance(result.response, dict) else {}
        status = str(body.get("status") or "").strip().lower()
        if status in {"disconnected", "failed", "credential_unavailable"}:
            return "scrubbed"
    except ApiRejectedError as exc:
        # Already gone is success for scrub.
        if getattr(exc, "code", "") in {
            "crm_connection_not_found",
            "not_found",
            "invalid_crm_connection",
        }:
            return "scrubbed"
        return "uncertain"
    except (
        ApiUnavailableError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        KeyError,
        TypeError,
    ):
        return "uncertain"

    # Authoritative re-query after dispatch.
    remote = _connection_remote_status(
        client, connection_id=connection_id, owner_session=owner_session
    )
    if remote in {"failed", "absent"}:
        return "scrubbed"
    if remote == "connected":
        return "connected"
    return "uncertain"


def _reject_oauth_complete_for_reconnect(
    mutation: ApiMutation,
    *,
    error_code: str = "oauth_complete_uncertain",
    error_message: str | None = None,
) -> bool:
    """Terminalize a *settled-unknown* OAuth complete so reconnect can advance.

    Never rejects:
    - COMPLETED / REJECTED (already terminal)
    - PENDING while a lease is active (in-flight dispatch owns the mutation)
    - UNKNOWN while a lease is active
    - unleased PENDING (stranded create that never dispatched; leave for abandon)

    Returns True when the mutation was moved to REJECTED.
    """

    mutation.refresh_from_db()
    if mutation.state not in {
        ApiMutation.State.UNKNOWN,
        ApiMutation.State.PENDING,
    }:
        return False
    if _lease_is_active(mutation):
        return False
    # PENDING without an active lease is not auto-rejected (operator abandon).
    # Only auto-terminalize UNKNOWN (uncertain after a send).
    if mutation.state == ApiMutation.State.PENDING:
        return False
    mutation.state = ApiMutation.State.REJECTED
    mutation.error_code = error_code
    mutation.error_message = error_message or (
        "CRM authorization outcome is uncertain and the one-time code is no "
        "longer available. Start a new CRM connection."
    )
    mutation.lease_token = None
    mutation.lease_expires_at = None
    mutation.save(
        update_fields=[
            "state",
            "error_code",
            "error_message",
            "lease_token",
            "lease_expires_at",
            "updated_at",
        ]
    )
    return True


def _complete_oauth_mutation_from_resource(
    mutation: ApiMutation, resource: dict
) -> None:
    """Write a COMPLETED receipt with the same bookkeeping as api_client dispatch."""

    digest = canonical_digest(resource)
    mutation.state = ApiMutation.State.COMPLETED
    mutation.http_status = 200
    mutation.response_json = resource
    mutation.response_digest = digest
    mutation.error_code = ""
    mutation.error_message = ""
    mutation.lease_token = None
    mutation.lease_expires_at = None
    mutation.save(
        update_fields=[
            "state",
            "http_status",
            "response_json",
            "response_digest",
            "error_code",
            "error_message",
            "lease_token",
            "lease_expires_at",
            "updated_at",
        ]
    )


class _OauthSettleResult(NamedTuple):
    outcome: str
    reclaim_offer: dict[str, Any] | None = None


def _rejected_oauth_complete_response(
    request, settled: _OauthSettleResult, *, message: str
):
    messages.error(request, message)
    if settled.reclaim_offer:
        return _render_crm_connections(request, reclaim_offer=settled.reclaim_offer)
    return redirect("importer:crm_connections")


def _settle_oauth_complete_from_connection(
    client: EasyImportsApiClient,
    mutation: ApiMutation,
    *,
    connection_id: str,
    owner_session: str,
) -> _OauthSettleResult:
    """Reconcile UNKNOWN/PENDING OAuth complete without replaying the raw code.

    Prefers GET ``/v1/mutations/{key}``. Falls back to GET connection. Never
    POSTs the authorization code.

    ``outcome`` is one of: ``completed``, ``failed``, ``unknown``.
    """

    mutation.refresh_from_db()
    if mutation.state == ApiMutation.State.COMPLETED:
        return _OauthSettleResult("completed")
    if mutation.state == ApiMutation.State.REJECTED:
        return _OauthSettleResult("failed")
    if _lease_is_active(mutation):
        return _OauthSettleResult("unknown")

    try:
        operation_status = client.mutation_status(
            mutation.idempotency_key, owner_session=owner_session
        )
    except (
        ApiRejectedError,
        ApiUnavailableError,
        ApiConsistencyError,
        TypeError,
        KeyError,
    ):
        operation_status = None
    if isinstance(operation_status, dict):
        status = str(operation_status.get("status") or "").strip().lower()
        if status == "completed":
            try:
                result = client.reconcile_mutation_status(mutation, operation_status)
            except (ApiConsistencyError, TypeError, KeyError):
                result = None
            if result is not None:
                if result.mutation.state == ApiMutation.State.COMPLETED:
                    return _OauthSettleResult("completed")
                if result.mutation.state == ApiMutation.State.REJECTED:
                    return _OauthSettleResult(
                        "failed",
                        _reclaim_offer_from_response(result.response),
                    )

    try:
        resource = client.crm_connection(connection_id, owner_session=owner_session)
    except ApiRejectedError as exc:
        # Definitive not-found / invalid: allow a new connect generation.
        if getattr(exc, "code", "") in {
            "crm_connection_not_found",
            "not_found",
            "invalid_crm_connection",
        }:
            _reject_oauth_complete_for_reconnect(mutation)
            return _OauthSettleResult("failed")
        return _OauthSettleResult("unknown")
    except (ApiUnavailableError, ApiConsistencyError, TypeError, KeyError):
        return _OauthSettleResult("unknown")

    status = str(resource.get("status") or "").strip().lower()
    if status == "connected":
        mutation.refresh_from_db()
        if mutation.state == ApiMutation.State.COMPLETED:
            return _OauthSettleResult("completed")
        if _lease_is_active(mutation):
            return _OauthSettleResult("unknown")
        # Only settle when still non-terminal and unleased.
        if mutation.state in {
            ApiMutation.State.UNKNOWN,
            ApiMutation.State.PENDING,
        }:
            _complete_oauth_mutation_from_resource(mutation, resource)
        return _OauthSettleResult("completed")
    if status in {"failed", "disconnected", "credential_unavailable"}:
        _reject_oauth_complete_for_reconnect(mutation)
        return _OauthSettleResult("failed")
    # pending or unexpected: keep unknown (operator may abandon after age check)
    return _OauthSettleResult("unknown")


def _reconcile_pending_oauth_completes(
    client: EasyImportsApiClient,
    journal: ImportSession,
    *,
    owner_session: str,
) -> tuple[list[str], list[tuple[ApiMutation, str]], dict[str, Any] | None]:
    """GET-safe poll of in-flight oauth/complete rows. Never replays the code.

    Returns ``(completed_connection_ids, just_rejected, reclaim_offer)``.
    ``just_rejected`` is ``(mutation, operator_reason)`` for rows this call
    settled to ``REJECTED``. Historical rejections are not re-emitted.
    """

    completed: list[str] = []
    rejected: list[tuple[ApiMutation, str]] = []
    reclaim_offer: dict[str, Any] | None = None
    pending_rows = journal.api_mutations.filter(
        mutation_kind="crm_connection_oauth_complete",
        state__in=[ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING],
    ).order_by("created_at", "id")
    for mutation in pending_rows:
        connection_id = _connection_id_from_complete_identity(
            str(mutation.logical_action_identity or ""), owner_session
        )
        if not connection_id:
            continue
        prior_state = mutation.state
        settled = _settle_oauth_complete_from_connection(
            client,
            mutation,
            connection_id=connection_id,
            owner_session=owner_session,
        )
        if settled.outcome == "completed":
            completed.append(_completed_connection_id(mutation, fallback=connection_id))
            continue
        if settled.outcome != "failed":
            continue
        mutation.refresh_from_db()
        if (
            prior_state in {ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING}
            and mutation.state == ApiMutation.State.REJECTED
        ):
            rejected.append((mutation, _rejected_oauth_complete_flash(mutation)))
            if reclaim_offer is None and settled.reclaim_offer:
                reclaim_offer = settled.reclaim_offer
    return completed, rejected, reclaim_offer


def _oauth_in_progress_message() -> str:
    return (
        "CRM authorization is in progress. Refresh this page shortly; "
        "do not start a second connection yet."
    )


def _uncertain_oauth_rows(session: ImportSession, *, owner_session: str) -> list[dict]:
    """Journal rows the operator may abandon (UI hints only)."""

    rows: list[dict] = []
    for mutation in session.api_mutations.filter(
        mutation_kind="crm_connection_oauth_complete",
        state__in=[ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING],
    ).order_by("-created_at")[:20]:
        connection_id = _connection_id_from_complete_identity(
            str(mutation.logical_action_identity or ""), owner_session
        )
        if not connection_id:
            continue
        ok, reason = _can_abandon_uncertain_oauth(mutation)
        rows.append(
            {
                "connection_id": connection_id,
                "mutation_state": mutation.state,
                "can_abandon": ok,
                "abandon_hint": reason,
                "age_seconds": int(_mutation_age_seconds(mutation)),
            }
        )
    return rows


def _hub_provider_cards(
    providers: list[dict],
    connections: list[dict],
    *,
    registered_keys: set[str] | frozenset[str] | None = None,
) -> list[dict]:
    """Shape connectable providers for the Connect CRM hub (D5 / Phase 4).

    Practice (fake) is connect-only. Device registration is proven only via the
    CRM app registration inventory (not mere catalog presence — prebuilt SF/HS
    graphs can be connectable without a device registration).
    """

    registered = {str(k) for k in (registered_keys or set()) if str(k)}
    active_keys = {
        str(row.get("provider_key") or "")
        for row in connections
        if str(row.get("status") or "") in {"connected", "pending"}
    }
    reconnectable_keys = {
        str(row.get("provider_key") or "")
        for row in connections
        if str(row.get("status") or "") in {"disconnected", "credential_unavailable"}
    }
    cards: list[dict] = []
    for provider in providers:
        key = str(provider.get("provider_key") or "")
        if not key:
            continue
        is_practice = key == "fake"
        has_active = key in active_keys
        was_disconnected = key in reconnectable_keys and not has_active
        is_registered = key in registered
        manage_url = ""
        if is_registered and key == "salesforce":
            manage_url = "importer:crm_setup_salesforce"
        elif is_registered and key == "hubspot":
            manage_url = "importer:crm_setup_hubspot"
        cards.append(
            {
                "provider_key": key,
                "provider_label": provider.get("provider_label") or key,
                "is_practice": is_practice,
                "is_registered": is_registered,
                "has_active_connection": has_active,
                "was_disconnected": was_disconnected,
                "show_connect": not has_active,
                "connect_label": (
                    "Reconnect"
                    if was_disconnected
                    else ("Connect practice" if is_practice else "Connect")
                ),
                "show_manage_app": bool(manage_url),
                "manage_url_name": manage_url,
                "ready_state": (
                    "App registered on this device — connect without re-entering secrets."
                    if is_registered and not has_active
                    else (
                        "Connected — app credentials stay vaulted on this device."
                        if is_registered and has_active
                        else ""
                    )
                ),
            }
        )
    return cards


def _registration_provider_keys(
    client: EasyImportsApiClient,
) -> tuple[set[str], Exception | None]:
    """Return (provider keys with device registration, load error or None).

    Phase 2B: registration inventory failures must not look like “no apps
    registered” without diagnostic evidence for Technical details.
    """

    try:
        body = client.crm_app_registrations()
    except (ApiUnavailableError, ApiRejectedError, ApiConsistencyError) as exc:
        return set(), exc
    keys: set[str] = set()
    for row in body.get("registrations") or []:
        if not isinstance(row, dict):
            continue
        key = str(row.get("provider_key") or "").strip()
        if key:
            keys.add(key)
    return keys, None


def _conflict_display_label(response: Any) -> str:
    if not isinstance(response, dict):
        return ""
    error = response.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    conflict = details.get("conflict") if isinstance(details, dict) else None
    if not isinstance(conflict, dict):
        return ""
    return str(conflict.get("display_label") or "").strip()


def _rejected_oauth_complete_flash(mutation: ApiMutation) -> str:
    """Operator-facing reason for a just-settled oauth/complete rejection.

    Names the blocking connection when the scrubbed conflict still carries a
    display label. Does not mention reclaim (that path cannot work yet).
    """

    mutation.refresh_from_db()
    reason = str(mutation.error_message or "").strip()
    if not reason:
        reason = "CRM authorization was rejected."
    display = _conflict_display_label(mutation.response_json)
    if display and display not in reason:
        return f"{reason} The blocking connection is {display}."
    return reason


def _apply_reclaim_no_store(response, reclaim_offer: dict[str, Any] | None):
    """Handle-bearing HTML is a handle-bearing HTTP response (hardened A)."""

    if reclaim_offer and str(reclaim_offer.get("reclaim_handle") or "").strip():
        response["Cache-Control"] = "no-store"
    return response


def _reclaim_offer_from_response(response: Any) -> dict[str, Any] | None:
    if not isinstance(response, dict):
        return None
    error = response.get("error")
    details = error.get("details") if isinstance(error, dict) else None
    conflict = details.get("conflict") if isinstance(details, dict) else None
    if not isinstance(conflict, dict):
        return None
    if str(conflict.get("reason") or "") != "tenant_already_connected_other_session":
        return None
    handle = str(conflict.get("reclaim_handle") or "").strip()
    offer_id = str(conflict.get("reclaim_offer_id") or "").strip()
    existing = str(conflict.get("existing_connection_id") or "").strip()
    if not handle or not offer_id or not existing:
        return None
    return {
        "reclaim_handle": handle,
        "reclaim_offer_id": offer_id,
        "existing_connection_id": existing,
        "provider_key": conflict.get("provider_key") or "",
        "display_label": conflict.get("display_label") or "",
        "expires_at": conflict.get("expires_at") or "",
    }


def _render_crm_connections(request, *, reclaim_offer: dict[str, Any] | None = None):
    client = EasyImportsApiClient()
    owner_session = _owner_session(request)
    journal = _connection_session(request)
    completed, rejected, reconciled_offer = _reconcile_pending_oauth_completes(
        client, journal, owner_session=owner_session
    )
    _reconcile_reconnect_mutations(client, journal, owner_session=owner_session)
    for _mutation, reason in rejected:
        messages.error(request, reason)
    if reclaim_offer is None:
        reclaim_offer = reconciled_offer
    api_technical = None
    try:
        providers = client.crm_providers().get("providers", [])
        connections = client.crm_connections(owner_session=owner_session).get(
            "connections", []
        )
        _annotate_reconnect_hub_rows(
            connections, journal, owner_session=owner_session
        )
        confirmed = [
            cid
            for cid in completed
            if _resource_is_connected(
                next(
                    (
                        row
                        for row in connections
                        if str(row.get("connection_id") or "") == cid
                    ),
                    None,
                ),
                connection_id=cid,
            )
        ]
    except (ApiUnavailableError, ApiRejectedError) as exc:
        messages.error(request, str(exc))
        api_technical = api_error_technical_details(exc)
        providers = []
        connections = []
        confirmed = []
    if confirmed and request.session.get("crm_setup_oauth_return"):
        return _oauth_success_redirect(request, connection_id=confirmed[-1])
    if confirmed:
        messages.success(request, "CRM connection established.")
    registered_keys, reg_load_error = _registration_provider_keys(client)
    if reg_load_error is not None:
        messages.error(
            request,
            "Could not load CRM app registrations on this device: " f"{reg_load_error}",
        )
        reg_details = api_error_technical_details(reg_load_error)
        if api_technical is None:
            api_technical = reg_details
        else:
            # Prefer first failure; append registration route if distinct.
            if reg_details.get("route") and not api_technical.get("route"):
                api_technical = {**api_technical, **reg_details}
    provider_cards = _hub_provider_cards(
        providers, connections, registered_keys=registered_keys
    )
    has_registered_app = bool(registered_keys)
    owner_id = owner_id_for_request(request)
    stuck_raw = (
        list_stuck_unknown_registration_puts(owner_id=owner_id) if owner_id else []
    )
    settings_session = _settings_session(request)
    stuck_registration_puts = [
        {
            "provider_key": item.provider_key,
            "generation": item.generation,
            "http_status": item.http_status,
            "start_over_form_token": _issue_reg_form_token(
                request,
                session=settings_session,
                action_kind=_REG_ACTION_START_OVER,
                action_id=item.provider_key,
            ),
        }
        for item in stuck_raw
    ]
    return _apply_reclaim_no_store(
        render(
            request,
            "importer/crm_connections.html",
            {
                "providers": providers,
                "provider_cards": provider_cards,
                "connections": connections,
                "registered_provider_keys": registered_keys,
                "has_connections": bool(connections),
                "has_connectable_providers": bool(provider_cards),
                "has_registered_app": has_registered_app,
                "uncertain_oauth": _uncertain_oauth_rows(
                    journal, owner_session=owner_session
                ),
                "api_technical": _hub_api_technical_after_remove_stash(
                    request, api_technical
                ),
                "stuck_registration_puts": stuck_registration_puts,
                "reclaim_offer": reclaim_offer,
            },
        ),
        reclaim_offer,
    )


@require_GET
def crm_connections(request):
    return _render_crm_connections(request)


def _connection_id_from_mutation_route(mutation: ApiMutation) -> str:
    parts = str(mutation.route or "").strip("/").split("/")
    if len(parts) >= 4 and parts[0] == "v1" and parts[1] == "crm":
        return parts[3]
    return ""


def _resource_is_connected(
    resource: dict[str, Any] | None, *, connection_id: str, provider_key: str = ""
) -> bool:
    if not isinstance(resource, dict):
        return False
    if str(resource.get("connection_id") or "") != str(connection_id):
        return False
    if str(resource.get("status") or "") != "connected":
        return False
    if provider_key and str(resource.get("provider_key") or "") != str(provider_key):
        return False
    return True


def _reconcile_reconnect_mutations(
    client: EasyImportsApiClient,
    journal: ImportSession,
    *,
    owner_session: str,
) -> None:
    """Poll start/abandon mutation status. Never invent row state from transport."""

    for mutation in journal.api_mutations.filter(
        mutation_kind__in={
            "crm_connection_reconnect",
            "crm_connection_reconnect_abandon",
        },
        state__in=[ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING],
    ).order_by("created_at", "id"):
        if _lease_is_active(mutation):
            continue
        try:
            operation_status = client.mutation_status(
                mutation.idempotency_key, owner_session=owner_session
            )
        except (
            ApiRejectedError,
            ApiUnavailableError,
            ApiConsistencyError,
            TypeError,
            KeyError,
        ):
            continue
        if not isinstance(operation_status, dict):
            continue
        if str(operation_status.get("status") or "").strip().lower() != "completed":
            continue
        try:
            client.reconcile_mutation_status(mutation, operation_status)
        except (ApiConsistencyError, TypeError, KeyError):
            continue


def _completed_reconnect_start_for_attempt(
    journal: ImportSession,
    *,
    connection_id: str,
    owner_session: str,
    attempt_id: str,
) -> ApiMutation | None:
    if not attempt_id:
        return None
    identity = f"crm-reconnect:{connection_id}:{owner_session}"
    for mutation in journal.api_mutations.filter(
        mutation_kind="crm_connection_reconnect",
        logical_action_identity=identity,
        state=ApiMutation.State.COMPLETED,
    ).order_by("-logical_action_generation", "-created_at"):
        body = mutation.response_json if isinstance(mutation.response_json, dict) else {}
        if str(body.get("reconnect_attempt_id") or "").strip() == attempt_id:
            return mutation
    return None


def _annotate_reconnect_hub_rows(
    connections: list[dict[str, Any]],
    journal: ImportSession,
    *,
    owner_session: str,
) -> None:
    open_by_id: dict[str, list[ApiMutation]] = {}
    for mutation in journal.api_mutations.filter(
        mutation_kind__in={
            "crm_connection_reconnect",
            "crm_connection_reconnect_abandon",
            "crm_connection_oauth_complete",
        },
        state__in=[ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING],
    ):
        cid = _connection_id_from_mutation_route(mutation)
        if cid:
            open_by_id.setdefault(cid, []).append(mutation)
    for row in connections:
        if not isinstance(row, dict):
            continue
        cid = str(row.get("connection_id") or "")
        status = str(row.get("status") or "")
        attempt_id = str(row.get("reconnect_attempt_id") or "").strip()
        open_muts = open_by_id.get(cid, [])
        if status == "connected":
            row["reconnect_uncertain"] = False
            row["reconnect_pending"] = False
            row["reconnect_retry_kind"] = ""
            row["reconnect_can_continue"] = False
            continue
        if status == "disconnected" and not attempt_id:
            row["reconnect_uncertain"] = False
            row["reconnect_pending"] = False
            row["reconnect_retry_kind"] = ""
            row["reconnect_can_continue"] = False
            continue
        start_unknown = any(
            item.mutation_kind == "crm_connection_reconnect"
            and item.state == ApiMutation.State.UNKNOWN
            for item in open_muts
        )
        abandon_open = any(
            item.mutation_kind == "crm_connection_reconnect_abandon"
            for item in open_muts
        )
        still_open = bool(open_muts)
        row["reconnect_uncertain"] = still_open
        row["reconnect_pending"] = status == "pending" and bool(attempt_id)
        complete_exists = journal.api_mutations.filter(
            mutation_kind="crm_connection_oauth_complete",
            logical_action_identity=_oauth_complete_identity(
                cid, owner_session, reconnect_attempt_id=attempt_id or None
            ),
        ).exists()
        saved_start = _completed_reconnect_start_for_attempt(
            journal,
            connection_id=cid,
            owner_session=owner_session,
            attempt_id=attempt_id,
        )
        row["reconnect_can_continue"] = (
            status == "pending"
            and bool(attempt_id)
            and saved_start is not None
            and not complete_exists
        )
        if start_unknown:
            row["reconnect_retry_kind"] = "start"
        elif abandon_open:
            row["reconnect_retry_kind"] = "abandon"
        else:
            row["reconnect_retry_kind"] = ""


def _complete_reconnect_result(
    request,
    *,
    started: dict[str, Any],
    owner_session: str,
    session: ImportSession,
    client: EasyImportsApiClient,
) -> tuple[str, str | None]:
    connection_id = str(started.get("connection_id") or "")
    authorization = started.get("authorization") or {}
    attempt_id = str(started.get("reconnect_attempt_id") or "").strip()
    epoch = started.get("reconnect_epoch")
    generation = int(epoch or 0)
    authorization_url = authorization.get("authorization_url")
    provider_key = str(started.get("provider_key") or "")
    if authorization_url and provider_key != "fake":
        request.session["crm_oauth_pending"] = {
            "connection_id": connection_id,
            "provider_key": provider_key,
            "state": authorization.get("state"),
            "owner_session": owner_session,
            "reconnect_attempt_id": attempt_id,
            "reconnect_epoch": generation,
        }
        return "oauth_redirect", str(authorization_url)
    complete_identity = _oauth_complete_identity(
        connection_id, owner_session, reconnect_attempt_id=attempt_id or None
    )
    code = f"django-fake-code-{uuid4().hex}"
    complete_mutation = create_or_reuse_mutation(
        session=session,
        form_instance=uuid4(),
        mutation_kind="crm_connection_oauth_complete",
        route=f"/v1/crm/connections/{connection_id}/oauth/complete",
        logical_action_identity=complete_identity,
        logical_action_generation=generation,
        request_json={
            "authorization_code_digest": authorization_code_digest(code),
            "state": authorization.get("state") or started["authorization"]["state"],
            "reconnect_attempt_id": attempt_id,
            "owner_session": owner_session,
        },
    )
    stage_ephemeral_oauth_code(complete_mutation.id, code)
    try:
        completed = client.dispatch(complete_mutation, explicit_retry=False)
    except ApiOperationInProgressError:
        return "pending", connection_id
    if completed.mutation.state == ApiMutation.State.REJECTED:
        return "error", (
            completed.mutation.error_message or "CRM reconnect was rejected."
        )
    if completed.mutation.state in {
        ApiMutation.State.PENDING,
        ApiMutation.State.UNKNOWN,
    }:
        return "pending", connection_id
    try:
        resource = client.crm_connection(connection_id, owner_session=owner_session)
    except (ApiRejectedError, ApiUnavailableError, ApiConsistencyError):
        return "pending", connection_id
    if _resource_is_connected(
        resource, connection_id=connection_id, provider_key=provider_key
    ):
        return "ok", connection_id
    return "pending", connection_id


@require_POST
def crm_reconnect(request, connection_id: str):
    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    try:
        start_generation = crm_reconnect_action_generation(
            session, connection_id=connection_id, owner_session=owner_session
        )
        start_mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_reconnect",
            route=f"/v1/crm/connections/{connection_id}/reconnect",
            logical_action_identity=f"crm-reconnect:{connection_id}:{owner_session}",
            logical_action_generation=start_generation,
            request_json={"owner_session": owner_session},
        )
        started_result = _dispatch_connection_mutation(client, start_mutation)
        if started_result.mutation.state == ApiMutation.State.REJECTED:
            messages.error(
                request,
                started_result.mutation.error_message
                or "CRM reconnect could not start.",
            )
            return redirect("importer:crm_connections")
        if started_result.mutation.state in {
            ApiMutation.State.PENDING,
            ApiMutation.State.UNKNOWN,
        }:
            messages.info(
                request,
                "CRM reconnect is still in progress. Refresh this page.",
            )
            return redirect("importer:crm_connections")
        started = started_result.response or {}
        status, detail = _complete_reconnect_result(
            request,
            started=started,
            owner_session=owner_session,
            session=session,
            client=client,
        )
        if status == "oauth_redirect" and detail:
            return redirect(detail)
        if status == "ok":
            messages.success(request, "CRM connection established.")
        elif status == "pending":
            messages.info(
                request,
                "CRM reconnect is still in progress. Refresh this page.",
            )
        else:
            messages.error(request, detail or "CRM reconnect failed.")
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        KeyError,
        TypeError,
    ) as exc:
        messages.error(request, str(exc))
    return redirect("importer:crm_connections")


@require_POST
def crm_reconnect_continue(request, connection_id: str):
    """Resume a saved completed reconnect-start. GET never continues."""

    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    attempt_id = (request.POST.get("reconnect_attempt_id") or "").strip()
    start = _completed_reconnect_start_for_attempt(
        session,
        connection_id=connection_id,
        owner_session=owner_session,
        attempt_id=attempt_id,
    )
    if start is None or not isinstance(start.response_json, dict):
        messages.error(request, "No saved reconnect attempt to continue.")
        return redirect("importer:crm_connections")
    try:
        status, detail = _complete_reconnect_result(
            request,
            started=start.response_json,
            owner_session=owner_session,
            session=session,
            client=client,
        )
        if status == "oauth_redirect" and detail:
            return redirect(detail)
        if status == "ok":
            messages.success(request, "CRM connection established.")
        elif status == "pending":
            messages.info(
                request,
                "CRM reconnect is still in progress. Refresh this page.",
            )
        else:
            messages.error(request, detail or "CRM reconnect failed.")
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        KeyError,
        TypeError,
    ) as exc:
        messages.error(request, str(exc))
    return redirect("importer:crm_connections")


@require_POST
def crm_reconnect_abandon(request, connection_id: str):
    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    attempt_id = (request.POST.get("reconnect_attempt_id") or "").strip()
    if not attempt_id:
        messages.error(request, "Reconnect attempt was missing.")
        return redirect("importer:crm_connections")
    try:
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_reconnect_abandon",
            route=f"/v1/crm/connections/{connection_id}/reconnect/abandon",
            logical_action_identity=(
                f"crm-reconnect-abandon:{connection_id}:{owner_session}:{attempt_id}"
            ),
            request_json={
                "reconnect_attempt_id": attempt_id,
                "owner_session": owner_session,
            },
        )
        result = _dispatch_connection_mutation(client, mutation)
        if result.mutation.state == ApiMutation.State.COMPLETED:
            restored = False
            try:
                resource = client.crm_connection(
                    connection_id, owner_session=owner_session
                )
            except (ApiRejectedError, ApiUnavailableError, ApiConsistencyError):
                resource = None
            if (
                isinstance(resource, dict)
                and str(resource.get("status") or "") == "disconnected"
                and not str(resource.get("reconnect_attempt_id") or "").strip()
            ):
                restored = True
            if restored:
                messages.success(request, "Reconnect abandoned.")
            else:
                messages.info(
                    request,
                    "Abandon reconnect is still being confirmed. Refresh this page.",
                )
        elif result.mutation.state in {
            ApiMutation.State.PENDING,
            ApiMutation.State.UNKNOWN,
        }:
            messages.info(
                request,
                "Abandon reconnect is still in progress. Refresh this page.",
            )
        else:
            messages.error(
                request,
                result.mutation.error_message or "Could not abandon reconnect.",
            )
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
    ) as exc:
        messages.error(request, str(exc))
    return redirect("importer:crm_connections")


@require_POST
def crm_connect(request):
    provider_key = (request.POST.get("provider_key") or "").strip()
    if not provider_key:
        messages.error(request, "Choose a CRM provider to connect.")
        return redirect("importer:crm_connections")
    status, detail = _connect_provider_result(request, provider_key=provider_key)
    if status == "oauth_redirect" and detail:
        return redirect(detail)
    if status == "pending":
        messages.info(request, _oauth_in_progress_message())
        return redirect("importer:crm_connections")
    if status == "ok":
        messages.success(request, "CRM connection established.")
        return redirect("importer:crm_connections")
    messages.error(request, detail or "CRM connection failed.")
    reclaim = getattr(request, "crm_reclaim_offer", None)
    if reclaim:
        return _render_crm_connections(request, reclaim_offer=reclaim)
    return redirect("importer:crm_connections")


# ---------------------------------------------------------------------------
# Phase 2 — Guided setup wizard shell (provider pick + practice connect)
# ---------------------------------------------------------------------------


@require_GET
def crm_setup_start(request):
    """Entry: ensure setup draft session and open provider pick."""

    session = _setup_session(request)
    draft = _setup_draft(session)
    if not draft.get("step"):
        _save_setup_draft(
            session,
            {
                "step": "pick",
                "provider_key": "",
                "provider_label": "",
                "connection_id": "",
                "outcome": "",
            },
        )
    return redirect("importer:crm_setup_pick")


@require_GET
def crm_setup_pick(request):
    session = _setup_session(request)
    owner_id = owner_id_for_request(request)
    choose_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_CHOOSE,
    )
    return render(
        request,
        "importer/crm_setup_pick.html",
        {
            "setup_session": session,
            "provider_choices": SETUP_PROVIDER_CHOICES,
            "choose_token": choose_token,
            "draft": _setup_draft(session),
        },
    )


@require_POST
def crm_setup_choose(request):
    session = _setup_session(request)
    owner_id = owner_id_for_request(request)
    token = (request.POST.get("form_token") or "").strip()
    provider_key = (request.POST.get("provider_key") or "").strip()
    try:
        validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_CHOOSE,
        )
    except FormTokenError as exc:
        messages.error(request, str(exc))
        return redirect("importer:crm_setup_pick")

    choice = _provider_choice(provider_key)
    if choice is None:
        messages.error(request, "Choose a CRM provider to continue.")
        return redirect("importer:crm_setup_pick")

    _save_setup_draft(
        session,
        {
            "step": "provider",
            "provider_key": choice["provider_key"],
            "provider_label": choice["provider_label"],
            "connection_id": "",
            "outcome": "",
        },
    )
    if provider_key == "fake":
        return redirect("importer:crm_setup_practice")
    if provider_key == "salesforce":
        return redirect("importer:crm_setup_salesforce")
    if provider_key == "hubspot":
        return redirect("importer:crm_setup_hubspot")
    if provider_key in SETUP_GUIDED_PENDING:
        return redirect("importer:crm_setup_provider", provider_key=provider_key)
    messages.error(request, "That provider is not available in setup yet.")
    return redirect("importer:crm_setup_pick")


def _practice_connect_identity(request) -> str:
    return f"crm-connect:fake:{_owner_session(request)}"


@require_GET
def crm_setup_practice(request):
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("provider_key") != "fake":
        messages.info(request, "Choose Practice CRM to continue this path.")
        return redirect("importer:crm_setup_pick")
    owner_id = owner_id_for_request(request)
    # Bind token to the journaled connect identity/generation on the connection
    # journal (D3 form-scoped exact-retry). Setup session only owns the draft UI.
    connection_journal = _connection_session(request)
    connect_identity = _practice_connect_identity(request)
    connect_generation = crm_connect_action_generation(
        connection_journal,
        provider_key="fake",
        owner_session=_owner_session(request),
    )
    connect_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_PRACTICE_CONNECT,
        logical_action_identity=connect_identity,
        logical_action_generation=connect_generation,
    )
    return render(
        request,
        "importer/crm_setup_practice.html",
        {
            "setup_session": session,
            "draft": draft,
            "connect_token": connect_token,
        },
    )


@require_POST
def crm_setup_practice_connect(request):
    """Practice path only — journaled fake connect; no secrets (Phase 2)."""

    session = _setup_session(request)
    owner_id = owner_id_for_request(request)
    token = (request.POST.get("form_token") or "").strip()
    connect_identity = _practice_connect_identity(request)
    try:
        # Decode first so generation is the signed value; validate against the
        # expected practice connect identity (D3 form-scoped exact-retry).
        decoded = decode_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_PRACTICE_CONNECT,
        )
        connect_generation = int(decoded.get("logical_action_generation") or 0)
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_PRACTICE_CONNECT,
            logical_action_identity=connect_identity,
            logical_action_generation=connect_generation,
        )
    except FormTokenError as exc:
        messages.error(request, str(exc))
        return redirect("importer:crm_setup_practice")

    form_instance = claims["form_instance"]
    if not isinstance(form_instance, UUID):
        try:
            form_instance = UUID(str(form_instance))
        except (TypeError, ValueError):
            messages.error(request, "This form is invalid or expired. Reload it.")
            return redirect("importer:crm_setup_practice")
    connect_generation = int(claims.get("logical_action_generation") or 0)

    draft = _setup_draft(session)
    if draft.get("provider_key") != "fake":
        messages.error(request, "Practice connect requires choosing Practice CRM.")
        return redirect("importer:crm_setup_pick")

    status, detail = _connect_provider_result(
        request,
        provider_key="fake",
        form_instance=form_instance,
        logical_action_generation=connect_generation,
    )
    if status == "oauth_redirect" and detail:
        # Unexpected for fake; fail closed rather than leaving the wizard.
        messages.error(
            request,
            "Practice CRM returned an unexpected authorization redirect.",
        )
        return redirect("importer:crm_setup_practice")
    if status == "pending":
        messages.info(request, _oauth_in_progress_message())
        return redirect("importer:crm_connections")
    if status != "ok":
        messages.error(request, detail or "Practice CRM connection failed.")
        return redirect("importer:crm_setup_practice")

    _save_setup_draft(
        session,
        {
            "step": "complete",
            "provider_key": "fake",
            "provider_label": "Practice CRM",
            "connection_id": detail or "",
            "outcome": "connected",
        },
    )
    messages.success(request, "Practice CRM is connected.")
    return redirect("importer:crm_setup_complete")


@require_GET
def crm_setup_provider(request, provider_key: str):
    """Legacy pending shell route; SF/HS use dedicated guided routes."""

    key = (provider_key or "").strip()
    if key == "salesforce":
        return redirect("importer:crm_setup_salesforce")
    if key == "hubspot":
        return redirect("importer:crm_setup_hubspot")
    if key not in SETUP_GUIDED_PENDING:
        raise Http404("Unknown setup provider.")
    session = _setup_session(request)
    draft = _setup_draft(session)
    choice = _provider_choice(key)
    if draft.get("provider_key") != key:
        # Allow deep-link resume into the pending shell by adopting the choice.
        if choice is None:
            raise Http404("Unknown setup provider.")
        _save_setup_draft(
            session,
            {
                "step": "provider",
                "provider_key": choice["provider_key"],
                "provider_label": choice["provider_label"],
                "connection_id": "",
                "outcome": "",
            },
        )
        draft = _setup_draft(session)
    return render(
        request,
        "importer/crm_setup_provider_pending.html",
        {
            "setup_session": session,
            "draft": draft,
            "provider_key": key,
            "provider_label": draft.get("provider_label")
            or (choice or {}).get("provider_label")
            or key,
        },
    )


def _require_setup_loopback(request):
    """Secret-bearing wizard steps stay loopback-only (same gate as Settings)."""

    if not _is_loopback_request(request):
        from django.http import HttpResponseForbidden

        return HttpResponseForbidden(
            "CRM application setup is available only from this machine (loopback)."
        )
    return None


def _ensure_salesforce_draft(session: ImportSession) -> dict[str, Any]:
    draft = _setup_draft(session)
    if draft.get("provider_key") != "salesforce":
        choice = _provider_choice("salesforce") or {
            "provider_key": "salesforce",
            "provider_label": "Salesforce",
        }
        _save_setup_draft(
            session,
            {
                "step": "credentials",
                "provider_key": "salesforce",
                "provider_label": choice.get("provider_label") or "Salesforce",
                "connection_id": "",
                "outcome": "",
            },
        )
        draft = _setup_draft(session)
    elif draft.get("step") in {"", "pick", "provider"}:
        draft = {
            **draft,
            "step": "credentials",
            "provider_key": "salesforce",
            "provider_label": draft.get("provider_label") or "Salesforce",
        }
        _save_setup_draft(session, draft)
        draft = _setup_draft(session)
    return draft


def _sf_registration_slot(
    *,
    session: ImportSession,
    form_instance: UUID,
    form_payload_digest: str,
    signed_generation: int,
) -> tuple[UUID, int]:
    """Bind Salesforce registration to the form token's signed generation.

    Same fail-closed rule as HubSpot Phase 3B rem: do not recalculate generation
    for unused forms, or a stale Manage-app tab can overwrite newer credentials.
    """

    prior = (
        session.api_mutations.filter(form_instance=form_instance)
        .order_by("-created_at")
        .first()
    )
    if prior is not None:
        if int(prior.logical_action_generation) != int(signed_generation):
            raise FormTokenError("This form is stale. Reload it before continuing.")
        return form_instance, int(prior.logical_action_generation)
    expected = _registration_generation(
        session,
        identity=SETUP_SF_REG_IDENTITY,
        form_payload_digest=form_payload_digest,
    )
    if int(signed_generation) != int(expected):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    return form_instance, int(signed_generation)


@require_GET
def crm_setup_salesforce(request):
    """Phase 3A: Salesforce env + Connected App checklist + credentials."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _ensure_salesforce_draft(session)
    edit_mode = (request.GET.get("edit") or "").strip() in {"1", "true", "yes"}
    # Manage app (?edit=1) must work after connected/complete — check edit first.
    if not edit_mode and (
        draft.get("outcome") == "connected" or draft.get("step") == "complete"
    ):
        return redirect("importer:crm_setup_complete")
    registration_ready = (
        draft.get("step") == "registration_ready"
        or draft.get("outcome") == "registration_ready"
    )
    # Phase 4: Manage app from Connect hub uses ?edit=1 (same as HubSpot).
    if registration_ready and not edit_mode:
        return redirect("importer:crm_setup_salesforce_connect")

    owner_id = owner_id_for_request(request)
    save_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_SF_SAVE,
        logical_action_identity=SETUP_SF_REG_IDENTITY,
        logical_action_generation=_registration_generation(
            session,
            identity=SETUP_SF_REG_IDENTITY,
            form_payload_digest="",
        ),
    )
    stuck_sf = [
        item
        for item in list_stuck_unknown_registration_puts(owner_id=owner_id)
        if item.provider_key == "salesforce"
    ]
    settings_session = _settings_session(request)
    start_over_token = None
    if stuck_sf:
        start_over_token = _issue_reg_form_token(
            request,
            session=settings_session,
            action_kind=_REG_ACTION_START_OVER,
            action_id="salesforce",
        )
    api_technical = None
    session_store = getattr(request, "session", None)
    if session_store is not None:
        api_technical = session_store.pop("crm_setup_api_technical", None)
    return render(
        request,
        "importer/crm_setup_salesforce.html",
        {
            "setup_session": session,
            "draft": draft,
            "save_token": save_token,
            "callback_uri": resolve_salesforce_oauth_redirect_uri(),
            "edit_mode": edit_mode,
            "login_environments": (
                ("production", "Production (login.salesforce.com)"),
                ("sandbox", "Sandbox (test.salesforce.com)"),
            ),
            "stuck_registration_put": stuck_sf[0] if stuck_sf else None,
            "start_over_form_token": start_over_token,
            "api_technical": api_technical,
        },
    )


@require_POST
def crm_setup_salesforce_save(request):
    """Journaled Salesforce app registration from the Connect wizard (0A-J)."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _ensure_salesforce_draft(session)
    owner_id = owner_id_for_request(request)
    token = (request.POST.get("form_token") or "").strip()
    client_secret = request.POST.get("client_secret") or ""
    label = (request.POST.get("label") or "").strip()
    login_environment = (request.POST.get("login_environment") or "").strip()
    client_id = (request.POST.get("client_id") or "").strip()
    my_domain_host = (request.POST.get("my_domain_host") or "").strip()
    expected_org_id = (request.POST.get("expected_org_id") or "").strip()

    try:
        decoded = decode_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_SF_SAVE,
        )
        generation_claim = int(decoded.get("logical_action_generation") or 0)
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_SF_SAVE,
            logical_action_identity=SETUP_SF_REG_IDENTITY,
            logical_action_generation=generation_claim,
        )
        form_instance = claims["form_instance"]
        if not isinstance(form_instance, UUID):
            form_instance = UUID(str(form_instance))
    except (FormTokenError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) if str(exc) else "This form is invalid.")
        return redirect("importer:crm_setup_salesforce")

    if login_environment not in {"production", "sandbox"}:
        messages.error(request, "Choose production or sandbox.")
        return redirect("importer:crm_setup_salesforce")
    if not label or not client_id:
        messages.error(request, "Label and client ID are required.")
        return redirect("importer:crm_setup_salesforce")

    journal_body = {
        "label": label,
        "login_environment": login_environment,
        "client_id": client_id,
        "redirect_uri": resolve_salesforce_oauth_redirect_uri(),
        "my_domain_host": my_domain_host or None,
        "expected_org_id": expected_org_id or None,
        "client_secret_digest": registration_secret_digest(client_secret),
    }
    form_digest = _registration_form_digest(journal_body)
    client = EasyImportsApiClient()
    try:
        _require_registration_secret(client_secret=client_secret)
        form_instance, generation = _sf_registration_slot(
            session=session,
            form_instance=form_instance,
            form_payload_digest=form_digest,
            signed_generation=generation_claim,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=SETUP_SF_REG_IDENTITY,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="salesforce",
        )
        stage_ephemeral_registration_secrets(mutation.id, client_secret=client_secret)
        _dispatch_registration(client, mutation)
    except (
        RegistrationSecretError,
        ApiUnavailableError,
        ApiRejectedError,
        ApiConsistencyError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        FormTokenError,
    ) as exc:
        messages.error(request, str(exc))
        if isinstance(
            exc,
            (
                ApiUnavailableError,
                ApiRejectedError,
                MutationReuseError,
                MutationExplicitRetryRequired,
            ),
        ):
            session_store = getattr(request, "session", None)
            if session_store is not None:
                session_store["crm_setup_api_technical"] = api_error_technical_details(
                    exc
                )
        return redirect("importer:crm_setup_salesforce")

    _save_setup_draft(
        session,
        {
            **draft,
            "step": "registration_ready",
            "provider_key": "salesforce",
            "provider_label": "Salesforce",
            "outcome": "registration_ready",
            "label": label,
            "login_environment": login_environment,
            "client_id": client_id,
            "my_domain_host": my_domain_host,
            "expected_org_id": expected_org_id,
            "connection_id": "",
        },
    )
    messages.success(
        request,
        "Salesforce Connected App saved. Next: connect your org.",
    )
    return redirect("importer:crm_setup_salesforce_connect")


@require_GET
def crm_setup_salesforce_connect(request):
    """After registration: connect org via journaled OAuth start."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("provider_key") != "salesforce":
        messages.info(request, "Choose Salesforce to continue this path.")
        return redirect("importer:crm_setup_pick")
    if draft.get("step") not in {"registration_ready", "connect"} and draft.get(
        "outcome"
    ) not in {"registration_ready", "connected"}:
        messages.info(request, "Save Connected App credentials before connecting.")
        return redirect("importer:crm_setup_salesforce")
    if draft.get("outcome") == "connected" or draft.get("connection_id"):
        return redirect("importer:crm_setup_complete")

    owner_id = owner_id_for_request(request)
    connection_journal = _connection_session(request)
    owner_session = _owner_session(request)
    connect_identity = f"crm-connect:salesforce:{owner_session}"
    connect_generation = crm_connect_action_generation(
        connection_journal,
        provider_key="salesforce",
        owner_session=owner_session,
    )
    connect_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_SF_CONNECT,
        logical_action_identity=connect_identity,
        logical_action_generation=connect_generation,
    )
    return render(
        request,
        "importer/crm_setup_salesforce_connect.html",
        {
            "setup_session": session,
            "draft": draft,
            "connect_token": connect_token,
            "callback_uri": resolve_salesforce_oauth_redirect_uri(),
        },
    )


@require_POST
def crm_setup_salesforce_connect_submit(request):
    """Form-scoped journaled Salesforce org connect (OAuth start/complete)."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("provider_key") != "salesforce":
        messages.error(request, "Salesforce connect requires choosing Salesforce.")
        return redirect("importer:crm_setup_pick")
    if (
        draft.get("step") not in {"registration_ready", "connect"}
        and draft.get("outcome") != "registration_ready"
    ):
        messages.error(request, "Save Connected App credentials before connecting.")
        return redirect("importer:crm_setup_salesforce")

    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    connect_identity = f"crm-connect:salesforce:{owner_session}"
    token = (request.POST.get("form_token") or "").strip()
    try:
        decoded = decode_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_SF_CONNECT,
        )
        connect_generation = int(decoded.get("logical_action_generation") or 0)
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_SF_CONNECT,
            logical_action_identity=connect_identity,
            logical_action_generation=connect_generation,
        )
        form_instance = claims["form_instance"]
        if not isinstance(form_instance, UUID):
            form_instance = UUID(str(form_instance))
        connect_generation = int(claims.get("logical_action_generation") or 0)
    except (FormTokenError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) if str(exc) else "This form is invalid.")
        return redirect("importer:crm_setup_salesforce_connect")

    status, detail = _connect_provider_result(
        request,
        provider_key="salesforce",
        form_instance=form_instance,
        logical_action_generation=connect_generation,
    )
    if status == "oauth_redirect" and detail:
        # Live OAuth: browser leaves for Salesforce authorize URL; return to
        # wizard complete when the callback succeeds.
        _save_setup_draft(
            session,
            {
                **draft,
                "step": "connect",
                "outcome": "registration_ready",
            },
        )
        request.session["crm_setup_oauth_return"] = "salesforce"
        return redirect(detail)
    if status == "pending":
        messages.info(request, _oauth_in_progress_message())
        return redirect("importer:crm_connections")
    if status != "ok":
        messages.error(request, detail or "Salesforce connection failed.")
        return redirect("importer:crm_setup_salesforce_connect")

    _save_setup_draft(
        session,
        {
            **draft,
            "step": "complete",
            "provider_key": "salesforce",
            "provider_label": "Salesforce",
            "connection_id": detail or "",
            "outcome": "connected",
        },
    )
    messages.success(request, "Salesforce org is connected.")
    return redirect("importer:crm_setup_complete")


@require_GET
def crm_setup_complete(request):
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("step") != "complete" and not draft.get("connection_id"):
        if draft.get("outcome") == "registration_ready":
            messages.info(request, "Registration is saved. Connect your org to finish.")
            if draft.get("provider_key") == "salesforce":
                return redirect("importer:crm_setup_salesforce_connect")
            if draft.get("provider_key") == "hubspot":
                return redirect("importer:crm_setup_hubspot_connect")
        messages.info(request, "Finish connecting a CRM before viewing success.")
        return redirect("importer:crm_setup_pick")
    return render(
        request,
        "importer/crm_setup_complete.html",
        {
            "setup_session": session,
            "draft": draft,
        },
    )


def _ensure_hubspot_draft(session: ImportSession) -> dict[str, Any]:
    draft = _setup_draft(session)
    if draft.get("provider_key") != "hubspot":
        choice = _provider_choice("hubspot") or {
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
        }
        _save_setup_draft(
            session,
            {
                "step": "credentials",
                "provider_key": "hubspot",
                "provider_label": choice.get("provider_label") or "HubSpot",
                "connection_id": "",
                "outcome": "",
            },
        )
        draft = _setup_draft(session)
    elif draft.get("step") in {"", "pick", "provider"}:
        draft = {
            **draft,
            "step": "credentials",
            "provider_key": "hubspot",
            "provider_label": draft.get("provider_label") or "HubSpot",
        }
        _save_setup_draft(session, draft)
        draft = _setup_draft(session)
    return draft


def _hs_registration_slot(
    *,
    session: ImportSession,
    form_instance: UUID,
    form_payload_digest: str,
    signed_generation: int,
) -> tuple[UUID, int]:
    """Bind HubSpot registration to the form token's signed generation.

    Recalculating generation for unused forms would let a stale second tab
    overwrite a newer registration (open two forms at gen 0 → first completes
    → second would advance to gen 1). New submits must match the open journal
    generation; completed form instances exact-replay their prior generation.
    """

    prior = (
        session.api_mutations.filter(form_instance=form_instance)
        .order_by("-created_at")
        .first()
    )
    if prior is not None:
        if int(prior.logical_action_generation) != int(signed_generation):
            raise FormTokenError("This form is stale. Reload it before continuing.")
        return form_instance, int(prior.logical_action_generation)
    expected = _registration_generation(
        session,
        identity=SETUP_HS_REG_IDENTITY,
        form_payload_digest=form_payload_digest,
    )
    if int(signed_generation) != int(expected):
        raise FormTokenError("This form is stale. Reload it before continuing.")
    return form_instance, int(signed_generation)


@require_GET
def crm_setup_hubspot(request):
    """Phase 3B: HubSpot private-app primary + OAuth alternate credentials."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _ensure_hubspot_draft(session)
    edit_mode = (request.GET.get("edit") or "").strip() in {"1", "true", "yes"}
    # Manage app (?edit=1) must work after connected/complete — check edit first.
    if not edit_mode and (
        draft.get("outcome") == "connected" or draft.get("step") == "complete"
    ):
        return redirect("importer:crm_setup_complete")
    registration_ready = (
        draft.get("step") == "registration_ready"
        or draft.get("outcome") == "registration_ready"
    )
    # After save, continue to connect — unless the operator asked to edit
    # credentials (connect page "Edit application" link).
    if registration_ready and not edit_mode:
        return redirect("importer:crm_setup_hubspot_connect")

    owner_id = owner_id_for_request(request)
    save_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_HS_SAVE,
        logical_action_identity=SETUP_HS_REG_IDENTITY,
        logical_action_generation=_registration_generation(
            session,
            identity=SETUP_HS_REG_IDENTITY,
            form_payload_digest="",
        ),
    )
    stuck_hs = [
        item
        for item in list_stuck_unknown_registration_puts(owner_id=owner_id)
        if item.provider_key == "hubspot"
    ]
    settings_session = _settings_session(request)
    start_over_token = None
    if stuck_hs:
        start_over_token = _issue_reg_form_token(
            request,
            session=settings_session,
            action_kind=_REG_ACTION_START_OVER,
            action_id="hubspot",
        )
    api_technical = None
    session_store = getattr(request, "session", None)
    if session_store is not None:
        api_technical = session_store.pop("crm_setup_api_technical", None)
    return render(
        request,
        "importer/crm_setup_hubspot.html",
        {
            "setup_session": session,
            "draft": draft,
            "save_token": save_token,
            "callback_uri": resolve_hubspot_oauth_redirect_uri(),
            "edit_mode": edit_mode,
            "auth_modes": (
                ("private_app", "Private app (recommended)"),
                ("oauth", "OAuth app"),
            ),
            "stuck_registration_put": stuck_hs[0] if stuck_hs else None,
            "start_over_form_token": start_over_token,
            "api_technical": api_technical,
        },
    )


@require_POST
def crm_setup_hubspot_save(request):
    """Journaled HubSpot app registration from the Connect wizard (0A-J)."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _ensure_hubspot_draft(session)
    owner_id = owner_id_for_request(request)
    token = (request.POST.get("form_token") or "").strip()
    label = (request.POST.get("label") or "").strip()
    auth_mode = (request.POST.get("auth_mode") or "").strip()
    expected_hub_id = (request.POST.get("expected_hub_id") or "").strip()
    access_token = request.POST.get("access_token") or ""
    client_id = (request.POST.get("client_id") or "").strip()
    client_secret = request.POST.get("client_secret") or ""

    try:
        decoded = decode_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_HS_SAVE,
        )
        generation_claim = int(decoded.get("logical_action_generation") or 0)
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_HS_SAVE,
            logical_action_identity=SETUP_HS_REG_IDENTITY,
            logical_action_generation=generation_claim,
        )
        form_instance = claims["form_instance"]
        if not isinstance(form_instance, UUID):
            form_instance = UUID(str(form_instance))
    except (FormTokenError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) if str(exc) else "This form is invalid.")
        return redirect("importer:crm_setup_hubspot")

    if auth_mode not in {"private_app", "oauth"}:
        messages.error(request, "Choose private app or OAuth app.")
        return redirect("importer:crm_setup_hubspot")
    if not label:
        messages.error(request, "Label is required.")
        return redirect("importer:crm_setup_hubspot")

    journal_body: dict[str, Any] = {
        "label": label,
        "auth_mode": auth_mode,
        "expected_hub_id": expected_hub_id or None,
        "client_secret_digest": None,
        "access_token_digest": None,
    }
    if auth_mode == "private_app":
        if not expected_hub_id:
            messages.error(
                request, "Expected hub / portal id is required for private app."
            )
            return redirect("importer:crm_setup_hubspot")
        journal_body["client_id"] = None
        journal_body["redirect_uri"] = None
        journal_body["access_token_digest"] = registration_secret_digest(access_token)
    else:
        if not client_id:
            messages.error(request, "Client ID is required for OAuth app.")
            return redirect("importer:crm_setup_hubspot")
        journal_body["client_id"] = client_id
        journal_body["client_secret_digest"] = registration_secret_digest(client_secret)
        journal_body["redirect_uri"] = resolve_hubspot_oauth_redirect_uri()

    form_digest = _registration_form_digest(journal_body)
    client = EasyImportsApiClient()
    try:
        if auth_mode == "private_app":
            _require_registration_secret(access_token=access_token)
        else:
            _require_registration_secret(client_secret=client_secret)
        form_instance, generation = _hs_registration_slot(
            session=session,
            form_instance=form_instance,
            form_payload_digest=form_digest,
            signed_generation=generation_claim,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_hubspot_put",
            route="/v1/settings/crm-app-registrations/hubspot",
            logical_action_identity=SETUP_HS_REG_IDENTITY,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="hubspot",
        )
        if auth_mode == "private_app":
            stage_ephemeral_registration_secrets(mutation.id, access_token=access_token)
        else:
            stage_ephemeral_registration_secrets(
                mutation.id, client_secret=client_secret
            )
        _dispatch_registration(client, mutation)
    except (
        RegistrationSecretError,
        ApiUnavailableError,
        ApiRejectedError,
        ApiConsistencyError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        FormTokenError,
    ) as exc:
        messages.error(request, str(exc))
        if isinstance(
            exc,
            (
                ApiUnavailableError,
                ApiRejectedError,
                MutationReuseError,
                MutationExplicitRetryRequired,
            ),
        ):
            session_store = getattr(request, "session", None)
            if session_store is not None:
                session_store["crm_setup_api_technical"] = api_error_technical_details(
                    exc
                )
        return redirect("importer:crm_setup_hubspot")

    _save_setup_draft(
        session,
        {
            **draft,
            "step": "registration_ready",
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
            "outcome": "registration_ready",
            "label": label,
            "auth_mode": auth_mode,
            "expected_hub_id": expected_hub_id,
            "client_id": client_id if auth_mode == "oauth" else "",
            "connection_id": "",
        },
    )
    messages.success(
        request,
        "HubSpot application saved. Next: connect your portal.",
    )
    return redirect("importer:crm_setup_hubspot_connect")


def _render_hubspot_connect(request, *, reclaim_offer: dict[str, Any] | None = None):
    """Render the HubSpot guided-connect step, optionally with a reclaim form."""

    session = _setup_session(request)
    draft = _setup_draft(session)
    owner_id = owner_id_for_request(request)
    connection_journal = _connection_session(request)
    owner_session = _owner_session(request)
    connect_identity = f"crm-connect:hubspot:{owner_session}"
    connect_generation = crm_connect_action_generation(
        connection_journal,
        provider_key="hubspot",
        owner_session=owner_session,
    )
    connect_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind=SETUP_ACTION_HS_CONNECT,
        logical_action_identity=connect_identity,
        logical_action_generation=connect_generation,
    )
    return _apply_reclaim_no_store(
        render(
            request,
            "importer/crm_setup_hubspot_connect.html",
            {
                "setup_session": session,
                "draft": draft,
                "connect_token": connect_token,
                "callback_uri": resolve_hubspot_oauth_redirect_uri(),
                "reclaim_offer": reclaim_offer,
            },
        ),
        reclaim_offer,
    )


@require_GET
def crm_setup_hubspot_connect(request):
    """After registration: connect portal via journaled connect start."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("provider_key") != "hubspot":
        messages.info(request, "Choose HubSpot to continue this path.")
        return redirect("importer:crm_setup_pick")
    if draft.get("step") not in {"registration_ready", "connect"} and draft.get(
        "outcome"
    ) not in {"registration_ready", "connected"}:
        messages.info(
            request, "Save HubSpot application credentials before connecting."
        )
        return redirect("importer:crm_setup_hubspot")
    if draft.get("outcome") == "connected" or draft.get("connection_id"):
        return redirect("importer:crm_setup_complete")
    return _render_hubspot_connect(request)


@require_POST
def crm_setup_hubspot_connect_submit(request):
    """Form-scoped journaled HubSpot portal connect (private-app or OAuth)."""

    denied = _require_setup_loopback(request)
    if denied is not None:
        return denied
    session = _setup_session(request)
    draft = _setup_draft(session)
    if draft.get("provider_key") != "hubspot":
        messages.error(request, "HubSpot connect requires choosing HubSpot.")
        return redirect("importer:crm_setup_pick")
    if (
        draft.get("step") not in {"registration_ready", "connect"}
        and draft.get("outcome") != "registration_ready"
    ):
        messages.error(
            request, "Save HubSpot application credentials before connecting."
        )
        return redirect("importer:crm_setup_hubspot")

    owner_id = owner_id_for_request(request)
    owner_session = _owner_session(request)
    connect_identity = f"crm-connect:hubspot:{owner_session}"
    token = (request.POST.get("form_token") or "").strip()
    try:
        decoded = decode_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_HS_CONNECT,
        )
        connect_generation = int(decoded.get("logical_action_generation") or 0)
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind=SETUP_ACTION_HS_CONNECT,
            logical_action_identity=connect_identity,
            logical_action_generation=connect_generation,
        )
        form_instance = claims["form_instance"]
        if not isinstance(form_instance, UUID):
            form_instance = UUID(str(form_instance))
        connect_generation = int(claims.get("logical_action_generation") or 0)
    except (FormTokenError, TypeError, ValueError) as exc:
        messages.error(request, str(exc) if str(exc) else "This form is invalid.")
        return redirect("importer:crm_setup_hubspot_connect")

    status, detail = _connect_provider_result(
        request,
        provider_key="hubspot",
        form_instance=form_instance,
        logical_action_generation=connect_generation,
    )
    if status == "oauth_redirect" and detail:
        _save_setup_draft(
            session,
            {
                **draft,
                "step": "connect",
                "outcome": "registration_ready",
            },
        )
        request.session["crm_setup_oauth_return"] = "hubspot"
        return redirect(detail)
    if status == "pending":
        messages.info(request, _oauth_in_progress_message())
        return redirect("importer:crm_connections")
    if status != "ok":
        messages.error(request, detail or "HubSpot connection failed.")
        reclaim = getattr(request, "crm_reclaim_offer", None)
        if reclaim:
            return _render_hubspot_connect(request, reclaim_offer=reclaim)
        return redirect("importer:crm_setup_hubspot_connect")

    _save_setup_draft(
        session,
        {
            **draft,
            "step": "complete",
            "provider_key": "hubspot",
            "provider_label": "HubSpot",
            "connection_id": detail or "",
            "outcome": "connected",
        },
    )
    messages.success(request, "HubSpot portal is connected.")
    return redirect("importer:crm_setup_complete")


def _oauth_success_redirect(request, *, connection_id: str = ""):
    """After successful OAuth, resume the Connect setup wizard when applicable."""

    oauth_return = request.session.pop("crm_setup_oauth_return", None)
    if oauth_return == "salesforce":
        setup = _setup_session(request)
        draft = _setup_draft(setup)
        _save_setup_draft(
            setup,
            {
                **draft,
                "step": "complete",
                "provider_key": "salesforce",
                "provider_label": "Salesforce",
                "connection_id": connection_id or str(draft.get("connection_id") or ""),
                "outcome": "connected",
            },
        )
        return redirect("importer:crm_setup_complete")
    if oauth_return == "hubspot":
        setup = _setup_session(request)
        draft = _setup_draft(setup)
        _save_setup_draft(
            setup,
            {
                **draft,
                "step": "complete",
                "provider_key": "hubspot",
                "provider_label": "HubSpot",
                "connection_id": connection_id or str(draft.get("connection_id") or ""),
                "outcome": "connected",
            },
        )
        return redirect("importer:crm_setup_complete")
    return redirect("importer:crm_connections")


@require_GET
def crm_oauth_callback(request):
    """Complete live CRM OAuth after the provider redirects back with a code.

    The authorization code is accepted only for this request and POSTed to the
    API; it is not rendered back to the browser and is not durable. Prefer
    respond-async hands the code to the API worker before the HTTP 202; a
    later refresh reconciles via mutation status / connection GET and never
    replays the raw code.
    """

    code = (request.GET.get("code") or "").strip()
    state = (request.GET.get("state") or "").strip()
    pending = request.session.get("crm_oauth_pending") or {}
    if not code or not state:
        messages.error(request, "CRM authorization callback was incomplete.")
        return redirect("importer:crm_connections")
    connection_id = str(pending.get("connection_id") or "").strip()
    owner_session = str(pending.get("owner_session") or _owner_session(request)).strip()
    expected_state = str(pending.get("state") or "").strip()
    if not connection_id:
        messages.error(request, "CRM authorization session was missing or expired.")
        return redirect("importer:crm_connections")
    if expected_state and expected_state != state:
        messages.error(request, "CRM authorization state did not match.")
        return redirect("importer:crm_connections")
    pending_attempt = str(pending.get("reconnect_attempt_id") or "").strip()
    pending_epoch = pending.get("reconnect_epoch")

    session = _connection_session(request)
    client = EasyImportsApiClient()
    complete_identity = _oauth_complete_identity(
        connection_id, owner_session, reconnect_attempt_id=pending_attempt or None
    )
    complete_mutation = None
    try:
        request_json = {
            "authorization_code_digest": authorization_code_digest(code),
            "state": state,
            "owner_session": owner_session,
        }
        if pending_attempt:
            request_json["reconnect_attempt_id"] = pending_attempt
        # Durable journal stores only a digest — never the raw OAuth code.
        complete_mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_oauth_complete",
            route=f"/v1/crm/connections/{connection_id}/oauth/complete",
            logical_action_identity=complete_identity,
            logical_action_generation=int(pending_epoch or 0),
            request_json=request_json,
        )
        complete_mutation.refresh_from_db()

        # Already finished successfully (e.g. prior callback won the race).
        if complete_mutation.state == ApiMutation.State.COMPLETED:
            request.session.pop("crm_oauth_pending", None)
            messages.success(request, "CRM connection established.")
            return _oauth_success_redirect(
                request,
                connection_id=_completed_connection_id(
                    complete_mutation, fallback=connection_id
                ),
            )

        # Prior UNKNOWN, or PENDING after a 202 / active lease: reconcile
        # without replaying the raw code. A freshly created PENDING row still
        # needs its first dispatch.
        already_accepted = complete_mutation.operation_in_progress or _lease_is_active(
            complete_mutation
        )
        if complete_mutation.state == ApiMutation.State.UNKNOWN or already_accepted:
            settled = _settle_oauth_complete_from_connection(
                client,
                complete_mutation,
                connection_id=connection_id,
                owner_session=owner_session,
            )
            if settled.outcome == "completed":
                request.session.pop("crm_oauth_pending", None)
                messages.success(request, "CRM connection established.")
                return _oauth_success_redirect(
                    request,
                    connection_id=_completed_connection_id(
                        complete_mutation, fallback=connection_id
                    ),
                )
            if settled.outcome == "failed":
                request.session.pop("crm_oauth_pending", None)
                return _rejected_oauth_complete_response(
                    request,
                    settled,
                    message=(
                        "Prior CRM authorization failed. Start a new CRM connection."
                    ),
                )
            # Still unknown / in flight: keep session so the operator can wait
            # or refresh; do not clear pending and do not restage the code.
            messages.info(request, _oauth_in_progress_message())
            return redirect("importer:crm_connections")

        if complete_mutation.state == ApiMutation.State.REJECTED:
            request.session.pop("crm_oauth_pending", None)
            messages.error(
                request,
                "CRM authorization was rejected. Start a new CRM connection.",
            )
            return redirect("importer:crm_connections")

        stage_ephemeral_oauth_code(complete_mutation.id, code)
        # Do not use the UNKNOWN explicit-retry helper: the code is single-use.
        try:
            completed = client.dispatch(complete_mutation, explicit_retry=False)
        except ApiOperationInProgressError:
            settled = _settle_oauth_complete_from_connection(
                client,
                complete_mutation,
                connection_id=connection_id,
                owner_session=owner_session,
            )
            if settled.outcome == "completed":
                request.session.pop("crm_oauth_pending", None)
                messages.success(request, "CRM connection established.")
                return _oauth_success_redirect(
                    request,
                    connection_id=_completed_connection_id(
                        complete_mutation, fallback=connection_id
                    ),
                )
            if settled.outcome == "failed":
                request.session.pop("crm_oauth_pending", None)
                return _rejected_oauth_complete_response(
                    request,
                    settled,
                    message=(
                        "CRM authorization was rejected. Start a new CRM connection."
                    ),
                )
            messages.info(request, _oauth_in_progress_message())
            return redirect("importer:crm_connections")
        request.session.pop("crm_oauth_pending", None)
        if completed.mutation.state == ApiMutation.State.REJECTED:
            reclaim = _reclaim_offer_from_response(completed.response)
            messages.error(
                request,
                completed.mutation.error_message or "CRM authorization was rejected.",
            )
            if reclaim:
                return _render_crm_connections(request, reclaim_offer=reclaim)
            return redirect("importer:crm_connections")
        messages.success(request, "CRM connection established.")
        return _oauth_success_redirect(
            request,
            connection_id=str(
                (completed.response or {}).get("connection_id") or connection_id
            ),
        )
    except MutationBusyError:
        # Another callback owns the lease — never rewrite the mutation.
        # Keep crm_oauth_pending so a refresh can reconcile.
        messages.warning(
            request,
            "CRM authorization is already in progress. Wait a moment and refresh.",
        )
    except MutationReuseError:
        # Digest mismatch with a prior complete attempt: try to reconcile
        # the existing mutation if present, else unlock reconnect.
        existing = (
            session.api_mutations.filter(logical_action_identity=complete_identity)
            .order_by("-created_at")
            .first()
        )
        if existing is not None:
            settled = _settle_oauth_complete_from_connection(
                client,
                existing,
                connection_id=connection_id,
                owner_session=owner_session,
            )
            if settled.outcome == "completed":
                request.session.pop("crm_oauth_pending", None)
                messages.success(request, "CRM connection established.")
                return _oauth_success_redirect(
                    request,
                    connection_id=_completed_connection_id(
                        existing, fallback=connection_id
                    ),
                )
            if settled.outcome == "failed":
                request.session.pop("crm_oauth_pending", None)
                return _rejected_oauth_complete_response(
                    request,
                    settled,
                    message=(
                        "CRM authorization could not be resumed. "
                        "Start a new CRM connection."
                    ),
                )
            messages.warning(
                request,
                "CRM authorization is still uncertain. Wait and refresh.",
            )
            return redirect("importer:crm_connections")
        request.session.pop("crm_oauth_pending", None)
        messages.error(
            request,
            "CRM authorization could not be resumed. Start a new CRM connection.",
        )
    except ApiConsistencyError as exc:
        # Upstream may have succeeded after a lease race. Reconcile connection.
        if complete_mutation is not None:
            settled = _settle_oauth_complete_from_connection(
                client,
                complete_mutation,
                connection_id=connection_id,
                owner_session=owner_session,
            )
            if settled.outcome == "completed":
                request.session.pop("crm_oauth_pending", None)
                messages.success(request, "CRM connection established.")
                return _oauth_success_redirect(
                    request,
                    connection_id=_completed_connection_id(
                        complete_mutation, fallback=connection_id
                    ),
                )
            if settled.outcome == "failed":
                request.session.pop("crm_oauth_pending", None)
                return _rejected_oauth_complete_response(
                    request,
                    settled,
                    message=(
                        "CRM authorization could not be confirmed. "
                        "Start a new CRM connection."
                    ),
                )
        messages.warning(
            request,
            f"{exc} Wait a moment and refresh the connections page.",
        )
    except ApiRejectedError as exc:
        request.session.pop("crm_oauth_pending", None)
        if complete_mutation is not None:
            complete_mutation.refresh_from_db()
            # Definitive rejection from the API is already terminal via dispatch
            # when the client persisted REJECTED; otherwise leave as-is.
            if complete_mutation.state == ApiMutation.State.UNKNOWN:
                _reject_oauth_complete_for_reconnect(complete_mutation)
        messages.error(
            request,
            f"{exc} Start a new CRM connection if the problem persists.",
        )
    except (ApiUnavailableError, MutationExplicitRetryRequired) as exc:
        # Lost response: reconcile before rejecting. Code may already be used.
        if complete_mutation is not None:
            complete_mutation.refresh_from_db()
            if complete_mutation.state == ApiMutation.State.UNKNOWN:
                settled = _settle_oauth_complete_from_connection(
                    client,
                    complete_mutation,
                    connection_id=connection_id,
                    owner_session=owner_session,
                )
                if settled.outcome == "completed":
                    request.session.pop("crm_oauth_pending", None)
                    messages.success(request, "CRM connection established.")
                    return _oauth_success_redirect(
                        request,
                        connection_id=_completed_connection_id(
                            complete_mutation, fallback=connection_id
                        ),
                    )
                if settled.outcome == "failed":
                    request.session.pop("crm_oauth_pending", None)
                    return _rejected_oauth_complete_response(
                        request,
                        settled,
                        message=(
                            "CRM authorization failed. Start a new CRM connection."
                        ),
                    )
                # Remain unknown: keep session pending for refresh reconcile.
                messages.warning(
                    request,
                    f"{exc} Authorization is uncertain; refresh shortly before "
                    "starting a new connection.",
                )
                return redirect("importer:crm_connections")
        messages.error(
            request,
            f"{exc} Start a new CRM connection if the problem persists.",
        )
    except (KeyError, TypeError) as exc:
        request.session.pop("crm_oauth_pending", None)
        messages.error(request, str(exc))
    finally:
        if complete_mutation is not None:
            clear_ephemeral_oauth_code(complete_mutation.id)
    return redirect("importer:crm_connections")


@require_POST
def crm_reclaim(request, connection_id: str):
    """Explicit reclaim of a connected CRM row onto this browser session."""

    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    handle = str(request.POST.get("reclaim_handle") or "").strip()
    offer_id = str(request.POST.get("reclaim_offer_id") or "").strip()
    explicit_retry = str(request.POST.get("explicit_retry") or "") == "1"
    if not handle or not offer_id:
        messages.error(
            request,
            "This reclaim form is incomplete. Connect again to mint a new offer.",
        )
        return redirect("importer:crm_connections")
    mutation = None
    try:
        identity = f"crm-reclaim:{connection_id}:{owner_session}:{offer_id}"
        existing = session.api_mutations.filter(
            logical_action_identity=identity
        ).first()
        if existing is not None:
            mutation = existing
        else:
            mutation = create_or_reuse_mutation(
                session=session,
                form_instance=uuid4(),
                mutation_kind="crm_connection_reclaim",
                route=f"/v1/crm/connections/{connection_id}/reclaim",
                logical_action_identity=identity,
                resource_identity=connection_id,
                request_json={
                    "reclaim_handle_digest": reclaim_handle_digest(handle),
                    "reclaim_offer_id": offer_id,
                    "owner_session": owner_session,
                },
            )
        stage_ephemeral_reclaim_handle(mutation.id, handle)
        result = client.dispatch(
            mutation,
            explicit_retry=explicit_retry
            or mutation.state == ApiMutation.State.UNKNOWN,
        )
        if result.mutation.state == ApiMutation.State.REJECTED:
            messages.error(
                request,
                result.mutation.error_message or "CRM reclaim was rejected.",
            )
            return redirect("importer:crm_connections")
        if result.mutation.state != ApiMutation.State.COMPLETED:
            messages.warning(
                request,
                "CRM reclaim outcome is uncertain. Retry this exact action.",
            )
            return _render_crm_connections(
                request,
                reclaim_offer={
                    "reclaim_handle": handle,
                    "reclaim_offer_id": offer_id,
                    "existing_connection_id": connection_id,
                    "unknown_retry": True,
                },
            )
        messages.success(request, "CRM connection reclaimed on this browser.")
        last = (result.response or {}).get("last_reclaim") or {}
        when = str(last.get("reclaimed_at") or "").strip()
        mutation_id = str(last.get("reclaim_mutation_id") or "").strip()
        if when or mutation_id:
            messages.info(
                request,
                (
                    f"Reclaimed from another browser session at {when}."
                    if when
                    else "Reclaimed from another browser session."
                )
                + (f" Mutation {mutation_id}." if mutation_id else ""),
            )
    except (MutationExplicitRetryRequired, ApiUnavailableError) as exc:
        if mutation is not None:
            mutation.refresh_from_db()
        messages.warning(
            request,
            f"{exc} Retry this exact reclaim action.",
        )
        return _render_crm_connections(
            request,
            reclaim_offer={
                "reclaim_handle": handle,
                "reclaim_offer_id": offer_id,
                "existing_connection_id": connection_id,
                "unknown_retry": True,
            },
        )
    except (
        ApiRejectedError,
        MutationBusyError,
        MutationReuseError,
        ApiConsistencyError,
    ) as exc:
        messages.error(request, str(exc))
    finally:
        if mutation is not None:
            clear_ephemeral_reclaim_handle(mutation.id)
    return redirect("importer:crm_connections")


@require_POST
def crm_disconnect(request, connection_id: str):
    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    try:
        # Advance the generation past any terminal prior disconnect so a new
        # click issues a fresh API POST instead of replaying a stale frozen
        # receipt (e.g. after the connection was reconnected). PENDING/UNKNOWN
        # rows keep the same generation for double-submit / exact-retry safety.
        identity = f"crm-disconnect:{connection_id}:{owner_session}"
        generation = _registration_generation(
            session, identity=identity, form_payload_digest=""
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_disconnect",
            route=f"/v1/crm/connections/{connection_id}/disconnect",
            logical_action_identity=identity,
            logical_action_generation=generation,
            resource_identity=connection_id,
            request_json={"owner_session": owner_session},
        )
        result = _dispatch_connection_mutation(client, mutation)
        if result.mutation.state == ApiMutation.State.REJECTED:
            messages.error(
                request,
                result.mutation.error_message
                or "CRM connection could not be disconnected.",
            )
            return redirect("importer:crm_connections")
        if result.mutation.state != ApiMutation.State.COMPLETED:
            messages.warning(
                request,
                "CRM disconnect is uncertain. Retry this exact action.",
            )
            return redirect("importer:crm_connections")
        messages.success(request, "CRM connection disconnected.")
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        ApiConsistencyError,
    ) as exc:
        messages.error(request, str(exc))
    return redirect("importer:crm_connections")


# Phase 6: Django-local copy of Phase 0 blocker wire keys. Do not import
# mappings_2. Upload/plan keys stay off this map (generic fallback).
CONNECTION_REMOVAL_BLOCKER_FLASH: dict[str, str] = {
    "duplicate-resolution journey": (
        "This connection can't be removed yet — a duplicate-resolution journey "
        "using it is still in progress."
    ),
    "CRM read grant": (
        "This connection can't be removed yet — a CRM read grant using it is "
        "still in progress."
    ),
    "workflow checkpoint": (
        "This connection can't be removed yet — a workflow using it is still "
        "in progress."
    ),
    "CRM query": (
        "This connection can't be removed yet — a CRM query using it is still "
        "in progress."
    ),
    "in-flight dependent": (
        "This connection can't be removed yet — another action is still using it."
    ),
}

_REMOVE_API_TECHNICAL_SESSION_KEY = "crm_connection_remove_api_technical"
_REMOVE_WITH_DEPENDENTS_ACTION = "crm_connection_remove_with_dependents"


def _blocker_from_mutation(mutation: ApiMutation) -> str | None:
    payload = mutation.response_json
    if not isinstance(payload, dict):
        return None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None
    details = error.get("details")
    if not isinstance(details, dict):
        return None
    blocker = details.get("blocker")
    if blocker is None:
        return None
    text = str(blocker).strip()
    return text or None


def _remove_blocker_flash(blocker: str | None, fallback: str) -> str:
    if blocker and blocker in CONNECTION_REMOVAL_BLOCKER_FLASH:
        return CONNECTION_REMOVAL_BLOCKER_FLASH[blocker]
    return fallback


def _remove_api_technical_from_mutation(
    mutation: ApiMutation | None,
) -> dict[str, Any] | None:
    if mutation is None:
        return None
    route = str(mutation.route or "").strip()
    http_status = mutation.http_status
    error_code = str(mutation.error_code or "").strip()
    if not route and http_status is None and not error_code:
        return None
    payload = mutation.response_json if isinstance(mutation.response_json, dict) else {}
    error = payload.get("error") if isinstance(payload, dict) else None
    error_id = None
    if isinstance(error, dict) and error.get("error_id"):
        error_id = str(error["error_id"])
    details: dict[str, Any] = {}
    if route:
        details["route"] = route
    if http_status is not None:
        details["http_status"] = int(http_status)
    if error_code:
        details["error_code"] = error_code
    if error_id:
        details["error_id"] = error_id
    message = str(mutation.error_message or "").strip()
    if message:
        details["message"] = message
    return details or None


def _stash_remove_api_technical(request, mutation: ApiMutation | None) -> None:
    details = _remove_api_technical_from_mutation(mutation)
    session_store = getattr(request, "session", None)
    if details is None or session_store is None:
        return
    session_store[_REMOVE_API_TECHNICAL_SESSION_KEY] = details


def _hub_api_technical_after_remove_stash(
    request, api_technical: dict[str, Any] | None
) -> dict[str, Any] | None:
    """Pop rejected-Remove diagnostics. GET-time list failure still wins."""

    session_store = getattr(request, "session", None)
    stashed = None
    if session_store is not None:
        stashed = session_store.pop(_REMOVE_API_TECHNICAL_SESSION_KEY, None)
    if api_technical is not None:
        return api_technical
    return stashed if isinstance(stashed, dict) else None


@require_POST
def crm_connection_remove(request, connection_id: str):
    """Remove a disconnected connection row. Checks persisted mutation state."""

    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    mutation = None
    try:
        identity = f"crm-forget:{connection_id}:{owner_session}"
        journal_body = {
            "connection_id": connection_id,
            "owner_session": owner_session,
        }
        generation = _registration_generation(
            session,
            identity=identity,
            form_payload_digest="",
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=uuid4(),
            mutation_kind="crm_connection_delete",
            route=f"/v1/crm/connections/{connection_id}",
            logical_action_identity=identity,
            logical_action_generation=generation,
            resource_identity=connection_id,
            request_json=journal_body,
        )
        result = _dispatch_connection_mutation(client, mutation)
        if result.mutation.state == ApiMutation.State.REJECTED:
            if result.mutation.error_code == "crm_connection_in_use":
                try:
                    preview = client.crm_connection_removal_dependents(
                        connection_id, owner_session=owner_session
                    )
                    if preview.get("runs") or preview.get("orphaned_dependents"):
                        return redirect(
                            "importer:crm_connection_remove_confirm",
                            connection_id=connection_id,
                        )
                except (ApiUnavailableError, ApiRejectedError):
                    pass
            fallback = (
                result.mutation.error_message or "CRM connection could not be removed."
            )
            messages.error(
                request,
                _remove_blocker_flash(
                    _blocker_from_mutation(result.mutation), fallback
                ),
            )
            _stash_remove_api_technical(request, result.mutation)
            return redirect("importer:crm_connections")
        if result.mutation.state != ApiMutation.State.COMPLETED:
            messages.warning(
                request,
                "CRM connection removal is uncertain. Retry this exact action.",
            )
            return redirect("importer:crm_connections")
        messages.success(request, "Removed CRM connection.")
    except (
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        ApiConsistencyError,
    ) as exc:
        messages.error(request, str(exc))
        if mutation is not None:
            mutation.refresh_from_db()
            _stash_remove_api_technical(request, mutation)
    return redirect("importer:crm_connections")


@require_GET
def crm_connection_remove_confirm(request, connection_id: str):
    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    try:
        preview = client.crm_connection_removal_dependents(
            connection_id, owner_session=owner_session
        )
    except (ApiUnavailableError, ApiRejectedError) as exc:
        messages.error(request, str(exc))
        return redirect("importer:crm_connections")
    if not preview.get("runs") and not preview.get("orphaned_dependents"):
        messages.info(request, "No dependent analyses remain. Use Remove again.")
        return redirect("importer:crm_connections")
    preview = dict(preview)
    preview["has_unloadable_runs"] = any(
        str(run.get("run_status") or "") in {"missing", "incompatible", "unloadable"}
        for run in preview.get("runs", [])
        if isinstance(run, dict)
    )
    digest = str(preview["confirmation_digest"])
    identity = f"crm-remove-with-dependents:{connection_id}:{owner_session}"
    generation = _registration_generation(
        session, identity=identity, form_payload_digest=digest
    )
    token = issue_form_token(
        owner_id=owner_id_for_request(request),
        session=session,
        action_kind=_REMOVE_WITH_DEPENDENTS_ACTION,
        action_id=digest,
        logical_action_identity=identity,
        logical_action_generation=generation,
    )
    return render(
        request,
        "importer/crm_connection_remove_confirm.html",
        {"connection_id": connection_id, "preview": preview, "form_token": token},
    )


@require_POST
def crm_connection_remove_with_dependents(request, connection_id: str):
    owner_session = _owner_session(request)
    session = _connection_session(request)
    digest = str(request.POST.get("confirmation_digest") or "").strip()
    identity = f"crm-remove-with-dependents:{connection_id}:{owner_session}"
    generation = _registration_generation(
        session, identity=identity, form_payload_digest=digest
    )
    try:
        claims = validate_form_token(
            str(request.POST.get("form_token") or ""),
            owner_id=owner_id_for_request(request),
            session=session,
            action_kind=_REMOVE_WITH_DEPENDENTS_ACTION,
            action_id=digest,
            logical_action_identity=identity,
            logical_action_generation=generation,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=claims["form_instance"],
            mutation_kind="crm_connection_remove_with_dependents",
            route=f"/v1/crm/connections/{connection_id}/remove-with-dependents",
            logical_action_identity=identity,
            logical_action_generation=generation,
            resource_identity=connection_id,
            request_json={
                "confirmation_digest": digest,
                "owner_session": owner_session,
            },
        )
        result = _dispatch_connection_mutation(EasyImportsApiClient(), mutation)
        if result.mutation.state != ApiMutation.State.COMPLETED:
            if result.mutation.error_code == "stale_removal_confirmation":
                messages.warning(
                    request,
                    "The dependent analyses changed. Review the updated list.",
                )
                return redirect(
                    "importer:crm_connection_remove_confirm",
                    connection_id=connection_id,
                )
            messages.error(
                request,
                result.mutation.error_message or "CRM connection could not be removed.",
            )
            return redirect("importer:crm_connections")
        messages.success(
            request, "Stopped dependent analyses and removed CRM connection."
        )
    except (
        FormTokenError,
        ApiUnavailableError,
        ApiRejectedError,
        MutationBusyError,
        MutationExplicitRetryRequired,
        MutationReuseError,
        ApiConsistencyError,
    ) as exc:
        messages.error(request, str(exc))
    return redirect("importer:crm_connections")


@require_POST
def crm_abandon_uncertain_connection(request, connection_id: str):
    """Operator-controlled abandon of a stuck/uncertain OAuth complete.

    Terminalizes only after remote disconnect is confirmed. Claim uses an
    atomic unleased conditional update so an in-flight callback cannot be
    clobbered between check and write.
    """

    connection_id = str(connection_id or "").strip()
    owner_session = _owner_session(request)
    session = _connection_session(request)
    client = EasyImportsApiClient()
    if not connection_id:
        messages.error(request, "Missing CRM connection identity.")
        return redirect("importer:crm_connections")

    complete = _oauth_complete_mutation(
        session, connection_id=connection_id, owner_session=owner_session
    )
    ok, reason = _can_abandon_uncertain_oauth(complete)
    if not ok or complete is None:
        messages.error(request, reason or "Cannot abandon this authorization yet.")
        return redirect("importer:crm_connections")

    claim = _claim_oauth_abandon_lease(complete)
    if claim is None:
        messages.error(
            request,
            "CRM authorization changed or is in progress. Wait and refresh.",
        )
        return redirect("importer:crm_connections")
    complete.refresh_from_db()

    # If OAuth already connected, settle success instead of abandoning.
    remote = _connection_remote_status(
        client, connection_id=connection_id, owner_session=owner_session
    )
    if remote == "connected":
        try:
            resource = client.crm_connection(connection_id, owner_session=owner_session)
        except (ApiUnavailableError, ApiRejectedError, ApiConsistencyError):
            resource = None
        if (
            isinstance(resource, dict)
            and str(resource.get("status") or "") == "connected"
        ):
            # Conditional settle only while we still own the abandon claim.
            updated = ApiMutation.objects.filter(
                pk=complete.pk,
                lease_token=claim,
                state__in=[
                    ApiMutation.State.UNKNOWN,
                    ApiMutation.State.PENDING,
                ],
            ).update(
                state=ApiMutation.State.COMPLETED,
                http_status=200,
                response_json=resource,
                response_digest=canonical_digest(resource),
                error_code="",
                error_message="",
                lease_token=None,
                lease_expires_at=None,
                updated_at=Now(),
            )
            if updated == 1:
                pending = request.session.get("crm_oauth_pending") or {}
                if str(pending.get("connection_id") or "") == connection_id:
                    request.session.pop("crm_oauth_pending", None)
                messages.success(request, "CRM connection was already established.")
                return redirect("importer:crm_connections")
        _release_oauth_abandon_claim(
            complete,
            claim=claim,
            error_code="oauth_abandon_pending_disconnect",
            error_message=(
                "Connection still appears connected; abandon did not unlock reconnect."
            ),
        )
        messages.error(
            request,
            "CRM connection still appears connected. Disconnect it normally, "
            "or wait and retry abandon.",
        )
        return redirect("importer:crm_connections")

    if remote in {"failed", "absent"}:
        if _finalize_oauth_abandon(complete, claim=claim):
            pending = request.session.get("crm_oauth_pending") or {}
            if str(pending.get("connection_id") or "") == connection_id:
                request.session.pop("crm_oauth_pending", None)
            messages.success(
                request,
                "Uncertain CRM authorization abandoned. You can start a new connection.",
            )
        else:
            messages.error(
                request,
                "CRM authorization changed during abandon. Refresh and try again.",
            )
        return redirect("importer:crm_connections")

    # pending / unknown remote: durable disconnect + re-query before terminalize.
    scrub = _confirm_remote_disconnect(
        client,
        session,
        connection_id=connection_id,
        owner_session=owner_session,
    )
    if scrub == "scrubbed":
        if _finalize_oauth_abandon(complete, claim=claim):
            pending = request.session.get("crm_oauth_pending") or {}
            if str(pending.get("connection_id") or "") == connection_id:
                request.session.pop("crm_oauth_pending", None)
            messages.success(
                request,
                "Uncertain CRM authorization abandoned. You can start a new connection.",
            )
        else:
            messages.error(
                request,
                "CRM authorization changed during abandon. Refresh and try again.",
            )
        return redirect("importer:crm_connections")

    if scrub == "connected":
        _release_oauth_abandon_claim(
            complete,
            claim=claim,
            error_code="oauth_abandon_pending_disconnect",
            error_message=(
                "Remote connection is still connected after disconnect attempt."
            ),
        )
        messages.error(
            request,
            "CRM connection is still connected. Use Disconnect, then retry abandon "
            "if needed.",
        )
        return redirect("importer:crm_connections")

    # Uncertain disconnect: keep complete non-terminal; release claim only.
    _release_oauth_abandon_claim(
        complete,
        claim=claim,
        error_code="oauth_abandon_pending_disconnect",
        error_message=(
            "Remote disconnect is not confirmed. Abandon remains pending; "
            "retry after the connection is failed, disconnected, or gone."
        ),
    )
    messages.warning(
        request,
        "Could not confirm remote disconnect. Authorization is still uncertain; "
        "retry abandon later. Do not start a second connection yet.",
    )
    return redirect("importer:crm_connections")
