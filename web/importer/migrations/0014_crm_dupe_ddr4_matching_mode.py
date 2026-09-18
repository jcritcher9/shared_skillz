"""DDR-4: freeze matching_mode on root attempt claims."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0013_crm_dupe_drux2_merge_plan_lease"),
    ]

    operations = [
        migrations.AddField(
            model_name="crmduplicatejourneyattemptclaim",
            name="matching_mode",
            field=models.CharField(default="default", max_length=16),
        ),
    ]
