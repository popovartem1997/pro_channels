from django.db import migrations, models


def copy_example_to_list(apps, schema_editor):
    KeywordHarvestJob = apps.get_model('parsing', 'KeywordHarvestJob')
    for job in KeywordHarvestJob.objects.exclude(example_channel='').iterator():
        ex = (job.example_channel or '').strip()
        if ex:
            KeywordHarvestJob.objects.filter(pk=job.pk).update(example_channels=[ex])


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0016_keywordharvestjob'),
    ]

    operations = [
        migrations.AddField(
            model_name='keywordharvestjob',
            name='example_channels',
            field=models.JSONField(blank=True, default=list, verbose_name='Каналы-примеры (Telegram)'),
        ),
        migrations.RunPython(copy_example_to_list, migrations.RunPython.noop),
    ]
