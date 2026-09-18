"""Local-only Settings UI for Phase 7A-P / 7B-P / 0A-J (storage + journaled CRM app registrations).

Phase **2A** adds operator Start-over for stuck UNKNOWN registration puts
(read-side reconcile before terminalizing the journal). Phase **2B** surfaces
API route / status / error_id under Technical details on failure paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID, uuid4

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from .api_client import (
    ApiConsistencyError,
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    RegistrationSecretError,
    api_error_technical_details,
    canonical_digest,
    clear_ephemeral_registration_secrets,
    create_or_reuse_mutation,
    registration_secret_digest,
    stage_ephemeral_registration_secrets,
)
from .models import ApiMutation, ImportSession
from .oauth_redirect import (
    resolve_hubspot_oauth_redirect_uri,
    resolve_salesforce_oauth_redirect_uri,
)
from .workflow_state import (
    FormTokenError,
    decode_form_token,
    issue_form_token,
    owner_id_for_request,
)

_REG_ACTION_SF_PUT = "crm_reg_salesforce_put"
_REG_ACTION_SF_ROTATE = "crm_reg_salesforce_rotate"
_REG_ACTION_HS_PUT = "crm_reg_hubspot_put"
_REG_ACTION_HS_ROTATE = "crm_reg_hubspot_rotate"
_REG_ACTION_DELETE = "crm_reg_delete"
_REG_ACTION_START_OVER = "crm_reg_start_over"

# Journal identities used by Settings advanced forms and Connect wizard.
REGISTRATION_PUT_IDENTITIES: dict[str, tuple[str, ...]] = {
    "hubspot": ("crm-reg:hubspot:put:wizard", "crm-reg:hubspot:put:local"),
    "salesforce": ("crm-reg:salesforce:put:wizard", "crm-reg:salesforce:put:local"),
}

MANAGE_REGISTRATION_URL_NAMES: dict[str, str] = {
    "hubspot": "importer:crm_setup_hubspot",
    "salesforce": "importer:crm_setup_salesforce",
}


def _client() -> EasyImportsApiClient:
    return EasyImportsApiClient()


def _is_loopback_request(request) -> bool:
    """Settings is local-administrator only for the real TCP peer address.

    Local MVP: require ``REMOTE_ADDR`` loopback only. Client-controlled headers
    such as ``X-Forwarded-For`` are never trusted (trusted-proxy mode is out of
    local MVP scope).
    """

    remote = (request.META.get("REMOTE_ADDR") or "").strip()
    return remote in {"127.0.0.1", "::1", "localhost", "testserver"}


def _require_local_settings(request):
    if not _is_loopback_request(request):
        return HttpResponseForbidden(
            "Local Settings is available only from this machine (loopback)."
        )
    return None


def _store_settings_api_technical(request, exc: BaseException) -> None:
    session_store = getattr(request, "session", None)
    if session_store is None:
        return
    session_store["settings_api_technical"] = api_error_technical_details(exc)


def _settings_session(request) -> ImportSession:
    """Session-scoped journal ownership for local CRM registration mutations."""

    owner = owner_id_for_request(request)
    existing = (
        ImportSession.objects.filter(owner_id=owner, product_key="crm.settings")
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing
    return ImportSession.objects.create(
        owner_id=owner,
        operator_label="Local settings",
        product_key="crm.settings",
        target_provider_id="",
    )


def _registration_generation(
    session: ImportSession, *, identity: str, form_payload_digest: str
) -> int:
    """Return generation for create_or_reuse_mutation.

    While a mutation is **PENDING/UNKNOWN**, generation is **fixed** regardless of
    digest. ``create_or_reuse_mutation`` then fails closed on a changed digest
    (no second Idempotency-Key fork). Terminal COMPLETED/REJECTED rows advance
    generation for intentional new submits; form-scoped ``form_instance`` still
    allows exact-replay of a completed double-submit.
    """

    del form_payload_digest  # digest enforcement is create_or_reuse's job for open gens
    latest = (
        session.api_mutations.filter(logical_action_identity=identity)
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )
    if latest is None:
        return 0
    if latest.state in {ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN}:
        return int(latest.logical_action_generation)
    return int(latest.logical_action_generation) + 1


def _registration_form_digest(journal_body: dict) -> str:
    """Content-address the full non-secret journal body (fail-closed on change)."""

    return canonical_digest(journal_body)


def _registration_form_instance(
    request, *, session: ImportSession, action_kind: str
) -> UUID:
    """Resolve form_instance from a signed Settings form token."""

    owner = owner_id_for_request(request)
    if owner is None:
        raise FormTokenError("This form is invalid or expired. Reload it.")
    token = (request.POST.get("form_token") or "").strip()
    if not token:
        raise FormTokenError("This form is incomplete. Reload Settings and try again.")
    claims = decode_form_token(
        token,
        owner_id=owner,
        session=session,
        action_kind=action_kind,
    )
    try:
        return UUID(str(claims["form_instance"]))
    except (KeyError, TypeError, ValueError) as exc:
        raise FormTokenError("This form is invalid or expired. Reload it.") from exc


def _registration_mutation_slot(
    request,
    *,
    session: ImportSession,
    action_kind: str,
    identity: str,
    form_payload_digest: str,
) -> tuple[UUID, int]:
    """Return (form_instance, generation) for create_or_reuse_mutation.

    Same form_instance always reuses the generation of any prior mutation on that
    form (completed double-submit replay). New form tokens use
    ``_registration_generation`` (pins open PENDING/UNKNOWN; advances after
    terminal).
    """

    form_instance = _registration_form_instance(
        request, session=session, action_kind=action_kind
    )
    prior = (
        session.api_mutations.filter(form_instance=form_instance)
        .order_by("-created_at")
        .first()
    )
    if prior is not None:
        return form_instance, int(prior.logical_action_generation)
    generation = _registration_generation(
        session, identity=identity, form_payload_digest=form_payload_digest
    )
    return form_instance, generation


def _issue_reg_form_token(
    request, *, session: ImportSession, action_kind: str, action_id: str = ""
) -> str:
    owner = owner_id_for_request(request)
    return issue_form_token(
        owner_id=owner,
        session=session,
        action_kind=action_kind,
        action_id=action_id,
    )


_REGISTRATION_ERRORS = (
    ApiUnavailableError,
    ApiRejectedError,
    ApiConsistencyError,
    MutationBusyError,
    MutationExplicitRetryRequired,
    MutationReuseError,
    RegistrationSecretError,
    FormTokenError,
)


def _require_registration_secret(
    *,
    client_secret: str | None = None,
    access_token: str | None = None,
) -> None:
    """Validate secret material before creating any ApiMutation row.

    Blank staging after create would leave a PENDING mutation that pins
    generation and poisons later valid submits.
    """

    has_secret = bool(client_secret and str(client_secret).strip())
    has_token = bool(access_token and str(access_token).strip())
    if not has_secret and not has_token:
        raise RegistrationSecretError(
            "A client secret or access token is required."
        )


def _dispatch_registration(
    client: EasyImportsApiClient, mutation: ApiMutation
) -> dict:
    mutation.refresh_from_db()
    try:
        result = client.dispatch(
            mutation,
            explicit_retry=mutation.state == ApiMutation.State.UNKNOWN,
        )
    finally:
        # Drop staged secrets once terminal; keep for UNKNOWN/PENDING retry.
        mutation.refresh_from_db()
        if mutation.state in {
            ApiMutation.State.COMPLETED,
            ApiMutation.State.REJECTED,
        }:
            clear_ephemeral_registration_secrets(mutation.id)

    mutation.refresh_from_db()
    if mutation.state == ApiMutation.State.REJECTED:
        raise rejected_registration_error_from_mutation(mutation)
    if mutation.state != ApiMutation.State.COMPLETED:
        raise ApiConsistencyError(
            "CRM registration did not complete. Retry this exact action."
        )
    if not isinstance(result.response, dict):
        raise ApiConsistencyError("CRM registration response is malformed.")
    return result.response


def rejected_registration_error_from_mutation(mutation: ApiMutation) -> ApiRejectedError:
    """Rebuild a rejected registration error with Phase 2B technical fields.

    Preserves route, HTTP status, and envelope error_id from the frozen mutation
    journal (not only code/message).
    """

    error_id = None
    details = None
    payload = mutation.response_json
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            if error.get("error_id"):
                error_id = str(error.get("error_id"))
            details = error.get("details")
        elif payload.get("error_id"):
            error_id = str(payload.get("error_id"))
    return ApiRejectedError(
        mutation.error_code or "crm_app_registration_rejected",
        mutation.error_message or "CRM application registration was rejected.",
        details=details,
        route=mutation.route or None,
        http_status=mutation.http_status,
        error_id=error_id,
    )


@require_GET
def local_settings(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    session = _settings_session(request)
    storage = None
    registrations: list = []
    error = None
    api_technical = None
    session_store = getattr(request, "session", None)
    if session_store is not None:
        api_technical = session_store.pop("settings_api_technical", None)
    try:
        storage = client.local_storage()
        body = client.crm_app_registrations()
        registrations = list(body.get("registrations") or [])
    except (ApiUnavailableError, ApiRejectedError) as exc:
        error = str(exc)
        api_technical = api_error_technical_details(exc)
    for reg in registrations:
        key = str(reg.get("provider_key") or "")
        reg["delete_form_token"] = _issue_reg_form_token(
            request,
            session=session,
            action_kind=_REG_ACTION_DELETE,
            action_id=key,
        )
    stuck: list[dict[str, Any]] = []
    if getattr(request, "session", None) is not None:
        owner = owner_id_for_request(request)
        stuck_raw = (
            list_stuck_unknown_registration_puts(owner_id=owner) if owner else []
        )
        for item in stuck_raw:
            stuck.append(
                {
                    "provider_key": item.provider_key,
                    "identity": item.identity,
                    "mutation_id": item.mutation_id,
                    "generation": item.generation,
                    "http_status": item.http_status,
                    "error_message": item.error_message,
                    "start_over_form_token": _issue_reg_form_token(
                        request,
                        session=session,
                        action_kind=_REG_ACTION_START_OVER,
                        action_id=item.provider_key,
                    ),
                }
            )
    return render(
        request,
        "importer/local_settings.html",
        {
            "storage": storage,
            "registrations": registrations,
            "error": error,
            "api_technical": api_technical,
            "stuck_registration_puts": stuck,
            "salesforce_redirect_uri": resolve_salesforce_oauth_redirect_uri(),
            "hubspot_redirect_uri": resolve_hubspot_oauth_redirect_uri(),
            # Backward-compatible alias (Salesforce); prefer provider-specific keys.
            "default_redirect_uri": resolve_salesforce_oauth_redirect_uri(),
            "sf_put_form_token": _issue_reg_form_token(
                request, session=session, action_kind=_REG_ACTION_SF_PUT
            ),
            "sf_rotate_form_token": _issue_reg_form_token(
                request, session=session, action_kind=_REG_ACTION_SF_ROTATE
            ),
            "hs_put_form_token": _issue_reg_form_token(
                request, session=session, action_kind=_REG_ACTION_HS_PUT
            ),
            "hs_rotate_form_token": _issue_reg_form_token(
                request, session=session, action_kind=_REG_ACTION_HS_ROTATE
            ),
        },
    )


@require_POST
def local_settings_storage(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    restore = request.POST.get("restore_default") == "1"
    data_root = (request.POST.get("data_root") or "").strip()
    payload: dict = {"restore_default": restore}
    if not restore:
        payload["data_root"] = data_root
    try:
        result = client.update_local_storage(payload)
        if result.get("restart_required"):
            messages.warning(
                request,
                result.get("message")
                or "Storage pointer updated. Restart the API process.",
            )
        else:
            messages.success(request, "Storage location updated.")
    except (ApiUnavailableError, ApiRejectedError) as exc:
        messages.error(request, str(exc))
    return redirect("importer:local_settings")


@require_http_methods(["POST"])
def local_settings_salesforce_registration(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    session = _settings_session(request)
    client_secret = request.POST.get("client_secret") or ""
    journal_body = {
        "label": (request.POST.get("label") or "").strip(),
        "login_environment": (request.POST.get("login_environment") or "").strip(),
        "client_id": (request.POST.get("client_id") or "").strip(),
        "redirect_uri": resolve_salesforce_oauth_redirect_uri(),
        "my_domain_host": (request.POST.get("my_domain_host") or "").strip() or None,
        "expected_org_id": (request.POST.get("expected_org_id") or "").strip() or None,
        "client_secret_digest": registration_secret_digest(client_secret),
    }
    identity = "crm-reg:salesforce:put:local"
    form_digest = _registration_form_digest(journal_body)
    try:
        _require_registration_secret(client_secret=client_secret)
        form_instance, generation = _registration_mutation_slot(
            request,
            session=session,
            action_kind=_REG_ACTION_SF_PUT,
            identity=identity,
            form_payload_digest=form_digest,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_salesforce_put",
            route="/v1/settings/crm-app-registrations/salesforce",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="salesforce",
        )
        stage_ephemeral_registration_secrets(
            mutation.id, client_secret=client_secret
        )
        _dispatch_registration(client, mutation)
        messages.success(request, "Salesforce application registration saved.")
    except _REGISTRATION_ERRORS as exc:
        messages.error(request, str(exc))
        _store_settings_api_technical(request, exc)
    return redirect("importer:local_settings")


@require_POST
def local_settings_rotate_secret(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    session = _settings_session(request)
    secret = request.POST.get("client_secret") or ""
    journal_body = {"client_secret_digest": registration_secret_digest(secret)}
    identity = "crm-reg:salesforce:rotate:local"
    form_digest = _registration_form_digest(journal_body)
    try:
        _require_registration_secret(client_secret=secret)
        form_instance, generation = _registration_mutation_slot(
            request,
            session=session,
            action_kind=_REG_ACTION_SF_ROTATE,
            identity=identity,
            form_payload_digest=form_digest,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_salesforce_rotate_secret",
            route="/v1/settings/crm-app-registrations/salesforce/rotate-secret",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="salesforce",
        )
        stage_ephemeral_registration_secrets(mutation.id, client_secret=secret)
        _dispatch_registration(client, mutation)
        messages.success(request, "Salesforce client secret rotated.")
    except _REGISTRATION_ERRORS as exc:
        messages.error(request, str(exc))
        _store_settings_api_technical(request, exc)
    return redirect("importer:local_settings")


@require_http_methods(["POST"])
def local_settings_hubspot_registration(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    session = _settings_session(request)
    auth_mode = (request.POST.get("auth_mode") or "").strip()
    client_secret = request.POST.get("client_secret") or ""
    access_token = request.POST.get("access_token") or ""
    journal_body: dict = {
        "label": (request.POST.get("label") or "").strip(),
        "auth_mode": auth_mode,
        "expected_hub_id": (request.POST.get("expected_hub_id") or "").strip() or None,
        "client_secret_digest": None,
        "access_token_digest": None,
    }
    if auth_mode == "private_app":
        journal_body["client_id"] = None
        journal_body["redirect_uri"] = None
        journal_body["access_token_digest"] = registration_secret_digest(access_token)
    else:
        journal_body["client_id"] = (request.POST.get("client_id") or "").strip()
        journal_body["client_secret_digest"] = registration_secret_digest(client_secret)
        journal_body["redirect_uri"] = resolve_hubspot_oauth_redirect_uri()
    identity = "crm-reg:hubspot:put:local"
    # Full journal body (all non-secret fields + secret digests) — fail closed.
    form_digest = _registration_form_digest(journal_body)
    try:
        if auth_mode == "private_app":
            _require_registration_secret(access_token=access_token)
        else:
            _require_registration_secret(client_secret=client_secret)
        form_instance, generation = _registration_mutation_slot(
            request,
            session=session,
            action_kind=_REG_ACTION_HS_PUT,
            identity=identity,
            form_payload_digest=form_digest,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_hubspot_put",
            route="/v1/settings/crm-app-registrations/hubspot",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="hubspot",
        )
        if auth_mode == "private_app":
            stage_ephemeral_registration_secrets(
                mutation.id, access_token=access_token
            )
        else:
            stage_ephemeral_registration_secrets(
                mutation.id, client_secret=client_secret
            )
        _dispatch_registration(client, mutation)
        messages.success(request, "HubSpot application registration saved.")
    except _REGISTRATION_ERRORS as exc:
        messages.error(request, str(exc))
        _store_settings_api_technical(request, exc)
    return redirect("importer:local_settings")


@require_POST
def local_settings_rotate_hubspot_secret(request):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    client = _client()
    session = _settings_session(request)
    auth_mode = (request.POST.get("auth_mode") or "oauth").strip()
    client_secret = request.POST.get("client_secret") or ""
    access_token = request.POST.get("access_token") or ""
    if auth_mode == "private_app":
        journal_body = {
            "access_token_digest": registration_secret_digest(access_token),
            "client_secret_digest": None,
        }
    else:
        journal_body = {
            "client_secret_digest": registration_secret_digest(client_secret),
            "access_token_digest": None,
        }
    # Include auth_mode so private-app vs oauth rotate digests diverge.
    journal_body["auth_mode"] = auth_mode
    identity = "crm-reg:hubspot:rotate:local"
    form_digest = _registration_form_digest(journal_body)
    try:
        if auth_mode == "private_app":
            _require_registration_secret(access_token=access_token)
        else:
            _require_registration_secret(client_secret=client_secret)
        form_instance, generation = _registration_mutation_slot(
            request,
            session=session,
            action_kind=_REG_ACTION_HS_ROTATE,
            identity=identity,
            form_payload_digest=form_digest,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_hubspot_rotate_secret",
            route="/v1/settings/crm-app-registrations/hubspot/rotate-secret",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity="hubspot",
        )
        if auth_mode == "private_app":
            stage_ephemeral_registration_secrets(
                mutation.id, access_token=access_token
            )
        else:
            stage_ephemeral_registration_secrets(
                mutation.id, client_secret=client_secret
            )
        _dispatch_registration(client, mutation)
        messages.success(request, "HubSpot secret rotated.")
    except _REGISTRATION_ERRORS as exc:
        messages.error(request, str(exc))
        _store_settings_api_technical(request, exc)
    return redirect("importer:local_settings")


@require_POST
def local_settings_remove_registration(request, provider_key: str):
    denied = _require_local_settings(request)
    if denied is not None:
        return denied
    if request.POST.get("confirm") != "1":
        messages.error(request, "Confirm removal to delete the CRM application registration.")
        return redirect("importer:local_settings")
    client = _client()
    session = _settings_session(request)
    key = str(provider_key or "").strip().lower()
    journal_body = {"provider_key": key}
    identity = f"crm-reg:{key}:delete:local"
    form_digest = _registration_form_digest(journal_body)
    try:
        # Bind token action_id to provider when present.
        token = (request.POST.get("form_token") or "").strip()
        if token:
            owner = owner_id_for_request(request)
            claims = decode_form_token(
                token,
                owner_id=owner,
                session=session,
                action_kind=_REG_ACTION_DELETE,
            )
            claimed = str(claims.get("action_id") or "").strip().lower()
            if claimed and claimed != key:
                raise FormTokenError("This remove form is stale. Reload Settings.")
        form_instance, generation = _registration_mutation_slot(
            request,
            session=session,
            action_kind=_REG_ACTION_DELETE,
            identity=identity,
            form_payload_digest=form_digest,
        )
        mutation = create_or_reuse_mutation(
            session=session,
            form_instance=form_instance,
            mutation_kind="crm_app_registration_delete",
            route=f"/v1/settings/crm-app-registrations/{key}",
            logical_action_identity=identity,
            logical_action_generation=generation,
            form_payload_digest=form_digest,
            request_json=journal_body,
            resource_identity=key,
        )
        _dispatch_registration(client, mutation)
        messages.success(request, f"Removed {provider_key} registration.")
    except _REGISTRATION_ERRORS as exc:
        messages.error(request, str(exc))
        _store_settings_api_technical(request, exc)
    return redirect("importer:local_settings")


@dataclass(frozen=True)
class StuckRegistrationPut:
    provider_key: str
    identity: str
    mutation_id: str
    generation: int
    http_status: int | None
    error_message: str


def list_stuck_unknown_registration_puts(
    *, owner_id: UUID | str
) -> list[StuckRegistrationPut]:
    """UNKNOWN registration put rows for this browser owner (any product session)."""

    identities = tuple(
        identity
        for keys in REGISTRATION_PUT_IDENTITIES.values()
        for identity in keys
    )
    rows = (
        ApiMutation.objects.filter(
            session__owner_id=owner_id,
            logical_action_identity__in=identities,
            state=ApiMutation.State.UNKNOWN,
        )
        .select_related("session")
        .order_by("-logical_action_generation", "-created_at")
    )
    seen_providers: set[str] = set()
    stuck: list[StuckRegistrationPut] = []
    for mutation in rows:
        provider = _provider_key_from_registration_identity(
            mutation.logical_action_identity
        )
        if not provider or provider in seen_providers:
            continue
        seen_providers.add(provider)
        stuck.append(
            StuckRegistrationPut(
                provider_key=provider,
                identity=mutation.logical_action_identity,
                mutation_id=str(mutation.id),
                generation=int(mutation.logical_action_generation),
                http_status=mutation.http_status,
                error_message=str(mutation.error_message or ""),
            )
        )
    return stuck


def _provider_key_from_registration_identity(identity: str) -> str | None:
    text = str(identity or "")
    for provider, keys in REGISTRATION_PUT_IDENTITIES.items():
        if text in keys:
            return provider
    return None


def api_has_provider_registration(
    client: EasyImportsApiClient, provider_key: str
) -> bool:
    """Read-side: whether the API already lists a registration for *provider_key*."""

    body = client.crm_app_registrations()
    key = str(provider_key or "").strip().lower()
    for reg in body.get("registrations") or []:
        if str(reg.get("provider_key") or "").strip().lower() == key:
            return True
    return False


def api_provider_is_connectable(
    client: EasyImportsApiClient, provider_key: str
) -> bool:
    """Read-side: provider appears in the connectable CRM provider catalog."""

    body = client.crm_providers()
    key = str(provider_key or "").strip().lower()
    for row in body.get("providers") or []:
        if not isinstance(row, dict):
            continue
        if str(row.get("provider_key") or "").strip().lower() != key:
            continue
        # Catalog only lists composed connectable providers; default True.
        if row.get("connectable", True) is False:
            return False
        return True
    return False


def mutation_has_active_dispatch_lease(mutation: ApiMutation) -> bool:
    """True while an exact-retry/dispatch lease is still held (in-flight)."""

    if mutation.lease_token is None:
        return False
    expires = mutation.lease_expires_at
    if expires is None:
        # Token without expiry is treated as held until cleared by journal.
        return True
    return expires > timezone.now()


def terminalize_unknown_registration_mutation(
    mutation: ApiMutation,
    *,
    reason: str,
) -> None:
    """Explicit operator terminalization of an UNKNOWN registration put.

    Does not invent API state. Call only after read-side reconcile confirmed the
    registration was not committed (or when abandoning an uncertain attempt that
    left no API registration). Refuses while a dispatch lease is active so an
    in-flight exact-retry cannot be rejected underneath a committing POST.
    """

    if mutation.state != ApiMutation.State.UNKNOWN:
        raise ApiConsistencyError(
            "Only UNKNOWN registration attempts can be started over this way."
        )
    with transaction.atomic():
        locked = (
            ApiMutation.objects.select_for_update()
            .filter(pk=mutation.pk, state=ApiMutation.State.UNKNOWN)
            .first()
        )
        if locked is None:
            raise ApiConsistencyError(
                "This registration attempt is no longer UNKNOWN. Reload and retry."
            )
        if mutation_has_active_dispatch_lease(locked):
            raise MutationBusyError(
                "This registration attempt is still being sent to the API. "
                "Wait for it to finish (or for the lease to expire), then use "
                "exact retry or Start over."
            )
        locked.state = ApiMutation.State.REJECTED
        locked.error_code = "operator_start_over"
        locked.error_message = reason
        locked.lease_token = None
        locked.lease_expires_at = None
        locked.save(
            update_fields=[
                "state",
                "error_code",
                "error_message",
                "lease_token",
                "lease_expires_at",
                "updated_at",
            ]
        )
    clear_ephemeral_registration_secrets(mutation.id)


@dataclass(frozen=True)
class RegistrationStartOverResult:
    outcome: str  # "cleared" | "already_registered" | "already_connectable" | "none" | "busy"
    provider_key: str
    terminalized_count: int
    message: str
    manage_path: str | None = None


def start_over_registration_put(
    *,
    owner_id: UUID | str,
    provider_key: str,
    client: EasyImportsApiClient | None = None,
) -> RegistrationStartOverResult:
    """Phase 2A: reconcile API registration + connectability, then unlock.

    - If API lists the registration → do not terminalize; point to manage.
    - If provider is already connectable (composed catalog) → do not terminalize;
      another silent put must not be invented.
    - If neither, and no active dispatch lease on UNKNOWN rows → terminalize.
    - Exact-retry of the same digest remains available until Start over succeeds.
    """

    key = str(provider_key or "").strip().lower()
    if key not in REGISTRATION_PUT_IDENTITIES:
        raise ValueError(f"Unsupported provider for registration start-over: {key}")
    api = client or EasyImportsApiClient()
    manage_name = MANAGE_REGISTRATION_URL_NAMES.get(key)
    manage = (
        reverse(manage_name) + "?edit=1" if manage_name else None
    )

    registered = api_has_provider_registration(api, key)
    connectable = api_provider_is_connectable(api, key)
    if registered:
        return RegistrationStartOverResult(
            outcome="already_registered",
            provider_key=key,
            terminalized_count=0,
            message=(
                f"{key.title()} application credentials are already saved on the "
                "API. Use Manage app to edit, or remove the registration first — "
                "Start over does not invent a second put."
            ),
            manage_path=manage,
        )
    if connectable:
        return RegistrationStartOverResult(
            outcome="already_connectable",
            provider_key=key,
            terminalized_count=0,
            message=(
                f"{key.title()} is already connectable on this API process. "
                "Use Connect CRM / Manage app rather than starting over into a "
                "second registration put."
            ),
            manage_path=manage or reverse("importer:crm_connections"),
        )

    identities = REGISTRATION_PUT_IDENTITIES[key]
    open_unknown = list(
        ApiMutation.objects.filter(
            session__owner_id=owner_id,
            logical_action_identity__in=identities,
            state=ApiMutation.State.UNKNOWN,
        ).order_by("created_at", "id")
    )
    if not open_unknown:
        return RegistrationStartOverResult(
            outcome="none",
            provider_key=key,
            terminalized_count=0,
            message=(
                f"No stuck UNKNOWN {key} registration attempt was found. "
                "You can submit credentials normally."
            ),
            manage_path=manage,
        )

    if any(mutation_has_active_dispatch_lease(m) for m in open_unknown):
        return RegistrationStartOverResult(
            outcome="busy",
            provider_key=key,
            terminalized_count=0,
            message=(
                f"A {key} registration attempt is still being sent to the API "
                "(active dispatch lease). Wait for it to finish, then retry the "
                "exact same values or Start over again."
            ),
            manage_path=manage,
        )

    reason = (
        "Operator chose Start over with different credentials after read-side "
        "reconcile found no committed API registration and the provider is not "
        "connectable for this process."
    )
    for mutation in open_unknown:
        terminalize_unknown_registration_mutation(mutation, reason=reason)

    return RegistrationStartOverResult(
        outcome="cleared",
        provider_key=key,
        terminalized_count=len(open_unknown),
        message=(
            f"Started over for {key.title()}: cleared {len(open_unknown)} uncertain "
            "registration attempt(s). Enter credentials again (exact retry of the "
            "previous values still worked before Start over)."
        ),
        manage_path=manage,
    )


@require_POST
def registration_start_over(request, provider_key: str):
    """Explicit Start over with different credentials (Phase 2A)."""

    # Same local-admin rule as Settings / Connect wizard registration.
    if not _is_loopback_request(request):
        return HttpResponseForbidden(
            "Registration start-over is available only from this machine (loopback)."
        )

    key = str(provider_key or "").strip().lower()
    next_url = (request.POST.get("next") or "").strip() or "/settings/"
    # Only allow relative app paths (no open redirect).
    if not next_url.startswith("/") or next_url.startswith("//"):
        next_url = "/settings/"

    owner = owner_id_for_request(request)
    if owner is None:
        messages.error(request, "Session is missing. Reload and try again.")
        return redirect(next_url)

    session = _settings_session(request)
    token = (request.POST.get("form_token") or "").strip()
    try:
        if not token:
            raise FormTokenError("This form is incomplete. Reload and try again.")
        claims = decode_form_token(
            token,
            owner_id=owner,
            session=session,
            action_kind=_REG_ACTION_START_OVER,
        )
        claimed = str(claims.get("action_id") or "").strip().lower()
        if claimed and claimed != key:
            raise FormTokenError("This Start over form is stale. Reload and try again.")
        result = start_over_registration_put(owner_id=owner, provider_key=key)
    except (
        FormTokenError,
        ApiUnavailableError,
        ApiRejectedError,
        ApiConsistencyError,
        MutationBusyError,
        ValueError,
    ) as exc:
        messages.error(request, str(exc))
        if isinstance(exc, (ApiUnavailableError, ApiRejectedError)):
            _store_settings_api_technical(request, exc)
        return redirect(next_url)

    if result.outcome in {"already_registered", "already_connectable"}:
        messages.warning(request, result.message)
        if result.manage_path:
            return redirect(result.manage_path)
    elif result.outcome == "cleared":
        messages.success(request, result.message)
    elif result.outcome == "busy":
        messages.warning(request, result.message)
    else:
        messages.info(request, result.message)
    return redirect(next_url)
