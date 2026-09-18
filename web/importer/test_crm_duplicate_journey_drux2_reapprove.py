"""DRUX-2: re-approve after go-back uses a merge-plan lease + once-only CAS."""

from __future__ import annotations

from html.parser import HTMLParser
from threading import Thread
from unittest.mock import patch
from uuid import UUID, uuid4

from django.db import close_old_connections
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import reverse

from .api_client import EasyImportsApiClient, create_or_reuse_mutation
from .journey_views import (
    _cas_apply_invalidate_lease,
    _invalidate_merge_identity,
    _step_form_instance,
)
from .models import ApiMutation, CrmDuplicateMergePlanLease, ImportSession
from .test_crm_duplicate_journey_phase5a import (
    _handoff_receipt,
    _intent,
    _reviewed_result,
)

REUSE_ERROR = "This logical action was already submitted with different values."


class _NamedInputFormParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, str]] = []
        self._current: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_d = {key: (value or "") for key, value in attrs}
        if tag == "form":
            self._current = {}
            return
        if self._current is None:
            return
        if tag in {"input", "button"}:
            name = attrs_d.get("name")
            if name:
                self._current[name] = attrs_d.get("value") or ""


    def handle_endtag(self, tag: str) -> None:
        if tag == "form" and self._current is not None:
            self.forms.append(self._current)
            self._current = None


def _invalidate_payload_from_html(html: str) -> dict[str, str]:
    parser = _NamedInputFormParser()
    parser.feed(html)
    matches = [
        dict(form) for form in parser.forms if form.get("merge_action") == "invalidate"
    ]
    if len(matches) != 1:
        raise AssertionError(f"expected one invalidate form, found {len(matches)}")
    return matches[0]


def _handoff_for(continuation_run_id: str) -> dict:
    receipt = _handoff_receipt()
    receipt["result"]["merge_plan_handoff"]["continuation_run_id"] = continuation_run_id
    return receipt


def _invalidate_receipt(*revoked: str) -> dict:
    return {
        "outcome": "accepted",
        "command_kind": "invalidate_duplicate_merge_plan_freeze",
        "result": {
            "invalidated": True,
            "revoked_continuation_run_ids": list(revoked),
        },
    }


