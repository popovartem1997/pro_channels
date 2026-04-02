# Generated manually for ChannelAdAddon kinds (top block / pin hourly).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0010_channel_ad_wizard_and_addons'),
    ]

    operations = [
        migrations.AddField(
            model_name='channeladaddon',
            name='addon_kind',
            field=models.CharField(
                choices=[
                    ('custom', 'Фиксированная цена (как раньше)'),
                    ('top_block', 'Топ-блок: фикс. цена за N часов без других постов'),
                    ('pin_hourly', 'Закреп: цена за час × выбранные часы'),
                ],
                default='custom',
                help_text='Топ-блок: укажите «Часов блока» и общую цену. Закреп почасовой: код обычно pin, в «Цена» — стоимость одного часа.',
                max_length=20,
                verbose_name='Тип опции',
            ),
        ),
        migrations.AddField(
            model_name='channeladaddon',
            name='block_hours',
            field=models.PositiveSmallIntegerField(
                blank=True,
                help_text='Только для типа «Топ-блок»: 1, 2, 3… На это время после публикации не ставятся обычные посты.',
                null=True,
                verbose_name='Часов блока (топ)',
            ),
        ),
        migrations.AddField(
            model_name='channeladaddon',
            name='max_pin_hours',
            field=models.PositiveSmallIntegerField(
                default=72,
                help_text='Для «Закреп почасовой»: ограничение в мастере заявки.',
                verbose_name='Макс. часов закрепа',
            ),
        ),
        migrations.AlterField(
            model_name='channeladaddon',
            name='price',
            field=models.DecimalField(
                decimal_places=2,
                help_text='Для «Закреп почасовой» — цена за один час. Для топ-блока и custom — полная стоимость опции.',
                max_digits=12,
                verbose_name='Цена (₽)',
            ),
        ),
        migrations.AlterField(
            model_name='channeladaddon',
            name='top_duration_minutes',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Для старых опций с типом «Фиксированная»: если код начинается с top_, используется это значение. Для «Топ-блок» можно оставить 0 — берётся block_hours.',
                verbose_name='Длительность «топа» (мин., устар.)',
            ),
        ),
    ]
