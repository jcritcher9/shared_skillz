from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0006_importsession_setup_draft"),
    ]

    operations = [
        migrations.AddField(
            model_name="sourcefile",
            name="detached_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="sourcefile",
            name="original_role",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.RemoveConstraint(
            model_name="sourcefile",
            name="unique_active_api_upload_slot",
        ),
        migrations.AddConstraint(
            model_name="sourcefile",
            constraint=models.UniqueConstraint(
                condition=models.Q(
                    upload_mutation__isnull=False,
                    detached_at__isnull=True,
                ),
                fields=("session", "role"),
                name="unique_active_api_upload_slot",
            ),
        ),
    ]
