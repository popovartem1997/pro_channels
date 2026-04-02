from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0009_remove_tg_premium_emoji_and_import'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='ad_top_block_minutes',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Если >0 (оплаченный «топ»), после успешной публикации канал не публикует остальные посты это время.',
                verbose_name='Пауза очереди после публикации (мин.)',
            ),
        ),
    ]
