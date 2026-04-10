"""
HTTP-клиент python-telegram-bot (HTTPXRequest): таймауты и опциональный прокси.

Прокси задаётся в «Ключи API» (GlobalApiKeys.telegram_bot_proxy_url) или TELEGRAM_BOT_PROXY_URL в .env.
Формат: http://user:pass@host:port, https://..., socks5://... (для SOCKS нужен extra [socks] в requirements).
"""
from __future__ import annotations


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
        kw['proxy'] = proxy
    try:
        return HTTPXRequest(media_write_timeout=300.0, **kw)
    except TypeError:
        return HTTPXRequest(**kw)
