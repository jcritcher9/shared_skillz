"""DRC-1: entity-specific score helper + field-fill copy (network-free)."""

from __future__ import annotations

import re
from html.parser import HTMLParser
from unittest.mock import patch
from uuid import uuid4

from django.template.loader import render_to_string
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .api_client import EasyImportsApiClient
from .crm_duplicate_score_helper import (
    AUTO_MERGE_INFO_TIP,
    COMPANY_SCORE_BANDS,
    PERSON_AUDIT_NOTE,
    PERSON_SCORE_BANDS,
    THRESHOLD_INFO_TIP,
)
from .field_merge_display import (
    ACCOUNT_EMPTY_FILL_RULES,
    DOMAIN_FOLLOWS_WEBSITE_LABEL,
    EMPTY_FIELD_DECISIONS_COPY,
    FIELD_FILL_EMPTY_SENTENCE,
    FILL_BLANK_SURVIVOR_FIELD_LABEL,
    KEEP_SURVIVOR_VALUE_LABEL,
    PERSON_EMPTY_FILL_RULES,
    field_merge_display,
    merge_rule_operator_label,
)
from .forms import CrmDuplicateJourneyForm
from .models import ImportSession
from .test_crm_duplicate_journey_phase5a import _reviewed_result
from .test_crm_duplicate_journey_phase7c import _connected_fake
from .test_crm_field_merge_1b_ui import _sample_field_merge_plan


class _HtmlTree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root: dict = {
            "tag": "[document]",
            "attrs": {},
            "children": [],
            "text": "",
            "hidden": False,
        }
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        ad = dict(attrs)
        node = {
            "tag": tag,
            "attrs": ad,
            "children": [],
            "text": "",
            "hidden": "hidden" in ad,
        }
        self._stack[-1]["children"].append(node)
        if tag not in {"br", "img", "input", "meta", "link", "hr", "source"}:
            self._stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index]["tag"] == tag:
                self._stack = self._stack[:index]
                break

    def handle_data(self, data):
        self._stack[-1]["text"] += data


def _parse_html(html: str) -> dict:
    parser = _HtmlTree()
    parser.feed(html)
    parser.close()
    return parser.root


def _walk(node: dict):
    yield node
    for child in node.get("children") or []:
        yield from _walk(child)


def _by_id(root: dict, element_id: str) -> dict | None:
    for node in _walk(root):
        if node.get("attrs", {}).get("id") == element_id:
            return node
    return None


def _visible_text(node: dict, ancestor_hidden: bool = False) -> str:
    hidden = ancestor_hidden or bool(node.get("hidden"))
    parts: list[str] = []
    if not hidden:
        parts.append(node.get("text") or "")
        for child in node.get("children") or []:
            parts.append(_visible_text(child, False))
    return " ".join(part for part in parts if part.strip())


def _effectively_hidden(root: dict, element_id: str) -> bool | None:
    """True when the element or any ancestor has the HTML hidden attribute."""

    def walk(node: dict, ancestor_hidden: bool) -> bool | None:
        hidden = ancestor_hidden or bool(node.get("hidden"))
        if node.get("attrs", {}).get("id") == element_id:
            return hidden
        for child in node.get("children") or []:
            found = walk(child, hidden)
            if found is not None:
                return found
        return None

    return walk(root, False)


def _form_token(html: str) -> str:
    match = re.search(r'name="form_token" value="([^"]+)"', html)
    assert match, "start page is missing form_token"
    return match.group(1)


