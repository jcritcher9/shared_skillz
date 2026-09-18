"""DRC-2 review-card display columns and winner sentence (Django-only).

No ``mappings_2`` imports. Headers are operator labels; empty cells are an
em dash. Winner copy walks the locked reason table top to bottom.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Mapping

EMPTY_CELL = "—"

ACCOUNT_FIELD_COLUMNS: tuple[dict[str, str], ...] = (
    {"key": "name", "operator_label": "Name"},
    {"key": "created_date", "operator_label": "Created date"},
    {"key": "domain", "operator_label": "Domain"},
    {"key": "website", "operator_label": "Website"},
    {"key": "location", "operator_label": "Location"},
    {"key": "account_type", "operator_label": "Type"},
    {"key": "owner", "operator_label": "Owner"},
    {"key": "last_activity", "operator_label": "Last activity"},
)
PERSON_FIELD_COLUMNS: tuple[dict[str, str], ...] = (
    {"key": "name", "operator_label": "Name"},
    {"key": "created_date", "operator_label": "Created date"},
    {"key": "email", "operator_label": "Email"},
    {"key": "company_account", "operator_label": "Account"},
    {"key": "title", "operator_label": "Title"},
    {"key": "person_type", "operator_label": "Type"},
    {"key": "owner", "operator_label": "Owner"},
)

_CREATED_DATE_FIELD_KEYS = (
    "created_date",
    "CreatedDate",
    "createdate",
    "account_created_date",
    "contact_created_date",
    "acct_created_date",
)
_CREATED_DATE_EVIDENCE_KEYS = (
    "created_date_raw",
    "created_at",
    "created_date",
    "CreatedDate",
)
ACCOUNT_RANKING_COLUMNS: tuple[dict[str, str], ...] = (
    {"key": "open_opportunity_count", "operator_label": "Open opportunities"},
    {"key": "closed_won_opportunity_count", "operator_label": "Won opportunities"},
)

NOT_MERGEABLE_PERSON_CONFLICT = "Not mergeable: genuine person / type conflict."
NOT_MERGEABLE = "Not mergeable: this group cannot be merged."
ONLY_CUSTOMER = "Recommended because this is the only Customer in the group."
CONTACT_WITH_ACCOUNT = (
    "Recommended because it is a Contact with an Account "
    "(Leads in this group cannot survive)."
)
CONTACT_OVER_LEAD = (
    "Recommended because it is a Contact (Leads in this group cannot survive)."
)
MORE_RECENT_AND_MORE_COMPLETE = (
    "Recommended because it has more recent activity and a more complete record."
)
MORE_RECENT = "Recommended because it has more recent activity."
MORE_COMPLETE = "Recommended because it has a more complete record."
MORE_OPPORTUNITY = "Recommended because it has stronger opportunity history."
PREFERRED_OWNER = "Recommended because it is owned by a preferred owner."
HIGHER_HEALTH = "Recommended because its overall record health is higher."
HIGHEST_RANKED = "Recommended as the highest-ranked eligible survivor."
NO_RECOMMENDATION = "No recommended survivor for this group."

_MERGE_ACTIONS = frozenset({"approve", "override_survivor"})


def format_created_date_cell(value: Any) -> str:
    """Operator-facing created date: calendar day, never a raw timestamp."""

    if value is None or value is True or value is False:
        return EMPTY_CELL
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "<na>", "nat", "<nat>"}:
        return EMPTY_CELL
    if len(text) >= 10 and text[4] == "-" and text[7] == "-":
        return text[:10]
    return text


def review_field_columns(group: Mapping[str, Any]) -> list[dict[str, str]]:
    """Locked {key, operator_label} spec for one review group."""

    entity = str(group.get("entity_family") or "")
    if entity == "person":
        return [dict(col) for col in PERSON_FIELD_COLUMNS]
    columns = [dict(col) for col in ACCOUNT_FIELD_COLUMNS]
    members = [
        member
        for member in (group.get("members") or [])
        if isinstance(member, dict)
    ]
    for ranking_col in ACCOUNT_RANKING_COLUMNS:
        key = ranking_col["key"]
        if any(
            isinstance(member.get("ranking_evidence"), dict)
            and key in member["ranking_evidence"]
            for member in members
        ):
            columns.append(dict(ranking_col))
    return columns


def review_cell_value(member: Mapping[str, Any], column: Mapping[str, str]) -> str:
    key = str(column.get("key") or "")
    if key in {"open_opportunity_count", "closed_won_opportunity_count"}:
        evidence = member.get("ranking_evidence") or {}
        if not isinstance(evidence, dict) or key not in evidence:
            return EMPTY_CELL
        raw = evidence.get(key)
        if raw is None or raw == "":
            return EMPTY_CELL
        return str(raw)
    if key == "created_date":
        fields = member.get("display_fields") or {}
        if isinstance(fields, dict):
            for field_key in _CREATED_DATE_FIELD_KEYS:
                formatted = format_created_date_cell(fields.get(field_key))
                if formatted != EMPTY_CELL:
                    return formatted
        evidence = member.get("ranking_evidence") or {}
        if isinstance(evidence, dict):
            for evidence_key in _CREATED_DATE_EVIDENCE_KEYS:
                formatted = format_created_date_cell(evidence.get(evidence_key))
                if formatted != EMPTY_CELL:
                    return formatted
        return EMPTY_CELL
    fields = member.get("display_fields") or {}
    if not isinstance(fields, dict):
        return EMPTY_CELL
    raw = fields.get(key)
    if raw is None or str(raw) == "":
        return EMPTY_CELL
    return str(raw)


def winner_reason(group: Mapping[str, Any]) -> tuple[str, str]:
    """First matching locked reason. Returns (reason_id, sentence)."""

    blockers = [str(item) for item in (group.get("execution_blockers") or [])]
    allowed = {str(item) for item in (group.get("allowed_actions") or [])}
    entity = str(group.get("entity_family") or "")
    members = [
        member
        for member in (group.get("members") or [])
        if isinstance(member, dict)
    ]
    recommended = _recommended_member(group, members)

    if "person_type_conflict" in blockers:
        return "not_mergeable_person_conflict", NOT_MERGEABLE_PERSON_CONFLICT
    if blockers or not (allowed & _MERGE_ACTIONS):
        return "not_mergeable", NOT_MERGEABLE

    if recommended is None:
        return "no_recommendation", NO_RECOMMENDATION

    rec_ev = _evidence(recommended)
    others_all = [member for member in members if member is not recommended]
    others_eligible = [
        member for member in others_all if bool(member.get("survivor_eligible"))
    ]

    if entity == "company" and _truthy(rec_ev.get("is_customer")):
        if not any(_truthy(_evidence(other).get("is_customer")) for other in others_all):
            return "only_customer", ONLY_CUSTOMER

    if entity == "person" and str(rec_ev.get("person_type") or "") == "Contact":
        has_lead = any(
            str(_evidence(other).get("person_type") or "") == "Lead"
            for other in others_all
        )
        if (
            _truthy(rec_ev.get("has_account"))
            and _truthy(rec_ev.get("contact_priority_applied"))
            and has_lead
        ):
            return "contact_with_account", CONTACT_WITH_ACCOUNT
        if _truthy(rec_ev.get("contact_priority_applied")) and has_lead:
            return "contact_over_lead", CONTACT_OVER_LEAD

    more_recent = _strictly_more_recent(rec_ev, others_eligible)
    more_complete = _strictly_higher(rec_ev, others_eligible, "completeness_score")
    if more_recent and more_complete:
        return "more_recent_and_more_complete", MORE_RECENT_AND_MORE_COMPLETE
    if more_recent:
        return "more_recent", MORE_RECENT
    if more_complete:
        return "more_complete", MORE_COMPLETE

    if entity == "company" and _strictly_higher(
        rec_ev, others_eligible, "opportunity_score"
    ):
        return "more_opportunity", MORE_OPPORTUNITY

    if _truthy(rec_ev.get("is_preferred_owner")):
        if not any(
            _truthy(_evidence(other).get("is_preferred_owner"))
            for other in others_eligible
        ):
            return "preferred_owner", PREFERRED_OWNER

    if _strictly_higher(rec_ev, others_eligible, "total_health_score"):
        return "higher_health", HIGHER_HEALTH

    return "highest_ranked", HIGHEST_RANKED


def _recommended_member(
    group: Mapping[str, Any], members: list[dict[str, Any]]
) -> dict[str, Any] | None:
    flagged = next((member for member in members if member.get("recommended")), None)
    if flagged is not None:
        return flagged
    recommended_id = str(group.get("recommended_survivor_id") or "").strip()
    if not recommended_id:
        return None
    return next(
        (
            member
            for member in members
            if str(member.get("record_id") or "") == recommended_id
        ),
        None,
    )


def _evidence(member: Mapping[str, Any]) -> dict[str, Any]:
    evidence = member.get("ranking_evidence") or {}
    return evidence if isinstance(evidence, dict) else {}


def _truthy(value: Any) -> bool:
    return value is True or value == 1 or value == "true" or value == "True"


def _number(value: Any) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _strictly_more_recent(
    rec_ev: Mapping[str, Any], others: list[dict[str, Any]]
) -> bool:
    rec_age = _number(rec_ev.get("last_activity_age_days"))
    if rec_age is None:
        return False
    for other in others:
        other_age = _number(_evidence(other).get("last_activity_age_days"))
        if other_age is None:
            continue
        if not rec_age < other_age:
            return False
    return True


def _strictly_higher(
    rec_ev: Mapping[str, Any], others: list[dict[str, Any]], key: str
) -> bool:
    rec_score = _number(rec_ev.get(key))
    if rec_score is None:
        return False
    for other in others:
        other_score = _number(_evidence(other).get(key))
        if other_score is None:
            continue
        if not rec_score > other_score:
            return False
    return True
