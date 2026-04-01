# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0005_alter_parsesource_platform_choices'),
    ]

    operations = [
        migrations.AddField(
            model_name='parsesource',
            name='last_parsed_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Последний парсинг'),
        ),
        migrations.AddField(
            model_name='parsesource',
            name='last_parse_new_items',
            field=models.PositiveIntegerField(default=0, verbose_name='Новых материалов за последний запуск'),
        ),
        migrations.AddField(
            model_name='parsesource',
            name='last_parse_keywords_matched',
            field=models.PositiveIntegerField(default=0, verbose_name='Ключевых слов срабатываний (уник.)'),
        ),
    ]
