"""MAP-R5: linear happy path — journey gates, progress, Accounts hygiene.

Network-free unit/Django tests plus dual-process browser → Django → API accept
on isolated fake-only state. Does not authorize live CRM.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch
from uuid import uuid4

import requests
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse

from mappings_2.application.exports.profiles import get_export_profile

from importer.api_client import EasyImportsApiClient
from importer.forms import WorkflowConfigurationForm
from importer.models import ApiMutation, ImportSession, SourceFile
from importer.setup_service import (
    SetupValidationError,
    column_mapping_gate_state,
    confirmed_column_mapping_bind,
    product_requires_column_mapping,
    session_has_confirmed_column_mapping,
    session_requires_column_mapping,
)
from importer.workflow_state import OWNER_SESSION_KEY, issue_form_token

ACCOUNT_CATALOG = "easyimports.account_fields.v1"
CONTACT_CATALOG = "easyimports.contact_fields.v1"


def _digests() -> dict[str, str]:
    return {
        "plan_content_digest": "a" * 64,
        "source_schema_digest": "b" * 64,
        "destination_digest": "c" * 64,
        "target_contract_digest": "d" * 64,
    }


def _confirmed_map_options(
    *,
    plan_id: str = "cmp_r5_test",
    upload_id: str = "upload-r5-1",
    catalog_id: str = ACCOUNT_CATALOG,
    headers: list[str] | None = None,
) -> dict:
    return {
        "column_mapping_map2": {
            "plan_id": plan_id,
            "status": "confirmed",
            "confirmed_digests": _digests(),
            "upload_id": upload_id,
            "destination": {
                "destination_mode": "catalog",
                "catalog_id": catalog_id,
            },
            "source_headers": list(
                headers or ["account_name", "website", "domain", "industry"]
            ),
        }
    }


class ColumnMappingMapR5UnitTests(SimpleTestCase):
    """Gate helpers without database I/O beyond model instances."""

    def test_product_requires_mapping_for_bind_products(self):
        self.assertTrue(
            product_requires_column_mapping("easyimports.single_dataset_import")
        )
        self.assertTrue(product_requires_column_mapping("easyimports.list_import"))
        self.assertTrue(
            product_requires_column_mapping("easyimports.account_list_import")
        )
        self.assertFalse(
            product_requires_column_mapping("easyimports.crm_duplicate_resolution")
        )
        self.assertFalse(product_requires_column_mapping(None))

    def test_gate_states_without_binding_are_not_confirmed(self):
        session = ImportSession(
            owner_id=uuid4(),
            setup_entity="accounts",
            setup_operation="clean_only",
            setup_reference_source="none",
            options={},
        )
        self.assertTrue(session_requires_column_mapping(session))
        self.assertEqual(column_mapping_gate_state(session), "missing")
        self.assertFalse(session_has_confirmed_column_mapping(session))

        session.options = {
            "column_mapping_map2": {"plan_id": "cmp_x", "status": "draft"}
        }
        self.assertEqual(column_mapping_gate_state(session), "draft")

        # Digests alone → stale (not bind-ready).
        session.options = {
            "column_mapping_map2": {
                "plan_id": "cmp_r5_test",
                "status": "confirmed",
                "confirmed_digests": _digests(),
            }
        }
        self.assertEqual(column_mapping_gate_state(session), "stale")
        self.assertFalse(session_has_confirmed_column_mapping(session))

    def test_accounts_configure_hides_type_of_people(self):
        product_entry = {
            "product_key": "easyimports.single_dataset_import",
            "tracks": {
                "dataset_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        }
        target = {
            "target_provider_id": "fake-preview-v1",
            "maximum_modes": {
                "dataset_writes": "disabled",
                "delivery": "preview",
            },
        }
        form = WorkflowConfigurationForm(
            product_entry=product_entry,
            target=target,
            uploaded_roles={"dataset"},
            route_defaults={
                "target_object": "account",
                "content_type": "accounts",
                "canon_profile": "accounts",
                "person_kind": None,
            },
        )
        self.assertNotIn("person_kind", form.fields)
        labels = [f.label for f in form.visible_fields()]
        self.assertNotIn("Type of people", labels)

    def test_people_clean_only_keeps_locked_person_kind(self):
        product_entry = {
            "product_key": "easyimports.single_dataset_import",
            "tracks": {
                "dataset_writes": ["disabled"],
                "delivery": ["disabled", "preview"],
            },
        }
        target = {
            "target_provider_id": "fake-preview-v1",
            "maximum_modes": {
                "dataset_writes": "disabled",
                "delivery": "preview",
            },
        }
        form = WorkflowConfigurationForm(
            product_entry=product_entry,
            target=target,
            uploaded_roles={"dataset"},
            route_defaults={
                "target_object": "contact",
                "content_type": "people_and_accounts",
                "canon_profile": "new_list",
                "person_kind": "contact",
            },
        )
        self.assertIn("person_kind", form.fields)
        self.assertTrue(form.fields["person_kind"].disabled)


class ColumnMappingMapR5DjangoTests(TestCase):
    """Django request path for upload primary CTA, configure gate, and staleness."""

    def setUp(self):
        self.owner = uuid4()
        browser = self.client.session
        browser[OWNER_SESSION_KEY] = str(self.owner)
        browser.save()

    def _session_with_upload(
        self,
        *,
        with_confirmed_map: bool = False,
        entity: str = "accounts",
        operation: str = "clean_only",
        people_output: str = "",
        headers: list[str] | None = None,
        catalog_id: str = ACCOUNT_CATALOG,
        upload_id: str = "upload-r5-1",
    ) -> ImportSession:
        headers = list(headers or ["account_name", "website", "domain", "industry"])
        options = (
            _confirmed_map_options(
                upload_id=upload_id,
                catalog_id=catalog_id,
                headers=headers,
            )
            if with_confirmed_map
            else {}
        )
        session = ImportSession.objects.create(
            owner_id=self.owner,
            product_key="",
            setup_entity=entity,
            setup_operation=operation,
            setup_reference_source="none",
            setup_people_output=people_output,
            setup_revision=1,
            target_provider_id="fake-preview-v1",
            operator_label="MAP-R5 Operator",
            options=options,
        )
        mutation = ApiMutation.objects.create(
            session=session,
            idempotency_key=f"test-upload-r5-{uuid4()}",
            mutation_kind="register_upload",
            route="/v1/uploads",
            request_digest="request-digest",
            state=ApiMutation.State.COMPLETED,
            result_upload_id=upload_id,
        )
        SourceFile.objects.create(
            session=session,
            role="dataset",
            original_name="accounts.csv",
            stored_path="accounts.csv",
            api_upload_id=upload_id,
            columns=headers,
            upload_mutation=mutation,
        )
        return session

    def _products_targets(self):
        products = {
            "products": [
                {
                    "product_key": "easyimports.single_dataset_import",
                    "tracks": {
                        "dataset_writes": ["disabled"],
                        "delivery": ["disabled", "preview"],
                    },
                }
            ]
        }
        targets = {
            "targets": [
                {
                    "target_provider_id": "fake-preview-v1",
                    "maximum_modes": {
                        "dataset_writes": "disabled",
                        "delivery": "preview",
                    },
                }
            ]
        }
        return products, targets

    def test_upload_primary_cta_is_map_columns_when_mapping_required(self):
        session = self._session_with_upload(with_confirmed_map=False)
        page = self.client.get(reverse("importer:upload", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        body = page.content.decode("utf-8")
        self.assertRegex(
            body,
            r'class="btn btn-primary"[^>]*href="[^"]*column-mapping',
        )
        self.assertIn("Map columns", body)
        self.assertIn("Configure", body)
        self.assertIn("Review &amp; download", body)

    def test_configure_blocks_start_import_without_confirmed_mapping(self):
        session = self._session_with_upload(with_confirmed_map=False)
        products, targets = self._products_targets()
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"mapping-required-gate", customer)
        self.assertIn(b"Map columns", customer)
        self.assertNotIn(b'id="configure-form"', customer)
        self.assertNotIn(b">Start import<", customer)

        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="create_workflow",
            logical_action_identity=f"session:{session.id}:create_workflow",
            logical_action_generation=0,
        )
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            posted = self.client.post(
                reverse("importer:configure", args=[session.id]),
                {
                    "form_token": token,
                    "setup_revision": 1,
                    "mode__dataset_writes": "disabled",
                    "mode__delivery": "preview",
                    "list_duplicate_policy": "drop_repeats",
                },
            )
        self.assertEqual(posted.status_code, 302)
        self.assertEqual(posted.url, reverse("importer:configure", args=[session.id]))
        session.refresh_from_db()
        self.assertIsNone(session.active_workflow_id)
        self.assertFalse(
            session.api_mutations.filter(mutation_kind="create_workflow").exists()
        )

    def test_configure_shows_start_import_when_mapping_confirmed_and_current(self):
        session = self._session_with_upload(with_confirmed_map=True)
        self.assertEqual(column_mapping_gate_state(session), "confirmed")
        products, targets = self._products_targets()
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        self.assertEqual(page.status_code, 200)
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"Start import", customer)
        self.assertNotIn(b"mapping-required-gate", customer)
        self.assertNotIn(b"Type of people", customer)

    def test_stale_after_setup_entity_change_blocks_start_import(self):
        """Account confirmed map must not stay confirmed after People setup."""

        session = self._session_with_upload(with_confirmed_map=True)
        self.assertEqual(column_mapping_gate_state(session), "confirmed")
        bind = confirmed_column_mapping_bind(session)
        self.assertEqual(bind["plan_id"], "cmp_r5_test")

        # Operator changes setup intent; keeps the Account mapping draft.
        session.setup_entity = "people"
        session.setup_operation = "clean_only"
        session.setup_people_output = "contact"
        session.setup_reference_source = "none"
        session.save(
            update_fields=[
                "setup_entity",
                "setup_operation",
                "setup_people_output",
                "setup_reference_source",
                "updated_at",
            ]
        )
        session.refresh_from_db()
        self.assertEqual(column_mapping_gate_state(session), "stale")
        self.assertFalse(session_has_confirmed_column_mapping(session))
        with self.assertRaises(SetupValidationError) as ctx:
            confirmed_column_mapping_bind(session)
        self.assertIn("no longer matches", str(ctx.exception).lower())

        products, targets = self._products_targets()
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        customer = page.content.split(
            b'<details class="card run-details technical-details">'
        )[0]
        self.assertIn(b"mapping-required-gate", customer)
        self.assertIn(b"out of date", customer)
        self.assertNotIn(b">Start import<", customer)
        self.assertIn(b"Map columns", customer)

        token = issue_form_token(
            owner_id=self.owner,
            session=session,
            action_kind="create_workflow",
            logical_action_identity=f"session:{session.id}:create_workflow",
            logical_action_generation=0,
        )
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            posted = self.client.post(
                reverse("importer:configure", args=[session.id]),
                {
                    "form_token": token,
                    "setup_revision": 1,
                    "mode__dataset_writes": "disabled",
                    "mode__delivery": "preview",
                    "list_duplicate_policy": "drop_repeats",
                },
            )
        self.assertEqual(posted.status_code, 302)
        session.refresh_from_db()
        self.assertFalse(
            session.api_mutations.filter(mutation_kind="create_workflow").exists()
        )

    def test_stale_after_upload_id_change_blocks_start_import(self):
        session = self._session_with_upload(with_confirmed_map=True)
        self.assertEqual(column_mapping_gate_state(session), "confirmed")
        source = session.files.first()
        assert source is not None
        source.api_upload_id = "upload-replaced"
        source.save(update_fields=["api_upload_id"])
        session.refresh_from_db()
        self.assertEqual(column_mapping_gate_state(session), "stale")
        products, targets = self._products_targets()
        with (
            patch.object(EasyImportsApiClient, "products", return_value=products),
            patch.object(EasyImportsApiClient, "targets", return_value=targets),
            patch.object(
                EasyImportsApiClient,
                "crm_connections",
                return_value={"connections": []},
            ),
        ):
            page = self.client.get(reverse("importer:configure", args=[session.id]))
        self.assertIn(b"mapping-required-gate", page.content)
        self.assertNotIn(b">Start import<", page.content)


class ColumnMappingMapR5BrowserAcceptTests(TransactionTestCase):
    """MAP-R5 dual-process browser accept (full original acceptance criteria)."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.repo = Path(__file__).resolve().parents[2]
        cls.test_dir = tempfile.TemporaryDirectory()
        root = Path(cls.test_dir.name)
        cls.api_state = root / "api_state"
        cls.django_db = root / "django.sqlite3"
        cls.api_state.mkdir(parents=True, exist_ok=True)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.api_port = sock.getsockname()[1]
        sock.close()
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        cls.django_port = sock.getsockname()[1]
        sock.close()

        cls.api_base = f"http://127.0.0.1:{cls.api_port}"
        cls.django_base = f"http://127.0.0.1:{cls.django_port}"
        cls._start_api()
        cls._start_django()

    @classmethod
    def _api_env(cls) -> dict:
        environment = os.environ.copy()
        environment["EASYIMPORTS_API_PORT"] = str(cls.api_port)
        environment["EASYIMPORTS_API_STATE_ROOT"] = str(cls.api_state)
        environment["EASYIMPORTS_SECRET_STORE"] = "file_insecure"
        environment["EASYIMPORTS_SF_OAUTH_EXCHANGE"] = "synthetic"
        environment["PYTHONPATH"] = str(cls.repo)
        return environment

    @classmethod
    def _django_env(cls) -> dict:
        environment = os.environ.copy()
        environment["DJANGO_SETTINGS_MODULE"] = "easyimports_web.settings"
        environment["EASYIMPORTS_API_BASE_URL"] = cls.api_base
        environment["EASYIMPORTS_DB_PATH"] = str(cls.django_db)
        environment["EASYIMPORTS_DEBUG"] = "1"
        environment["EASYIMPORTS_ALLOWED_HOSTS"] = "127.0.0.1,localhost,testserver"
        environment["PYTHONPATH"] = os.pathsep.join(
            [str(cls.repo / "web"), str(cls.repo), environment.get("PYTHONPATH", "")]
        )
        return environment

    @classmethod
    def _start_api(cls) -> None:
        cls.api_log = cls.api_state.parent / "api.log"
        cls.api_log_handle = open(cls.api_log, "wb")
        cls.api_process = subprocess.Popen(
            [sys.executable, "-m", "mappings_2.api.app"],
            cwd=str(cls.repo),
            env=cls._api_env(),
            stdout=cls.api_log_handle,
            stderr=subprocess.STDOUT,
        )
        health = f"{cls.api_base}/health"
        for _ in range(80):
            try:
                if requests.get(health, timeout=0.25).status_code == 200:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.api_process.terminate()
        raise RuntimeError("MAP-R5 API process did not start.")

    @classmethod
    def _stop_api(cls) -> None:
        if getattr(cls, "api_process", None) is None:
            return
        cls.api_process.terminate()
        try:
            cls.api_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.api_process.kill()
            cls.api_process.wait(timeout=5)
        cls.api_process = None
        handle = getattr(cls, "api_log_handle", None)
        if handle is not None:
            handle.close()
            cls.api_log_handle = None

    @classmethod
    def _start_django(cls) -> None:
        env = cls._django_env()
        migrate = subprocess.run(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "migrate",
                "--run-syncdb",
                "--verbosity",
                "0",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            text=True,
            capture_output=True,
        )
        if migrate.returncode != 0:
            raise RuntimeError(
                "MAP-R5 Django migrate failed:\n"
                f"stdout={migrate.stdout}\nstderr={migrate.stderr}"
            )
        cls.django_process = subprocess.Popen(
            [
                sys.executable,
                str(cls.repo / "web" / "manage.py"),
                "runserver",
                f"127.0.0.1:{cls.django_port}",
                "--noreload",
            ],
            cwd=str(cls.repo / "web"),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        landing = f"{cls.django_base}/"
        for _ in range(80):
            try:
                response = requests.get(landing, timeout=0.25)
                if response.status_code in {200, 302}:
                    return
            except requests.RequestException:
                time.sleep(0.1)
        cls.django_process.terminate()
        raise RuntimeError("MAP-R5 Django process did not start.")

    @classmethod
    def _stop_django(cls) -> None:
        if getattr(cls, "django_process", None) is None:
            return
        cls.django_process.terminate()
        try:
            cls.django_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            cls.django_process.kill()
            cls.django_process.wait(timeout=5)
        cls.django_process = None

    @classmethod
    def tearDownClass(cls):
        cls._stop_django()
        cls._stop_api()
        cls.test_dir.cleanup()
        super().tearDownClass()

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = "/" + path
        return f"{self.django_base}{path}"

    def _browser(self) -> requests.Session:
        return requests.Session()

    def _get(self, browser: requests.Session, path: str):
        return browser.get(self._url(path), timeout=60, allow_redirects=True)

    @staticmethod
    def _extract_csrf(html: str) -> str:
        match = re.search(
            r'name=["\']csrfmiddlewaretoken["\']\s+value=["\']([^"\']+)',
            html,
        )
        if match is None:
            match = re.search(
                r'value=["\']([^"\']+)["\']\s+name=["\']csrfmiddlewaretoken["\']',
                html,
            )
        assert match is not None, "csrf token missing"
        return match.group(1)

    @staticmethod
    def _extract_form_token(html: str) -> str:
        match = re.search(
            r'name=["\']form_token["\']\s+value=["\']([^"\']+)',
            html,
        )
        if match is None:
            match = re.search(
                r'value=["\']([^"\']+)["\']\s+name=["\']form_token["\']',
                html,
            )
        assert match is not None, "form_token missing"
        return match.group(1)

    @staticmethod
    def _extract_hidden(html: str, name: str) -> str:
        match = re.search(
            rf'name=["\']{re.escape(name)}["\']\s+value=["\']([^"\']*)',
            html,
        )
        if match is None:
            match = re.search(
                rf'value=["\']([^"\']*)["\']\s+name=["\']{re.escape(name)}["\']',
                html,
            )
        assert match is not None, f"hidden {name} missing"
        return match.group(1)

    @staticmethod
    def _session_id_from_url(url: str) -> str:
        match = re.search(r"/sessions/([0-9a-fA-F-]{36})/", url)
        assert match is not None, f"session id not in {url}"
        return match.group(1)

    def _post_form(
        self,
        browser: requests.Session,
        path: str,
        payload: dict,
        *,
        files=None,
        allow_redirects: bool = True,
    ):
        headers = {}
        if "csrftoken" in browser.cookies:
            headers["X-CSRFToken"] = browser.cookies["csrftoken"]
            headers["Referer"] = self._url(path)
        return browser.post(
            self._url(path),
            data=payload,
            files=files,
            headers=headers,
            timeout=90,
            allow_redirects=allow_redirects,
        )

    def _wait_workflow_for(
        self,
        browser: requests.Session,
        session_id: str,
        needle: str,
        *,
        timeout: float = 60,
    ):
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._get(browser, f"/sessions/{session_id}/workflow/")
            if last.status_code == 200 and needle in last.text:
                return last
            time.sleep(0.35)
        assert last is not None
        self.fail(f"workflow never showed {needle!r}: {last.text[:600]}")

    def test_browser_linear_happy_path_accounts_clean_only(self):
        """CMX-7: SF picker → correction → compiler → canonical download."""

        browser = self._browser()
        home = self._get(browser, "/")
        csrf = self._extract_csrf(home.text)
        started = self._post_form(
            browser,
            "/sessions/new/",
            {"csrfmiddlewaretoken": csrf},
        )
        self.assertEqual(started.status_code, 200, started.text[:400])
        session_id = self._session_id_from_url(started.url)
        self.assertIn("does not change your download format", started.text)
        export_profile_before = get_export_profile(
            "crm_agnostic.accounts.export.v1"
        ).profile_contract_digest

        form_token = self._extract_form_token(started.text)
        csrf = self._extract_csrf(started.text)
        setup_revision = self._extract_hidden(started.text, "setup_revision")
        target = self._extract_hidden(started.text, "target_provider_id")
        product = self._post_form(
            browser,
            f"/sessions/{session_id}/product/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "operator_label": "MAP-R5 Browser Operator",
                "entity": "accounts",
                "operation": "clean_only",
                "reference_source": "uploaded",
                "connection_id": "",
                "people_output": "",
                "vocabulary": "salesforce",
                "target_provider_id": target,
                "setup_revision": setup_revision,
            },
        )
        self.assertEqual(product.status_code, 200, product.text[:500])
        self.assertIn("/upload/", product.url)
        self.assertIn("Map columns", product.text)

        form_token = self._extract_form_token(product.text)
        csrf = self._extract_csrf(product.text)
        # CMX-7: one deliberately unknown header must be corrected through the
        # Salesforce-labelled picker before compiler execution.
        csv_bytes = (
            b"account_name,Mystery Website,domain,industry,temporary_notes\n"
            b"Acme,https://acme.example,acme.example,Software,skip-me\n"
            b"Beta,https://beta.example,beta.example,Retail,also-skip\n"
        )
        uploaded = self._post_form(
            browser,
            f"/sessions/{session_id}/upload/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
                "role": "dataset",
                "csv_encoding": "utf-8-sig",
                "xlsx_sheet_index": "0",
            },
            files={"file": ("accounts.csv", csv_bytes, "text/csv")},
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text[:500])
        self.assertIn("accounts.csv", uploaded.text)
        self.assertRegex(
            uploaded.text,
            r'class="btn btn-primary"[^>]*href="[^"]*column-mapping',
        )

        configure_early = self._get(browser, f"/sessions/{session_id}/configure/")
        self.assertEqual(configure_early.status_code, 200)
        self.assertIn("mapping-required-gate", configure_early.text)
        self.assertNotIn('id="configure-form"', configure_early.text)

        map_start = self._get(browser, f"/sessions/{session_id}/column-mapping/")
        self.assertEqual(map_start.status_code, 200, map_start.text[:400])
        form_token = self._extract_form_token(map_start.text)
        csrf = self._extract_csrf(map_start.text)
        created_map = self._post_form(
            browser,
            f"/sessions/{session_id}/column-mapping/",
            {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
            },
        )
        self.assertEqual(created_map.status_code, 200, created_map.text[:500])
        plan_match = re.search(r"/column-mapping/([^/?#]+)/", created_map.url)
        if plan_match is None:
            link = re.search(
                rf"/sessions/{session_id}/column-mapping/([^/\"'?]+)/",
                created_map.text,
            )
            self.assertIsNotNone(link, "plan create did not reach review")
            plan_id = link.group(1)
        else:
            plan_id = plan_match.group(1)
        self.assertNotEqual(plan_id, session_id)

        review = self._get(browser, f"/sessions/{session_id}/column-mapping/{plan_id}/")
        self.assertEqual(review.status_code, 200)
        # Real sample values from CSV rows (not table header "Example 1").
        self.assertIn("Acme", review.text)
        self.assertIn("Beta", review.text)
        self.assertIn("https://acme.example", review.text)
        self.assertIn("account_name", review.text)
        self.assertIn("easyimports.ingest.salesforce.account.v1", review.text)
        self.assertRegex(review.text, r">\s*Website\s*<")

        # Deliberate corrections: map Mystery Website → Salesforce-labelled
        # Website and temporary_notes → Ignore column.
        form_token = self._extract_form_token(review.text)
        csrf = self._extract_csrf(review.text)
        manual_payload: dict[str, str] = {
            "form_token": form_token,
            "csrfmiddlewaretoken": csrf,
            "action": "save_draft",
        }
        corrected_choice_name: str | None = None
        website_choice_name: str | None = None
        website_choice_id: str | None = None
        for sel in re.finditer(
            r'<select[^>]*\bname="(choice_\d+)"[^>]*>(.*?)</select>',
            review.text,
            flags=re.DOTALL | re.IGNORECASE,
        ):
            name = sel.group(1)
            block = sel.group(2)
            row_start = review.text.rfind("<tr", 0, sel.start())
            row_end = review.text.find("</tr>", sel.end())
            row_html = (
                review.text[row_start : row_end + len("</tr>")]
                if row_start >= 0 and row_end >= 0
                else ""
            )
            selected = re.search(
                r'<option[^>]*\bvalue="([^"]*)"[^>]*selected',
                block,
                flags=re.IGNORECASE,
            )
            if "temporary_notes" in row_html:
                manual_payload[name] = "system:ignore"
                corrected_choice_name = name
            elif "Mystery Website" in row_html:
                website_option = re.search(
                    r'<option[^>]*\bvalue="([^"]+)"[^>]*>\s*Website\s*</option>',
                    block,
                    flags=re.IGNORECASE,
                )
                self.assertIsNotNone(
                    website_option,
                    "Salesforce Website picker option was not available",
                )
                website_choice_id = website_option.group(1)
                manual_payload[name] = website_choice_id
                website_choice_name = name
            elif selected and selected.group(1):
                manual_payload[name] = selected.group(1)
            else:
                manual_payload[name] = "system:ignore"
        self.assertIsNotNone(
            corrected_choice_name,
            "expected temporary_notes row for manual Ignore correction",
        )
        self.assertIsNotNone(
            website_choice_name,
            "expected Mystery Website row for manual Salesforce correction",
        )
        posted_manual = self._post_form(
            browser,
            f"/sessions/{session_id}/column-mapping/{plan_id}/",
            manual_payload,
        )
        self.assertEqual(posted_manual.status_code, 200, posted_manual.text[:500])

        # Reload plan and prove the manual correction persisted.
        reloaded = self._get(
            browser, f"/sessions/{session_id}/column-mapping/{plan_id}/"
        )
        self.assertEqual(reloaded.status_code, 200, reloaded.text[:500])
        assert corrected_choice_name is not None
        sel_block = re.search(
            rf'<select[^>]*\bname="{re.escape(corrected_choice_name)}"[^>]*>'
            r"(.*?)</select>",
            reloaded.text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        self.assertIsNotNone(
            sel_block,
            f"select {corrected_choice_name} missing after reload",
        )
        selected_after = re.search(
            r'<option[^>]*\bvalue="([^"]*)"[^>]*selected',
            sel_block.group(1),
            flags=re.IGNORECASE,
        )
        self.assertIsNotNone(
            selected_after, "no selected option after manual correction"
        )
        self.assertEqual(
            selected_after.group(1),
            "system:ignore",
            "manual Ignore correction did not persist on reload",
        )
        assert website_choice_name is not None
        assert website_choice_id is not None
        website_select = re.search(
            rf'<select[^>]*\bname="{re.escape(website_choice_name)}"[^>]*>'
            r"(.*?)</select>",
            reloaded.text,
            flags=re.DOTALL | re.IGNORECASE,
        )
        self.assertIsNotNone(website_select)
        selected_website = re.search(
            r'<option[^>]*\bvalue="([^"]+)"[^>]*selected',
            website_select.group(1),
            flags=re.IGNORECASE,
        )
        self.assertIsNotNone(selected_website)
        self.assertEqual(selected_website.group(1), website_choice_id)
        # Row still identifies the source header.
        self.assertIn("temporary_notes", reloaded.text)

        # Confirm remaining rows (ignore unresolved).
        confirm_attempt_urls: list[str] = []
        confirm_attempt_messages: list[str] = []
        for _ in range(12):
            review = self._get(
                browser, f"/sessions/{session_id}/column-mapping/{plan_id}/"
            )
            self.assertEqual(review.status_code, 200)
            if (
                "/configure/" in review.url
                or "Review your import settings" in review.text
            ):
                break
            if re.search(
                r"Mapping confirmed|id=\"mapping-confirmed\"",
                review.text,
                flags=re.IGNORECASE,
            ):
                break
            form_token = self._extract_form_token(review.text)
            csrf = self._extract_csrf(review.text)
            payload: dict[str, str] = {
                "form_token": form_token,
                "csrfmiddlewaretoken": csrf,
            }
            for sel in re.finditer(
                r'<select[^>]*\bname="(choice_\d+)"[^>]*>(.*?)</select>',
                review.text,
                flags=re.DOTALL | re.IGNORECASE,
            ):
                name = sel.group(1)
                block = sel.group(2)
                selected = re.search(
                    r'<option[^>]*\bvalue="([^"]*)"[^>]*selected',
                    block,
                    flags=re.IGNORECASE,
                )
                if selected and selected.group(1):
                    payload[name] = selected.group(1)
                else:
                    payload[name] = "system:ignore"
            needs_review = bool(
                re.search(
                    r"<tr\b[^>]*\bdata-filter=\"needs_review\"",
                    review.text,
                    flags=re.IGNORECASE,
                )
            )
            payload["action"] = "save_draft" if needs_review else "confirm_mapping"
            posted = self._post_form(
                browser,
                f"/sessions/{session_id}/column-mapping/{plan_id}/",
                payload,
            )
            confirm_attempt_urls.append(posted.url)
            confirm_attempt_messages.extend(
                re.sub(r"<[^>]+>", " ", value)
                for value in re.findall(
                    r'<li\b[^>]*class="[^"]*message[^"]*"[^>]*>(.*?)</li>',
                    posted.text,
                    flags=re.DOTALL | re.IGNORECASE,
                )
            )
            self.assertEqual(posted.status_code, 200, posted.text[:500])
            if (
                "/configure/" in posted.url
                or "Review your import settings" in posted.text
            ):
                break
        else:
            unresolved = re.findall(
                r'<tr\b[^>]*\bdata-filter="needs_review"[^>]*>(.*?)</tr>',
                review.text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            messages = re.findall(
                r'<li\b[^>]*class="[^"]*message[^"]*"[^>]*>(.*?)</li>',
                review.text,
                flags=re.DOTALL | re.IGNORECASE,
            )
            self.fail(
                "Could not confirm mapping plan within attempts; unresolved="
                + repr([re.sub(r"<[^>]+>", " ", row) for row in unresolved])
                + "; messages="
                + repr([re.sub(r"<[^>]+>", " ", row) for row in messages])
                + "; urls="
                + repr(confirm_attempt_urls)
                + "; attempt_messages="
                + repr(confirm_attempt_messages)
            )

        configure = self._get(browser, f"/sessions/{session_id}/configure/")
        self.assertEqual(configure.status_code, 200, configure.text[:500])
        self.assertIn("Start import", configure.text)
        self.assertNotIn("mapping-required-gate", configure.text)
        self.assertNotIn("Type of people", configure.text)

        form_token = self._extract_form_token(configure.text)
        csrf = self._extract_csrf(configure.text)
        setup_revision = self._extract_hidden(configure.text, "setup_revision")
        mode_fields = re.findall(r'name="(mode__[^"]+)"', configure.text)
        data = {
            "form_token": form_token,
            "csrfmiddlewaretoken": csrf,
            "setup_revision": setup_revision,
            "list_duplicate_policy": "drop_repeats",
        }
        for field in mode_fields:
            if field == "mode__delivery":
                data[field] = "preview"
            else:
                data[field] = "disabled"

        created_wf = self._post_form(
            browser,
            f"/sessions/{session_id}/configure/",
            data,
        )
        self.assertEqual(created_wf.status_code, 200, created_wf.text[:600])
        self.assertTrue(
            "/workflow/" in created_wf.url or "workflow" in created_wf.text.lower(),
            f"expected workflow page, got {created_wf.url}: {created_wf.text[:400]}",
        )
        self.assertNotIn(
            "Confirm column mapping before starting the import",
            created_wf.text,
        )
        self.assertNotIn(
            "no longer matches",
            created_wf.text.lower(),
        )

        # Terminal evidence only (not stepper "Review & download" / results-head).
        terminal_summary = "Run summary"
        terminal_no_crm = "No live CRM changes were authorized for this run."
        page = self._get(browser, f"/sessions/{session_id}/workflow/")
        self.assertEqual(page.status_code, 200, page.text[:500])

        def _is_terminal(html: str) -> bool:
            return terminal_summary in html and terminal_no_crm in html

        if not _is_terminal(page.text):
            # Authorize delivery preview when the effect gate is present.
            if 'name="selected_mode"' not in page.text:
                page = self._wait_workflow_for(
                    browser,
                    session_id,
                    needle='name="selected_mode"',
                    timeout=60,
                )
            effect_form = re.search(
                r'action="(/sessions/[^"]+/effects/authorize/)"(.*?</form>)',
                page.text,
                re.DOTALL,
            )
            self.assertIsNotNone(
                effect_form,
                "expected effect authorize form before terminal evidence; "
                f"body={page.text[:700]}",
            )
            form_chunk = effect_form.group(0)
            effect_token = self._extract_form_token(form_chunk)
            csrf = self._extract_csrf(page.text)
            mode = "preview"
            if 'value="preview"' not in form_chunk:
                mode_match = re.search(r'<option value="([^"]+)"', form_chunk)
                self.assertIsNotNone(mode_match)
                mode = mode_match.group(1)
            authorized = self._post_form(
                browser,
                f"/sessions/{session_id}/effects/authorize/",
                {
                    "form_token": effect_token,
                    "csrfmiddlewaretoken": csrf,
                    "selected_mode": mode,
                },
            )
            self.assertEqual(authorized.status_code, 200, authorized.text[:500])
            deadline = time.time() + 90
            page = authorized
            while time.time() < deadline:
                page = self._get(browser, f"/sessions/{session_id}/workflow/")
                if _is_terminal(page.text):
                    break
                time.sleep(0.4)
            else:
                self.fail(
                    "workflow never reached terminal evidence "
                    f"({terminal_summary!r} + {terminal_no_crm!r}): "
                    f"{page.text[:900]}"
                )

        self.assertIn(terminal_summary, page.text)
        self.assertIn(terminal_no_crm, page.text)

        # CMX-7 execution proof: the downloadable prepared source contains the
        # compiler-owned canonical column, not the uploaded mystery header or
        # Salesforce picker label. Vocabulary did not mutate export intent.
        artifact_link = re.search(
            r'href="([^"]+/artifacts/[^"]+)"',
            page.text,
            flags=re.IGNORECASE,
        )
        self.assertIsNotNone(
            artifact_link,
            f"prepared source artifact link missing: {page.text[:900]}",
        )
        artifact = self._get(browser, artifact_link.group(1))
        self.assertEqual(artifact.status_code, 200)
        prepared_csv = artifact.content.decode("utf-8")
        header = prepared_csv.splitlines()[0]
        self.assertIn("account_website", header)
        self.assertNotIn("Mystery Website", header)
        self.assertNotIn("Salesforce.com", header)
        self.assertIn("https://acme.example", prepared_csv)
        self.assertIn("https://beta.example", prepared_csv)
        self.assertEqual(
            get_export_profile(
                "crm_agnostic.accounts.export.v1"
            ).profile_contract_digest,
            export_profile_before,
        )
