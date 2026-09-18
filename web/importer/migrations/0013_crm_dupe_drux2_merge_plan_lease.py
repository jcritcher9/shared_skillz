from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("importer", "0012_crm_duplicate_journey_dismissal"),
    ]

    operations = [
        migrations.CreateModel(
            name="CrmDuplicateMergePlanLease",
            fields=[
                (
                    "session",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        related_name="merge_plan_lease",
                        serialize=False,
                        to="importer.importsession",
                    ),
                ),
                ("epoch", models.PositiveIntegerField(default=0)),
                (
                    "continuation_run_id",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "decision_set_content_digest",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "applied_invalidate_mutation_id",
                    models.CharField(blank=True, default="", max_length=160),
                ),
                (
                    "applied_finalize_mutation_id",
                    models.CharField(blank=True, default="", max_length=160),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="crmduplicatemergeplanlease",
            constraint=models.CheckConstraint(
                condition=models.Q(epoch__gte=0),
                name="crm_dupe_merge_plan_lease_epoch_gte_0",
            ),
        ),
    ]
