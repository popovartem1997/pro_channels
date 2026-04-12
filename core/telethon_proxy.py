"""
Прокси для Telethon (исходящий TCP к серверам Telegram, MTProto).

По умолчанию берётся тот же URL, что в «Ключи API» → «Прокси для Telegram Bot API»
(``GlobalApiKeys.telegram_bot_proxy_url``), затем запасной ``TELEGRAM_BOT_PROXY_URL`` из .env
(см. ``effective_telegram_bot_proxy_url``). Это те же http(s)/socks5, что и для Bot API.

Если нужен **другой** прокси только для Telethon — задайте ``TELETHON_PROXY_URL`` в .env (перекрывает поле).

Формат URL — обычный SOCKS5 или HTTP CONNECT, например:
  socks5://127.0.0.1:1080
  socks5://user:pass@host:1080
  socks5h://127.0.0.1:1080   (удалённый DNS)
  http://proxy:3128

Публичные tg://proxy?server=…&port=…&secret=… (MTProto-прокси) Telethon подключает иначе:
  connection=ConnectionTcpMTProxyRandomizedIntermediate, proxy=(host, port, secret_bytes)
Такие ссылки сюда не подставляются — нужен либо отдельный код, либо локальный SOCKS
(например, клиент, который поднимает socks5 на localhost).
"""
from __future__ import annotations

import logging
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def parse_telethon_proxy_url(url: str):
    """
    Возвращает значение для аргумента ``proxy=`` у telethon.TelegramClient (tuple).

    См. https://docs.telethon.dev/ — раздел про proxies.
    """
    u = urlparse((url or '').strip())
    scheme = (u.scheme or '').lower()
    host = u.hostname
    if not host:
        raise ValueError('В URL прокси нужны схема и хост (например socks5://127.0.0.1:1080).')

    if scheme == 'tg':
        raise ValueError(
            'Ссылка tg:// — это MTProto-прокси; для Telethon используйте класс соединения '
            'ConnectionTcpMTProxyRandomizedIntermediate и tuple (host, port, secret), '
            'или поднимите SOCKS5 локально и укажите socks5://127.0.0.1:порт.'
        )

    port = u.port
    user = unquote(u.username) if u.username else None
    pwd = unquote(u.password) if u.password else ''

    if scheme in ('socks5', 'socks5h'):
        if port is None:
            port = 1080
        rdns = scheme == 'socks5h'
        if user is not None:
            return ('socks5', host, port, rdns, user, pwd)
        return ('socks5', host, port)

    if scheme in ('http', 'https'):
        if port is None:
            port = 8080 if scheme == 'http' else 443
        if user is not None:
            return ('http', host, port, True, user, pwd)
        return ('http', host, port)

    raise ValueError(
        f'Неподдерживаемая схема «{scheme}». Для Telethon укажите socks5://, socks5h:// или http://.'
    )


def merge_telethon_proxy_from_settings(kwargs: dict) -> dict:
    """Копирует kwargs и при необходимости добавляет ``proxy`` для Telethon.

    Приоритет: ``TELETHON_PROXY_URL`` (.env) → иначе то же, что для Bot API (поле в «Ключи API»
    или ``TELEGRAM_BOT_PROXY_URL`` в .env).
    """
    from django.conf import settings

    from core.telegram_bot_request import effective_telegram_bot_proxy_url

    raw = (getattr(settings, 'TELETHON_PROXY_URL', '') or '').strip()
    if not raw:
        raw = (effective_telegram_bot_proxy_url() or '').strip()
    if not raw:
        return kwargs
    try:
        proxy = parse_telethon_proxy_url(raw)
    except ValueError as exc:
        logger.warning('Прокси Telethon не применён (%s): %s', raw[:80], exc)
        return kwargs
    out = dict(kwargs)
    out['proxy'] = proxy
    return out
