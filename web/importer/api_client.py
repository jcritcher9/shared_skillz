"""HTTP-only EasyImports API client with a fenced mutation journal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from hashlib import sha256
import json
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlparse
from uuid import UUID, uuid4

import requests
from django.conf import settings
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F, Q
from django.db.models.functions import Now
from django.utils import timezone

from .api_contract import (
    ApiContractError,
    validate_api_mutation_status,
    validate_artifact_collection,
    validate_command_receipt,
    validate_crm_campaign_get_result,
    validate_crm_campaign_member_statuses_result,
    validate_crm_campaign_search_result,
    validate_crm_connection_collection,
    validate_crm_connection_resource,
    validate_crm_duplicate_journey_collection,
    validate_crm_duplicate_journey_resource,
    validate_crm_provider_catalog,
    validate_crm_query_collection,
    validate_crm_query_resource,
    validate_crm_query_rows_page,
    validate_auto_disposition_result,
    validate_duplicate_read_grant_resource,
    validate_duplicate_review_window,
    validate_duplicate_review_window_submit_result,
    validate_duplicate_reviewed_result,
    validate_duplicate_reviewed_result_summary,
    validate_reviewed_result_materialization_progress,
    validate_finalize_merge_plan_handoff_result,
    validate_amend_reviewed_disposition_window_result,
    validate_reviewed_disposition_window,
    validate_error_envelope,
    validate_health,
    validate_implied_reference_authorization_result,
    validate_prepare_reviewed_result,
    validate_product_catalog,
    validate_raw_population_upload_resource,
    validate_record_id_mapping_resource,
    validate_run_output_package_resource,
    validate_target_catalog,
    validate_upload_resource,
    validate_workflow_resource,
    validate_duplicate_execution_exception_page,
)
from .models import ApiMutation, ImportSession
from .pending_copy import in_progress_flash_message
from .uncertain_mutation_copy import (
    GENERIC_UNCERTAIN_MESSAGE,
    uncertain_mutation_message,
)


class EasyImportsApiClientError(RuntimeError):
    pass


class ApiUnavailableError(EasyImportsApiClientError):
    """API transport / 5xx / uncertain outcome (Phase 2B technical details)."""

    def __init__(
        self,
        message: str,
        *,
        route: str | None = None,
        http_status: int | None = None,
        error_id: str | None = None,
        error_code: str | None = None,
    ) -> None:
        super().__init__(message)
        self.route = route
        self.http_status = http_status
        self.error_id = error_id
        self.error_code = error_code


class ApiOperationInProgressError(EasyImportsApiClientError):
    pass


class ApiRejectedError(EasyImportsApiClientError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        details: Any = None,
        route: str | None = None,
        http_status: int | None = None,
        error_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details
        self.route = route
        self.http_status = http_status
        self.error_id = error_id
        # Alias for templates that read error_code uniformly.
        self.error_code = code


class MutationBusyError(EasyImportsApiClientError):
    pass


class MutationReuseError(EasyImportsApiClientError):
    pass


class MutationExplicitRetryRequired(EasyImportsApiClientError):
    pass


class ApiConsistencyError(EasyImportsApiClientError):
    pass


def api_error_technical_details(exc: BaseException | None) -> dict[str, Any]:
    """Public technical fields for closed Technical details disclosures (2B).

    Never includes secrets. Safe for templates under closed details.
    """

    if exc is None:
        return {"message": ""}
    status = getattr(exc, "http_status", None)
    code = getattr(exc, "error_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    error_id = getattr(exc, "error_id", None)
    route = getattr(exc, "route", None)
    out: dict[str, Any] = {"message": str(exc)}
    if route:
        out["route"] = str(route)
    if status is not None:
        out["http_status"] = int(status)
    if error_id:
        out["error_id"] = str(error_id)
    if code:
        out["error_code"] = str(code)
    return out


def _error_body_fields(payload: Any) -> tuple[str | None, str | None, str | None]:
    """Return (error_id, code, message) from an API error envelope body."""

    if not isinstance(payload, dict):
        return None, None, None
    error = payload.get("error")
    if not isinstance(error, dict):
        return None, None, None
    error_id = error.get("error_id")
    code = error.get("code")
    message = error.get("message")
    return (
        str(error_id) if error_id else None,
        str(code) if code else None,
        str(message) if message else None,
    )


@dataclass(frozen=True)
class MutationDispatchResult:
    mutation: ApiMutation
    response: dict[str, Any]


# Process-local OAuth authorization codes for live CRM complete.
# Never written to ApiMutation.request_json / Django durable storage.
_EPHEMERAL_OAUTH_CODES: dict[str, str] = {}
_EPHEMERAL_RECLAIM_HANDLES: dict[str, str] = {}

# Process-local CRM app registration secrets for Phase 0A-J dispatch.
# Keys: mutation id → {"client_secret"?: str, "access_token"?: str}
_EPHEMERAL_REGISTRATION_SECRETS: dict[str, dict[str, str]] = {}

_REGISTRATION_MUTATION_KINDS = frozenset(
    {
        "crm_app_registration_salesforce_put",
        "crm_app_registration_salesforce_rotate_secret",
        "crm_app_registration_hubspot_put",
        "crm_app_registration_hubspot_rotate_secret",
        "crm_app_registration_delete",
    }
)


def stage_ephemeral_oauth_code(mutation_id: UUID | str, authorization_code: str) -> None:
    """Hold an OAuth code in process memory for a single dispatch."""

    code = str(authorization_code or "").strip()
    if not code:
        raise ValueError("authorization_code must be non-empty.")
    _EPHEMERAL_OAUTH_CODES[str(mutation_id)] = code


def clear_ephemeral_oauth_code(mutation_id: UUID | str) -> None:
    _EPHEMERAL_OAUTH_CODES.pop(str(mutation_id), None)


def authorization_code_digest(authorization_code: str) -> str:
    return sha256(str(authorization_code).encode("utf-8")).hexdigest()


def stage_ephemeral_reclaim_handle(mutation_id: UUID | str, reclaim_handle: str) -> None:
    """Hold a reclaim handle in process memory for a single dispatch."""

    handle = str(reclaim_handle or "").strip()
    if not handle:
        raise ValueError("reclaim_handle must be non-empty.")
    _EPHEMERAL_RECLAIM_HANDLES[str(mutation_id)] = handle


def clear_ephemeral_reclaim_handle(mutation_id: UUID | str) -> None:
    _EPHEMERAL_RECLAIM_HANDLES.pop(str(mutation_id), None)


def reclaim_handle_digest(reclaim_handle: str) -> str:
    return sha256(str(reclaim_handle).encode("utf-8")).hexdigest()


def scrub_reclaim_handle_from_envelope(body: dict[str, Any] | None) -> dict[str, Any]:
    """Drop the one-shot reclaim handle before persisting a journal body."""

    if not isinstance(body, dict):
        return {}
    error = body.get("error")
    if not isinstance(error, dict):
        return body
    details = error.get("details")
    if not isinstance(details, dict):
        return body
    conflict = details.get("conflict")
    if not isinstance(conflict, dict) or "reclaim_handle" not in conflict:
        return body
    scrubbed_conflict = {
        key: value for key, value in conflict.items() if key != "reclaim_handle"
    }
    return {
        **body,
        "error": {
            **error,
            "details": {**details, "conflict": scrubbed_conflict},
        },
    }


class RegistrationSecretError(ValueError):
    """Blank or missing registration secret (operator validation failure)."""


def stage_ephemeral_registration_secrets(
    mutation_id: UUID | str,
    *,
    client_secret: str | None = None,
    access_token: str | None = None,
) -> None:
    """Hold registration secrets in process memory for a single dispatch."""

    payload: dict[str, str] = {}
    if client_secret is not None and str(client_secret).strip():
        payload["client_secret"] = str(client_secret)
    if access_token is not None and str(access_token).strip():
        payload["access_token"] = str(access_token)
    if not payload:
        raise RegistrationSecretError(
            "A client secret or access token is required."
        )
    _EPHEMERAL_REGISTRATION_SECRETS[str(mutation_id)] = payload


def clear_ephemeral_registration_secrets(mutation_id: UUID | str) -> None:
    _EPHEMERAL_REGISTRATION_SECRETS.pop(str(mutation_id), None)


def registration_secret_digest(value: str | None) -> str | None:
    if value is None or not str(value):
        return None
    return sha256(str(value).encode("utf-8")).hexdigest()


def canonical_digest(value: Any) -> str:
    material = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return sha256(material).hexdigest()


_SQLITE_LOCK_ATTEMPTS = 16
_SQLITE_LOCK_SLEEP_BASE_S = 0.05


def create_or_reuse_mutation(
    *,
    session: ImportSession,
    form_instance: UUID,
    mutation_kind: str,
    route: str,
    logical_action_identity: str,
    logical_action_generation: int = 0,
    request_json: dict[str, Any] | None = None,
    request_builder: Callable[[datetime], dict[str, Any]] | None = None,
    multipart_metadata: dict[str, Any] | None = None,
    form_payload_digest: str = "",
    workflow=None,
    resource_identity: str = "",
    replacement_of: ApiMutation | None = None,
) -> ApiMutation:
    if not logical_action_identity:
        raise ValueError("A mutation requires a logical action identity.")
    if request_builder is not None and request_json is not None:
        raise ValueError("Use request_json or request_builder, not both.")
    request_kind = "multipart" if multipart_metadata is not None else "json"
    submitted_form_digest = form_payload_digest or canonical_digest(
        multipart_metadata if request_kind == "multipart" else request_json
    )

    def matching_existing() -> ApiMutation | None:
        return (
            ApiMutation.objects.filter(session=session)
            .filter(
                Q(form_instance=form_instance)
                | Q(
                    logical_action_identity=logical_action_identity,
                    logical_action_generation=logical_action_generation,
                )
            )
            .order_by("created_at", "id")
            .first()
        )

    def validate_existing(existing: ApiMutation) -> ApiMutation:
        if (
            existing.session_id != session.id
            or existing.mutation_kind != mutation_kind
            or existing.route != route
            or existing.logical_action_identity != logical_action_identity
            or existing.logical_action_generation != logical_action_generation
            or existing.form_payload_digest != submitted_form_digest
        ):
            raise MutationReuseError(
                "This logical action was already submitted with different values."
            )
        return existing

    # SQLite may surface a concurrent unique INSERT as OperationalError
    # ("database is locked") instead of IntegrityError until the winner
    # commits. Matching rules stay exact; only the lock is retried.
    last_error: BaseException | None = None
    for attempt_i in range(_SQLITE_LOCK_ATTEMPTS):
        try:
            existing = matching_existing()
            if existing is not None:
                return validate_existing(existing)

            mutation_id = uuid4()
            try:
                with transaction.atomic():
                    first_submitted_at = timezone.now()
                    frozen_request_json = (
                        request_builder(first_submitted_at)
                        if request_builder is not None
                        else request_json
                    )
                    frozen = (
                        multipart_metadata
                        if request_kind == "multipart"
                        else frozen_request_json
                    )
                    digest = canonical_digest(
                        {
                            "method": "POST",
                            "route": route,
                            "kind": mutation_kind,
                            "resource_identity": resource_identity,
                            "request_kind": request_kind,
                            "request": frozen,
                        }
                    )
                    return ApiMutation.objects.create(
                        id=mutation_id,
                        session=session,
                        workflow=workflow,
                        form_instance=form_instance,
                        idempotency_key=f"web-{mutation_id.hex}",
                        mutation_kind=mutation_kind,
                        route=route,
                        resource_identity=resource_identity,
                        logical_action_identity=logical_action_identity,
                        logical_action_generation=logical_action_generation,
                        form_payload_digest=submitted_form_digest,
                        request_kind=request_kind,
                        request_json=frozen_request_json,
                        multipart_metadata=multipart_metadata,
                        request_digest=digest,
                        first_submitted_at=first_submitted_at,
                        replacement_of=replacement_of,
                    )
            except IntegrityError:
                existing = matching_existing()
                if existing is None:
                    raise
                return validate_existing(existing)
        except OperationalError as exc:
            last_error = exc
            time.sleep(_SQLITE_LOCK_SLEEP_BASE_S * (attempt_i + 1))
    if last_error is not None:
        raise last_error
    raise OperationalError("sqlite lock retry exhausted")


class EasyImportsApiClient:
    def __init__(self, *, http: requests.Session | None = None) -> None:
        self.base_url = settings.EASYIMPORTS_API_BASE_URL.rstrip("/")
        self.timeout = (
            settings.EASYIMPORTS_API_CONNECT_TIMEOUT,
            settings.EASYIMPORTS_API_READ_TIMEOUT,
        )
        self.mutation_timeout = (
            settings.EASYIMPORTS_API_CONNECT_TIMEOUT,
            settings.EASYIMPORTS_API_MUTATION_READ_TIMEOUT,
        )
        self.lease_seconds = (
            settings.EASYIMPORTS_API_CONNECT_TIMEOUT
            + settings.EASYIMPORTS_API_MUTATION_READ_TIMEOUT
            + settings.EASYIMPORTS_API_LEASE_SAFETY_SECONDS
        )
        self.http = http or requests.Session()

    def assert_compatible(self) -> dict[str, Any]:
        return self._get_json("/health", validate_health)

    def products(self) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json("/v1/catalog/products", validate_product_catalog)

    def targets(self) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json("/v1/catalog/targets", validate_target_catalog)

    def crm_providers(self) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json("/v1/crm/providers", validate_crm_provider_catalog)

    def local_storage(self) -> dict[str, Any]:
        """Phase 7A-P Settings → Storage (local-only API)."""

        self.assert_compatible()
        return self._get_json("/v1/settings/storage", lambda payload: dict(payload))

    def update_local_storage(self, body: dict[str, Any]) -> dict[str, Any]:
        self.assert_compatible()
        return self._json_request("PUT", "/v1/settings/storage", body)

    def crm_app_registrations(self) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            "/v1/settings/crm-app-registrations", lambda payload: dict(payload)
        )

    def put_salesforce_app_registration(
        self, body: dict[str, Any], *, mutation: ApiMutation
    ) -> dict[str, Any]:
        """Dispatch journaled Salesforce registration (Phase 0A-J)."""

        self.assert_compatible()
        result = self.dispatch(mutation, explicit_retry=False)
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("Salesforce registration response is malformed.")
        return result.response

    def rotate_salesforce_app_secret(
        self, body: dict[str, Any], *, mutation: ApiMutation
    ) -> dict[str, Any]:
        self.assert_compatible()
        result = self.dispatch(mutation, explicit_retry=False)
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("Salesforce rotate response is malformed.")
        return result.response

    def put_hubspot_app_registration(
        self, body: dict[str, Any], *, mutation: ApiMutation
    ) -> dict[str, Any]:
        self.assert_compatible()
        result = self.dispatch(mutation, explicit_retry=False)
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("HubSpot registration response is malformed.")
        return result.response

    def rotate_hubspot_app_secret(
        self, body: dict[str, Any], *, mutation: ApiMutation
    ) -> dict[str, Any]:
        self.assert_compatible()
        result = self.dispatch(mutation, explicit_retry=False)
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("HubSpot rotate response is malformed.")
        return result.response

    def delete_crm_app_registration(
        self, provider_key: str, *, mutation: ApiMutation
    ) -> dict[str, Any]:
        self.assert_compatible()
        result = self.dispatch(mutation, explicit_retry=False)
        if not isinstance(result.response, dict):
            raise ApiConsistencyError("CRM registration delete response is malformed.")
        return result.response

    def _json_request(
        self, method: str, route: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return self._json_request_owner(
            method,
            route,
            body,
            owner_session=None,
            expected_status=None,
            validator=None,
        )

    def _json_request_owner(
        self,
        method: str,
        route: str,
        body: dict[str, Any],
        *,
        owner_session: str | None,
        expected_status: int | None,
        validator: Callable[[Any], dict[str, Any]] | None,
    ) -> dict[str, Any]:
        url = urljoin(self.base_url + "/", route.lstrip("/"))
        headers = {"Content-Type": "application/json"}
        if owner_session:
            headers["X-Owner-Session"] = owner_session
        try:
            response = self.http.request(
                method,
                url,
                json=body,
                timeout=self.timeout,
                allow_redirects=False,
                headers=headers,
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError(
                "The EasyImports API is unavailable.",
                route=route,
            ) from exc
        if 300 <= response.status_code < 400:
            raise ApiUnavailableError(
                "The EasyImports API redirected unexpectedly.",
                route=route,
                http_status=response.status_code,
            )
        try:
            payload = response.json() if response.content else {}
        except ValueError as exc:
            raise ApiUnavailableError(
                "The EasyImports API returned malformed JSON.",
                route=route,
                http_status=response.status_code,
            ) from exc
        if response.status_code in {404, 409, 422}:
            try:
                error = validate_error_envelope(payload)["error"]
            except ApiContractError as exc:
                raise ApiUnavailableError(
                    "The API error response was malformed.",
                    route=route,
                    http_status=response.status_code,
                ) from exc
            raise ApiRejectedError(
                error["code"],
                error["message"],
                details=error.get("details"),
                route=route,
                http_status=response.status_code,
                error_id=error.get("error_id"),
            )
        if response.status_code >= 500 or not 200 <= response.status_code < 300:
            err_id, err_code, err_msg = _error_body_fields(payload)
            raise ApiUnavailableError(
                err_msg or "The EasyImports API request failed.",
                route=route,
                http_status=response.status_code,
                error_id=err_id,
                error_code=err_code,
            )
        if expected_status is not None and response.status_code != expected_status:
            raise ApiUnavailableError(
                f"Unexpected status {response.status_code} for {route}.",
                route=route,
                http_status=response.status_code,
            )
        if not isinstance(payload, dict):
            raise ApiUnavailableError(
                "The EasyImports API contract is incompatible.",
                route=route,
                http_status=response.status_code,
            )
        if validator is not None:
            try:
                return validator(payload)
            except (ApiContractError, TypeError, KeyError, ApiUnavailableError) as exc:
                if isinstance(exc, ApiUnavailableError):
                    raise
                raise ApiUnavailableError(
                    "The EasyImports API contract is incompatible.",
                    route=route,
                    http_status=response.status_code,
                ) from exc
        return payload

    def crm_connections(self, *, owner_session: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            "/v1/crm/connections",
            validate_crm_connection_collection,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_connection(self, connection_id: str, *, owner_session: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            f"/v1/crm/connections/{quote(connection_id, safe='')}",
            validate_crm_connection_resource,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_connection_removal_dependents(
        self, connection_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        self.assert_compatible()

        def validate(value: Any) -> dict[str, Any]:
            if not isinstance(value, dict):
                raise ApiUnavailableError("Connection removal preview is malformed.")
            if not isinstance(value.get("runs"), list) or not isinstance(
                value.get("orphaned_dependents"), list
            ) or not str(value.get("confirmation_digest") or ""):
                raise ApiUnavailableError("Connection removal preview is malformed.")
            return dict(value)

        return self._get_json(
            f"/v1/crm/connections/{quote(connection_id, safe='')}/removal-dependents",
            validate,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_duplicate_journeys(self, *, owner_session: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            "/v1/crm/duplicate-journeys",
            validate_crm_duplicate_journey_collection,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_duplicate_journey(
        self, journey_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            f"/v1/crm/duplicate-journeys/{quote(journey_id, safe='')}",
            validate_crm_duplicate_journey_resource,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_duplicate_population_upload(
        self, population_upload_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        """GET durable raw population upload inspect resource (Phase 1A)."""

        self.assert_compatible()
        return self._get_json(
            f"/v1/crm/duplicate-population-uploads/"
            f"{quote(population_upload_id, safe='')}",
            validate_raw_population_upload_resource,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_duplicate_read_grant(
        self, grant_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            f"/v1/crm/duplicate-read-grants/{quote(grant_id, safe='')}",
            validate_duplicate_read_grant_resource,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_queries(self, *, owner_session: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            "/v1/crm/queries",
            validate_crm_query_collection,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_query(self, query_id: str, *, owner_session: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            f"/v1/crm/queries/{quote(query_id, safe='')}",
            validate_crm_query_resource,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_query_rows(
        self,
        query_id: str,
        *,
        owner_session: str,
        cursor: str | None = None,
        offset: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        self.assert_compatible()
        from urllib.parse import urlencode

        query: dict[str, str] = {}
        if cursor is not None:
            query["cursor"] = str(cursor)
        if offset is not None:
            query["offset"] = str(offset)
        if page_size is not None:
            query["page_size"] = str(page_size)
        route = f"/v1/crm/queries/{quote(query_id, safe='')}/rows"
        if query:
            route = f"{route}?{urlencode(query)}"
        return self._get_json(
            route,
            validate_crm_query_rows_page,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_campaigns_search(
        self,
        connection_id: str,
        *,
        owner_session: str,
        mode: str = "name_exact",
        q: str,
    ) -> dict[str, Any]:
        """Proxy FE-CM-1 Campaign name search (ephemeral; no SF credentials here)."""

        self.assert_compatible()
        from urllib.parse import urlencode

        route = (
            f"/v1/crm/connections/{quote(connection_id, safe='')}/campaigns"
            f"?{urlencode({'mode': mode, 'q': q})}"
        )
        return self._get_json(
            route,
            validate_crm_campaign_search_result,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_campaign(
        self,
        connection_id: str,
        campaign_id: str,
        *,
        owner_session: str,
    ) -> dict[str, Any]:
        """Proxy FE-CM-1 Campaign get by Id."""

        self.assert_compatible()
        route = (
            f"/v1/crm/connections/{quote(connection_id, safe='')}"
            f"/campaigns/{quote(campaign_id, safe='')}"
        )
        return self._get_json(
            route,
            validate_crm_campaign_get_result,
            headers={"X-Owner-Session": owner_session},
        )

    def crm_campaign_member_statuses(
        self,
        connection_id: str,
        campaign_id: str,
        *,
        owner_session: str,
    ) -> dict[str, Any]:
        """Proxy FE-CM-1 ordered CampaignMember status labels."""

        self.assert_compatible()
        route = (
            f"/v1/crm/connections/{quote(connection_id, safe='')}"
            f"/campaigns/{quote(campaign_id, safe='')}/member-statuses"
        )
        return self._get_json(
            route,
            validate_crm_campaign_member_statuses_result,
            headers={"X-Owner-Session": owner_session},
        )

    def duplicate_execution_exceptions(
        self,
        run_id: str,
        *,
        owner_session: str,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET one committed duplicate-execution exception page (Phase 5)."""

        self.assert_compatible()
        route = (
            f"/v1/workflows/{quote(run_id, safe='')}/duplicate-execution-exceptions"
        )
        query = [f"limit={int(limit)}"]
        token = str(cursor or "").strip()
        if token:
            query.append(f"cursor={quote(token, safe='')}")
        return self._get_json(
            f"{route}?{'&'.join(query)}",
            validate_duplicate_execution_exception_page,
            headers={"X-Owner-Session": owner_session},
        )

    def workflow(
        self, run_id: str, *, owner_session: str | None = None
    ) -> dict[str, Any]:
        self.assert_compatible()
        headers = None
        if owner_session is not None:
            headers = {"X-Owner-Session": owner_session}
        return self._get_json(
            f"/v1/workflows/{run_id}",
            validate_workflow_resource,
            headers=headers,
        )

    def duplicate_review_window(
        self, run_id: str, *, owner_session: str, cursor: str | None = None
    ) -> dict[str, Any]:
        """GET next ≤5 undecided groups or review-complete terminal (Phase 4B)."""

        self.assert_compatible()
        route = f"/v1/workflows/{quote(run_id, safe='')}/duplicate-review-window"
        token = str(cursor or "").strip()
        if token:
            route = f"{route}?cursor={quote(token, safe='')}"
        return self._get_json(
            route,
            validate_duplicate_review_window,
            headers={"X-Owner-Session": owner_session},
        )

    def duplicate_reviewed_result(
        self, review_run_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        """GET digest-bound final reviewed frames (Phase 5A)."""

        self.assert_compatible()
        return self._get_json(
            f"/v1/workflows/{quote(review_run_id, safe='')}/duplicate-reviewed-result",
            validate_duplicate_reviewed_result,
            headers={"X-Owner-Session": owner_session},
        )

    def duplicate_reviewed_result_summary(
        self, review_run_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        """GET the bounded reviewed-result companion (API 1.59.0)."""

        self.assert_compatible()
        return self._get_json(
            f"/v1/workflows/{quote(review_run_id, safe='')}/duplicate-reviewed-result-summary",
            validate_duplicate_reviewed_result_summary,
            headers={"X-Owner-Session": owner_session},
        )

    def duplicate_reviewed_disposition_window(
        self,
        review_run_id: str,
        *,
        owner_session: str,
        cursor: str | None = None,
        limit: int = 25,
    ) -> dict[str, Any]:
        """GET one persisted completed-review disposition window."""

        self.assert_compatible()
        route = (
            f"/v1/workflows/{quote(review_run_id, safe='')}"
            "/duplicate-reviewed-dispositions-window"
        )
        query = [f"limit={int(limit)}"]
        token = str(cursor or "").strip()
        if token:
            query.append(f"cursor={quote(token, safe='')}")
        return self._get_json(
            f"{route}?{'&'.join(query)}",
            validate_reviewed_disposition_window,
            headers={"X-Owner-Session": owner_session},
        )

    def duplicate_reviewed_result_progress(
        self, review_run_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        """GET-only reviewed-result materialization progress. Never writes."""

        self.assert_compatible()
        return self._get_json(
            (
                f"/v1/workflows/{quote(review_run_id, safe='')}"
                "/duplicate-reviewed-result-progress"
            ),
            validate_reviewed_result_materialization_progress,
            headers={"X-Owner-Session": owner_session},
        )

    def mutation_status(
        self,
        idempotency_key: str,
        *,
        owner_session: str | None = None,
    ) -> dict[str, Any]:
        headers: dict[str, str] = {}
        presented = str(owner_session or "").strip()
        if presented:
            headers["X-Owner-Session"] = presented
        return self._get_json(
            f"/v1/mutations/{quote(idempotency_key, safe='')}",
            validate_api_mutation_status,
            headers=headers or None,
        )

    def artifacts(self, run_id: str) -> dict[str, Any]:
        self.assert_compatible()
        return self._get_json(
            f"/v1/workflows/{run_id}/artifacts", validate_artifact_collection
        )

    def run_output_package_for_run(
        self, run_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        """Read-only package lookup for a run. Never creates (freeze §5.3)."""

        self.assert_compatible()
        value = self._get_json(
            f"/v1/workflows/{quote(run_id, safe='')}/run-output-package",
            validate_run_output_package_resource,
            headers={"X-Owner-Session": owner_session},
        )
        if str(value.get("run_id") or "") != str(run_id):
            raise ApiConsistencyError(
                "Run-output package response run_id does not match the requested run."
            )
        return value

    def run_output_package(
        self, package_id: str, *, owner_session: str
    ) -> dict[str, Any]:
        self.assert_compatible()
        value = self._get_json(
            f"/v1/run-output-packages/{quote(package_id, safe='')}",
            validate_run_output_package_resource,
            headers={"X-Owner-Session": owner_session},
        )
        if str(value.get("package_id") or "") != str(package_id):
            raise ApiConsistencyError(
                "Run-output package response package_id does not match the request."
            )
        return value

    def review_handoff(self, run_id: str, handoff_id: str) -> dict[str, Any]:
        self.assert_compatible()
        value = self._get_json(
            f"/v1/workflows/{run_id}/review-handoffs/{handoff_id}",
            lambda item: item,
        )
        required = {"handoff_id", "entity", "group_count", "binding_digest"}
        if not isinstance(value, dict) or set(value) != required:
            raise ApiUnavailableError("The API returned a malformed review handoff.")
        return value

    @staticmethod
    def _validate_column_mapping_plan(payload: Any) -> dict[str, Any]:
        if not isinstance(payload, dict):
            raise ApiUnavailableError("Column mapping plan response is malformed.")
        for key in (
            "plan_id",
            "status",
            "rows",
            "source_schema_digest",
            "destination_digest",
            "target_contract_digest",
            "plan_content_digest",
        ):
            if key not in payload:
                raise ApiUnavailableError(
                    f"Column mapping plan missing field {key!r}."
                )
        if not isinstance(payload.get("rows"), list):
            raise ApiUnavailableError("Column mapping plan rows must be a list.")
        return dict(payload)

    def create_column_mapping_plan(
        self, body: dict[str, Any], *, owner_session: str
    ) -> dict[str, Any]:
        """MAP-2: create draft plan + auto-detect."""

        self.assert_compatible()
        return self._json_request_owner(
            "POST",
            "/v1/column-mapping-plans",
            body,
            owner_session=owner_session,
            expected_status=201,
            validator=self._validate_column_mapping_plan,
        )

    def get_column_mapping_plan(
        self, plan_id: str, *, owner_session: str, support_mode: bool = False
    ) -> dict[str, Any]:
        self.assert_compatible()
        query = "?support_mode=true" if support_mode else ""
        return self._get_json(
            f"/v1/column-mapping-plans/{quote(plan_id, safe='')}{query}",
            self._validate_column_mapping_plan,
            headers={"X-Owner-Session": owner_session},
        )

    def patch_column_mapping_plan_row(
        self,
        plan_id: str,
        source_ordinal: int,
        body: dict[str, Any],
        *,
        owner_session: str,
    ) -> dict[str, Any]:
        self.assert_compatible()
        return self._json_request_owner(
            "POST",
            f"/v1/column-mapping-plans/{quote(plan_id, safe='')}/rows/{int(source_ordinal)}",
            body,
            owner_session=owner_session,
            expected_status=200,
            validator=self._validate_column_mapping_plan,
        )

    def confirm_column_mapping_plan(
        self,
        plan_id: str,
        body: dict[str, Any] | None = None,
        *,
        owner_session: str,
        plan_content_digest: str | None = None,
    ) -> dict[str, Any]:
        self.assert_compatible()
        payload = dict(body or {})
        if plan_content_digest is not None:
            payload["plan_content_digest"] = plan_content_digest
        if not str(payload.get("plan_content_digest") or "").strip():
            raise ApiUnavailableError(
                "confirm requires plan_content_digest of the reviewed plan."
            )
        return self._json_request_owner(
            "POST",
            f"/v1/column-mapping-plans/{quote(plan_id, safe='')}/confirm",
            payload,
            owner_session=owner_session,
            expected_status=200,
            validator=self._validate_column_mapping_plan,
        )

    def atomic_review_column_mapping_plan(
        self,
        plan_id: str,
        body: dict[str, Any],
        *,
        owner_session: str,
    ) -> dict[str, Any]:
        """MAP-R3 multi-row save_draft or confirm (CAS + journal)."""

        self.assert_compatible()
        payload = dict(body or {})
        if not str(payload.get("expected_plan_content_digest") or "").strip():
            raise ApiUnavailableError(
                "atomic review requires expected_plan_content_digest."
            )
        if str(payload.get("intent") or "").strip() not in {"save_draft", "confirm"}:
            raise ApiUnavailableError(
                "atomic review intent must be save_draft or confirm."
            )
        return self._json_request_owner(
            "POST",
            f"/v1/column-mapping-plans/{quote(plan_id, safe='')}/atomic-review",
            payload,
            owner_session=owner_session,
            expected_status=200,
            validator=self._validate_column_mapping_plan,
        )

    def list_column_mapping_choices(
        self,
        *,
        owner_session: str,
        destination_mode: str = "catalog",
        catalog_id: str | None = None,
        provider_key: str | None = None,
        connection_id: str | None = None,
        object_keys: list[str] | None = None,
        operation_key: str | None = None,
        include_expansion: bool = False,
    ) -> dict[str, Any]:
        self.assert_compatible()
        params: list[str] = [f"destination_mode={quote(destination_mode, safe='')}"]
        if catalog_id:
            params.append(f"catalog_id={quote(str(catalog_id), safe='')}")
        if provider_key:
            params.append(f"provider_key={quote(str(provider_key), safe='')}")
        if connection_id:
            params.append(f"connection_id={quote(str(connection_id), safe='')}")
        if object_keys:
            params.append(
                "object_keys=" + quote(",".join(str(k) for k in object_keys), safe="")
            )
        if operation_key:
            params.append(f"operation_key={quote(str(operation_key), safe='')}")
        if include_expansion:
            params.append("include_expansion=true")
        route = "/v1/column-mapping-choices?" + "&".join(params)

        def _validate(payload: Any) -> dict[str, Any]:
            if not isinstance(payload, dict) or not isinstance(
                payload.get("choices"), list
            ):
                raise ApiUnavailableError("Column mapping choices response is malformed.")
            return dict(payload)

        return self._get_json(
            route,
            _validate,
            headers={"X-Owner-Session": owner_session},
        )

    def dispatch(
        self,
        mutation: ApiMutation,
        *,
        file_path: Path | None = None,
        explicit_retry: bool = False,
    ) -> MutationDispatchResult:
        mutation.refresh_from_db()
        if mutation.state in {
            ApiMutation.State.COMPLETED,
            ApiMutation.State.REJECTED,
        }:
            if not isinstance(mutation.response_json, dict):
                raise ApiConsistencyError("A finished mutation has no frozen response.")
            return MutationDispatchResult(mutation, mutation.response_json)
        if mutation.state == ApiMutation.State.UNKNOWN and not explicit_retry:
            raise MutationExplicitRetryRequired(
                "This outcome is uncertain. Use the explicit exact-retry action."
            )

        self.assert_compatible()
        lease_token = self._acquire_lease(mutation)
        started = time.monotonic()
        try:
            response = self._send(mutation, file_path=file_path)
        except (
            requests.ReadTimeout,
            requests.ConnectTimeout,
            requests.RequestException,
        ) as exc:
            message = uncertain_mutation_message(
                mutation_kind=mutation.mutation_kind,
                exc=exc,
                elapsed_seconds=time.monotonic() - started,
                connect_timeout=self.mutation_timeout[0],
                read_timeout=self.mutation_timeout[1],
            )
            self._persist_unknown(mutation, lease_token, message=message)
            raise ApiUnavailableError(
                message,
                route=mutation.route,
            ) from exc

        raw_digest = sha256(response.content).hexdigest()
        # Journaled DELETE may return 204 with empty body; freeze a receipt from
        # the durable request material (never invent secrets).
        if (
            response.status_code == 204
            and mutation.mutation_kind == "crm_app_registration_delete"
        ):
            req = mutation.request_json if isinstance(mutation.request_json, dict) else {}
            payload = {
                "provider_key": str(req.get("provider_key") or mutation.resource_identity or ""),
                "status": "deleted",
            }
            return self._persist_final(
                mutation,
                lease_token,
                state=ApiMutation.State.COMPLETED,
                http_status=204,
                payload=payload,
                error_code="",
                error_message="",
            )
        if (
            response.status_code == 204
            and mutation.mutation_kind == "crm_connection_delete"
        ):
            req = mutation.request_json if isinstance(mutation.request_json, dict) else {}
            payload = {
                "connection_id": str(
                    req.get("connection_id") or mutation.resource_identity or ""
                ),
                "status": "deleted",
            }
            return self._persist_final(
                mutation,
                lease_token,
                state=ApiMutation.State.COMPLETED,
                http_status=204,
                payload=payload,
                error_code="",
                error_message="",
            )

        try:
            payload = response.json()
        except ValueError as exc:
            self._persist_unknown(
                mutation,
                lease_token,
                http_status=response.status_code,
                response_digest=raw_digest,
                message="The API returned malformed JSON.",
            )
            raise ApiUnavailableError(
                GENERIC_UNCERTAIN_MESSAGE,
                route=mutation.route,
                http_status=response.status_code,
            ) from exc

        if response.status_code == 202:
            try:
                operation_status = validate_api_mutation_status(payload)
            except ApiContractError as exc:
                return self._unknown_contract_response(
                    mutation,
                    lease_token,
                    response.status_code,
                    raw_digest,
                    payload,
                    exc,
                )
            if operation_status["mutation_kind"] != mutation.mutation_kind:
                self._persist_unknown(
                    mutation,
                    lease_token,
                    http_status=response.status_code,
                    response_digest=raw_digest,
                    payload=operation_status,
                    message="The API mutation status named a different command.",
                )
                raise ApiConsistencyError(
                    "The API mutation status named a different command."
                )
            self._persist_processing(
                mutation,
                lease_token,
                http_status=response.status_code,
                response_digest=raw_digest,
                payload=operation_status,
            )
            raise ApiOperationInProgressError(
                in_progress_flash_message(mutation)
            )

        if response.status_code in {403, 404, 409, 422}:
            try:
                envelope = validate_error_envelope(payload)
            except ApiContractError as exc:
                return self._unknown_contract_response(
                    mutation,
                    lease_token,
                    response.status_code,
                    raw_digest,
                    payload,
                    exc,
                )
            error = envelope["error"]
            if (
                response.status_code == 409
                and error["code"] == "mutation_in_progress"
            ):
                self._persist_processing(
                    mutation,
                    lease_token,
                    http_status=response.status_code,
                    response_digest=raw_digest,
                    payload=envelope,
                )
                raise ApiOperationInProgressError(
                    in_progress_flash_message(mutation)
                )
            return self._persist_final(
                mutation,
                lease_token,
                state=ApiMutation.State.REJECTED,
                http_status=response.status_code,
                payload=envelope,
                error_code=error["code"],
                error_message=error["message"],
            )

        if response.status_code >= 500:
            err_id, err_code, _err_msg = _error_body_fields(payload)
            self._persist_unknown(
                mutation,
                lease_token,
                http_status=response.status_code,
                response_digest=raw_digest,
                payload=payload,
                message=GENERIC_UNCERTAIN_MESSAGE,
            )
            raise ApiUnavailableError(
                GENERIC_UNCERTAIN_MESSAGE,
                route=mutation.route,
                http_status=response.status_code,
                error_id=err_id,
                error_code=err_code,
            )

        try:
            validated, state, error_code, error_message = (
                self._classify_mutation_payload(mutation, payload)
            )
        except ApiContractError as exc:
            return self._unknown_contract_response(
                mutation, lease_token, response.status_code, raw_digest, payload, exc
            )

        if not 200 <= response.status_code < 300:
            self._persist_unknown(
                mutation,
                lease_token,
                http_status=response.status_code,
                response_digest=raw_digest,
                payload=payload,
                message="The API returned an unsupported status.",
            )
            err_id, err_code, err_msg = _error_body_fields(payload)
            raise ApiUnavailableError(
                err_msg or GENERIC_UNCERTAIN_MESSAGE,
                route=mutation.route,
                http_status=response.status_code,
                error_id=err_id,
                error_code=err_code,
            )

        return self._persist_final(
            mutation,
            lease_token,
            state=state,
            http_status=response.status_code,
            payload=validated,
            error_code=error_code,
            error_message=error_message,
        )

    def reconcile_mutation_status(
        self,
        mutation: ApiMutation,
        operation_status: dict[str, Any],
    ) -> MutationDispatchResult | None:
        """Materialize a frozen API response obtained through read-only polling."""

        status = validate_api_mutation_status(operation_status)
        mutation.refresh_from_db()
        if status["mutation_kind"] != mutation.mutation_kind:
            raise ApiConsistencyError(
                "The API mutation status named a different command."
            )
        if status["status"] == "pending":
            return None
        payload = status["response"]
        http_status = status["http_status"]
        if not isinstance(payload, dict) or not isinstance(http_status, int):
            raise ApiConsistencyError(
                "The completed API mutation status has no frozen response."
            )
        if not 200 <= http_status < 300:
            try:
                envelope = validate_error_envelope(payload)
            except ApiContractError as exc:
                raise ApiConsistencyError(
                    "The completed API mutation error response is malformed."
                ) from exc
            error = envelope["error"]
            return self._persist_polled_final(
                mutation,
                state=ApiMutation.State.REJECTED,
                http_status=http_status,
                payload=envelope,
                error_code=error["code"],
                error_message=error["message"],
            )
        validated, state, error_code, error_message = (
            self._classify_mutation_payload(mutation, payload)
        )
        return self._persist_polled_final(
            mutation,
            state=state,
            http_status=http_status,
            payload=validated,
            error_code=error_code,
            error_message=error_message,
        )

    def _classify_mutation_payload(
        self,
        mutation: ApiMutation,
        payload: dict[str, Any],
    ) -> tuple[dict[str, Any], str, str, str]:
        if mutation.mutation_kind == "register_upload":
            return (
                validate_upload_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_connection_delete":
            if not isinstance(payload, dict) or payload.get("status") != "deleted":
                raise ApiConsistencyError(
                    "CRM connection delete response is malformed."
                )
            return (
                payload,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind.startswith("crm_connection"):
            return (
                validate_crm_connection_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_duplicate_journey_start":
            return (
                validate_crm_duplicate_journey_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_duplicate_population_upload":
            return (
                validate_raw_population_upload_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_duplicate_record_id_mapping":
            return (
                validate_record_id_mapping_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_duplicate_read_grant_create":
            return (
                validate_duplicate_read_grant_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_duplicate_implied_reference_authorization":
            return (
                validate_implied_reference_authorization_result(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "submit_duplicate_review_end_early":
            from .api_contract import validate_duplicate_review_end_early_result

            validated = validate_duplicate_review_end_early_result(payload)
            state = (
                ApiMutation.State.COMPLETED
                if validated.get("outcome") == "accepted"
                else ApiMutation.State.REJECTED
            )
            return (
                validated,
                state,
                validated.get("error_code") or "",
                validated.get("message") or "",
            )
        if mutation.mutation_kind == "submit_duplicate_review_window":
            validated = validate_duplicate_review_window_submit_result(payload)
            state = (
                ApiMutation.State.COMPLETED
                if validated.get("outcome") == "accepted"
                else ApiMutation.State.REJECTED
            )
            return (
                validated,
                state,
                validated.get("error_code") or "",
                validated.get("message") or "",
            )
        if mutation.mutation_kind == "submit_duplicate_auto_disposition":
            # Phase 7B public HTTP returns AutoDispositionResultBody at root
            # (not a command-receipt wrapper).
            return (
                validate_auto_disposition_result(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "prepare_duplicate_reviewed_result":
            return (
                validate_prepare_reviewed_result(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "finalize_duplicate_merge_plan_handoff":
            validated = validate_finalize_merge_plan_handoff_result(payload)
            state = (
                ApiMutation.State.COMPLETED
                if validated.get("outcome") == "accepted"
                else ApiMutation.State.REJECTED
            )
            return (
                validated,
                state,
                validated.get("error_code") or "",
                validated.get("message") or "",
            )
        if mutation.mutation_kind == "amend_duplicate_reviewed_disposition_window":
            validated = validate_amend_reviewed_disposition_window_result(payload)
            state = (
                ApiMutation.State.COMPLETED
                if validated.get("outcome") == "accepted"
                else ApiMutation.State.REJECTED
            )
            return (
                validated,
                state,
                validated.get("error_code") or "",
                validated.get("message") or "",
            )
        if mutation.mutation_kind == "invalidate_duplicate_merge_plan_freeze":
            if not isinstance(payload, dict) or payload.get("outcome") != "accepted":
                raise ApiConsistencyError(
                    "Merge-plan invalidate response is malformed."
                )
            result = payload.get("result")
            if not isinstance(result, dict) or result.get("invalidated") is not True:
                raise ApiConsistencyError(
                    "Merge-plan invalidate receipt is missing invalidated=true."
                )
            return (
                payload,
                ApiMutation.State.COMPLETED,
                payload.get("error_code") or "",
                payload.get("message") or "",
            )
        if mutation.mutation_kind == "crm_query_start":
            return (
                validate_crm_query_resource(payload),
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_query_delete":
            if not isinstance(payload, dict) or payload.get("status") != "deleted":
                raise ApiConsistencyError("CRM query delete response is malformed.")
            return (
                payload,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "crm_app_registration_delete":
            if not isinstance(payload, dict) or payload.get("status") != "deleted":
                raise ApiConsistencyError(
                    "CRM app registration delete response is malformed."
                )
            return (
                payload,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind in _REGISTRATION_MUTATION_KINDS:
            if not isinstance(payload, dict) or not payload.get("provider_key"):
                raise ApiConsistencyError(
                    "CRM app registration response is malformed."
                )
            return (
                payload,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind == "create_run_output_package":
            validated = validate_run_output_package_resource(payload)
            expected_run = str(mutation.resource_identity or "").strip()
            if expected_run and str(validated.get("run_id") or "") != expected_run:
                raise ApiConsistencyError(
                    "Package create response run_id does not match the mutation resource."
                )
            workflow = getattr(mutation, "workflow", None)
            if workflow is not None:
                if int(validated.get("run_revision", -1)) != int(workflow.revision):
                    raise ApiConsistencyError(
                        "Package create response run_revision does not match the workflow."
                    )
                expected_key = str(workflow.workflow_key or "").strip()
                if expected_key and str(validated.get("workflow_key") or "") != expected_key:
                    raise ApiConsistencyError(
                        "Package create response workflow_key does not match the workflow."
                    )
            return (
                validated,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        if mutation.mutation_kind.startswith("column_mapping_plan"):
            # MAP-2/3: create / patch / confirm return plan resources, not receipts.
            validated = self._validate_column_mapping_plan(payload)
            return (
                validated,
                ApiMutation.State.COMPLETED,
                "",
                "",
            )
        validated = validate_command_receipt(payload)
        state = (
            ApiMutation.State.COMPLETED
            if validated["outcome"] == "accepted"
            else ApiMutation.State.REJECTED
        )
        return (
            validated,
            state,
            validated.get("error_code") or "",
            validated.get("message") or "",
        )

    def stream_run_output_package(
        self,
        package_id: str,
        *,
        owner_session: str,
        download_url: str | None = None,
    ) -> requests.Response:
        """Stream ZIP package content with owner session (integrity checked by caller)."""

        self.assert_compatible()
        if download_url:
            absolute = urljoin(self.base_url + "/", download_url.lstrip("/"))
        else:
            absolute = urljoin(
                self.base_url + "/",
                f"v1/run-output-packages/{quote(package_id, safe='')}/content",
            )
        expected = urlparse(self.base_url)
        actual = urlparse(absolute)
        if (
            actual.scheme != expected.scheme
            or actual.netloc != expected.netloc
            or not actual.path.startswith("/v1/run-output-packages/")
            or not actual.path.endswith("/content")
        ):
            raise ApiRejectedError(
                "invalid_package_url",
                "Package URL does not identify the stored run-output package.",
            )
        # Path must include the requested package_id.
        expected_path = (
            f"/v1/run-output-packages/{quote(package_id, safe='')}/content"
        )
        if actual.path != expected_path and actual.path != f"/v1/run-output-packages/{package_id}/content":
            raise ApiRejectedError(
                "invalid_package_url",
                "Package URL does not match the requested package_id.",
            )
        try:
            response = self.http.get(
                absolute,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
                headers={"X-Owner-Session": owner_session},
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError("The package service is unavailable.") from exc
        if 300 <= response.status_code < 400:
            response.close()
            raise ApiRejectedError(
                "package_redirect_rejected", "Package redirects are not allowed."
            )
        if response.status_code != 200:
            response.close()
            raise ApiRejectedError(
                "package_unavailable", "The results package is not available."
            )
        return response

    def stream_artifact(
        self, download_url: str, *, run_id: str, artifact_id: str
    ) -> requests.Response:
        self.assert_compatible()
        absolute = urljoin(self.base_url + "/", download_url.lstrip("/"))
        expected = urlparse(self.base_url)
        actual = urlparse(absolute)
        base_path = expected.path.rstrip("/")
        expected_path = (
            f"{base_path}/v1/workflows/{quote(run_id, safe='')}/artifacts/"
            f"{quote(artifact_id, safe='')}/content"
        )
        if (
            actual.scheme not in {"http", "https"}
            or actual.scheme != expected.scheme
            or actual.netloc != expected.netloc
            or actual.path != expected_path
            or actual.params
            or actual.query
            or actual.fragment
        ):
            raise ApiRejectedError(
                "invalid_artifact_url",
                "Artifact URL does not identify the stored API artifact.",
            )
        try:
            response = self.http.get(
                absolute,
                timeout=self.timeout,
                allow_redirects=False,
                stream=True,
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError("The artifact service is unavailable.") from exc
        if 300 <= response.status_code < 400:
            response.close()
            raise ApiRejectedError(
                "artifact_redirect_rejected", "Artifact redirects are not allowed."
            )
        if response.status_code != 200:
            response.close()
            raise ApiRejectedError(
                "artifact_unavailable", "The artifact is not available."
            )
        return response

    def _send(self, mutation: ApiMutation, *, file_path: Path | None):
        url = urljoin(self.base_url + "/", mutation.route.lstrip("/"))
        headers = {"Idempotency-Key": mutation.idempotency_key}
        if mutation.mutation_kind in {
            "authorize_effect",
            "resume_effect",
            "continue_duplicate_execution",
            "finish_duplicate_execution",
            "submit_decision",
            "crm_duplicate_implied_reference_authorization",
            "crm_connection_oauth_complete",
            "create_run_output_package",
            "create_export_artifacts",
        }:
            headers["Prefer"] = "respond-async"
        owner_session = None
        if isinstance(mutation.request_json, dict):
            owner_session = mutation.request_json.get("owner_session")
        if isinstance(mutation.multipart_metadata, dict):
            owner_session = owner_session or mutation.multipart_metadata.get(
                "owner_session"
            )
        if not owner_session:
            session = getattr(mutation, "session", None)
            owner = getattr(session, "owner_id", None) if session is not None else None
            if owner is not None:
                owner_session = f"django-{owner}"
        if owner_session:
            headers["X-Owner-Session"] = str(owner_session)
        if mutation.mutation_kind in {
            "crm_query_delete",
            "crm_app_registration_delete",
            "crm_connection_delete",
        }:
            # Journaled DELETE (not POST) with the frozen idempotency key.
            return self.http.delete(
                url,
                headers=headers,
                timeout=self.mutation_timeout,
                allow_redirects=False,
            )
        if mutation.request_kind == "multipart":
            if file_path is None or not file_path.exists():
                raise ApiConsistencyError("The frozen upload bytes are unavailable.")
            meta = mutation.multipart_metadata or {}
            handle = open(file_path, "rb")
            try:
                data = dict(meta.get("form", {}))
                files = {
                    "file": (
                        meta["filename"],
                        handle,
                        meta["media_type"],
                    )
                }
                return self.http.post(
                    url,
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=self.mutation_timeout,
                    allow_redirects=False,
                )
            finally:
                handle.close()
        body = mutation.request_json
        # Owner session is a header (X-Owner-Session), never a StrictModel body
        # field. Journal may freeze it for identity/replay; strip before wire POST
        # so openAPI additionalProperties:forbid routes (review start, finalize,
        # authorize, invalidate, etc.) accept the request.
        if isinstance(body, dict) and "owner_session" in body:
            if not headers.get("X-Owner-Session"):
                headers["X-Owner-Session"] = str(body.get("owner_session") or "")
            body = {
                key: value for key, value in body.items() if key != "owner_session"
            }
        if (
            mutation.mutation_kind == "submit_duplicate_auto_disposition"
            and isinstance(body, dict)
            and "source_run_id" in body
        ):
            body = {
                key: value for key, value in body.items() if key != "source_run_id"
            }
        # CRM query / journey effect paths: derive owner session from the Django
        # ImportSession owner when the frozen body does not carry one.
        if not headers.get("X-Owner-Session"):
            session = getattr(mutation, "session", None)
            owner = getattr(session, "owner_id", None) if session is not None else None
            if owner is not None:
                headers["X-Owner-Session"] = f"django-{owner}"
        # Live OAuth: inject process-local code; never read a durable raw code.
        if (
            mutation.mutation_kind == "crm_connection_oauth_complete"
            and isinstance(body, dict)
        ):
            body = dict(body)
            ephemeral = _EPHEMERAL_OAUTH_CODES.get(str(mutation.id))
            if ephemeral:
                body["authorization_code"] = ephemeral
                body.pop("authorization_code_digest", None)
            elif not str(body.get("authorization_code") or "").strip():
                # Fail closed: live codes are not re-durable; exact-retry after
                # process loss requires a new OAuth connect.
                raise ApiConsistencyError(
                    "OAuth authorization code is no longer available for dispatch. "
                    "Start a new CRM connection."
                )
        if (
            mutation.mutation_kind == "crm_connection_reclaim"
            and isinstance(body, dict)
        ):
            body = dict(body)
            ephemeral = _EPHEMERAL_RECLAIM_HANDLES.get(str(mutation.id))
            if ephemeral:
                body["reclaim_handle"] = ephemeral
                body.pop("reclaim_handle_digest", None)
                body.pop("reclaim_offer_id", None)
            elif not str(body.get("reclaim_handle") or "").strip():
                raise ApiConsistencyError(
                    "Reclaim handle is no longer available for dispatch. "
                    "Start a new CRM connection."
                )
        # Phase 0A-J: inject registration secrets; journal holds digests only.
        if (
            mutation.mutation_kind in _REGISTRATION_MUTATION_KINDS
            and mutation.mutation_kind != "crm_app_registration_delete"
            and isinstance(body, dict)
        ):
            body = dict(body)
            ephemeral = _EPHEMERAL_REGISTRATION_SECRETS.get(str(mutation.id)) or {}
            if "client_secret" in ephemeral:
                body["client_secret"] = ephemeral["client_secret"]
            if "access_token" in ephemeral:
                body["access_token"] = ephemeral["access_token"]
            # Journal-only digests are not wire fields (OpenAPI additionalProperties
            # false). Always strip before POST so dual-process / real API accepts.
            body.pop("client_secret_digest", None)
            body.pop("access_token_digest", None)
            needs_secret = mutation.mutation_kind.endswith("_put") or mutation.mutation_kind.endswith(
                "_rotate_secret"
            )
            if needs_secret and not (
                str(body.get("client_secret") or "").strip()
                or str(body.get("access_token") or "").strip()
            ):
                raise ApiConsistencyError(
                    "CRM application secret is no longer available for dispatch. "
                    "Submit the registration form again."
                )
        return self.http.post(
            url,
            headers={**headers, "Content-Type": "application/json"},
            json=body,
            timeout=self.mutation_timeout,
            allow_redirects=False,
        )

    def _get_json(
        self,
        route: str,
        validator: Callable[[Any], dict[str, Any]],
        *,
        headers: dict[str, str] | None = None,
    ):
        url = urljoin(self.base_url + "/", route.lstrip("/"))
        try:
            response = self.http.get(
                url,
                timeout=self.timeout,
                allow_redirects=False,
                headers=headers or None,
            )
        except requests.RequestException as exc:
            raise ApiUnavailableError(
                "The EasyImports API is unavailable.",
                route=route,
            ) from exc
        if 300 <= response.status_code < 400:
            raise ApiUnavailableError(
                "The EasyImports API redirected unexpectedly.",
                route=route,
                http_status=response.status_code,
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise ApiUnavailableError(
                "The EasyImports API returned malformed JSON.",
                route=route,
                http_status=response.status_code,
            ) from exc
        if response.status_code in {404, 409, 422}:
            try:
                error = validate_error_envelope(payload)["error"]
            except ApiContractError as exc:
                raise ApiUnavailableError(
                    "The API error response was malformed.",
                    route=route,
                    http_status=response.status_code,
                ) from exc
            raise ApiRejectedError(
                error["code"],
                error["message"],
                details=error.get("details"),
                route=route,
                http_status=response.status_code,
                error_id=error.get("error_id"),
            )
        if response.status_code >= 500 or not 200 <= response.status_code < 300:
            err_id, err_code, err_msg = _error_body_fields(payload)
            raise ApiUnavailableError(
                err_msg or "The EasyImports API request failed.",
                route=route,
                http_status=response.status_code,
                error_id=err_id,
                error_code=err_code,
            )
        try:
            return validator(payload)
        except (ApiContractError, TypeError, KeyError) as exc:
            raise ApiUnavailableError(
                "The EasyImports API contract is incompatible.",
                route=route,
                http_status=response.status_code,
            ) from exc

    def _acquire_lease(self, mutation: ApiMutation) -> UUID:
        token = uuid4()
        updated = (
            ApiMutation.objects.filter(
                pk=mutation.pk,
                state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
            )
            .filter(Q(lease_expires_at__isnull=True) | Q(lease_expires_at__lte=Now()))
            .update(
                lease_token=token,
                lease_expires_at=Now() + timedelta(seconds=self.lease_seconds),
                dispatched_at=Now(),
                attempt_count=F("attempt_count") + 1,
            )
        )
        if updated != 1:
            raise MutationBusyError("This exact action is already being sent.")
        return token

    def _persist_final(
        self,
        mutation: ApiMutation,
        lease_token: UUID,
        *,
        state: str,
        http_status: int,
        payload: dict[str, Any],
        error_code: str,
        error_message: str,
    ) -> MutationDispatchResult:
        persist_payload = scrub_reclaim_handle_from_envelope(payload)
        digest = canonical_digest(persist_payload)
        # JSON null for optional identity fields must not violate NOT NULL
        # CharField constraints on the mutation journal.
        upload_id = str(payload.get("upload_id") or "")
        run_id = str(payload.get("run_id") or "")
        updated = ApiMutation.objects.filter(
            pk=mutation.pk,
            lease_token=lease_token,
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
        ).update(
            state=state,
            http_status=http_status,
            response_json=persist_payload,
            response_digest=digest,
            error_code=error_code,
            error_message=error_message,
            result_upload_id=upload_id,
            result_run_id=run_id,
            lease_token=None,
            lease_expires_at=None,
            updated_at=Now(),
        )
        mutation.refresh_from_db()
        if updated == 1:
            return MutationDispatchResult(mutation, payload)
        if (
            mutation.state in {ApiMutation.State.COMPLETED, ApiMutation.State.REJECTED}
            and mutation.response_digest == digest
            and mutation.response_json == persist_payload
        ):
            return MutationDispatchResult(mutation, payload)
        raise ApiConsistencyError(
            "A superseded mutation attempt returned inconsistent evidence."
        )

    def _persist_processing(
        self,
        mutation: ApiMutation,
        lease_token: UUID,
        *,
        http_status: int,
        response_digest: str,
        payload: dict[str, Any],
    ) -> None:
        updated = ApiMutation.objects.filter(
            pk=mutation.pk,
            lease_token=lease_token,
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
        ).update(
            state=ApiMutation.State.PENDING,
            http_status=http_status,
            response_json=payload,
            response_digest=response_digest,
            error_code="",
            error_message="",
            lease_token=None,
            lease_expires_at=None,
            updated_at=Now(),
        )
        mutation.refresh_from_db()
        if updated == 1:
            return
        if (
            mutation.state == ApiMutation.State.PENDING
            and mutation.http_status in {202, 409}
        ):
            return
        raise ApiConsistencyError(
            "A superseded mutation attempt changed processing state."
        )

    def _persist_polled_final(
        self,
        mutation: ApiMutation,
        *,
        state: str,
        http_status: int,
        payload: dict[str, Any],
        error_code: str,
        error_message: str,
    ) -> MutationDispatchResult:
        persist_payload = scrub_reclaim_handle_from_envelope(payload)
        digest = canonical_digest(persist_payload)
        upload_id = str(payload.get("upload_id") or "")
        run_id = str(payload.get("run_id") or "")
        updated = ApiMutation.objects.filter(
            pk=mutation.pk,
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
        ).update(
            state=state,
            http_status=http_status,
            response_json=persist_payload,
            response_digest=digest,
            error_code=error_code,
            error_message=error_message,
            result_upload_id=upload_id,
            result_run_id=run_id,
            lease_token=None,
            lease_expires_at=None,
            updated_at=Now(),
        )
        mutation.refresh_from_db()
        if updated == 1:
            return MutationDispatchResult(mutation, payload)
        if (
            mutation.state in {
                ApiMutation.State.COMPLETED,
                ApiMutation.State.REJECTED,
            }
            and mutation.response_digest == digest
            and mutation.response_json == persist_payload
        ):
            return MutationDispatchResult(mutation, payload)
        raise ApiConsistencyError(
            "A polled mutation completion changed its frozen response."
        )

    def _persist_unknown(
        self,
        mutation: ApiMutation,
        lease_token: UUID,
        *,
        http_status: int | None = None,
        response_digest: str = "",
        payload: dict[str, Any] | None = None,
        message: str,
    ) -> None:
        ApiMutation.objects.filter(
            pk=mutation.pk,
            lease_token=lease_token,
            state__in=[ApiMutation.State.PENDING, ApiMutation.State.UNKNOWN],
        ).update(
            state=ApiMutation.State.UNKNOWN,
            http_status=http_status,
            response_digest=response_digest,
            response_json=payload,
            error_message=message,
            lease_token=None,
            lease_expires_at=None,
            updated_at=Now(),
        )

    def _unknown_contract_response(
        self,
        mutation: ApiMutation,
        lease_token: UUID,
        status: int,
        digest: str,
        payload: dict[str, Any],
        exc: Exception,
    ) -> MutationDispatchResult:
        self._persist_unknown(
            mutation,
            lease_token,
            http_status=status,
            response_digest=digest,
            payload=payload,
            message=str(exc),
        )
        raise ApiUnavailableError(GENERIC_UNCERTAIN_MESSAGE)