class _MergeJourneyMixin:
    owner: object
    session: ImportSession
    client: Client

    def _seed_session(self) -> None:
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="CRM dupe DRUX-2",
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

    def _merge_url(self) -> str:
        return reverse(
            "importer:crm_duplicate_journey_merge",
            kwargs={"session_id": self.session.id},
        )

    def _invalidate_payload_from_get(self) -> dict[str, str]:
        response = self.client.get(self._merge_url())
        self.assertEqual(response.status_code, 200)
        payload = _invalidate_payload_from_html(response.content.decode("utf-8"))
        self.assertIn("form_token", payload)
        self.assertTrue(payload["form_token"])
        self.assertEqual(payload["merge_action"], "invalidate")
        return payload

    def _lease(self) -> CrmDuplicateMergePlanLease:
        return CrmDuplicateMergePlanLease.objects.get(session=self.session)

    def _fake_workflow(self, revisions: dict[str, int]):
        def workflow(run_id, owner_session=None):
            if run_id == "run-review-5a":
                return {
                    "run_id": run_id,
                    "revision": int(revisions["review"]),
                    "status": "succeeded",
                }
            return {
                "run_id": run_id,
                "revision": 2,
                "status": "awaiting_effect_authorization",
                "effect_intent": _intent(),
            }

        return workflow

    def _fake_dispatch(self):
        def dispatch(**kwargs):
            kind = kwargs["mutation_kind"]
            generation = int(kwargs.get("logical_action_generation") or 0)
            mutation = (
                ApiMutation.objects.filter(
                    form_instance=kwargs["form_instance"]
                )
                .order_by("created_at")
                .first()
            )
            if mutation is not None and mutation.state != ApiMutation.State.COMPLETED:
                mutation.state = ApiMutation.State.COMPLETED
                mutation.save(update_fields=["state"])
            if kind == "finalize_duplicate_merge_plan_handoff":
                continuation = (
                    "run-cont-c1" if generation == 0 else "run-cont-c2"
                )
                return _handoff_for(continuation)
            if kind == "invalidate_duplicate_merge_plan_freeze":
                revoked = "run-cont-c1" if generation == 0 else "run-cont-c2"
                return _invalidate_receipt(revoked)
            raise AssertionError(kind)

        return dispatch


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class CrmDuplicateJourneyDrux2ReapproveTests(_MergeJourneyMixin, TestCase):
    def setUp(self):
        self._seed_session()

    def test_approve_go_back_reapprove_uses_new_epoch_mutation(self):
        revisions = {"review": 5}
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._fake_workflow(revisions),
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                side_effect=self._fake_dispatch(),
                            ):
                                token1 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                first = self.client.post(
                                    self._merge_url(),
                                    data={
                                        "form_token": token1,
                                        "merge_action": "finalize",
                                    },
                                )
                                self.assertEqual(first.status_code, 302)
                                lease = self._lease()
                                self.assertEqual(lease.continuation_run_id, "run-cont-c1")
                                self.assertEqual(lease.epoch, 0)
                                first_finalize_id = lease.applied_finalize_mutation_id
                                self.assertTrue(first_finalize_id)

                                invalidate_payload = self._invalidate_payload_from_get()
                                go_back = self.client.post(
                                    self._merge_url(),
                                    data=invalidate_payload,
                                )
                                self.assertEqual(go_back.status_code, 302)
                                self.assertIn(
                                    "/crm-duplicates/review/", go_back["Location"]
                                )
                                lease = self._lease()
                                self.assertEqual(lease.continuation_run_id, "")
                                self.assertEqual(lease.epoch, 1)
                                self.assertEqual(lease.applied_finalize_mutation_id, "")
                                invalidate_id = lease.applied_invalidate_mutation_id
                                self.assertTrue(invalidate_id)

                                revisions["review"] = 6
                                token2 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                second = self.client.post(
                                    self._merge_url(),
                                    data={
                                        "form_token": token2,
                                        "merge_action": "finalize",
                                    },
                                )
        self.assertEqual(second.status_code, 302)
        self.assertNotIn(REUSE_ERROR, second.content.decode("utf-8"))
        lease = self._lease()
        self.assertEqual(lease.continuation_run_id, "run-cont-c2")
        self.assertEqual(lease.epoch, 1)
        self.assertNotEqual(lease.applied_finalize_mutation_id, first_finalize_id)

        mutations = list(
            ApiMutation.objects.filter(
                session=self.session,
                mutation_kind="finalize_duplicate_merge_plan_handoff",
            ).order_by("created_at")
        )
        self.assertEqual(len(mutations), 2)
        self.assertNotEqual(mutations[0].form_instance, mutations[1].form_instance)
        self.assertNotEqual(
            (
                mutations[0].logical_action_identity,
                mutations[0].logical_action_generation,
            ),
            (
                mutations[1].logical_action_identity,
                mutations[1].logical_action_generation,
            ),
        )
        self.assertEqual(mutations[0].logical_action_generation, 0)
        self.assertEqual(mutations[1].logical_action_generation, 1)
        self.assertEqual(mutations[0].state, ApiMutation.State.COMPLETED)
        self.assertEqual(mutations[1].state, ApiMutation.State.COMPLETED)
        self.assertEqual(
            str(lease.applied_finalize_mutation_id), str(mutations[1].id)
        )

    def test_exact_retry_of_second_approve_replays_without_third_epoch(self):
        revisions = {"review": 5}
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._fake_workflow(revisions),
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                side_effect=self._fake_dispatch(),
                            ):
                                token1 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                self.client.post(
                                    self._merge_url(),
                                    {
                                        "form_token": token1,
                                        "merge_action": "finalize",
                                    },
                                )
                                self.client.post(
                                    self._merge_url(),
                                    self._invalidate_payload_from_get(),
                                )
                                revisions["review"] = 6
                                token2 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                payload = {
                                    "form_token": token2,
                                    "merge_action": "finalize",
                                }
                                second = self.client.post(self._merge_url(), payload)
                                retry = self.client.post(self._merge_url(), payload)
        self.assertEqual(second.status_code, 302)
        self.assertEqual(retry.status_code, 302)
        self.assertNotIn(REUSE_ERROR, retry.content.decode("utf-8"))
        lease = self._lease()
        self.assertEqual(lease.epoch, 1)
        self.assertEqual(lease.continuation_run_id, "run-cont-c2")
        self.assertEqual(
            ApiMutation.objects.filter(
                session=self.session,
                mutation_kind="finalize_duplicate_merge_plan_handoff",
            ).count(),
            2,
        )

    def test_stale_invalidate_replay_after_reapprove_keeps_new_continuation(self):
        revisions = {"review": 5}
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._fake_workflow(revisions),
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                side_effect=self._fake_dispatch(),
                            ):
                                token1 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                self.client.post(
                                    self._merge_url(),
                                    {
                                        "form_token": token1,
                                        "merge_action": "finalize",
                                    },
                                )
                                invalidate_payload = self._invalidate_payload_from_get()
                                self.assertEqual(
                                    set(invalidate_payload) - {"csrfmiddlewaretoken"},
                                    {"form_token", "merge_action"},
                                )
                                self.client.post(self._merge_url(), invalidate_payload)
                                revisions["review"] = 6
                                token2 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                self.client.post(
                                    self._merge_url(),
                                    {
                                        "form_token": token2,
                                        "merge_action": "finalize",
                                    },
                                )
                                lease = self._lease()
                                self.assertEqual(lease.continuation_run_id, "run-cont-c2")
                                self.assertEqual(lease.epoch, 1)
                                missing_token = self.client.post(
                                    self._merge_url(),
                                    {"merge_action": "invalidate"},
                                )
                                self.assertNotEqual(missing_token.status_code, 302)
                                lease = self._lease()
                                self.assertEqual(lease.continuation_run_id, "run-cont-c2")
                                self.assertEqual(lease.epoch, 1)
                                stale = self.client.post(
                                    self._merge_url(), invalidate_payload
                                )
        self.assertEqual(stale.status_code, 302)
        lease = self._lease()
        self.assertEqual(lease.continuation_run_id, "run-cont-c2")
        self.assertEqual(lease.epoch, 1)
        self.assertNotEqual(lease.applied_finalize_mutation_id, "")
        invalidate_mutations = ApiMutation.objects.filter(
            session=self.session,
            mutation_kind="invalidate_duplicate_merge_plan_freeze",
        )
        self.assertEqual(invalidate_mutations.count(), 1)

    def test_second_approve_does_not_surface_reuse_error(self):
        revisions = {"review": 5}
        with self._patch_owner():
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        side_effect=self._fake_workflow(revisions),
                    ):
                        with patch(
                            "importer.journey_views.store_workflow_projection"
                        ):
                            with patch(
                                "importer.journey_views._dispatch_json_mutation",
                                side_effect=self._fake_dispatch(),
                            ):
                                token1 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                first = self.client.post(
                                    self._merge_url(),
                                    {
                                        "form_token": token1,
                                        "merge_action": "finalize",
                                    },
                                )
                                self.client.post(
                                    self._merge_url(),
                                    self._invalidate_payload_from_get(),
                                )
                                revisions["review"] = 6
                                token2 = self.client.get(self._merge_url()).context[
                                    "form_token"
                                ]
                                second = self.client.post(
                                    self._merge_url(),
                                    {
                                        "form_token": token2,
                                        "merge_action": "finalize",
                                    },
                                    follow=True,
                                )
        self.assertEqual(first.status_code, 302)
        body = second.content.decode("utf-8")
        self.assertNotIn(REUSE_ERROR, body)
        self.assertEqual(self._lease().continuation_run_id, "run-cont-c2")


