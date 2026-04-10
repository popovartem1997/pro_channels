from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_globalapikeys_parse_media_disk_quota_bytes'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalapikeys',
            name='telegram_bot_proxy_url',
            field=models.CharField(
                blank=True,
                help_text='Один URL: http://host:port, http://user:pass@host:port, socks5://host:port. Пусто — без прокси. '
                'Для SOCKS5 в образе должен быть установлен extra python-telegram-bot[socks].',
                max_length=512,
                verbose_name='Прокси для Telegram Bot API',
            ),
        ),
    ]
