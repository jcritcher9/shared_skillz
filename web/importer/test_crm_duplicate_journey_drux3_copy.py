"""DRUX-3: merge-plan copy, large CTAs, stacked write-mode radios."""

from __future__ import annotations

from pathlib import Path
import re
from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import TestCase, override_settings
from django.urls import reverse

from .api_client import EasyImportsApiClient
from .crm_duplicate_merge_copy import (
    APPROVE_MERGE_PLAN_LABEL,
    AUTHORIZE_MERGE_STEP_LABEL,
    CONTINUE_TO_APPROVE_MERGE_PLAN_LABEL,
    GO_BACK_TO_REVIEWING_GROUPS_LABEL,
    LEGACY_FREEZE_MERGE_PLAN_LABEL,
    LEGACY_INVALIDATE_FROZEN_PLAN_LABEL,
    MERGE_MODE_CHOICES_CLASS,
    MERGE_PLAN_CTA_CLASS,
)
from .models import CrmDuplicateMergePlanLease, ImportSession
from .test_crm_duplicate_journey_phase5a import _intent, _reviewed_result

_APP_CSS = (
    Path(__file__).resolve().parent / "static" / "importer" / "css" / "app.css"
)


def _css_text() -> str:
    return _APP_CSS.read_text(encoding="utf-8")


def _submit_button_classes(html: str, label: str) -> str:
    pattern = (
        r'<button\b[^>]*\btype="submit"[^>]*\bclass="([^"]*)"[^>]*>'
        rf"\s*{re.escape(label)}"
    )
    match = re.search(pattern, html)
    if match is None:
        raise AssertionError(f"submit button for {label!r} not found")
    return match.group(1)


class CrmDuplicateJourneyDrux3CopyTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="CRM dupe DRUX-3",
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            target_provider_id="fake",
            options={
                "run_id": "run-source-5a",
                "review_run_id": "run-review-5a",
                "review_handoff_id": "review_abc",
                "apply_root_form_instance": str(uuid4()),
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        session = self.client.session
        session["easyimports_owner_id"] = str(self.owner)
        session.save()

    def _patch_owner(self):
        return patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        )

    def _get_merge(self, *, frozen: bool):
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(frozen=frozen),
                ):
                    if frozen:
                        with patch.object(
                            EasyImportsApiClient,
                            "workflow",
                            return_value={
                                "run_id": "run-cont-5a",
                                "revision": 2,
                                "status": "awaiting_effect_authorization",
                                "effect_intent": _intent(),
                            },
                        ):
                            with patch(
                                "importer.journey_views.store_workflow_projection"
                            ):
                                return self.client.get(
                                    reverse(
                                        "importer:crm_duplicate_journey_merge",
                                        kwargs={"session_id": self.session.id},
                                    )
                                )
                    with patch(
                        "importer.journey_views._dispatch_json_mutation"
                    ) as dispatch:
                        response = self.client.get(
                            reverse(
                                "importer:crm_duplicate_journey_merge",
                                kwargs={"session_id": self.session.id},
                            )
                        )
                    dispatch.assert_not_called()
                    return response

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_unfrozen_merge_page_uses_approve_copy_and_large_cta(self):
        response = self._get_merge(frozen=False)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(APPROVE_MERGE_PLAN_LABEL, html)
        self.assertNotIn(LEGACY_FREEZE_MERGE_PLAN_LABEL, html)
        self.assertIn("explicitly approve the merge plan", html)
        self.assertIn("does not approve", html.lower())
        classes = _submit_button_classes(html, APPROVE_MERGE_PLAN_LABEL)
        self.assertIn("btn", classes.split())
        self.assertIn(MERGE_PLAN_CTA_CLASS, classes.split())
        self.assertIn("btn-primary", classes.split())

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_frozen_merge_page_stacked_radios_and_go_back_cta(self):
        CrmDuplicateMergePlanLease.objects.update_or_create(
            session=self.session,
            defaults={
                "epoch": 0,
                "continuation_run_id": "run-cont-5a",
                "decision_set_content_digest": "sha256:x",
            },
        )
        response = self._get_merge(frozen=True)
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(GO_BACK_TO_REVIEWING_GROUPS_LABEL, html)
        self.assertNotIn(LEGACY_INVALIDATE_FROZEN_PLAN_LABEL, html)
        self.assertIn(AUTHORIZE_MERGE_STEP_LABEL, html)
        self.assertIn(f'class="{MERGE_MODE_CHOICES_CLASS}"', html)
        fieldset = re.search(
            rf'<fieldset class="{re.escape(MERGE_MODE_CHOICES_CLASS)}">(.*?)</fieldset>',
            html,
            flags=re.S,
        )
        self.assertIsNotNone(fieldset, "merge-mode-choices fieldset missing")
        choice_labels = re.findall(r'<label class="choice">', fieldset.group(1))
        self.assertEqual(len(choice_labels), 3)
        self.assertRegex(
            fieldset.group(1),
            re.compile(
                r'value="preview".*?value="dry_run".*?value="execute"',
                flags=re.S,
            ),
        )
        self.assertIn('name="merge_action" value="invalidate"', html)
        auth_classes = _submit_button_classes(html, AUTHORIZE_MERGE_STEP_LABEL)
        self.assertIn("btn", auth_classes.split())
        self.assertIn(MERGE_PLAN_CTA_CLASS, auth_classes.split())
        back_classes = _submit_button_classes(
            html, GO_BACK_TO_REVIEWING_GROUPS_LABEL
        )
        self.assertIn("btn", back_classes.split())
        self.assertIn(MERGE_PLAN_CTA_CLASS, back_classes.split())

    def test_stylesheet_defines_cta_and_stacked_mode_choices(self):
        css = _css_text()
        self.assertRegex(
            css,
            r"\.btn-cta\s*\{[^}]*width:\s*100%",
        )
        self.assertRegex(
            css,
            r"\.merge-mode-choices\s+\.choice\s*\{[^}]*display:\s*flex",
        )
        self.assertRegex(
            css,
            r"\.merge-mode-choices\s*\{[^}]*flex-direction:\s*column",
        )

    def test_review_summary_continue_link_uses_approve_copy(self):
        html = render_to_string(
            "importer/crm_duplicate_journey_review_summary.html",
            {
                "reviewed_result": _reviewed_result(),
                "edit_dispositions": False,
                "edit_dispositions_url": "/review/?edit=1",
                "edit_dispositions_label": "Edit dispositions",
                "form_errors": [],
                "form_token": "token",
                "merge_url": "/merge/",
                "progress_url": "/progress/",
                "workflow_url": "/workflow/",
                "start_url": "/",
                "run_id": "run-review-5a",
                "terminal_message": "All duplicate groups have been reviewed.",
            },
        )
        self.assertIn(CONTINUE_TO_APPROVE_MERGE_PLAN_LABEL, html)
        self.assertNotIn("Continue to freeze merge plan", html)
        self.assertNotIn(LEGACY_FREEZE_MERGE_PLAN_LABEL, html)
