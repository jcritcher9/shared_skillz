"""DDR-5 timeout-specific uncertain-mutation operator copy.

Django-only; no mappings_2 import. Action labels are a closed allowlist.
Raw routes, mutation kinds, request bodies, and exception text never enter
operator copy.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    completed_projects/crm_duplicate_discovery_and_review_performance.md
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any, Final, Iterable

import requests

GENERIC_UNCERTAIN_MESSAGE: Final[str] = (
    "The API response is uncertain. Retry this exact action."
)
GENERIC_PROGRESSIVE: Final[str] = "This saved action"
GENERIC_NOUN: Final[str] = "request"
_RETRY_SAVED_PREFIX: Final[str] = (
    "Retry this saved action; it uses the same idempotency key and will not "
    "create a second "
)
_CONNECT_UNCERTAIN_SUFFIX: Final[str] = (
    f" {GENERIC_UNCERTAIN_MESSAGE}"
)
_GENERIC_CONFIRMED_SUFFIX: Final[str] = (
    f" could not be confirmed. {GENERIC_UNCERTAIN_MESSAGE}"
)


@dataclass(frozen=True, slots=True)
class MutationActionCopy:
    progressive: str
    noun: str


# Closed allowlist. Unknown kinds use GENERIC_PROGRESSIVE / GENERIC_NOUN.
MUTATION_ACTION_COPY: Final[dict[str, MutationActionCopy]] = {
    "start_review_workflow": MutationActionCopy(
        "Starting your review", "review"
    ),
    "submit_duplicate_review_window": MutationActionCopy(
        "Saving your review choices", "review window"
    ),
    "submit_duplicate_auto_disposition": MutationActionCopy(
        "Auto-approving high-confidence groups", "auto-approval"
    ),
    "create_workflow": MutationActionCopy("Starting your import", "import"),
    "register_upload": MutationActionCopy("Uploading your file", "upload"),
    "submit_decision": MutationActionCopy(
        "Saving your review decision", "decision"
    ),
    "authorize_effect": MutationActionCopy(
        "Authorizing this CRM step", "authorization"
    ),
    "resume_effect": MutationActionCopy("Resuming this CRM step", "resume"),
    "recover_reference_acquisition": MutationActionCopy(
        "Recovering the CRM read", "recovery"
    ),
    "abandon_run": MutationActionCopy("Stopping this journey", "stop"),
    "create_export_artifacts": MutationActionCopy(
        "Preparing download files", "download"
    ),
    "create_run_output_package": MutationActionCopy(
        "Preparing your results package", "package"
    ),
    "delete_run_output_package": MutationActionCopy(
        "Removing a results package", "package removal"
    ),
    "create_decision_set_handoff": MutationActionCopy(
        "Saving reviewed results", "reviewed-result save"
    ),
    "bind_decision_set_handoff": MutationActionCopy(
        "Binding the merge plan", "merge-plan bind"
    ),
    "finalize_duplicate_merge_plan_handoff": MutationActionCopy(
        "Approving the merge plan", "merge-plan approval"
    ),
    "amend_duplicate_reviewed_disposition_window": MutationActionCopy(
        "Updating reviewed groups", "review update"
    ),
    "prepare_duplicate_reviewed_result": MutationActionCopy(
        "Preparing the merge plan", "reviewed-result prepare"
    ),
    "invalidate_duplicate_merge_plan_freeze": MutationActionCopy(
        "Returning to group review", "go-back"
    ),
    "crm_duplicate_journey_start": MutationActionCopy(
        "Starting duplicate analysis", "analysis"
    ),
    "crm_duplicate_population_upload": MutationActionCopy(
        "Uploading records to analyze", "upload"
    ),
    "crm_duplicate_record_id_mapping": MutationActionCopy(
        "Saving the record ID map", "mapping"
    ),
    "crm_duplicate_read_grant_create": MutationActionCopy(
        "Authorizing the CRM read", "read grant"
    ),
    "crm_duplicate_implied_reference_authorization": MutationActionCopy(
        "Reading CRM records", "CRM read"
    ),
    "crm_query_start": MutationActionCopy("Starting the CRM query", "query"),
    "crm_query_delete": MutationActionCopy(
        "Removing the CRM query", "query removal"
    ),
    "crm_connection_start": MutationActionCopy(
        "Connecting your CRM", "connection"
    ),
    "crm_connection_oauth_complete": MutationActionCopy(
        "Completing CRM authorization", "authorization"
    ),
    "crm_connection_disconnect": MutationActionCopy(
        "Disconnecting your CRM", "disconnect"
    ),
    "crm_connection_delete": MutationActionCopy(
        "Removing the CRM connection", "connection removal"
    ),
    "crm_connection_remove_with_dependents": MutationActionCopy(
        "Removing the CRM connection", "connection removal"
    ),
    "crm_connection_reclaim": MutationActionCopy(
        "Reclaiming this CRM connection", "reclaim"
    ),
    "crm_app_registration_salesforce_put": MutationActionCopy(
        "Saving Salesforce app registration", "registration"
    ),
    "crm_app_registration_salesforce_rotate_secret": MutationActionCopy(
        "Updating the Salesforce app secret", "secret update"
    ),
    "crm_app_registration_hubspot_put": MutationActionCopy(
        "Saving HubSpot app registration", "registration"
    ),
    "crm_app_registration_hubspot_rotate_secret": MutationActionCopy(
        "Updating the HubSpot app secret", "secret update"
    ),
    "crm_app_registration_delete": MutationActionCopy(
        "Removing the CRM app registration", "registration removal"
    ),
    "column_mapping_plan_create": MutationActionCopy(
        "Starting column mapping", "mapping"
    ),
    "column_mapping_plan_patch_row": MutationActionCopy(
        "Saving a column mapping", "mapping save"
    ),
    "column_mapping_plan_confirm": MutationActionCopy(
        "Confirming column mapping", "mapping confirm"
    ),
    "column_mapping_plan_atomic_review": MutationActionCopy(
        "Saving column mapping review", "mapping review"
    ),
}

_INTEGER_SECONDS_RE: Final[re.Pattern[str]] = re.compile(r"(?:0|[1-9]\d*)")
_GENERIC_COPY: Final[MutationActionCopy] = MutationActionCopy(
    GENERIC_PROGRESSIVE, GENERIC_NOUN
)


def action_copy_for_kind(mutation_kind: str) -> MutationActionCopy:
    """Return allowlisted copy. Unknown kinds never echo the raw kind."""

    return MUTATION_ACTION_COPY.get(str(mutation_kind or ""), _GENERIC_COPY)


def format_timeout_seconds(value: float) -> str:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise ValueError("timeout seconds must be finite and greater than 0")
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.1f}"


def format_elapsed_seconds(value: float) -> str:
    """Whole-second measured wait. Zero is valid; negative or non-finite is not."""

    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise ValueError("elapsed seconds must be finite and at least 0")
    return str(int(round(number)))


def _parse_display_seconds(raw: str) -> float | None:
    if _INTEGER_SECONDS_RE.fullmatch(raw) is None:
        return None
    value = float(int(raw))
    if format_elapsed_seconds(value) != raw:
        return None
    return value


def _parse_configured_seconds(raw: str) -> float | None:
    if re.fullmatch(r"(?:0|[1-9]\d*)(?:\.\d)?", raw) is None:
        return None
    try:
        value = float(raw)
        if format_timeout_seconds(value) != raw:
            return None
    except ValueError:
        return None
    return value


def classify_dispatch_exception(exc: BaseException) -> str:
    """Classify a transport exception. Read vs connect is exact, not inferred."""

    if isinstance(exc, requests.ReadTimeout):
        return "read_timeout"
    if isinstance(exc, requests.ConnectTimeout):
        return "connect_timeout"
    return "uncertain"


def _read_timeout_message_for_copy(
    copy: MutationActionCopy, *, elapsed_seconds: float
) -> str:
    seconds = format_elapsed_seconds(elapsed_seconds)
    return (
        f"{copy.progressive} is taking longer than {seconds} seconds. "
        f"{_RETRY_SAVED_PREFIX}{copy.noun}."
    )


def _connect_timeout_message_for_copy(
    copy: MutationActionCopy, *, connect_timeout: float
) -> str:
    seconds = format_timeout_seconds(connect_timeout)
    return (
        f"{copy.progressive} could not connect to EasyImports within "
        f"{seconds} seconds.{_CONNECT_UNCERTAIN_SUFFIX}"
    )


def _generic_uncertain_message_for_copy(copy: MutationActionCopy) -> str:
    return f"{copy.progressive}{_GENERIC_CONFIRMED_SUFFIX}"


def read_timeout_message(mutation_kind: str, *, elapsed_seconds: float) -> str:
    return _read_timeout_message_for_copy(
        action_copy_for_kind(mutation_kind), elapsed_seconds=elapsed_seconds
    )


def connect_timeout_message(mutation_kind: str, *, connect_timeout: float) -> str:
    return _connect_timeout_message_for_copy(
        action_copy_for_kind(mutation_kind), connect_timeout=connect_timeout
    )


def generic_uncertain_message(mutation_kind: str | None = None) -> str:
    if mutation_kind is None:
        return GENERIC_UNCERTAIN_MESSAGE
    return _generic_uncertain_message_for_copy(action_copy_for_kind(mutation_kind))


def uncertain_mutation_message(
    *,
    mutation_kind: str,
    exc: BaseException,
    elapsed_seconds: float,
    connect_timeout: float,
    read_timeout: float,
) -> str:
    """Build persisted/operator copy from classified timeout + allowlisted action.

    Read-timeout copy interpolates the measured wait. Configured ``read_timeout``
    is the dispatch budget only; it is never the operator duration. Connect
    timeout copy uses the configured connect budget.
    """

    kind = classify_dispatch_exception(exc)
    if kind == "read_timeout":
        try:
            format_timeout_seconds(read_timeout)
            return read_timeout_message(
                mutation_kind, elapsed_seconds=elapsed_seconds
            )
        except (TypeError, ValueError):
            return generic_uncertain_message(mutation_kind)
    if kind == "connect_timeout":
        try:
            return connect_timeout_message(
                mutation_kind, connect_timeout=connect_timeout
            )
        except (TypeError, ValueError):
            return generic_uncertain_message(mutation_kind)
    return generic_uncertain_message(mutation_kind)


def _action_copies_for_kind(mutation_kind: str | None) -> Iterable[MutationActionCopy]:
    if mutation_kind is None:
        yielded: list[MutationActionCopy] = []
        seen: set[tuple[str, str]] = set()
        for spec in (*MUTATION_ACTION_COPY.values(), _GENERIC_COPY):
            key = (spec.progressive, spec.noun)
            if key in seen:
                continue
            seen.add(key)
            yielded.append(spec)
        return yielded
    return (action_copy_for_kind(mutation_kind),)


def _seconds_between(
    text: str,
    prefix: str,
    suffix: str,
    *,
    parser=_parse_display_seconds,
) -> float | None:
    if not text.startswith(prefix) or not text.endswith(suffix):
        return None
    raw = text[len(prefix) : len(text) - len(suffix)]
    return parser(raw)


def is_safe_uncertain_diagnostic(
    message: str, *, mutation_kind: str | None = None
) -> bool:
    """True only when ``message`` equals a reconstructed DDR-5 sentence."""

    text = str(message or "")
    if not text or text != text.strip():
        return False
    if text == GENERIC_UNCERTAIN_MESSAGE:
        return True
    for copy in _action_copies_for_kind(mutation_kind):
        if text == _generic_uncertain_message_for_copy(copy):
            return True
        elapsed = _seconds_between(
            text,
            f"{copy.progressive} is taking longer than ",
            f" seconds. {_RETRY_SAVED_PREFIX}{copy.noun}.",
        )
        if elapsed is not None and text == _read_timeout_message_for_copy(
            copy, elapsed_seconds=elapsed
        ):
            return True
        connect = _seconds_between(
            text,
            f"{copy.progressive} could not connect to EasyImports within ",
            f" seconds.{_CONNECT_UNCERTAIN_SUFFIX}",
            parser=_parse_configured_seconds,
        )
        if connect is not None:
            try:
                reconstructed = _connect_timeout_message_for_copy(
                    copy, connect_timeout=connect
                )
            except ValueError:
                continue
            if text == reconstructed:
                return True
    return False


def retry_body_for_mutation(mutation: Any) -> str | None:
    """Persisted safe diagnostic for UNKNOWN retry cards, else None."""

    diagnostic = str(getattr(mutation, "error_message", "") or "")
    kind = str(getattr(mutation, "mutation_kind", "") or "")
    if is_safe_uncertain_diagnostic(diagnostic, mutation_kind=kind):
        return diagnostic
    return None
