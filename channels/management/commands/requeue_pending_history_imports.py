"""
Снова отправить в Celery задачи импорта истории TG→MAX со статусом «В очереди» (pending).

Нужно, если:
  - долго не работали контейнеры celery / redis;
  - очередь в Redis потеряна (редко — например FLUSHALL на брокере);
  - задача так и не перешла в «В работе».

Не снимает Redis-блокировки Telethon — для этого есть parsing.clear_telethon_session_locks.

Примеры:
  docker compose exec web python manage.py requeue_pending_history_imports --dry-run
  docker compose exec web python manage.py requeue_pending_history_imports
  docker compose exec web python manage.py requeue_pending_history_imports --run-ids 12 15
"""

from django.core.management.base import BaseCommand

from channels.models import HistoryImportRun
from channels.tasks import import_tg_history_to_max_task


class Command(BaseCommand):
    help = 'Повторно поставить в очередь Celery импорты истории (status=pending)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--run-ids',
            nargs='*',
            type=int,
            help='Только указанные id запусков (иначе все pending)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать, какие записи затронуты',
        )

    def handle(self, *args, **options):
        qs = HistoryImportRun.objects.filter(status=HistoryImportRun.STATUS_PENDING).order_by('pk')
        if options['run_ids']:
            qs = qs.filter(pk__in=options['run_ids'])

        ids = list(qs.values_list('pk', flat=True))
        if not ids:
            self.stdout.write(self.style.SUCCESS('Нет записей HistoryImportRun в статусе pending.'))
            return

        self.stdout.write('Записи: %s' % ', '.join(str(x) for x in ids))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('dry-run: в Celery ничего не отправлено.'))
            return

        for pk in ids:
            import_tg_history_to_max_task.delay(pk)
            self.stdout.write('Отправлено в очередь: run_id=%s' % pk)

        self.stdout.write(self.style.SUCCESS('Готово. Убедитесь, что контейнеры celery и redis запущены.'))
