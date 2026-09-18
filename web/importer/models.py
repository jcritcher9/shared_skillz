"""Database models for the Django-owned EasyImports API client state."""

from __future__ import annotations

import uuid
from pathlib import Path

from django.conf import settings
from django.db import models
from django.utils import timezone

from .constants import SOURCE_ROLE_CHOICES


class ImportSession(models.Model):
    """Django-owned state for one API-backed import workflow session."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"
        ARCHIVED = "archived", "Archived"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner_id = models.UUIDField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    archived_at = models.DateTimeField(null=True, blank=True)
    is_legacy = models.BooleanField(default=False)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
    )
    options = models.JSONField(default=dict, blank=True)
    summary = models.JSONField(null=True, blank=True)
    error_message = models.TextField(blank=True, default="")
    # Frozen only after successful API workflow create (or legacy/journey sessions).
    product_key = models.CharField(max_length=128, blank=True, default="")
    target_provider_id = models.CharField(max_length=128, blank=True, default="")
    operator_label = models.CharField(max_length=160, blank=True, default="")
    operator_label_frozen_at = models.DateTimeField(null=True, blank=True)
    # Operator intent draft — independent of frozen product_key.
    setup_entity = models.CharField(max_length=32, blank=True, default="")
    setup_operation = models.CharField(max_length=32, blank=True, default="")
    setup_reference_source = models.CharField(max_length=32, blank=True, default="")
    setup_people_output = models.CharField(max_length=32, blank=True, default="")
    setup_connection_id = models.CharField(max_length=160, blank=True, default="")
    # CMX-1: field-name vocabulary intent (product | fake | salesforce | hubspot).
    # Independent of match/export; does not invent connections or change download format.
    setup_vocabulary = models.CharField(max_length=32, blank=True, default="")
    setup_revision = models.PositiveIntegerField(default=0)
    active_workflow = models.ForeignKey(
        "ApiWorkflow",
        related_name="+",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
    )

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"ImportSession {self.id} ({self.status})"

    # --- On-disk locations -------------------------------------------------
    @property
    def work_dir(self) -> Path:
        return Path(settings.SESSIONS_ROOT) / str(self.id)

    @property
    def uploads_dir(self) -> Path:
        return self.work_dir / "uploads"

    # --- Convenience -------------------------------------------------------
    @property
    def is_complete(self) -> bool:
        return self.status == self.Status.COMPLETED


class ApiWorkflow(models.Model):
    """A public API workflow resource owned by one Django import session."""

    class Role(models.TextChoices):
        PRIMARY = "primary", "Primary"
        REVIEW = "review", "Review"
        CONTINUATION = "continuation", "Continuation"

    session = models.ForeignKey(
        ImportSession,
        related_name="api_workflows",
        on_delete=models.CASCADE,
    )
    source_workflow = models.ForeignKey(
        "self",
        related_name="derived_workflows",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    run_id = models.CharField(max_length=160, unique=True)
    role = models.CharField(
        max_length=24,
        choices=Role.choices,
        default=Role.PRIMARY,
    )
    workflow_key = models.CharField(max_length=128)
    workflow_version = models.PositiveIntegerField()
    target_provider_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=64)
    stage = models.CharField(max_length=160)
    revision = models.PositiveIntegerField()
    resource_url = models.CharField(max_length=512)
    projection = models.JSONField(default=dict)
    projection_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"{self.run_id} ({self.status}@{self.revision})"


class Payment(models.Model):
    """A checkout attempt for the paid plan (via WooCommerce)."""

    class Status(models.TextChoices):
        CREATED = "created", "Created"
        PENDING = "pending", "Pending payment"
        COMPLETED = "completed", "Completed"
        FAILED = "failed", "Failed"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    provider = models.CharField(max_length=32, default="woocommerce")
    email = models.EmailField(blank=True, default="")
    plan_name = models.CharField(max_length=128, blank=True, default="")
    amount_label = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.CREATED,
    )
    wc_order_id = models.IntegerField(null=True, blank=True)
    wc_order_key = models.CharField(max_length=128, blank=True, default="")
    payment_url = models.URLField(max_length=1024, blank=True, default="")
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"Payment {self.id} ({self.status})"


class SourceFile(models.Model):
    """An uploaded input file tied to a session and a pipeline role."""

    session = models.ForeignKey(
        ImportSession,
        related_name="files",
        on_delete=models.CASCADE,
    )
    role = models.CharField(max_length=64, choices=SOURCE_ROLE_CHOICES)
    original_name = models.CharField(max_length=255)
    stored_path = models.CharField(max_length=1024)
    row_count = models.IntegerField(null=True, blank=True)
    columns = models.JSONField(default=list, blank=True)
    media_type = models.CharField(max_length=160, blank=True, default="")
    csv_encoding = models.CharField(max_length=64, blank=True, default="")
    xlsx_sheet = models.JSONField(null=True, blank=True)
    content_digest = models.CharField(max_length=64, blank=True, default="")
    api_upload_id = models.CharField(max_length=160, blank=True, default="")
    upload_mutation = models.OneToOneField(
        "ApiMutation",
        related_name="uploaded_source",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    slot_generation = models.PositiveIntegerField(default=0)
    replacement_authorized_at = models.DateTimeField(null=True, blank=True)
    # Detach removes a completed/rejected file from the active setup role set
    # without deleting retained bytes or mutation history. The active slot is
    # freed by renaming ``role`` and setting these fields.
    detached_at = models.DateTimeField(null=True, blank=True)
    original_role = models.CharField(max_length=64, blank=True, default="")
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["role", "uploaded_at"]
        constraints = [
            models.UniqueConstraint(
                fields=("session", "role"),
                condition=models.Q(
                    upload_mutation__isnull=False, detached_at__isnull=True
                ),
                name="unique_active_api_upload_slot",
            )
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.original_name}"

    @property
    def path(self) -> Path:
        return Path(self.stored_path)


class ApiMutation(models.Model):
    """One globally keyed, retry-safe outbound POST from Django to the API."""

    class State(models.TextChoices):
        PENDING = "pending", "Pending"
        COMPLETED = "completed", "Completed"
        REJECTED = "rejected", "Rejected"
        UNKNOWN = "unknown", "Unknown"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ImportSession,
        related_name="api_mutations",
        on_delete=models.CASCADE,
    )
    workflow = models.ForeignKey(
        ApiWorkflow,
        related_name="mutations",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    form_instance = models.UUIDField(unique=True, default=uuid.uuid4, editable=False)
    idempotency_key = models.CharField(max_length=255, unique=True)
    mutation_kind = models.CharField(max_length=64)
    method = models.CharField(max_length=8, default="POST")
    route = models.CharField(max_length=512)
    resource_identity = models.CharField(max_length=255, blank=True, default="")
    logical_action_identity = models.CharField(max_length=512, blank=True, default="")
    logical_action_generation = models.PositiveIntegerField(default=0)
    form_payload_digest = models.CharField(max_length=64, blank=True, default="")
    request_kind = models.CharField(max_length=24, default="json")
    request_json = models.JSONField(null=True, blank=True)
    multipart_metadata = models.JSONField(null=True, blank=True)
    request_digest = models.CharField(max_length=64)
    state = models.CharField(
        max_length=16,
        choices=State.choices,
        default=State.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    lease_token = models.UUIDField(null=True, blank=True)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    dispatched_at = models.DateTimeField(null=True, blank=True)
    http_status = models.PositiveIntegerField(null=True, blank=True)
    response_json = models.JSONField(null=True, blank=True)
    response_digest = models.CharField(max_length=64, blank=True, default="")
    error_code = models.CharField(max_length=128, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    result_upload_id = models.CharField(max_length=160, blank=True, default="")
    result_run_id = models.CharField(max_length=160, blank=True, default="")
    first_submitted_at = models.DateTimeField(default=timezone.now)
    acknowledged_at = models.DateTimeField(null=True, blank=True)
    replacement_of = models.ForeignKey(
        "self",
        related_name="replacement_mutations",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["created_at", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(method="POST"),
                name="api_mutation_post_only",
            ),
            models.UniqueConstraint(
                fields=(
                    "session",
                    "logical_action_identity",
                    "logical_action_generation",
                ),
                condition=~models.Q(logical_action_identity=""),
                name="unique_api_logical_action_generation",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.mutation_kind}:{self.idempotency_key} ({self.state})"

    @property
    def retry_available(self) -> bool:
        if self.operation_in_progress:
            return False
        if self.state == self.State.UNKNOWN and self.lease_token is None:
            return True
        return self.state == self.State.PENDING and (
            self.lease_expires_at is None or self.lease_expires_at <= timezone.now()
        )

    @property
    def operation_in_progress(self) -> bool:
        return self.state == self.State.PENDING and self.http_status in {202, 409}

    @property
    def is_idempotency_conflict(self) -> bool:
        return self.error_code in {
            "idempotency_conflict",
            "idempotency_key_reused",
            "idempotency_key_conflict",
        }


class ApiArtifact(models.Model):
    """Server-owned artifact metadata bound to a stored workflow projection."""

    workflow = models.ForeignKey(
        ApiWorkflow,
        related_name="artifacts",
        on_delete=models.CASCADE,
    )
    artifact_id = models.CharField(max_length=160)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("workflow", "artifact_id"),
                name="unique_api_artifact_per_workflow",
            )
        ]
        ordering = ["artifact_id"]

    def __str__(self) -> str:
        return f"{self.workflow.run_id}:{self.artifact_id}"


class CrmDuplicateJourneyAttemptClaim(models.Model):
    """DB-enforced single local session per CRM-dupe root form attempt.

    Unique on ``(journal, root_form_instance)`` so SQLite and PostgreSQL both
    converge on one winner via IntegrityError, without relying on
    ``select_for_update`` (which SQLite does not enforce for this purpose).
    Frozen intent columns reject changed-payload token reuse.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.ForeignKey(
        ImportSession,
        related_name="crm_dupe_4a_attempt_claims",
        on_delete=models.CASCADE,
    )
    root_form_instance = models.UUIDField(db_index=True)
    claimed_session = models.ForeignKey(
        ImportSession,
        related_name="crm_dupe_4a_claimed_by",
        on_delete=models.CASCADE,
    )
    source_mode = models.CharField(max_length=64)
    connection_id = models.CharField(max_length=160)
    entity_family = models.CharField(max_length=32)
    duplicate_execution_maximum = models.CharField(max_length=32)
    # Phase 7C: frozen auto-merge threshold ("none" or integer string 90–100).
    auto_merge_min_confidence = models.CharField(max_length=8, default="none")
    # DDR-4: frozen entity-neutral matching preference.
    matching_mode = models.CharField(max_length=16, default="default")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("journal", "root_form_instance"),
                name="unique_crm_dupe_4a_attempt_claim",
            )
        ]
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return (
            f"CrmDupeAttemptClaim journal={self.journal_id} "
            f"root={self.root_form_instance}"
        )


