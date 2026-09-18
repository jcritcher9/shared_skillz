from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("importer", "0005_apimutation_acknowledged_at_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="importsession",
            name="setup_connection_id",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="importsession",
            name="setup_entity",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="importsession",
            name="setup_operation",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="importsession",
            name="setup_people_output",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="importsession",
            name="setup_reference_source",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="importsession",
            name="setup_revision",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
