"""Regression coverage for prepare-reviewed-result mutation classification."""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from uuid import uuid4

from django.test import TestCase

from .api_client import EasyImportsApiClient, create_or_reuse_mutation
from .api_contract import validate_prepare_reviewed_result
from .models import ApiMutation, ImportSession


def _completed_prepare_result() -> dict:
    return {
        "command_kind": "prepare_duplicate_reviewed_result",
        "review_run_id": "review-run-1",
        "status": "complete",
        "reviewed_result_content_digest": "sha256:reviewed-result",
        "expected_revision": 7,
    }


class PrepareReviewedResultClassifierTests(TestCase):
    def setUp(self) -> None:
        self.session = ImportSession.objects.create(owner_id=uuid4())

    def test_completed_prepare_200_classifies_completed(self) -> None:
        payload = _completed_prepare_result()
        self.assertEqual(validate_prepare_reviewed_result(payload), payload)

        mutation = MagicMock(mutation_kind="prepare_duplicate_reviewed_result")
        validated, state, error_code, error_message = (
            EasyImportsApiClient()._classify_mutation_payload(mutation, payload)
        )

        self.assertEqual(validated, payload)
        self.assertEqual(state, ApiMutation.State.COMPLETED)
        self.assertEqual(error_code, "")
        self.assertEqual(error_message, "")

    def test_exact_retry_replay_of_completed_prepare_stays_completed(self) -> None:
        payload = _completed_prepare_result()
        mutation = create_or_reuse_mutation(
            session=self.session,
            form_instance=uuid4(),
            mutation_kind="prepare_duplicate_reviewed_result",
            route="/v1/workflows/review-run-1/prepare-reviewed-result",
            logical_action_identity="prepare-reviewed-result:review-run-1",
            request_json={"expected_revision": 7},
            resource_identity="review-run-1",
        )
        response = MagicMock()
        response.status_code = 200
        response.content = b"completed prepare result"
        response.json.return_value = payload
        client = EasyImportsApiClient()

        with patch.object(client, "assert_compatible", return_value=None):
            with patch.object(client, "_send", return_value=response) as send_mock:
                first = client.dispatch(mutation)
                replay = client.dispatch(mutation, explicit_retry=True)

        mutation.refresh_from_db()
        self.assertEqual(send_mock.call_count, 1)
        self.assertEqual(first.response, payload)
        self.assertEqual(replay.response, payload)
        self.assertEqual(first.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(replay.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
