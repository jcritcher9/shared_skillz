"""Phase 7C: freeze auto_merge_min_confidence on root attempt claims."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0009_importsession_setup_vocabulary"),
    ]

    operations = [
        migrations.AddField(
            model_name="crmduplicatejourneyattemptclaim",
            name="auto_merge_min_confidence",
            field=models.CharField(default="none", max_length=8),
        ),
    ]
