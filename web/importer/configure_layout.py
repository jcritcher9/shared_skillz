"""OUT-6B: group list-import configure fields without mappings_2 import."""

from __future__ import annotations

from typing import Any

from .constants import ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS

# Left — this list (People match). Present only when the form kept the field.
THIS_LIST_FIELD_NAMES: tuple[str, ...] = (
    "contacts_only",
    "match_speed",
    "list_duplicate_policy",
    "crm_match_duplicate_policy",
    "crm_account_multi_match_policy",
    "fill_missing_emails",
    "blank_non_north_america_address",
    "strict_validation",
    "batch_validation_policy",
    "duplicate_analysis_as_of_date",
)

# Right — collapsed optional CRM writes. Phase 6A: the person-write update
# policy sits directly beside mode__people_writes; it is present in the form only
# for list-import when people writes are available (dropped otherwise and never
# added for clean-only), so _bound skips it exactly when it must stay hidden.
OPTIONAL_WRITE_FIELD_NAMES: tuple[str, ...] = (
    "mode__account_provisioning",
    "mode__account_writes",
    "mode__people_writes",
    "update_existing_contact_information",
    "mode__person_duplicate_resolution",
    "mode__dataset_writes",
)

REFERENCE_FIELD_NAME = "mode__reference_acquisition"
CAMPAIGN_TRACK_FIELD_NAME = "mode__campaign_member_writes"
CAMPAIGN_POLICY_FIELD_NAMES: tuple[str, ...] = (
    "campaign_id_column",
    "campaign_match_column",
    "member_status_column",
    "default_campaign_binding",
    "default_desired_status",
)
# Already answered in setup; keep submitted, never dump onto Configure.
SETUP_LOCK_FIELD_NAMES: frozenset[str] = frozenset(
    {
        "target_object",
        "content_type",
        "canon_profile",
        "person_kind",
    }
)
CLEAN_ONLY_PRODUCT = "easyimports.single_dataset_import"


def _bound(form, name: str):
    if name not in form.fields:
        return None
    field = form[name]
    if getattr(field.field.widget, "is_hidden", False):
        return None
    return field


def configure_form_sections(
    form,
    *,
    product_key: str,
    reference_requirement: str | None = None,
    campaign_ceiling: str = "disabled",
) -> dict[str, Any]:
    """Split configure visible fields into OUT-6B surfaces.

    Grouped hide/collapse applies only to the three list-import family
    products. Other products, including duplicate_resolution, keep every
    visible field on the left so required entity/date/execution controls
    stay usable. On import products, reference is visible only when setup
    did not already require it. Required / prohibited reference stays
    submitted but hidden. Clean-only hides write / Campaign / reference /
    locked setup choices. Campaign is one block, only when the ceiling is
    not disabled. Unclaimed leftovers on import products stay submitted
    hidden, not dumped onto the left column.
    """

    product = str(product_key or "")
    if product not in ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS:
        visible = list(form.visible_fields())
        return {
            "this_list_fields": visible,
            "optional_write_fields": [],
            "reference_in_optional": None,
            "reference_on_left": None,
            "show_optional_crm": False,
            "campaign_track_field": None,
            "campaign_policy_fields": [],
            "show_campaign_block": False,
            "hidden_section_fields": [],
            "leftover_fields": visible,
            "is_clean_only": False,
        }

    clean_only = product == CLEAN_ONLY_PRODUCT
    ceiling = str(campaign_ceiling or "disabled").strip() or "disabled"
    requirement = str(reference_requirement or "").strip()
    campaign_ok = ceiling != "disabled" and not clean_only

    this_list = []
    for name in THIS_LIST_FIELD_NAMES:
        bound = _bound(form, name)
        if bound is not None:
            this_list.append(bound)

    optional_writes = []
    hidden_writes = []
    for name in OPTIONAL_WRITE_FIELD_NAMES:
        bound = _bound(form, name)
        if bound is None:
            continue
        if clean_only:
            hidden_writes.append(bound)
        else:
            optional_writes.append(bound)

    reference = _bound(form, REFERENCE_FIELD_NAME)
    reference_on_left = None
    reference_in_optional = None
    if reference is not None:
        # Visible only when setup did not already require the read.
        if clean_only or requirement in {"prohibited", "execute"}:
            hidden_writes.append(reference)
        else:
            reference_in_optional = reference

    campaign_track = _bound(form, CAMPAIGN_TRACK_FIELD_NAME)
    campaign_policy = []
    for name in CAMPAIGN_POLICY_FIELD_NAMES:
        bound = _bound(form, name)
        if bound is not None:
            campaign_policy.append(bound)
    campaign_hidden = []
    if not campaign_ok:
        if campaign_track is not None:
            campaign_hidden.append(campaign_track)
            campaign_track = None
        campaign_hidden.extend(campaign_policy)
        campaign_policy = []

    for name in SETUP_LOCK_FIELD_NAMES:
        bound = _bound(form, name)
        if bound is not None:
            hidden_writes.append(bound)

    claimed = {field.name for field in this_list}
    claimed.update(field.name for field in optional_writes)
    if reference_in_optional is not None:
        claimed.add(reference_in_optional.name)
    if campaign_track is not None:
        claimed.add(campaign_track.name)
    claimed.update(field.name for field in campaign_policy)
    claimed.update(field.name for field in hidden_writes)
    claimed.update(field.name for field in campaign_hidden)

    leftovers = []
    for field in form.visible_fields():
        if field.name in claimed:
            continue
        leftovers.append(field)
        hidden_writes.append(field)

    show_optional = bool(optional_writes or reference_in_optional)
    show_campaign = campaign_track is not None or bool(campaign_policy)
    return {
        "this_list_fields": this_list,
        "optional_write_fields": optional_writes,
        "reference_in_optional": reference_in_optional,
        "reference_on_left": reference_on_left,
        "show_optional_crm": show_optional,
        "campaign_track_field": campaign_track,
        "campaign_policy_fields": campaign_policy,
        "show_campaign_block": show_campaign,
        "hidden_section_fields": hidden_writes + campaign_hidden,
        "leftover_fields": leftovers,
        "is_clean_only": clean_only,
    }
