"""
Команда для запуска VK ботов-предложок в режиме Long Polling.

Использование:
    python manage.py run_vk_bots
    python manage.py run_vk_bots --bot-id 2

В production рекомендуется использовать VK Callback API (webhook view).
"""
import logging
import threading

from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Запустить VK боты-предложки (Long Polling)'

    def add_arguments(self, parser):
        parser.add_argument('--bot-id', type=int, default=None)

    def handle(self, *args, **options):
        from bots.models import SuggestionBot
        from bots.vk.bot import VKSuggestionBot

        bot_id = options.get('bot_id')
        qs = SuggestionBot.objects.filter(platform=SuggestionBot.PLATFORM_VK, is_active=True)
        if bot_id:
            qs = qs.filter(pk=bot_id)

        bots = list(qs)
        if not bots:
            self.stdout.write(self.style.WARNING('Активных VK ботов не найдено.'))
            return

        self.stdout.write(self.style.SUCCESS(f'Запускаю {len(bots)} VK бот(ов)...'))

        if len(bots) == 1:
            VKSuggestionBot(bots[0]).run()
        else:
            threads = []
            for bot_config in bots:
                t = threading.Thread(
                    target=VKSuggestionBot(bot_config).run,
                    name=f'vk-bot-{bot_config.pk}',
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
