"""CMX-1 vocabulary intent helpers for Django setup (no mappings_2 import).

Mirrors ``mappings_2.canon.column_mapping.vocabulary`` so the web
package stays free of mappings_2 runtime imports.
"""

from __future__ import annotations

from typing import Final

VOCABULARY_PRODUCT: Final[str] = "product"
VOCABULARY_FAKE: Final[str] = "fake"
VOCABULARY_SALESFORCE: Final[str] = "salesforce"
VOCABULARY_HUBSPOT: Final[str] = "hubspot"

VALID_VOCABULARY_VALUES: Final[frozenset[str]] = frozenset(
    {
        VOCABULARY_PRODUCT,
        VOCABULARY_FAKE,
        VOCABULARY_SALESFORCE,
        VOCABULARY_HUBSPOT,
    }
)
PROVIDER_VOCABULARY_VALUES: Final[frozenset[str]] = frozenset(
    {VOCABULARY_FAKE, VOCABULARY_SALESFORCE, VOCABULARY_HUBSPOT}
)
DEFAULT_VOCABULARY: Final[str] = VOCABULARY_PRODUCT

VOCABULARY_UI_COPY: Final[str] = (
    "Field names for mapping does not change your download format. "
    "Choose export format under Configure / package options."
)

VOCABULARY_CHOICES: Final[tuple[tuple[str, str], ...]] = (
    (VOCABULARY_PRODUCT, "EasyImports field names (default)"),
    (VOCABULARY_SALESFORCE, "Salesforce field names"),
    (VOCABULARY_HUBSPOT, "HubSpot field names"),
    (VOCABULARY_FAKE, "Practice CRM field names"),
)

VOCABULARY_LABELS: Final[dict[str, str]] = dict(VOCABULARY_CHOICES)

# OUT-6A: locked Contacts-only help. Visible on configure, not title-only.
CONTACTS_ONLY_LEAD_FALLBACK_HELP: Final[str] = (
    "We will try to prepare Contact rows only. If a person has no matching "
    "Account, EasyImports will prepare that person as a Lead instead."
)
CONTACTS_ONLY_HUBSPOT_HELP: Final[str] = (
    "We will prepare Contact rows only. HubSpot has no Lead. People "
    "without a Company match stay Contacts in the HubSpot Contact file."
)


class VocabularyIntentError(ValueError):
    """Vocabulary intent cannot be accepted for the given setup combination."""


def normalize_vocabulary(raw: str | None) -> str:
    value = str(raw or "").strip().lower()
    if not value:
        return DEFAULT_VOCABULARY
    if value not in VALID_VOCABULARY_VALUES:
        raise VocabularyIntentError(
            f"Unknown field-name vocabulary {value!r}. "
            f"Choose one of: {', '.join(sorted(VALID_VOCABULARY_VALUES))}."
        )
    return value


def validate_vocabulary_for_setup(
    *,
    operation: str,
    vocabulary: str | None,
    connection_id: str | None = None,
    match_provider_key: str | None = None,
) -> str:
    """Validate and return normalized vocabulary for a setup draft.

    Clean-only may set any allowed vocabulary without a connection.
    Match + connected CRM: CRM-provider vocabulary must equal connection
    provider (fail closed). Product vocabulary never conflicts.
    Does not invent connections or change export profile.
    """

    op = str(operation or "").strip().lower()
    if op not in {"clean_only", "crm_matching"}:
        raise VocabularyIntentError(
            "Choose clean-only or CRM matching before setting field names."
        )

    vocab = normalize_vocabulary(vocabulary)
    conn = str(connection_id or "").strip()
    provider = str(match_provider_key or "").strip().lower()

    if op == "clean_only":
        return vocab

    if provider and vocab in PROVIDER_VOCABULARY_VALUES and vocab != provider:
        raise VocabularyIntentError(
            "Field names for mapping do not match the connected CRM provider. "
            f"Vocabulary is {vocab!r} but the connection is {provider!r}. "
            "Choose matching field names, or switch the CRM connection."
        )

    if conn and not provider and vocab in PROVIDER_VOCABULARY_VALUES:
        raise VocabularyIntentError(
            "The selected CRM connection has no provider key, so CRM "
            "field-name vocabulary cannot be validated. Reconnect the CRM "
            "or choose product field names."
        )

    return vocab


