"""
HTTP-клиент python-telegram-bot (HTTPXRequest): таймауты и опциональный прокси.

Прокси задаётся в «Ключи API» (GlobalApiKeys.telegram_bot_proxy_url) или TELEGRAM_BOT_PROXY_URL в .env.
Формат: http://user:pass@host:port, https://..., socks5://... (для SOCKS нужен extra [socks] в requirements).

Не поддерживаются ссылки вида tg://proxy (MTProto) — это другой протокол, его понимают клиенты Telegram,
а не HTTPS-запросы к api.telegram.org.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_PROXY_HTTP_ALLOWED = ('http://', 'https://', 'socks5://', 'socks5h://', 'socks4://')


def validate_telegram_http_proxy_url(url: str) -> str:
    """
    Пустая строка — ок. Иначе только HTTP(S)/SOCKS для httpx/requests.
    tg:// (MTProto) — ValueError с пояснением.
    """
    u = (url or '').strip()
    if not u:
        return ''
    low = u.lower()
    if low.startswith('tg://'):
        raise ValueError(
            'Ссылки tg:// (MTProto proxy) не подходят для Bot API: запросы идут на https://api.telegram.org '
            'через HTTP- или SOCKS5-прокси. Нужен отдельный прокси вида http://хост:порт или socks5://хост:порт '
            '(тот же, через который у вас открывается Telegram в браузере, если это HTTP/SOCKS).'
        )
    if not low.startswith(_PROXY_HTTP_ALLOWED):
        raise ValueError(
            'Укажите прокси с протоколом: http://, https://, socks5:// или socks5h://'
        )
    return u


def effective_telegram_bot_proxy_url() -> str:
    from django.conf import settings

    from core.models import get_global_api_keys

    gk = get_global_api_keys()
    u = (getattr(gk, 'telegram_bot_proxy_url', None) or '').strip()
    if u:
        return u
    return (getattr(settings, 'TELEGRAM_BOT_PROXY_URL', '') or '').strip()


def telegram_bot_requests_proxies() -> dict[str, str] | None:
    """Для requests.get/post к api.telegram.org (не httpx)."""
    p = effective_telegram_bot_proxy_url()
    if not p:
        return None
    try:
        p = validate_telegram_http_proxy_url(p)
    except ValueError as exc:
        logger.warning('Прокси Bot API не используется (requests): %s', exc)
        return None
    return {'http': p, 'https': p}


def build_telegram_bot_http_request(*, proxy_url: str | None = None):
    """Единый HTTPXRequest для Bot API: публикация постов, бот-предложка, служебные вызовы.

    В async-корутине Django нельзя дергать ORM — передайте ``proxy_url`` заранее (из синхронного кода),
    либо результат ``effective_telegram_bot_proxy_url()`` до ``asyncio.run``.
    Если ``proxy_url`` не передан, читается из БД/.env (только из sync-контекста).
    """
    from telegram.request import HTTPXRequest

    if proxy_url is not None:
        proxy = (proxy_url or '').strip()
    else:
        proxy = effective_telegram_bot_proxy_url()
    kw: dict = dict(
        connection_pool_size=8,
        connect_timeout=60.0,
        read_timeout=300.0,
        write_timeout=300.0,
        pool_timeout=60.0,
    )
    if proxy:
        kw['proxy'] = validate_telegram_http_proxy_url(proxy)
    try:
        return HTTPXRequest(media_write_timeout=300.0, **kw)
    except TypeError:
        return HTTPXRequest(**kw)
