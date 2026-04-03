from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0004_user_feed_ai_moods'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='telegram_user_id',
            field=models.BigIntegerField(
                blank=True,
                help_text='Числовой ID в Telegram для личных уведомлений бота предложки.',
                null=True,
                verbose_name='Telegram user ID',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='max_user_id',
            field=models.CharField(
                blank=True,
                default='',
                help_text='ID пользователя MAX для личных уведомлений о предложках.',
                max_length=100,
                verbose_name='MAX user ID',
            ),
        ),
    ]
