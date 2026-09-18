from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ("importer", "0011_crm_dupe_7c_claim_auto_merge_backfill"),
    ]

    operations = [
        migrations.CreateModel(
            name="CrmDuplicateJourneyDismissal",
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
                ("journey_id", models.CharField(max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "journal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="crm_duplicate_journey_dismissals",
                        to="importer.importsession",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "id"]},
        ),
        migrations.AddConstraint(
            model_name="crmduplicatejourneydismissal",
            constraint=models.UniqueConstraint(
                fields=("journal", "journey_id"),
                name="unique_crm_duplicate_journey_dismissal",
            ),
        ),
    ]
