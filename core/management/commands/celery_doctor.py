"""Проверка: куда смотрит web/воркер (Redis, Celery inspect). Запуск: python manage.py celery_doctor"""

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = 'Проверка CELERY_BROKER_URL, Redis PING и ответа воркеров (celery inspect ping).'

    def handle(self, *args, **options):
        broker = getattr(settings, 'CELERY_BROKER_URL', '')
        cache_url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', '')
        self.stdout.write(f'CELERY_BROKER_URL: {broker}')
        self.stdout.write(f'DJANGO_CACHE_REDIS_URL: {cache_url}')
        self.stdout.write(f'CELERY_TASK_ALWAYS_EAGER: {getattr(settings, "CELERY_TASK_ALWAYS_EAGER", None)}')
        self.stdout.write(f'CELERY_TASK_DEFAULT_QUEUE: {getattr(settings, "CELERY_TASK_DEFAULT_QUEUE", None)}')
        routes = getattr(settings, 'CELERY_TASK_ROUTES', None)
        if routes:
            self.stdout.write(f'CELERY_TASK_ROUTES: {routes}')
            self.stdout.write(
                'Воркер: import_history (импорт TG→MAX), prio (публикация/планировщик), celery (парсинг): '
                'celery -A pro_channels worker -Q import_history,prio,celery -c 4'
            )

        if '127.0.0.1' in broker or 'localhost' in broker.lower():
            self.stdout.write(
                self.style.WARNING(
                    'В URL фигурирует localhost/127.0.0.1. В Docker это адрес САМОГО контейнера, '
                    'а не сервиса redis — воркер и web должны использовать redis://redis:6379/0 '
                    '(docker-compose уже задаёт это в environment).'
                )
            )

        try:
            import redis

            r = redis.from_url(broker, socket_connect_timeout=5, socket_timeout=5)
            r.ping()
            self.stdout.write(self.style.SUCCESS('Redis (broker): PING OK'))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Redis (broker): PING FAIL — {exc}'))

        try:
            from pro_channels.celery import app as celery_app

            insp = celery_app.control.inspect(timeout=5.0)
            if insp is None:
                self.stdout.write(self.style.ERROR('Celery inspect: вернул None (брокер недоступен или нет воркеров).'))
                return
            ping = insp.ping() or {}
            names = list(ping.keys())
            if names:
                self.stdout.write(self.style.SUCCESS(f'Celery workers (ping): {names}'))
            else:
                self.stdout.write(self.style.ERROR('Celery workers (ping): пусто — запущен ли контейнер celery?'))
        except Exception as exc:
            self.stdout.write(self.style.ERROR(f'Celery inspect: {exc}'))
