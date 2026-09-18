"""MAP-R3 UI-only sample preview extraction (no mappings_2 import).

Mirrors mappings_2 MAP-R0 freeze §6 limits and MAP-R3 extract behavior so
Django can attach sample_values on plan create without runtime path injection.

MAP-R3 Grade D rem: shared three-row window with empty-string placeholders
(aligned across columns).
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Sequence
import zipfile
import xml.etree.ElementTree as ET

# Keep in sync with mappings_2.canon.column_mapping.freezes.map_r0.
SAMPLE_VALUES_MAX_COUNT = 3
SAMPLE_VALUES_MAX_CHARS = 80
SAMPLE_VALUES_MAX_BLANK_SKIP = 5
_SAMPLE_ELLIPSIS = "…"


def bound_sample_value(value: object) -> str:
    text = str(value if value is not None else "")
    cleaned = "".join(
        ch if (ch == "\t" or ord(ch) >= 32) else " " for ch in text
    ).strip()
    max_chars = SAMPLE_VALUES_MAX_CHARS
    if len(cleaned) <= max_chars:
        return cleaned
    if max_chars <= 1:
        return _SAMPLE_ELLIPSIS
    return cleaned[: max_chars - 1] + _SAMPLE_ELLIPSIS


def bound_sample_values(values: Sequence[object] | None) -> list[str]:
    """Preserve empty placeholders so example columns stay row-aligned."""

    if not values:
        return []
    out: list[str] = []
    for raw in values:
        if len(out) >= SAMPLE_VALUES_MAX_COUNT:
            break
        out.append(bound_sample_value(raw))
    return out


def _blank_row(cells: Sequence[str]) -> bool:
    return not any(str(c or "").strip() for c in cells)


def _columns_from_window(
    window: Sequence[Sequence[str]],
    *,
    column_count: int,
) -> list[list[str]]:
    columns: list[list[str]] = [[] for _ in range(column_count)]
    for cells in window:
        for i in range(column_count):
            raw = cells[i] if i < len(cells) else ""
            columns[i].append(bound_sample_value(raw))
    return columns


def _collect_data_window(
    rows,
    *,
    column_count: int,
) -> list[list[str]]:
    """Stream any row iterable; keep only a bounded non-blank window."""

    window: list[list[str]] = []
    blanks = 0
    for row in rows:
        cells = [str(row[i]) if i < len(row) else "" for i in range(column_count)]
        if _blank_row(cells):
            blanks += 1
            if blanks > SAMPLE_VALUES_MAX_BLANK_SKIP:
                break
            continue
        blanks = 0
        window.append(cells)
        if len(window) >= SAMPLE_VALUES_MAX_COUNT:
            break
    return window


def extract_csv_samples(
    path: Path | str,
    *,
    expected_headers: Sequence[str],
    encoding: str = "utf-8",
) -> list[list[str]]:
    headers = [str(h) for h in expected_headers]
    empty: list[list[str]] = [[] for _ in headers]
    if not headers:
        return []
    p = Path(path)
    try:
        with p.open("r", encoding=encoding or "utf-8", newline="") as fh:
            reader = csv.reader(fh)
            try:
                file_headers = next(reader)
            except StopIteration:
                return empty
            if [str(h) for h in file_headers] != headers:
                return empty
            # Stream reader; do not materialize the full upload.
            window = _collect_data_window(reader, column_count=len(headers))
            return _columns_from_window(window, column_count=len(headers))
    except (OSError, UnicodeError, csv.Error):
        return empty


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    try:
        root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    except (KeyError, ET.ParseError):
        return []
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    strings: list[str] = []
    for si in root.findall("m:si", ns):
        texts = [t.text or "" for t in si.findall(".//m:t", ns)]
        strings.append("".join(texts))
    return strings


def _xlsx_cell_text(cell: ET.Element, shared: list[str], ns: dict[str, str]) -> str:
    cell_type = cell.get("t")
    v = cell.find("m:v", ns)
    if v is None or v.text is None:
        is_elem = cell.find("m:is", ns)
        if is_elem is not None:
            return "".join(t.text or "" for t in is_elem.findall(".//m:t", ns))
        return ""
    raw = v.text
    if cell_type == "s":
        try:
            return shared[int(raw)]
        except (ValueError, IndexError):
            return ""
    return str(raw)


def _col_index(cell_ref: str) -> int:
    letters = "".join(ch for ch in cell_ref if ch.isalpha())
    n = 0
    for ch in letters.upper():
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return max(n - 1, 0)


def extract_xlsx_samples(
    path: Path | str,
    *,
    expected_headers: Sequence[str],
    sheet_index: int = 0,
) -> list[list[str]]:
    headers = [str(h) for h in expected_headers]
    empty: list[list[str]] = [[] for _ in headers]
    if not headers:
        return []
    p = Path(path)
    try:
        with zipfile.ZipFile(p, "r") as zf:
            shared = _xlsx_shared_strings(zf)
            wb = ET.fromstring(zf.read("xl/workbook.xml"))
            ns = {
                "m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
                "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
            }
            sheets = wb.findall("m:sheets/m:sheet", ns)
            if not sheets or sheet_index < 0 or sheet_index >= len(sheets):
                return empty
            rel_id = sheets[sheet_index].get(
                "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
            )
            rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
            rel_ns = {
                "pr": "http://schemas.openxmlformats.org/package/2006/relationships"
            }
            target = None
            for rel in rels.findall("pr:Relationship", rel_ns):
                if rel.get("Id") == rel_id:
                    target = rel.get("Target")
                    break
            if not target:
                return empty
            sheet_path = "xl/" + target.lstrip("/")
            if sheet_path.startswith("xl/xl/"):
                sheet_path = sheet_path[3:]
            root = ET.fromstring(zf.read(sheet_path))
            rows_xml = root.findall("m:sheetData/m:row", ns)
            if not rows_xml:
                return empty

            def row_values(row_el: ET.Element) -> list[str]:
                cells: dict[int, str] = {}
                max_i = -1
                for c in row_el.findall("m:c", ns):
                    ref = c.get("r") or "A1"
                    idx = _col_index(ref)
                    cells[idx] = _xlsx_cell_text(c, shared, ns)
                    max_i = max(max_i, idx)
                if max_i < 0:
                    return []
                return [cells.get(i, "") for i in range(max_i + 1)]

            file_headers_raw = row_values(rows_xml[0])
            file_headers = [str(h) for h in file_headers_raw[: len(headers)]]
            if file_headers != headers:
                trimmed = [h for h in file_headers_raw if str(h).strip() != ""]
                if trimmed != headers:
                    return empty

            def data_row_iter():
                for row_el in rows_xml[1:]:
                    yield row_values(row_el)

            window = _collect_data_window(data_row_iter(), column_count=len(headers))
            return _columns_from_window(window, column_count=len(headers))
    except (OSError, zipfile.BadZipFile, ET.ParseError, KeyError, ValueError):
        return empty


def extract_samples_for_source(
    *,
    path: Path | str,
    expected_headers: Sequence[str],
    encoding: str | None = None,
    xlsx_sheet: object = None,
    media_type: str = "",
    original_name: str = "",
) -> list[list[str]]:
    """Pick CSV vs XLSX using path / media / name; fail open to empty samples."""

    p = Path(path)
    name = (original_name or p.name).lower()
    media = (media_type or "").lower()
    sheet_index = 0
    if isinstance(xlsx_sheet, int):
        sheet_index = xlsx_sheet
    elif isinstance(xlsx_sheet, dict) and "index" in xlsx_sheet:
        try:
            sheet_index = int(xlsx_sheet["index"])
        except (TypeError, ValueError):
            sheet_index = 0

    if name.endswith(".xlsx") or "spreadsheet" in media or media.endswith("xlsx"):
        return extract_xlsx_samples(
            p, expected_headers=expected_headers, sheet_index=sheet_index
        )
    return extract_csv_samples(
        p, expected_headers=expected_headers, encoding=encoding or "utf-8"
    )
