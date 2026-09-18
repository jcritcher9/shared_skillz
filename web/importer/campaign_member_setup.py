"""FE-CM-3 helpers: CM ceiling projection, policy freeze, resolution parsing.

Network-free Django-side helpers only. Campaign browse still goes through the
HTTP API client (FE-CM-1); plan freeze authority is the durable
``campaign_member_policy`` on create_workflow (FE-CM-2 contracts).

Remediation (FE-CM-3 rem1):
- Durable resolution store on ``ImportSession.options`` (survives multi-search)
- Source-column completeness: every distinct match key that freezes via the
  name path must have a map entry (defaults/Id column do not waive this)
- Picks are bound to a server-side re-run of FE-CM-1 name search evidence
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Salesforce Campaign key prefix (15/18-char Id).
_CAMPAIGN_ID_RE = re.compile(r"^701[a-zA-Z0-9]{12}([a-zA-Z0-9]{3})?$")

# Providers whose journey stacks expose CAMPAIGN_MEMBER_MUTATION (execute).
# HubSpot and unknown providers remain disabled-only (SF-only CM v1).
# Prefer API maximum_authorization.campaign_member_writes when present.
_CM_EXECUTE_PROVIDER_KEYS = frozenset({"salesforce", "fake"})

# Durable draft store key on ImportSession.options (not one-shot flash).
CM_RESOLUTIONS_OPTIONS_KEY = "cm_match_resolutions_v1"

MODE_RANK = {
    "disabled": 0,
    "preview": 1,
    "plan_only": 1,
    "dry_run": 2,
    "execute": 3,
}

# Cap missing-key error detail.
_MAX_MISSING_KEYS_IN_MESSAGE = 8


class CampaignMemberSetupError(ValueError):
    """Operator-facing CM configure validation failure."""


def normalize_match_key(value: str) -> str:
    """Strip then Unicode casefold (matches product normalize_campaign_match_key)."""

    return str(value or "").strip().casefold()


def is_well_formed_campaign_id(value: str) -> bool:
    text = str(value or "").strip()
    return bool(_CAMPAIGN_ID_RE.fullmatch(text))


def campaign_member_ceiling_from_connection(
    connection: dict[str, Any] | None,
) -> str:
    """Effective stack ceiling for campaign_member_writes.

    Prefer an explicit API field when present; otherwise project from
    provider_key using the known journey-stack capability map (FE-CM-0 freeze:
    SF/fake → up to execute; providers without CM → disabled only).
    """

    if not connection:
        return "disabled"
    max_auth = connection.get("maximum_authorization") or {}
    if isinstance(max_auth, dict):
        # Prefer product-scoped list_import CM ceiling when present (Phase 1C).
        by_product = max_auth.get("by_product") or {}
        if isinstance(by_product, dict):
            for product_key in (
                "easyimports.list_import",
                "easyimports.account_list_import",
            ):
                product_auth = by_product.get(product_key)
                if (
                    isinstance(product_auth, dict)
                    and "campaign_member_writes" in product_auth
                ):
                    ceiling = str(
                        product_auth.get("campaign_member_writes") or "disabled"
                    ).strip()
                    if ceiling in MODE_RANK:
                        return ceiling
        if "campaign_member_writes" in max_auth:
            ceiling = str(max_auth.get("campaign_member_writes") or "disabled").strip()
            if ceiling in MODE_RANK:
                return ceiling
            return "disabled"
    provider = str(connection.get("provider_key") or "").strip().lower()
    if provider in _CM_EXECUTE_PROVIDER_KEYS:
        return "execute"
    return "disabled"


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def load_session_resolutions(session: Any) -> list[dict[str, Any]]:
    """Load durable verified resolutions from ImportSession.options."""

    options = getattr(session, "options", None) or {}
    if not isinstance(options, dict):
        return []
    raw = options.get(CM_RESOLUTIONS_OPTIONS_KEY)
    if not raw:
        return []
    if not isinstance(raw, list):
        return []
    connection_id = str(getattr(session, "setup_connection_id", "") or "").strip()
    if not connection_id:
        return []
    try:
        return parse_campaign_match_resolutions(raw, connection_id=connection_id)
    except CampaignMemberSetupError:
        return []


def load_session_resolutions_json(session: Any) -> str:
    entries = load_session_resolutions(session)
    if not entries:
        return "[]"
    return json.dumps(entries, separators=(",", ":"), sort_keys=True)


def save_session_resolutions(session: Any, entries: list[dict[str, Any]]) -> None:
    """Persist verified resolutions on the ImportSession (survives multi-search)."""

    options = dict(getattr(session, "options", None) or {})
    options[CM_RESOLUTIONS_OPTIONS_KEY] = list(entries)
    session.options = options
    session.save(update_fields=["options", "updated_at"])


def clear_session_resolutions(session: Any) -> None:
    options = dict(getattr(session, "options", None) or {})
    if CM_RESOLUTIONS_OPTIONS_KEY not in options:
        return
    options.pop(CM_RESOLUTIONS_OPTIONS_KEY, None)
    session.options = options
    session.save(update_fields=["options", "updated_at"])


def parse_campaign_match_resolutions(
    raw: Any,
    *,
    connection_id: str,
) -> list[dict[str, Any]]:
    """Parse durable resolution entries from form JSON (or list).

    Entries may omit ``connection_id``, ``match_mode``, and ``resolved_at``;
    those are filled from the configure context. ``match_key`` is normalized.
    """

    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise CampaignMemberSetupError(
                "Campaign match resolutions must be valid JSON."
            ) from exc
    elif isinstance(raw, list):
        parsed = raw
    else:
        raise CampaignMemberSetupError(
            "Campaign match resolutions must be a JSON list."
        )
    if not isinstance(parsed, list):
        raise CampaignMemberSetupError(
            "Campaign match resolutions must be a JSON list."
        )

    connection_id = str(connection_id or "").strip()
    if not connection_id:
        raise CampaignMemberSetupError(
            "Campaign match resolutions require a CRM connection."
        )

    seen: set[tuple[str, str]] = set()
    out: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise CampaignMemberSetupError(
                f"Resolution entry {index + 1} must be an object."
            )
        match_mode = str(item.get("match_mode") or "name_exact").strip()
        if match_mode != "name_exact":
            raise CampaignMemberSetupError(
                "Only name_exact Campaign match mode is supported."
            )
        match_key = normalize_match_key(str(item.get("match_key") or ""))
        if not match_key:
            raise CampaignMemberSetupError(
                f"Resolution entry {index + 1} needs a non-empty match key."
            )
        campaign_id = str(item.get("campaign_id") or "").strip()
        if not is_well_formed_campaign_id(campaign_id):
            raise CampaignMemberSetupError(
                f"Resolution entry {index + 1} has an invalid Campaign Id."
            )
        selection = str(item.get("selection_source") or "").strip()
        if selection not in {"unique_match", "operator_pick"}:
            raise CampaignMemberSetupError(
                f"Resolution entry {index + 1} needs selection_source "
                "unique_match or operator_pick."
            )
        entry_connection = str(item.get("connection_id") or connection_id).strip()
        if entry_connection != connection_id:
            raise CampaignMemberSetupError(
                "Campaign match resolutions must use this import's CRM connection."
            )
        resolved_at = str(item.get("resolved_at") or "").strip() or utc_now_iso()
        observed = item.get("observed_name")
        if observed is not None:
            observed = str(observed).strip() or None
        lookup_bound = bool(item.get("lookup_bound", False))
        key = (match_mode, match_key)
        if key in seen:
            raise CampaignMemberSetupError(
                f"Duplicate Campaign match resolution for {match_key!r}."
            )
        seen.add(key)
        entry: dict[str, Any] = {
            "match_mode": match_mode,
            "match_key": match_key,
            "campaign_id": campaign_id,
            "connection_id": connection_id,
            "selection_source": selection,
            "resolved_at": resolved_at,
            "lookup_bound": lookup_bound,
        }
        if observed is not None:
            entry["observed_name"] = observed
        out.append(entry)
    return out


def require_lookup_bound_resolutions(entries: list[dict[str, Any]]) -> None:
    """Reject freeform / untrusted entries that never passed server lookup."""

    for entry in entries:
        if not entry.get("lookup_bound"):
            raise CampaignMemberSetupError(
                "Campaign name resolutions must come from a verified Campaign "
                "search and pick (freeform Id injection is not allowed)."
            )


def build_campaign_member_policy(
    *,
    connection_id: str,
    default_campaign_binding: str = "",
    default_desired_status: str = "",
    campaign_id_column: str = "",
    campaign_match_column: str = "",
    member_status_column: str = "",
    campaign_match_resolutions_raw: Any = None,
) -> dict[str, Any] | None:
    """Build public ``campaign_member_policy.v2`` or None when all fields empty."""

    default_campaign = str(default_campaign_binding or "").strip()
    default_status = str(default_desired_status or "").strip()
    id_column = str(campaign_id_column or "").strip()
    match_column = str(campaign_match_column or "").strip()
    status_column = str(member_status_column or "").strip()
    resolutions = parse_campaign_match_resolutions(
        campaign_match_resolutions_raw,
        connection_id=connection_id,
    )

    if not any(
        (
            default_campaign,
            default_status,
            id_column,
            match_column,
            status_column,
            resolutions,
        )
    ):
        return None

    if default_campaign and not is_well_formed_campaign_id(default_campaign):
        raise CampaignMemberSetupError(
            "Default Campaign Id must be a well-formed Salesforce Campaign Id "
            "(701…, 15 or 18 characters)."
        )

    # Public API policy omits internal lookup_bound marker.
    public_resolutions: list[dict[str, Any]] = []
    for entry in resolutions:
        public = {
            "match_mode": entry["match_mode"],
            "match_key": entry["match_key"],
            "campaign_id": entry["campaign_id"],
            "connection_id": entry["connection_id"],
            "selection_source": entry["selection_source"],
            "resolved_at": entry["resolved_at"],
        }
        if entry.get("observed_name") is not None:
            public["observed_name"] = entry["observed_name"]
        public_resolutions.append(public)

    policy: dict[str, Any] = {
        "policy_version": "campaign_member_policy.v2",
        "campaign_match_resolutions": public_resolutions,
    }
    if default_campaign:
        policy["default_campaign_binding"] = default_campaign
    if default_status:
        policy["default_desired_status"] = default_status
    if id_column:
        policy["campaign_id_column"] = id_column
    if match_column:
        policy["campaign_match_column"] = match_column
    if status_column:
        policy["member_status_column"] = status_column
    return policy


def validate_enabled_campaign_member_policy(
    policy: dict[str, Any] | None,
    *,
    connection_id: str,
    required_match_keys: set[str] | None = None,
    require_lookup_bound: bool = False,
    bound_entries: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Fail closed when CM track is enabled without a complete policy path.

    When ``campaign_match_column`` is set, every distinct source key that will
    resolve via the name path (nonblank match cell with blank/absent Id cell)
    **must** have a map entry. Default Campaign Id and Id column do **not**
    waive missing map entries for nonblank match cells (FE-CM-0 precedence).
    """

    if not policy:
        raise CampaignMemberSetupError(
            "Campaign membership is enabled, but no Campaign membership policy "
            "was provided. Map columns or pick a default Campaign and status."
        )
    connection_id = str(connection_id or "").strip()
    if not connection_id:
        raise CampaignMemberSetupError(
            "Campaign membership requires a connected CRM on this import."
        )

    has_campaign_path = bool(
        policy.get("default_campaign_binding")
        or policy.get("campaign_id_column")
        or policy.get("campaign_match_column")
    )
    has_status_path = bool(
        policy.get("default_desired_status") or policy.get("member_status_column")
    )
    if not has_campaign_path:
        raise CampaignMemberSetupError(
            "Enabled Campaign membership needs a Campaign path: default Campaign "
            "Id, Campaign Id column, or Campaign name column."
        )
    if not has_status_path:
        raise CampaignMemberSetupError(
            "Enabled Campaign membership needs a status path: default member "
            "status or member status column."
        )

    match_column = policy.get("campaign_match_column")
    resolutions = policy.get("campaign_match_resolutions") or []
    if require_lookup_bound and bound_entries is not None:
        require_lookup_bound_resolutions(bound_entries)
    elif require_lookup_bound:
        # Public policy strips lookup_bound; use bound_entries when available.
        pass

    for entry in resolutions:
        if str(entry.get("connection_id") or "") != connection_id:
            raise CampaignMemberSetupError(
                "Campaign match resolutions must use this import's CRM connection."
            )

    if match_column:
        # Always require coverage for required source keys (no default/Id waiver).
        required = set(required_match_keys or ())
        if required_match_keys is None:
            # Caller did not supply source scan — fail closed if map empty.
            if not resolutions:
                raise CampaignMemberSetupError(
                    "Campaign name column requires a durable name→Id resolution "
                    "for every distinct non-blank name in the uploaded list."
                )
        else:
            require_resolutions_cover_keys(resolutions, required)

    return policy


