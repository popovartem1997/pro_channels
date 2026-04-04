import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0009_globalapikeys_ord_contract_amount'),
    ]

    operations = [
        migrations.AddField(
            model_name='globalapikeys',
            name='parse_media_retention_days',
            field=models.PositiveSmallIntegerField(
                default=3,
                validators=[
                    django.core.validators.MinValueValidator(1),
                    django.core.validators.MaxValueValidator(365),
                ],
                verbose_name='Парсинг: хранить локальные медиа (дней)',
                help_text=(
                    'Файлы в media/parsed_items/ старше этого срока удаляются (Celery раз в сутки); '
                    'у старых записей парсинга поле «медиа» обнуляется.'
                ),
            ),
        ),
    ]
