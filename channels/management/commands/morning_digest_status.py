"""
Диагностика утреннего дайджеста: время, окно, last_sent_on, Beat, канал.
Запуск: python manage.py morning_digest_status
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from zoneinfo import ZoneInfo

from channels import views as channels_views
from channels.digest_services import is_digest_due_now
from channels.models import ChannelMorningDigest


class Command(BaseCommand):
    help = 'Показать, почему дайджест мог не сработать по расписанию'

    def add_arguments(self, parser):
        parser.add_argument(
            '--all',
            action='store_true',
            help='Показать все записи ChannelMorningDigest (в т.ч. выключенные), не только is_enabled=True',
        )

    def handle(self, *args, **options):
        show_all = options.get('all')
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

        form_ver = getattr(channels_views, 'MORNING_DIGEST_FORM_HANDLER_VERSION', 0)
        if form_ver >= 3:
            self.stdout.write(
                self.style.SUCCESS(
                    f'Код web (форма дайджеста): MORNING_DIGEST_FORM_HANDLER_VERSION={form_ver} — актуально.'
                )
            )
        elif form_ver >= 2:
            self.stdout.write(
                self.style.WARNING(
                    f'Код web (форма дайджеста): MORNING_DIGEST_FORM_HANDLER_VERSION={form_ver} — '
                    f'лучше обновить до ≥3 (один hidden send_time, без конфликта со старым полем time).'
                )
            )
        else:
            self.stdout.write(
                self.style.ERROR(
                    f'Код web (форма дайджеста): MORNING_DIGEST_FORM_HANDLER_VERSION={form_ver} '
                    f'(ожидается ≥3). В образе старый код или слой COPY . /app взят из кэша Docker без новых файлов.'
                )
            )
        self.stdout.write('')

        base_qs = ChannelMorningDigest.objects.select_related('channel', 'channel__owner').order_by('pk')
        total_rows = base_qs.count()
        enabled_rows = base_qs.filter(is_enabled=True).count()
        self.stdout.write(
            f'ChannelMorningDigest в БД: всего записей {total_rows}, из них включённых '
            f'(is_enabled=True, их обрабатывает Beat): {enabled_rows}. '
            f'По умолчанию ниже выводятся только включённые; полный список каналов: '
            f'python manage.py morning_digest_status --all'
        )
        self.stdout.write('')

        cfgs = list(base_qs.filter(is_enabled=True)) if not show_all else list(base_qs)
        if not cfgs:
            self.stdout.write(
                self.style.WARNING(
                    'Нет записей ChannelMorningDigest в БД.'
                    if show_all
                    else (
                        'Нет включённых записей (is_enabled=True). '
                        'Включите переключатель «Включить автоматическую отправку» в настройках утреннего дайджеста '
                        'нужного канала или выполните команду с --all, чтобы увидеть выключенные конфиги.'
                    )
                )
            )
            return
        if show_all:
            self.stdout.write(self.style.NOTICE('Режим --all: подробно по всем записям (включая выключенные).'))
            self.stdout.write('')

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

            en = 'вкл' if cfg.is_enabled else 'ВЫКЛ'
            self.stdout.write(f'--- Дайджест id={cfg.pk} · канал «{ch.name}» (pk={ch.pk}) · is_enabled={en} ---')
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
        self.stdout.write('')
        self.stdout.write(
            'Если send_time не меняется: на сервере сделайте git pull, затем пересбор БЕЗ кэша слоя COPY: '
            'docker compose build --no-cache web && docker compose up -d web. '
            'Если выше VERSION<3 — в контейнере не последняя правка формы времени.'
        )