def require_resolutions_cover_keys(
    resolutions: list[dict[str, Any]],
    required_keys: set[str],
) -> None:
    """Fail closed when any required normalized match key lacks a map entry."""

    present = {
        normalize_match_key(str(entry.get("match_key") or ""))
        for entry in resolutions
        if isinstance(entry, dict)
    }
    present.discard("")
    missing = sorted(key for key in required_keys if key and key not in present)
    if not missing:
        return
    shown = missing[:_MAX_MISSING_KEYS_IN_MESSAGE]
    more = len(missing) - len(shown)
    detail = ", ".join(repr(key) for key in shown)
    if more > 0:
        detail = f"{detail}, … (+{more} more)"
    raise CampaignMemberSetupError(
        "Missing Campaign name→Id resolutions for: "
        f"{detail}. Search and pick each distinct name before starting."
    )


def _iter_source_row_dicts(source: Any) -> Iterable[dict[str, Any]]:
    """Yield row mappings from a completed SourceFile (CSV or XLSX)."""

    path = Path(getattr(source, "stored_path", "") or "")
    if not path.is_file():
        raise CampaignMemberSetupError(
            "The uploaded list file is not available to validate Campaign names."
        )
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        import openpyxl

        workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
        try:
            sheet_ref = getattr(source, "xlsx_sheet", None)
            if isinstance(sheet_ref, int):
                worksheet = workbook.worksheets[sheet_ref]
            elif isinstance(sheet_ref, str) and sheet_ref.strip():
                worksheet = workbook[sheet_ref]
            else:
                worksheet = workbook.active
            rows = worksheet.iter_rows(values_only=True)
            try:
                header_row = next(rows)
            except StopIteration:
                return
            headers = [str(cell or "").strip() for cell in header_row]
            for values in rows:
                yield {
                    headers[index]: ("" if values[index] is None else values[index])
                    for index in range(len(headers))
                    if headers[index]
                }
        finally:
            workbook.close()
        return

    encoding = str(getattr(source, "csv_encoding", "") or "").strip() or "utf-8-sig"
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield {str(key or ""): value for key, value in row.items()}


