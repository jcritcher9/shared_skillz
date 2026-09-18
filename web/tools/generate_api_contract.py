"""Regenerate strict Pydantic models and constants from canonical OpenAPI."""

from __future__ import annotations

import argparse
from hashlib import sha256
from importlib.metadata import version
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import urllib.request

GENERATOR = "datamodel-code-generator/0.48.0"
GENERATOR_PACKAGE_VERSION = "0.48.0"
EXPECTED_SHA256 = "4d3411b669f19c265beade6b8a2877da087931663e6b360ed6c1816eb209ae86"
PRODUCT_SCHEMAS = (
    "ListImportCreate",
    "AccountListImportCreate",
    "SingleDatasetImportCreate",
    "DuplicateResolutionCreate",
)
CATALOG_SCHEMAS = {
    "easyimports.list_import": "ListImportCatalogEntry",
    "easyimports.account_list_import": "AccountListCatalogEntry",
    "easyimports.single_dataset_import": "SingleDatasetCatalogEntry",
    "easyimports.duplicate_resolution": "DuplicateResolutionCatalogEntry",
}
DECISION_SCHEMAS = (
    "ValidationDecisionResource",
    "ListDuplicateDecisionResource",
    "CrmPersonMatchDecisionResource",
    "CrmAccountMatchDecisionResource",
    "UploadedAccountIdDisagreementResource",
    "UploadedPersonIdDisagreementResource",
    "AccountReviewDecisionResource",
    "PersonReviewDecisionResource",
)


def _tuple_source(name: str, values: list[str]) -> str:
    body = "".join(f"    {value!r},\n" for value in values)
    return f"{name} = (\n{body})\n"


def _resolve(schemas: dict, value: dict) -> dict:
    if "$ref" not in value:
        return value
    return schemas[value["$ref"].rsplit("/", 1)[-1]]


def _enum_values(schema: dict) -> list[str]:
    if "enum" in schema:
        return list(schema["enum"])
    if "const" in schema:
        return [schema["const"]]
    variants = schema.get("anyOf") or schema.get("oneOf") or []
    result: list[str] = []
    for variant in variants:
        if variant.get("type") == "null":
            continue
        result.extend(_enum_values(variant))
    return result


def contract_metadata(document: dict) -> tuple[str, dict]:
    canonical = json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    digest = sha256(canonical).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(
            f"OpenAPI checksum mismatch: expected {EXPECTED_SHA256}, got {digest}"
        )
    schemas = document["components"]["schemas"]
    products = [
        schemas[name]["properties"]["product_key"]["const"] for name in PRODUCT_SCHEMAS
    ]
    decisions = [
        schemas[name]["properties"]["decision_type"]["const"]
        for name in DECISION_SCHEMAS
    ]
    statuses = schemas["WorkflowResource"]["properties"]["status"]["enum"]
    modes = schemas["EffectIntentResource"]["properties"]["maximum_mode"]["enum"]
    track_modes: dict[str, dict[str, list[str]]] = {}
    for product_key, catalog_name in CATALOG_SCHEMAS.items():
        entry = schemas[catalog_name]
        tracks = _resolve(schemas, entry["properties"]["tracks"])
        track_modes[product_key] = {
            name: _enum_values(_resolve(schemas, prop)["items"])
            for name, prop in sorted(tracks["properties"].items())
        }
    return digest, {
        "products": products,
        "decisions": decisions,
        "statuses": statuses,
        "modes": modes,
        "track_modes": track_modes,
    }


def constants_source(document: dict) -> str:
    digest, values = contract_metadata(document)
    return (
        '"""Generated EasyImports OpenAPI v1 constants. DO NOT EDIT."""\n\n'
        f"GENERATOR = {GENERATOR!r}\n"
        f"API_VERSION = {document['info']['version']!r}\n"
        f"OPENAPI_SHA256 = {digest!r}\n\n"
        + _tuple_source("PRODUCT_KEYS", values["products"])
        + _tuple_source("WORKFLOW_STATUSES", values["statuses"])
        + _tuple_source("DECISION_TYPES", values["decisions"])
        + _tuple_source("AUTHORIZATION_MODES", values["modes"])
        + "PRODUCT_TRACK_MODES = "
        + repr(values["track_modes"])
        + "\n"
    )


