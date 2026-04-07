from django.db import migrations, models


def backfill_stats_skipped(apps, schema_editor):
    ParseKeyword = apps.get_model('parsing', 'ParseKeyword')
    ParsedItem = apps.get_model('parsing', 'ParsedItem')
    for kw in ParseKeyword.objects.all().iterator():
        n = ParsedItem.objects.filter(keyword_id=kw.pk, status='ignored').count()
        if n:
            ParseKeyword.objects.filter(pk=kw.pk).update(stats_skipped=n)


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0014_parsetask_permission_clear_telethon_locks'),
    ]

    operations = [
        migrations.AddField(
            model_name='parsekeyword',
            name='stats_skipped',
            field=models.PositiveIntegerField(default=0, verbose_name='Пропусков (статистика)'),
        ),
        migrations.AddField(
            model_name='parsekeyword',
            name='stats_published',
            field=models.PositiveIntegerField(default=0, verbose_name='Публикаций (статистика)'),
        ),
        migrations.RunPython(backfill_stats_skipped, migrations.RunPython.noop),
    ]