def distinct_match_keys_requiring_resolution(
    source: Any | None,
    *,
    match_column: str,
    id_column: str = "",
) -> set[str]:
    """Normalized match keys that freeze via the name path (not Id column).

    Per FE-CM-0 precedence, a nonblank match cell uses the map even when a
    default Campaign Id exists. Only a nonblank well-formed Id-column cell
    skips the map for that row.
    """

    match_column = str(match_column or "").strip()
    if not match_column:
        return set()
    if source is None:
        raise CampaignMemberSetupError(
            "Campaign name column requires a completed uploaded list to validate "
            "resolutions."
        )
    columns = list(getattr(source, "columns", None) or [])
    if columns and match_column not in columns:
        raise CampaignMemberSetupError(
            f"Campaign name column {match_column!r} is not in the uploaded list."
        )
    id_column = str(id_column or "").strip()
    if id_column and columns and id_column not in columns:
        raise CampaignMemberSetupError(
            f"Campaign Id column {id_column!r} is not in the uploaded list."
        )

    required: set[str] = set()
    for row in _iter_source_row_dicts(source):
        if id_column:
            id_cell = str(row.get(id_column, "") or "").strip()
            if id_cell:
                # Nonblank Id path — map not consulted for this row.
                continue
        match_cell = row.get(match_column, "")
        if match_cell is None:
            continue
        key = normalize_match_key(str(match_cell))
        if key:
            required.add(key)
    return required


