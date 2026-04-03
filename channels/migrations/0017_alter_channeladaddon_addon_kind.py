from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0016_alter_historyimportrun_celery_task_id'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channeladaddon',
            name='addon_kind',
            field=models.CharField(
                choices=[
                    ('custom', 'Фиксированная цена (как раньше)'),
                    ('top_block', 'Топ-блок: фикс. цена за N часов без других постов'),
                    ('pin_hourly', 'Закреп: цена за час × выбранные часы'),
                ],
                default='custom',
                help_text='Топ-блок: «Часов блока» = сколько часов без других постов (1 = топ на час), «Цена» = разово за услугу. '
                'Закреп почасовой: «Цена» за один час, «Макс. часов закрепа» ≥ 24 если нужен закреп на сутки; рекламодатель укажет часы (например 24).',
                max_length=20,
                verbose_name='Тип опции',
            ),
        ),
    ]
