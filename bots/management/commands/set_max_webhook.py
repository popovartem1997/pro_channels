"""
Настроить Webhook-подписку для MAX Bot API.

Использование:
  python manage.py set_max_webhook --bot-id 3
  python manage.py set_max_webhook --bot-id 3 --delete
  python manage.py set_max_webhook --bot-id 3 --url https://example.com/bots/webhook/max/3/

Важно:
  - Webhook и Long Polling (run_max_bots) нельзя использовать одновременно.
  - Для webhook нужен публичный HTTPS URL.
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Настроить webhook для MAX бота'

    def add_arguments(self, parser):
        parser.add_argument('--bot-id', type=int, required=True)
        parser.add_argument('--url', type=str, default='')
        parser.add_argument('--delete', action='store_true')

    def handle(self, *args, **options):
        from django.conf import settings
        from django.urls import reverse
        from bots.models import SuggestionBot
        from bots.max_bot.bot import MaxBotAPI

        bot_id = options['bot_id']
        url_override = (options.get('url') or '').strip()
        do_delete = bool(options.get('delete'))

        bot = SuggestionBot.objects.filter(pk=bot_id, platform=SuggestionBot.PLATFORM_MAX).first()
        if not bot:
            raise CommandError(f'MAX бот #{bot_id} не найден.')
        token = (bot.get_token() or '').strip()
        if not token:
            raise CommandError(f'У MAX бота #{bot_id} не задан токен.')

        api = MaxBotAPI(token)

        if do_delete:
            res = api.delete_webhook()
            self.stdout.write(self.style.SUCCESS(f'Webhook удалён: {res or "ok"}'))
            return

        if url_override:
            url = url_override
        else:
            site_url = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
            if not site_url:
                raise CommandError('В settings.SITE_URL не задан публичный URL (нужен для webhook).')
            url = site_url + reverse('bots:max_webhook', kwargs={'bot_id': bot.pk})

        res = api.set_webhook(url)
        self.stdout.write(self.style.SUCCESS(f'Webhook настроен: {url}\nОтвет: {res or "ok"}'))