def resolution_from_lookup_evidence(
    *,
    connection_id: str,
    match_key_raw: str,
    campaign_id: str,
    search_result: dict[str, Any],
) -> dict[str, Any]:
    """Build one lookup-bound resolution from a server-side FE-CM-1 search.

    Trusts only the search response body + requested campaign_id membership in
    that response. Derives selection_source from returned candidate count.
    """

    if not isinstance(search_result, dict):
        raise CampaignMemberSetupError("Campaign search returned an invalid result.")
    if search_result.get("too_many_matches") or search_result.get("truncated"):
        raise CampaignMemberSetupError(
            "Too many Campaigns share this exact name. Use a Campaign Id column "
            "or default Campaign Id instead of name resolution."
        )
    q = str(search_result.get("q") or match_key_raw or "").strip()
    match_key = normalize_match_key(q)
    if not match_key:
        raise CampaignMemberSetupError("Match key is required.")
    # Search q is the authority for the match key (not a separate browser field).
    requested_id = str(campaign_id or "").strip()
    if not is_well_formed_campaign_id(requested_id):
        raise CampaignMemberSetupError("Picked Campaign Id is not well-formed.")
    campaigns = search_result.get("campaigns") or []
    if not isinstance(campaigns, list) or not campaigns:
        raise CampaignMemberSetupError(
            "No active Campaign matched that exact name. Nothing was saved."
        )
    match = None
    for item in campaigns:
        if not isinstance(item, dict):
            continue
        if str(item.get("id") or "").strip() == requested_id:
            match = item
            break
    if match is None:
        raise CampaignMemberSetupError(
            "The selected Campaign is not in the current search results. "
            "Search again and pick from the list."
        )
    returned = int(search_result.get("returned") or len(campaigns))
    if returned == 1:
        selection_source = "unique_match"
    elif 2 <= returned <= 25:
        selection_source = "operator_pick"
    else:
        raise CampaignMemberSetupError(
            "Unexpected Campaign search result size; pick was not saved."
        )
    observed = str(match.get("name") or "").strip() or None
    entry: dict[str, Any] = {
        "match_mode": "name_exact",
        "match_key": match_key,
        "campaign_id": requested_id,
        "connection_id": str(connection_id).strip(),
        "selection_source": selection_source,
        "resolved_at": utc_now_iso(),
        "lookup_bound": True,
    }
    if observed is not None:
        entry["observed_name"] = observed
    return entry


