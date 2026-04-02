import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0009_historyimportrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='ad_slot_schedule_json',
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Список объектов: {"weekday": 0-6 (пн=0), "times": ["10:00", "14:00", ...]}. '
                'Из расписания генерируются свободные слоты.',
                verbose_name='Расписание слотов (JSON)',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='ad_slot_horizon_days',
            field=models.PositiveIntegerField(
                default=56,
                help_text='Генерация свободных дат для выбора в заявке.',
                verbose_name='На сколько дней вперёд слоты',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='ad_post_lifetime_days',
            field=models.PositiveIntegerField(
                default=7,
                help_text='После публикации — период для акта и учёта; задаётся владельцем канала.',
                verbose_name='Срок «рекламного» поста (дней)',
            ),
        ),
        migrations.AddField(
            model_name='channel',
            name='ad_publish_pause_until',
            field=models.DateTimeField(
                blank=True,
                help_text='Пока время не прошло, очередные посты в этот канал не публикуются (например, оплаченный «топ»).',
                null=True,
                verbose_name='Пауза публикаций до',
            ),
        ),
        migrations.CreateModel(
            name='ChannelAdVolumeDiscount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('min_posts', models.PositiveIntegerField(help_text='Если в заявке выбрано не меньше слотов — применяется эта скидка (берётся наибольшая подходящая ступень).', verbose_name='От количества постов')),
                ('discount_percent', models.DecimalField(decimal_places=2, help_text='Например 10.00 = 10%.', max_digits=5, verbose_name='Скидка, %')),
                ('channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ad_volume_discounts', to='channels.channel', verbose_name='Канал')),
            ],
            options={
                'verbose_name': 'Скидка за объём (реклама)',
                'verbose_name_plural': 'Скидки за объём (реклама)',
                'ordering': ['channel_id', 'min_posts'],
            },
        ),
        migrations.CreateModel(
            name='ChannelAdAddon',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(help_text='Рекомендуется: pin, top_1h, top_2h — публикация учитывает top_* для паузы очереди.', max_length=32, verbose_name='Код')),
                ('title', models.CharField(max_length=120, verbose_name='Название для рекламодателя')),
                ('price', models.DecimalField(decimal_places=2, max_digits=12, verbose_name='Цена (₽)')),
                ('top_duration_minutes', models.PositiveIntegerField(default=0, help_text='Для закрепа — 0. Для топа — 60 или 120 и т.д.; после публикации на это время блокируются прочие посты в канал.', verbose_name='Длительность «топа» (мин.)')),
                ('is_active', models.BooleanField(default=True, verbose_name='Включено')),
                ('channel', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ad_addons', to='channels.channel', verbose_name='Канал')),
            ],
            options={
                'verbose_name': 'Доп. услуга (реклама)',
                'verbose_name_plural': 'Доп. услуги (реклама)',
            },
        ),
        migrations.AddConstraint(
            model_name='channeladvolumediscount',
            constraint=models.UniqueConstraint(fields=('channel', 'min_posts'), name='uniq_channel_ad_vol_discount_min_posts'),
        ),
        migrations.AddConstraint(
            model_name='channeladaddon',
            constraint=models.UniqueConstraint(fields=('channel', 'code'), name='uniq_channel_ad_addon_code'),
        ),
    ]
