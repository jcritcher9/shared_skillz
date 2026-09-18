"""Django forms for the HTTP-backed EasyImports workflow client."""

from __future__ import annotations

from datetime import date
import csv
import io
import json
from typing import Any

from django import forms

from .constants import (
    ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS,
    MODE_LABELS,
    OPT_IN_WRITE_TRACKS,
    PRODUCT_SOURCE_ROLES,
    SourceRole,
    TRACK_HELP_TEXT,
    TRACK_LABELS,
    display_target_label,
)
from .vocabulary_intent import (
    DEFAULT_VOCABULARY,
    VOCABULARY_CHOICES,
    VOCABULARY_UI_COPY,
    VocabularyIntentError,
    contacts_only_help_text,
    validate_vocabulary_for_setup,
)

ALLOWED_UPLOAD_SUFFIXES = (".csv", ".xlsx")
MODE_RANK = {"disabled": 0, "preview": 1, "dry_run": 2, "execute": 3}
CANDIDATE_GROUPS_UPLOAD_MAX_BYTES = 10 * 1024 * 1024
CANDIDATE_GROUPS_LONG_REQUIRED_COLUMNS = ("group_id", "member_id")
CANDIDATE_GROUPS_LONG_OPTIONAL_COLUMNS = ("group_label",)
DEFERRED_DUPLICATE_GROUPS_EXPORT_CONTRACT = "easyimports.deferred_duplicate_groups.v1"
DEFERRED_DUPLICATE_GROUPS_COLUMNS = (
    "export_contract",
    "source_connection_id",
    "source_provider_key",
    "entity_family",
    "group_id",
    "member_ids",
)


class CatalogCompatibilityError(ValueError):
    pass


