"""
Сброс Redis-блокировок Telethon (pch:telethon:sess:*).

Если воркер Celery убили или сервер перезагрузили во время парсинга/импорта истории,
блокировка может оставаться до истечения TTL (до TELETHON_REDIS_LOCK_TTL сек).
Пока она есть, другие задачи ждут TELETHON_REDIS_LOCK_WAIT (по умолчанию 600 с) и падают с ошибкой
«не удалось занять сессию за 600 с».

Безопасно запускать, когда нет активного парсинга Telegram / импорта истории для этого же аккаунта.

Пример (Docker):
  docker compose exec web python manage.py clear_telethon_session_locks
  docker compose exec web python manage.py clear_telethon_session_locks --dry-run
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Удалить Redis-ключи блокировки сессии Telethon (pch:telethon:sess:*)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Только показать ключи, не удалять',
        )

    def handle(self, *args, **options):
        from django.conf import settings

        url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', None) or ''
        if not url:
            raise CommandError('DJANGO_CACHE_REDIS_URL пуст — блокировки через Redis не используются.')

        try:
            import redis
        except ImportError as e:
            raise CommandError('Пакет redis не установлен') from e

        r = redis.from_url(url)
        pattern = 'pch:telethon:sess:*'
        keys = list(r.scan_iter(match=pattern, count=100))
        if not keys:
            self.stdout.write(self.style.SUCCESS('Ключей %s не найдено.' % pattern))
            return

        for k in keys:
            self.stdout.write(str(k))

        if options['dry_run']:
            self.stdout.write(self.style.WARNING('dry-run: удаление пропущено (%d ключей).' % len(keys)))
            return

        deleted = 0
        for k in keys:
            try:
                deleted += int(r.delete(k))
            except Exception as ex:
                self.stderr.write('Не удалось удалить %s: %s' % (k, ex))

        self.stdout.write(self.style.SUCCESS('Удалено ключей: %d' % deleted))
