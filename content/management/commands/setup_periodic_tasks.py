"""
Management-команда для создания периодических задач Celery Beat.

Использование:
    python manage.py setup_periodic_tasks

Создаёт (или обновляет) записи PeriodicTask в базе данных
для django-celery-beat DatabaseScheduler.
"""
from django.core.management.base import BaseCommand
from django_celery_beat.models import PeriodicTask, IntervalSchedule


TASKS = [
    {
        'name': 'Проверка запланированных постов (каждые 60 сек)',
        'task': 'content.tasks.check_scheduled_posts',
        'interval_every': 60,
        'interval_period': IntervalSchedule.SECONDS,
    },
    {
        'name': 'Проверка задач парсинга (каждые 5 мин)',
        'task': 'parsing.tasks.check_parse_tasks',
        'interval_every': 5,
        'interval_period': IntervalSchedule.MINUTES,
    },
    {
        'name': 'Сбор статистики каналов (каждые 6 часов)',
        'task': 'stats.tasks.sync_channel_stats',
        'interval_every': 6,
        'interval_period': IntervalSchedule.HOURS,
    },
    {
        'name': 'Сбор статистики постов (каждый час)',
        'task': 'stats.tasks.sync_post_stats',
        'interval_every': 1,
        'interval_period': IntervalSchedule.HOURS,
    },
]


class Command(BaseCommand):
    help = 'Создаёт периодические задачи Celery Beat в базе данных'

    def handle(self, *args, **options):
        created_count = 0
        updated_count = 0

        for task_def in TASKS:
            schedule, _ = IntervalSchedule.objects.get_or_create(
                every=task_def['interval_every'],
                period=task_def['interval_period'],
            )

            task, created = PeriodicTask.objects.update_or_create(
                name=task_def['name'],
                defaults={
                    'task': task_def['task'],
                    'interval': schedule,
                    'enabled': True,
                },
            )

            if created:
                created_count += 1
                self.stdout.write(self.style.SUCCESS(f'  + {task.name}'))
            else:
                updated_count += 1
                self.stdout.write(f'  ~ {task.name} (обновлено)')

        self.stdout.write(self.style.SUCCESS(
            f'\nГотово: создано {created_count}, обновлено {updated_count}'
        ))
