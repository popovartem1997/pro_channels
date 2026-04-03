"""
Сброс Redis-блокировок Telethon (pch:telethon:sess:*).

Если воркер Celery убили или сервер перезагрузили во время парсинга/импорта истории,
блокировка может оставаться до истечения TTL (до TELETHON_REDIS_LOCK_TTL сек).
Пока она есть, другие задачи ждут TELETHON_REDIS_LOCK_WAIT (по умолчанию 600 с) и падают с ошибкой
«не удалось занять сессию за 600 с».

Безопасно запускать, когда нет активного парсинга Telegram / импорта истории для этого же аккаунта.

То же действие доступно в админке: Задачи парсинга → действие «Снять зависшие блокировки Telethon».

При TELETHON_SESSION_LOCK_BACKEND=file блокировка — fcntl на volume; «снять» её можно только
остановив процесс или убив воркер; clear_telethon_session_locks чистит только Redis.

Пример (Docker):
  docker compose exec web python manage.py clear_telethon_session_locks
  docker compose exec web python manage.py clear_telethon_session_locks --dry-run
"""

from django.core.management.base import BaseCommand, CommandError

from parsing.telethon_locks import clear_telethon_redis_locks


class Command(BaseCommand):
    help = 'Удалить Redis-ключи блокировки сессии Telethon (pch:telethon:sess:*)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать ключи, не удалять',
        )

    def handle(self, *args, **options):
        result = clear_telethon_redis_locks(dry_run=options['dry_run'])
        if result.get('error') == 'no_redis_url':
            raise CommandError(result['message'])

        for k in result.get('keys') or []:
            self.stdout.write(k)

        msg = result.get('message') or ''
        if result.get('ok'):
            self.stdout.write(self.style.SUCCESS(msg))
        else:
            raise CommandError(msg or result.get('error') or 'Ошибка')
