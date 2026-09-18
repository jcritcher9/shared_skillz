"""EFG-4B: Django review consumes generated HTTP only.

The public window body is unchanged. Django still journals GET/POST through
generated contracts and never imports mappings_2.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/in_progress/crm_duplicate_exact_first_grouping_redesign.md
Phase 4B.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer import api_client, api_contract, journey_views
from importer.api_client import ApiRejectedError, EasyImportsApiClient
from importer.models import ImportSession
from importer.test_crm_duplicate_review_refresh_efg0a import (
    STILL_WORKING_COPY,
    _not_ready,
    _ready_window,
)


class Efg4bImportGateTests(SimpleTestCase):
    def test_django_review_path_does_not_import_mappings_2(self):
        for module in (journey_views, api_client, api_contract):
            source = inspect.getsource(module)
            self.assertIsNone(
                re.search(r"^(?:import|from)\s+mappings_2\b", source, re.M)
            )

    def test_generated_client_is_the_window_path(self):
        source = inspect.getsource(EasyImportsApiClient.duplicate_review_window)
        self.assertIn("/duplicate-review-window", source)
        self.assertIn("validate_duplicate_review_window", source)
        self.assertIn("cursor", source)

    def test_review_cursor_keys_are_scoped_per_run(self):
        from importer.journey_views import (
            _review_cursor_session_key,
            _review_next_cursor_session_key,
        )

        self.assertEqual(
            _review_cursor_session_key("run-a"), "efg_review_cursor:run-a"
        )
        self.assertNotEqual(
            _review_cursor_session_key("run-a"),
            _review_cursor_session_key("run-b"),
        )
        self.assertNotEqual(
            _review_next_cursor_session_key("run-a"),
            _review_next_cursor_session_key("run-b"),
        )


class Efg4bReviewContractTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-efg4b-source",
                "review_run_id": "run-efg4b-review",
                "redesign_phase": "4b",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    def _review_url(self):
        return reverse(
            "importer:crm_duplicate_journey_review",
            kwargs={"session_id": self.session.id},
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_get_renders_generated_window_and_never_writes(self):
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=_ready_window(),
                ) as window_get:
                    with patch(
                        "importer.journey_views._dispatch_json_mutation",
                        side_effect=AssertionError("GET must not dispatch"),
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("g0", body)
        self.assertIn('value="decline"', body)
        self.assertIn('value="quarantine"', body)
        self.assertIn('name="review_cursor"', body)
        self.assertIn('name="review_next_cursor"', body)
        self.assertIn("L0", body)
        window_get.assert_called_once()
        kwargs = window_get.call_args.kwargs
        self.assertIn("owner_session", kwargs)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_complete_set_wait_journals_nothing_on_get(self):
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=_not_ready(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value={
                            "status": "running",
                            "stage": "building_review_materials",
                            "duplicate_analysis_progress": {
                                "stage": "building_review_materials",
                                "completed_count": 0,
                                "total_count": 12,
                                "count_unit": "groups",
                                "review_ready": False,
                                "review_window_ready": False,
                                "review_groups_ready": 0,
                                "review_groups_total": 12,
                            },
                        },
                    ):
                        with patch(
                            "importer.journey_views._dispatch_json_mutation",
                            side_effect=AssertionError("GET must not dispatch"),
                        ):
                            response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn(STILL_WORKING_COPY, body)
        self.assertIn("Refresh status", body)
        self.assertNotIn("2500", body)
