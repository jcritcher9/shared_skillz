"""FE-CM-3: Django list-import CampaignMember configure UI (network-free)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from django.test import Client, TestCase
from django.urls import reverse

from .api_contract import validate_workflow_create
from .campaign_member_setup import (
    CampaignMemberSetupError,
    append_resolution_entry,
    build_campaign_member_policy,
    campaign_member_ceiling_from_connection,
    distinct_match_keys_requiring_resolution,
    is_well_formed_campaign_id,
    load_session_resolutions,
    parse_campaign_match_resolutions,
    require_resolutions_cover_keys,
    resolution_from_lookup_evidence,
    save_session_resolutions,
    validate_enabled_campaign_member_policy,
)
from .forms import WorkflowConfigurationForm
from .models import ApiMutation, ImportSession, SourceFile
from .setup_service import connected_import_target_projection
from .workflow_state import OWNER_SESSION_KEY, issue_form_token


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


def _bound_entry(
    *,
    match_key: str,
    campaign_id: str = CAMP_A,
    selection_source: str = "unique_match",
    connection_id: str = "conn-1",
) -> dict:
    return {
        "match_mode": "name_exact",
        "match_key": match_key,
        "campaign_id": campaign_id,
        "connection_id": connection_id,
        "selection_source": selection_source,
        "resolved_at": "2026-08-01T12:00:00Z",
        "lookup_bound": True,
        "observed_name": match_key,
    }


def _write_raw_list_csv(path: Path, rows: list[dict], fieldnames: list[str]) -> None:
    import csv

    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)



def _dummy_column_mapping(body: dict) -> dict:
    payload = dict(body)
    if payload.get('product_key') in {
        'easyimports.list_import',
        'easyimports.account_list_import',
        'easyimports.single_dataset_import',
    }:
        dig = 'd' * 64
        payload['column_mapping'] = {
            'plan_id': 'cmp_django_schema_only',
            'plan_content_digest': dig,
            'source_schema_digest': dig,
            'destination_digest': dig,
            'target_contract_digest': dig,
        }
    return payload

class CampaignMemberCeilingTests(TestCase):
    def test_provider_projection_sf_and_fake_execute_hubspot_disabled(self):
        self.assertEqual(
            campaign_member_ceiling_from_connection({"provider_key": "salesforce"}),
            "execute",
        )
        self.assertEqual(
            campaign_member_ceiling_from_connection({"provider_key": "fake"}),
            "execute",
        )
        self.assertEqual(
            campaign_member_ceiling_from_connection({"provider_key": "hubspot"}),
            "disabled",
        )
        self.assertEqual(campaign_member_ceiling_from_connection(None), "disabled")

    def test_explicit_maximum_authorization_wins(self):
        self.assertEqual(
            campaign_member_ceiling_from_connection(
                {
                    "provider_key": "salesforce",
                    "maximum_authorization": {"campaign_member_writes": "dry_run"},
                }
            ),
            "dry_run",
        )

    def test_connected_import_target_projection_uses_connection(self):
        projected = connected_import_target_projection(
            target_provider_id="fake-crm-phase0b-v1",
            product_key="easyimports.list_import",
            connection={"provider_key": "fake", "connection_id": "c1"},
        )
        self.assertEqual(
            projected["maximum_modes"]["campaign_member_writes"], "execute"
        )
        projected_hs = connected_import_target_projection(
            target_provider_id="hubspot-stack",
            product_key="easyimports.list_import",
            connection={"provider_key": "hubspot", "connection_id": "c2"},
        )
        self.assertEqual(
            projected_hs["maximum_modes"]["campaign_member_writes"], "disabled"
        )


class CampaignMemberPolicyBuildTests(TestCase):
    def test_campaign_id_shape(self):
        self.assertTrue(is_well_formed_campaign_id(CAMP_A))
        self.assertFalse(is_well_formed_campaign_id("003000000000001AAA"))
        self.assertFalse(is_well_formed_campaign_id("not-an-id"))

    def test_parse_normalizes_match_key_and_fills_connection(self):
        raw = json.dumps(
            [
                {
                    "match_key": "  Spring Launch  ",
                    "campaign_id": CAMP_A,
                    "selection_source": "unique_match",
                    "resolved_at": "2026-08-01T12:00:00Z",
                    "lookup_bound": True,
                }
            ]
        )
        entries = parse_campaign_match_resolutions(raw, connection_id="conn-1")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["match_key"], "spring launch")
        self.assertEqual(entries[0]["connection_id"], "conn-1")
        self.assertEqual(entries[0]["campaign_id"], CAMP_A)

    def test_duplicate_match_keys_fail(self):
        raw = [
            _bound_entry(match_key="A"),
            _bound_entry(match_key="a", campaign_id=CAMP_B, selection_source="operator_pick"),
        ]
        with self.assertRaises(CampaignMemberSetupError):
            parse_campaign_match_resolutions(raw, connection_id="conn-1")

    def test_enabled_policy_requires_paths(self):
        with self.assertRaises(CampaignMemberSetupError):
            validate_enabled_campaign_member_policy(None, connection_id="conn-1")
        policy = build_campaign_member_policy(
            connection_id="conn-1",
            default_campaign_binding=CAMP_A,
            default_desired_status="Sent",
        )
        validated = validate_enabled_campaign_member_policy(
            policy, connection_id="conn-1"
        )
        self.assertEqual(validated["policy_version"], "campaign_member_policy.v2")

    def test_match_column_without_map_fails_even_with_default(self):
        """Default/Id column must not waive missing name-path map entries."""

        policy = build_campaign_member_policy(
            connection_id="conn-1",
            campaign_match_column="Campaign_Name",
            default_campaign_binding=CAMP_A,
            default_desired_status="Sent",
            campaign_match_resolutions_raw=[],
        )
        with self.assertRaises(CampaignMemberSetupError) as ctx:
            validate_enabled_campaign_member_policy(
                policy,
                connection_id="conn-1",
                required_match_keys={"spring launch"},
            )
        self.assertIn("Missing Campaign name", str(ctx.exception))

    def test_match_column_requires_every_source_key(self):
        resolutions = [
            _bound_entry(match_key="alpha"),
        ]
        policy = build_campaign_member_policy(
            connection_id="conn-1",
            campaign_match_column="Campaign_Name",
            default_desired_status="Sent",
            campaign_match_resolutions_raw=resolutions,
        )
        with self.assertRaises(CampaignMemberSetupError):
            validate_enabled_campaign_member_policy(
                policy,
                connection_id="conn-1",
                required_match_keys={"alpha", "beta"},
            )
        # Complete map accepts.
        resolutions.append(_bound_entry(match_key="beta", campaign_id=CAMP_B))
        policy = build_campaign_member_policy(
            connection_id="conn-1",
            campaign_match_column="Campaign_Name",
            default_desired_status="Sent",
            campaign_match_resolutions_raw=resolutions,
        )
        validate_enabled_campaign_member_policy(
            policy,
            connection_id="conn-1",
            required_match_keys={"alpha", "beta"},
            require_lookup_bound=True,
            bound_entries=resolutions,
        )

    def test_distinct_keys_skip_rows_with_id_column(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            _write_raw_list_csv(
                path,
                [
                    {"Campaign_Name": "Alpha", "Campaign_Id": CAMP_A},
                    {"Campaign_Name": "Beta", "Campaign_Id": ""},
                    {"Campaign_Name": "  beta ", "Campaign_Id": "  "},
                ],
                ["Campaign_Name", "Campaign_Id"],
            )
            source = SourceFile(
                stored_path=str(path),
                columns=["Campaign_Name", "Campaign_Id"],
                csv_encoding="utf-8",
            )
            keys = distinct_match_keys_requiring_resolution(
                source,
                match_column="Campaign_Name",
                id_column="Campaign_Id",
            )
            # Alpha has Id → not required; Beta appears twice blank Id → one key.
            self.assertEqual(keys, {"beta"})

    def test_lookup_evidence_binds_selection_source(self):
        search_one = {
            "connection_id": "conn-1",
            "mode": "name_exact",
            "q": "Spring Launch",
            "campaigns": [
                {
                    "id": CAMP_A,
                    "name": "Spring Launch",
                    "status": "In Progress",
                    "start_date": None,
                    "is_active": True,
                }
            ],
            "returned": 1,
            "truncated": False,
            "too_many_matches": False,
        }
        entry = resolution_from_lookup_evidence(
            connection_id="conn-1",
            match_key_raw="Spring Launch",
            campaign_id=CAMP_A,
            search_result=search_one,
        )
        self.assertTrue(entry["lookup_bound"])
        self.assertEqual(entry["selection_source"], "unique_match")
        self.assertEqual(entry["match_key"], "spring launch")

        search_many = {
            **search_one,
            "campaigns": [
                search_one["campaigns"][0],
                {
                    "id": CAMP_B,
                    "name": "Spring Launch",
                    "status": "Planned",
                    "start_date": None,
                    "is_active": True,
                },
            ],
            "returned": 2,
        }
        entry2 = resolution_from_lookup_evidence(
            connection_id="conn-1",
            match_key_raw="Spring Launch",
            campaign_id=CAMP_B,
            search_result=search_many,
        )
        self.assertEqual(entry2["selection_source"], "operator_pick")

        with self.assertRaises(CampaignMemberSetupError):
            resolution_from_lookup_evidence(
                connection_id="conn-1",
                match_key_raw="Spring Launch",
                campaign_id="701000000000099ZZZ",
                search_result=search_one,
            )

    def test_append_resolution_replaces_same_key(self):
        first = append_resolution_entry(
            [],
            connection_id="conn-1",
            entry=_bound_entry(match_key="alpha", campaign_id=CAMP_A),
        )
        second = append_resolution_entry(
            first,
            connection_id="conn-1",
            entry=_bound_entry(
                match_key="alpha",
                campaign_id=CAMP_B,
                selection_source="operator_pick",
            ),
        )
        self.assertEqual(len(second), 1)
        self.assertEqual(second[0]["campaign_id"], CAMP_B)

    def test_unbound_entry_rejected_on_append(self):
        with self.assertRaises(CampaignMemberSetupError):
            append_resolution_entry(
                [],
                connection_id="conn-1",
                entry={
                    "match_mode": "name_exact",
                    "match_key": "alpha",
                    "campaign_id": CAMP_A,
                    "connection_id": "conn-1",
                    "selection_source": "unique_match",
                    "resolved_at": "2026-08-01T12:00:00Z",
                    "lookup_bound": False,
                },
            )


class CampaignMemberConfigureFormTests(TestCase):
    def test_default_mode_disabled_and_omits_policy(self):
        form = WorkflowConfigurationForm(
            _base_form_data(),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
            verified_resolutions_json="[]",
        )
        self.assertTrue(form.is_valid(), form.errors)
        cm_field = form.fields["mode__campaign_member_writes"]
        self.assertEqual(cm_field.initial, "disabled")
        self.assertIn("execute", dict(cm_field.choices))
        request = form.workflow_request(
            upload_ids={"raw_list": "up-raw"},
            target_provider_id="fake-crm-phase0b-v1",
        )
        self.assertNotIn("campaign_member_policy", request)
        self.assertEqual(request["maximum_modes"]["campaign_member_writes"], "disabled")
        validate_workflow_create(_dummy_column_mapping(request))

    def test_hubspot_ceiling_only_disabled(self):
        form = WorkflowConfigurationForm(
            _base_form_data(),
            product_entry=_list_product(),
            target=_target(cm_ceiling="disabled"),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-hs",
        )
        self.assertTrue(form.is_valid(), form.errors)
        choices = dict(form.fields["mode__campaign_member_writes"].choices)
        self.assertEqual(set(choices), {"disabled"})

    def test_enabled_without_policy_fails(self):
        form = WorkflowConfigurationForm(
            _base_form_data(mode__campaign_member_writes="preview"),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("Campaign membership", str(form.errors))

    def test_enabled_with_default_binding_freezes_policy(self):
        form = WorkflowConfigurationForm(
            _base_form_data(
                mode__campaign_member_writes="dry_run",
                default_campaign_binding=CAMP_A,
                default_desired_status="Sent",
            ),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "up-raw"},
            target_provider_id="fake-crm-phase0b-v1",
        )
        policy = request["campaign_member_policy"]
        self.assertEqual(policy["policy_version"], "campaign_member_policy.v2")
        self.assertEqual(policy["default_campaign_binding"], CAMP_A)
        self.assertEqual(policy["default_desired_status"], "Sent")
        self.assertEqual(request["maximum_modes"]["campaign_member_writes"], "dry_run")
        self.assertEqual(request["connection_id"], "conn-1")
        validate_workflow_create(_dummy_column_mapping(request))

    def test_posted_freeform_resolutions_are_ignored(self):
        """Client-supplied resolution JSON cannot inject unbound Ids."""

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            _write_raw_list_csv(
                path,
                [{"Campaign_Name": "Alpha"}],
                ["Campaign_Name"],
            )
            source = SourceFile(
                stored_path=str(path),
                columns=["Campaign_Name"],
                csv_encoding="utf-8",
            )
            forged = json.dumps(
                [
                    {
                        "match_key": "alpha",
                        "campaign_id": CAMP_A,
                        "selection_source": "unique_match",
                        "resolved_at": "2026-08-01T12:00:00Z",
                        "connection_id": "conn-1",
                        "lookup_bound": True,
                    }
                ]
            )
            form = WorkflowConfigurationForm(
                _base_form_data(
                    mode__campaign_member_writes="preview",
                    campaign_match_column="Campaign_Name",
                    default_desired_status="Sent",
                    # Attacker tries to forge a complete map via POST.
                    campaign_match_resolutions_json=forged,
                ),
                product_entry=_list_product(),
                target=_target(),
                uploaded_roles={"raw_list"},
                reference_acquisition_requirement="execute",
                connection_id="conn-1",
                # Server store is empty — POST body must not win.
                verified_resolutions_json="[]",
                raw_list_source=source,
            )
            self.assertFalse(form.is_valid())
            self.assertIn("Missing Campaign name", str(form.errors))

    def test_resolutions_from_verified_store_cover_source_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            _write_raw_list_csv(
                path,
                [
                    {"Campaign_Name": "Spring Launch"},
                    {"Campaign_Name": "Fall Drive"},
                ],
                ["Campaign_Name"],
            )
            source = SourceFile(
                stored_path=str(path),
                columns=["Campaign_Name"],
                csv_encoding="utf-8",
            )
            verified = [
                _bound_entry(match_key="spring launch"),
                _bound_entry(match_key="fall drive", campaign_id=CAMP_B),
            ]
            form = WorkflowConfigurationForm(
                _base_form_data(
                    mode__campaign_member_writes="preview",
                    campaign_match_column="Campaign_Name",
                    default_desired_status="Sent",
                ),
                product_entry=_list_product(),
                target=_target(),
                uploaded_roles={"raw_list"},
                reference_acquisition_requirement="execute",
                connection_id="conn-1",
                verified_resolutions_json=json.dumps(verified),
                raw_list_source=source,
            )
            self.assertTrue(form.is_valid(), form.errors)
            request = form.workflow_request(
                upload_ids={"raw_list": "up-raw"},
                target_provider_id="fake-crm-phase0b-v1",
            )
            frozen = request["campaign_member_policy"]["campaign_match_resolutions"]
            self.assertEqual(len(frozen), 2)
            # Public body must not leak internal lookup_bound marker.
            self.assertNotIn("lookup_bound", frozen[0])
            validate_workflow_create(_dummy_column_mapping(request))

    def test_incomplete_map_fails_with_default_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "raw.csv"
            _write_raw_list_csv(
                path,
                [{"Campaign_Name": "Orphan Name"}],
                ["Campaign_Name"],
            )
            source = SourceFile(
                stored_path=str(path),
                columns=["Campaign_Name"],
                csv_encoding="utf-8",
            )
            form = WorkflowConfigurationForm(
                _base_form_data(
                    mode__campaign_member_writes="preview",
                    campaign_match_column="Campaign_Name",
                    default_campaign_binding=CAMP_A,
                    default_desired_status="Sent",
                ),
                product_entry=_list_product(),
                target=_target(),
                uploaded_roles={"raw_list"},
                reference_acquisition_requirement="execute",
                connection_id="conn-1",
                verified_resolutions_json="[]",
                raw_list_source=source,
            )
            self.assertFalse(form.is_valid())
            self.assertIn("orphan name", str(form.errors).lower())

    def test_policy_fields_with_disabled_mode_rejected(self):
        form = WorkflowConfigurationForm(
            _base_form_data(
                mode__campaign_member_writes="disabled",
                default_campaign_binding=CAMP_A,
                default_desired_status="Sent",
            ),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
        )
        self.assertFalse(form.is_valid())
        self.assertIn("enabling", str(form.errors).lower())

    def test_disabled_mode_ignores_exploratory_draft_resolutions(self):
        """Draft picks alone must not force enablement (B+ rem blocker)."""

        verified = [_bound_entry(match_key="spring launch")]
        form = WorkflowConfigurationForm(
            _base_form_data(mode__campaign_member_writes="disabled"),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
            verified_resolutions_json=json.dumps(verified),
        )
        self.assertTrue(form.is_valid(), form.errors)
        request = form.workflow_request(
            upload_ids={"raw_list": "up-raw"},
            target_provider_id="fake-crm-phase0b-v1",
        )
        self.assertNotIn("campaign_member_policy", request)
        self.assertEqual(request["maximum_modes"]["campaign_member_writes"], "disabled")
        validate_workflow_create(_dummy_column_mapping(request))

    def test_malformed_default_campaign_id_fails(self):
        form = WorkflowConfigurationForm(
            _base_form_data(
                mode__campaign_member_writes="preview",
                default_campaign_binding="bad-id",
                default_desired_status="Sent",
            ),
            product_entry=_list_product(),
            target=_target(),
            uploaded_roles={"raw_list"},
            reference_acquisition_requirement="execute",
            connection_id="conn-1",
        )
        self.assertFalse(form.is_valid())


class CampaignLookupProxyViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        owner = uuid4()
        session = self.client.session
        session[OWNER_SESSION_KEY] = str(owner)
        session.save()
        self.session = ImportSession.objects.create(
            owner_id=owner,
            setup_entity="people",
            setup_operation="crm_matching",
            setup_reference_source="connected_crm",
            setup_connection_id="conn-fe-cm3",
            target_provider_id="fake-crm-phase0b-v1",
        )

    def test_lookup_proxy_requires_connection_and_forwards_search(self):
        orphan = ImportSession.objects.create(
            owner_id=self.session.owner_id,
            setup_entity="people",
            setup_operation="crm_matching",
            setup_reference_source="uploaded",
            setup_connection_id="",
        )
        url = reverse("importer:campaign_lookup", args=[orphan.id])
        response = self.client.get(url, {"q": "Spring"})
        self.assertEqual(response.status_code, 422)

        payload = {
            "connection_id": "conn-fe-cm3",
            "mode": "name_exact",
            "q": "Spring",
            "campaigns": [
                {
                    "id": CAMP_A,
                    "name": "Spring",
                    "status": "In Progress",
                    "start_date": None,
                    "is_active": True,
                }
            ],
            "returned": 1,
            "truncated": False,
            "too_many_matches": False,
        }
        with patch(
            "importer.workflow_views.EasyImportsApiClient.crm_campaigns_search",
            return_value=payload,
        ) as mocked:
            response = self.client.get(
                reverse("importer:campaign_lookup", args=[self.session.id]),
                {"q": "Spring"},
            )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["returned"], 1)
        self.assertEqual(body["campaigns"][0]["id"], CAMP_A)
        mocked.assert_called_once()

    def test_resolution_pick_requires_server_lookup_and_persists_durably(self):
        token = issue_form_token(
            owner_id=self.session.owner_id,
            session=self.session,
            action_kind="cm_resolution_pick",
            logical_action_identity=f"session:{self.session.id}:cm_resolution_pick",
            logical_action_generation=0,
        )
        search_payload = {
            "connection_id": "conn-fe-cm3",
            "mode": "name_exact",
            "q": "Spring Launch",
            "campaigns": [
                {
                    "id": CAMP_A,
                    "name": "Spring Launch",
                    "status": "In Progress",
                    "start_date": None,
                    "is_active": True,
                }
            ],
            "returned": 1,
            "truncated": False,
            "too_many_matches": False,
        }
        with patch(
            "importer.workflow_views.EasyImportsApiClient.crm_campaigns_search",
            return_value=search_payload,
        ) as mocked:
            response = self.client.post(
                reverse("importer:campaign_resolution_pick", args=[self.session.id]),
                {
                    "form_token": token,
                    "match_key": "Spring Launch",
                    "campaign_id": CAMP_A,
                },
            )
        self.assertEqual(response.status_code, 302)
        mocked.assert_called_once()
        self.session.refresh_from_db()
        entries = load_session_resolutions(self.session)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["campaign_id"], CAMP_A)
        self.assertTrue(entries[0]["lookup_bound"])
        self.assertEqual(entries[0]["selection_source"], "unique_match")

    def test_pick_without_lookup_hit_is_rejected(self):
        token = issue_form_token(
            owner_id=self.session.owner_id,
            session=self.session,
            action_kind="cm_resolution_pick",
            logical_action_identity=f"session:{self.session.id}:cm_resolution_pick",
            logical_action_generation=0,
        )
        empty = {
            "connection_id": "conn-fe-cm3",
            "mode": "name_exact",
            "q": "Missing",
            "campaigns": [],
            "returned": 0,
            "truncated": False,
            "too_many_matches": False,
        }
        with patch(
            "importer.workflow_views.EasyImportsApiClient.crm_campaigns_search",
            return_value=empty,
        ):
            response = self.client.post(
                reverse("importer:campaign_resolution_pick", args=[self.session.id]),
                {
                    "form_token": token,
                    "match_key": "Missing",
                    "campaign_id": CAMP_A,
                },
            )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(load_session_resolutions(self.session), [])

    def test_multi_pick_survives_across_searches(self):
        """Finding 1 rem: second search must not wipe the first resolution."""

        first = [_bound_entry(match_key="alpha", connection_id="conn-fe-cm3")]
        save_session_resolutions(self.session, first)
        self.session.refresh_from_db()
        self.assertEqual(len(load_session_resolutions(self.session)), 1)

        token = issue_form_token(
            owner_id=self.session.owner_id,
            session=self.session,
            action_kind="cm_resolution_pick",
            logical_action_identity=f"session:{self.session.id}:cm_resolution_pick",
            logical_action_generation=0,
        )
        search_payload = {
            "connection_id": "conn-fe-cm3",
            "mode": "name_exact",
            "q": "Beta",
            "campaigns": [
                {
                    "id": CAMP_B,
                    "name": "Beta",
                    "status": "In Progress",
                    "start_date": None,
                    "is_active": True,
                }
            ],
            "returned": 1,
            "truncated": False,
            "too_many_matches": False,
        }
        with patch(
            "importer.workflow_views.EasyImportsApiClient.crm_campaigns_search",
            return_value=search_payload,
        ):
            self.client.post(
                reverse("importer:campaign_resolution_pick", args=[self.session.id]),
                {
                    "form_token": token,
                    "match_key": "Beta",
                    "campaign_id": CAMP_B,
                },
            )
        self.session.refresh_from_db()
        keys = {item["match_key"] for item in load_session_resolutions(self.session)}
        self.assertEqual(keys, {"alpha", "beta"})

    def test_clear_draft_resolutions(self):
        save_session_resolutions(
            self.session,
            [_bound_entry(match_key="alpha", connection_id="conn-fe-cm3")],
        )
        self.assertEqual(len(load_session_resolutions(self.session)), 1)
        token = issue_form_token(
            owner_id=self.session.owner_id,
            session=self.session,
            action_kind="cm_resolution_clear",
            logical_action_identity=f"session:{self.session.id}:cm_resolution_clear",
            logical_action_generation=0,
        )
        response = self.client.post(
            reverse("importer:campaign_resolution_clear", args=[self.session.id]),
            {"form_token": token},
        )
        self.assertEqual(response.status_code, 302)
        self.session.refresh_from_db()
        self.assertEqual(load_session_resolutions(self.session), [])
