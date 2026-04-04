# Синхронизация help_text с models.HistoryImportRun.download_tg_media.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0018_historyimportrun_download_tg_media'),
    ]

    operations = [
        migrations.AlterField(
            model_name='historyimportrun',
            name='download_tg_media',
            field=models.BooleanField(
                default=True,
                help_text='Выключите для ускорения: в MAX только текст; посты только с файлами без подписи пропускаются.',
                verbose_name='Скачивать медиа из Telegram',
            ),
        ),
    ]
