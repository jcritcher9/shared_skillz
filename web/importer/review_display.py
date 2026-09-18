"""OUT-7 review-table columns (Django-only; no mappings_2 import).

Mirrors the OUT-1 review-core tuples and the OUT-7 typed extras so
operator-facing validation / list-dupe / CRM-match tables do not dump
LIST_CANON or back-fill leftover columns after a 7-col cap.

Authority:
  mappings_2/codex_context/cross_agent_eval/project_implementations/
    list_import_operator_output_and_settings_clarity.md
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

REVIEW_CORE_ACCOUNT_ROLES: Final[tuple[str, ...]] = (
    "account_name",
    "account_domain",
    "account_website",
    "account_billing_city",
    "account_billing_state",
)

REVIEW_CORE_PEOPLE_ROLES: Final[tuple[str, ...]] = (
    "contact_first_name",
    "contact_last_name",
    "contact_email",
    "account_name",
    "contact_title",
    "contact_work_phone",
)

ENTITY_FAMILY_ACCOUNTS: Final[str] = "accounts"
ENTITY_FAMILY_PEOPLE: Final[str] = "people"

PRODUCT_KEY_PEOPLE: Final[str] = "easyimports.list_import"
PRODUCT_KEY_ACCOUNTS: Final[str] = "easyimports.account_list_import"

# One alias group per extra column. First name that exists on the frame wins.
VALIDATION_FAILED_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("validation_message", "validation_reason", "reason"),
)
LIST_DUPLICATES_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("list_duplicate_rules",),
    ("list_duplicate_evidence_rules",),
)
MULTIPLE_CRM_MATCHES_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("candidate_person_full_name", "name"),
    ("candidate_person_email",),
    ("candidate_person_type",),
    ("match_score",),
)
MULTIPLE_CRM_ACCOUNT_MATCHES_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("candidate_account_name", "candidate_acct_name"),
    ("candidate_account_domain", "candidate_acct_domain"),
    ("match_score",),
)
UPLOADED_ACCOUNT_ID_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("uploaded_id",),
    ("found_id",),
    ("found_name",),
    ("option_label",),
)
UPLOADED_PERSON_ID_EXTRAS: Final[tuple[tuple[str, ...], ...]] = (
    ("uploaded_id",),
    ("found_id",),
    ("found_name",),
    ("option_label",),
)

DECISION_EXTRAS: Final[dict[str, tuple[tuple[str, ...], ...]]] = {
    "validation_failed": VALIDATION_FAILED_EXTRAS,
    "list_duplicates": LIST_DUPLICATES_EXTRAS,
    "multiple_crm_matches": MULTIPLE_CRM_MATCHES_EXTRAS,
    "multiple_crm_account_matches": MULTIPLE_CRM_ACCOUNT_MATCHES_EXTRAS,
    "uploaded_account_id_disagreement": UPLOADED_ACCOUNT_ID_EXTRAS,
    "uploaded_person_id_disagreement": UPLOADED_PERSON_ID_EXTRAS,
}

# People-only / account-only roles used to infer family from the frame.
_PEOPLE_ONLY_ROLES: Final[frozenset[str]] = frozenset(
    {
        "contact_first_name",
        "contact_last_name",
        "contact_email",
        "contact_title",
        "contact_work_phone",
    }
)
_ACCOUNT_ONLY_ROLES: Final[frozenset[str]] = frozenset(
    {
        "account_domain",
        "account_website",
        "account_billing_city",
        "account_billing_state",
    }
)

# Download-core roles that must not appear on review unless they are also
# review-core or a typed extra (characterization aid for tests).
DOWNLOAD_CORE_NOT_REVIEW_PEOPLE: Final[frozenset[str]] = frozenset(
    {
        "contact_mobile",
        "contact_mailing_street",
        "contact_mailing_city",
        "contact_mailing_state",
        "contact_mailing_postal_code",
        "contact_mailing_country",
        "contact_id",
        "account_id",
    }
)
DOWNLOAD_CORE_NOT_REVIEW_ACCOUNT: Final[frozenset[str]] = frozenset(
    {
        "account_phone",
        "account_industry",
        "account_employee_count",
        "account_type",
        "account_billing_street",
        "account_billing_postal_code",
        "account_billing_country",
        "account_id",
    }
)


def role_column_aliases(role: str) -> tuple[str, ...]:
    """Frame names that count as one review-core role (prefer current names)."""

    aliases = [role, f"{role}_final"]
    if role.startswith("account_"):
        rest = role[len("account_") :]
        aliases.extend((f"acct_{rest}", f"acct_{rest}_final"))
    return tuple(aliases)


def _first_existing(available: set[str], candidates: Sequence[str]) -> str | None:
    for name in candidates:
        if name in available:
            return name
    return None


def _role_present(available: set[str], role: str) -> bool:
    return _first_existing(available, role_column_aliases(role)) is not None


def resolve_review_entity_family(
    *,
    product_key: str = "",
    setup_entity: str = "",
    decision_type: str | None = None,
    available: Sequence[str] = (),
) -> str:
    entity = str(setup_entity or "").strip().lower()
    if entity in {ENTITY_FAMILY_PEOPLE, ENTITY_FAMILY_ACCOUNTS}:
        return entity
    key = str(product_key or "").strip()
    if key == PRODUCT_KEY_PEOPLE:
        return ENTITY_FAMILY_PEOPLE
    if key == PRODUCT_KEY_ACCOUNTS:
        return ENTITY_FAMILY_ACCOUNTS
    dtype = str(decision_type or "")
    if dtype in {"multiple_crm_matches", "uploaded_person_id_disagreement"}:
        return ENTITY_FAMILY_PEOPLE
    if dtype in {
        "multiple_crm_account_matches",
        "uploaded_account_id_disagreement",
    }:
        return ENTITY_FAMILY_ACCOUNTS
    available_set = set(available)
    people_hits = sum(
        1 for role in _PEOPLE_ONLY_ROLES if _role_present(available_set, role)
    )
    account_hits = sum(
        1 for role in _ACCOUNT_ONLY_ROLES if _role_present(available_set, role)
    )
    if people_hits and not account_hits:
        return ENTITY_FAMILY_PEOPLE
    if account_hits and not people_hits:
        return ENTITY_FAMILY_ACCOUNTS
    return ENTITY_FAMILY_PEOPLE


def review_core_roles(entity_family: str) -> tuple[str, ...]:
    if entity_family == ENTITY_FAMILY_ACCOUNTS:
        return REVIEW_CORE_ACCOUNT_ROLES
    return REVIEW_CORE_PEOPLE_ROLES


def select_existing_review_columns(
    *,
    decision_type: str | None,
    available: Sequence[str],
    product_key: str = "",
    setup_entity: str = "",
) -> list[str]:
    """Return review-core ∪ typed extras that exist on ``available``.

    Does not back-fill leftover LIST_CANON. Does not cap at 7 columns.
    """

    available_set = set(available)
    family = resolve_review_entity_family(
        product_key=product_key,
        setup_entity=setup_entity,
        decision_type=decision_type,
        available=available,
    )
    ordered: list[str] = []

    def _add(name: str | None) -> None:
        if name and name in available_set and name not in ordered:
            ordered.append(name)

    for role in review_core_roles(family):
        _add(_first_existing(available_set, role_column_aliases(role)))
    for aliases in DECISION_EXTRAS.get(str(decision_type or ""), ()):
        _add(_first_existing(available_set, aliases))
    return ordered
