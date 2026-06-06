from django.db import migrations


class Migration(migrations.Migration):
    # Rename Run.shell_progress -> Run.progress. The image's progress signal
    # is per-DATASET ("PANDDA_PROGRESS: dataset j/N"), not per-shell, so the
    # field is renamed to a granularity-neutral name before anything depends
    # on it. RenameField (not drop+add) keeps it a pure rename.

    dependencies = [
        ("inspect_api", "0012_project_external_id_run"),
    ]

    operations = [
        migrations.RenameField(
            model_name="run",
            old_name="shell_progress",
            new_name="progress",
        ),
    ]
