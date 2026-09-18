"""OUT-I2 Django surface — uploaded-id disagreement is a grouped stop.

Network-free. Django does not import mappings_2.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from importer.workflow_views import (
    GROUPED_SELECTION_DECISION_TYPES,
    GROUPED_SELECTION_TITLES,
    KNOWN_GROUPED_OPTION_KINDS,
)


class OutI2GroupedDecisionSurfaceTests(SimpleTestCase):
    def test_uploaded_id_disagreements_are_grouped_stops(self) -> None:
        assert "uploaded_account_id_disagreement" in GROUPED_SELECTION_DECISION_TYPES
        assert "uploaded_person_id_disagreement" in GROUPED_SELECTION_DECISION_TYPES
        assert (
            GROUPED_SELECTION_TITLES["uploaded_account_id_disagreement"]
            == "Uploaded Account Id"
        )
        assert (
            GROUPED_SELECTION_TITLES["uploaded_person_id_disagreement"]
            == "Uploaded Contact Id"
        )
        assert {"keep_uploaded_id", "use_matched_id", "quarantine_write"} <= (
            KNOWN_GROUPED_OPTION_KINDS
        )

    def test_workflow_views_do_not_import_verify_port(self) -> None:
        from pathlib import Path

        source = Path(__file__).with_name("workflow_views.py").read_text(
            encoding="utf-8"
        )
        assert "verify_id_in_snapshot" not in source
        assert "uploaded_id_authority" not in source
