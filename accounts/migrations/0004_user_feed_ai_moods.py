from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0003_user_ad_payment_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='feed_ai_moods',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Пусто — встроенный набор. Иначе JSON-массив объектов с полями id, label, title, instruction.',
                verbose_name='AI: интонации в ленте (парсинг)',
            ),
        ),
    ]
