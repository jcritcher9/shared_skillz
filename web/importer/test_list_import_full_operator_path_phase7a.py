"""Phase 7A (web) — Campaign-scoped default-status picker replaces free text.

Network-free Django form tests. Proves the configure form:

* renders the default member status as a bounded ChoiceField (allowlist) when
  the FE-CM-1 member-status browse is supplied, not free text;
* rejects an out-of-allowlist default status (single Campaign);
* offers the validated intersection for multiple Campaigns and requires a
  per-row member status column when the intersection is empty (D12);
* keeps the Campaign axis and Status axis independent (D10);
* keeps ``default_desired_status`` inside CAMPAIGN_POLICY_FIELD_NAMES (in-place
  replacement, no second Campaign surface);
* never imports ``mappings_2`` from Django (import boundary).
"""

from __future__ import annotations

from django import forms
from django.test import TestCase

from .campaign_member_setup import (
    CampaignMemberSetupError,
    campaign_scoped_status_choices,
    resolved_default_campaign_ids,
    validate_default_status_choice,
)
from .configure_layout import CAMPAIGN_POLICY_FIELD_NAMES
from .forms import WorkflowConfigurationForm

CAMP_A = "701000000000001AAA"
CAMP_B = "701000000000002BBB"


def _list_product(*, cm_modes=None):
    return {
        "product_key": "easyimports.list_import",
        "tracks": {
            "reference_acquisition": ["disabled", "execute"],
            "account_provisioning": ["disabled"],
            "person_duplicate_resolution": ["disabled"],
            "people_writes": ["disabled"],
            "campaign_member_writes": cm_modes
            or ["disabled", "preview", "dry_run", "execute"],
            "delivery": ["disabled", "preview"],
        },
    }


def _target(*, cm_ceiling="execute"):
    return {
        "target_provider_id": "fake-crm-phase0b-v1",
        "maximum_modes": {
            "reference_acquisition": "execute",
            "account_provisioning": "disabled",
            "person_duplicate_resolution": "execute",
            "people_writes": "disabled",
            "campaign_member_writes": cm_ceiling,
            "delivery": "preview",
        },
    }


def _base_form_data(**overrides):
    data = {
        "form_token": "token",
        "mode__reference_acquisition": "execute",
        "mode__account_provisioning": "disabled",
        "mode__person_duplicate_resolution": "disabled",
        "mode__people_writes": "disabled",
        "mode__campaign_member_writes": "disabled",
        "mode__delivery": "preview",
        "list_duplicate_policy": "surface",
        "crm_match_duplicate_policy": "drop_repeats",
        "crm_account_multi_match_policy": "review",
        "batch_validation_policy": "quarantine",
    }
    data.update(overrides)
    return data


def _bound_entry(*, match_key, campaign_id=CAMP_A, connection_id="conn-1"):
    return {
        "match_mode": "name_exact",
        "match_key": match_key,
        "campaign_id": campaign_id,
        "connection_id": connection_id,
        "selection_source": "unique_match",
        "resolved_at": "2026-08-01T12:00:00Z",
        "lookup_bound": True,
        "observed_name": match_key,
    }


def _form(data, *, statuses=None, verified="[]", **kw):
    return WorkflowConfigurationForm(
        data,
        product_entry=_list_product(),
        target=_target(),
        uploaded_roles={"raw_list"},
        reference_acquisition_requirement="execute",
        connection_id="conn-1",
        verified_resolutions_json=verified,
        campaign_member_statuses=statuses,
        **kw,
    )


class DjangoImportBoundaryTests(TestCase):
    def test_django_module_does_not_import_mappings_2(self):
        import importer.campaign_member_setup as setup
        import importer.forms as forms_mod

        for module in (setup, forms_mod):
            source = open(module.__file__, encoding="utf-8").read()
            self.assertNotIn("import mappings_2", source)
            self.assertNotIn("from mappings_2", source)


