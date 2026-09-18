"""X1 operator-visible: Django dispatch of a second authorize mutation with a
frozen stale expected_revision must hit the real authorize path and must not
render revision_conflict.

The identity match lives in mappings_2. Tests may import mappings_2; production
Django still does not.

Authority:
mappings_2/codex_context/cross_agent_eval/project_implementations/crm_duplicate_execute_retry_and_terminal_ux_reliability.md
Phase X1.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlparse
from uuid import uuid4

import requests
from django.test import TestCase, override_settings
from django.urls import reverse
from fastapi.testclient import TestClient

_REPO = Path(__file__).resolve().parents[2]
_MAP_TESTS = _REPO / "tests" / "mappings_2"
for _path in (_REPO, _MAP_TESTS):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from column_mapping_map3_support import (  # noqa: E402
    bind_for_upload,
    owner_headers,
    with_column_mapping,
)
from mappings_2.api.app import create_app  # noqa: E402

from importer.api_client import (  # noqa: E402
    EasyImportsApiClient,
    canonical_digest,
    create_or_reuse_mutation,
)
from importer.models import ApiMutation, ApiWorkflow, ImportSession  # noqa: E402


class _FastAPIAdapter(requests.adapters.BaseAdapter):
    """Forward requests.Session calls to an in-process FastAPI TestClient.

    Authorize dispatch normally sends Prefer: respond-async. This adapter
    drops that header so the TestClient returns the command result on the
    same call (200 or 409). The identity match still runs.
    """

    def __init__(self, client: TestClient) -> None:
        super().__init__()
        self._client = client

    def close(self) -> None:
        return None

    def send(
        self,
        request,
        stream=False,
        timeout=None,
        verify=True,
        cert=None,
        proxies=None,
    ):
        parsed = urlparse(request.url)
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() not in {"host", "content-length", "content-type"}
            and not (
                key.lower() == "prefer"
                and "respond-async" in str(value).lower()
            )
        }
        method = request.method.lower()
        body = request.body
        json_body = None
        content_type = str(request.headers.get("Content-Type") or "")
        if body and "application/json" in content_type:
            if isinstance(body, bytes):
                json_body = json.loads(body.decode("utf-8"))
            else:
                json_body = json.loads(body)
        caller = getattr(self._client, method)
        if json_body is not None:
            upstream = caller(path, headers=headers, json=json_body)
        elif body:
            upstream = caller(path, headers=headers, content=body)
        else:
            upstream = caller(path, headers=headers)
        response = requests.Response()
        response.status_code = upstream.status_code
        response._content = upstream.content
        response.headers.update(upstream.headers)
        response.url = request.url
        response.request = request
        response.reason = getattr(upstream, "reason_phrase", "") or ""
        return response


@override_settings(EASYIMPORTS_API_BASE_URL="http://x1-api.test")
class ExecuteRetryX1OperatorVisibleTests(TestCase):
    """Django journals a real second-key authorize against mappings_2."""

    def setUp(self):
        self.owner = uuid4()
        self.owner_session = f"django-{self.owner}"
        browser_session = self.client.session
        browser_session["easyimports_owner_id"] = str(self.owner)
        browser_session.save()
        self._tmpdir = tempfile.TemporaryDirectory(prefix="x1_django_")
        self._api = TestClient(create_app(state_root=Path(self._tmpdir.name)))
        self._api.__enter__()
        self._http = requests.Session()
        self._http.mount("http://x1-api.test/", _FastAPIAdapter(self._api))

    def tearDown(self):
        self._api.__exit__(None, None, None)
        self._http.close()
        self._tmpdir.cleanup()

    def _headers(self, key: str) -> dict[str, str]:
        return owner_headers(self.owner_session, key=key)

    def _drive_to_authorized_run(self) -> tuple[dict, dict, dict]:
        uploaded = self._api.post(
            "/v1/uploads",
            files={
                "file": (
                    "accounts.csv",
                    b"Account Name,Website\nAcme,https://acme.example\n",
                    "text/csv",
                )
            },
            headers=self._headers("x1-op-upload"),
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        upload = uploaded.json()
        bind = bind_for_upload(
            self._api,
            upload,
            owner=self.owner_session,
            key_prefix="x1-op-map",
        )
        created = self._api.post(
            "/v1/workflows",
            json=with_column_mapping(
                {
                    "product_key": "easyimports.single_dataset_import",
                    "target_provider_id": "fake-preview-v1",
                    "uploads": {"dataset": upload["upload_id"]},
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                    "target_object": "account",
                },
                bind,
            ),
            headers=self._headers("x1-op-create"),
        )
        self.assertEqual(created.status_code, 201, created.text)
        waiting = self._api.get(
            created.json()["resource"],
            headers=self._headers("x1-op-get-waiting"),
        )
        self.assertEqual(waiting.status_code, 200, waiting.text)
        workflow = waiting.json()
        intent = workflow["effect_intent"]
        frozen = {
            "expected_revision": workflow["revision"],
            "intent_id": intent["intent_id"],
            "track": intent["track"],
            "maximum_mode": intent["maximum_mode"],
            "selected_mode": "preview",
            "target_provider_id": intent["target_provider_id"],
            "target_fingerprint": intent["target_fingerprint"],
            "work_digest": intent["work_digest"],
            "confirmation": intent["confirmation"],
        }
        first = self._api.post(
            f"/v1/workflows/{workflow['run_id']}/effect-authorizations",
            json=frozen,
            headers=self._headers("x1-op-first-authorize"),
        )
        self.assertEqual(first.status_code, 200, first.text)
        current = self._api.get(
            created.json()["resource"],
            headers=self._headers("x1-op-get-authorized"),
        )
        self.assertEqual(current.status_code, 200, current.text)
        advanced = current.json()
        self.assertNotEqual(advanced["revision"], frozen["expected_revision"])
        return workflow, frozen, advanced

    def _session_for(self, projection: dict) -> tuple[ImportSession, ApiWorkflow]:
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key=projection["workflow_key"],
            target_provider_id=projection.get("target_provider_id") or "fake-preview-v1",
            operator_label="X1 Operator",
        )
        workflow = ApiWorkflow.objects.create(
            session=session,
            role=ApiWorkflow.Role.PRIMARY,
            run_id=projection["run_id"],
            workflow_key=projection["workflow_key"],
            workflow_version=projection["workflow_version"],
            target_provider_id=projection.get("target_provider_id") or "fake-preview-v1",
            status=projection["status"],
            stage=projection["stage"],
            revision=projection["revision"],
            resource_url=f"/v1/workflows/{projection['run_id']}",
            projection=projection,
            projection_digest=canonical_digest(projection),
        )
        session.active_workflow = workflow
        session.save(update_fields=["active_workflow"])
        return session, workflow

    def test_second_authorize_mutation_with_frozen_stale_revision_does_not_show_revision_conflict(
        self,
    ):
        waiting, frozen, advanced = self._drive_to_authorized_run()
        session, workflow = self._session_for(advanced)
        first_mutation = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route=f"/v1/workflows/{waiting['run_id']}/effect-authorizations",
            logical_action_identity="x1-op:first",
            request_json=dict(frozen),
            resource_identity=waiting["run_id"],
        )
        first_mutation.state = ApiMutation.State.COMPLETED
        first_mutation.http_status = 200
        first_mutation.error_code = ""
        first_mutation.save(update_fields=["state", "http_status", "error_code"])

        second = create_or_reuse_mutation(
            session=session,
            workflow=workflow,
            form_instance=uuid4(),
            mutation_kind="authorize_effect",
            route=first_mutation.route,
            logical_action_identity="x1-op:second",
            request_json=dict(frozen),
            resource_identity=waiting["run_id"],
        )
        self.assertNotEqual(second.idempotency_key, first_mutation.idempotency_key)
        self.assertEqual(second.request_json["expected_revision"], frozen["expected_revision"])

        def _session() -> requests.Session:
            return self._http

        with patch("importer.api_client.requests.Session", _session):
            dispatched = EasyImportsApiClient().dispatch(second, explicit_retry=True)

        self.assertEqual(dispatched.mutation.state, ApiMutation.State.COMPLETED)
        self.assertEqual(dispatched.mutation.http_status, 200)
        self.assertNotEqual(dispatched.mutation.error_code, "revision_conflict")

        with patch("importer.workflow_views.refresh_workflow", return_value=workflow):
            terminal = self.client.get(reverse("importer:workflow", args=[session.id]))
        self.assertEqual(terminal.status_code, 200)
        self.assertNotContains(terminal, "revision_conflict")
        self.assertNotContains(terminal, f"expected {frozen['expected_revision']}")
        keys = list(
            session.api_mutations.filter(mutation_kind="authorize_effect").values_list(
                "idempotency_key", flat=True
            )
        )
        self.assertEqual(len(set(keys)), 2)
        for mutation in session.api_mutations.filter(mutation_kind="authorize_effect"):
            self.assertEqual(mutation.state, ApiMutation.State.COMPLETED)
            self.assertNotEqual(mutation.error_code, "revision_conflict")
            self.assertNotEqual(mutation.http_status, 409)
