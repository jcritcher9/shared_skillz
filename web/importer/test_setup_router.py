"""Exhaustive truth-table tests for the pure setup intent router."""

from __future__ import annotations

import unittest

from importer.setup_router import (
    OperatorIntent,
    RouteError,
    derive_route,
    parse_operator_intent,
    validate_uploads_against_route,
)


class ParseOperatorIntentTests(unittest.TestCase):
    def test_clean_only_forces_none_reference_source(self):
        intent = parse_operator_intent(
            entity="Accounts",
            operation="clean_only",
            reference_source="",
        )
        self.assertEqual(intent.reference_source, "none")

    def test_clean_only_rejects_uploaded_reference_source(self):
        with self.assertRaises(RouteError):
            parse_operator_intent(
                entity="accounts",
                operation="clean_only",
                reference_source="uploaded",
            )

    def test_matching_requires_reference_source(self):
        with self.assertRaises(RouteError):
            parse_operator_intent(
                entity="accounts",
                operation="crm_matching",
                reference_source="none",
            )

    def test_people_clean_only_requires_output(self):
        with self.assertRaises(RouteError):
            parse_operator_intent(
                entity="people",
                operation="clean_only",
            )

    def test_people_matching_rejects_people_output(self):
        with self.assertRaises(RouteError):
            parse_operator_intent(
                entity="people",
                operation="crm_matching",
                reference_source="uploaded",
                people_output="contact",
            )


class DeriveRouteTruthTableTests(unittest.TestCase):
    def test_accounts_clean_only(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="clean_only",
                reference_source="none",
            )
        )
        self.assertEqual(route.product_key, "easyimports.single_dataset_import")
        self.assertEqual(route.target_object, "account")
        self.assertEqual(route.required_upload_roles, frozenset({"dataset"}))
        self.assertEqual(route.reference_acquisition_requirement, "prohibited")
        self.assertIn("accounts", route.prohibited_upload_roles)
        self.assertEqual(route.content_type, "accounts")
        self.assertEqual(route.canon_profile, "accounts")

    def test_people_clean_only_contacts(self):
        route = derive_route(
            OperatorIntent(
                entity="people",
                operation="clean_only",
                reference_source="none",
                people_output="contact",
            )
        )
        self.assertEqual(route.product_key, "easyimports.single_dataset_import")
        self.assertEqual(route.target_object, "contact")
        self.assertEqual(route.person_kind, "contact")
        self.assertEqual(route.required_upload_roles, frozenset({"dataset"}))
        self.assertEqual(route.reference_acquisition_requirement, "prohibited")

    def test_people_clean_only_leads(self):
        route = derive_route(
            OperatorIntent(
                entity="people",
                operation="clean_only",
                reference_source="none",
                people_output="lead",
            )
        )
        self.assertEqual(route.target_object, "lead")
        self.assertEqual(route.person_kind, "lead")

    def test_accounts_matching_uploaded(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="crm_matching",
                reference_source="uploaded",
            )
        )
        self.assertEqual(route.product_key, "easyimports.account_list_import")
        self.assertEqual(
            route.required_upload_roles, frozenset({"raw_list", "accounts"})
        )
        self.assertEqual(route.reference_acquisition_requirement, "disabled")
        self.assertIn("dataset", route.prohibited_upload_roles)

    def test_accounts_matching_connected(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="crm_matching",
                reference_source="connected_crm",
            )
        )
        self.assertEqual(route.product_key, "easyimports.account_list_import")
        self.assertEqual(route.required_upload_roles, frozenset({"raw_list"}))
        self.assertIn("accounts", route.prohibited_upload_roles)
        self.assertEqual(route.reference_acquisition_requirement, "execute")

    def test_people_matching_uploaded(self):
        route = derive_route(
            OperatorIntent(
                entity="people",
                operation="crm_matching",
                reference_source="uploaded",
            )
        )
        self.assertEqual(route.product_key, "easyimports.list_import")
        self.assertEqual(route.required_upload_roles, frozenset({"raw_list"}))
        self.assertEqual(route.reference_acquisition_requirement, "disabled")
        self.assertEqual(
            route.required_any_of_role_groups, (frozenset({"contacts", "leads"}),)
        )
        self.assertIn("contacts", route.optional_upload_roles)

    def test_people_matching_connected(self):
        route = derive_route(
            OperatorIntent(
                entity="people",
                operation="crm_matching",
                reference_source="connected_crm",
            )
        )
        self.assertEqual(route.product_key, "easyimports.list_import")
        self.assertEqual(route.required_upload_roles, frozenset({"raw_list"}))
        self.assertEqual(route.reference_acquisition_requirement, "execute")
        for role in ("accounts", "contacts", "leads"):
            self.assertIn(role, route.prohibited_upload_roles)


class UploadValidationTests(unittest.TestCase):
    def test_matching_without_references_is_error_not_fallback(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="crm_matching",
                reference_source="uploaded",
            )
        )
        problems = validate_uploads_against_route(
            route, completed_roles={"raw_list"}
        )
        self.assertTrue(any("accounts" in item for item in problems))

    def test_clean_only_rejects_reference_upload(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="clean_only",
                reference_source="none",
            )
        )
        problems = validate_uploads_against_route(
            route, completed_roles={"dataset", "accounts"}
        )
        self.assertTrue(any("not used" in item for item in problems))

    def test_people_uploaded_requires_contacts_or_leads(self):
        route = derive_route(
            OperatorIntent(
                entity="people",
                operation="crm_matching",
                reference_source="uploaded",
            )
        )
        only_accounts = validate_uploads_against_route(
            route, completed_roles={"raw_list", "accounts"}
        )
        self.assertTrue(any("contacts" in item for item in only_accounts))
        with_contacts = validate_uploads_against_route(
            route, completed_roles={"raw_list", "contacts"}
        )
        self.assertEqual(with_contacts, [])

    def test_connected_accounts_prohibits_accounts_upload(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="crm_matching",
                reference_source="connected_crm",
            )
        )
        problems = validate_uploads_against_route(
            route, completed_roles={"raw_list", "accounts"}
        )
        self.assertTrue(problems)

    def test_valid_clean_only_complete(self):
        route = derive_route(
            OperatorIntent(
                entity="accounts",
                operation="clean_only",
                reference_source="none",
            )
        )
        self.assertEqual(
            validate_uploads_against_route(route, completed_roles={"dataset"}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