def generate_models(document: dict, output: Path) -> None:
    digest, _ = contract_metadata(document)
    installed = version("datamodel-code-generator")
    if installed != GENERATOR_PACKAGE_VERSION:
        raise SystemExit(
            f"Generator mismatch: expected {GENERATOR_PACKAGE_VERSION}, got {installed}"
        )
    with tempfile.TemporaryDirectory() as directory:
        schema_path = Path(directory) / "openapi.json"
        schema_path.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":")),
            encoding="utf-8",
        )
        subprocess.run(
            [
                sys.executable,
                "-m",
                "datamodel_code_generator",
                "--input",
                str(schema_path),
                "--input-file-type",
                "openapi",
                "--output",
                str(output),
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--target-python-version",
                "3.12",
                "--use-standard-collections",
                "--use-annotated",
                "--extra-fields",
                "forbid",
                "--enum-field-as-literal",
                "all",
                "--disable-timestamp",
                "--enable-version-header",
                "--custom-file-header",
                '"""Generated from canonical EasyImports OpenAPI. DO NOT EDIT."""',
            ],
            check=True,
        )
    source = output.read_text(encoding="utf-8")
    # datamodel-code-generator 0.48.0 emits an invalid runtime expression for
    # nullable recursive references under Python 3.12 (``"JsonValue" | None``).
    # Normalize that known generator defect deterministically as part of generation.
    # Generator import lines vary slightly by schema surface; normalize Optional.
    for typing_import in (
        "from typing import Annotated, Literal, Union",
        "from typing import Annotated, Any, Literal, Union",
    ):
        if typing_import in source and "Optional" not in typing_import:
            source = source.replace(
                typing_import,
                typing_import.replace("Union", "Optional, Union"),
                1,
            )
            break
    source = source.replace('"JsonValue" | None', 'Optional["JsonValue"]')
    marker = "from __future__ import annotations\n"
    metadata = f"\nGENERATOR = {GENERATOR!r}\n" f"OPENAPI_SHA256 = {digest!r}\n"
    if marker not in source:
        raise SystemExit("Generated models lack the expected future import.")
    output.write_text(
        source.replace(marker, marker + metadata, 1),
        encoding="utf-8",
        newline="\n",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--schema-url", default="http://127.0.0.1:8000/openapi.json")
    base = Path(__file__).resolve().parents[1] / "importer"
    parser.add_argument(
        "--constants-output",
        type=Path,
        default=base / "api_contract_generated.py",
    )
    parser.add_argument(
        "--models-output",
        type=Path,
        default=base / "api_models_generated.py",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify checked-in generated files without modifying them.",
    )
    args = parser.parse_args()
    with urllib.request.urlopen(args.schema_url, timeout=10) as response:
        document = json.load(response)
    constants = constants_source(document)
    if args.check:
        with tempfile.TemporaryDirectory() as directory:
            generated_models = Path(directory) / "api_models_generated.py"
            generate_models(document, generated_models)
            mismatches = []
            if args.constants_output.read_text(encoding="utf-8") != constants:
                mismatches.append(str(args.constants_output))
            if args.models_output.read_text(
                encoding="utf-8"
            ) != generated_models.read_text(encoding="utf-8"):
                mismatches.append(str(args.models_output))
        if mismatches:
            raise SystemExit(
                "Generated API contract files are stale: " + ", ".join(mismatches)
            )
        return
    args.constants_output.write_text(constants, encoding="utf-8", newline="\n")
    generate_models(document, args.models_output)


if __name__ == "__main__":
    main()
