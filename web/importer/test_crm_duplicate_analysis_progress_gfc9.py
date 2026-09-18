"""GFC-9: Django progress copy and public-HTTP incremental-review proofs.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/completed_projects/crm_duplicate_group_pipeline_efficiency.md
Phase GFC-9 / GFC-8.13.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from importer.api_client import ApiRejectedError, EasyImportsApiClient
from importer.journey_views import _analysis_progress_context
from importer.models import ImportSession


class Gfc9ReviewMaterialsProgressCopyTests(SimpleTestCase):
    def test_building_review_materials_uses_ready_prefix_copy(self):
        context = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "building_review_materials",
                    "completed_count": 100,
                    "total_count": 2915,
                    "count_unit": "groups",
                    "review_ready": False,
                    "review_window_ready": True,
                    "review_groups_ready": 100,
                    "review_groups_total": 2915,
                }
            }
        )
        self.assertEqual(context["analysis_progress_stage"], "building_review_materials")
        self.assertEqual(
            context["analysis_progress_copy"],
            "Preparing groups for review — 100 of 2,915 groups",
        )
        self.assertIs(context["analysis_progress_review_ready"], False)
        self.assertIs(context["analysis_progress_review_window_ready"], True)

    def test_unknown_stage_still_falls_through_empty(self):
        context = _analysis_progress_context(
            {
                "duplicate_analysis_progress": {
                    "stage": "not_a_stage",
                    "completed_count": 0,
                    "total_count": 1,
                    "count_unit": "stage",
                    "review_ready": False,
                }
            }
        )
        self.assertIsNone(context["analysis_progress_copy"])


def _member(record_id: str, *, recommended: bool = False) -> dict:
    return {
        "record_id": record_id,
        "display_fields": {"Name": f"Name {record_id}", "Domain": f"{record_id}.example"},
        "recommended": recommended,
        "selected": recommended,
        "survivor_eligible": True,
        "ranking_evidence": {"score": 10 if recommended else 5},
    }


def _first_window() -> dict:
    groups = []
    for index in range(5):
        left = f"L{index}"
        right = f"R{index}"
        groups.append(
            {
                "group_id": f"g{index}",
                "group_revision": f"rev-g{index}",
                "entity_family": "company",
                "group_status": "ready",
                "review_lane": "standard",
                "confidence_band": "high",
                "confidence_score": 92,
                "advanced_review_required": False,
                "execution_blockers": [],
                "allowed_actions": ["approve", "override_survivor", "decline", "quarantine"],
                "recommended_survivor_id": left,
                "selected_survivor_id": left,
                "members": [_member(left, recommended=True), _member(right)],
                "conflicts": [],
                "evidence": [{"kind": "name_match"}],
            }
        )
    return {
        "review_contract": "easyimports.crm.duplicate_review_window.v1",
        "window_id": "win-1",
        "window_digest": "sha256:window-1",
        "expected_revision": 3,
        "group_start": 1,
        "group_end": 5,
        "page_size": 5,
        "total_group_count": 12,
        "decided_group_count": 0,
        "remaining_group_count": 12,
        "groups": groups,
        "outcome": "next_window",
    }


class Gfc9IncrementalReviewHttpTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": "run-gfc9-source",
                "review_run_id": "run-gfc9-review",
                "redesign_phase": "4a",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        django_session = self.client.session
        django_session["easyimports_owner_id"] = str(self.owner)
        django_session.save()

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

    def _progress_url(self):
        return reverse(
            "importer:crm_duplicate_journey_progress",
            kwargs={"session_id": self.session.id},
        )

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_operator_opens_first_window_before_complete(self):
        window = _first_window()
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    return_value=window,
                ) as review_get:
                    response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        review_get.assert_called_once()
        body = response.content.decode("utf-8")
        self.assertIn("Review duplicate groups 1–5 of 12", body)
        self.assertIn('class="review-group-card"', body)
        self.assertEqual(body.count('class="review-group-card"'), 5)
        self.assertNotIn("frontier_wait", body)
        self.assertNotIn(self._progress_url(), response.get("Location", ""))

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_frontier_wait_stays_on_review_without_tight_poll(self):
        not_ready = ApiRejectedError(
            "duplicate_review_window_not_ready",
            "The next review window is not ready yet.",
            http_status=409,
        )
        source_projection = {
            "duplicate_analysis_progress": {
                "stage": "building_review_materials",
                "completed_count": 5,
                "total_count": 12,
                "count_unit": "groups",
                "review_ready": False,
                "review_window_ready": True,
                "review_groups_ready": 5,
                "review_groups_total": 12,
            }
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=not_ready,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=source_projection,
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.get("Location"))
        body = response.content.decode("utf-8")
        self.assertIn("Preparing the next groups to review — 5 of 12 ready", body)
        self.assertIn(
            "Still working. Refresh this page to check again. GET does not start more work.",
            body,
        )
        self.assertIn("var frontierWait = true;", body)
        self.assertIn('data-frontier-wait', body)
        self.assertIn('data-review-wait-refresh', body)
        self.assertNotIn("This page refreshes automatically.", body)
        self.assertNotIn("window.location.reload();", body)
        self.assertNotIn("2500", body)
        self.assertNotIn('class="review-group-card"', body)
        self.assertIsNone(response.get("Location"))
        self.assertNotEqual(response.status_code, 302)

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_frontier_wait_surfaces_failed_source(self):
        not_ready = ApiRejectedError(
            "duplicate_review_window_not_ready",
            "The next review window is not ready yet.",
            http_status=409,
        )
        source_projection = {
            "status": "failed",
            "stage": "building_review_materials",
            "error": {"code": "workflow_failed", "message": "producer died"},
            "duplicate_analysis_progress": {
                "stage": "building_review_materials",
                "completed_count": 5,
                "total_count": 12,
                "count_unit": "groups",
                "review_ready": False,
                "review_window_ready": True,
                "review_groups_ready": 5,
                "review_groups_total": 12,
            },
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=not_ready,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=source_projection,
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._progress_url())

    @override_settings(EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9")
    def test_frontier_wait_surfaces_paused_unknown_source(self):
        not_ready = ApiRejectedError(
            "duplicate_review_window_not_ready",
            "The next review window is not ready yet.",
            http_status=409,
        )
        source_projection = {
            "status": "paused_unknown",
            "stage": "paused_unknown",
            "duplicate_analysis_progress": {
                "stage": "building_review_materials",
                "completed_count": 5,
                "total_count": 12,
                "count_unit": "groups",
                "review_ready": False,
                "review_window_ready": True,
                "review_groups_ready": 5,
                "review_groups_total": 12,
            },
        }
        with self._patch_owner():
            with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_review_window",
                    side_effect=not_ready,
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "workflow",
                        return_value=source_projection,
                    ):
                        response = self.client.get(self._review_url())
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], self._progress_url())


class Gfc9DjangoOperatorPublicHttpTests(TestCase):
    """Django GET+POST of the first incomplete window via live public HTTP.

    The test module does not import mappings_2. A helper seeds the API state
    root; a separate API process serves it.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.state_dir = tempfile.TemporaryDirectory()
        root = Path(cls.state_dir.name)
        cls.api_state = (root / "api_state").resolve()
        cls.api_state.mkdir(parents=True, exist_ok=True)
        seed_out = root / "seed.json"
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.api_port = sock.getsockname()[1]
        sock.close()
        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        seed = subprocess.run(
            [
                sys.executable,
                str(cls.repo / "web" / "tools" / "seed_gfc9_incremental_review.py"),
                "--state-root",
                str(cls.api_state),
                "--output",
                str(seed_out),
            ],
            cwd=str(cls.repo),
            env={**os.environ, "PYTHONPATH": str(cls.repo)},
            text=True,
            capture_output=True,
        )
        if seed.returncode != 0:
            raise RuntimeError(
                "GFC-9 seed failed:\n"
                f"stdout={seed.stdout}\nstderr={seed.stderr}"
            )
        cls.seed = json.loads(seed_out.read_text(encoding="utf-8"))
        cls.api_process = subprocess.Popen(
            [
                sys.executable,
                "-c",
                (
                    "from mappings_2.api.app import create_app; "
                    "import uvicorn; "
                    f"uvicorn.run(create_app(state_root=r'{cls.api_state}'), "
                    f"host='127.0.0.1', port={cls.api_port}, log_level='warning')"
                ),
            ],
            cwd=str(cls.repo),
            env={**os.environ, "PYTHONPATH": str(cls.repo)},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        health = f"{cls.api_base}/health"
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("GFC-9 API process did not start.")

    @classmethod
    def tearDownClass(cls):
        process = getattr(cls, "api_process", None)
        if process is not None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        state_dir = getattr(cls, "state_dir", None)
        if state_dir is not None:
            state_dir.cleanup()
        super().tearDownClass()

    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            options={
                "run_id": self.seed["source_run_id"],
                "review_run_id": self.seed["review_run_id"],
                "redesign_phase": "4a",
                "orchestrator_form_instance": str(uuid4()),
                "mutation_journal_id": "",
            },
        )
        self.session.options = {
            **dict(self.session.options or {}),
            "mutation_journal_id": str(self.session.id),
        }
        self.session.save(update_fields=["options", "updated_at"])
        django_session = self.client.session
        django_session["easyimports_owner_id"] = str(self.owner)
        django_session.save()

    def test_operator_opens_and_submits_first_incomplete_window(self):
        self.assertFalse(self.seed["review_ready"])
        self.assertTrue(self.seed["review_window_ready"])
        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        ):
            with self.settings(EASYIMPORTS_API_BASE_URL=self.api_base):
                get_resp = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_review",
                        kwargs={"session_id": self.session.id},
                    )
                )
                self.assertEqual(get_resp.status_code, 200)
                body = get_resp.content.decode("utf-8")
                self.assertIn('class="review-group-card"', body)
                self.assertEqual(body.count('class="review-group-card"'), 5)
                self.assertNotIn("var frontierWait = true;", body)
                window = get_resp.context["window"]
                cards = get_resp.context["cards"]
                self.assertEqual(len(cards), 5)
                post_data = {
                    "form_token": get_resp.context["form_token"],
                    "window_id": window["window_id"],
                    "window_digest": window["window_digest"],
                    "expected_revision": str(window["expected_revision"]),
                    "group_order": get_resp.context["group_order"],
                }
                for card in cards:
                    gid = card["group_id"]
                    post_data[f"action_{gid}"] = "approve"
                    post_data[f"survivor_{gid}"] = card["recommended_survivor_id"]
                    post_data[f"group_revision_{gid}"] = card["group_revision"]
                    post_data[f"allowed_{gid}"] = ",".join(card["allowed_actions"])
                    post_data[f"recommended_{gid}"] = card["recommended_survivor_id"]
                    post_data[f"eligible_{gid}"] = ",".join(
                        member["record_id"] for member in card["members"]
                    )
                    post_data[f"advanced_{gid}"] = (
                        "1" if card.get("advanced_review_required") else "0"
                    )
                post_resp = self.client.post(
                    reverse(
                        "importer:crm_duplicate_journey_review",
                        kwargs={"session_id": self.session.id},
                    ),
                    data=post_data,
                )
        self.assertEqual(post_resp.status_code, 302)
        self.assertIn("/crm-duplicates/review/", post_resp["Location"])
        source = requests.get(
            f"{self.api_base}/v1/workflows/{self.seed['source_run_id']}",
            headers={"X-Owner-Session": f"django-{self.owner}"},
            timeout=5,
        )
        self.assertEqual(source.status_code, 200, source.text)
        progress = source.json().get("duplicate_analysis_progress") or {}
        self.assertIs(progress.get("review_ready"), False)
        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        ):
            with self.settings(EASYIMPORTS_API_BASE_URL=self.api_base):
                after = self.client.get(
                    reverse(
                        "importer:crm_duplicate_journey_review",
                        kwargs={"session_id": self.session.id},
                    )
                )
        self.assertEqual(after.status_code, 200)
        after_body = after.content.decode("utf-8")
        self.assertIn("var frontierWait = true;", after_body)
        self.assertIn("Preparing the next groups to review", after_body)
