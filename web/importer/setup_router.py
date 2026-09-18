"""Pure setup-intent router for EasyImports web workflow creation.

Operators choose entity, operation, and (when matching) reference source.
This module derives the existing backend product box and upload contract.
It is independent of Django persistence and of observed upload state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Entity = Literal["accounts", "people"]
Operation = Literal["clean_only", "crm_matching"]
ReferenceSource = Literal["none", "uploaded", "connected_crm"]
PeopleOutput = Literal["contact", "lead"]
AcquisitionRequirement = Literal["disabled", "execute", "prohibited"]

VALID_ENTITIES: frozenset[str] = frozenset({"accounts", "people"})
VALID_OPERATIONS: frozenset[str] = frozenset({"clean_only", "crm_matching"})
VALID_REFERENCE_SOURCES: frozenset[str] = frozenset(
    {"none", "uploaded", "connected_crm"}
)
# Phase 5: connected CRM import setup is enabled (API binds connection_id +
# reference_acquisition execute on list / account-list create).
CONNECTED_CRM_ENABLED_FOR_IMPORT_SETUP = True

DETACHED_ROLE_PREFIX = "detached__"
VALID_PEOPLE_OUTPUTS: frozenset[str] = frozenset({"contact", "lead"})

# All known upload roles across import products (not duplicate-resolution).
ALL_IMPORT_ROLES: frozenset[str] = frozenset(
    {
        "dataset",
        "raw_list",
        "accounts",
        "contacts",
        "leads",
        "users",
        "territory",
        "company_exclusions",
        "industry_mapping",
    }
)

PEOPLE_MATCHING_OPTIONAL_ROLES: frozenset[str] = frozenset(
    {
        "accounts",
        "contacts",
        "leads",
        "users",
        "territory",
        "company_exclusions",
        "industry_mapping",
    }
)

PEOPLE_SUPPLIED_REFERENCE_ROLES: frozenset[str] = frozenset(
    {"accounts", "contacts", "leads"}
)

ACCOUNT_MATCHING_OPTIONAL_ROLES: frozenset[str] = frozenset()


class RouteError(ValueError):
    """Operator intent cannot be routed to a backend product."""


@dataclass(frozen=True, slots=True)
class OperatorIntent:
    entity: Entity
    operation: Operation
    reference_source: ReferenceSource
    people_output: PeopleOutput | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "entity": self.entity,
            "operation": self.operation,
            "reference_source": self.reference_source,
            "people_output": self.people_output,
        }


@dataclass(frozen=True, slots=True)
class RouteDecision:
    """Backend product and source contract for one operator intent."""

    product_key: str
    target_object: str | None
    required_upload_roles: frozenset[str]
    optional_upload_roles: frozenset[str]
    prohibited_upload_roles: frozenset[str]
    reference_acquisition_requirement: AcquisitionRequirement
    # For people matching with uploaded refs: at least one role from each group.
    required_any_of_role_groups: tuple[frozenset[str], ...] = ()
    content_type: str | None = None
    canon_profile: str | None = None
    person_kind: str | None = None
    customer_label: str = ""

    def allowed_upload_roles(self) -> frozenset[str]:
        return self.required_upload_roles | self.optional_upload_roles


def parse_operator_intent(
    *,
    entity: str,
    operation: str,
    reference_source: str | None = None,
    people_output: str | None = None,
) -> OperatorIntent:
    """Normalize and validate raw form values into an OperatorIntent."""

    entity_value = (entity or "").strip().lower()
    operation_value = (operation or "").strip().lower()
    if entity_value not in VALID_ENTITIES:
        raise RouteError("Choose Accounts or People.")
    if operation_value not in VALID_OPERATIONS:
        raise RouteError("Choose clean-only or CRM matching.")

    if operation_value == "clean_only":
        source_value: ReferenceSource = "none"
        if reference_source and reference_source.strip().lower() not in {
            "",
            "none",
        }:
            raise RouteError(
                "Clean-only preparation does not use CRM reference sources."
            )
    else:
        source_raw = (reference_source or "").strip().lower()
        if source_raw == "connected_crm" and not CONNECTED_CRM_ENABLED_FOR_IMPORT_SETUP:
            raise RouteError(
                "Matching with a connected CRM is not available for import "
                "setup yet. Upload CRM exports instead, or use Resolve CRM "
                "duplicates for connection-bound work."
            )
        if source_raw not in {"uploaded", "connected_crm"}:
            raise RouteError(
                "Choose uploaded CRM exports for matching, or clean-only if "
                "you do not need CRM matching."
            )
        source_value = source_raw  # type: ignore[assignment]

    output_value: PeopleOutput | None = None
    if entity_value == "people" and operation_value == "clean_only":
        output_raw = (people_output or "").strip().lower()
        if output_raw not in VALID_PEOPLE_OUTPUTS:
            raise RouteError(
                "Choose whether to prepare people as Contacts or Leads."
            )
        output_value = output_raw  # type: ignore[assignment]
    elif people_output and people_output.strip():
        raise RouteError(
            "Contact versus Lead output is only chosen for People clean-only."
        )

    return OperatorIntent(
        entity=entity_value,  # type: ignore[arg-type]
        operation=operation_value,  # type: ignore[arg-type]
        reference_source=source_value,
        people_output=output_value,
    )


def derive_route(intent: OperatorIntent) -> RouteDecision:
    """Map operator intent to a backend product and source contract."""

    if intent.operation == "clean_only":
        if intent.reference_source != "none":
            raise RouteError(
                "Clean-only preparation cannot request CRM references."
            )
        if intent.entity == "accounts":
            required = frozenset({"dataset"})
            return RouteDecision(
                product_key="easyimports.single_dataset_import",
                target_object="account",
                required_upload_roles=required,
                optional_upload_roles=frozenset(),
                prohibited_upload_roles=ALL_IMPORT_ROLES - required,
                reference_acquisition_requirement="prohibited",
                content_type="accounts",
                canon_profile="accounts",
                person_kind=None,
                customer_label="Accounts · clean and prepare",
            )
        # people clean-only
        if intent.people_output not in VALID_PEOPLE_OUTPUTS:
            raise RouteError(
                "Choose whether to prepare people as Contacts or Leads."
            )
        required = frozenset({"dataset"})
        target = intent.people_output
        assert target is not None
        return RouteDecision(
            product_key="easyimports.single_dataset_import",
            target_object=target,
            required_upload_roles=required,
            optional_upload_roles=frozenset(),
            prohibited_upload_roles=ALL_IMPORT_ROLES - required,
            reference_acquisition_requirement="prohibited",
            content_type="people_and_accounts",
            canon_profile="new_list",
            person_kind=target,
            customer_label=f"People · clean and prepare as {target.title()}s",
        )

    # CRM matching
    if intent.reference_source == "none":
        raise RouteError("CRM matching requires a reference source.")
    if intent.people_output is not None:
        raise RouteError(
            "Contact versus Lead output is only chosen for People clean-only."
        )

    if intent.entity == "accounts":
        primary = frozenset({"raw_list"})
        if intent.reference_source == "uploaded":
            required = primary | frozenset({"accounts"})
            return RouteDecision(
                product_key="easyimports.account_list_import",
                target_object=None,
                required_upload_roles=required,
                optional_upload_roles=ACCOUNT_MATCHING_OPTIONAL_ROLES,
                prohibited_upload_roles=ALL_IMPORT_ROLES - required,
                reference_acquisition_requirement="disabled",
                customer_label="Accounts · match against CRM (uploaded)",
            )
        # connected_crm
        return RouteDecision(
            product_key="easyimports.account_list_import",
            target_object=None,
            required_upload_roles=primary,
            optional_upload_roles=frozenset(),
            prohibited_upload_roles=ALL_IMPORT_ROLES - primary,
            reference_acquisition_requirement="execute",
            customer_label="Accounts · match against CRM (connected)",
        )

    # people matching
    primary = frozenset({"raw_list"})
    if intent.reference_source == "uploaded":
        optional = PEOPLE_MATCHING_OPTIONAL_ROLES
        return RouteDecision(
            product_key="easyimports.list_import",
            target_object=None,
            required_upload_roles=primary,
            optional_upload_roles=optional,
            prohibited_upload_roles=ALL_IMPORT_ROLES - primary - optional,
            reference_acquisition_requirement="disabled",
            required_any_of_role_groups=(
                frozenset({"contacts", "leads"}),
            ),
            customer_label="People · match against CRM (uploaded)",
        )
    # connected_crm
    return RouteDecision(
        product_key="easyimports.list_import",
        target_object=None,
        required_upload_roles=primary,
        optional_upload_roles=frozenset(
            {
                "users",
                "territory",
                "company_exclusions",
                "industry_mapping",
            }
        ),
        prohibited_upload_roles=PEOPLE_SUPPLIED_REFERENCE_ROLES | frozenset({"dataset"}),
        reference_acquisition_requirement="execute",
        customer_label="People · match against CRM (connected)",
    )


def validate_uploads_against_route(
    route: RouteDecision,
    *,
    completed_roles: set[str] | frozenset[str],
) -> list[str]:
    """Return human-readable problems comparing completed uploads to the route.

    Only completed uploads count. Pending/unknown/rejected must not appear in
    ``completed_roles``.
    """

    problems: list[str] = []
    completed = frozenset(completed_roles)
    missing = sorted(route.required_upload_roles - completed)
    if missing:
        problems.append(
            "Required files are missing: " + ", ".join(missing) + "."
        )
    unexpected = sorted(completed & route.prohibited_upload_roles)
    if unexpected:
        problems.append(
            "These uploaded files are not used for this setup and block "
            "starting the import: " + ", ".join(unexpected) + "."
        )
    for group in route.required_any_of_role_groups:
        if not (completed & group):
            problems.append(
                "Matching with uploaded CRM exports requires at least one of: "
                + ", ".join(sorted(group))
                + "."
            )
    return problems


def intent_is_complete(intent: OperatorIntent | None) -> bool:
    if intent is None:
        return False
    try:
        derive_route(intent)
    except RouteError:
        return False
    return True
