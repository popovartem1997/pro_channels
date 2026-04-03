import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0012_channelmorningdigest'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelInterestingFacts',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_enabled', models.BooleanField(default=False, verbose_name='Включено')),
                (
                    'topic',
                    models.TextField(
                        blank=True,
                        help_text='Например: интересные факты о городе Щёлково Московской области. Обязательно, если включена автогенерация.',
                        verbose_name='Тема / запрос',
                    ),
                ),
                (
                    'interval_hours',
                    models.PositiveSmallIntegerField(
                        choices=[
                            (6, 'Каждые 6 часов'),
                            (12, 'Каждые 12 часов'),
                            (24, 'Раз в сутки'),
                            (48, 'Раз в 2 суток'),
                            (72, 'Раз в 3 суток'),
                            (168, 'Раз в неделю'),
                        ],
                        default=24,
                        verbose_name='Периодичность',
                    ),
                ),
                (
                    'last_generated_at',
                    models.DateTimeField(blank=True, null=True, verbose_name='Последняя генерация'),
                ),
                ('last_error', models.TextField(blank=True, verbose_name='Последняя ошибка')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'channel',
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='interesting_facts',
                        to='channels.channel',
                        verbose_name='Канал',
                    ),
                ),
            ],
            options={
                'verbose_name': 'Интересные факты (канал)',
                'verbose_name_plural': 'Интересные факты (каналы)',
            },
        ),
    ]
