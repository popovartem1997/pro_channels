"""
Диагностика утреннего дайджеста: время, окно, last_sent_on, Beat, канал.
Запуск: python manage.py morning_digest_status
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from zoneinfo import ZoneInfo

from channels.digest_services import is_digest_due_now
from channels.models import ChannelMorningDigest


class Command(BaseCommand):
    help = 'Показать, почему дайджест мог не сработать по расписанию'

    def handle(self, *args, **options):
        try:
            from django_celery_beat.models import PeriodicTask
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f'django-celery-beat: {exc}'))
            PeriodicTask = None

        if PeriodicTask:
            qs = PeriodicTask.objects.filter(task__icontains='channel_morning_digest_tick')
            if not qs.exists():
                self.stdout.write(
                    self.style.WARNING(
                        'В БД нет PeriodicTask для channels.tasks.channel_morning_digest_tick — '
                        'выполните: python manage.py setup_periodic_tasks'
                    )
                )
            for t in qs:
                st = 'вкл' if t.enabled else 'ВЫКЛ'
                self.stdout.write(f'Beat: «{t.name}» — {st}, task={t.task}')
        else:
            self.stdout.write(self.style.WARNING('PeriodicTask проверить не удалось.'))

        self.stdout.write('')
        self.stdout.write(
            'Убедитесь, что запущены контейнеры celery-beat и celery (воркер с очередью prio).'
        )
        self.stdout.write('')

        cfgs = list(
            ChannelMorningDigest.objects.filter(is_enabled=True).select_related('channel', 'channel__owner')
        )
        if not cfgs:
            self.stdout.write(
                self.style.WARNING(
                    'Нет включённых записей ChannelMorningDigest (is_enabled=True). '
                    'Включите дайджест в настройках канала.'
                )
            )
            return

        server_now = timezone.now()
        self.stdout.write(f'Server now (Django): {server_now.isoformat()}')
        self.stdout.write('')

        for cfg in cfgs:
            ch = cfg.channel
            tz_name = cfg.timezone_name or 'Europe/Moscow'
            try:
                tz = ZoneInfo(tz_name)
            except Exception as e:
                self.stdout.write(self.style.ERROR(f'cfg pk={cfg.pk}: неверный timezone {tz_name!r}: {e}'))
                continue
            local_now = server_now.astimezone(tz)
            due = is_digest_due_now(cfg, local_now)
            token_ok = getattr(ch, 'token_configured', False)

            self.stdout.write(f'--- Дайджест id={cfg.pk} · канал «{ch.name}» (pk={ch.pk}) ---')
            self.stdout.write(f'  Локальное время: {local_now.strftime("%Y-%m-%d %H:%M:%S %Z")}')
            self.stdout.write(f'  send_time: {cfg.send_time} · TZ: {tz_name}')
            self.stdout.write(f'  weekdays: {cfg.weekdays!r} (пусто = каждый день)')
            self.stdout.write(f'  last_sent_on: {cfg.last_sent_on!r} (если сегодня — второй раз за день не шлём)')
            self.stdout.write(f'  channel.is_active: {ch.is_active}')
            self.stdout.write(f'  token_configured: {token_ok} (если False — черновик создаётся, в канал не публикуем)')
            self.stdout.write(
                self.style.SUCCESS(f'  is_digest_due_now сейчас: {due}')
                if due
                else self.style.WARNING(f'  is_digest_due_now сейчас: {due} (вне окна или уже слали сегодня)')
            )
            self.stdout.write('')

        self.stdout.write(
            'Если due=False: подождите окна после send_time (до MORNING_DIGEST_DUE_WINDOW_SEC с) '
            'или сбросьте last_sent_on для теста. Идемпотентность — в БД (last_sent_on + блокировка строки), '
            'Redis для слота больше не используется.'
        )
