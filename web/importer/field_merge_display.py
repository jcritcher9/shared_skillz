"""Display-only mapping for public field_merge_plan frames (1B-UI / DRC-1).

No ``mappings_2`` imports and no field business rules.
"""

from __future__ import annotations

from typing import Any

# Frozen operator sentence (DRC-1). Tests pin this exact text.
FIELD_FILL_EMPTY_SENTENCE = (
    "Empty fields on the surviving record will be filled from the other records "
    "in this group. Fields the survivor already has keep their current values. "
    "No field is overwritten just because another record has a different value."
)

KEEP_SURVIVOR_VALUE_LABEL = "Keep survivor value"
FILL_BLANK_SURVIVOR_FIELD_LABEL = "Fill blank survivor field"
DOMAIN_FOLLOWS_WEBSITE_LABEL = "Domain follows the projected website"
EMPTY_FIELD_DECISIONS_COPY = (
    "Survivor already has every compared field; nothing is copied from the "
    "other records."
)

# Production Account rules from merge_policy.py (not the 1B-API fixture token).
ACCOUNT_EMPTY_FILL_RULES: tuple[str, ...] = (
    "fill_empty_from_ranked_loser",
    "fill_atomic_billing_address_from_ranked_loser",
    "fill_coherent_billing_address_from_ranked_loser",
)

# Production People rules from person_merge_policy.py.
PERSON_EMPTY_FILL_RULES: tuple[str, ...] = (
    "fill_empty_email_from_ranked_person",
    "fill_empty_title_from_ranked_person",
    "fill_empty_linkedin_from_ranked_person",
    "fill_empty_work_phone_from_ranked_person",
    "fill_empty_mobile_from_ranked_person",
    "fill_atomic_mailing_address_from_ranked_person",
    "fill_coherent_mailing_address_from_ranked_person",
)

# Historical / 1B-API fixture spelling. Current engines emit fill_empty_*.
LEGACY_EMPTY_FILL_RULES: tuple[str, ...] = ("empty_fill_from_ranked_loser",)

_EMPTY_FILL_RULES = frozenset(
    ACCOUNT_EMPTY_FILL_RULES + PERSON_EMPTY_FILL_RULES + LEGACY_EMPTY_FILL_RULES
)
_DERIVE_DOMAIN_RULE = "derive_domain_from_projected_website"
_EMPTY_FILL_PREFIXES = (
    "empty_fill_",
    "fill_empty_",
    "fill_atomic_",
    "fill_coherent_",
)


def merge_rule_operator_label(merge_rule: str) -> str:
    """Map a public merge_rule token to the locked operator label."""

    key = str(merge_rule or "").strip()
    if key == _DERIVE_DOMAIN_RULE:
        return DOMAIN_FOLLOWS_WEBSITE_LABEL
    if key in _EMPTY_FILL_RULES or key.startswith(_EMPTY_FILL_PREFIXES):
        return FILL_BLANK_SURVIVOR_FIELD_LABEL
    return key.replace("_", " ").strip() or key


def overwrite_operator_label(overwrites_existing_value: bool) -> str:
    if overwrites_existing_value:
        return "Overwrite survivor value"
    return KEEP_SURVIVOR_VALUE_LABEL


def field_merge_display(plan: Any) -> dict[str, Any]:
    """Map a public field_merge_plan frame into template-ready display rows."""

    if not isinstance(plan, dict):
        return {
            "present": False,
            "survivor_id": None,
            "execution_intent": None,
            "execution_intent_summary": None,
            "empty_fill_sentence": FIELD_FILL_EMPTY_SENTENCE,
            "empty_decisions_copy": EMPTY_FIELD_DECISIONS_COPY,
            "field_decisions": [],
            "merge_conflicts": [],
            "populate_field_keys": [],
            "consolidate_pairs": [],
        }
    decisions_out: list[dict[str, Any]] = []
    for item in plan.get("field_decisions") or []:
        if not isinstance(item, dict):
            continue
        merge_rule = str(item.get("merge_rule") or "")
        overwrites = bool(item.get("overwrites_existing_value"))
        decisions_out.append(
            {
                "logical_field_key": str(item.get("logical_field_key") or ""),
                "value": item.get("value"),
                "source_member_id": item.get("source_member_id"),
                "source_field_key": item.get("source_field_key"),
                "merge_rule": merge_rule,
                "merge_rule_label": merge_rule_operator_label(merge_rule),
                "overwrites_existing_value": overwrites,
                "overwrite_label": overwrite_operator_label(overwrites),
            }
        )
    conflicts_out: list[dict[str, Any]] = []
    for row in plan.get("merge_conflicts") or []:
        if isinstance(row, dict):
            conflicts_out.append(dict(row))
    sequence = plan.get("planned_sequence") or {}
    pairs: list[dict[str, Any]] = []
    if isinstance(sequence, dict):
        for pair in sequence.get("consolidate_pairs") or []:
            if isinstance(pair, dict):
                pairs.append(
                    {
                        "source_id": str(pair.get("source_id") or ""),
                        "target_id": str(pair.get("target_id") or ""),
                    }
                )
    return {
        "present": True,
        "survivor_id": str(plan.get("survivor_id") or "") or None,
        "execution_intent": str(plan.get("execution_intent") or "") or None,
        "execution_intent_summary": str(plan.get("execution_intent_summary") or "")
        or None,
        "empty_fill_sentence": FIELD_FILL_EMPTY_SENTENCE,
        "empty_decisions_copy": EMPTY_FIELD_DECISIONS_COPY,
        "field_decisions": decisions_out,
        "merge_conflicts": conflicts_out,
        "populate_field_keys": [
            str(key) for key in (plan.get("populate_field_keys") or [])
        ],
        "consolidate_pairs": pairs,
    }
