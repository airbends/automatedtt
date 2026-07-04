from django.db import migrations


def ensure_teacher_subjects_known_table(apps, schema_editor):
    """Repair drifted databases where the M2M table is missing."""
    Teacher = apps.get_model('scheduler', 'Teacher')
    through_model = Teacher._meta.get_field('subjects_known').remote_field.through
    table_name = through_model._meta.db_table

    existing_tables = schema_editor.connection.introspection.table_names()
    if table_name not in existing_tables:
        schema_editor.create_model(through_model)


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0011_alter_teacher_subjects_known'),
    ]

    operations = [
        migrations.RunPython(
            ensure_teacher_subjects_known_table,
            migrations.RunPython.noop,
        ),
    ]
