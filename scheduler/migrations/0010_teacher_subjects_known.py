from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('scheduler', '0009_divisionsubject'),
    ]

    operations = [
        migrations.AddField(
            model_name='teacher',
            name='subjects_known',
            field=models.ManyToManyField(blank=True, related_name='qualified_teachers', to='scheduler.Subject'),
        ),
    ]
