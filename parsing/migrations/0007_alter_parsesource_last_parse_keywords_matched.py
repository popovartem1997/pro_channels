# Generated manually — синхронизация verbose_name с models.ParseSource

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('parsing', '0006_parsesource_parse_meta'),
    ]

    operations = [
        migrations.AlterField(
            model_name='parsesource',
            name='last_parse_keywords_matched',
            field=models.PositiveIntegerField(
                default=0,
                verbose_name='Уникальных ключевых слов (срабатывания)',
            ),
        ),
    ]
