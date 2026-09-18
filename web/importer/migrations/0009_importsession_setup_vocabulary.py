from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0008_crm_dupe_4a_attempt_claim"),
    ]

    operations = [
        migrations.AddField(
            model_name="importsession",
            name="setup_vocabulary",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