def append_resolution_entry(
    existing: list[dict[str, Any]] | str | None,
    *,
    connection_id: str,
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge one verified entry (unique on match_mode+match_key)."""

    if isinstance(existing, list):
        current = parse_campaign_match_resolutions(
            existing, connection_id=connection_id
        )
    else:
        current = parse_campaign_match_resolutions(
            existing or "[]", connection_id=connection_id
        )
    if not entry.get("lookup_bound"):
        raise CampaignMemberSetupError(
            "Only lookup-bound Campaign resolutions may be saved."
        )
    key = normalize_match_key(str(entry.get("match_key") or ""))
    if not key:
        raise CampaignMemberSetupError("Match key is required.")
    kept = [item for item in current if item.get("match_key") != key]
    kept.append(entry)
    return kept


# ---------------------------------------------------------------------------
# Phase 7A: Campaign-scoped default-status picker (D10 independent axes / D12).
#
# Network-free Django mirror of
# ``mappings_2.products.list_import_operator_path_phase7a_campaign_status``.
# Django must not import ``mappings_2`` (import-boundary rule), so the picker
# algorithm is duplicated here over the same ``campaign_member_policy.v2`` field
# names. The FE-CM-1 member-status browse supplies ``campaign_statuses``; this
# module performs no CRM I/O.
# ---------------------------------------------------------------------------

# Picker modes (mirror StatusPickerMode).
STATUS_PICKER_SINGLE_CAMPAIGN = "single_campaign"
STATUS_PICKER_INTERSECTION = "intersection"
STATUS_PICKER_REQUIRE_PER_ROW = "require_per_row"


def resolved_default_campaign_ids(
    *,
    default_campaign_binding: str = "",
    resolutions: Iterable[dict[str, Any]] | None = None,
) -> list[str]:
    """Bounded, order-preserving Campaign Ids known at configure time.

    Default Campaign binding plus each verified name resolution's Campaign Id.
    A per-row ``campaign_id_column`` is intentionally excluded (open set).
    """

    ordered: list[str] = []
    seen: set[str] = set()
    default_campaign = str(default_campaign_binding or "").strip()
    if default_campaign and default_campaign not in seen:
        seen.add(default_campaign)
        ordered.append(default_campaign)
    for entry in resolutions or ():
        if not isinstance(entry, dict):
            continue
        cid = str(entry.get("campaign_id") or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            ordered.append(cid)
    return ordered


def _intersection_preserving_order(
    status_lists: list[list[str]],
) -> list[str]:
    """Statuses present in every Campaign, in the first Campaign's order."""

    if not status_lists:
        return []
    others = [set(lst) for lst in status_lists[1:]]
    result: list[str] = []
    seen: set[str] = set()
    for status in status_lists[0]:
        if status in seen:
            continue
        if all(status in other for other in others):
            seen.add(status)
            result.append(status)
    return result


def campaign_scoped_status_choices(
    *,
    resolved_campaign_ids: list[str],
    campaign_statuses: dict[str, list[str]],
    has_open_campaign_id_column: bool = False,
) -> dict[str, Any]:
    """Resolve the Campaign-scoped default-status picker (D12).

    Returns a dict with ``mode``, ``choices`` (bounded allowlist),
    ``requires_per_row_status``, ``resolved_campaign_ids``, and ``reason``.
    Fails closed when a resolved Campaign has no fetched status list.
    """

    ids = [str(cid).strip() for cid in resolved_campaign_ids if str(cid).strip()]

    if has_open_campaign_id_column:
        return {
            "mode": STATUS_PICKER_REQUIRE_PER_ROW,
            "choices": [],
            "requires_per_row_status": True,
            "resolved_campaign_ids": ids,
            "reason": (
                "A Campaign Id column resolves to an unbounded per-row Campaign "
                "set; map a member status column instead of a run default."
            ),
        }

    if not ids:
        raise CampaignMemberSetupError(
            "A Campaign-scoped status picker needs at least one resolved "
            "Campaign (default Campaign or a name resolution)."
        )

    missing = [cid for cid in ids if cid not in campaign_statuses]
    if missing:
        raise CampaignMemberSetupError(
            "Missing member statuses for resolved Campaign(s). Load each "
            "Campaign's statuses before bounding the default-status picker."
        )

    if len(ids) == 1:
        choices = [str(s) for s in campaign_statuses[ids[0]]]
        return {
            "mode": STATUS_PICKER_SINGLE_CAMPAIGN,
            "choices": choices,
            "requires_per_row_status": False,
            "resolved_campaign_ids": ids,
            "reason": "Single resolved Campaign; picker shows its ordered statuses.",
        }

    intersection = _intersection_preserving_order(
        [[str(s) for s in campaign_statuses[cid]] for cid in ids]
    )
    if intersection:
        return {
            "mode": STATUS_PICKER_INTERSECTION,
            "choices": intersection,
            "requires_per_row_status": False,
            "resolved_campaign_ids": ids,
            "reason": (
                "Multiple resolved Campaigns with a non-empty status "
                "intersection; picker shows the shared statuses."
            ),
        }
    return {
        "mode": STATUS_PICKER_REQUIRE_PER_ROW,
        "choices": [],
        "requires_per_row_status": True,
        "resolved_campaign_ids": ids,
        "reason": (
            "Multiple resolved Campaigns with an empty status intersection; "
            "map a per-row member status column instead of a run default."
        ),
    }


def validate_default_status_choice(
    default_status: str | None,
    decision: dict[str, Any],
) -> None:
    """Fail closed when a run default status is not the bounded picker (D12)."""

    text = str(default_status or "").strip()
    if decision.get("requires_per_row_status"):
        if text:
            raise CampaignMemberSetupError(
                "A run default status is not allowed here: "
                f"{decision.get('reason', '')} Map a member status column."
            )
        return
    if not text:
        return
    if text not in (decision.get("choices") or []):
        raise CampaignMemberSetupError(
            f"Default status {text!r} is not an allowed status for the resolved "
            f"Campaign(s); allowed={list(decision.get('choices') or [])!r}."
        )


def resolution_technical_rows(policy: dict[str, Any] | None) -> list[dict[str, str]]:
    """Safe rows for technical details (Ids only; no secrets)."""

    if not policy:
        return []
    rows: list[dict[str, str]] = []
    for entry in policy.get("campaign_match_resolutions") or []:
        if not isinstance(entry, dict):
            continue
        rows.append(
            {
                "match_key": str(entry.get("match_key") or ""),
                "campaign_id": str(entry.get("campaign_id") or ""),
                "selection_source": str(entry.get("selection_source") or ""),
                "observed_name": str(entry.get("observed_name") or ""),
                "resolved_at": str(entry.get("resolved_at") or ""),
            }
        )
    return rows
