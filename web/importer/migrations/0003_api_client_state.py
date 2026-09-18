import django.db.models.deletion
import uuid
from django.db import migrations, models


def mark_existing_sessions_legacy(apps, _schema_editor):
    ImportSession = apps.get_model("importer", "ImportSession")
    ImportSession.objects.update(is_legacy=True)


class Migration(migrations.Migration):
    dependencies = [("importer", "0002_payment")]

    operations = [
        migrations.AddField(
            model_name="importsession",
            name="owner_id",
            field=models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name="importsession",
            name="archived_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="importsession",
            name="is_legacy",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="importsession",
            name="product_key",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="importsession",
            name="target_provider_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AlterField(
            model_name="importsession",
            name="status",
            field=models.CharField(
                choices=[
                    ("created", "Created"),
                    ("running", "Running"),
                    ("completed", "Completed"),
                    ("failed", "Failed"),
                    ("archived", "Archived"),
                ],
                default="created",
                max_length=20,
            ),
        ),
        migrations.RunPython(mark_existing_sessions_legacy, migrations.RunPython.noop),
        migrations.CreateModel(
            name="ApiWorkflow",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("run_id", models.CharField(max_length=160, unique=True)),
                (
                    "role",
                    models.CharField(
                        choices=[
                            ("primary", "Primary"),
                            ("review", "Review"),
                            ("continuation", "Continuation"),
                        ],
                        default="primary",
                        max_length=24,
                    ),
                ),
                ("workflow_key", models.CharField(max_length=128)),
                ("workflow_version", models.PositiveIntegerField()),
                (
                    "target_provider_id",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("status", models.CharField(max_length=64)),
                ("stage", models.CharField(max_length=160)),
                ("revision", models.PositiveIntegerField()),
                ("resource_url", models.CharField(max_length=512)),
                ("projection", models.JSONField(default=dict)),
                ("projection_digest", models.CharField(max_length=64)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_workflows",
                        to="importer.importsession",
                    ),
                ),
                (
                    "source_workflow",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="derived_workflows",
                        to="importer.apiworkflow",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddField(
            model_name="importsession",
            name="active_workflow",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="importer.apiworkflow",
            ),
        ),
        migrations.CreateModel(
            name="ApiMutation",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "form_instance",
                    models.UUIDField(default=uuid.uuid4, editable=False, unique=True),
                ),
                ("idempotency_key", models.CharField(max_length=255, unique=True)),
                ("mutation_kind", models.CharField(max_length=64)),
                ("method", models.CharField(default="POST", max_length=8)),
                ("route", models.CharField(max_length=512)),
                (
                    "resource_identity",
                    models.CharField(blank=True, default="", max_length=255),
                ),
                ("request_kind", models.CharField(default="json", max_length=24)),
                ("request_json", models.JSONField(blank=True, null=True)),
                ("multipart_metadata", models.JSONField(blank=True, null=True)),
                ("request_digest", models.CharField(max_length=64)),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("pending", "Pending"),
                            ("completed", "Completed"),
                            ("rejected", "Rejected"),
                            ("unknown", "Unknown"),
                        ],
                        default="pending",
                        max_length=16,
                    ),
                ),
                ("attempt_count", models.PositiveIntegerField(default=0)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("dispatched_at", models.DateTimeField(blank=True, null=True)),
                ("http_status", models.PositiveIntegerField(blank=True, null=True)),
                ("response_json", models.JSONField(blank=True, null=True)),
                (
                    "response_digest",
                    models.CharField(blank=True, default="", max_length=64),
                ),
                (
                    "error_code",
                    models.CharField(blank=True, default="", max_length=128),
                ),
                ("error_message", models.TextField(blank=True, default="")),
                (
                    "result_upload_id",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "result_run_id",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="api_mutations",
                        to="importer.importsession",
                    ),
                ),
                (
                    "workflow",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="mutations",
                        to="importer.apiworkflow",
                    ),
                ),
            ],
            options={
                "ordering": ["created_at", "id"],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(("method", "POST")),
                        name="api_mutation_post_only",
                    )
                ],
            },
        ),
        migrations.AlterField(
            model_name="sourcefile",
            name="role",
            field=models.CharField(
                choices=[
                    ("raw_list", "Marketing list to import"),
                    ("contacts", "Salesforce Contacts export"),
                    ("accounts", "Salesforce Accounts export"),
                    ("leads", "Salesforce Leads export"),
                    ("users", "Salesforce Users export"),
                    ("territory", "Territory / ownership export"),
                    ("company_exclusions", "Company exclusions"),
                    ("industry_mapping", "Industry mapping"),
                    ("dataset", "Dataset"),
                    ("canonical_records", "Canonical CRM records"),
                    ("related_contacts", "Related Contacts"),
                    ("related_leads", "Related Leads"),
                    ("opportunities", "Opportunities"),
                ],
                max_length=64,
            ),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="media_type",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="csv_encoding",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="xlsx_sheet",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="content_digest",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="api_upload_id",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="upload_mutation",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="uploaded_sources",
                to="importer.apimutation",
            ),
        ),
        migrations.CreateModel(
            name="ApiArtifact",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("artifact_id", models.CharField(max_length=160)),
                ("metadata", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "workflow",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="artifacts",
                        to="importer.apiworkflow",
                    ),
                ),
            ],
            options={
                "ordering": ["artifact_id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("workflow", "artifact_id"),
                        name="unique_api_artifact_per_workflow",
                    )
                ],
            },
        ),
    ]