class ProductSelectionForm(forms.Form):
    """Operator intent wizard — does not submit an authoritative product key."""

    form_token = forms.CharField(widget=forms.HiddenInput)
    operator_label = forms.CharField(
        max_length=160,
        label="Your name",
        help_text="Shown on any review decisions made during this import.",
    )
    entity = forms.ChoiceField(
        label="What are you preparing?",
        choices=(
            ("accounts", "Accounts"),
            ("people", "People"),
        ),
    )
    operation = forms.ChoiceField(
        label="What should EasyImports do?",
        # Desire #6 / D7: match-first default; clean-only secondary with frozen label.
        choices=(
            ("crm_matching", "Match it against my CRM"),
            ("clean_only", "Clean and prepare my file (no matching)"),
        ),
        initial="crm_matching",
    )
    reference_source = forms.ChoiceField(
        required=False,
        label="Where is the CRM data?",
        # Desire #7: only real matching sources (no “Not needed for clean-only”).
        choices=(
            ("uploaded", "Upload CRM exports"),
            ("connected_crm", "Use a connected CRM"),
        ),
        help_text=(
            "Upload CRM exports, or use a connected CRM so EasyImports loads "
            "references from that connection during matching."
        ),
    )
    connection_id = forms.ChoiceField(
        required=False,
        label="CRM connection",
        help_text="Required when matching against a connected CRM.",
    )
    people_output = forms.ChoiceField(
        required=False,
        label="Prepare these records as",
        choices=(
            ("", "Not needed for this setup"),
            ("contact", "Contacts"),
            ("lead", "Leads"),
        ),
        help_text="Required only when cleaning a people file without CRM matching.",
    )
    # CMX-1: vocabulary intent (mapping field labels / inbound dialect preference).
    # Independent of match and export; clean-only may set CRM vocabulary without connection.
    vocabulary = forms.ChoiceField(
        required=False,
        label="Field names for mapping",
        choices=VOCABULARY_CHOICES,
        initial=DEFAULT_VOCABULARY,
        help_text=VOCABULARY_UI_COPY,
    )
    # D6 / Phase 7B: not shown to operators; default catalog target (or connected
    # override in clean()). Retained for API create_workflow.
    target_provider_id = forms.ChoiceField(widget=forms.HiddenInput)
    setup_revision = forms.IntegerField(widget=forms.HiddenInput)

    def __init__(
        self,
        *args,
        targets: list[dict],
        connections: list[dict] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self._connections_by_id: dict[str, dict] = {}
        self._catalog_target_ids: list[str] = [
            str(item.get("target_provider_id") or "").strip()
            for item in targets
            if str(item.get("target_provider_id") or "").strip()
        ]
        connected = [
            item for item in (connections or []) if item.get("status") == "connected"
        ]
        self._connections_by_id = {
            str(item.get("connection_id") or ""): item for item in connected
        }
        self.fields["connection_id"].choices = [("", "Select a connected CRM")] + [
            (
                item["connection_id"],
                (
                    f"{item.get('provider_label') or item.get('provider_key')}"
                    f" — {item.get('display_label') or item['connection_id']}"
                ),
            )
            for item in connected
        ]
        if not connected:
            self.fields["connection_id"].choices = [
                ("", "No connected CRM available — connect one first")
            ]
        self.fields["target_provider_id"].choices = [
            (
                item["target_provider_id"],
                display_target_label(item["target_provider_id"]),
            )
            for item in targets
        ]
        # D8: default CRM-data source when blank / new setup.
        if not self.is_bound:
            current_ref = self.initial.get("reference_source")
            if current_ref not in {"uploaded", "connected_crm"}:
                self.initial["reference_source"] = (
                    "connected_crm" if connected else "uploaded"
                )
            if not self.initial.get("operation"):
                self.initial["operation"] = "crm_matching"
            if not self.initial.get("vocabulary"):
                self.initial["vocabulary"] = DEFAULT_VOCABULARY
            if not self.initial.get("target_provider_id") and self._catalog_target_ids:
                self.initial["target_provider_id"] = self._catalog_target_ids[0]
        # Bound posts without target (legacy tests / odd clients): still default.
        if (
            self.is_bound
            and not (self.data.get("target_provider_id") or "").strip()
            and self._catalog_target_ids
        ):
            mutable = self.data.copy()
            mutable["target_provider_id"] = self._catalog_target_ids[0]
            self.data = mutable

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        if data.get("setup_revision") is None:
            self.add_error(
                "setup_revision",
                "This setup form is incomplete. Reload it before continuing.",
            )
            return data
        operation = data.get("operation")
        reference_source = data.get("reference_source") or ""
        people_output = data.get("people_output") or ""
        connection_id = str(data.get("connection_id") or "").strip()
        match_provider_key = ""
        # Internal default catalog target when hidden field omitted (D6).
        if not str(data.get("target_provider_id") or "").strip():
            if self._catalog_target_ids:
                data["target_provider_id"] = self._catalog_target_ids[0]
        if operation == "clean_only":
            data["reference_source"] = "none"
            data["connection_id"] = ""
            connection_id = ""
            if data.get("entity") != "people":
                data["people_output"] = ""
        elif operation == "crm_matching":
            data["people_output"] = ""
            if reference_source not in {"uploaded", "connected_crm"}:
                self.add_error(
                    "reference_source",
                    "Choose uploaded CRM exports or a connected CRM for matching.",
                )
            if reference_source == "connected_crm":
                if not connection_id:
                    self.add_error(
                        "connection_id",
                        "Choose an active CRM connection for connected matching.",
                    )
                else:
                    connected = self._connections_by_id.get(connection_id)
                    if connected is None:
                        self.add_error(
                            "connection_id",
                            "The selected CRM connection is not available.",
                        )
                    else:
                        target_id = str(
                            connected.get("execution_target_provider_id") or ""
                        ).strip()
                        if not target_id:
                            self.add_error(
                                "connection_id",
                                "This CRM connection has no execution target for "
                                "import matching.",
                            )
                        else:
                            # Connected path freezes the connection-derived target.
                            data["target_provider_id"] = target_id
                            data["connection_id"] = connection_id
                            match_provider_key = str(
                                connected.get("provider_key") or ""
                            ).strip()
            else:
                data["connection_id"] = ""
                connection_id = ""
        if data.get("entity") == "people" and operation == "clean_only":
            if people_output not in {"contact", "lead"}:
                self.add_error(
                    "people_output",
                    "Choose Contacts or Leads for people clean-only.",
                )
        # CMX-1: persist vocabulary; fail closed when match provider ≠ vocabulary.
        try:
            data["vocabulary"] = validate_vocabulary_for_setup(
                operation=str(operation or ""),
                vocabulary=data.get("vocabulary"),
                connection_id=connection_id,
                match_provider_key=match_provider_key or None,
            )
        except VocabularyIntentError as exc:
            self.add_error("vocabulary", str(exc))
        return data


class SourceUploadForm(forms.Form):
    form_token = forms.CharField(widget=forms.HiddenInput)
    role = forms.ChoiceField(widget=forms.HiddenInput)
    file = forms.FileField(label="File")
    csv_encoding = forms.CharField(
        required=False,
        initial="utf-8-sig",
        help_text="Used only for CSV files.",
    )
    xlsx_sheet_index = forms.IntegerField(
        required=False,
        min_value=0,
        initial=0,
        help_text="Zero-based XLSX sheet index.",
    )

    def __init__(
        self,
        *args,
        product_key: str | None = None,
        allowed_roles: tuple[SourceRole, ...] | list[SourceRole] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        if allowed_roles is not None:
            roles = tuple(allowed_roles)
        else:
            roles = PRODUCT_SOURCE_ROLES.get(product_key or "", ())
        self.fields["role"].choices = [(role.key, role.label) for role in roles]
        self.fields["file"].widget.attrs.update({"accept": ".csv,.xlsx"})

    def clean_file(self):
        uploaded = self.cleaned_data["file"]
        name = (uploaded.name or "").lower()
        if not name.endswith(ALLOWED_UPLOAD_SUFFIXES):
            raise forms.ValidationError("Uploads must be CSV or XLSX files.")
        return uploaded


class CheckoutForm(forms.Form):
    email = forms.EmailField(
        required=False,
        label="Billing email",
        help_text="Optional. Pre-fills your WooCommerce order.",
        widget=forms.EmailInput(attrs={"placeholder": "you@company.com"}),
    )


class CrmDuplicateJourneyForm(forms.Form):
    """Start redesigned CRM duplicate journey (Phase 4A two-source path).

    Primary sources: find-in-CRM (acquire_all) or upload ordinary records
    (uploaded_population). Pre-grouped candidate_upload remains accepted only
    for advanced/API compatibility posts, not as a primary UI choice.
    """

    connection_id = forms.ChoiceField(label="Connected CRM")
    entity_family = forms.ChoiceField(
        label="What should we check?",
        choices=(
            ("company", "Companies/Accounts"),
            ("person", "People"),
        ),
        initial="company",
    )
    source_mode = forms.ChoiceField(
        label="Where should EasyImports look?",
        choices=(
            (
                "acquire_all",
                "Find duplicates in CRM",
            ),
            (
                "uploaded_population",
                "Upload records to analyze",
            ),
        ),
        initial="acquire_all",
        widget=forms.RadioSelect,
        help_text=(
            "Find-in-CRM reads active records for the selected object and "
            "discovers groups. Upload an ordinary CRM export of record IDs "
            "when you already know which population to analyze."
        ),
    )
    # Ordinary ungrouped population file (Phase 4A primary upload path).
    population_file = forms.FileField(
        required=False,
        label="Records to analyze (CSV or Excel)",
        help_text=(
            "One row per CRM record. Any header names are fine — you will "
            "map the Record ID column on the next step."
        ),
        widget=forms.FileInput(
            attrs={"accept": ".csv,.xlsx", "class": "file-input"}
        ),
    )
    # Legacy pre-grouped file (compatibility / advanced only; not primary UI).
    candidate_groups_file = forms.FileField(
        required=False,
        label="Pre-grouped potential duplicates (advanced)",
        help_text=(
            "Advanced compatibility input. One row per member with columns "
            "group_id and member_id. Prefer Upload records to analyze for "
            "ordinary populations."
        ),
        widget=forms.FileInput(attrs={"accept": ".csv,.xlsx"}),
    )
    # Advanced: JSON remains supported for API/test callers (collapsed in UI).
    candidate_groups_json = forms.CharField(
        required=False,
        label="Candidate groups as JSON",
        help_text=(
            "Advanced alternative to a spreadsheet. Example: "
            '[{"group_id": "company-group-1", "member_ids": ["A1", "A2", "A3"]}]'
        ),
        widget=forms.Textarea(
            attrs={
                "rows": 6,
                "spellcheck": "false",
                "placeholder": (
                    '[{"group_id": "company-group-1", '
                    '"member_ids": ["A1", "A2", "A3"]}]'
                ),
            }
        ),
    )
    # H1-A selected-IDs handoff posts through the CRM query results page, not
    # this operator form. Fields remain for clean() when tests/tools post the
    # mode, and for progressive-disclosure safety if re-enabled later.
    selected_ids = forms.CharField(
        required=False,
        label="Selected record IDs (comma or newline separated)",
        help_text=(
            "Use the handoff form on a CRM query results page. "
            "EasyImports rereads them and discovers groups. Max 10,000 IDs."
        ),
        widget=forms.Textarea(attrs={"rows": 4, "spellcheck": "false"}),
    )
    query_id = forms.CharField(
        required=False,
        label="Source query ID",
        help_text=(
            "Required with selected-IDs handoff from a completed CRM query "
            "on the same connection."
        ),
    )
    query_result_digest = forms.CharField(
        required=False,
        label="Source query result digest",
        help_text="Must match the frozen CRM query snapshot.",
    )
    # Signed form token freezes form_instance for exact-retry (Phase 4A rem).
    form_token = forms.CharField(widget=forms.HiddenInput, required=True)
    # Phase 7C: optional high-confidence auto-merge (floor 90; default off).
    matching_mode = forms.ChoiceField(
        label="Matching detail",
        choices=(
            ("default", "Default matching"),
            ("exact_only", "Exact matches only"),
        ),
        initial="default",
        required=False,
        widget=forms.RadioSelect,
        help_text=(
            "Default matching uses every current rule for this entity. "
            "Exact matches only skips Company name-and-geography fuzzy "
            "matching. People keep the same results for both choices today."
        ),
    )
    auto_merge_high_confidence = forms.BooleanField(
        required=False,
        initial=False,
        label="Auto-merge high-confidence groups",
        help_text=(
            "When enabled, groups at or above the threshold skip manual "
            "review. No CRM changes until you authorize."
        ),
    )
    auto_merge_min_confidence = forms.IntegerField(
        required=False,
        min_value=90,
        max_value=100,
        initial=90,
        label="Confidence threshold",
        help_text=(
            "Integer from 90 to 100. Values below 90 are rejected, not lowered."
        ),
        widget=forms.NumberInput(
            attrs={"min": "90", "max": "100", "step": "1", "inputmode": "numeric"}
        ),
    )
    # Not shown on the redesigned start screen; ceiling is derived from the
    # selected connection's maximum_authorization.duplicate_execution.
    duplicate_execution_maximum = forms.ChoiceField(
        label="Maximum CRM change level",
        choices=(
            ("dry_run", "Dry run only (recommended)"),
            ("execute", "Allow applying merges after review"),
            ("preview", "Preview only"),
            ("disabled", "Disabled"),
        ),
        initial="dry_run",
        required=False,
        help_text=(
            "Not an operator start field. Server freezes the connection "
            "capability ceiling for later merge authorization."
        ),
    )

    def __init__(self, *args, connections: list[dict], **kwargs):
        super().__init__(*args, **kwargs)
        connected = [item for item in connections if item.get("status") == "connected"]
        self._connections_by_id = {
            str(item.get("connection_id") or ""): item for item in connected
        }
        self.fields["connection_id"].choices = [
            (
                item["connection_id"],
                (
                    f"{item.get('provider_label') or item.get('provider_key')}"
                    f" — {item.get('display_label') or item['connection_id']}"
                ),
            )
            for item in connected
        ]
        if not connected:
            self.fields["connection_id"].choices = [("", "No connected CRM available")]
            self.fields["connection_id"].disabled = True
        # Path C: live HubSpot is company-only. If every connected CRM is
        # HubSpot, hide People. Mixed catalogs still validate per-connection.
        only_hubspot = bool(connected) and all(
            item.get("provider_key") == "hubspot" for item in connected
        )
        if only_hubspot:
            self.fields["entity_family"].choices = (("company", "Companies/Accounts"),)
        # Accept selected_ids / candidate_upload when posted (H1-A / advanced
        # tests) without listing them as primary operator choices.
        posted_mode = ""
        if self.data is not None:
            posted_mode = str(self.data.get("source_mode") or "")
        if posted_mode in {"selected_ids", "candidate_upload"} or (
            self.initial
            and self.initial.get("source_mode") in {"selected_ids", "candidate_upload"}
        ):
            self.fields["source_mode"].choices = (
                (
                    "acquire_all",
                    "Find duplicates in CRM",
                ),
                (
                    "uploaded_population",
                    "Upload records to analyze",
                ),
                (
                    "candidate_upload",
                    "Upload pre-grouped potential duplicates (advanced)",
                ),
                (
                    "selected_ids",
                    "Selected CRM record IDs (from CRM query handoff)",
                ),
            )
        # Operator never chooses merge mode at start; field is ignored on clean.
        self.fields["duplicate_execution_maximum"].initial = "dry_run"

    def clean(self):
        cleaned = super().clean()
        connection_id = str(cleaned.get("connection_id") or "")
        entity_family = str(cleaned.get("entity_family") or "")
        connected = self._connections_by_id.get(connection_id) or {}
        if connected.get("provider_key") == "hubspot" and entity_family == "person":
            self.add_error(
                "entity_family",
                "HubSpot live Path C supports Companies only (Contact merge is disabled).",
            )
        mode = cleaned.get("matching_mode")
        if mode in (None, ""):
            cleaned["matching_mode"] = "default"
        elif mode not in {"default", "exact_only"}:
            self.add_error(
                "matching_mode",
                "Matching detail must be Default matching or Exact matches only.",
            )
        # Phase 7C: optional auto-merge threshold (product floor 90).
        # Fail closed below 90 / above 100; never silent clamp. Off → None.
        enabled = bool(cleaned.get("auto_merge_high_confidence"))
        threshold = cleaned.get("auto_merge_min_confidence")
        if enabled:
            if threshold is None:
                self.add_error(
                    "auto_merge_min_confidence",
                    "Enter a confidence threshold from 90 to 100 when "
                    "auto-merge is enabled.",
                )
            else:
                try:
                    value = int(threshold)
                except (TypeError, ValueError):
                    self.add_error(
                        "auto_merge_min_confidence",
                        "Confidence threshold must be an integer from 90 to 100.",
                    )
                else:
                    if value < 90 or value > 100:
                        self.add_error(
                            "auto_merge_min_confidence",
                            "Confidence threshold must be an integer from 90 to 100 "
                            "(values below 90 are rejected, not lowered to 90).",
                        )
                    else:
                        cleaned["auto_merge_min_confidence"] = value
        else:
            cleaned["auto_merge_min_confidence"] = None
        mode = cleaned.get("source_mode")
        raw = (cleaned.get("candidate_groups_json") or "").strip()
        uploaded = cleaned.get("candidate_groups_file")
        population = cleaned.get("population_file")
        # Merge ceiling is applied in the view from connection capability;
        # ignore any posted value so the start form cannot elevate writes.
        cleaned["duplicate_execution_maximum"] = None
        if mode == "uploaded_population":
            # Ordinary ungrouped population — no group_id/member_id required.
            cleaned["candidate_groups"] = None
            cleaned["candidate_groups_json"] = ""
            cleaned["candidate_groups_file"] = None
            cleaned["selected_ids_list"] = []
            cleaned["selected_ids"] = ""
            cleaned["query_id"] = None
            cleaned["query_result_digest"] = None
            if population is None:
                self.add_error(
                    "population_file",
                    "Upload a CSV or Excel file of records to analyze.",
                )
                return cleaned
            name = str(getattr(population, "name", "") or "").lower()
            if not (name.endswith(".csv") or name.endswith(".xlsx")):
                self.add_error(
                    "population_file",
                    "Upload must be a CSV or Excel (.xlsx) file.",
                )
            size = getattr(population, "size", None)
            if size is not None and size > CANDIDATE_GROUPS_UPLOAD_MAX_BYTES:
                self.add_error(
                    "population_file",
                    "Upload must be 10 MB or smaller.",
                )
            cleaned["population_file"] = population
            return cleaned
        if mode == "candidate_upload":
            if raw and uploaded is not None:
                self.add_error(
                    "candidate_groups_file",
                    "Provide candidate groups as JSON or a spreadsheet, not both.",
                )
                return cleaned
            if not raw and uploaded is None:
                self.add_error(
                    "candidate_groups_file",
                    "Upload a potential-duplicates spreadsheet (CSV or Excel "
                    "with group_id and member_id columns), a remaining-groups "
                    "CSV, or use Advanced JSON.",
                )
                return cleaned
            if uploaded is not None:
                try:
                    cleaned["candidate_groups"] = self._clean_candidate_groups_file(
                        uploaded,
                        connection_id=connection_id,
                        provider_key=str(connected.get("provider_key") or ""),
                        entity_family=entity_family,
                    )
                except forms.ValidationError as exc:
                    self.add_error("candidate_groups_file", exc)
                return cleaned
            try:
                groups = json.loads(raw)
            except json.JSONDecodeError as exc:
                self.add_error(
                    "candidate_groups_json",
                    f"Candidate groups must be valid JSON: {exc.msg}.",
                )
                return cleaned
            if not isinstance(groups, list) or not groups:
                self.add_error(
                    "candidate_groups_json",
                    "Candidate groups must be a non-empty JSON list.",
                )
                return cleaned
            normalized = []
            for index, group in enumerate(groups):
                if not isinstance(group, dict):
                    self.add_error(
                        "candidate_groups_json",
                        f"Group {index + 1} must be an object with group_id and member_ids.",
                    )
                    return cleaned
                group_id = str(group.get("group_id") or "").strip()
                member_ids = group.get("member_ids")
                if not group_id or not isinstance(member_ids, list):
                    self.add_error(
                        "candidate_groups_json",
                        f"Group {index + 1} needs group_id and a member_ids list.",
                    )
                    return cleaned
                members = [str(value).strip() for value in member_ids]
                if len(members) < 2 or any(not value for value in members):
                    self.add_error(
                        "candidate_groups_json",
                        f"Group {group_id!r} needs at least two non-empty member IDs.",
                    )
                    return cleaned
                if len(members) != len(set(members)):
                    self.add_error(
                        "candidate_groups_json",
                        f"Group {group_id!r} has duplicate member IDs.",
                    )
                    return cleaned
                normalized.append({"group_id": group_id, "member_ids": members})
            try:
                self._assert_no_cross_group_member_overlap(normalized)
            except forms.ValidationError as exc:
                self.add_error("candidate_groups_json", exc)
                return cleaned
            cleaned["candidate_groups"] = normalized
            # Progressive panels may leave stale selected-ID fields in the POST
            # when the operator previously used another mode; ignore them.
            cleaned["selected_ids_list"] = []
            cleaned["selected_ids"] = ""
            cleaned["query_id"] = None
            cleaned["query_result_digest"] = None
        elif mode == "selected_ids":
            # Ignore upload-mode leftovers from progressive disclosure.
            cleaned["candidate_groups"] = None
            cleaned["candidate_groups_json"] = ""
            cleaned["candidate_groups_file"] = None
            raw_ids = str(cleaned.get("selected_ids") or "")
            ids = [
                part.strip()
                for part in raw_ids.replace("\n", ",").split(",")
                if part.strip()
            ]
            if not ids:
                self.add_error(
                    "selected_ids",
                    "Enter at least one CRM record ID to hand off.",
                )
            elif len(ids) != len(set(ids)):
                self.add_error("selected_ids", "Selected IDs must be unique.")
            elif len(ids) > 10_000:
                self.add_error(
                    "selected_ids",
                    "Selected IDs exceed the maximum of 10,000.",
                )
            cleaned["selected_ids_list"] = ids
            query_id = str(cleaned.get("query_id") or "").strip()
            query_digest = str(cleaned.get("query_result_digest") or "").strip()
            if not query_id:
                self.add_error(
                    "query_id",
                    "Selected-IDs handoff requires the completed source CRM query ID.",
                )
            if not query_digest:
                self.add_error(
                    "query_result_digest",
                    "Selected-IDs handoff requires the source query result digest.",
                )
            cleaned["query_id"] = query_id or None
            cleaned["query_result_digest"] = query_digest or None
        else:
            # acquire_all: ignore upload / selected-ID leftovers so switching
            # discovery mode cannot trap the operator behind hidden errors.
            cleaned["candidate_groups"] = None
            cleaned["candidate_groups_json"] = ""
            cleaned["candidate_groups_file"] = None
            cleaned["selected_ids_list"] = []
            cleaned["selected_ids"] = ""
            cleaned["query_id"] = None
            cleaned["query_result_digest"] = None
        return cleaned

    def _clean_candidate_groups_file(
        self,
        uploaded,
        *,
        connection_id: str,
        provider_key: str,
        entity_family: str,
    ) -> list[dict[str, Any]]:
        """Parse candidate-upload files: long spreadsheet or remaining-groups CSV."""

        name = str(uploaded.name or "").lower()
        size = getattr(uploaded, "size", None)
        if size is not None and size > CANDIDATE_GROUPS_UPLOAD_MAX_BYTES:
            raise forms.ValidationError("Candidate upload must be 10 MB or smaller.")
        if name.endswith(".xlsx"):
            return self._clean_candidate_groups_long_spreadsheet(uploaded)
        if not name.endswith(".csv"):
            raise forms.ValidationError(
                "Candidate upload must be a CSV or Excel (.xlsx) file."
            )
        try:
            text = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError(
                "Candidate upload CSV must use UTF-8 encoding."
            ) from exc
        reader = csv.DictReader(io.StringIO(text))
        fieldnames = tuple(reader.fieldnames or ())
        if fieldnames == DEFERRED_DUPLICATE_GROUPS_COLUMNS:
            return self._clean_deferred_groups_csv_text(
                text,
                connection_id=connection_id,
                provider_key=provider_key,
                entity_family=entity_family,
            )
        return self._clean_candidate_groups_long_csv_text(text)

    def _clean_candidate_groups_long_spreadsheet(
        self,
        uploaded,
    ) -> list[dict[str, Any]]:
        """Parse long-format candidate groups from CSV or Excel rows."""

        name = str(uploaded.name or "").lower()
        if name.endswith(".xlsx"):
            rows, columns = self._read_candidate_long_xlsx_rows(uploaded)
            return self._normalize_candidate_long_rows(rows, columns=columns)
        if name.endswith(".csv"):
            try:
                text = uploaded.read().decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise forms.ValidationError(
                    "Candidate upload CSV must use UTF-8 encoding."
                ) from exc
            return self._clean_candidate_groups_long_csv_text(text)
        raise forms.ValidationError(
            "Candidate upload must be a CSV or Excel (.xlsx) file."
        )

    def _clean_candidate_groups_long_csv_text(
        self,
        text: str,
    ) -> list[dict[str, Any]]:
        stream = io.StringIO(text)
        # Detect duplicate headers before DictReader collapses them.
        header_line = stream.readline()
        if not header_line.strip():
            raise forms.ValidationError(
                "Candidate spreadsheet is missing a header row."
            )
        # csv.reader handles quoted headers consistently with DictReader.
        header_reader = csv.reader(io.StringIO(header_line))
        try:
            raw_headers = next(header_reader)
        except StopIteration as exc:
            raise forms.ValidationError(
                "Candidate spreadsheet is missing a header row."
            ) from exc
        headers = [str(value or "").strip() for value in raw_headers]
        if not headers or all(not value for value in headers):
            raise forms.ValidationError(
                "Candidate spreadsheet is missing a header row."
            )
        if len(headers) != len(set(headers)):
            raise forms.ValidationError(
                "Candidate spreadsheet has duplicate column headers."
            )
        stream.seek(0)
        reader = csv.DictReader(stream)
        columns = tuple(str(name or "").strip() for name in (reader.fieldnames or ()))
        rows: list[dict[str, str]] = []
        for index, row in enumerate(reader, start=2):
            rows.append(
                {
                    str(key or "").strip(): (
                        "" if value is None else str(value).strip()
                    )
                    for key, value in row.items()
                }
            )
            # Preserve original row numbers for fail-closed messages.
            rows[-1]["__row_number__"] = str(index)
        return self._normalize_candidate_long_rows(rows, columns=columns)

    def _read_candidate_long_xlsx_rows(
        self,
        uploaded,
    ) -> tuple[list[dict[str, str]], tuple[str, ...]]:
        """Read long-format Excel rows.

        Headers are inspected with openpyxl **before** pandas so duplicate
        column names fail closed (pandas renames duplicates and would hide them).
        """

        import openpyxl
        import pandas as pd

        raw = uploaded.read()
        try:
            workbook = openpyxl.load_workbook(
                io.BytesIO(raw),
                read_only=True,
                data_only=True,
            )
        except Exception as exc:  # noqa: BLE001 — surface as field error
            raise forms.ValidationError(
                f"Could not read candidate Excel file: {exc}."
            ) from exc
        try:
            sheet = workbook.active
            if sheet is None:
                raise forms.ValidationError(
                    "Candidate spreadsheet is missing a header row."
                )
            header_cells = next(
                sheet.iter_rows(min_row=1, max_row=1, values_only=True),
                None,
            )
        finally:
            workbook.close()
        if header_cells is None:
            raise forms.ValidationError(
                "Candidate spreadsheet is missing a header row."
            )
        # Drop trailing blank header cells common in exported workbooks.
        header_values = list(header_cells)
        while header_values and (
            header_values[-1] is None or str(header_values[-1]).strip() == ""
        ):
            header_values.pop()
        headers = [
            "" if value is None else str(value).strip() for value in header_values
        ]
        if not headers or all(not value for value in headers):
            raise forms.ValidationError(
                "Candidate spreadsheet is missing a header row."
            )
        if len(headers) != len(set(headers)):
            raise forms.ValidationError(
                "Candidate spreadsheet has duplicate column headers."
            )
        columns = tuple(headers)
        try:
            frame = pd.read_excel(
                io.BytesIO(raw),
                dtype=str,
                engine="openpyxl",
            )
        except Exception as exc:  # noqa: BLE001 — surface as field error
            raise forms.ValidationError(
                f"Could not read candidate Excel file: {exc}."
            ) from exc
        # Align pandas column names with openpyxl-stripped headers. Pandas keeps
        # raw header strings (e.g. " group_id "), while validation uses strip().
        frame = frame.rename(
            columns={name: str(name).strip() for name in frame.columns.tolist()}
        )
        frame_columns = [str(name) for name in frame.columns.tolist()]
        usable = [name for name in frame_columns if name in set(columns)]
        if set(usable) != set(columns):
            # Should not happen when headers are unique; fail closed if frame drifts.
            raise forms.ValidationError(
                "Candidate Excel columns could not be read consistently."
            )
        rows: list[dict[str, str]] = []
        for offset, record in enumerate(frame.to_dict(orient="records")):
            row: dict[str, str] = {}
            for key in columns:
                value = record.get(key)
                if value is None or (isinstance(value, float) and pd.isna(value)):
                    cell = ""
                else:
                    cell = str(value).strip()
                    if cell.lower() == "nan":
                        cell = ""
                row[key] = cell
            # Excel data rows start at sheet row 2 when row 1 is the header.
            row["__row_number__"] = str(offset + 2)
            rows.append(row)
        return rows, columns

    def _normalize_candidate_long_rows(
        self,
        rows: list[dict[str, str]],
        *,
        columns: tuple[str, ...],
    ) -> list[dict[str, Any]]:
        required = set(CANDIDATE_GROUPS_LONG_REQUIRED_COLUMNS)
        present = {name for name in columns if name}
        missing = sorted(required - present)
        if missing:
            raise forms.ValidationError(
                "Candidate spreadsheet is missing required column(s): "
                + ", ".join(missing)
                + "."
            )
        # Optional columns (e.g. group_label) may be present without rejection.
        # Phase 1 does not emit them: the neutral candidate_groups /
        # CrmCandidateGroup contract only carries group_id + member_ids.
        # Carrying labels into review needs a later API/contract extension.
        optional_columns = frozenset(CANDIDATE_GROUPS_LONG_OPTIONAL_COLUMNS)
        if optional_columns & required:
            raise forms.ValidationError(
                "Internal candidate column contract is misconfigured."
            )
        # group_id order of first appearance; members order-stable within group.
        ordered_group_ids: list[str] = []
        members_by_group: dict[str, list[str]] = {}
        member_owner: dict[str, str] = {}
        data_rows = 0
        for row in rows:
            row_number = int(row.get("__row_number__") or 0) or None
            group_id = str(row.get("group_id") or "").strip()
            member_id = str(row.get("member_id") or "").strip()
            # Skip fully blank trailing rows (common in Excel exports).
            # Non-required columns include optional group_label and any extras.
            other_values = [
                str(row.get(col) or "").strip()
                for col in columns
                if col not in required
            ]
            if not group_id and not member_id and not any(other_values):
                continue
            data_rows += 1
            where = f"Row {row_number}" if row_number is not None else "A row"
            if not group_id:
                raise forms.ValidationError(f"{where} has an empty group_id.")
            if not member_id:
                raise forms.ValidationError(f"{where} has an empty member_id.")
            prior_group = member_owner.get(member_id)
            if prior_group is not None and prior_group != group_id:
                raise forms.ValidationError(
                    f"{where}: member ID {member_id!r} already appears in group "
                    f"{prior_group!r}; each member may belong to only one group."
                )
            if group_id not in members_by_group:
                ordered_group_ids.append(group_id)
                members_by_group[group_id] = []
            existing = members_by_group[group_id]
            if member_id in existing:
                raise forms.ValidationError(
                    f"{where}: group {group_id!r} has duplicate member ID "
                    f"{member_id!r}."
                )
            existing.append(member_id)
            member_owner[member_id] = group_id
        if data_rows == 0:
            raise forms.ValidationError(
                "Candidate spreadsheet must contain at least one data row."
            )
        normalized: list[dict[str, Any]] = []
        for group_id in ordered_group_ids:
            members = members_by_group[group_id]
            if len(members) < 2:
                raise forms.ValidationError(
                    f"Group {group_id!r} needs at least two non-empty member IDs."
                )
            # Emit only the neutral contract shape (no group_label).
            normalized.append({"group_id": group_id, "member_ids": members})
        return normalized

    def _assert_no_cross_group_member_overlap(
        self,
        groups: list[dict[str, Any]],
    ) -> None:
        """Fail closed when the same member_id appears in more than one group."""

        seen: dict[str, str] = {}
        for group in groups:
            group_id = str(group.get("group_id") or "")
            for member_id in group.get("member_ids") or ():
                member_text = str(member_id)
                prior = seen.get(member_text)
                if prior is not None:
                    raise forms.ValidationError(
                        f"Member ID {member_text!r} appears in groups {prior!r} "
                        f"and {group_id!r}; each member may belong to only one group."
                    )
                seen[member_text] = group_id

    def _clean_deferred_groups_csv(
        self,
        uploaded,
        *,
        connection_id: str,
        provider_key: str,
        entity_family: str,
    ) -> list[dict[str, Any]]:
        if not str(uploaded.name or "").lower().endswith(".csv"):
            raise forms.ValidationError("Remaining-groups upload must be a CSV file.")
        if (
            getattr(uploaded, "size", None) is not None
            and uploaded.size > CANDIDATE_GROUPS_UPLOAD_MAX_BYTES
        ):
            raise forms.ValidationError(
                "Remaining-groups CSV must be 10 MB or smaller."
            )
        try:
            text = uploaded.read().decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError(
                "Remaining-groups CSV must use UTF-8 encoding."
            ) from exc
        return self._clean_deferred_groups_csv_text(
            text,
            connection_id=connection_id,
            provider_key=provider_key,
            entity_family=entity_family,
        )

    def _clean_deferred_groups_csv_text(
        self,
        text: str,
        *,
        connection_id: str,
        provider_key: str,
        entity_family: str,
    ) -> list[dict[str, Any]]:
        reader = csv.DictReader(io.StringIO(text))
        if tuple(reader.fieldnames or ()) != DEFERRED_DUPLICATE_GROUPS_COLUMNS:
            raise forms.ValidationError(
                "Remaining-groups CSV columns do not match the EasyImports export."
            )
        normalized: list[dict[str, Any]] = []
        seen_groups: set[str] = set()
        for index, row in enumerate(reader, start=2):
            if row["export_contract"] != DEFERRED_DUPLICATE_GROUPS_EXPORT_CONTRACT:
                raise forms.ValidationError(
                    f"Row {index} is not a supported EasyImports remaining-groups export."
                )
            if row["source_connection_id"] != connection_id:
                raise forms.ValidationError(
                    "This CSV belongs to a different CRM connection."
                )
            if row["source_provider_key"] != provider_key:
                raise forms.ValidationError(
                    "This CSV belongs to a different CRM provider."
                )
            if row["entity_family"] != entity_family:
                raise forms.ValidationError(
                    "This CSV does not match the selected duplicate entity."
                )
            group_id = str(row["group_id"] or "").strip()
            try:
                members = json.loads(row["member_ids"])
            except json.JSONDecodeError as exc:
                raise forms.ValidationError(
                    f"Row {index} has invalid member_ids JSON."
                ) from exc
            if (
                not group_id
                or group_id in seen_groups
                or not isinstance(members, list)
                or len(members) < 2
            ):
                raise forms.ValidationError(
                    f"Row {index} needs a unique group ID and at least two members."
                )
            member_ids = [str(value).strip() for value in members]
            if any(not value for value in member_ids) or len(member_ids) != len(
                set(member_ids)
            ):
                raise forms.ValidationError(
                    f"Row {index} has blank or duplicate member IDs."
                )
            seen_groups.add(group_id)
            normalized.append({"group_id": group_id, "member_ids": member_ids})
        if not normalized:
            raise forms.ValidationError(
                "Remaining-groups CSV must contain at least one duplicate group."
            )
        self._assert_no_cross_group_member_overlap(normalized)
        return normalized


class CrmQueryForm(forms.Form):
    """Start a connection-bound CRM query (Q-CAT-B; not a public catalog product)."""

    form_token = forms.CharField(widget=forms.HiddenInput)
    connection_id = forms.ChoiceField(label="Connected CRM")
    mode = forms.ChoiceField(
        label="Query mode",
        choices=(
            ("by_ids", "Look up records by ID"),
            ("registered_filter_template", "Registered filter template"),
        ),
        initial="by_ids",
        widget=forms.RadioSelect,
    )
    object_family = forms.ChoiceField(
        label="Object family",
        choices=(
            ("people", "People"),
            ("companies", "Companies"),
        ),
        initial="people",
    )
    record_ids = forms.CharField(
        required=False,
        label="Record IDs (comma or newline separated)",
        help_text="Required for by-ID mode. Example: C1, C2",
        widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
    )
    template_id = forms.CharField(
        required=False,
        label="Template ID",
        help_text="Required for registered filter template mode.",
    )
    template_version = forms.IntegerField(
        required=False,
        min_value=1,
        label="Template version",
        initial=1,
    )
    template_params_json = forms.CharField(
        required=False,
        label="Template parameters (JSON object)",
        help_text='Example: {"marker": "SYNTH-4B"}',
        widget=forms.Textarea(attrs={"rows": 3, "spellcheck": "false"}),
    )
    field_projection = forms.CharField(
        required=False,
        label="Field projection (optional, comma-separated)",
        help_text="Leave blank for the default projection for the object family.",
    )

    def __init__(self, *args, connections: list[dict], **kwargs):
        super().__init__(*args, **kwargs)
        connected = [item for item in connections if item.get("status") == "connected"]
        self.fields["connection_id"].choices = [
            (
                item["connection_id"],
                (
                    f"{item.get('provider_label') or item.get('provider_key')}"
                    f" — {item.get('display_label') or item['connection_id']}"
                ),
            )
            for item in connected
        ]
        if not connected:
            self.fields["connection_id"].choices = [("", "No connected CRM available")]
            self.fields["connection_id"].disabled = True

    def clean(self):
        cleaned = super().clean()
        mode = str(cleaned.get("mode") or "").strip()
        raw_ids = str(cleaned.get("record_ids") or "")
        ids = [
            part.strip()
            for part in raw_ids.replace("\n", ",").split(",")
            if part.strip()
        ]
        cleaned["record_ids_list"] = ids
        params_raw = str(cleaned.get("template_params_json") or "").strip()
        params: dict[str, str] = {}
        if params_raw:
            try:
                parsed = json.loads(params_raw)
            except json.JSONDecodeError as exc:
                self.add_error(
                    "template_params_json",
                    f"Template parameters must be valid JSON: {exc.msg}.",
                )
                return cleaned
            if not isinstance(parsed, dict):
                self.add_error(
                    "template_params_json",
                    "Template parameters must be a JSON object.",
                )
                return cleaned
            params = {str(k): str(v) for k, v in parsed.items()}
        cleaned["template_params"] = params
        projection_raw = str(cleaned.get("field_projection") or "")
        cleaned["field_projection_list"] = [
            part.strip() for part in projection_raw.split(",") if part.strip()
        ]
        if mode == "by_ids":
            if not ids:
                self.add_error(
                    "record_ids", "by_ids mode requires at least one record ID."
                )
            if cleaned.get("template_id") or params:
                self.add_error(
                    "template_id",
                    "by_ids mode cannot include template fields.",
                )
        elif mode == "registered_filter_template":
            if not str(cleaned.get("template_id") or "").strip():
                self.add_error(
                    "template_id",
                    "registered_filter_template requires template_id.",
                )
            if cleaned.get("template_version") is None:
                self.add_error(
                    "template_version",
                    "registered_filter_template requires template_version.",
                )
            if ids:
                self.add_error(
                    "record_ids",
                    "registered_filter_template cannot include record_ids.",
                )
        return cleaned


class WorkflowConfigurationForm(forms.Form):
    """Strict four-product creation form derived from the v1 OpenAPI contract."""

    form_token = forms.CharField(widget=forms.HiddenInput)

    contacts_only = forms.BooleanField(
        required=False,
        label="Create Contacts only",
        help_text=(
            "We will try to prepare Contact rows only. If a person has no matching "
            "Account, EasyImports will prepare that person as a Lead instead."
        ),
    )
    match_speed = forms.ChoiceField(
        required=False,
        label="Matching depth",
        choices=(
            ("", "Recommended"),
            ("fast", "Faster"),
            ("mixed", "Balanced"),
            ("thorough", "More thorough"),
        ),
    )
    list_duplicate_policy = forms.ChoiceField(
        required=False,
        label="Duplicates in the uploaded file",
        choices=(
            ("surface", "Ask me to review"),
            ("quarantine", "Set them aside"),
            ("drop_repeats", "Keep the first occurrence"),
        ),
        initial="surface",
    )
    crm_match_duplicate_policy = forms.ChoiceField(
        required=False,
        label="Multiple matches in existing records",
        choices=(
            ("surface", "Ask me to review"),
            ("quarantine", "Set them aside"),
            ("drop_repeats", "Use the first match"),
        ),
        initial="drop_repeats",
    )
    crm_account_multi_match_policy = forms.ChoiceField(
        required=False,
        label="Multiple matching Accounts",
        choices=(
            ("review", "Ask me to review"),
            ("quarantine", "Set them aside"),
            ("use_recommended", "Use the recommended match"),
        ),
        initial="review",
    )
    fill_missing_emails = forms.BooleanField(
        required=False, label="Fill missing email addresses when possible"
    )
    blank_non_north_america_address = forms.BooleanField(
        required=False, label="Clear non-North-American address fields"
    )
    strict_validation = forms.BooleanField(
        required=False, label="Use stricter validation"
    )
    # Phase 6A (D2 option A): public person-write update policy. Unchecked
    # (default) is blank-fill (never overwrite populated CRM fields); checked is
    # allowlisted overwrite (a fixed allowlist of Contact fields only, never an
    # unrestricted patch). Lives beside people writes in the optional CRM-write
    # card; hidden for clean-only runs and when people writes are unavailable.
    update_existing_contact_information = forms.BooleanField(
        required=False,
        label="Update existing Contact information",
        help_text=(
            "Off (default) only fills blank fields on existing Contacts. On "
            "overwrites a fixed allowlist of Contact fields with your uploaded "
            "values — allowlisted fields only, never every field."
        ),
    )
    batch_validation_policy = forms.ChoiceField(
        required=False,
        label="Rows with validation problems",
        choices=(
            ("quarantine", "Set them aside"),
            ("continue_with_invalid", "Keep processing them"),
        ),
        initial="quarantine",
    )
    duplicate_analysis_as_of_date = forms.DateField(
        required=False,
        label="Duplicate review date",
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    # FE-CM-3: CampaignMember policy (list import only; default mode remains disabled).
    campaign_id_column = forms.CharField(
        required=False,
        max_length=128,
        label="Campaign Id column",
        help_text="Source column with Salesforce Campaign Ids (701…). Optional.",
    )
    campaign_match_column = forms.CharField(
        required=False,
        max_length=128,
        label="Campaign name column",
        help_text=(
            "Source column with Campaign names. Each distinct name needs a "
            "durable search/pick resolution before start."
        ),
    )
    member_status_column = forms.CharField(
        required=False,
        max_length=128,
        label="Member status column",
        help_text="Source column with CampaignMember status labels. Optional.",
    )
    default_campaign_binding = forms.CharField(
        required=False,
        max_length=18,
        label="Default Campaign Id",
        help_text=(
            "Single run-level Campaign Id used when a row has no Id or name "
            "column value. Must be a well-formed 701 Id."
        ),
    )
    default_desired_status = forms.CharField(
        required=False,
        max_length=255,
        label="Default member status",
        help_text="Used when the status column is blank (for example Sent).",
    )
    campaign_match_resolutions_json = forms.CharField(
        required=False,
        label="Campaign name resolutions",
        widget=forms.HiddenInput,
        help_text=(
            "Server-owned durable name→Id map (lookup-bound picks only). "
            "Not editable freeform."
        ),
    )

    setup_revision = forms.IntegerField(
        widget=forms.HiddenInput, required=False, initial=0
    )
    entity = forms.ChoiceField(
        required=False,
        label="Record type",
        choices=(("account", "Accounts"), ("person", "People")),
    )
    analysis_as_of_date = forms.DateField(
        required=False,
        label="Review records as of",
        initial=date.today,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    target_object = forms.ChoiceField(
        required=False,
        label="Prepare output for",
        choices=(("account", "Account"), ("contact", "Contact"), ("lead", "Lead")),
    )
    content_type = forms.ChoiceField(
        required=False,
        label="What does the file contain?",
        choices=(
            ("", "Choose automatically"),
            ("accounts", "Accounts"),
            ("people", "People"),
            ("people_and_accounts", "People and accounts"),
        ),
    )
    canon_profile = forms.ChoiceField(
        required=False,
        label="How is the file organized?",
        choices=(
            ("", "Choose automatically"),
            ("new_list", "A new list"),
            ("accounts", "Like an Accounts export"),
            ("contacts", "Like a Contacts export"),
            ("leads", "Like a Leads export"),
        ),
    )
    person_kind = forms.ChoiceField(
        required=False,
        label="Type of people",
        choices=(
            ("", "Choose automatically"),
            ("contact", "Contacts"),
            ("lead", "Leads"),
            ("mixed", "A mix of Contacts and Leads"),
            ("generic", "Not specified"),
        ),
    )

    def __init__(
        self,
        *args,
        product_entry: dict[str, Any],
        target: dict[str, Any],
        uploaded_roles: set[str],
        route_defaults: dict[str, Any] | None = None,
        reference_acquisition_requirement: str | None = None,
        connection_id: str = "",
        verified_resolutions_json: str = "[]",
        raw_list_source: Any | None = None,
        vocabulary: str | None = None,
        provider_key: str | None = None,
        campaign_member_statuses: dict[str, list[str]] | None = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)
        self.product_key = product_entry["product_key"]
        self.uploaded_roles = uploaded_roles
        self.route_defaults = route_defaults or {}
        self.reference_acquisition_requirement = reference_acquisition_requirement
        self.connection_id = str(connection_id or "").strip()
        self.vocabulary = str(vocabulary or "").strip()
        self.provider_key = str(provider_key or "").strip()
        # FE-CM-3 rem: authority is server-owned lookup-bound store, not POST body.
        self.verified_resolutions_json = str(verified_resolutions_json or "[]")
        self.raw_list_source = raw_list_source
        # Phase 7A: ephemeral FE-CM-1 member-status browse per resolved Campaign
        # (campaign_id -> ordered statuses). No CRM I/O at freeze; used only to
        # bound the default-status picker (D12). Empty when not yet browsed.
        self.campaign_member_statuses: dict[str, list[str]] = {
            str(cid): [str(s) for s in (statuses or [])]
            for cid, statuses in (campaign_member_statuses or {}).items()
        }
        self._campaign_status_picker: dict[str, Any] | None = None
        self.track_fields: dict[str, str] = {}
        target_maximums = dict(target["maximum_modes"] or {})
        # Phase 1D: product-scoped ceilings from the target projection (API
        # authority via connected_import_target_projection / catalog). Used for
        # choice filtering and fail-closed clean revalidation.
        self._target_maximums: dict[str, str] = {
            str(track): str(mode) for track, mode in target_maximums.items()
        }
        for track, product_modes in product_entry["tracks"].items():
            if track not in target_maximums:
                raise CatalogCompatibilityError(
                    f"Target catalog is missing the {track!r} mode contract."
                )
            target_maximum = target_maximums[track]
            if target_maximum not in MODE_RANK or any(
                mode not in MODE_RANK for mode in product_modes
            ):
                raise CatalogCompatibilityError(
                    "The API catalog contains a mode unknown to this web client."
                )
            choices = [
                mode
                for mode in product_modes
                if MODE_RANK[mode] <= MODE_RANK[target_maximum]
            ]
            if not choices:
                raise CatalogCompatibilityError(
                    f"Target ceiling for {track!r} admits no modes for this product."
                )
            field_name = f"mode__{track}"
            # Phase 1D: write/provision tracks default to disabled (opt-in).
            # Delivery may default preview; required reference may default execute.
            # OUT-3: list-import family packages always include cleaned members,
            # so the operator is not asked and delivery defaults to disabled.
            if (
                track == "delivery"
                and self.product_key in ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS
                and "disabled" in choices
            ):
                initial = "disabled"
            elif track == "delivery" and "preview" in choices:
                initial = "preview"
            elif (
                track == "reference_acquisition"
                and reference_acquisition_requirement == "execute"
                and "execute" in choices
            ):
                initial = "execute"
            elif (
                track == "reference_acquisition"
                and reference_acquisition_requirement in {"disabled", "prohibited"}
                and "disabled" in choices
            ):
                initial = "disabled"
            elif track in OPT_IN_WRITE_TRACKS:
                # Phase 1D: write/provision never default above disabled.
                # Reject catalog/ceiling drift that omits disabled (would
                # otherwise initialize to the first allowed mode, even execute).
                if "disabled" not in choices:
                    raise CatalogCompatibilityError(
                        f"Opt-in track {track!r} must include 'disabled' at or "
                        f"below the destination ceiling; refuse to invent a "
                        f"non-disabled default."
                    )
                initial = "disabled"
            else:
                initial = "disabled" if "disabled" in choices else choices[0]
            help_text = TRACK_HELP_TEXT.get(track, "")
            if (
                track == "reference_acquisition"
                and reference_acquisition_requirement == "execute"
            ):
                help_text = (
                    "Required for this setup. You will still authorize the CRM "
                    "read before it begins."
                )
            # Phase 1D: MODE_LABELS maps execute → "Apply changes" (opt-in explicit).
            mode_choices = [
                (mode, MODE_LABELS.get(mode, mode.title())) for mode in choices
            ]
            hide_delivery = (
                track == "delivery"
                and self.product_key in ALWAYS_ON_CLEANED_PACKAGE_PRODUCTS
            )
            field_kwargs: dict[str, Any] = {
                "label": (
                    ""
                    if hide_delivery
                    else TRACK_LABELS.get(track, track.replace("_", " ").title())
                ),
                "choices": mode_choices,
                "initial": initial,
                "help_text": "" if hide_delivery else help_text,
                "required": not hide_delivery,
            }
            if hide_delivery:
                field_kwargs["widget"] = forms.HiddenInput()
            self.fields[field_name] = forms.ChoiceField(**field_kwargs)
            self.track_fields[track] = field_name
            if (
                track == "reference_acquisition"
                and reference_acquisition_requirement == "execute"
            ):
                # Keep the field submitted but not freely changed away from execute.
                self.fields[field_name].choices = [
                    (mode, label)
                    for mode, label in self.fields[field_name].choices
                    if mode == "execute"
                ]
            if (
                track == "reference_acquisition"
                and reference_acquisition_requirement == "prohibited"
                and "disabled" in choices
            ):
                self.fields[field_name].choices = [
                    (mode, label)
                    for mode, label in self.fields[field_name].choices
                    if mode == "disabled"
                ]

        allowed = {"form_token", "setup_revision"} | set(self.track_fields.values())
        if self.product_key == "easyimports.list_import":
            allowed |= {
                "contacts_only",
                "match_speed",
                "list_duplicate_policy",
                "crm_match_duplicate_policy",
                "crm_account_multi_match_policy",
                "fill_missing_emails",
                "blank_non_north_america_address",
                "strict_validation",
                "update_existing_contact_information",
                "batch_validation_policy",
                "duplicate_analysis_as_of_date",
                "campaign_id_column",
                "campaign_match_column",
                "member_status_column",
                "default_campaign_binding",
                "default_desired_status",
                "campaign_match_resolutions_json",
            }
        elif self.product_key == "easyimports.account_list_import":
            allowed |= {"list_duplicate_policy", "crm_account_multi_match_policy"}
        elif self.product_key == "easyimports.single_dataset_import":
            allowed |= {
                "target_object",
                "content_type",
                "canon_profile",
                "person_kind",
                "list_duplicate_policy",
            }
        else:
            allowed |= {"entity", "analysis_as_of_date"}
        for name in list(self.fields):
            if name not in allowed:
                del self.fields[name]

        # Phase 6A: the person-write update policy is meaningful only when people
        # writes are actually available for this product/destination. Drop it when
        # the people_writes ceiling is disabled (writes cannot be opted into), so
        # it never renders or submits. Clean-only products never reach here because
        # the field is not in their allowed set.
        if (
            "update_existing_contact_information" in self.fields
            and str(self._target_maximums.get("people_writes", "disabled")) == "disabled"
        ):
            del self.fields["update_existing_contact_information"]

        # OUT-6A: Contacts-only help is provider-specific and visible (not title=).
        if "contacts_only" in self.fields:
            self.fields["contacts_only"].help_text = contacts_only_help_text(
                vocabulary=self.vocabulary,
                provider_key=self.provider_key,
            )

        # FE-CM-3: place CM policy fields after track modes (operator sees ceiling
        # opt-in first, then maps / defaults / resolutions).
        if self.product_key == "easyimports.list_import":
            cm_field_names = (
                "campaign_id_column",
                "campaign_match_column",
                "member_status_column",
                "default_campaign_binding",
                "default_desired_status",
                "campaign_match_resolutions_json",
            )
            for name in cm_field_names:
                field = self.fields.pop(name, None)
                if field is not None:
                    self.fields[name] = field

        # Lock single-dataset target/content fields from the setup draft.
        if (
            self.product_key == "easyimports.single_dataset_import"
            and self.route_defaults
        ):
            for field_name, value in self.route_defaults.items():
                if field_name in self.fields and value is not None:
                    self.fields[field_name].initial = value
                    self.fields[field_name].disabled = True
            # MAP-R5: Accounts clean-only never shows "Type of people".
            # person_kind is None on the accounts route; people clean-only freezes
            # Contact/Lead from setup and keeps the locked control for clarity.
            target_object = str(self.route_defaults.get("target_object") or "").strip()
            content_type = str(self.route_defaults.get("content_type") or "").strip()
            is_accounts_context = (
                target_object == "account"
                or content_type == "accounts"
                or (
                    self.route_defaults.get("person_kind") is None
                    and target_object in {"", "account"}
                )
            )
            if is_accounts_context:
                self.fields.pop("person_kind", None)
            elif (
                "person_kind" in self.fields
                and self.route_defaults.get("person_kind") is not None
            ):
                # People clean-only: value already frozen from setup intent.
                self.fields["person_kind"].disabled = True

        # Phase 7A: replace the free-text default member status with a
        # Campaign-scoped allowlist / intersection picker (D12) whenever the
        # FE-CM-1 member-status browse has been supplied for the resolved
        # Campaign(s). No CRM I/O here; the statuses were fetched ephemerally.
        self._install_campaign_status_picker()

    def _raw_field_value(self, name: str):
        if self.is_bound:
            return self.data.get(self.add_prefix(name))
        if name in self.initial:
            return self.initial.get(name)
        field = self.fields.get(name)
        return getattr(field, "initial", None) if field is not None else None

    def _install_campaign_status_picker(self) -> None:
        if self.product_key != "easyimports.list_import":
            return
        if "default_desired_status" not in self.fields:
            return
        if not self.campaign_member_statuses:
            return
        from .campaign_member_setup import (
            CampaignMemberSetupError,
            campaign_scoped_status_choices,
            parse_campaign_match_resolutions,
            resolved_default_campaign_ids,
        )

        default_campaign = str(self._raw_field_value("default_campaign_binding") or "").strip()
        has_open_id_column = bool(
            str(self._raw_field_value("campaign_id_column") or "").strip()
        )
        resolutions: list[dict[str, Any]] = []
        if self.connection_id:
            try:
                resolutions = parse_campaign_match_resolutions(
                    self.verified_resolutions_json,
                    connection_id=self.connection_id,
                )
            except CampaignMemberSetupError:
                resolutions = []
        resolved_ids = resolved_default_campaign_ids(
            default_campaign_binding=default_campaign,
            resolutions=resolutions,
        )
        if not resolved_ids and not has_open_id_column:
            # Nothing resolved yet; keep the field unbounded until the operator
            # picks a Campaign (clean() still gates enablement).
            return
        try:
            decision = campaign_scoped_status_choices(
                resolved_campaign_ids=resolved_ids,
                campaign_statuses=self.campaign_member_statuses,
                has_open_campaign_id_column=has_open_id_column,
            )
        except CampaignMemberSetupError:
            # A resolved Campaign has no fetched statuses — cannot bound safely;
            # leave the field and let clean() surface the incomplete config.
            return
        self._campaign_status_picker = decision
        choices = [("", "Choose a status")] + [
            (status, status) for status in decision["choices"]
        ]
        existing = self.fields["default_desired_status"]
        self.fields["default_desired_status"] = forms.ChoiceField(
            required=False,
            label=existing.label,
            choices=choices,
            help_text=(
                "Choose from this Campaign's allowed statuses. Map a member "
                "status column for per-row statuses."
                if not decision["requires_per_row_status"]
                else (
                    "These Campaigns share no common status; map a member "
                    "status column to set per-row statuses."
                )
            ),
        )

    def clean(self):
        data = super().clean()
        if self.errors:
            return data
        # Always bind a concrete revision so create can assert atomically.
        if data.get("setup_revision") is None:
            data["setup_revision"] = 0
        missing_required = {
            spec.key
            for spec in PRODUCT_SOURCE_ROLES[self.product_key]
            if spec.required and spec.key not in self.uploaded_roles
        }
        if missing_required:
            labels = {
                spec.key: spec.label for spec in PRODUCT_SOURCE_ROLES[self.product_key]
            }
            raise forms.ValidationError(
                "Required files are missing: "
                + ", ".join(labels[key] for key in sorted(missing_required))
                + "."
            )
        modes = {}
        for track, field in self.track_fields.items():
            value = data.get(field)
            if track == "delivery" and not value:
                value = "disabled"
                data[field] = value
            modes[track] = value
        # Disabled fields are omitted from cleaned_data; restore route defaults.
        if self.product_key == "easyimports.single_dataset_import":
            for field_name, value in self.route_defaults.items():
                if field_name not in data or data.get(field_name) in (None, ""):
                    data[field_name] = value
        if (
            self.reference_acquisition_requirement == "execute"
            and "reference_acquisition" in self.track_fields
        ):
            modes["reference_acquisition"] = "execute"
            data[self.track_fields["reference_acquisition"]] = "execute"
        if (
            self.reference_acquisition_requirement == "prohibited"
            and "reference_acquisition" in self.track_fields
        ):
            modes["reference_acquisition"] = "disabled"
            data[self.track_fields["reference_acquisition"]] = "disabled"
        # Phase 1D: fail closed if any selected mode exceeds product-scoped ceiling
        # (defense in depth beyond ChoiceField choices).
        for track, mode in modes.items():
            ceiling = str(self._target_maximums.get(track, "disabled"))
            if mode not in MODE_RANK or ceiling not in MODE_RANK:
                raise forms.ValidationError(
                    f"The selected mode for {track.replace('_', ' ')} is not allowed."
                )
            if MODE_RANK[str(mode)] > MODE_RANK[ceiling]:
                raise forms.ValidationError(
                    f"The selected mode for {track.replace('_', ' ')} exceeds the "
                    f"destination ceiling ({ceiling}). Choose a lower mode or "
                    f"Do not run."
                )
        reference = modes.get("reference_acquisition", "disabled")
        if self.product_key == "easyimports.list_import":
            supplied_references = self.uploaded_roles & {
                "accounts",
                "contacts",
                "leads",
            }
            if reference != "disabled" and supplied_references:
                raise forms.ValidationError(
                    "Acquired list references cannot include supplied Accounts, Contacts, or Leads."
                )
            if reference == "disabled" and not (
                {"contacts", "leads"} & self.uploaded_roles
            ):
                raise forms.ValidationError(
                    "Supplied list references require a Contacts or Leads upload."
                )
            if modes.get("account_provisioning") != "disabled" and not data.get(
                "contacts_only"
            ):
                raise forms.ValidationError(
                    "Account provisioning for list import requires Contacts-only mode."
                )
            if modes.get("person_duplicate_resolution") != "disabled" and not data.get(
                "duplicate_analysis_as_of_date"
            ):
                raise forms.ValidationError(
                    "Person duplicate analysis requires an as-of date."
                )
            dependent = (
                modes.get("account_provisioning"),
                modes.get("person_duplicate_resolution"),
                modes.get("people_writes"),
            )
            if (
                any(mode in {"dry_run", "execute"} for mode in dependent)
                and reference != "execute"
            ):
                raise forms.ValidationError(
                    "Dry-run or execute CRM effects require acquired references."
                )
            # FE-CM-3: campaign membership policy when track is enabled.
            # Resolutions authority is the server-owned verified store only.
            cm_mode = modes.get("campaign_member_writes", "disabled")
            data["campaign_match_resolutions_json"] = self.verified_resolutions_json
            default_campaign = str(data.get("default_campaign_binding") or "").strip()
            default_status = str(data.get("default_desired_status") or "").strip()
            id_column = str(data.get("campaign_id_column") or "").strip()
            match_column = str(data.get("campaign_match_column") or "").strip()
            status_column = str(data.get("member_status_column") or "").strip()
            # Explicit operator form fields (not exploratory server-side picks).
            explicit_cm_fields = bool(
                default_campaign
                or default_status
                or id_column
                or match_column
                or status_column
            )

            if cm_mode == "disabled":
                # Exploratory search/pick may leave draft resolutions in session.
                # Those must not force enablement or block the required disabled
                # default. Explicit column/default form fields still require opt-in.
                if explicit_cm_fields:
                    raise forms.ValidationError(
                        "Campaign membership policy fields require enabling the "
                        "Campaign membership track (it defaults to Do not run). "
                        "Clear the policy fields, or opt in to a Campaign membership "
                        "mode."
                    )
                data["_campaign_member_policy"] = None
            else:
                try:
                    from .campaign_member_setup import (
                        CampaignMemberSetupError,
                        build_campaign_member_policy,
                        distinct_match_keys_requiring_resolution,
                        parse_campaign_match_resolutions,
                        require_lookup_bound_resolutions,
                        validate_default_status_choice,
                        validate_enabled_campaign_member_policy,
                    )

                    # Phase 7A: when a Campaign-scoped status picker is bounded
                    # (D12), the run default status must be one of its allowlist
                    # entries and an empty-intersection run rejects a default
                    # (per-row member_status required). Independent of the
                    # Campaign axis (D10). Preflight still runs at plan/execute.
                    if self._campaign_status_picker is not None:
                        if (
                            self._campaign_status_picker.get("requires_per_row_status")
                            and not status_column
                            and not default_status
                        ):
                            raise CampaignMemberSetupError(
                                "These Campaigns share no common status. Map a "
                                "member status column so each row carries its own "
                                "status."
                            )
                        validate_default_status_choice(
                            default_status, self._campaign_status_picker
                        )

                    bound_entries: list = []
                    if self.connection_id:
                        bound_entries = parse_campaign_match_resolutions(
                            self.verified_resolutions_json,
                            connection_id=self.connection_id,
                        )
                        if bound_entries:
                            require_lookup_bound_resolutions(bound_entries)

                    policy = build_campaign_member_policy(
                        connection_id=self.connection_id,
                        default_campaign_binding=default_campaign,
                        default_desired_status=default_status,
                        campaign_id_column=id_column,
                        campaign_match_column=match_column,
                        member_status_column=status_column,
                        campaign_match_resolutions_raw=self.verified_resolutions_json,
                    )
                    if cm_mode in {"dry_run", "execute"} and reference != "execute":
                        raise forms.ValidationError(
                            "Campaign membership dry-run or apply requires acquired "
                            "references."
                        )
                    required_keys: set[str] | None = None
                    if match_column:
                        required_keys = distinct_match_keys_requiring_resolution(
                            self.raw_list_source,
                            match_column=match_column,
                            id_column=id_column,
                        )
                    policy = validate_enabled_campaign_member_policy(
                        policy,
                        connection_id=self.connection_id,
                        required_match_keys=required_keys,
                        require_lookup_bound=True,
                        bound_entries=bound_entries,
                    )
                except CampaignMemberSetupError as exc:
                    raise forms.ValidationError(str(exc)) from exc
                data["_campaign_member_policy"] = policy
        elif self.product_key == "easyimports.account_list_import":
            if reference != "disabled" and "accounts" in self.uploaded_roles:
                raise forms.ValidationError(
                    "Acquired Account-list references cannot include an Accounts upload."
                )
            if reference == "disabled" and "accounts" not in self.uploaded_roles:
                raise forms.ValidationError(
                    "Supplied Account-list references require an Accounts upload."
                )
            if (
                modes.get("account_provisioning") in {"dry_run", "execute"}
                and reference != "execute"
            ):
                raise forms.ValidationError(
                    "Dry-run or execute Account provisioning requires acquired references."
                )
        elif self.product_key == "easyimports.duplicate_resolution":
            supplied = self.uploaded_roles & {
                "canonical_records",
                "related_contacts",
                "related_leads",
                "opportunities",
            }
            if reference == "disabled" and "canonical_records" not in supplied:
                raise forms.ValidationError(
                    "Supplied duplicate references require canonical records."
                )
            if reference != "disabled" and supplied:
                raise forms.ValidationError(
                    "Acquired duplicate references cannot include supplied reference files."
                )
            if data.get("entity") == "person" and supplied & {
                "related_contacts",
                "related_leads",
                "opportunities",
            }:
                raise forms.ValidationError(
                    "Person duplicate analysis does not accept Account-related uploads."
                )
            if (
                modes.get("duplicate_execution") in {"dry_run", "execute"}
                and reference != "execute"
            ):
                raise forms.ValidationError(
                    "Duplicate dry-run or execute requires acquired references."
                )
            if not data.get("entity") or not data.get("analysis_as_of_date"):
                raise forms.ValidationError(
                    "Duplicate entity and as-of date are required."
                )
        else:
            self._validate_single_dataset(data)
        return data

    def _validate_single_dataset(self, data: dict[str, Any]) -> None:
        target = data.get("target_object")
        if not target:
            raise forms.ValidationError("Target object is required.")
        content = data.get("content_type") or (
            "accounts" if target == "account" else "people_and_accounts"
        )
        profile = data.get("canon_profile") or (
            "accounts" if target == "account" else "new_list"
        )
        kind = data.get("person_kind") or None

        if content in {"people", "people_and_accounts"}:
            if profile not in {"new_list", "contacts", "leads"}:
                raise forms.ValidationError(
                    "People-bearing source content requires the New list, Contacts, "
                    "or Leads canon profile."
                )
            kind = kind or "generic"
        elif profile != "accounts" or kind is not None:
            raise forms.ValidationError(
                "Account-only source content requires the Accounts canon profile "
                "and no person kind."
            )

        if target == "account" and content not in {
            "accounts",
            "people_and_accounts",
        }:
            raise forms.ValidationError(
                "An Account target requires source content containing accounts."
            )
        if target in {"contact", "lead"} and content not in {
            "people",
            "people_and_accounts",
        }:
            raise forms.ValidationError(
                "A Contact or Lead target requires source content containing people."
            )

        # Freeze the same effective defaults used by the API request factory so
        # the journaled request remains explicit and reproducible.
        data["content_type"] = content
        data["canon_profile"] = profile
        data["person_kind"] = kind

    def workflow_request(self, *, upload_ids: dict[str, str], target_provider_id: str):
        data = dict(self.cleaned_data)
        if self.product_key == "easyimports.single_dataset_import":
            for field_name, value in self.route_defaults.items():
                if field_name not in data or data.get(field_name) in (None, ""):
                    data[field_name] = value
        modes = {}
        for track, field in self.track_fields.items():
            value = data.get(field)
            if track == "delivery" and not value:
                value = "disabled"
            modes[track] = value
        if self.reference_acquisition_requirement == "execute":
            modes["reference_acquisition"] = "execute"
        if self.reference_acquisition_requirement == "prohibited":
            modes.pop("reference_acquisition", None)
            # single-dataset has no reference track; matching products use disabled.
            if "reference_acquisition" in self.track_fields:
                modes["reference_acquisition"] = "disabled"
        # Only send upload roles accepted by the product create schema.
        if self.product_key == "easyimports.single_dataset_import":
            filtered_uploads = {
                key: value for key, value in upload_ids.items() if key == "dataset"
            }
        elif self.product_key == "easyimports.account_list_import":
            filtered_uploads = {
                key: value
                for key, value in upload_ids.items()
                if key in {"raw_list", "accounts"}
            }
        elif self.product_key == "easyimports.list_import":
            allowed = {
                "raw_list",
                "contacts",
                "accounts",
                "leads",
                "users",
                "territory",
                "company_exclusions",
                "industry_mapping",
            }
            filtered_uploads = {
                key: value for key, value in upload_ids.items() if key in allowed
            }
        else:
            filtered_uploads = dict(upload_ids)
        request: dict[str, Any] = {
            "product_key": self.product_key,
            "target_provider_id": target_provider_id,
            "uploads": filtered_uploads,
            "maximum_modes": modes,
        }
        connection_id = str(getattr(self, "connection_id", "") or "").strip()
        if connection_id and self.product_key in {
            "easyimports.list_import",
            "easyimports.account_list_import",
        }:
            request["connection_id"] = connection_id
        if self.product_key == "easyimports.list_import":
            request["options"] = {
                "interaction_mode": "interactive",
                "batch_validation_policy": data["batch_validation_policy"],
                "contacts_only": bool(data.get("contacts_only")),
                "match_speed": data.get("match_speed") or None,
                "list_duplicate_policy": data["list_duplicate_policy"],
                "crm_match_duplicate_policy": data["crm_match_duplicate_policy"],
                "crm_account_multi_match_policy": data[
                    "crm_account_multi_match_policy"
                ],
                "fill_missing_emails": bool(data.get("fill_missing_emails")),
                "blank_non_north_america_address": bool(
                    data.get("blank_non_north_america_address")
                ),
                "strict_validation": bool(data.get("strict_validation")),
            }
            request["duplicate_analysis_as_of_date"] = (
                data["duplicate_analysis_as_of_date"].isoformat()
                if data.get("duplicate_analysis_as_of_date")
                else None
            )
            # Phase 6A: public person-write update policy (blank-fill default).
            # Absent field (people writes unavailable) coalesces to blank-fill.
            request["update_existing_contact_information"] = bool(
                data.get("update_existing_contact_information")
            )
            # FE-CM-3: durable policy freezes with create_workflow journal body.
            policy = data.get("_campaign_member_policy")
            if policy is not None:
                request["campaign_member_policy"] = policy
        elif self.product_key == "easyimports.account_list_import":
            request["options"] = {
                "interaction_mode": "interactive",
                "list_duplicate_policy": data["list_duplicate_policy"],
                "crm_account_multi_match_policy": data[
                    "crm_account_multi_match_policy"
                ],
            }
        elif self.product_key == "easyimports.single_dataset_import":
            request["target_object"] = data["target_object"]
            for name in ("content_type", "canon_profile", "person_kind"):
                if data.get(name):
                    request[name] = data[name]
            request["options"] = {
                "list_duplicate_policy": data["list_duplicate_policy"]
            }
        else:
            request["entity"] = data["entity"]
            request["analysis_as_of_date"] = data["analysis_as_of_date"].isoformat()
        return request