class CrmDuplicateJourneyDismissal(models.Model):
    """Owner-journal presentation tombstone for one CRM duplicate journey.

    A row-per-journey unique constraint makes concurrent Clear requests an
    additive operation instead of a lossy JSON options read-modify-write.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    journal = models.ForeignKey(
        ImportSession,
        related_name="crm_duplicate_journey_dismissals",
        on_delete=models.CASCADE,
    )
    journey_id = models.CharField(max_length=160)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("journal", "journey_id"),
                name="unique_crm_duplicate_journey_dismissal",
            )
        ]
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"CrmDupeDismissal journal={self.journal_id} journey={self.journey_id}"


class CrmDuplicateMergePlanLease(models.Model):
    """1:1 merge-plan lease for one CRM-dupe ImportSession.

    Frozen iff ``continuation_run_id`` is non-empty. Local freeze and clear
    writes go through one CAS ``UPDATE`` (SQLite-safe; no select_for_update).
    """

    session = models.OneToOneField(
        ImportSession,
        primary_key=True,
        related_name="merge_plan_lease",
        on_delete=models.CASCADE,
    )
    epoch = models.PositiveIntegerField(default=0)
    continuation_run_id = models.CharField(max_length=160, blank=True, default="")
    decision_set_content_digest = models.CharField(
        max_length=160, blank=True, default=""
    )
    applied_invalidate_mutation_id = models.CharField(
        max_length=160, blank=True, default=""
    )
    applied_finalize_mutation_id = models.CharField(
        max_length=160, blank=True, default=""
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(epoch__gte=0),
                name="crm_dupe_merge_plan_lease_epoch_gte_0",
            )
        ]

    def __str__(self) -> str:
        return (
            f"CrmDupeMergePlanLease session={self.session_id} "
            f"epoch={self.epoch} continuation={self.continuation_run_id or '-'}"
        )
