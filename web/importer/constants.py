"""Current API product labels and upload-role presentation metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceRole:
    key: str
    label: str
    required: bool
    help_text: str


LIST_IMPORT_SOURCE_ROLES: tuple[SourceRole, ...] = (
    SourceRole(
        key="raw_list",
        label="Marketing list to import",
        required=True,
        help_text="The CSV/XLSX list you want to prepare.",
    ),
    SourceRole(
        key="contacts",
        label="Salesforce Contacts export",
        required=False,
        help_text="Existing Contacts. Supply Contacts, Leads, or both for local references.",
    ),
    SourceRole(
        key="accounts",
        label="Salesforce Accounts export",
        required=False,
        help_text="Existing Accounts. If omitted, accounts are inferred from contacts/leads.",
    ),
    SourceRole(
        key="leads",
        label="Salesforce Leads export",
        required=False,
        help_text="Existing Leads, used to match people not yet converted to contacts.",
    ),
    SourceRole(
        key="users",
        label="Salesforce Users export",
        required=False,
        help_text="Users/owners, used to resolve owner names to IDs for ownership.",
    ),
    SourceRole(
        key="territory",
        label="Territory / ownership export",
        required=False,
        help_text="Territory rules used as an ownership fallback.",
    ),
    SourceRole(
        key="company_exclusions",
        label="Company exclusions",
        required=False,
        help_text="Companies that should be excluded from list processing.",
    ),
    SourceRole(
        key="industry_mapping",
        label="Industry mapping",
        required=False,
        help_text="Optional two-column industry normalization mapping.",
    ),
)

ACCOUNT_LIST_SOURCE_ROLES: tuple[SourceRole, ...] = (
    SourceRole("raw_list", "Account list", True, "The incoming Account CSV/XLSX."),
    SourceRole(
        "accounts",
        "Salesforce Accounts export",
        False,
        "Required when reference acquisition is disabled.",
    ),
)

SINGLE_DATASET_SOURCE_ROLES: tuple[SourceRole, ...] = (
    SourceRole("dataset", "Dataset", True, "The single CSV/XLSX dataset to prepare."),
)

DUPLICATE_SOURCE_ROLES: tuple[SourceRole, ...] = (
    SourceRole(
        "canonical_records",
        "Canonical CRM records",
        False,
        "Required when reference acquisition is disabled.",
    ),
    SourceRole(
        "related_contacts",
        "Related Contacts",
        False,
        "Account duplicate evidence only.",
    ),
    SourceRole(
        "related_leads", "Related Leads", False, "Account duplicate evidence only."
    ),
    SourceRole(
        "opportunities", "Opportunities", False, "Optional Account duplicate evidence."
    ),
)

PRODUCT_LABELS = {
    "easyimports.list_import": "People · match against CRM",
    "easyimports.account_list_import": "Accounts · match against CRM",
    "easyimports.single_dataset_import": "Clean and prepare file",
    "easyimports.duplicate_resolution": "Duplicate resolution",
}

ENTITY_LABELS = {
    "accounts": "Accounts",
    "people": "People",
}

OPERATION_LABELS = {
    # D7: clean-only secondary wording (Phase 7A).
    "clean_only": "Clean and prepare my file (no matching)",
    "crm_matching": "Match it against my CRM",
}

REFERENCE_SOURCE_LABELS = {
    "none": "None",
    "uploaded": "Upload CRM exports",
    "connected_crm": "Use a connected CRM",
}

PEOPLE_OUTPUT_LABELS = {
    "contact": "Contacts",
    "lead": "Leads",
}

# Customer-facing upload role labels for the intent-driven setup flow.
SETUP_ROLE_LABELS: dict[str, tuple[str, str]] = {
    "dataset": (
        "File to prepare",
        "The CSV/XLSX you want EasyImports to clean and prepare.",
    ),
    "raw_list": (
        "List to prepare",
        "The CSV/XLSX list you want to prepare and match.",
    ),
    "accounts": (
        "Existing CRM Accounts",
        "Account export used for matching.",
    ),
    "contacts": (
        "Existing CRM Contacts",
        "Contact export used for matching people.",
    ),
    "leads": (
        "Existing CRM Leads",
        "Lead export used for matching people.",
    ),
    "users": (
        "CRM Users export",
        "Optional. Used to resolve owner names to IDs.",
    ),
    "territory": (
        "Territory / ownership export",
        "Optional ownership fallback rules.",
    ),
    "company_exclusions": (
        "Company exclusions",
        "Optional companies to exclude from processing.",
    ),
    "industry_mapping": (
        "Industry mapping",
        "Optional two-column industry normalization mapping.",
    ),
}

TARGET_LABELS = {
    "fake-preview-v1": "Preview files only",
    "developer-dry-run-v1": "Developer test environment",
}

TRACK_LABELS = {
    "reference_acquisition": "Reference data",
    "account_provisioning": "New Account creation",
    "account_writes": "Account updates",
    "people_writes": "Contact and Lead changes",
    "person_duplicate_resolution": "Person duplicate handling",
    "duplicate_execution": "Duplicate changes",
    "campaign_member_writes": "Campaign membership",
    "dataset_writes": "Dataset changes",
    "delivery": "Output files",
}

TRACK_HELP_TEXT = {
    "reference_acquisition": "Choose whether EasyImports should load existing reference records.",
    # Phase 1D: write/provision tracks default to Do not run; execute is labeled Apply changes.
    "account_provisioning": (
        "Optional. Defaults to Do not run. Opt in to Preview, Test without changes, "
        "or Apply changes only when you want net-new Accounts. Skipping Account "
        "creation leaves people without an Account match as Leads."
    ),
    "account_writes": (
        "Optional. Defaults to Do not run. Apply changes is an explicit later "
        "authorization step."
    ),
    "people_writes": (
        "Optional. Defaults to Do not run. Opt in to Preview, Test without changes, "
        "or Apply changes for Contact/Lead writes. Apply changes still requires a "
        "separate authorization before any CRM mutation."
    ),
    "person_duplicate_resolution": (
        "Optional. Defaults to Do not run. Opt in only when person CRM-duplicate "
        "review or merge is part of this run."
    ),
    "duplicate_execution": (
        "Optional. Defaults to Do not run. Apply changes is an explicit later "
        "authorization step."
    ),
    "campaign_member_writes": (
        "Optional. Defaults to Do not run. Opt in only when Campaign membership "
        "should be prepared or applied after person writes."
    ),
    "dataset_writes": (
        "Optional. Defaults to Do not run. Apply changes is an explicit later "
        "authorization step."
    ),
    "delivery": "Choose whether downloadable result files should be created.",
}

MODE_LABELS = {
    "disabled": "Do not run",
    "preview": "Preview only",
    "plan_only": "Preview only",
    "dry_run": "Test without changes",
    "execute": "Apply changes",
}

# OUT-3: these products always ship cleaned package preview members.
# The configure page must not ask whether to create downloadable files.
ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS = frozenset(
    {
        "easyimports.list_import",
        "easyimports.account_list_import",
        "easyimports.single_dataset_import",
    }
)

# Phase 1D: tracks that must default to disabled until the operator opts in.
# Delivery and required reference acquisition use separate initial rules.
OPT_IN_WRITE_TRACKS = frozenset(
    {
        "account_provisioning",
        "account_writes",
        "people_writes",
        "person_duplicate_resolution",
        "duplicate_execution",
        "campaign_member_writes",
        "dataset_writes",
    }
)

SESSION_STATUS_LABELS = {
    "created": ("Setup started", "created"),
    "running": ("In progress", "running"),
    "completed": ("Complete", "completed"),
    "failed": ("Needs attention", "failed"),
    "archived": ("Archived", "created"),
}

PRODUCT_SOURCE_ROLES = {
    "easyimports.list_import": LIST_IMPORT_SOURCE_ROLES,
    "easyimports.account_list_import": ACCOUNT_LIST_SOURCE_ROLES,
    "easyimports.single_dataset_import": SINGLE_DATASET_SOURCE_ROLES,
    "easyimports.duplicate_resolution": DUPLICATE_SOURCE_ROLES,
}

SOURCE_ROLES: tuple[SourceRole, ...] = tuple(
    {
        role.key: role for roles in PRODUCT_SOURCE_ROLES.values() for role in roles
    }.values()
)
SOURCE_ROLE_CHOICES: tuple[tuple[str, str], ...] = tuple(
    (role.key, role.label) for role in SOURCE_ROLES
)


def _fallback_label(value: str) -> str:
    return value.replace("_", " ").replace("-", " ").strip().capitalize()


def display_product_label(product_key: str) -> str:
    return PRODUCT_LABELS.get(product_key, _fallback_label(product_key))


def display_target_label(target_provider_id: str) -> str:
    return TARGET_LABELS.get(
        target_provider_id,
        _fallback_label(target_provider_id) or "Configured destination",
    )


def display_entity_label(entity: str) -> str:
    return ENTITY_LABELS.get(entity, _fallback_label(entity))


def display_operation_label(operation: str) -> str:
    return OPERATION_LABELS.get(operation, _fallback_label(operation))


def display_reference_source_label(source: str) -> str:
    return REFERENCE_SOURCE_LABELS.get(source, _fallback_label(source))


def display_people_output_label(output: str) -> str:
    return PEOPLE_OUTPUT_LABELS.get(output, _fallback_label(output))


def display_setup_intent_label(
    *,
    entity: str = "",
    operation: str = "",
    reference_source: str = "",
    people_output: str = "",
    product_key: str = "",
) -> str:
    """Customer-facing label for setup draft or frozen product."""

    if entity and operation:
        base = f"{display_entity_label(entity)} · {display_operation_label(operation)}"
        if operation == "crm_matching" and reference_source:
            return f"{base} ({display_reference_source_label(reference_source)})"
        if entity == "people" and operation == "clean_only" and people_output:
            return f"{base} as {display_people_output_label(people_output)}"
        return base
    if product_key:
        return display_product_label(product_key)
    return "Import setup"
