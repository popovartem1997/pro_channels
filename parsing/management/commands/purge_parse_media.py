"""
Удаление старых файлов медиа парсинга (parsed_items/…) и очистка поля ParsedItem.media.

По умолчанию — число дней из «Ключи API» → блок парсинга Telegram; при необходимости — PARSE_MEDIA_RETENTION_DAYS
из .env (см. effective_parse_media_retention_days). Затем квота PARSE_MEDIA_DISK_QUOTA_BYTES (кроме --skip-quota).
Та же логика, что у Celery purge_parse_media_retention.

Пример:
  python manage.py purge_parse_media
  python manage.py purge_parse_media --days 7
  python manage.py purge_parse_media --skip-quota
"""

from django.core.management.base import BaseCommand

from core.models import effective_parse_media_retention_days
from parsing.media_retention import purge_parse_media_older_than, run_parse_media_cleanup


class Command(BaseCommand):
    help = 'Удалить медиафайлы парсинга старше N дней и обнулить media у старых ParsedItem; опционально квота на диск'

    def add_arguments(self, parser):
        parser.add_argument(
            '--days',
            type=int,
            default=None,
            help='Сколько дней хранить (по умолчанию — PARSE_MEDIA_RETENTION_DAYS из settings, обычно 3)',
        )
        parser.add_argument(
            '--skip-quota',
            action='store_true',
            help='Не применять PARSE_MEDIA_DISK_QUOTA_BYTES (только возрастная очистка)',
        )

    def handle(self, *args, **options):
        days = options['days']
        if days is None:
            days = effective_parse_media_retention_days()
        else:
            days = max(1, min(int(days), 365))
        if options['skip_quota']:
            stats = purge_parse_media_older_than(retention_days=days)
            stats['quota'] = None
        else:
            stats = run_parse_media_cleanup(retention_days=days)
        self.stdout.write(self.style.SUCCESS(f"Готово: {stats}"))
