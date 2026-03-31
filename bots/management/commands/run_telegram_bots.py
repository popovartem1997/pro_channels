"""
Команда для запуска Telegram ботов-предложок в режиме Long Polling.

Использование:
    python manage.py run_telegram_bots                # запустить все активные боты
    python manage.py run_telegram_bots --bot-id 1    # только конкретный бот

В production рекомендуется webhook (Django view) + supervisor/systemd.
Long Polling удобен для разработки и для небольших нагрузок.

Запуск нескольких ботов одновременно:
    Команда запускает каждый бот в отдельном потоке.
"""
import asyncio
import logging
import threading

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запустить Telegram боты-предложки (Long Polling)'

    def add_arguments(self, parser):
        parser.add_argument(
            '--bot-id',
            type=int,
            default=None,
            help='ID конкретного бота (по умолчанию — все активные)'
        )

    def handle(self, *args, **options):
        from bots.models import SuggestionBot
        from bots.telegram.handlers import build_application

        bot_id = options.get('bot_id')
        qs = SuggestionBot.objects.filter(platform=SuggestionBot.PLATFORM_TELEGRAM, is_active=True)
        if bot_id:
            qs = qs.filter(pk=bot_id)

        bots = list(qs)
        if not bots:
            self.stdout.write(self.style.WARNING('Активных Telegram ботов не найдено.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Запускаю {len(bots)} Telegram бот(ов)...'))

        if len(bots) == 1:
            # Один бот — запускаем в главном потоке
            self._run_bot(bots[0])
        else:
            # Несколько ботов — каждый в своём потоке
            threads = []
            for bot_config in bots:
                t = threading.Thread(
                    target=self._run_bot,
                    args=(bot_config,),
                    name=f'tg-bot-{bot_config.pk}',
                    daemon=True
                )
                threads.append(t)
                t.start()
                self.stdout.write(f'  Запущен поток для "{bot_config.name}" (id={bot_config.pk})')

            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING('\nОстановка...'))

    def _run_bot(self, bot_config):
        """Запустить один бот в текущем потоке (blocking)."""
        from bots.telegram.handlers import build_application

        self.stdout.write(f'[TG] Старт: {bot_config.name}')
        try:
            app = build_application(bot_config)
            app.run_polling(
                poll_interval=1,
                drop_pending_updates=True,
            )
        except Exception as e:
            logger.exception('[TG] Бот "%s" упал: %s', bot_config.name, e)
            self.stderr.write(f'[TG] Ошибка бота "{bot_config.name}": {e}')
