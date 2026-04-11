from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0019_alter_historyimportrun_download_tg_media'),
    ]

    operations = [
        migrations.AddField(
            model_name='channelmorningdigest',
            name='block_yesterday_news',
            field=models.BooleanField(default=True, verbose_name='Сводка по вчерашним постам канала'),
        ),
        migrations.AddField(
            model_name='channelmorningdigest',
            name='block_holidays_tomorrow',
            field=models.BooleanField(default=True, verbose_name='Праздники завтра'),
        ),
        migrations.AddField(
            model_name='channelmorningdigest',
            name='use_ai_yesterday_news',
            field=models.BooleanField(default=True, verbose_name='Сводку за вчера — через DeepSeek'),
        ),
    ]
