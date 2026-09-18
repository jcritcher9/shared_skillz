"""OUT-8 mutation-kind pending-card copy (Django-only; no mappings_2 import).

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_operator_output_and_settings_clarity.md
"""

from __future__ import annotations

from typing import Any, Final

from .uncertain_mutation_copy import retry_body_for_mutation

SETUP_HEADLINE: Final[str] = "Setup is all done!"
SETUP_BODY: Final[str] = (
    "We are running your cleaning and importing now. "
    "Please check back shortly for your results."
)
UNKNOWN_HEADLINE: Final[str] = "Action needs confirmation"
UNKNOWN_BODY: Final[str] = (
    "The result of your last action is uncertain. "
    "Retry the same saved request to recover safely."
)
PACKAGE_HEADLINE: Final[str] = "Preparing your results"
PACKAGE_BODY: Final[str] = (
    "Files are being packaged. This does not change CRM records."
)
EXPORT_HEADLINE: Final[str] = "Preparing download files"
EXPORT_BODY: Final[str] = (
    "Downloadable preview files are being created. "
    "This does not change CRM records."
)
MERGE_HEADLINE: Final[str] = "Authorizing CRM merges"
MERGE_BODY: Final[str] = (
    "Your confirmed winners are ready. Choose a dry run to verify "
    "the merge plan with zero CRM changes, or apply merges only when "
    "that option is explicitly available. Recommendation was never "
    "approval — only the survivors you confirmed will be merged. "
    "This step can change CRM records only if execute was chosen."
)
GENERIC_HEADLINE: Final[str] = "Action in progress"
GENERIC_BODY: Final[str] = (
    "EasyImports is processing this action. "
    "This page will update automatically when it finishes."
)
STILL_WORKING_BODY: Final[str] = (
    "EasyImports is still working. Status will be checked again shortly."
)

# Mirrors workflow_views._effect_presentation write-track wording.
_WRITE_TRACK_BODIES: Final[dict[str, str]] = {
    "account_provisioning": (
        "Review the companies listed below (one row per company). You may "
        "skip Account creation entirely (those people become Leads), "
        "preview the plan, test without CRM changes, or apply creates when "
        "authorized. Account creation is optional."
    ),
    "reference_acquisition": (
        "Read the connected CRM records needed to find and rank "
        "duplicate groups. This step does not merge or change records."
    ),
    "delivery": (
        "Create downloadable preview files for this run. "
        "This step does not change CRM records."
    ),
}
_DEFAULT_WRITE_BODY: Final[str] = (
    "Select a preview or dry run to review the result without CRM changes, "
    "or apply changes only when that option is explicitly available."
)

SETUP_KINDS: Final[frozenset[str]] = frozenset({"create_workflow"})
PACKAGE_KINDS: Final[frozenset[str]] = frozenset({"create_run_output_package"})
EXPORT_KINDS: Final[frozenset[str]] = frozenset({"create_export_artifacts"})
AUTHORIZE_KIND: Final[str] = "authorize_effect"
CLEANING_MARKERS: Final[tuple[str, ...]] = (
    "Setup is all done",
    "cleaning and importing",
)


def effect_track(mutation: Any) -> str:
    body = getattr(mutation, "request_json", None)
    if not isinstance(body, dict):
        return ""
    return str(body.get("track") or "").strip()


def pending_copy_for_mutation(mutation: Any) -> dict[str, str]:
    """Return {headline, body} for one pending/unknown mutation card."""

    state = str(getattr(mutation, "state", "") or "")
    if state == "unknown":
        diagnostic = retry_body_for_mutation(mutation)
        return {
            "headline": UNKNOWN_HEADLINE,
            "body": diagnostic or UNKNOWN_BODY,
        }
    kind = str(getattr(mutation, "mutation_kind", "") or "")
    if kind in SETUP_KINDS:
        return {"headline": SETUP_HEADLINE, "body": SETUP_BODY}
    if kind in PACKAGE_KINDS:
        return {"headline": PACKAGE_HEADLINE, "body": PACKAGE_BODY}
    if kind in EXPORT_KINDS:
        return {"headline": EXPORT_HEADLINE, "body": EXPORT_BODY}
    if kind == AUTHORIZE_KIND:
        track = effect_track(mutation)
        if track == "duplicate_execution":
            return {"headline": MERGE_HEADLINE, "body": MERGE_BODY}
        body = _WRITE_TRACK_BODIES.get(track, _DEFAULT_WRITE_BODY)
        return {"headline": GENERIC_HEADLINE, "body": body}
    return {"headline": GENERIC_HEADLINE, "body": GENERIC_BODY}


def attach_pending_copy(mutation: Any) -> Any:
    mutation.pending_copy = pending_copy_for_mutation(mutation)
    return mutation


def in_progress_flash_message(mutation: Any) -> str:
    """Kind-specific 202 flash. Never uses setup copy for write/package."""

    copy = pending_copy_for_mutation(mutation)
    return copy["body"]


def contains_cleaning_sentence(text: str) -> bool:
    lowered = str(text or "")
    return any(marker in lowered for marker in CLEANING_MARKERS)


def html_has_pending_mutation_surface(html: str) -> bool:
    """True when HTML still shows a pending or unknown mutation card.

    Dual-process probes must use the OUT-8 hooks, not retired
    ``Action in progress`` copy. UNKNOWN still uses its own headline.
    """

    text = str(html or "")
    if "data-mutation-pending" in text:
        return True
    if "data-mutation-kind=" in text:
        return True
    if "data-mutation-status-url=" in text:
        return True
    if "Action needs confirmation" in text:
        return True
    if "processing this merge" in text.lower():
        return True
    return False
