"""
Превью вложений MAX для прокси (лента, миниатюры).
Telegram file_id здесь не используется — только token/url из Bot API.
"""
from __future__ import annotations

import logging
from typing import Any

import requests

logger = logging.getLogger(__name__)


def messages_chain_from_raw(raw: Any) -> list[dict]:
    """Как в content/views: собрать цепочку message dict из raw_data предложки."""
    if not isinstance(raw, dict):
        return []
    if isinstance(raw.get('messages'), list) and raw.get('messages'):
        return [m for m in raw['messages'] if isinstance(m, dict)]
    if isinstance(raw.get('message'), dict):
        return [raw['message']]
    if isinstance(raw.get('last_message'), dict):
        return [raw['last_message']]
    return [raw] if raw.get('body') is not None or raw.get('mid') else []


def deep_http_urls(obj: Any) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def walk(x: Any) -> None:
        if x is None:
            return
        if isinstance(x, str):
            if x.startswith('http') and x not in seen:
                seen.add(x)
                urls.append(x)
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
            return
        if isinstance(x, list):
            for it in x:
                walk(it)

    walk(obj)
    return urls


def attachment_entries_from_raw(raw: Any) -> list[dict[str, Any]]:
    """Порядок как у media_file_ids в боте: вложения по цепочке сообщений."""
    out: list[dict[str, Any]] = []
    for m in messages_chain_from_raw(raw):
        body = m.get('body') or {}
        if not isinstance(body, dict):
            continue
        atts = body.get('attachments') or []
        if not isinstance(atts, list):
            continue
        for att in atts:
            if not isinstance(att, dict):
                continue
            payload = att.get('payload') or {}
            token = None
            if isinstance(payload, dict):
                token = payload.get('token') or payload.get('id')
            if not token:
                continue
            out.append({
                'token': str(token),
                'type': (att.get('type') or '').lower(),
                'att': att,
            })
    return out


def urls_from_attachment_dict(att: dict) -> list[str]:
    if not isinstance(att, dict):
        return []
    urls: list[str] = []
    payload = att.get('payload') or {}
    if isinstance(payload, dict):
        for k in ('url', 'src', 'download_url', 'downloadUrl', 'file_url'):
            v = payload.get(k)
            if isinstance(v, str) and v.startswith('http'):
                urls.append(v)
        for k in ('sizes', 'variants', 'images', 'files'):
            arr = payload.get(k)
            if isinstance(arr, list):
                for it in arr:
                    if isinstance(it, dict):
                        u = it.get('url') or it.get('src')
                        if isinstance(u, str) and u.startswith('http'):
                            urls.append(u)
    if not urls:
        urls = deep_http_urls(att)
    return urls


def resolve_preview_urls(api: Any, token: str, att_type: str, att: dict) -> list[str]:
    """Собрать кандидатные URL (для картинки в <img> — в приоритете миниатюра для video)."""
    from bots.max_bot.bot import MaxBotAPI  # noqa: avoid circular

    urls: list[str] = []
    if isinstance(att, dict):
        urls.extend(urls_from_attachment_dict(att))

    if urls:
        return urls

    if not token:
        return []

    t = (att_type or '').lower()
    if t == 'video':
        info = api.get_video(token) if isinstance(api, MaxBotAPI) else {}
        if isinstance(info, dict):
            th = info.get('thumbnail')
            if th:
                urls.extend(deep_http_urls(th))
                urls.extend(urls_from_attachment_dict({'type': 'image', 'payload': th}) if isinstance(th, dict) else [])
            if not urls:
                urls.extend(deep_http_urls(info))
        return urls

    if t in ('image', 'photo'):
        info = api.get_image(token) if isinstance(api, MaxBotAPI) else {}
        urls.extend(deep_http_urls(info))
        if not urls:
            info = api.get_file(token) if isinstance(api, MaxBotAPI) else {}
            urls.extend(deep_http_urls(info))
        return urls

    if t == 'file':
        info = api.get_file(token) if isinstance(api, MaxBotAPI) else {}
        urls.extend(deep_http_urls(info))
        if not urls:
            info = api.get_image(token) if isinstance(api, MaxBotAPI) else {}
            urls.extend(deep_http_urls(info))
        return urls

    # Тип неизвестен — пробуем по очереди
    for method in ('get_video', 'get_image', 'get_file'):
        fn = getattr(api, method, None)
        if not callable(fn):
            continue
        info = fn(token) or {}
        if isinstance(info, dict) and t == '' and method == 'get_video':
            th = info.get('thumbnail')
            if th:
                urls.extend(deep_http_urls(th))
        urls.extend(deep_http_urls(info))
        if urls:
            break
    return urls


def download_binary(url: str, bot_token: str) -> tuple[bytes, str]:
    headers_common = {
        'User-Agent': 'Mozilla/5.0 (compatible; ProChannelsBot/1.0; +https://prochannels.ru)',
        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
        'Referer': 'https://max.ru/',
    }
    r = requests.get(url, timeout=30, stream=True, headers=headers_common)
    if r.status_code >= 400:
        r = requests.get(
            url,
            headers={**headers_common, 'Authorization': bot_token},
            timeout=30,
            stream=True,
        )
    r.raise_for_status()
    data = r.content or b''
    ct = (r.headers.get('Content-Type') or 'application/octet-stream').split(';')[0].strip()
    return data, ct


def fetch_max_preview_bytes(api: Any, bot_token: str, token: str, att_type: str, att: dict) -> tuple[bytes, str] | None:
    """
    Байты для отдачи в HttpResponse. Для превью в ленте предпочитаем image/*.
    """
    candidates = resolve_preview_urls(api, token, att_type, att if isinstance(att, dict) else {})
    if not candidates:
        return None

    # Два прохода: сначала картинки по Content-Type, потом любой не-text/html
    for pass_img_only in (True, False):
        for url in candidates:
            try:
                data, ct = download_binary(url, bot_token)
                ct_l = (ct or '').lower()
                if len(data) < 32:
                    continue
                if data[:20].lstrip().startswith(b'<!DOCTYPE'):
                    continue
                if 'text/html' in ct_l or ct_l.startswith('text/'):
                    continue
                if pass_img_only:
                    if ct_l.startswith('image/'):
                        return data, ct
                    continue
                return data, ct
            except Exception as e:
                logger.debug('MAX preview download failed url=%s: %s', url, e)
                continue
    return None
