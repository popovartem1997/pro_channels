"""
Сброс Redis-блокировок Telethon (см. parsing.tasks._telethon_session_lock).
"""
from __future__ import annotations

from typing import Any


def clear_telethon_redis_locks(*, dry_run: bool = False) -> dict[str, Any]:
    """
    Удаляет ключи pch:telethon:sess:* в Redis (DJANGO_CACHE_REDIS_URL).

    Возвращает dict:
      ok: bool
      keys: list[str] — найденные ключи
      deleted: int — удалено (0 при dry_run)
      message: str — человекочитаемо
      error: str | None
    """
    from django.conf import settings

    url = getattr(settings, 'DJANGO_CACHE_REDIS_URL', None) or ''
    if not url:
        return {
            'ok': False,
            'keys': [],
            'deleted': 0,
            'message': 'Redis для кэша не настроен (DJANGO_CACHE_REDIS_URL пуст).',
            'error': 'no_redis_url',
        }

    try:
        import redis
    except ImportError as e:
        return {
            'ok': False,
            'keys': [],
            'deleted': 0,
            'message': 'Пакет redis не установлен.',
            'error': str(e),
        }

    try:
        r = redis.from_url(url)
        pattern = 'pch:telethon:sess:*'
        raw_keys = list(r.scan_iter(match=pattern, count=100))
        keys = [k.decode('utf-8', errors='replace') if isinstance(k, (bytes, bytearray)) else str(k) for k in raw_keys]
        if not keys:
            return {
                'ok': True,
                'keys': [],
                'deleted': 0,
                'message': 'Ключей блокировки Telethon не найдено.',
                'error': None,
            }
        if dry_run:
            return {
                'ok': True,
                'keys': keys,
                'deleted': 0,
                'message': f'Найдено ключей: {len(keys)} (dry-run, удаление не выполнялось).',
                'error': None,
            }
        deleted = 0
        for k in raw_keys:
            try:
                deleted += int(r.delete(k))
            except Exception:
                pass
        return {
            'ok': True,
            'keys': keys,
            'deleted': deleted,
            'message': f'Удалено ключей блокировки: {deleted}.',
            'error': None,
        }
    except Exception as e:
        return {
            'ok': False,
            'keys': [],
            'deleted': 0,
            'message': str(e),
            'error': str(e),
        }
