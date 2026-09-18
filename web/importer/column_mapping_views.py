"""Django preflight UI for operator column mapping (MAP-2 + MAP-R4).

Session-bound after upload: real headers + destination. Journaled mutations
with rejected-result fail-closed handling and content-digest-versioned confirm.
MAP-R4: one stable table, focused field picker, atomic multi-row review, and a
no-JavaScript single-form fallback. Web must not import mappings_2 runtime.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import UUID, uuid4

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .api_client import (
    ApiRejectedError,
    ApiUnavailableError,
    EasyImportsApiClient,
    MutationDispatchResult,
    MutationExplicitRetryRequired,
    MutationReuseError,
    create_or_reuse_mutation,
)
from .models import ApiMutation, ImportSession, SourceFile
from .setup_service import (
    authoritative_mapping_source,
    mapping_auto_start_ready,
    read_operator_intent,
)
from .workflow_state import (
    FormTokenError,
    issue_form_token,
    owned_session_or_404,
    owner_id_for_request,
    owner_session_for_import_session,
    validate_form_token,
)

SYSTEM_IGNORE = "system:ignore"
# MAP-1 stub (legacy plans only). MAP-R1 product paths use context catalogs.
DEFAULT_CATALOG_ID = "easyimports.default_fields.v1"
CATALOG_ACCOUNT_FIELDS_V1 = "easyimports.account_fields.v1"
CATALOG_CONTACT_FIELDS_V1 = "easyimports.contact_fields.v1"
CATALOG_LEAD_FIELDS_V1 = "easyimports.lead_fields.v1"
CATALOG_PEOPLE_MATCHING_FIELDS_V1 = "easyimports.people_matching_fields.v1"
MAP_OPTIONS_KEY = "column_mapping_map2"

# MAP-R6: fake + Salesforce + HubSpot offline write inventories.
_SUPPORTED_CRM_PROVIDER_KEYS = {
    "fake": "fake-crm-phase0b-v1",
    "fake-crm-phase0b-v1": "fake-crm-phase0b-v1",
    "salesforce": "salesforce",
    "hubspot": "hubspot",
}

# Mirrors mappings_2.canon.column_mapping.freezes.map_r0.CATALOG_ROUTING_V1
# (web must not import mappings_2 runtime). people_output is setup singular.
_CATALOG_ROUTING = {
    ("accounts", "clean_only", ""): CATALOG_ACCOUNT_FIELDS_V1,
    ("accounts", "crm_matching", ""): CATALOG_ACCOUNT_FIELDS_V1,
    ("people", "clean_only", "contact"): CATALOG_CONTACT_FIELDS_V1,
    ("people", "clean_only", "lead"): CATALOG_LEAD_FIELDS_V1,
    ("people", "crm_matching", "contact"): CATALOG_PEOPLE_MATCHING_FIELDS_V1,
    ("people", "crm_matching", "lead"): CATALOG_PEOPLE_MATCHING_FIELDS_V1,
    ("people", "crm_matching", ""): CATALOG_PEOPLE_MATCHING_FIELDS_V1,
}


def _normalize_people_output(people_output: str | None) -> str:
    raw = str(people_output or "").strip().lower()
    if raw in {"contact", "contacts"}:
        return "contact"
    if raw in {"lead", "leads"}:
        return "lead"
    return raw


def _catalog_id_for_setup(
    *,
    entity: str | None,
    operation: str | None,
    people_output: str | None,
    vocabulary: str | None = None,
) -> str:
    """Resolve catalog id for setup + vocabulary; fail closed on unfrozen combos.

    MAP-R0 product catalogs when vocabulary is product; CMX-2 provider-labelled
    ingest catalogs when vocabulary is a CRM dialect.
    """

    from .vocabulary_intent import resolve_catalog_id_for_setup_vocabulary

    return resolve_catalog_id_for_setup_vocabulary(
        entity=entity,
        operation=operation,
        people_output=people_output,
        vocabulary=vocabulary,
    )


def _owner_id(request) -> UUID:
    owner = owner_id_for_request(request, create=True)
    assert owner is not None
    return owner


def _owner_session(owner_id: UUID) -> str:
    return f"django-{owner_id}"


def _draft(session: ImportSession) -> dict[str, Any]:
    options = session.options if isinstance(session.options, dict) else {}
    raw = options.get(MAP_OPTIONS_KEY)
    return dict(raw) if isinstance(raw, dict) else {}


def _save_draft(session: ImportSession, draft: dict[str, Any]) -> None:
    options = dict(session.options) if isinstance(session.options, dict) else {}
    options[MAP_OPTIONS_KEY] = dict(draft)
    session.options = options
    session.save(update_fields=["options", "updated_at"])


def _bump_create_generation(draft: dict[str, Any]) -> int:
    """Advance create generation so map-again cannot replay a completed create."""

    next_gen = int(draft.get("create_generation") or 0) + 1
    draft["create_generation"] = next_gen
    return next_gen


def _create_generation(draft: dict[str, Any]) -> int:
    try:
        return max(0, int(draft.get("create_generation") or 0))
    except (TypeError, ValueError):
        return 0


def _api() -> EasyImportsApiClient:
    return EasyImportsApiClient()


def _destination_fingerprint(destination: dict[str, Any]) -> str:
    """Stable fingerprint for draft reuse (all destination fields that bind digests)."""

    payload = {
        "destination_mode": destination.get("destination_mode"),
        "catalog_id": destination.get("catalog_id"),
        "provider_key": destination.get("provider_key"),
        "connection_id": destination.get("connection_id"),
        "object_keys": list(destination.get("object_keys") or []),
        "operation_key": destination.get("operation_key"),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _primary_source(session: ImportSession) -> SourceFile | None:
    """Authoritative mapping source for this route (R19)."""

    return authoritative_mapping_source(session)


def _source_schema_from_upload(source: SourceFile) -> list[dict[str, Any]]:
    columns = source.columns if isinstance(source.columns, list) else []
    headers = [str(c) for c in columns]
    if not headers:
        raise ValueError("Uploaded file has no column headers.")
    # MAP-R3: UI-only samples from retained upload; mismatch → empty samples.
    from .column_mapping_samples import extract_samples_for_source

    samples_by_col: list[list[str]] = [[] for _ in headers]
    try:
        path = source.path
        if path.is_file():
            sheet = source.xlsx_sheet
            sheet_index = 0
            if isinstance(sheet, int):
                sheet_index = sheet
            samples_by_col = extract_samples_for_source(
                path=path,
                expected_headers=headers,
                encoding=source.csv_encoding or "utf-8",
                xlsx_sheet=sheet_index if path.suffix.lower() == ".xlsx" else sheet,
                media_type=source.media_type or "",
                original_name=source.original_name or "",
            )
            if len(samples_by_col) != len(headers):
                samples_by_col = [[] for _ in headers]
    except OSError:
        samples_by_col = [[] for _ in headers]
    return [
        {
            "source_ordinal": i,
            "source_header": h,
            "sample_values": list(samples_by_col[i]) if i < len(samples_by_col) else [],
        }
        for i, h in enumerate(headers)
    ]


def _destination_for_session(
    session: ImportSession,
    *,
    owner_session: str,
    client: EasyImportsApiClient | None = None,
) -> dict[str, Any]:
    """Context catalog by setup intent; CRM when matching a connected CRM.

    Fail closed on incomplete/unfrozen setup and on CRM resolution errors —
    never silently substitute a different destination mode or catalog.
    """

    intent = read_operator_intent(session)
    connection_id = (session.setup_connection_id or "").strip()
    vocabulary = str(getattr(session, "setup_vocabulary", "") or "").strip() or None
    if not (
        intent is not None
        and intent.operation == "crm_matching"
        and intent.reference_source == "connected_crm"
        and connection_id
    ):
        if intent is not None:
            catalog_id = _catalog_id_for_setup(
                entity=intent.entity,
                operation=intent.operation,
                people_output=intent.people_output,
                vocabulary=vocabulary,
            )
        else:
            # Incomplete or invalid setup fields — still fail closed (no default).
            catalog_id = _catalog_id_for_setup(
                entity=getattr(session, "setup_entity", None),
                operation=getattr(session, "setup_operation", None),
                people_output=getattr(session, "setup_people_output", None),
                vocabulary=vocabulary,
            )
        return {
            "destination_mode": "catalog",
            "catalog_id": catalog_id,
        }

    api = client or _api()
    try:
        connection = api.crm_connection(connection_id, owner_session=owner_session)
    except ApiRejectedError as exc:
        raise ValueError(
            f"Cannot resolve CRM connection for mapping: {exc.message} "
            "Reconnect the CRM or change setup before mapping columns."
        ) from exc
    except ApiUnavailableError as exc:
        raise ValueError(
            "Cannot resolve CRM connection for mapping: API unavailable. "
            "Retry when the service is up; mapping will not switch to catalog mode."
        ) from exc

    status = str(connection.get("status") or "").strip()
    if status != "connected":
        raise ValueError(
            f"CRM connection {connection_id!r} is not connected "
            f"(status={status or 'unknown'!r}). Connect it before mapping columns. "
            "Mapping will not fall back to a catalog destination."
        )

    raw_provider = str(connection.get("provider_key") or "").strip()
    inventory_key = _SUPPORTED_CRM_PROVIDER_KEYS.get(raw_provider)
    if not inventory_key:
        raise ValueError(
            f"Column mapping is not available yet for CRM provider {raw_provider!r}. "
            "Use Practice CRM, Salesforce, or HubSpot, or change setup to "
            "clean-only catalog mode. Mapping will not invent a different CRM inventory."
        )

    # CMX-0/M1: primary MAP plan uses provider-labelled **ingest** catalog
    # (canon expansions). Write inventory is a sibling write-contract bind,
    # not the plan destination (never write API keys as plan expansions).
    entity = str(intent.entity or "").strip().lower() if intent is not None else ""
    if entity == "accounts":
        object_key = "Company" if inventory_key == "hubspot" else "Account"
        object_scope = "account"
    elif entity == "people":
        object_key = "Contact"
        object_scope = "contact"
    else:
        raise ValueError(
            "Import setup is incomplete for CRM column mapping. Choose "
            "Accounts or People before mapping columns. Mapping will not "
            "default invent a CRM object inventory."
        )

    mint_provider = (
        "fake"
        if str(inventory_key).startswith("fake")
        else str(inventory_key).strip().lower()
    )
    if mint_provider not in {"fake", "salesforce", "hubspot"}:
        raise ValueError(f"No ingest catalog mint for CRM provider {raw_provider!r}.")
    # Prefer operator vocabulary when it matches the connection provider;
    # otherwise mint from the connected provider (M1 match picker surface).
    from .vocabulary_intent import (
        DEFAULT_VOCABULARY,
        PROVIDER_VOCABULARY_VALUES,
        normalize_vocabulary,
    )

    try:
        vocab = normalize_vocabulary(vocabulary)
    except Exception:
        vocab = DEFAULT_VOCABULARY
    if vocab in PROVIDER_VOCABULARY_VALUES and vocab != mint_provider:
        raise ValueError(
            "Field names for mapping do not match the connected CRM provider. "
            f"Vocabulary is {vocab!r} but the connection is {mint_provider!r}."
        )
    if vocab in PROVIDER_VOCABULARY_VALUES:
        mint_provider = vocab
    catalog_id = f"easyimports.ingest.{mint_provider}.{object_scope}.v1"
    return {
        "destination_mode": "catalog",
        "catalog_id": catalog_id,
        # Sidecar (not part of DestinationDescriptor digest wire): write inventory
        # for M1 write-contract bind construction at workflow create.
        "m1_write_destination": {
            "destination_mode": "crm",
            "provider_key": inventory_key,
            "connection_id": connection_id,
            "object_keys": [object_key],
            "operation_key": "insert",
        },
    }


def _require_completed_plan_dispatch(
    result: MutationDispatchResult,
) -> dict[str, Any]:
    """Fail closed when journaled dispatch is rejected or not a plan resource."""

    mutation = result.mutation
    mutation.refresh_from_db()
    if mutation.state == ApiMutation.State.REJECTED:
        code = str(mutation.error_code or "column_mapping_plan_rejected")
        message = str(
            mutation.error_message or "The column mapping request was rejected."
        )
        # Prefer error envelope message when present.
        payload = result.response if isinstance(result.response, dict) else {}
        error = payload.get("error") if isinstance(payload.get("error"), dict) else None
        if error:
            code = str(error.get("code") or code)
            message = str(error.get("message") or message)
        raise ApiRejectedError(code, message, details=error)

    if mutation.state != ApiMutation.State.COMPLETED:
        raise ApiUnavailableError(
            f"Column mapping mutation is not complete (state={mutation.state})."
        )

    plan = result.response if isinstance(result.response, dict) else None
    if not plan or not plan.get("plan_id"):
        raise ApiUnavailableError(
            "Column mapping mutation completed without a plan resource."
        )
    return plan


# --- MAP-R4 presentation helpers (operator copy; no internal jargon) ---

_ORIGIN_OPERATOR = "operator_selected"
_ORIGIN_AUTO = "auto_detected"
_FILTER_ALL = "all"
_FILTER_NEEDS_REVIEW = "needs_review"
_FILTER_MAPPED = "mapped"
_FILTER_IGNORED = "ignored"


def operator_status_label(
    disposition: str | None,
    choice_origin: str | None = None,
    *,
    has_suggestion: bool = False,
) -> str:
    """Customer-facing row status (MAP-R0 copy rules)."""

    disp = str(disposition or "").strip().lower()
    if disp == "ignored":
        return "Ignore column"
    if disp == "mapped":
        origin = str(choice_origin or "").strip().lower()
        if origin == _ORIGIN_OPERATOR:
            return "Changed by you"
        if origin == _ORIGIN_AUTO:
            return "Matched automatically"
        return "Mapped"
    if has_suggestion:
        return "Needs review"
    return "Needs review"


def maps_to_display_label(
    *,
    disposition: str | None,
    display_target_label: str | None,
    mapping_choice_id: str | None,
    object_scope: str | None,
    suggested_display_label: str | None = None,
    multi_object: bool = False,
) -> str:
    """Primary Maps-to cell text; object scope only when multi-object inventory."""

    disp = str(disposition or "").strip().lower()
    if disp == "ignored" or str(mapping_choice_id or "") == SYSTEM_IGNORE:
        return "Ignored"
    label = str(display_target_label or "").strip()
    if disp == "mapped" and label:
        scope = str(object_scope or "").strip()
        if multi_object and scope:
            return f"{scope} · {label}"
        return label
    suggested = str(suggested_display_label or "").strip()
    if suggested:
        scope = str(object_scope or "").strip()
        if multi_object and scope:
            return f"Suggested: {scope} · {suggested}"
        return f"Suggested: {suggested}"
    return "Needs review"


def bound_sample_cells(sample_values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Exactly three display cells; empty → em dash (UI only)."""

    raw = list(sample_values or [])
    cells: list[str] = []
    for i in range(3):
        if i < len(raw) and str(raw[i]).strip():
            cells.append(str(raw[i]))
        else:
            cells.append("—")
    return cells


