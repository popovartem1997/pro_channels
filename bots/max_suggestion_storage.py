"""
Сохранение вложений MAX на диск в момент получения предложки.
При одобрении файлы копируются в Post без повторного запроса к CDN MAX.
"""
from __future__ import annotations

import io
import logging

import requests
from django.core.files.base import ContentFile
from django.db.models import Max

logger = logging.getLogger(__name__)


def _deep_http_urls(obj) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()

    def walk(x):
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


def _iter_attachment_urls(att: dict) -> list[str]:
    urls: list[str] = []
    if not isinstance(att, dict):
        return urls
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
        urls = _deep_http_urls(att)
    return urls


def persist_max_suggestion_attachments(suggestion, bot_token: str) -> int:
    """
    Сохранить вложения со всех сообщений предложки (цепочка «текст + фото» в MAX).
    Идемпотентно: повторный вызов не дублирует файлы (attachment_key).
    """
    raw = suggestion.raw_data or {}
    msgs: list[dict] = []
    if isinstance(raw, dict):
        prev = raw.get('messages')
        if isinstance(prev, list):
            msgs = [m for m in prev if isinstance(m, dict)]
        if not msgs and raw.get('mid'):
            msgs = [raw]
    total = 0
    for m in msgs:
        total += persist_max_message_attachments(suggestion, m, bot_token)
    return total


def persist_max_message_attachments(suggestion, message: dict, bot_token: str) -> int:
    """
    Скачать вложения из одного сообщения MAX и сохранить в SuggestionStoredMedia.
    Идемпотентно по attachment_key (токен или mid+idx).
    """
    from bots.models import SuggestionStoredMedia
    from bots.max_bot.bot import MaxBotAPI
    from content.models import PostMedia

    if not isinstance(message, dict) or not bot_token:
        return 0
    body = message.get('body') or {}
    if not isinstance(body, dict):
        return 0
    atts = body.get('attachments') or []
    if not isinstance(atts, list):
        return 0

    mid = str(message.get('mid') or '')
    api = MaxBotAPI(bot_token)
    base_order = SuggestionStoredMedia.objects.filter(suggestion=suggestion).aggregate(m=Max('order'))['m']
    next_order = int(base_order) if base_order is not None else 0
    saved = 0

    for idx, att in enumerate(atts[:15]):
        if not isinstance(att, dict):
            continue
        att_type = (att.get('type') or '').lower()
        media_type = PostMedia.TYPE_DOCUMENT
        if att_type in ('image', 'photo'):
            media_type = PostMedia.TYPE_PHOTO
        elif att_type == 'video':
            media_type = PostMedia.TYPE_VIDEO
        elif att_type == 'file':
            media_type = PostMedia.TYPE_DOCUMENT

        payload = att.get('payload') or {}
        token_key = ''
        if isinstance(payload, dict):
            token_key = str(payload.get('token') or payload.get('id') or '')
        akey = token_key if token_key else (f'{mid}_{idx}' if mid else f'nomid_{idx}_{hash(str(att)) % 10_000_000}')
        akey = akey[:280]

        if SuggestionStoredMedia.objects.filter(suggestion=suggestion, attachment_key=akey).exists():
            continue

        urls = _iter_attachment_urls(att)
        if not urls:
            try:
                token = token_key or None
                if token:
                    if att_type == 'video':
                        urls = _deep_http_urls(api.get_video(token))
                    elif att_type in ('image', 'photo'):
                        urls = _deep_http_urls(api.get_image(token))
                        if not urls:
                            urls = _deep_http_urls(api.get_file(token))
                    elif att_type == 'file':
                        urls = _deep_http_urls(api.get_file(token))
                        if not urls:
                            urls = _deep_http_urls(api.get_image(token))
                    else:
                        urls = _deep_http_urls(api.get_image(token))
                        if not urls:
                            urls = _deep_http_urls(api.get_file(token))
                        if not urls:
                            urls = _deep_http_urls(api.get_video(token))
            except Exception as e:
                logger.debug('MAX store: resolve URL failed: %s', e)
                urls = []

        if not urls:
            continue

        url = urls[0]
        try:
            headers_common = {
                'User-Agent': 'Mozilla/5.0 (compatible; ProChannelsBot/1.0)',
                'Accept': 'image/avif,image/webp,image/*,*/*;q=0.8',
                'Referer': 'https://max.ru/',
            }
            dl = requests.get(url, timeout=45, stream=True, headers=headers_common)
            if dl.status_code >= 400:
                dl = requests.get(
                    url,
                    headers={**headers_common, 'Authorization': bot_token},
                    timeout=45,
                    stream=True,
                )
            dl.raise_for_status()
            ct = (dl.headers.get('Content-Type') or '').lower()
            if ct.startswith('text/') or 'json' in ct:
                continue
            data_bytes = dl.content or b''
            if len(data_bytes) < 50:
                continue
            if data_bytes[:20].lstrip().startswith(b'<!DOCTYPE'):
                continue
            if media_type == PostMedia.TYPE_PHOTO:
                try:
                    from PIL import Image

                    im = Image.open(io.BytesIO(data_bytes))
                    im.load()
                    w, h = im.size
                    if w <= 8 or h <= 8:
                        continue
                except Exception:
                    continue
            ext = 'bin'
            if 'image/' in ct:
                ext = ct.split('image/', 1)[1].split(';', 1)[0] or 'jpg'
            elif 'video/' in ct:
                ext = ct.split('video/', 1)[1].split(';', 1)[0] or 'mp4'
            fname = f'sug_{suggestion.short_tracking_id}_{next_order + 1}.{ext}'
            next_order += 1
            row = SuggestionStoredMedia(
                suggestion=suggestion,
                attachment_key=akey,
                media_type=media_type,
                order=next_order,
            )
            row.file.save(fname, ContentFile(data_bytes), save=True)
            saved += 1
        except Exception as e:
            logger.warning('MAX store attachment failed key=%s: %s', akey, e)

    return saved
