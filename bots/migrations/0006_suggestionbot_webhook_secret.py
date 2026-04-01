# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bots', '0005_suggestionstoredmedia'),
    ]

    operations = [
        migrations.AddField(
            model_name='suggestionbot',
            name='webhook_secret',
            field=models.CharField(
                blank=True,
                help_text='Секретный токен для заголовка X-Telegram-Bot-Api-Secret-Token (настраивается при setWebhook).',
                max_length=200,
                verbose_name='Webhook secret (Telegram)',
            ),
        ),
    ]