class FieldFillCopyHelperTests(SimpleTestCase):
    def test_frozen_sentence_and_rule_labels(self):
        display = field_merge_display(_sample_field_merge_plan())
        self.assertEqual(display["empty_fill_sentence"], FIELD_FILL_EMPTY_SENTENCE)
        row = display["field_decisions"][0]
        self.assertEqual(row["merge_rule"], "empty_fill_from_ranked_loser")
        self.assertEqual(row["merge_rule_label"], FILL_BLANK_SURVIVOR_FIELD_LABEL)
        self.assertEqual(row["overwrite_label"], KEEP_SURVIVOR_VALUE_LABEL)
        self.assertEqual(
            merge_rule_operator_label("derive_domain_from_projected_website"),
            DOMAIN_FOLLOWS_WEBSITE_LABEL,
        )

    def test_production_account_rules_use_locked_empty_fill_label(self):
        self.assertIn("fill_empty_from_ranked_loser", ACCOUNT_EMPTY_FILL_RULES)
        for rule in ACCOUNT_EMPTY_FILL_RULES:
            self.assertEqual(
                merge_rule_operator_label(rule),
                FILL_BLANK_SURVIVOR_FIELD_LABEL,
                rule,
            )
        self.assertEqual(
            merge_rule_operator_label("derive_domain_from_projected_website"),
            DOMAIN_FOLLOWS_WEBSITE_LABEL,
        )
        plan = _sample_field_merge_plan()
        plan["field_decisions"] = [
            {
                "logical_field_key": "account_linkedin_url",
                "value": "https://linkedin.com/company/acme",
                "merge_rule": "fill_empty_from_ranked_loser",
                "overwrites_existing_value": False,
                "source_member_id": "A2",
                "source_field_key": "account_linkedin_url",
            },
            {
                "logical_field_key": "account_billing_city",
                "value": "Austin",
                "merge_rule": "fill_atomic_billing_address_from_ranked_loser",
                "overwrites_existing_value": False,
                "source_member_id": "A2",
                "source_field_key": "account_billing_city",
            },
            {
                "logical_field_key": "account_domain",
                "value": "acme.com",
                "merge_rule": "derive_domain_from_projected_website",
                "overwrites_existing_value": False,
                "source_member_id": "A2",
                "source_field_key": "account_website",
            },
        ]
        html = render_to_string(
            "importer/_field_merge_plan.html",
            {"field_merge": field_merge_display(plan)},
        )
        self.assertIn(FILL_BLANK_SURVIVOR_FIELD_LABEL, html)
        self.assertIn(DOMAIN_FOLLOWS_WEBSITE_LABEL, html)
        self.assertNotIn("fill empty from ranked loser", html)
        self.assertNotIn("fill atomic billing address from ranked loser", html)
        self.assertNotIn(">fill_empty_from_ranked_loser<", html)

    def test_production_people_rules_use_locked_empty_fill_label(self):
        self.assertIn("fill_empty_email_from_ranked_person", PERSON_EMPTY_FILL_RULES)
        for rule in PERSON_EMPTY_FILL_RULES:
            self.assertEqual(
                merge_rule_operator_label(rule),
                FILL_BLANK_SURVIVOR_FIELD_LABEL,
                rule,
            )
        plan = _sample_field_merge_plan()
        plan["entity_family"] = "person"
        plan["field_decisions"] = [
            {
                "logical_field_key": "email",
                "value": "pat@example.com",
                "merge_rule": "fill_empty_email_from_ranked_person",
                "overwrites_existing_value": False,
                "source_member_id": "P2",
                "source_field_key": "email",
            },
            {
                "logical_field_key": "mailing_city",
                "value": "Denver",
                "merge_rule": "fill_atomic_mailing_address_from_ranked_person",
                "overwrites_existing_value": False,
                "source_member_id": "P2",
                "source_field_key": "mailing_city",
            },
        ]
        html = render_to_string(
            "importer/_field_merge_plan.html",
            {"field_merge": field_merge_display(plan)},
        )
        self.assertIn(FILL_BLANK_SURVIVOR_FIELD_LABEL, html)
        self.assertNotIn("fill empty email from ranked person", html)
        self.assertNotIn("fill atomic mailing address from ranked person", html)
        self.assertNotIn(">fill_empty_email_from_ranked_person<", html)
        self.assertNotEqual(
            merge_rule_operator_label("complex_overwrite"),
            FILL_BLANK_SURVIVOR_FIELD_LABEL,
        )

    def test_empty_decisions_copy(self):
        plan = _sample_field_merge_plan()
        plan["field_decisions"] = []
        display = field_merge_display(plan)
        html = render_to_string(
            "importer/_field_merge_plan.html",
            {"field_merge": display},
        )
        self.assertIn(FIELD_FILL_EMPTY_SENTENCE, html)
        self.assertIn(EMPTY_FIELD_DECISIONS_COPY, html)
        self.assertNotIn("No empty-fill field recommendations", html)
        self.assertNotIn("empty_fill_from_ranked_loser", html)

    def test_review_include_uses_operator_columns_and_sentence(self):
        html = render_to_string(
            "importer/_field_merge_plan.html",
            {"field_merge": field_merge_display(_sample_field_merge_plan())},
        )
        self.assertIn(FIELD_FILL_EMPTY_SENTENCE, html)
        self.assertIn("data-field-fill-copy", html)
        self.assertIn("After merge", html)
        self.assertIn("Comes from", html)
        self.assertIn("What happens", html)
        self.assertIn("Fill blank survivor field", html)
        self.assertIn(KEEP_SURVIVOR_VALUE_LABEL, html)
        self.assertNotIn("Recommended value", html)
        self.assertNotIn("Overwrites existing?", html)
        # Raw rule token must not be the only explanation.
        self.assertIn("Fill blank survivor field", html)
        self.assertNotIn(">empty_fill_from_ranked_loser<", html)


