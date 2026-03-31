"""
Команда для запуска MAX ботов-предложок в режиме Long Polling.

Использование:
    python manage.py run_max_bots
    python manage.py run_max_bots --bot-id 3
"""
import logging
import threading

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запустить MAX боты-предложки (Long Polling)'

    def add_arguments(self, parser):
        parser.add_argument('--bot-id', type=int, default=None)

    def handle(self, *args, **options):
        from bots.models import SuggestionBot
        from bots.max_bot.bot import MAXSuggestionBot

        bot_id = options.get('bot_id')
        qs = SuggestionBot.objects.filter(platform=SuggestionBot.PLATFORM_MAX, is_active=True)
        if bot_id:
            qs = qs.filter(pk=bot_id)

        bots = list(qs)
        if not bots:
            self.stdout.write(self.style.WARNING('Активных MAX ботов не найдено.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Запускаю {len(bots)} MAX бот(ов)...'))

        if len(bots) == 1:
            MAXSuggestionBot(bots[0]).run()
        else:
            threads = []
            for bot_config in bots:
                t = threading.Thread(
                    target=MAXSuggestionBot(bot_config).run,
                    name=f'max-bot-{bot_config.pk}',
                    daemon=True
                )
                threads.append(t)
                t.start()
                self.stdout.write(f'  Запущен поток для "{bot_config.name}"')

            try:
                for t in threads:
                    t.join()
            except KeyboardInterrupt:
                self.stdout.write(self.style.WARNING('\nОстановка...'))