class PickerHelperTests(TestCase):
    def test_single_campaign_allowlist_ordered(self):
        decision = campaign_scoped_status_choices(
            resolved_campaign_ids=[CAMP_A],
            campaign_statuses={CAMP_A: ["Sent", "Responded"]},
        )
        self.assertEqual(decision["mode"], "single_campaign")
        self.assertEqual(decision["choices"], ["Sent", "Responded"])
        self.assertFalse(decision["requires_per_row_status"])

    def test_multi_campaign_intersection(self):
        decision = campaign_scoped_status_choices(
            resolved_campaign_ids=[CAMP_A, CAMP_B],
            campaign_statuses={
                CAMP_A: ["Sent", "Responded", "Bounced"],
                CAMP_B: ["Responded", "Sent"],
            },
        )
        self.assertEqual(decision["mode"], "intersection")
        self.assertEqual(decision["choices"], ["Sent", "Responded"])

    def test_multi_campaign_empty_intersection_requires_per_row(self):
        decision = campaign_scoped_status_choices(
            resolved_campaign_ids=[CAMP_A, CAMP_B],
            campaign_statuses={
                CAMP_A: ["Sent"],
                CAMP_B: ["Attended"],
            },
        )
        self.assertEqual(decision["mode"], "require_per_row")
        self.assertTrue(decision["requires_per_row_status"])
        self.assertEqual(decision["choices"], [])

    def test_open_id_column_requires_per_row(self):
        decision = campaign_scoped_status_choices(
            resolved_campaign_ids=[CAMP_A],
            campaign_statuses={CAMP_A: ["Sent"]},
            has_open_campaign_id_column=True,
        )
        self.assertTrue(decision["requires_per_row_status"])

    def test_missing_statuses_fail_closed(self):
        with self.assertRaises(CampaignMemberSetupError):
            campaign_scoped_status_choices(
                resolved_campaign_ids=[CAMP_A, CAMP_B],
                campaign_statuses={CAMP_A: ["Sent"]},
            )

    def test_resolved_ids_default_plus_resolutions(self):
        ids = resolved_default_campaign_ids(
            default_campaign_binding=CAMP_A,
            resolutions=[{"campaign_id": CAMP_B}, {"campaign_id": CAMP_A}],
        )
        self.assertEqual(ids, [CAMP_A, CAMP_B])

    def test_validate_default_status_rejects_out_of_list(self):
        decision = campaign_scoped_status_choices(
            resolved_campaign_ids=[CAMP_A],
            campaign_statuses={CAMP_A: ["Sent"]},
        )
        validate_default_status_choice("Sent", decision)
        with self.assertRaises(CampaignMemberSetupError):
            validate_default_status_choice("Bogus", decision)


class ConfigureFormStatusPickerTests(TestCase):
    def test_default_status_is_bounded_choicefield_not_free_text(self):
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
            ),
            statuses={CAMP_A: ["Sent", "Responded"]},
        )
        field = form.fields["default_desired_status"]
        self.assertIsInstance(field, forms.ChoiceField)
        # Not the free-text CharField any longer.
        self.assertNotIsInstance(field, forms.CharField)
        values = [value for value, _label in field.choices]
        self.assertEqual(values, ["", "Sent", "Responded"])

    def test_default_status_still_in_campaign_policy_field_names(self):
        # In-place replacement: no second Campaign surface, field name preserved.
        self.assertIn("default_desired_status", CAMPAIGN_POLICY_FIELD_NAMES)

    def test_valid_default_status_from_allowlist_freezes_policy(self):
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                default_desired_status="Sent",
            ),
            statuses={CAMP_A: ["Sent", "Responded"]},
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "up-raw"},
            target_provider_id="fake-crm-phase0b-v1",
        )
        policy = request["campaign_member_policy"]
        self.assertEqual(policy["policy_version"], "campaign_member_policy.v2")
        self.assertEqual(policy["default_desired_status"], "Sent")
        self.assertEqual(policy["default_campaign_binding"], CAMP_A)

    def test_out_of_allowlist_default_status_rejected(self):
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                default_desired_status="Bogus",
            ),
            statuses={CAMP_A: ["Sent", "Responded"]},
        )
        self.assertFalse(form.is_valid())
        self.assertIn("default_desired_status", form.errors)

    def test_multi_campaign_intersection_bounds_picker(self):
        verified = [
            _bound_entry(match_key="alpha", campaign_id=CAMP_A),
            _bound_entry(match_key="beta", campaign_id=CAMP_B),
        ]
        import json

        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
            ),
            statuses={
                CAMP_A: ["Sent", "Responded", "Bounced"],
                CAMP_B: ["Responded", "Sent"],
            },
            verified=json.dumps(verified),
        )
        values = [
            value for value, _ in form.fields["default_desired_status"].choices
        ]
        self.assertEqual(values, ["", "Sent", "Responded"])

    def test_multi_campaign_empty_intersection_requires_status_column(self):
        import json

        verified = [
            _bound_entry(match_key="alpha", campaign_id=CAMP_A),
            _bound_entry(match_key="beta", campaign_id=CAMP_B),
        ]
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                # No member_status_column, no per-row status → must fail closed.
            ),
            statuses={
                CAMP_A: ["Sent"],
                CAMP_B: ["Attended"],
            },
            verified=json.dumps(verified),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("member status column", str(form.errors).lower())

    def test_axes_independent_status_column_does_not_need_status_picker(self):
        # D10: mapping a member_status column (status axis) satisfies status
        # without a default picker; the Campaign axis (default binding) is
        # separate. Bounded picker is bypassed because the operator carries
        # per-row status.
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                member_status_column="Member_Status",
            ),
            statuses={CAMP_A: ["Sent", "Responded"]},
        )
        self.assertTrue(form.is_valid(), form.errors)
        policy = form.workflow_request(
            upload_ids={"raw_list": "up-raw"},
            target_provider_id="fake-crm-phase0b-v1",
        )["campaign_member_policy"]
        self.assertEqual(policy["member_status_column"], "Member_Status")
        self.assertIsNone(policy.get("default_desired_status"))

    def test_no_statuses_supplied_keeps_legacy_free_text_field(self):
        # Before any Campaign browse, the field stays a permissive input so the
        # operator is not blocked; the bounded picker activates once statuses
        # arrive (via the view's ephemeral FE-CM-1 fetch).
        form = _form(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                default_desired_status="Sent",
            ),
            statuses=None,
        )
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIsInstance(
            form.fields["default_desired_status"], forms.ChoiceField
        )
