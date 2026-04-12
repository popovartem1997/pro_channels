# Generated manually for verbose_name/help_text

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_globalapikeys_telegram_bot_proxy_url'),
    ]

    operations = [
        migrations.AlterField(
            model_name='globalapikeys',
            name='telegram_bot_proxy_url',
            field=models.CharField(
                blank=True,
                help_text='Один URL для api.telegram.org и для Telethon (парсинг, импорт): http(s):// или socks5://… '
                'Пусто — без прокси. TELETHON_PROXY_URL в .env задаёт другой прокси только для Telethon.',
                max_length=512,
                verbose_name='Прокси для Telegram (Bot API и Telethon)',
            ),
        ),
    ]