class ScoreHelperCopyTests(SimpleTestCase):
    def test_people_bands_are_100_90_75_only(self):
        ranges = [band["range"] for band in PERSON_SCORE_BANDS]
        self.assertEqual(
            ranges,
            ["100 (high)", "90 (high)", "75 (medium)"],
        )
        joined = " ".join(band["text"] for band in PERSON_SCORE_BANDS)
        self.assertIn("usable email", joined)
        self.assertNotIn("50", joined)
        self.assertNotIn("25", joined)
        self.assertNotIn("50", PERSON_AUDIT_NOTE)
        self.assertNotIn("25", PERSON_AUDIT_NOTE)

    def test_company_drc3_list_has_80_and_narrowed_70(self):
        ranges = [band["range"] for band in COMPANY_SCORE_BANDS]
        self.assertEqual(
            ranges,
            [
                "90–100 (high)",
                "80 (medium)",
                "70 (medium)",
                "Below 70 (low)",
            ],
        )
        joined = " ".join(
            f"{band['range']} {band['text']}" for band in COMPANY_SCORE_BANDS
        )
        self.assertIn("company domain", joined)
        self.assertIn("every account in the pair has no meaningful domain", joined)
        self.assertIn(
            "exactly one side has a meaningful domain and the other has none",
            joined,
        )
        self.assertNotIn("70–89", joined)
        self.assertNotIn("both empty", joined)

    def test_threshold_below_90_still_rejected(self):
        form = CrmDuplicateJourneyForm(
            data={
                "connection_id": "crm_conn_test",
                "entity_family": "company",
                "source_mode": "acquire_all",
                "form_token": "token",
                "auto_merge_high_confidence": "on",
                "auto_merge_min_confidence": "89",
            },
            connections=_connected_fake(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("auto_merge_min_confidence", form.errors)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9",
)
class Drc1StartPageVisibilityTests(TestCase):
    def _get_start(self):
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value={"connections": _connected_fake()},
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "crm_duplicate_journeys",
                        return_value={"journeys": []},
                    ):
                        return self.client.get(
                            reverse("importer:crm_duplicate_journey")
                        )

    def _post_start(
        self,
        *,
        entity_family: str,
        auto_merge: bool = True,
        threshold: str = "89",
        source_mode: str = "acquire_all",
    ):
        first = self._get_start()
        self.assertEqual(first.status_code, 200)
        token = _form_token(first.content.decode("utf-8"))
        data = {
            "form_token": token,
            "connection_id": "crm_conn_test",
            "entity_family": entity_family,
            "source_mode": source_mode,
        }
        if auto_merge:
            data["auto_merge_high_confidence"] = "on"
            data["auto_merge_min_confidence"] = threshold
        with patch.object(EasyImportsApiClient, "assert_compatible", return_value=None):
            with patch.object(
                EasyImportsApiClient,
                "crm_providers",
                return_value={"providers": [{"provider_key": "fake"}]},
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "crm_connections",
                    return_value={"connections": _connected_fake()},
                ):
                    with patch.object(
                        EasyImportsApiClient,
                        "crm_duplicate_journeys",
                        return_value={"journeys": []},
                    ):
                        return self.client.post(
                            reverse("importer:crm_duplicate_journey"),
                            data=data,
                        )

    def test_get_companies_default_hides_people_list(self):
        response = self._get_start()
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        tree = _parse_html(html)
        company = _by_id(tree, "crm-dupe-score-helper-company")
        person = _by_id(tree, "crm-dupe-score-helper-person")
        self.assertIsNotNone(company)
        self.assertIsNotNone(person)
        self.assertFalse(_effectively_hidden(tree, "crm-dupe-score-helper-company"))
        self.assertTrue(_effectively_hidden(tree, "crm-dupe-score-helper-person"))
        self.assertIn('name="entity_family"', html)
        self.assertIn("<select", html)
        self.assertIn("info-bubble", html)
        self.assertIn(AUTO_MERGE_INFO_TIP, html)
        self.assertIn(THRESHOLD_INFO_TIP, html)
        self.assertIn("syncScoreHelpers", html)
        visible = _visible_text(tree)
        self.assertIn("company domain", visible)
        self.assertIn("90–100", visible)
        self.assertIn("80 (medium)", visible)
        self.assertNotIn("usable email", visible)
        self.assertNotIn("same account + same phone", visible)

    def test_companies_selected_visible_helper_has_account_scale(self):
        response = self._post_start(entity_family="company")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        tree = _parse_html(html)
        threshold = _by_id(tree, "crm-dupe-auto-merge-threshold")
        self.assertFalse(threshold["hidden"])
        self.assertFalse(_effectively_hidden(tree, "crm-dupe-score-helper-company"))
        self.assertTrue(_effectively_hidden(tree, "crm-dupe-score-helper-person"))
        visible = _visible_text(tree)
        self.assertIn("company domain", visible)
        self.assertIn("90–100", visible)
        self.assertIn("80 (medium)", visible)
        self.assertIn("exactly one side has a meaningful domain", visible)
        self.assertNotIn("usable email", visible)
        self.assertNotIn("same account + same phone", visible)
        self.assertIn("rejected, not lowered", html)

    def test_people_selected_visible_helper_has_people_scale(self):
        response = self._post_start(entity_family="person")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        tree = _parse_html(html)
        self.assertTrue(_effectively_hidden(tree, "crm-dupe-score-helper-company"))
        self.assertFalse(_effectively_hidden(tree, "crm-dupe-score-helper-person"))
        visible = _visible_text(tree)
        self.assertIn("usable email", visible)
        self.assertIn("100 (high)", visible)
        self.assertIn("90 (high)", visible)
        self.assertIn("75 (medium)", visible)
        self.assertNotIn("company domain", visible)
        self.assertNotIn("80 (medium)", visible)
        person = _by_id(tree, "crm-dupe-score-helper-person")
        person_visible = _visible_text(person)
        self.assertNotRegex(person_visible, r"\b50\b")
        self.assertNotRegex(person_visible, r"\b25\b")
        select = _by_id(tree, "id_entity_family")
        self.assertIsNotNone(select)
        selected = [
            child
            for child in select["children"]
            if child["tag"] == "option" and "selected" in child["attrs"]
        ]
        self.assertTrue(selected)
        self.assertEqual(selected[0]["attrs"].get("value"), "person")

    def test_people_helper_stays_visible_when_auto_merge_is_off(self):
        """Do not hide the whole People helper just because auto-merge is off."""

        response = self._post_start(
            entity_family="person",
            auto_merge=False,
            source_mode="uploaded_population",
        )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        tree = _parse_html(html)
        threshold = _by_id(tree, "crm-dupe-auto-merge-threshold")
        self.assertTrue(_effectively_hidden(tree, "crm-dupe-auto-merge-threshold"))
        self.assertTrue(threshold["hidden"])
        self.assertTrue(_effectively_hidden(tree, "crm-dupe-score-helper-company"))
        self.assertFalse(_effectively_hidden(tree, "crm-dupe-score-helper-person"))
        visible = _visible_text(tree)
        self.assertIn("usable email", visible)
        self.assertNotIn("company domain", visible)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    STATICFILES_STORAGE="django.contrib.staticfiles.storage.StaticFilesStorage",
    EASYIMPORTS_API_BASE_URL="http://127.0.0.1:9",
)
class Drc1MergePageCopyTests(TestCase):
    def setUp(self):
        self.owner = uuid4()
        self.session = ImportSession.objects.create(
            owner_id=self.owner,
            operator_label="CRM dupe DRC-1",
            product_key="easyimports.duplicate_resolution",
            status=ImportSession.Status.RUNNING,
            target_provider_id="fake",
            options={
                "run_id": "run-source-drc1",
                "review_run_id": "run-review-drc1",
                "review_handoff_id": "review_drc1",
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
        browser = self.client.session
        browser["easyimports_owner_id"] = str(self.owner)
        browser.save()

    def test_merge_plan_page_shows_frozen_empty_fill_sentence(self):
        with patch(
            "importer.journey_views.owner_id_for_request",
            return_value=self.owner,
        ):
            with patch.object(
                EasyImportsApiClient, "assert_compatible", return_value=None
            ):
                with patch.object(
                    EasyImportsApiClient,
                    "duplicate_reviewed_result",
                    return_value=_reviewed_result(),
                ):
                    response = self.client.get(
                        reverse(
                            "importer:crm_duplicate_journey_merge",
                            kwargs={"session_id": self.session.id},
                        )
                    )
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")
        self.assertIn(FIELD_FILL_EMPTY_SENTENCE, html)
        self.assertIn("data-field-fill-copy", html)
        self.assertNotIn("populate survivor fields from the frozen projection", html)
        self.assertNotIn("empty_fill_from_ranked_loser", html)
        # DRC-1 must not add per-group field-merge tables on the merge page.
        self.assertNotIn("Field decisions for survivor", html)
