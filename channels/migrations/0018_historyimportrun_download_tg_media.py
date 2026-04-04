# Generated manually for HistoryImportRun.download_tg_media

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0017_alter_channeladaddon_addon_kind'),
    ]

    operations = [
        migrations.AddField(
            model_name='historyimportrun',
            name='download_tg_media',
            field=models.BooleanField(
                default=True,
                help_text='Если выключено — в MAX только текст; посты только с файлами без подписи пропускаются.',
                verbose_name='Скачивать медиа из Telegram',
            ),
        ),
    ]
