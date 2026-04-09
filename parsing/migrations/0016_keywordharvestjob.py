import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('channels', '0019_alter_historyimportrun_download_tg_media'),
        ('parsing', '0015_parsekeyword_stats_counters'),
    ]

    operations = [
        migrations.CreateModel(
            name='KeywordHarvestJob',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('target_mode', models.CharField(
                    choices=[
                        ('group_all', 'Все каналы группы (кроме MAX/Instagram)'),
                        ('group_one', 'Один канал из группы'),
                    ],
                    default='group_all',
                    max_length=20,
                    verbose_name='Куда вешать ключевики',
                )),
                ('example_channel', models.CharField(
                    help_text='@username или ссылка t.me/... — откуда читать последние посты',
                    max_length=255,
                    verbose_name='Пример канала в Telegram',
                )),
                ('region_prompt', models.TextField(
                    help_text='Опишите населённый пункт и район (Московская область и т.д.), чтобы AI адаптировал формулировки',
                    verbose_name='Ваш район / контекст',
                )),
                ('max_posts', models.PositiveSmallIntegerField(
                    default=20,
                    help_text='Ограничение сверху — не больше 80',
                    verbose_name='Сколько последних постов взять',
                )),
                ('status', models.CharField(
                    choices=[
                        ('pending', 'В очереди'),
                        ('running', 'Выполняется'),
                        ('ready', 'Готово к выбору'),
                        ('applied', 'Ключевики добавлены'),
                        ('failed', 'Ошибка'),
                    ],
                    default='pending',
                    max_length=20,
                    verbose_name='Статус',
                )),
                ('error_message', models.TextField(blank=True, verbose_name='Текст ошибки')),
                ('posts_snapshot', models.JSONField(blank=True, default=list, verbose_name='Фрагменты постов (для просмотра)')),
                ('suggested_keywords', models.JSONField(blank=True, default=list, verbose_name='Кандидаты от AI')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('applied_at', models.DateTimeField(blank=True, null=True, verbose_name='Добавлено в ключевики')),
                ('channel_group', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='keyword_harvest_jobs',
                    to='channels.channelgroup',
                    verbose_name='Группа каналов',
                )),
                ('created_by', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='keyword_harvest_jobs',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Кто создал',
                )),
                ('target_channel', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='keyword_harvest_jobs',
                    to='channels.channel',
                    verbose_name='Канал (если один)',
                )),
            ],
            options={
                'verbose_name': 'Задача: ключевики с примера канала',
                'verbose_name_plural': 'Очередь ключевиков с примера канала',
                'ordering': ['-created_at'],
            },
        ),
    ]
