from __future__ import annotations

from pathlib import Path

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import SimpleTestCase, TestCase

from .api_client import MutationReuseError
from .forms import CrmDuplicateJourneyForm
from .journey_views import (
    InvalidMatchingModeError,
    _CHANGED_ATTEMPT_REUSE_MSG,
    _claim_or_get_attempt_session,
    _form_matching_mode,
    _intent_from_claim,
    _normalize_matching_mode_intent,
    _reject_changed_attempt_reuse,
    _root_attempt_intent,
)
from .models import CrmDuplicateJourneyAttemptClaim, ImportSession


def _form(data: dict) -> CrmDuplicateJourneyForm:
    return CrmDuplicateJourneyForm(
        data=data,
        connections=[
            {
                "connection_id": "conn-1",
                "status": "connected",
                "provider_key": "fake",
                "provider_label": "Fake",
                "display_label": "Demo",
            }
        ],
    )


class MatchingModeDdr4FormTests(SimpleTestCase):
    def test_form_defaults_and_accepts_both_modes(self):
        omitted = _form(
            {
                "connection_id": "conn-1",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
            }
        )
        self.assertTrue(omitted.is_valid(), omitted.errors)
        self.assertEqual(omitted.cleaned_data["matching_mode"], "default")
        self.assertEqual(_form_matching_mode(omitted), "default")

        exact = _form(
            {
                "connection_id": "conn-1",
                "entity_family": "person",
                "source_mode": "acquire_all",
                "matching_mode": "exact_only",
                "form_token": "token",
            }
        )
        self.assertTrue(exact.is_valid(), exact.errors)
        self.assertEqual(exact.cleaned_data["matching_mode"], "exact_only")
        self.assertEqual(_form_matching_mode(exact), "exact_only")

    def test_form_rejects_unknown_mode(self):
        form = _form(
            {
                "connection_id": "conn-1",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "matching_mode": "fuzzy",
                "form_token": "token",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("matching_mode", form.errors)

    def test_changed_matching_mode_cannot_reuse_attempt(self):
        original = _root_attempt_intent(
            source_mode="acquire_all",
            connection_id="conn-1",
            entity_family="company",
            duplicate_execution_maximum="dry_run",
            matching_mode="default",
        )
        changed = dict(original)
        changed["matching_mode"] = "exact_only"
        with self.assertRaises(MutationReuseError) as raised:
            _reject_changed_attempt_reuse(original, changed)
        self.assertEqual(str(raised.exception), _CHANGED_ATTEMPT_REUSE_MSG)

    def test_normalize_matching_mode_intent_fails_closed(self):
        self.assertEqual(_normalize_matching_mode_intent(None), "default")
        self.assertEqual(_normalize_matching_mode_intent("exact_only"), "exact_only")
        with self.assertRaises(InvalidMatchingModeError):
            _normalize_matching_mode_intent("fuzzy")
        with self.assertRaises(InvalidMatchingModeError):
            _normalize_matching_mode_intent(" exact_only ")

    def test_form_rejects_padded_mode(self):
        form = _form(
            {
                "connection_id": "conn-1",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "matching_mode": " exact_only ",
                "form_token": "token",
            }
        )
        self.assertFalse(form.is_valid())
        self.assertIn("matching_mode", form.errors)

    def test_start_page_copy_is_entity_neutral(self):
        form = _form(
            {
                "connection_id": "conn-1",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
            }
        )
        self.assertEqual(form.fields["matching_mode"].label, "Matching detail")
        self.assertEqual(
            dict(form.fields["matching_mode"].choices),
            {
                "default": "Default matching",
                "exact_only": "Exact matches only",
            },
        )
        template = (
            Path(__file__).resolve().parent
            / "templates"
            / "importer"
            / "crm_duplicate_journey.html"
        ).read_text(encoding="utf-8")
        self.assertIn("Matching detail", template)
        self.assertNotIn("disable fuzzy", template.lower())


class MatchingModeDdr4ClaimTests(TestCase):
    def _round_trip(self, *, entity_family: str) -> None:
        owner = uuid4()
        request = MagicMock()
        request.session = {}
        with patch(
            "importer.journey_views.owner_id_for_request", return_value=owner
        ):
            journal = ImportSession.objects.create(
                owner_id=owner, product_key="crm.journey"
            )
            root = uuid4()
            first = _root_attempt_intent(
                source_mode="acquire_all",
                connection_id="crm_conn_test",
                entity_family=entity_family,
                duplicate_execution_maximum="dry_run",
                matching_mode="exact_only",
            )

            def factory():
                return ImportSession.objects.create(
                    owner_id=owner,
                    product_key="easyimports.duplicate_resolution",
                    status=ImportSession.Status.CREATED,
                    options=dict(first),
                )

            session, created = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=first,
                factory=factory,
            )
            self.assertTrue(created)
            claim = CrmDuplicateJourneyAttemptClaim.objects.get(
                journal=journal, root_form_instance=root
            )
            self.assertEqual(claim.matching_mode, "exact_only")
            self.assertEqual(session.options["matching_mode"], "exact_only")

            reloaded = CrmDuplicateJourneyAttemptClaim.objects.get(pk=claim.pk)
            self.assertEqual(_intent_from_claim(reloaded)["matching_mode"], "exact_only")

            again, created2 = _claim_or_get_attempt_session(
                request,
                journal,
                form_instance=root,
                intent=first,
                factory=factory,
            )
            self.assertFalse(created2)
            self.assertEqual(again.id, session.id)

            changed = dict(first)
            changed["matching_mode"] = "default"
            with self.assertRaises(MutationReuseError):
                _claim_or_get_attempt_session(
                    request,
                    journal,
                    form_instance=root,
                    intent=changed,
                    factory=factory,
                )

            reloaded.matching_mode = " exact_only "
            reloaded.save(update_fields=["matching_mode"])
            with self.assertRaises(InvalidMatchingModeError):
                _intent_from_claim(
                    CrmDuplicateJourneyAttemptClaim.objects.get(pk=reloaded.pk)
                )

    def test_company_claim_persists_retries_and_rejects_changed_mode(self):
        self._round_trip(entity_family="company")

    def test_people_claim_persists_retries_and_rejects_changed_mode(self):
        self._round_trip(entity_family="person")
