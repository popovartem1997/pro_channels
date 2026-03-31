from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Создаёт/обновляет периодические задачи Celery Beat для ProChannels."

    def handle(self, *args, **options):
        try:
            from django_celery_beat.models import IntervalSchedule, PeriodicTask
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"django-celery-beat не доступен: {e}"))
            return

        # 1 минута — публикация запланированных постов
        every_1m, _ = IntervalSchedule.objects.get_or_create(
            every=1, period=IntervalSchedule.MINUTES
        )
        PeriodicTask.objects.update_or_create(
            name="posts: check scheduled posts (every 1m)",
            defaults={
                "interval": every_1m,
                "task": "content.tasks.check_scheduled_posts",
                "enabled": True,
            },
        )

        # 10 минут — проверка задач парсинга
        every_10m, _ = IntervalSchedule.objects.get_or_create(
            every=10, period=IntervalSchedule.MINUTES
        )
        PeriodicTask.objects.update_or_create(
            name="parsing: check parse tasks (every 10m)",
            defaults={
                "interval": every_10m,
                "task": "parsing.tasks.check_parse_tasks",
                "enabled": True,
            },
        )

        self.stdout.write(self.style.SUCCESS("Periodic tasks are configured."))

