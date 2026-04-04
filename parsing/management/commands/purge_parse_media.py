"""
Удаление старых файлов медиа парсинга (parsed_items/…) и очистка поля ParsedItem.media.

По умолчанию — PARSE_MEDIA_RETENTION_DAYS из settings (или 3). Та же логика, что у Celery
parsing.tasks.purge_parse_media_retention.

Пример:
  python manage.py purge_parse_media
  python manage.py purge_parse_media --days 7
"""

from django.conf import settings
from django.core.management.base import BaseCommand

from parsing.media_retention import purge_parse_media_older_than


class Command(BaseCommand):
    help = 'Удалить медиафайлы парсинга старше N дней и обнулить media у старых ParsedItem'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Сколько дней хранить (по умолчанию — PARSE_MEDIA_RETENTION_DAYS из settings, обычно 3)',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days is None:
            days = int(getattr(settings, 'PARSE_MEDIA_RETENTION_DAYS', 3) or 3)
        days = max(1, min(int(days), 365))
        stats = purge_parse_media_older_than(retention_days=days)
        self.stdout.write(self.style.SUCCESS(f"Готово: {stats}"))