@override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
class CrmDuplicateJourneyDrux2ConcurrentInvalidateTests(
    _MergeJourneyMixin, TransactionTestCase
):
    def setUp(self):
        self._seed_session()

    def test_concurrent_double_submit_invalidate_applies_once(self):
        """Two invalidate attempts share one journal row; CAS applies once.

        Dual TestClient POSTs race Django's session store on SQLite and are
        not a valid proof. This harness runs the production journal+CAS
        sequence without a fake dispatcher writing mutation rows.
        """

        CrmDuplicateMergePlanLease.objects.update_or_create(
            session=self.session,
            defaults={
                "epoch": 0,
                "continuation_run_id": "run-cont-c1",
                "decision_set_content_digest": "sha256:x",
                "applied_finalize_mutation_id": "f1-seed",
            },
        )
        raw_root = (self.session.options or {}).get("apply_root_form_instance")
        root = UUID(str(raw_root)) if raw_root else uuid4()
        form_instance = _step_form_instance(root, "invalidate:0")
        identity = _invalidate_merge_identity(
            epoch=0,
            owner_session=f"django-{self.owner}",
            review_run_id="run-review-5a",
        )
        body = {
            "expected_revision": 5,
            "owner_session": f"django-{self.owner}",
        }
        outcomes: list = []

        def worker() -> None:
            close_old_connections()
            try:
                mutation = create_or_reuse_mutation(
                    session=self.session,
                    form_instance=form_instance,
                    mutation_kind="invalidate_duplicate_merge_plan_freeze",
                    route=(
                        "/v1/workflows/run-review-5a/duplicate-merge-plan-invalidate"
                    ),
                    logical_action_identity=identity,
                    logical_action_generation=0,
                    request_json=body,
                    resource_identity="run-review-5a",
                )
                applied = _cas_apply_invalidate_lease(
                    workflow_session=self.session,
                    mutation_id=str(mutation.id),
                    revoked_continuation_run_ids=["run-cont-c1"],
                )
                outcomes.append((str(mutation.id), applied))
            except Exception as exc:  # noqa: BLE001 — both attempts must finish
                outcomes.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=worker), Thread(target=worker)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(outcomes), 2, msg=repr(outcomes))
        self.assertTrue(
            all(isinstance(item, tuple) for item in outcomes),
            msg=repr(outcomes),
        )
        mutation_ids = {item[0] for item in outcomes}
        self.assertEqual(len(mutation_ids), 1)
        self.assertEqual(sum(1 for item in outcomes if item[1] is True), 1)
        lease = self._lease()
        self.assertEqual(lease.epoch, 1)
        self.assertEqual(lease.continuation_run_id, "")
        self.assertEqual(lease.applied_finalize_mutation_id, "")
        self.assertEqual(
            ApiMutation.objects.filter(
                session=self.session,
                mutation_kind="invalidate_duplicate_merge_plan_freeze",
            ).count(),
            1,
        )