def display_vocabulary_label(vocabulary: str) -> str:
    key = str(vocabulary or "").strip().lower() or DEFAULT_VOCABULARY
    return VOCABULARY_LABELS.get(key, key or "—")


def _is_hubspot_vocabulary_or_provider(
    *,
    vocabulary: str | None,
    provider_key: str | None,
) -> bool:
    vocab = str(vocabulary or "").strip().lower()
    provider = str(provider_key or "").strip().lower()
    if vocab == VOCABULARY_HUBSPOT:
        return True
    return provider == VOCABULARY_HUBSPOT or provider.startswith("hubspot")


def contacts_only_help_text(
    *,
    vocabulary: str | None = None,
    provider_key: str | None = None,
) -> str:
    """OUT-6A: provider-specific Contacts-only help (People list-import).

    HubSpot vocabulary or a HubSpot connection never promises Leads.
    Salesforce / product / fake (and unknown) use the Lead-fallback sentence.
    """

    if _is_hubspot_vocabulary_or_provider(
        vocabulary=vocabulary, provider_key=provider_key
    ):
        return CONTACTS_ONLY_HUBSPOT_HELP
    return CONTACTS_ONLY_LEAD_FALLBACK_HELP


# CMX-2: catalog ids for provider-labelled ingest (mirrors mappings_2 cmx0 mint).
# Web must not import mappings_2 at runtime.
_INGEST_CATALOG_TEMPLATE = "easyimports.ingest.{provider}.{object_scope}.v1"
_PRODUCT_CATALOG_ROUTING: dict[tuple[str, str, str], str] = {
    ("accounts", "clean_only", ""): "easyimports.account_fields.v1",
    ("accounts", "crm_matching", ""): "easyimports.account_fields.v1",
    ("people", "clean_only", "contact"): "easyimports.contact_fields.v1",
    ("people", "clean_only", "lead"): "easyimports.lead_fields.v1",
    ("people", "crm_matching", "contact"): "easyimports.people_matching_fields.v1",
    ("people", "crm_matching", "lead"): "easyimports.people_matching_fields.v1",
    ("people", "crm_matching", ""): "easyimports.people_matching_fields.v1",
}


def _normalize_people_output_for_catalog(people_output: str | None) -> str:
    raw = str(people_output or "").strip().lower()
    if raw in {"contact", "contacts"}:
        return "contact"
    if raw in {"lead", "leads"}:
        return "lead"
    return raw


def resolve_catalog_id_for_setup_vocabulary(
    *,
    entity: str | None,
    operation: str | None,
    people_output: str | None,
    vocabulary: str | None,
) -> str:
    """Resolve catalog-mode destination id for setup + vocabulary (CMX-1/2).

    Product vocabulary → MAP-R1 catalogs. CRM vocabulary → ingest catalog ids
    with canon expansions (first boundary: account/contact). Lead + CRM
    vocabulary fails closed (lead ingest deferred).
    """

    ent = str(entity or "").strip().lower()
    op = str(operation or "").strip().lower()
    if not ent or not op:
        raise ValueError(
            "Import setup is incomplete. Choose Accounts or People and how "
            "EasyImports should process the file before mapping columns."
        )
    po = _normalize_people_output_for_catalog(people_output)
    if ent == "people" and op == "clean_only" and not po:
        raise ValueError(
            "Choose whether to prepare people as Contacts or Leads before "
            "mapping columns."
        )

    vocab = normalize_vocabulary(vocabulary)
    if vocab == VOCABULARY_PRODUCT:
        catalog_id = _PRODUCT_CATALOG_ROUTING.get((ent, op, po))
        if catalog_id is None:
            raise ValueError(
                "Column mapping is not available for this setup combination. "
                "Return to setup and choose a supported option."
            )
        return catalog_id

    if vocab not in PROVIDER_VOCABULARY_VALUES:
        raise ValueError(f"Unknown field-name vocabulary {vocab!r}.")

    if ent == "accounts":
        object_scope = "account"
    elif ent == "people":
        if op == "clean_only" and po == "lead":
            raise ValueError(
                "Lead ingest vocabulary is deferred. Use EasyImports field "
                "names for Leads, or prepare as Contacts."
            )
        object_scope = "contact"
    else:
        raise ValueError("Column mapping is not available for this setup combination.")
    return _INGEST_CATALOG_TEMPLATE.format(provider=vocab, object_scope=object_scope)