def choice_object_scopes(choices: list[dict[str, Any]]) -> set[str]:
    scopes: set[str] = set()
    for choice in choices:
        scope = str(choice.get("object_scope") or "").strip()
        if scope:
            scopes.add(scope)
    return scopes


def _samples_by_ordinal(plan: dict[str, Any]) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    for col in plan.get("source_schema") or []:
        if not isinstance(col, dict):
            continue
        try:
            ordinal = int(col.get("source_ordinal"))
        except (TypeError, ValueError):
            continue
        samples = col.get("sample_values") or []
        if isinstance(samples, list):
            out[ordinal] = [str(s) for s in samples]
        else:
            out[ordinal] = []
    return out


def _choice_lookup(choices: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for choice in choices:
        cid = str(choice.get("mapping_choice_id") or "").strip()
        if cid:
            by_id[cid] = choice
    return by_id


def group_choices_for_picker(
    choices: list[dict[str, Any]],
) -> dict[str, Any]:
    """Object-grouped field choices; Ignore is separate (not mixed into lists)."""

    field_groups: dict[str, list[dict[str, str]]] = {}
    ignore_choice: dict[str, str] | None = None
    for choice in choices:
        cid = str(choice.get("mapping_choice_id") or "").strip()
        label = str(choice.get("label") or "").strip() or cid
        if not cid:
            continue
        if cid == SYSTEM_IGNORE or str(choice.get("choice_kind") or "") == "ignore":
            ignore_choice = {
                "mapping_choice_id": cid,
                "label": "Ignore column",
            }
            continue
        scope = str(choice.get("object_scope") or "").strip() or "Fields"
        field_groups.setdefault(scope, []).append(
            {
                "mapping_choice_id": cid,
                "label": label,
                "object_scope": scope if scope != "Fields" else "",
                # Search tokens only (label + id tail); never show choice_kind.
                "search_text": f"{label} {cid} {scope}".lower(),
            }
        )
    groups: list[dict[str, Any]] = []
    for scope in sorted(field_groups.keys(), key=lambda s: (s == "Fields", s.lower())):
        items = sorted(field_groups[scope], key=lambda c: c["label"].lower())
        groups.append({"object_scope": scope, "choices": items})
    return {
        "field_groups": groups,
        "ignore": ignore_choice,
    }


def build_review_table_rows(
    plan: dict[str, Any],
    choices: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Stable upload-order rows for the MAP-R4 table (identity = source_ordinal)."""

    by_choice = _choice_lookup(choices)
    multi_object = len(choice_object_scopes(choices)) > 1
    samples = _samples_by_ordinal(plan)
    header_counts: dict[str, int] = {}
    for row in plan.get("rows") or []:
        if not isinstance(row, dict):
            continue
        h = str(row.get("source_header") or "")
        header_counts[h] = header_counts.get(h, 0) + 1

    table_rows: list[dict[str, Any]] = []
    for row in sorted(
        (r for r in (plan.get("rows") or []) if isinstance(r, dict)),
        key=lambda r: int(r.get("source_ordinal") or 0),
    ):
        try:
            ordinal = int(row.get("source_ordinal"))
        except (TypeError, ValueError):
            continue
        disposition = str(row.get("disposition") or "unresolved")
        origin = row.get("choice_origin")
        choice_id = row.get("mapping_choice_id")
        choice_id_s = str(choice_id).strip() if choice_id else ""
        choice_meta = by_choice.get(choice_id_s) if choice_id_s else None
        suggested_id = str(row.get("suggested_mapping_choice_id") or "").strip()
        suggested_label = row.get("suggested_display_label")
        suggested_meta = by_choice.get(suggested_id) if suggested_id else None
        object_scope = None
        if choice_meta:
            object_scope = choice_meta.get("object_scope")
        elif suggested_meta:
            object_scope = suggested_meta.get("object_scope")
        has_suggestion = bool(suggested_id or suggested_label)
        header = str(row.get("source_header") or "")
        # Ordinal suffix only when duplicate headers (copy rule).
        source_label = header
        if header_counts.get(header, 0) > 1:
            source_label = f"{header} (column {ordinal})"

        if disposition == "unresolved":
            filter_key = _FILTER_NEEDS_REVIEW
            action_label = "Review" if has_suggestion else "Change"
        elif disposition == "ignored":
            filter_key = _FILTER_IGNORED
            action_label = "Change"
        else:
            filter_key = _FILTER_MAPPED
            action_label = "Change"

        table_rows.append(
            {
                "source_ordinal": ordinal,
                "source_header": header,
                "source_label": source_label,
                "disposition": disposition,
                "filter_key": filter_key,
                "mapping_choice_id": choice_id_s or "",
                "display_target_label": row.get("display_target_label"),
                "choice_origin": origin,
                "status_label": operator_status_label(
                    disposition, origin, has_suggestion=has_suggestion
                ),
                "maps_to_label": maps_to_display_label(
                    disposition=disposition,
                    display_target_label=row.get("display_target_label"),
                    mapping_choice_id=choice_id_s or None,
                    object_scope=str(object_scope) if object_scope else None,
                    suggested_display_label=(
                        str(suggested_label) if suggested_label else None
                    ),
                    multi_object=multi_object,
                ),
                "suggested_mapping_choice_id": suggested_id,
                "suggested_display_label": suggested_label,
                "suggestion_reason": row.get("suggestion_reason"),
                "sample_cells": bound_sample_cells(samples.get(ordinal)),
                "action_label": action_label,
                "row_anchor": f"mapping-row-{ordinal}",
            }
        )
    return table_rows


def review_filter_counts(table_rows: list[dict[str, Any]]) -> dict[str, int]:
    counts = {
        _FILTER_ALL: len(table_rows),
        _FILTER_NEEDS_REVIEW: 0,
        _FILTER_MAPPED: 0,
        _FILTER_IGNORED: 0,
    }
    for row in table_rows:
        key = str(row.get("filter_key") or "")
        if key in counts:
            counts[key] += 1
    return counts


def choices_public_json(choices: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Wire shape for the picker: no choice_kind in ordinary operator UI."""

    out: list[dict[str, Any]] = []
    for choice in choices:
        cid = str(choice.get("mapping_choice_id") or "").strip()
        if not cid:
            continue
        is_ignore = (
            cid == SYSTEM_IGNORE or str(choice.get("choice_kind") or "") == "ignore"
        )
        aliases_raw = choice.get("search_aliases") or []
        aliases: list[str] = []
        if isinstance(aliases_raw, (list, tuple)):
            aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
        out.append(
            {
                "mapping_choice_id": cid,
                "label": (
                    "Ignore column"
                    if is_ignore
                    else (str(choice.get("label") or "").strip() or cid)
                ),
                "object_scope": str(choice.get("object_scope") or "").strip(),
                "is_ignore": is_ignore,
                "search_aliases": aliases,
            }
        )
    return out


_ATOMIC_REVIEW_ACTIONS = frozenset(
    {"save_draft", "confirm_mapping", "bulk_ignore", "bulk_ignore_all"}
)
_IGNORE_REVIEW_ACTIONS = frozenset({"bulk_ignore", "bulk_ignore_all"})


def review_error_anchor(
    *,
    action: str,
    row_updates: list[dict[str, Any]] | None = None,
    source_ordinal: int | None = None,
) -> str:
    """Fragment for no-JS validation recovery (MAP-R4 freeze §8)."""

    if source_ordinal is not None:
        return f"#mapping-row-{int(source_ordinal)}"
    if row_updates:
        try:
            first = min(int(u["source_ordinal"]) for u in row_updates)
            return f"#mapping-row-{first}"
        except (KeyError, TypeError, ValueError):
            pass
    if action in {
        "save_draft",
        "confirm_mapping",
        "bulk_ignore",
        "bulk_ignore_all",
        "confirm",
    }:
        return "#mapping-review-form"
    return "#mapping-review"


def parse_row_updates_from_post(
    post,
    plan_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Distinct ordinal updates from no-JS / enhanced form fields.

    Only rows whose submitted choice differs from the durable plan (or that
    clear an unresolved gap) are included so auto_detected origins are not
    rewritten on a pure confirm.
    """

    current = {}
    for row in plan_rows:
        if not isinstance(row, dict):
            continue
        try:
            ordinal = int(row.get("source_ordinal"))
        except (TypeError, ValueError):
            continue
        current[ordinal] = row

    updates: list[dict[str, Any]] = []
    seen: set[int] = set()

    # Bulk ignore: checkboxes name bulk_ordinal
    bulk = post.getlist("bulk_ordinal") if hasattr(post, "getlist") else []
    for raw in bulk:
        try:
            ordinal = int(raw)
        except (TypeError, ValueError):
            continue
        if ordinal in seen or ordinal not in current:
            continue
        row = current[ordinal]
        if str(row.get("disposition") or "") != "unresolved":
            continue
        seen.add(ordinal)
        updates.append(
            {
                "source_ordinal": ordinal,
                "mapping_choice_id": SYSTEM_IGNORE,
            }
        )

    for ordinal, row in sorted(current.items()):
        if ordinal in seen:
            continue
        key = f"choice_{ordinal}"
        if key not in post:
            continue
        choice = str(post.get(key) or "").strip()
        if not choice:
            continue
        current_choice = str(row.get("mapping_choice_id") or "").strip()
        if choice == current_choice:
            continue
        seen.add(ordinal)
        updates.append({"source_ordinal": ordinal, "mapping_choice_id": choice})

    return updates


def _row_source_ordinal(row: dict[str, Any]) -> int | None:
    try:
        return int(row.get("source_ordinal"))
    except (TypeError, ValueError):
        return None


def unresolved_plan_ordinals(plan_rows: list[dict[str, Any]]) -> list[int]:
    """Upload-order ordinals whose durable disposition is still unresolved."""

    ordinals: list[int] = []
    for row in plan_rows:
        if not isinstance(row, dict):
            continue
        if str(row.get("disposition") or "") != "unresolved":
            continue
        ordinal = _row_source_ordinal(row)
        if ordinal is None:
            continue
        ordinals.append(ordinal)
    return ordinals


def append_ignore_updates_for_ordinals(
    row_updates: list[dict[str, Any]],
    ordinals: list[int],
) -> list[dict[str, Any]]:
    """Add system:ignore rows for ordinals that do not already have an update."""

    seen: set[int] = set()
    for update in row_updates:
        try:
            seen.add(int(update["source_ordinal"]))
        except (KeyError, TypeError, ValueError):
            continue
    for ordinal in ordinals:
        if ordinal in seen:
            continue
        row_updates.append(
            {
                "source_ordinal": int(ordinal),
                "mapping_choice_id": SYSTEM_IGNORE,
            }
        )
        seen.add(int(ordinal))
    return row_updates


def remaining_unresolved_after_updates(
    plan_rows: list[dict[str, Any]],
    row_updates: list[dict[str, Any]],
) -> list[int]:
    """Unresolved durable rows that this payload does not map or ignore."""

    resolved: set[int] = set()
    for update in row_updates:
        try:
            ordinal = int(update["source_ordinal"])
        except (KeyError, TypeError, ValueError):
            continue
        if str(update.get("mapping_choice_id") or "").strip():
            resolved.add(ordinal)
    return [
        ordinal
        for ordinal in unresolved_plan_ordinals(plan_rows)
        if ordinal not in resolved
    ]


def _atomic_rows_fingerprint(rows: list[dict[str, Any]]) -> str:
    material = [
        {
            "source_ordinal": int(r["source_ordinal"]),
            "mapping_choice_id": str(r["mapping_choice_id"]),
        }
        for r in sorted(rows, key=lambda x: int(x["source_ordinal"]))
    ]
    raw = json.dumps(material, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _persist_confirmed_map_draft(
    session: ImportSession,
    plan: dict[str, Any],
    *,
    plan_id: str,
) -> None:
    draft = _draft(session)
    draft["plan_id"] = str(plan.get("plan_id") or plan_id)
    draft["status"] = "confirmed"
    digests = plan.get("confirmed_digests")
    if isinstance(digests, dict) and digests:
        draft["confirmed_digests"] = {str(k): str(v) for k, v in digests.items()}
    else:
        draft["confirmed_digests"] = {
            key: str(plan.get(key) or "")
            for key in (
                "plan_content_digest",
                "source_schema_digest",
                "destination_digest",
                "target_contract_digest",
            )
        }
    draft["destination"] = plan.get("destination") or draft.get("destination")
    draft["source_headers"] = [
        str(col.get("source_header") or "") for col in (plan.get("source_schema") or [])
    ]
    # Preserve upload binding for MAP-R5 configure-gate currency checks.
    if not str(draft.get("upload_id") or "").strip():
        source = authoritative_mapping_source(session)
        if source is not None:
            draft["upload_id"] = source.api_upload_id
    _save_draft(session, draft)


def _dispatch_atomic_review(
    *,
    session: ImportSession,
    client: EasyImportsApiClient,
    claims: dict[str, Any],
    plan_id: str,
    owner_session: str,
    expected_digest: str,
    intent: str,
    row_updates: list[dict[str, Any]],
) -> dict[str, Any]:
    body = {
        "schema_version": "column_mapping_atomic_review_command.v1",
        "expected_plan_content_digest": expected_digest,
        "intent": intent,
        "rows": row_updates,
        "owner_session": owner_session,
    }
    fp = _atomic_rows_fingerprint(row_updates)
    mutation = create_or_reuse_mutation(
        session=session,
        form_instance=claims["form_instance"],
        mutation_kind="column_mapping_plan_atomic_review",
        route=f"/v1/column-mapping-plans/{plan_id}/atomic-review",
        logical_action_identity=(
            f"column-map-atomic:{plan_id}:{intent}:{expected_digest}:{fp}"
        ),
        request_json=body,
        resource_identity=plan_id,
    )
    return _require_completed_plan_dispatch(client.dispatch(mutation))


def _draft_matches_current(
    draft: dict[str, Any],
    *,
    source: SourceFile,
    destination: dict[str, Any],
) -> bool:
    if str(draft.get("upload_id") or "") != str(source.api_upload_id or ""):
        return False
    saved_dest = draft.get("destination")
    if not isinstance(saved_dest, dict):
        return False
    return _destination_fingerprint(saved_dest) == _destination_fingerprint(destination)


def _matching_plan_kind(
    draft: dict[str, Any],
    *,
    source: SourceFile,
    destination: dict[str, Any],
    client: EasyImportsApiClient,
    owner_session: str,
) -> str | None:
    """Return ``unconfirmed``, ``confirmed``, or None. GET-safe: no writes."""

    plan_id = str(draft.get("plan_id") or "").strip()
    if not plan_id or not _draft_matches_current(
        draft, source=source, destination=destination
    ):
        return None
    if str(draft.get("status") or "").strip() == "confirmed":
        return "confirmed"
    try:
        existing = client.get_column_mapping_plan(plan_id, owner_session=owner_session)
    except (ApiRejectedError, ApiUnavailableError):
        return None
    if existing is None:
        return None
    if existing.get("status") == "confirmed":
        return "confirmed"
    return "unconfirmed"


class PlanCreatePending(Exception):
    """Plan-create is still in flight; poll instead of minting a new journal."""

    def __init__(self, mutation: ApiMutation):
        super().__init__("Column mapping plan create is still in progress.")
        self.mutation = mutation


def _plan_create_identity(
    session: ImportSession,
    source: SourceFile,
    destination: dict[str, Any],
    create_generation: int,
) -> str:
    return (
        f"column-map-create:{session.id}:{source.api_upload_id}:"
        f"{_destination_fingerprint(destination)}:g{create_generation}"
    )


def _latest_plan_create_mutation(
    session: ImportSession,
    source: SourceFile,
    destination: dict[str, Any],
) -> ApiMutation | None:
    prefix = (
        f"column-map-create:{session.id}:{source.api_upload_id}:"
        f"{_destination_fingerprint(destination)}:g"
    )
    return (
        session.api_mutations.filter(
            mutation_kind="column_mapping_plan_create",
            logical_action_identity__startswith=prefix,
        )
        .order_by("-logical_action_generation", "-created_at")
        .first()
    )


def _effective_plan_create_generation(
    session: ImportSession,
    source: SourceFile,
    destination: dict[str, Any],
    draft: dict[str, Any],
    *,
    map_again: bool = False,
) -> int:
    """R18: freeze generation until an acknowledged rejection, then advance once."""

    if map_again:
        return _create_generation(draft)
    latest = _latest_plan_create_mutation(session, source, destination)
    if latest is None:
        return _create_generation(draft)
    if (
        latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is not None
        and not latest.is_idempotency_conflict
    ):
        return latest.logical_action_generation + 1
    return latest.logical_action_generation


def plan_create_retry_action_id(session: ImportSession, mutation: ApiMutation) -> str:
    """Bind retry tokens to this mutation and the current setup revision."""

    return f"{mutation.id}:{int(session.setup_revision)}"


def setup_revision_from_plan_create_retry_claims(
    claims: dict[str, Any], mutation: ApiMutation
) -> int | None:
    """Return the setup revision bound on a validated plan-create retry token."""

    action_id = str(claims.get("action_id") or "")
    prefix = f"{mutation.id}:"
    if not action_id.startswith(prefix):
        return None
    try:
        return int(action_id[len(prefix):])
    except ValueError:
        return None


def _draft_blocks_plan_adoption(
    draft: dict[str, Any], mutation: ApiMutation, plan_id: str
) -> bool:
    """True when the session already holds a newer mapping draft."""

    draft_gen = _create_generation(draft)
    if draft_gen > int(mutation.logical_action_generation or 0):
        return True
    draft_plan = str(draft.get("plan_id") or "").strip()
    if draft_plan and draft_plan != plan_id:
        if draft_gen >= int(mutation.logical_action_generation or 0):
            return True
        if str(draft.get("status") or "").strip() == "confirmed":
            return True
    return False


def adopt_completed_plan_create(
    session: ImportSession,
    mutation: ApiMutation,
    *,
    expected_setup_revision: int | None = None,
) -> str | None:
    """Persist a completed plan-create journal only when it still matches now."""

    if mutation.mutation_kind != "column_mapping_plan_create":
        return None
    if mutation.state != ApiMutation.State.COMPLETED:
        return None
    if expected_setup_revision is None:
        return None
    if int(expected_setup_revision) != int(session.setup_revision):
        return None
    plan = mutation.response_json if isinstance(mutation.response_json, dict) else {}
    plan_id = str(plan.get("plan_id") or "").strip()
    if not plan_id:
        return None
    if not mapping_auto_start_ready(session):
        return None
    source = authoritative_mapping_source(session)
    if source is None:
        return None
    if str(source.api_upload_id or "").strip() != str(
        mutation.resource_identity or ""
    ).strip():
        return None
    owner_session = owner_session_for_import_session(session) or ""
    try:
        destination = _destination_for_session(
            session, owner_session=owner_session
        )
    except ValueError:
        return None
    expected_identity = _plan_create_identity(
        session, source, destination, mutation.logical_action_generation
    )
    if str(mutation.logical_action_identity or "") != expected_identity:
        return None
    draft = _draft(session)
    if _draft_blocks_plan_adoption(draft, mutation, plan_id):
        return None
    _store_draft_plan(
        session,
        plan=plan,
        source=source,
        destination=destination,
        create_generation=mutation.logical_action_generation,
    )
    return plan_id


def _reconcile_pending_plan_create(
    *,
    client: EasyImportsApiClient,
    mutation: ApiMutation,
    owner_session: str,
) -> MutationDispatchResult | None:
    status = client.mutation_status(
        mutation.idempotency_key, owner_session=owner_session
    )
    if status.get("status") == "pending":
        if status.get("retryable"):
            ApiMutation.objects.filter(
                pk=mutation.pk,
                state=ApiMutation.State.PENDING,
            ).update(
                state=ApiMutation.State.UNKNOWN,
                http_status=None,
                response_json=status,
                error_message=(
                    "The API worker no longer owns this action. "
                    "Use the saved exact retry."
                ),
                lease_token=None,
                lease_expires_at=None,
                updated_at=timezone.now(),
            )
            mutation.refresh_from_db()
        return None
    return client.reconcile_mutation_status(mutation, status)


def _store_draft_plan(
    session: ImportSession,
    *,
    plan: dict[str, Any],
    source: SourceFile,
    destination: dict[str, Any],
    create_generation: int,
) -> None:
    draft = _draft(session)
    draft["plan_id"] = plan["plan_id"]
    draft["upload_id"] = source.api_upload_id
    draft["destination"] = destination
    draft["create_generation"] = create_generation
    draft.pop("status", None)
    draft.pop("confirmed_digests", None)
    _save_draft(session, draft)


def _journal_plan_create(
    *,
    session: ImportSession,
    source: SourceFile,
    destination: dict[str, Any],
    owner_session: str,
    client: EasyImportsApiClient,
    form_instance: UUID,
    create_generation: int,
    replacement_of: ApiMutation | None = None,
) -> dict[str, Any]:
    source_schema = _source_schema_from_upload(source)
    body = {
        "source_schema": source_schema,
        "destination": destination,
        "run_auto_detect": True,
        "owner_session": owner_session,
    }
    mutation = create_or_reuse_mutation(
        session=session,
        form_instance=form_instance,
        mutation_kind="column_mapping_plan_create",
        route="/v1/column-mapping-plans",
        logical_action_identity=_plan_create_identity(
            session, source, destination, create_generation
        ),
        logical_action_generation=create_generation,
        request_json=body,
        resource_identity=str(source.api_upload_id or ""),
        replacement_of=replacement_of,
    )
    mutation.refresh_from_db()
    already_started = bool(
        mutation.dispatched_at
        or mutation.http_status
        or mutation.attempt_count
        or mutation.lease_token
    )
    if mutation.state == ApiMutation.State.PENDING and already_started:
        reconciled = _reconcile_pending_plan_create(
            client=client, mutation=mutation, owner_session=owner_session
        )
        if reconciled is None:
            mutation.refresh_from_db()
            if mutation.state == ApiMutation.State.UNKNOWN:
                result = client.dispatch(mutation, explicit_retry=True)
                return _require_completed_plan_dispatch(result)
            raise PlanCreatePending(mutation)
        return _require_completed_plan_dispatch(reconciled)
    explicit_retry = mutation.state == ApiMutation.State.UNKNOWN
    try:
        result = client.dispatch(mutation, explicit_retry=explicit_retry)
    except MutationExplicitRetryRequired:
        result = client.dispatch(mutation, explicit_retry=True)
    return _require_completed_plan_dispatch(result)


def _plan_create_recovery_context(
    *,
    session: ImportSession,
    source: SourceFile | None,
    destination: dict[str, Any] | None,
    owner_id,
) -> dict[str, Any]:
    if source is None or destination is None:
        return {
            "plan_create_mutation": None,
            "plan_create_retry_token": "",
            "plan_create_ack_token": "",
        }
    latest = _latest_plan_create_mutation(session, source, destination)
    if latest is None:
        return {
            "plan_create_mutation": None,
            "plan_create_retry_token": "",
            "plan_create_ack_token": "",
        }
    retry_token = ""
    ack_token = ""
    if latest.state in {ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING}:
        retry_token = issue_form_token(
            owner_id=owner_id,
            session=session,
            action_kind="retry",
            action_id=plan_create_retry_action_id(session, latest),
            logical_action_identity=latest.logical_action_identity,
            logical_action_generation=latest.logical_action_generation,
        )
    if (
        latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is None
        and not latest.is_idempotency_conflict
    ):
        ack_token = issue_form_token(
            owner_id=owner_id,
            session=session,
            action_kind="acknowledge_rejection",
            action_id=str(latest.id),
            logical_action_identity=latest.logical_action_identity,
            logical_action_generation=latest.logical_action_generation,
        )
    show = (
        latest.state in {ApiMutation.State.UNKNOWN, ApiMutation.State.PENDING}
        or ack_token
    )
    return {
        "plan_create_mutation": latest if show else None,
        "plan_create_retry_token": retry_token,
        "plan_create_ack_token": ack_token,
    }


def try_autostart_mapping_after_upload(request, session: ImportSession):
    """After a completed isolated upload, maybe journal plan create (R18 / R19).

    Returns a redirect response, or None to stay on Add your files.
    Never journals a second ``register_upload``. GET is not involved.
    """

    if not mapping_auto_start_ready(session):
        return None
    owner_id = _owner_id(request)
    owner_session = _owner_session(owner_id)
    client = _api()
    source = authoritative_mapping_source(session)
    if source is None:
        return None
    try:
        destination = _destination_for_session(
            session, owner_session=owner_session, client=client
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)

    draft = _draft(session)
    kind = _matching_plan_kind(
        draft,
        source=source,
        destination=destination,
        client=client,
        owner_session=owner_session,
    )
    if kind == "confirmed":
        return None
    if kind == "unconfirmed":
        return redirect(
            "importer:column_mapping_review",
            session_id=session.id,
            plan_id=str(draft.get("plan_id") or ""),
        )

    create_generation = _effective_plan_create_generation(
        session, source, destination, draft
    )
    latest = _latest_plan_create_mutation(session, source, destination)
    replacement = (
        latest
        if latest is not None
        and latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is not None
        else None
    )
    try:
        plan = _journal_plan_create(
            session=session,
            source=source,
            destination=destination,
            owner_session=owner_session,
            client=client,
            form_instance=uuid4(),
            create_generation=create_generation,
            replacement_of=replacement,
        )
        if plan.get("status") == "confirmed":
            return None
    except PlanCreatePending:
        messages.info(
            request,
            "EasyImports is still creating the mapping plan. Check this mapping "
            "without uploading the file again.",
        )
        return redirect("importer:column_mapping_session", session_id=session.id)
    except MutationReuseError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)
    except MutationExplicitRetryRequired:
        messages.error(
            request,
            "This mapping outcome is uncertain. Use Retry saved mapping action.",
        )
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ApiUnavailableError:
        messages.error(request, "The EasyImports API is unavailable.")
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ApiRejectedError as exc:
        messages.error(request, exc.message)
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)

    _store_draft_plan(
        session,
        plan=plan,
        source=source,
        destination=destination,
        create_generation=create_generation,
    )
    return redirect(
        "importer:column_mapping_review",
        session_id=session.id,
        plan_id=plan["plan_id"],
    )


@require_http_methods(["GET", "POST"])
def column_mapping_session(request, session_id):
    """Session-bound preflight: create plan from upload schema + destination."""

    owner_id = _owner_id(request)
    owner_session = _owner_session(owner_id)
    session = owned_session_or_404(request, session_id)
    draft = _draft(session)
    source = _primary_source(session)
    client = _api()

    form_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind="column_mapping_create",
    )

    destination: dict[str, Any] | None = None
    destination_error: str | None = None
    try:
        destination = _destination_for_session(
            session, owner_session=owner_session, client=client
        )
    except ValueError as exc:
        # Fail closed: do not invent a catalog or switch destination mode.
        destination_error = str(exc)
        messages.error(request, destination_error)

    if request.method == "GET":
        if source is None:
            messages.info(
                request,
                "Upload your list first so column mapping can use the file headers.",
            )
            return redirect("importer:upload", session_id=session.id)
        recovery = _plan_create_recovery_context(
            session=session,
            source=source,
            destination=destination,
            owner_id=owner_id,
        )
        if destination is None:
            return render(
                request,
                "importer/column_mapping_start.html",
                {
                    "session": session,
                    "form_token": form_token,
                    "source": source,
                    "headers": list(source.columns or []),
                    "destination": {},
                    "destination_mode": None,
                    "destination_error": destination_error
                    or "Column mapping destination could not be resolved.",
                    "mapping_blocked": True,
                    "matching_confirmed": False,
                    "step": 2,
                    **recovery,
                },
            )
        matching = _matching_plan_kind(
            draft,
            source=source,
            destination=destination,
            client=client,
            owner_session=owner_session,
        )
        if matching == "unconfirmed":
            return redirect(
                "importer:column_mapping_review",
                session_id=session.id,
                plan_id=str(draft.get("plan_id") or ""),
            )
        return render(
            request,
            "importer/column_mapping_start.html",
            {
                "session": session,
                "form_token": form_token,
                "source": source,
                "headers": list(source.columns or []),
                "destination": destination,
                "destination_mode": destination.get("destination_mode"),
                "matching_confirmed": matching == "confirmed",
                "step": 2,
                **recovery,
            },
        )

    if source is None:
        messages.error(request, "Upload a file before mapping columns.")
        return redirect("importer:upload", session_id=session.id)

    if destination is None:
        # Block create: destination must resolve (no silent catalog fallback).
        return redirect("importer:column_mapping_session", session_id=session.id)

    token = (request.POST.get("form_token") or "").strip()
    try:
        claims = validate_form_token(
            token,
            owner_id=owner_id,
            session=session,
            action_kind="column_mapping_create",
        )
    except FormTokenError:
        messages.error(request, "Your form expired. Please try again.")
        return redirect("importer:column_mapping_session", session_id=session.id)

    try:
        # Re-resolve under the same fail-closed rules (setup/CRM may have changed).
        destination = _destination_for_session(
            session, owner_session=owner_session, client=client
        )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)

    # Refresh draft (generation may have been bumped on Map again).
    draft = _draft(session)
    action = str(request.POST.get("action") or "").strip()
    if action == "map_again":
        _bump_create_generation(draft)
        _save_draft(session, draft)
    create_generation = _effective_plan_create_generation(
        session, source, destination, draft, map_again=action == "map_again"
    )
    latest = _latest_plan_create_mutation(session, source, destination)
    replacement = (
        latest
        if latest is not None
        and latest.state == ApiMutation.State.REJECTED
        and latest.acknowledged_at is not None
        else None
    )
    try:
        plan = _journal_plan_create(
            session=session,
            source=source,
            destination=destination,
            owner_session=owner_session,
            client=client,
            form_instance=claims["form_instance"],
            create_generation=create_generation,
            replacement_of=replacement,
        )
        if plan.get("status") == "confirmed":
            # Journal replayed an old confirmed create — bump and force operator retry.
            _bump_create_generation(draft)
            _save_draft(session, draft)
            messages.error(
                request,
                "Could not start a new mapping plan (previous create was reused). "
                "Try Map again.",
            )
            return redirect("importer:column_mapping_session", session_id=session.id)
    except PlanCreatePending:
        messages.info(
            request,
            "EasyImports is still creating the mapping plan. Check again "
            "without uploading the file again.",
        )
        return redirect("importer:column_mapping_session", session_id=session.id)
    except MutationReuseError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)
    except MutationExplicitRetryRequired:
        messages.error(
            request,
            "This mapping outcome is uncertain. Use Retry saved mapping action.",
        )
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ApiUnavailableError:
        messages.error(request, "The EasyImports API is unavailable.")
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ApiRejectedError as exc:
        messages.error(request, exc.message)
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("importer:column_mapping_session", session_id=session.id)

    _store_draft_plan(
        session,
        plan=plan,
        source=source,
        destination=destination,
        create_generation=create_generation,
    )
    return redirect(
        "importer:column_mapping_review",
        session_id=session.id,
        plan_id=plan["plan_id"],
    )


@require_http_methods(["GET", "POST"])
def column_mapping_review(request, session_id, plan_id: str):
    """MAP-R4 table review: atomic multi-row draft/confirm + row-at-a-time compat."""

    owner_id = _owner_id(request)
    owner_session = _owner_session(owner_id)
    session = owned_session_or_404(request, session_id)
    client = _api()
    plan_id = str(plan_id or "").strip()
    if not plan_id:
        raise Http404("plan not found")

    try:
        plan = client.get_column_mapping_plan(plan_id, owner_session=owner_session)
    except ApiRejectedError as exc:
        if exc.code == "column_mapping_plan_not_found":
            raise Http404("plan not found") from exc
        messages.error(request, exc.message)
        return redirect("importer:column_mapping_session", session_id=session.id)
    except ApiUnavailableError:
        messages.error(request, "The EasyImports API is unavailable.")
        return redirect("importer:column_mapping_session", session_id=session.id)

    destination = plan.get("destination") or {}
    choices_load_error: str | None = None
    try:
        choices_payload = client.list_column_mapping_choices(
            owner_session=owner_session,
            destination_mode=str(destination.get("destination_mode") or "catalog"),
            catalog_id=destination.get("catalog_id"),
            provider_key=destination.get("provider_key"),
            connection_id=destination.get("connection_id"),
            object_keys=list(destination.get("object_keys") or []),
            operation_key=destination.get("operation_key"),
        )
        choices = list(choices_payload.get("choices") or [])
        if not choices:
            # Empty inventory is not a silent empty picker — operator cannot map.
            choices_load_error = (
                "No destination fields are available for this mapping plan. "
                "Reload the page or return to setup and try again."
            )
    except ApiRejectedError as exc:
        choices = []
        choices_load_error = (
            f"Could not load destination fields: {exc.message} " "Reload to retry."
        )
    except ApiUnavailableError:
        choices = []
        choices_load_error = (
            "Could not load destination fields because the EasyImports API "
            "is unavailable. Reload to retry."
        )

    form_token = issue_form_token(
        owner_id=owner_id,
        session=session,
        action_kind="column_mapping_review",
        action_id=plan_id,
    )

    def _review_redirect(*, anchor: str = "") -> Any:
        url = reverse(
            "importer:column_mapping_review",
            kwargs={"session_id": session.id, "plan_id": plan_id},
        )
        if anchor:
            frag = anchor if anchor.startswith("#") else f"#{anchor}"
            return redirect(f"{url}{frag}")
        return redirect(url)

    if request.method == "POST":
        token = (request.POST.get("form_token") or "").strip()
        try:
            claims = validate_form_token(
                token,
                owner_id=owner_id,
                session=session,
                action_kind="column_mapping_review",
                action_id=plan_id,
            )
        except FormTokenError:
            messages.error(request, "Your form expired. Please try again.")
            return _review_redirect(anchor="#mapping-review-form")

        action = (request.POST.get("action") or "").strip()
        plan_rows = [r for r in (plan.get("rows") or []) if isinstance(r, dict)]

        if choices_load_error and action in {
            "save_draft",
            "confirm_mapping",
            "bulk_ignore",
            "bulk_ignore_all",
            "patch",
            "confirm",
        }:
            messages.error(
                request,
                "Mapping actions are blocked until destination fields load. "
                + choices_load_error,
            )
            return _review_redirect(anchor="#mapping-choices-error")

        # MAP-R4 product path: multi-row atomic save / confirm.
        # OUT-5A/5B: ignore-all is one payload; ignore that clears the last
        # unresolved row confirms (does not save_draft then redirect).
        if action in _ATOMIC_REVIEW_ACTIONS:
            content_digest = str(plan.get("plan_content_digest") or "").strip()
            if not content_digest:
                messages.error(
                    request,
                    "Plan is missing content digest; reload and try again.",
                )
                return _review_redirect(anchor="#mapping-review-form")
            row_updates = parse_row_updates_from_post(request.POST, plan_rows)
            if action == "bulk_ignore":
                selected: list[int] = []
                for raw in request.POST.getlist("bulk_ordinal"):
                    try:
                        selected.append(int(raw))
                    except (TypeError, ValueError):
                        continue
                unresolved = set(unresolved_plan_ordinals(plan_rows))
                append_ignore_updates_for_ordinals(
                    row_updates,
                    [ordinal for ordinal in selected if ordinal in unresolved],
                )
            elif action == "bulk_ignore_all":
                append_ignore_updates_for_ordinals(
                    row_updates,
                    unresolved_plan_ordinals(plan_rows),
                )
            if action in _IGNORE_REVIEW_ACTIONS:
                remaining = remaining_unresolved_after_updates(
                    plan_rows, row_updates
                )
                intent = "save_draft" if remaining else "confirm"
            elif action == "save_draft":
                intent = "save_draft"
            else:
                intent = "confirm"
            fail_anchor = review_error_anchor(action=action, row_updates=row_updates)
            try:
                plan = _dispatch_atomic_review(
                    session=session,
                    client=client,
                    claims=claims,
                    plan_id=plan_id,
                    owner_session=owner_session,
                    expected_digest=content_digest,
                    intent=intent,
                    row_updates=row_updates,
                )
                if intent == "confirm":
                    _persist_confirmed_map_draft(session, plan, plan_id=plan_id)
                    messages.success(
                        request,
                        "Mapping confirmed. Continue with import settings.",
                    )
                    # MAP-R5 / OUT-5B: leave mapping only after confirm bind.
                    return redirect("importer:configure", session_id=session.id)
                if action == "bulk_ignore_all":
                    messages.success(
                        request,
                        "Unmapped columns were marked as ignored. Save is applied.",
                    )
                elif action == "bulk_ignore":
                    messages.success(
                        request,
                        "Selected columns were marked as ignored. Save is applied.",
                    )
                else:
                    messages.success(request, "Draft mapping saved.")
            except MutationReuseError as exc:
                messages.error(request, str(exc))
                return _review_redirect(anchor=fail_anchor)
            except ApiRejectedError as exc:
                messages.error(request, exc.message)
                return _review_redirect(anchor=fail_anchor)
            except ApiUnavailableError:
                messages.error(request, "The EasyImports API is unavailable.")
                return _review_redirect(anchor=fail_anchor)
            return _review_redirect(
                anchor=fail_anchor if row_updates else "#mapping-review"
            )

        # Compatibility: single-row patch (MAP-2 path still valid).
        if action == "patch":
            try:
                ordinal = int(request.POST.get("source_ordinal"))
            except (TypeError, ValueError):
                messages.error(request, "Invalid column ordinal.")
                return _review_redirect(anchor="#mapping-review-form")
            choice_id = (request.POST.get("mapping_choice_id") or "").strip()
            if not choice_id:
                messages.error(request, "Select a target field or Ignore column.")
                return _review_redirect(anchor="#mapping-review-form")
            body = {
                "mapping_choice_id": choice_id,
                "owner_session": owner_session,
            }
            try:
                mutation = create_or_reuse_mutation(
                    session=session,
                    form_instance=claims["form_instance"],
                    mutation_kind="column_mapping_plan_patch_row",
                    route=(f"/v1/column-mapping-plans/{plan_id}/rows/{ordinal}"),
                    logical_action_identity=(
                        f"column-map-patch:{plan_id}:{ordinal}:"
                        f"{claims['form_instance']}"
                    ),
                    request_json=body,
                    resource_identity=f"{plan_id}:row:{ordinal}",
                )
                _require_completed_plan_dispatch(client.dispatch(mutation))
                messages.success(request, "Column mapping updated.")
                return _review_redirect(anchor=f"#mapping-row-{ordinal}")
            except MutationReuseError as exc:
                messages.error(request, str(exc))
            except ApiRejectedError as exc:
                messages.error(request, exc.message)
            except ApiUnavailableError:
                messages.error(request, "The EasyImports API is unavailable.")
            return _review_redirect(anchor=f"#mapping-row-{ordinal}")

        # Compatibility: confirm without row updates (MAP-2 identity tests).
        if action == "confirm":
            content_digest = str(plan.get("plan_content_digest") or "").strip()
            if not content_digest:
                messages.error(
                    request,
                    "Plan is missing content digest; reload and try again.",
                )
                return _review_redirect(anchor="#mapping-review-form")
            body = {
                "owner_session": owner_session,
                "plan_content_digest": content_digest,
            }
            try:
                mutation = create_or_reuse_mutation(
                    session=session,
                    form_instance=claims["form_instance"],
                    mutation_kind="column_mapping_plan_confirm",
                    route=f"/v1/column-mapping-plans/{plan_id}/confirm",
                    logical_action_identity=(
                        f"column-map-confirm:{plan_id}:{content_digest}"
                    ),
                    request_json=body,
                    resource_identity=plan_id,
                )
                plan = _require_completed_plan_dispatch(client.dispatch(mutation))
                _persist_confirmed_map_draft(session, plan, plan_id=plan_id)
                messages.success(
                    request,
                    "Mapping confirmed. Continue with import settings.",
                )
                return redirect("importer:configure", session_id=session.id)
            except MutationReuseError as exc:
                messages.error(request, str(exc))
            except ApiRejectedError as exc:
                messages.error(request, exc.message)
            except ApiUnavailableError:
                messages.error(request, "The EasyImports API is unavailable.")
            return _review_redirect(anchor="#mapping-review-form")

        messages.error(request, "Unknown action.")
        return _review_redirect(anchor="#mapping-review-form")

    table_rows = build_review_table_rows(plan, choices)
    filter_counts = review_filter_counts(table_rows)
    picker_groups = group_choices_for_picker(choices)
    multi_object = len(choice_object_scopes(choices)) > 1
    needs_review = filter_counts.get(_FILTER_NEEDS_REVIEW, 0)
    status = plan.get("status")
    mapping_actions_blocked = bool(choices_load_error) and status == "draft"
    return render(
        request,
        "importer/column_mapping_review.html",
        {
            "session": session,
            "plan": plan,
            "plan_id": plan_id,
            "form_token": form_token,
            "choices": choices,
            "choices_for_js": choices_public_json(choices),
            "picker_groups": picker_groups,
            "system_ignore": SYSTEM_IGNORE,
            "table_rows": table_rows,
            "filter_counts": filter_counts,
            "multi_object": multi_object,
            "needs_review_count": needs_review,
            "can_confirm": (
                status == "draft" and needs_review == 0 and not mapping_actions_blocked
            ),
            "choices_load_error": choices_load_error,
            "mapping_actions_blocked": mapping_actions_blocked,
            "status": status,
            "map_again_token": (
                issue_form_token(
                    owner_id=owner_id,
                    session=session,
                    action_kind="column_mapping_create",
                )
                if status == "confirmed"
                else ""
            ),
            "plan_content_digest": str(plan.get("plan_content_digest") or ""),
            "step": 2,
            # Legacy keys kept for any residual template/tests.
            "unresolved_rows": [
                r for r in table_rows if r.get("filter_key") == _FILTER_NEEDS_REVIEW
            ],
            "mapped_rows": [
                r for r in table_rows if r.get("filter_key") == _FILTER_MAPPED
            ],
            "ignored_rows": [
                r for r in table_rows if r.get("filter_key") == _FILTER_IGNORED
            ],
            "all_rows": table_rows,
        },
    )


@require_http_methods(["GET"])
def column_mapping_start(request):
    owner_id = _owner_id(request)
    session = ImportSession.objects.create(
        owner_id=owner_id,
        status=ImportSession.Status.CREATED,
    )
    messages.info(
        request,
        "Start import setup and upload a file, then map columns from the session.",
    )
    return redirect("importer:product", session_id=session.id)
