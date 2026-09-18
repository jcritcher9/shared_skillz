"""OUT-9 Django surface — whole-group exclusion sits below the row table.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from pathlib import Path

from django.test import SimpleTestCase

from importer.workflow_views import _decision_presentation

WORKFLOW_TEMPLATE = (
    Path(__file__).resolve().parent / "templates" / "importer" / "workflow.html"
)


def _list_duplicates_decision(*, selected_option_id: str = "option-keep") -> dict:
    rows = [
        {
            "option_id": "option-keep",
            "group_id": "list-dupe-1",
            "option_kind": "uploaded_row",
            "option_label": "Keep this uploaded row",
            "selected": selected_option_id == "option-keep",
            "contact_email_final": "person@example.com",
        },
        {
            "option_id": "option-exclude",
            "group_id": "list-dupe-1",
            "option_kind": "exclude_group",
            "option_label": "Exclude every row in this duplicate group",
            "selected": selected_option_id == "option-exclude",
            "contact_email_final": None,
        },
    ]
    return {
        "decision_id": "decision-out-9",
        "decision_type": "list_duplicates",
        "body": {
            "rows_df": {
                "columns": list(rows[0]),
                "rows": rows,
            },
            "option_id_col": "option_id",
            "group_id_col": "group_id",
        },
    }


class Out9PresentationTests(SimpleTestCase):
    def test_exclude_group_is_separate_from_uploaded_rows(self) -> None:
        presentation = _decision_presentation(_list_duplicates_decision())

        self.assertEqual(
            [row["option_id"] for row in presentation["groups"][0]["rows"]],
            ["option-keep"],
        )
        self.assertEqual(
            [
                option["option_id"]
                for option in presentation["groups"][0]["whole_group_options"]
            ],
            ["option-exclude"],
        )

    def test_submitted_exclude_selection_is_restored_below_table(self) -> None:
        presentation = _decision_presentation(
            _list_duplicates_decision(),
            submitted_group_choices={"list-dupe-1": ["option-exclude"]},
        )

        group = presentation["groups"][0]
        self.assertFalse(group["rows"][0]["selected"])
        self.assertTrue(group["whole_group_options"][0]["selected"])
        self.assertEqual(group["input_name"], "selected__0")

    def test_template_places_exclude_control_after_group_table(self) -> None:
        source = WORKFLOW_TEMPLATE.read_text(encoding="utf-8")
        grouped_branch = source.split(
            '{% elif dtype == "list_duplicates" or dtype == "multiple_crm_matches" or dtype == "multiple_crm_account_matches" %}',
            1,
        )[1].split('{% elif dtype == "account_duplicate_group_review"', 1)[0]

        table_close = grouped_branch.index("</table>")
        whole_group_loop = grouped_branch.index(
            "{% for option in group.whole_group_options %}"
        )
        self.assertGreater(whole_group_loop, table_close)
        table_source = grouped_branch[:table_close]
        self.assertIn("{% for row in group.rows %}", table_source)
        self.assertNotIn("whole_group_options", table_source)
        self.assertIn('name="{{ group.input_name }}"', grouped_branch)

    def test_exclude_kind_is_not_limited_to_list_duplicate_decisions(self) -> None:
        decision = _list_duplicates_decision()
        decision["decision_type"] = "multiple_crm_matches"

        group = _decision_presentation(decision)["groups"][0]

        self.assertEqual(group["rows"][0]["option_kind"], "uploaded_row")
        self.assertEqual(
            group["whole_group_options"][0]["option_kind"], "exclude_group"
        )
