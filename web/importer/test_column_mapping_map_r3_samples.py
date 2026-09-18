"""MAP-R3 Django-side sample preview helper (no mappings_2 import)."""

from __future__ import annotations

import csv
from pathlib import Path

from django.test import SimpleTestCase

from importer.column_mapping_samples import (
    bound_sample_value,
    extract_csv_samples,
    extract_samples_for_source,
)

FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "tests"
    / "mappings_2"
    / "fixtures"
    / "column_mapping_product_readiness"
    / "samples_preview_twin.v1.csv"
)


class ColumnMappingSamplesTests(SimpleTestCase):
    def test_csv_samples_aligned(self) -> None:
        with FIXTURE.open(encoding="utf-8", newline="") as fh:
            headers = next(csv.reader(fh))
        samples = extract_csv_samples(FIXTURE, expected_headers=headers)
        self.assertEqual(len(samples), len(headers))
        self.assertEqual(samples[0], ["3R8", "5Dm Group", "5Skye"])
        # Row-aligned: empty linkedin cell keeps a placeholder slot.
        self.assertEqual(len(samples[-1]), 3)
        self.assertEqual(samples[-1][2], "")
        self.assertEqual({len(col) for col in samples}, {3})

    def test_header_mismatch_empty(self) -> None:
        samples = extract_csv_samples(
            FIXTURE, expected_headers=["nope", "wrong"]
        )
        self.assertEqual(samples, [[], []])

    def test_bound_truncates(self) -> None:
        value = bound_sample_value("x" * 200)
        self.assertEqual(len(value), 80)
        self.assertTrue(value.endswith("…"))

    def test_extract_for_source_csv(self) -> None:
        with FIXTURE.open(encoding="utf-8", newline="") as fh:
            headers = next(csv.reader(fh))
        samples = extract_samples_for_source(
            path=FIXTURE,
            expected_headers=headers,
            encoding="utf-8",
            original_name="samples_preview_twin.v1.csv",
        )
        self.assertEqual(samples[0][0], "3R8")
