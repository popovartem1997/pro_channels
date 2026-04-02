# Generated manually: токен служебного импорт-бота больше не используется.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_globalapikeys_ord_defaults'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='globalapikeys',
            name='tg_import_bot_token_enc',
        ),
    ]
