from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('content', '0012_post_parsing_source_and_stats_flag'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='telegram_followup_text',
            field=models.TextField(blank=True, verbose_name='Доп. текст (plain, гороскоп)'),
        ),
        migrations.AddField(
            model_name='post',
            name='telegram_followup_html',
            field=models.TextField(blank=True, verbose_name='Доп. текст HTML (гороскоп для TG)'),
        ),
        migrations.AddField(
            model_name='post',
            name='telegram_first_message_media_only',
            field=models.BooleanField(default=False, verbose_name='TG: сначала только медиа'),
        ),
    ]
