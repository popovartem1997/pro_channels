import datetime

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0011_channeladaddon_addon_kind'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelMorningDigest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False, verbose_name='Включено')),
                (
                    'send_time',
                    models.TimeField(default=datetime.time(5, 0), verbose_name='Время отправки (локальное)'),
                ),
                (
                    'timezone_name',
                    models.CharField(
                        default='Europe/Moscow',
                        help_text='Например: Europe/Moscow, Asia/Yekaterinburg',
                        max_length=64,
                        verbose_name='Часовой пояс (IANA)',
                    ),
                ),
                (
                    'weekdays',
                    models.JSONField(
                        blank=True,
                        default=list,
                        help_text='Список чисел 0–6: пн=0 … вс=6. Пустой список — все дни.',
                        verbose_name='Дни недели (пусто = каждый день)',
                    ),
                ),
                (
                    'latitude',
                    models.DecimalField(decimal_places=6, default=55.7558, max_digits=9, verbose_name='Широта'),
                ),
                (
                    'longitude',
                    models.DecimalField(decimal_places=6, default=37.6173, max_digits=9, verbose_name='Долгота'),
                ),
                (
                    'location_label',
                    models.CharField(
                        blank=True,
                        help_text='Например: Москва — показывается в шапке блока погоды.',
                        max_length=120,
                        verbose_name='Название места (подпись)',
                    ),
                ),
                (
                    'country_for_holidays',
                    models.CharField(
                        default='RU',
                        help_text='RU, UA, BY, KZ и др. — библиотека holidays.',
                        max_length=2,
                        verbose_name='Код страны для праздников (ISO)',
                    ),
                ),
                (
                    'horoscope_sign',
                    models.CharField(
                        choices=[
                            ('general', 'Общий (без знака)'),
                            ('aries', 'Овен'),
                            ('taurus', 'Телец'),
                            ('gemini', 'Близнецы'),
                            ('cancer', 'Рак'),
                            ('leo', 'Лев'),
                            ('virgo', 'Дева'),
                            ('libra', 'Весы'),
                            ('scorpio', 'Скорпион'),
                            ('sagittarius', 'Стрелец'),
                            ('capricorn', 'Козерог'),
                            ('aquarius', 'Водолей'),
                            ('pisces', 'Рыбы'),
                        ],
                        default='general',
                        max_length=20,
                        verbose_name='Знак для гороскопа',
                    ),
                ),
                ('block_date', models.BooleanField(default=True, verbose_name='Дата')),
                ('block_weather', models.BooleanField(default=True, verbose_name='Погода по периодам дня')),
                ('block_sun', models.BooleanField(default=True, verbose_name='Восход и закат')),
                ('block_quote', models.BooleanField(default=True, verbose_name='Цитата дня')),
                ('block_english', models.BooleanField(default=True, verbose_name='Английское слово')),
                ('block_holidays', models.BooleanField(default=True, verbose_name='Праздники сегодня')),
                ('block_horoscope', models.BooleanField(default=True, verbose_name='Гороскоп на сегодня')),
                ('block_image', models.BooleanField(default=True, verbose_name='Картинка к посту')),
                (
                    'use_ai_quote',
                    models.BooleanField(default=True, verbose_name='Цитату генерировать через DeepSeek'),
                ),
                (
                    'use_ai_english',
                    models.BooleanField(default=True, verbose_name='Слово дня — через DeepSeek'),
                ),
                (
                    'use_ai_horoscope',
                    models.BooleanField(default=True, verbose_name='Гороскоп — через DeepSeek'),
                ),
                (
                    'image_seed_extra',
                    models.CharField(
                        blank=True,
                        help_text='Уникальность картинки дня между каналами (любой короткий текст).',
                        max_length=80,
                        verbose_name='Доп. seed для картинки',
                    ),
                ),
                (
                    'last_sent_on',
                    models.DateField(blank=True, null=True, verbose_name='Последняя отправка (локальная дата)'),
                ),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'channel',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='morning_digest',
                        to='channels.channel',
                        verbose_name='Канал',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Утренний дайджест канала',
                'verbose_name_plural': 'Утренние дайджесты каналов',
            },
        ),
    ]
