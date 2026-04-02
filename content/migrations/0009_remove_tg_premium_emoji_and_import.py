# Generated manually: удаление Premium emoji / импорта из Telegram для каналов.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0008_post_text_html'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='post',
            name='has_premium_emoji',
        ),
        migrations.RemoveField(
            model_name='post',
            name='tg_entities',
        ),
        migrations.DeleteModel(
            name='TelegramImportedMessage',
        ),
        migrations.DeleteModel(
            name='TelegramImportLink',
        ),
    ]
