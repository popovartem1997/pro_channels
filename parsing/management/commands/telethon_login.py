"""
Интерактивная авторизация Telethon (Telegram user API) для парсинга.

Запускается один раз на сервере, сохраняет session-файл в media/telethon_session.

Использование (Docker):
  docker compose exec -it web python3 manage.py telethon_login
"""

from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = 'Авторизовать Telethon session для Telegram парсинга'

    def handle(self, *args, **options):
        from django.conf import settings
        from core.models import get_global_api_keys
        import asyncio

        keys = get_global_api_keys()
        api_id = (keys.telegram_api_id or '').strip()
        api_hash = (keys.get_telegram_api_hash() or '').strip()
        if not api_id or not api_hash:
            raise CommandError('TELEGRAM_API_ID / TELEGRAM_API_HASH не заданы (Ключи API → Парсинг Telegram).')

        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = str(session_dir / 'user_default')

        from parsing.tasks import _telethon_client_kwargs

        _tc_kw = _telethon_client_kwargs()

        async def _login():
            from telethon import TelegramClient

            client = TelegramClient(session_path, int(api_id), api_hash, **_tc_kw)
            await client.start()  # интерактивно спросит телефон/код при необходимости
            me = await client.get_me()
            await client.disconnect()
            return me

        me = asyncio.run(_login())
        self.stdout.write(self.style.SUCCESS(f'Telethon авторизован: {getattr(me, "username", None) or getattr(me, "id", "")}'))

