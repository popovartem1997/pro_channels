import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('content', '0011_post_campaign_application'),
        ('parsing', '0015_parsekeyword_stats_counters'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='parsing_publish_stats_applied',
            field=models.BooleanField(
                default=False,
                verbose_name='Статистика публикации по парсингу учтена',
            ),
        ),
        migrations.AddField(
            model_name='post',
            name='source_parsed_item',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='derived_posts',
                to='parsing.parseditem',
                verbose_name='Источник: распарсенный элемент',
            ),
        ),
        migrations.AddField(
            model_name='post',
            name='source_parse_keyword',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='derived_posts',
                to='parsing.parsekeyword',
                verbose_name='Источник: ключевик',
            ),
        ),
    ]
