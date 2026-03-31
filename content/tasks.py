"""
Celery задачи для публикации постов.
"""
import logging
from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


def _import_suggestion_media_into_post(post_id: int) -> tuple[int, list[str]]:
    """
    Импортирует медиа из предложки в PostMedia.

    Возвращает (imported_count, warnings).
    """
    from .models import Post, PostMedia
    from django.core.files.base import ContentFile
    import requests

    warnings: list[str] = []
    imported = 0

    try:
        post = Post.objects.select_related('suggestion', 'suggestion__bot').get(pk=post_id)
    except Post.DoesNotExist:
        return 0, warnings

    suggestion = getattr(post, 'suggestion', None)
    if not suggestion:
        return 0, warnings
    bot = getattr(suggestion, 'bot', None)
    if not bot:
        return 0, warnings

    # Avoid duplicating import if media already present
    if PostMedia.objects.filter(post=post).exists():
        return 0, warnings

    try:
        current_max = int(
            PostMedia.objects.filter(post=post)
            .order_by('-order')
            .values_list('order', flat=True)
            .first() or 0
        )
    except Exception:
        current_max = 0

    # Telegram media import
    if bot.platform == bot.PLATFORM_TELEGRAM and (suggestion.media_file_ids or []):
        token = bot.get_token()
        api_base = f'https://api.telegram.org/bot{token}'
        file_base = f'https://api.telegram.org/file/bot{token}'

        media_type = PostMedia.TYPE_DOCUMENT
        if suggestion.content_type == suggestion.CONTENT_PHOTO:
            media_type = PostMedia.TYPE_PHOTO
        elif suggestion.content_type == suggestion.CONTENT_VIDEO:
            media_type = PostMedia.TYPE_VIDEO
        elif suggestion.content_type == suggestion.CONTENT_DOCUMENT:
            media_type = PostMedia.TYPE_DOCUMENT

        for idx, file_id in enumerate(suggestion.media_file_ids or []):
            try:
                r = requests.get(f'{api_base}/getFile', params={'file_id': file_id}, timeout=15)
                data = r.json()
                if not data.get('ok'):
                    raise ValueError(data.get('description') or 'getFile failed')
                file_path = data['result']['file_path']
                dl = requests.get(f'{file_base}/{file_path}', timeout=30)
                dl.raise_for_status()
                filename = (file_path.split('/')[-1] or f'media_{idx}')
                PostMedia.objects.create(
                    post=post,
                    file=ContentFile(dl.content, name=filename),
                    media_type=media_type,
                    order=current_max + idx + 1,
                )
                imported += 1
            except Exception as e:
                warnings.append(f'TG: failed file_id={file_id}: {e}')

    # MAX media import (best-effort)
    if bot.platform == bot.PLATFORM_MAX:
        try:
            raw = suggestion.raw_data or {}
            # В MAX мы сохраняем raw_data как объект message (без update wrapper).
            if isinstance(raw, dict) and isinstance(raw.get('message'), dict):
                msg_obj = raw.get('message')
            elif isinstance(raw, dict) and isinstance(raw.get('last_message'), dict):
                msg_obj = raw.get('last_message')
            elif isinstance(raw, dict) and isinstance(raw.get('messages'), list) and raw.get('messages'):
                last = raw.get('messages')[-1]
                msg_obj = last if isinstance(last, dict) else raw
            else:
                msg_obj = raw

            # Собираем вложения со всех сообщений "склейки" (текст может прийти отдельно от фото)
            messages_chain = []
            if isinstance(raw, dict) and isinstance(raw.get('messages'), list) and raw.get('messages'):
                messages_chain = [m for m in raw.get('messages') if isinstance(m, dict)]
            elif isinstance(msg_obj, dict):
                messages_chain = [msg_obj]

            attachments: list[dict] = []
            # 1) Попытка: получить полные сообщения через API по mid (часто там есть URL вложений)
            try:
                from bots.max_bot.bot import MaxBotAPI
                api = MaxBotAPI(bot.get_token())
                for m in messages_chain:
                    mid = m.get('mid')
                    if not mid:
                        continue
                    full_msg = api.get_message(mid)
                    body_full = (full_msg.get('body') or {}) if isinstance(full_msg, dict) else {}
                    atts = body_full.get('attachments') or []
                    if isinstance(atts, list):
                        attachments.extend([a for a in atts if isinstance(a, dict)])
            except Exception:
                pass

            # 2) Фоллбек: attachments из исходных payload-ов
            if not attachments:
                for m in messages_chain:
                    body = (m.get('body') or {}) if isinstance(m, dict) else {}
                    atts = body.get('attachments') or []
                    if isinstance(atts, list):
                        attachments.extend([a for a in atts if isinstance(a, dict)])

            def _deep_http_urls(obj) -> list[str]:
                """Достаёт все http(s) URL из вложенного dict/list."""
                urls: list[str] = []
                seen: set[str] = set()

                def _walk(x):
                    if x is None:
                        return
                    if isinstance(x, str):
                        if x.startswith('http'):
                            if x not in seen:
                                seen.add(x)
                                urls.append(x)
                        return
                    if isinstance(x, dict):
                        for v in x.values():
                            _walk(v)
                        return
                    if isinstance(x, list):
                        for it in x:
                            _walk(it)
                        return

                _walk(obj)
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
                    # Sometimes payload may contain list of sizes/variants
                    for k in ('sizes', 'variants', 'images', 'files'):
                        arr = payload.get(k)
                        if isinstance(arr, list):
                            for it in arr:
                                if isinstance(it, dict):
                                    u = it.get('url') or it.get('src')
                                    if isinstance(u, str) and u.startswith('http'):
                                        urls.append(u)
                # Generic fallback: scan everything for http urls
                if not urls:
                    urls = _deep_http_urls(att)
                return urls

            order = 0
            seen_urls = set()
            for att in attachments[:10]:
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

                urls = _iter_attachment_urls(att)
                # If no direct URL in payload, try resolve by token via API (video is documented)
                if not urls:
                    try:
                        payload = att.get('payload') or {}
                        token = None
                        if isinstance(payload, dict):
                            token = payload.get('token') or payload.get('id')
                        if token:
                            from bots.max_bot.bot import MaxBotAPI
                            api = MaxBotAPI(bot.get_token())
                            if att_type == 'video':
                                vinfo = api.get_video(token)
                                urls = _deep_http_urls(vinfo)
                            elif att_type in ('image', 'photo'):
                                iinfo = api.get_image(token)
                                urls = _deep_http_urls(iinfo)
                                if not urls:
                                    finfo = api.get_file(token)
                                    urls = _deep_http_urls(finfo)
                            elif att_type == 'file':
                                finfo = api.get_file(token)
                                urls = _deep_http_urls(finfo)
                                if not urls:
                                    iinfo = api.get_image(token)
                                    urls = _deep_http_urls(iinfo)
                    except Exception:
                        urls = urls or []
                if not urls:
                    warnings.append(f'MAX: no URL for attachment (type={att_type})')
                    continue

                url = urls[0]
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                try:
                    # Некоторые CDN-ссылки MAX не требуют Authorization; пробуем без него, а при ошибке — с ним.
                    headers_common = {
                        'User-Agent': 'Mozilla/5.0 (compatible; ProChannelsBot/1.0; +https://prochannels.ru)',
                        'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                        'Referer': 'https://prochannels.ru/',
                    }
                    dl = requests.get(url, timeout=30, stream=True, headers=headers_common)
                    if dl.status_code >= 400:
                        dl = requests.get(
                            url,
                            headers={**headers_common, 'Authorization': bot.get_token()},
                            timeout=30,
                            stream=True
                        )
                    dl.raise_for_status()
                    ct = (dl.headers.get('Content-Type') or '').lower()
                    # If we got HTML/JSON instead of binary — skip
                    if ct.startswith('text/') or 'json' in ct:
                        raise ValueError(f'Unexpected content-type: {ct}')
                    data_bytes = dl.content or b''
                    if not data_bytes or len(data_bytes) < 100:
                        raise ValueError(f'Empty/too small response: {len(data_bytes)} bytes')
                    if data_bytes[:20].lstrip().startswith(b'<!DOCTYPE'):
                        raise ValueError('Unexpected HTML response')
                    # Validate image bytes (and reject "white placeholder" images)
                    if media_type == PostMedia.TYPE_PHOTO:
                        try:
                            from PIL import Image
                            import io
                            im = Image.open(io.BytesIO(data_bytes))
                            im.load()
                            w, h = im.size
                            if w <= 8 or h <= 8:
                                raise ValueError(f'Image too small: {w}x{h}')
                            # Detect near-solid image (common placeholder): sample a few pixels
                            rgb = im.convert('RGB')
                            sample_points = [
                                (0, 0),
                                (w - 1, 0),
                                (0, h - 1),
                                (w - 1, h - 1),
                                (w // 2, h // 2),
                            ]
                            pixels = [rgb.getpixel(p) for p in sample_points]
                            if all(p[0] > 245 and p[1] > 245 and p[2] > 245 for p in pixels):
                                raise ValueError('Looks like placeholder (all sampled pixels are white)')
                        except Exception as e:
                            raise ValueError(f'Invalid/placeholder image: {e}')
                    ext = 'bin'
                    if 'image/' in ct:
                        ext = ct.split('image/', 1)[1].split(';', 1)[0] or 'jpg'
                    elif 'video/' in ct:
                        ext = ct.split('video/', 1)[1].split(';', 1)[0] or 'mp4'
                    filename = f'max_{suggestion.short_tracking_id}_{order}.{ext}'
                    PostMedia.objects.create(
                        post=post,
                        file=ContentFile(data_bytes, name=filename),
                        media_type=media_type,
                        order=current_max + order + 1,
                    )
                    imported += 1
                    order += 1
                except Exception as e:
                    warnings.append(f'MAX: failed url={url}: {e}')
        except Exception as e:
            warnings.append(f'MAX: import failed: {e}')

    return imported, warnings

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def import_media_from_suggestion_task(self, post_id: int):
    """
    Импорт медиа из предложки в фоне, чтобы модерация/редиректы не зависали.
    Best-effort: ошибки логируем, но задачу не валим бесконечными ретраями.
    """
    from .models import Post, PostMedia
    from django.core.files.base import ContentFile
    import requests
    try:
        post = Post.objects.select_related('suggestion', 'suggestion__bot').get(pk=post_id)
    except Post.DoesNotExist:
        return
    suggestion = getattr(post, 'suggestion', None)
    if not suggestion:
        return
    bot = getattr(suggestion, 'bot', None)
    if not bot:
        return

    # Avoid duplicating import if media already present
    if PostMedia.objects.filter(post=post).exists():
        return

    try:
        current_max = int(PostMedia.objects.filter(post=post).order_by('-order').values_list('order', flat=True).first() or 0)
    except Exception:
        current_max = 0

    # Telegram
    if bot.platform == bot.PLATFORM_TELEGRAM and (suggestion.media_file_ids or []):
        token = bot.get_token()
        api_base = f'https://api.telegram.org/bot{token}'
        file_base = f'https://api.telegram.org/file/bot{token}'
        media_type = PostMedia.TYPE_DOCUMENT
        if suggestion.content_type == suggestion.CONTENT_PHOTO:
            media_type = PostMedia.TYPE_PHOTO
        elif suggestion.content_type == suggestion.CONTENT_VIDEO:
            media_type = PostMedia.TYPE_VIDEO
        for idx, file_id in enumerate(suggestion.media_file_ids or []):
            try:
                r = requests.get(f'{api_base}/getFile', params={'file_id': file_id}, timeout=15)
                data = r.json()
                if not data.get('ok'):
                    raise ValueError(data.get('description') or 'getFile failed')
                file_path = data['result']['file_path']
                dl = requests.get(f'{file_base}/{file_path}', timeout=30)
                dl.raise_for_status()
                filename = (file_path.split('/')[-1] or f'media_{idx}')
                PostMedia.objects.create(
                    post=post,
                    file=ContentFile(dl.content, name=filename),
                    media_type=media_type,
                    order=current_max + idx + 1,
                )
            except Exception as e:
                logger.warning('TG import from suggestion failed post=%s file_id=%s: %s', post_id, file_id, e)

    # MAX
    if bot.platform == bot.PLATFORM_MAX:
        try:
            raw = suggestion.raw_data or {}
            # same logic as in content/views.py (trimmed)
            if isinstance(raw, dict) and isinstance(raw.get('message'), dict):
                msg_obj = raw.get('message')
            elif isinstance(raw, dict) and isinstance(raw.get('last_message'), dict):
                msg_obj = raw.get('last_message')
            elif isinstance(raw, dict) and isinstance(raw.get('messages'), list) and raw.get('messages'):
                last = raw.get('messages')[-1]
                msg_obj = last if isinstance(last, dict) else raw
            else:
                msg_obj = raw

            messages_chain = []
            if isinstance(raw, dict) and isinstance(raw.get('messages'), list) and raw.get('messages'):
                messages_chain = [m for m in raw.get('messages') if isinstance(m, dict)]
            elif isinstance(msg_obj, dict):
                messages_chain = [msg_obj]

            attachments: list[dict] = []
            try:
                from bots.max_bot.bot import MaxBotAPI
                api = MaxBotAPI(bot.get_token())
                for m in messages_chain:
                    mid = m.get('mid')
                    if not mid:
                        continue
                    full_msg = api.get_message(mid)
                    body_full = (full_msg.get('body') or {}) if isinstance(full_msg, dict) else {}
                    atts = body_full.get('attachments') or []
                    if isinstance(atts, list):
                        attachments.extend([a for a in atts if isinstance(a, dict)])
            except Exception:
                pass
            if not attachments:
                for m in messages_chain:
                    body = (m.get('body') or {}) if isinstance(m, dict) else {}
                    atts = body.get('attachments') or []
                    if isinstance(atts, list):
                        attachments.extend([a for a in atts if isinstance(a, dict)])

            def _deep_http_urls(obj) -> list[str]:
                urls: list[str] = []
                seen: set[str] = set()

                def _walk(x):
                    if x is None:
                        return
                    if isinstance(x, str):
                        if x.startswith('http') and x not in seen:
                            seen.add(x)
                            urls.append(x)
                        return
                    if isinstance(x, dict):
                        for v in x.values():
                            _walk(v)
                        return
                    if isinstance(x, list):
                        for it in x:
                            _walk(it)
                        return

                _walk(obj)
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

            order = 0
            seen_urls = set()
            api_max = None
            for att in attachments[:10]:
                att_type = (att.get('type') or '').lower()
                media_type = PostMedia.TYPE_DOCUMENT
                if att_type in ('image', 'photo'):
                    media_type = PostMedia.TYPE_PHOTO
                elif att_type == 'video':
                    media_type = PostMedia.TYPE_VIDEO
                urls = _iter_attachment_urls(att)
                if not urls:
                    payload = att.get('payload') or {}
                    token = None
                    if isinstance(payload, dict):
                        token = payload.get('token') or payload.get('id')
                    if token:
                        try:
                            from bots.max_bot.bot import MaxBotAPI
                            if api_max is None:
                                api_max = MaxBotAPI(bot.get_token())
                            if att_type == 'video':
                                vinfo = api_max.get_video(token)
                                urls = _deep_http_urls(vinfo)
                            elif att_type in ('image', 'photo'):
                                iinfo = api_max.get_image(token)
                                urls = _deep_http_urls(iinfo)
                                if not urls:
                                    finfo = api_max.get_file(token)
                                    urls = _deep_http_urls(finfo)
                            elif att_type == 'file':
                                finfo = api_max.get_file(token)
                                urls = _deep_http_urls(finfo)
                                if not urls:
                                    iinfo = api_max.get_image(token)
                                    urls = _deep_http_urls(iinfo)
                            else:
                                for fn_name in ('get_video', 'get_image', 'get_file'):
                                    fn = getattr(api_max, fn_name, None)
                                    if not callable(fn):
                                        continue
                                    info = fn(token) or {}
                                    urls = _deep_http_urls(info)
                                    if urls:
                                        break
                        except Exception:
                            urls = []
                if not urls:
                    continue
                url = urls[0]
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                headers_common = {
                    'User-Agent': 'Mozilla/5.0 (compatible; ProChannelsBot/1.0; +https://prochannels.ru)',
                    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
                    'Referer': 'https://prochannels.ru/',
                }
                try:
                    dl = requests.get(url, timeout=30, stream=True, headers=headers_common)
                    if dl.status_code >= 400:
                        dl = requests.get(url, headers={**headers_common, 'Authorization': bot.get_token()}, timeout=30, stream=True)
                    dl.raise_for_status()
                    data_bytes = dl.content or b''
                    if len(data_bytes) < 100:
                        continue
                    PostMedia.objects.create(
                        post=post,
                        file=ContentFile(data_bytes, name=f'max_{suggestion.short_tracking_id}_{order}.bin'),
                        media_type=media_type,
                        order=current_max + order + 1,
                    )
                    order += 1
                except Exception as e:
                    logger.warning('MAX import from suggestion failed post=%s url=%s: %s', post_id, url, e)
        except Exception as e:
            logger.warning('MAX import from suggestion failed post=%s: %s', post_id, e)

    return post_id


@shared_task(bind=True, max_retries=3, default_retry_delay=60)
def publish_post_task(self, post_id: int, force: bool = False):
    """Публикует пост во все подключённые каналы."""
    from .models import Post, PublishResult
    try:
        post = Post.objects.prefetch_related('channels', 'media_files').get(pk=post_id)
    except Post.DoesNotExist:
        logger.error(f'Пост #{post_id} не найден')
        return

    if post.status == Post.STATUS_PUBLISHED and not force:
        logger.info(f'Пост #{post_id} уже опубликован, пропускаем (force=False)')
        return

    post.status = Post.STATUS_PUBLISHING
    post.save(update_fields=['status'])

    success_count = 0
    fail_count = 0

    for channel in post.channels.all():
        try:
            result = _publish_to_channel(post, channel)
            PublishResult.objects.create(
                post=post,
                channel=channel,
                status=PublishResult.STATUS_OK,
                platform_message_id=str(result.get('message_id', '')) if isinstance(result, dict) else '',
            )
            success_count += 1
            logger.info(f'Пост #{post_id} опубликован в {channel.name}')
        except Exception as exc:
            PublishResult.objects.create(
                post=post,
                channel=channel,
                status=PublishResult.STATUS_FAIL,
                error_message=str(exc),
            )
            fail_count += 1
            logger.error(f'Ошибка публикации поста #{post_id} в {channel.name}: {exc}')

    if success_count > 0:
        post.status = Post.STATUS_PUBLISHED
        post.published_at = timezone.now()
        post.save(update_fields=['status', 'published_at'])
        # Запланировать следующий повтор
        if post.repeat_enabled:
            post.schedule_next_repeat()
        # Авто-регистрация ВК ОРД если пост с меткой
        if post.ord_label:
            _auto_register_ord(post)
    elif fail_count > 0:
        post.status = Post.STATUS_FAILED
        post.save(update_fields=['status'])


def _auto_register_ord(post):
    """Автоматически создаёт ORD-регистрации для поста с меткой."""
    from django.conf import settings
    from ord_marking.models import ORDRegistration
    from django.utils import timezone as tz

    access_token = getattr(settings, 'VK_ORD_ACCESS_TOKEN', '')
    advertiser = None
    try:
        # Если пост создан из рекламного заказа — прикрепим рекламодателя к регистрации.
        advertiser = post.advertising_order.advertiser
    except Exception:
        advertiser = None

    for channel in post.channels.filter(platform='vk'):
        # Не создаём дубли
        if ORDRegistration.objects.filter(post=post, channel=channel).exists():
            continue

        reg = ORDRegistration.objects.create(
            post=post,
            channel=channel,
            advertiser=advertiser,
            label_text=post.ord_label or 'Реклама',
            status=ORDRegistration.STATUS_PENDING,
        )

        if not access_token:
            continue

        try:
            import requests as req
            response = req.post(
                'https://api.vk.com/method/ads.createAdLabel',
                data={'access_token': access_token, 'v': '5.131', 'name': reg.label_text},
                timeout=10,
            )
            data = response.json()
            reg.raw_response = data
            resp = data.get('response')
            if resp:
                reg.ord_id = str(resp.get('id', ''))
                reg.ord_token = resp.get('token', '')
                reg.status = ORDRegistration.STATUS_REGISTERED
                reg.registered_at = tz.now()
            else:
                reg.status = ORDRegistration.STATUS_ERROR
                reg.error_message = str(data.get('error', ''))
            reg.save()
        except Exception as e:
            reg.status = ORDRegistration.STATUS_ERROR
            reg.error_message = str(e)
            reg.save()


def _publish_to_channel(post, channel):
    """Публикует пост в конкретный канал. Возвращает dict с message_id."""
    from channels.models import Channel

    if channel.platform == Channel.PLATFORM_TELEGRAM:
        return _publish_telegram(post, channel)
    elif channel.platform == Channel.PLATFORM_VK:
        return _publish_vk(post, channel)
    elif channel.platform == Channel.PLATFORM_MAX:
        return _publish_max(post, channel)
    elif channel.platform == Channel.PLATFORM_INSTAGRAM:
        return _publish_instagram(post, channel)
    else:
        raise ValueError(f'Неизвестная платформа: {channel.platform}')


def _build_text(post, channel):
    """
    Собирает итоговый текст поста: ОРД метка + текст + подпись канала.

    Подписи хранятся в HTML-разметке (для TG/MAX) или plain text (VK).
    Подпись НЕ добавляется к рекламным постам (ord_label задан).
    """
    from channels.models import Channel as Ch
    text = post.text

    if post.ord_label:
        # Рекламный пост — ОРД метка в начало, подпись не добавляем
        text = f'{post.ord_label}\n\n{text}'
    else:
        # Выбираем платформенную подпись
        if channel.platform == Ch.PLATFORM_TELEGRAM:
            footer = channel.tg_footer
        elif channel.platform == Ch.PLATFORM_MAX:
            footer = channel.max_footer
        elif channel.platform == Ch.PLATFORM_VK:
            footer = channel.vk_footer
        else:
            footer = ''

        if footer:
            text = f'{text}\n\n{footer}'

    return text


def _publish_telegram(post, channel):
    """Публикация в Telegram через Bot API."""
    import requests
    bot_token = channel.get_tg_token()
    chat_id = channel.tg_chat_id

    if not bot_token or not chat_id:
        raise ValueError('Не настроен токен бота или chat_id для Telegram')

    media_files = list(post.media_files.all())
    base_url = f'https://api.telegram.org/bot{bot_token}'

    text = _build_text(post, channel)

    kwargs = {
        'chat_id': chat_id,
        'disable_notification': post.disable_notification,
    }

    # TG Premium Emoji: если есть entities — отправляем их вместо parse_mode
    if post.has_premium_emoji and post.tg_entities:
        kwargs['entities'] = post.tg_entities
    else:
        kwargs['parse_mode'] = 'HTML'

    import json as json_module
    use_entities = post.has_premium_emoji and post.tg_entities

    if media_files:
        if len(media_files) == 1:
            mf = media_files[0]
            with open(mf.file.path, 'rb') as f:
                send_data = {**kwargs, 'caption': text}
                if use_entities:
                    send_data['caption_entities'] = json_module.dumps(post.tg_entities)
                    send_data.pop('entities', None)
                if mf.media_type == 'photo':
                    resp = requests.post(f'{base_url}/sendPhoto', data=send_data, files={'photo': f})
                elif mf.media_type == 'video':
                    resp = requests.post(f'{base_url}/sendVideo', data=send_data, files={'video': f})
                else:
                    resp = requests.post(f'{base_url}/sendDocument', data=send_data, files={'document': f})
        else:
            # Медиагруппа
            media = []
            files = {}
            try:
                for i, mf in enumerate(media_files[:10]):
                    key = f'file{i}'
                    item = {
                        'type': mf.media_type if mf.media_type != 'document' else 'photo',
                        'media': f'attach://{key}',
                        'caption': text if i == 0 else '',
                    }
                    if i == 0 and use_entities:
                        item['caption_entities'] = post.tg_entities
                    elif i == 0:
                        item['parse_mode'] = 'HTML'
                    media.append(item)
                    files[key] = open(mf.file.path, 'rb')

                send_kwargs = {k: v for k, v in kwargs.items() if k not in ('parse_mode', 'entities')}
                resp = requests.post(
                    f'{base_url}/sendMediaGroup',
                    data={**send_kwargs, 'media': json_module.dumps(media)},
                    files=files
                )
            finally:
                for f in files.values():
                    f.close()
    else:
        if use_entities:
            resp = requests.post(f'{base_url}/sendMessage', json={**kwargs, 'text': text})
        else:
            resp = requests.post(f'{base_url}/sendMessage', json={**kwargs, 'text': text})

    data = resp.json()
    if not data.get('ok'):
        raise ValueError(f'Telegram API error: {data.get("description", "Unknown")}')

    result = data.get('result', {})
    msg_id = result.get('message_id') if isinstance(result, dict) else result[0].get('message_id') if result else ''

    # Закрепить сообщение
    if post.pin_message and msg_id:
        requests.post(f'{base_url}/pinChatMessage', json={'chat_id': chat_id, 'message_id': msg_id})

    return {'message_id': msg_id}


def _publish_vk(post, channel):
    """Публикация в VK."""
    import vk_api
    token = channel.get_vk_token()
    group_id = channel.vk_group_id

    if not token or not group_id:
        raise ValueError('Не настроен токен или group_id для VK')

    session = vk_api.VkApi(token=token)
    vk = session.get_api()

    text = _build_text(post, channel)
    if post.ord_token:
        text = f'{text}\n\nerid:{post.ord_token}'

    attachments = []
    for mf in post.media_files.all()[:10]:
        if mf.media_type == 'photo':
            with open(mf.file.path, 'rb') as f:
                upload = vk_api.upload.VkUpload(session)
                photo = upload.photo_wall(f, group_id=abs(int(group_id)))
                if photo:
                    p = photo[0]
                    attachments.append(f'photo{p["owner_id"]}_{p["id"]}')

    result = vk.wall.post(
        owner_id=f'-{abs(int(group_id))}',
        message=text,
        attachments=','.join(attachments),
        from_group=1,
    )
    return {'message_id': result.get('post_id', '')}


def _publish_max(post, channel):
    """Публикация в MAX (https://dev.max.ru/).

    Важно: MAX больше не поддерживает access_token в query-параметрах.
    Нужно использовать:
    - Base URL: https://platform-api.max.ru
    - Authorization: <access_token> (header)
    - Отправка сообщения: POST /messages?chat_id=...
    """
    import requests
    bot_token = channel.get_max_token()
    channel_id = channel.max_channel_id

    if not bot_token or not channel_id:
        raise ValueError('Не настроен токен или channel_id для MAX')

    text = _build_text(post, channel)
    media_files = list(post.media_files.all())

    # Пока отправляем стабильно текстом (как тестовая кнопка).
    # MAX attachments отличаются от Telegram/VK, а локальные URLs файлов
    # не всегда доступны MAX-серверам. Медиа добавим отдельным этапом.
    _ = media_files  # silence "unused" intent-wise

    chat_id_raw = str(channel_id).strip()
    try:
        chat_id = int(chat_id_raw)
    except Exception:
        chat_id = chat_id_raw

    # MAX форматирование: чтобы ссылки точно работали, используем Markdown и конвертируем наш HTML.
    def _max_html_to_markdown(s: str) -> str:
        import re
        import html as html_module

        text0 = html_module.unescape(s or '')
        # Normalize line breaks
        text0 = text0.replace('\r\n', '\n')
        # Convert <br> to newline
        text0 = re.sub(r'<\s*br\s*/?\s*>', '\n', text0, flags=re.IGNORECASE)

        # Links
        def _link(m):
            url = (m.group(1) or '').strip()
            label = (m.group(2) or '').strip() or url
            return f'[{label}]({url})'
        text0 = re.sub(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', _link, text0, flags=re.IGNORECASE | re.DOTALL)

        # Basic tags
        text0 = re.sub(r'<\s*(b|strong)\s*>', '**', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'</\s*(b|strong)\s*>', '**', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'<\s*(i|em)\s*>', '_', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'</\s*(i|em)\s*>', '_', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'<\s*(u)\s*>', '++', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'</\s*(u)\s*>', '++', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'<\s*(s|del|strike)\s*>', '~~', text0, flags=re.IGNORECASE)
        text0 = re.sub(r'</\s*(s|del|strike)\s*>', '~~', text0, flags=re.IGNORECASE)

        # Inline code
        text0 = re.sub(r'<\s*code\s*>(.*?)</\s*code\s*>', lambda m: '`' + re.sub(r'\s+', ' ', (m.group(1) or '').strip()) + '`', text0, flags=re.IGNORECASE | re.DOTALL)

        # Blockquote: prefix each line with >
        def _bq(m):
            inner = (m.group(1) or '').strip()
            inner = re.sub(r'<[^>]+>', '', inner)  # strip remaining tags inside quote
            lines = [ln.strip() for ln in inner.splitlines() if ln.strip()]
            return '\n'.join('> ' + ln for ln in lines) + '\n'
        text0 = re.sub(r'<\s*blockquote\s*>(.*?)</\s*blockquote\s*>', _bq, text0, flags=re.IGNORECASE | re.DOTALL)

        # Strip any remaining tags
        text0 = re.sub(r'<[^>]+>', '', text0)
        return text0.strip()

    max_text = _max_html_to_markdown(text)

    resp = requests.post(
        'https://platform-api.max.ru/messages',
        params={'chat_id': chat_id},
        headers={'Authorization': bot_token},
        json={'text': max_text, 'format': 'markdown'},
        timeout=30,
    )
    # MAX API may return non-JSON or plain string error bodies.
    try:
        data = resp.json()
    except Exception:
        data = resp.text

    if isinstance(data, dict):
        # Ошибка обычно имеет вид {"code":"...","message":"..."} + http>=400
        if resp.status_code >= 400 or data.get('code'):
            raise ValueError(f'MAX API error (chat_id={chat_id_raw}, http={resp.status_code}): {data}')

        # Успех: обычно возвращается Message объект
        msg_id = (
            data.get('mid', '')
            or data.get('message_id', '')
            or data.get('id', '')
            or ''
        )
        if msg_id:
            return {'message_id': msg_id}

        # Иногда Message может быть вложен
        msg = data.get('message')
        if isinstance(msg, dict):
            msg_id = msg.get('mid', '') or msg.get('id', '') or ''
            if msg_id:
                return {'message_id': msg_id}

        return {'message_id': ''}

    raise ValueError(f'MAX API error (chat_id={chat_id_raw}, http={resp.status_code}): {data}')


def _publish_instagram(post, channel):
    """Публикация в Instagram через Graph API.

    Instagram Graph API требует:
    - Instagram Business/Creator аккаунт, привязанный к Facebook Page
    - Long-lived Access Token с правами instagram_basic, instagram_content_publish
    - Медиа должны быть доступны по публичному URL

    Ограничения:
    - Instagram НЕ поддерживает текстовые посты без медиа
    - Фото: JPEG, макс. 8MB
    - Видео (Reels): MP4, макс. 1GB, 3-90 сек
    """
    import requests
    from django.conf import settings

    token = channel.get_ig_token()
    account_id = channel.ig_account_id

    if not token or not account_id:
        raise ValueError('Не настроен токен или Account ID для Instagram')

    media_files = list(post.media_files.all())
    if not media_files:
        raise ValueError('Instagram не поддерживает публикации без медиафайлов. '
                         'Добавьте хотя бы одно фото или видео.')

    graph_url = 'https://graph.facebook.com/v19.0'
    text = _build_text(post, channel)

    if len(media_files) == 1:
        # Одно фото или видео
        mf = media_files[0]
        media_url = f'{settings.SITE_URL}{mf.file.url}'

        if mf.media_type == 'video':
            # Reels
            create_resp = requests.post(
                f'{graph_url}/{account_id}/media',
                data={
                    'access_token': token,
                    'video_url': media_url,
                    'caption': text,
                    'media_type': 'REELS',
                },
                timeout=30,
            )
        else:
            # Фото
            create_resp = requests.post(
                f'{graph_url}/{account_id}/media',
                data={
                    'access_token': token,
                    'image_url': media_url,
                    'caption': text,
                },
                timeout=30,
            )

        creation_data = create_resp.json()
        if 'id' not in creation_data:
            err = creation_data.get('error', {}).get('message', str(creation_data))
            raise ValueError(f'Instagram: ошибка создания контейнера — {err}')

        container_id = creation_data['id']

    else:
        # Карусель (2-10 элементов)
        children_ids = []
        for mf in media_files[:10]:
            media_url = f'{settings.SITE_URL}{mf.file.url}'
            if mf.media_type == 'video':
                child_resp = requests.post(
                    f'{graph_url}/{account_id}/media',
                    data={
                        'access_token': token,
                        'video_url': media_url,
                        'media_type': 'REELS',
                        'is_carousel_item': 'true',
                    },
                    timeout=30,
                )
            else:
                child_resp = requests.post(
                    f'{graph_url}/{account_id}/media',
                    data={
                        'access_token': token,
                        'image_url': media_url,
                        'is_carousel_item': 'true',
                    },
                    timeout=30,
                )
            child_data = child_resp.json()
            if 'id' not in child_data:
                err = child_data.get('error', {}).get('message', str(child_data))
                raise ValueError(f'Instagram: ошибка элемента карусели — {err}')
            children_ids.append(child_data['id'])

        # Создаём контейнер карусели
        create_resp = requests.post(
            f'{graph_url}/{account_id}/media',
            data={
                'access_token': token,
                'media_type': 'CAROUSEL',
                'caption': text,
                'children': ','.join(children_ids),
            },
            timeout=30,
        )
        creation_data = create_resp.json()
        if 'id' not in creation_data:
            err = creation_data.get('error', {}).get('message', str(creation_data))
            raise ValueError(f'Instagram: ошибка создания карусели — {err}')

        container_id = creation_data['id']

    # Публикуем контейнер
    import time
    time.sleep(3)  # Даём Instagram время обработать медиа

    publish_resp = requests.post(
        f'{graph_url}/{account_id}/media_publish',
        data={
            'access_token': token,
            'creation_id': container_id,
        },
        timeout=60,
    )
    publish_data = publish_resp.json()
    if 'id' not in publish_data:
        err = publish_data.get('error', {}).get('message', str(publish_data))
        raise ValueError(f'Instagram: ошибка публикации — {err}')

    return {'message_id': publish_data['id']}


@shared_task
def check_scheduled_posts():
    """Периодическая задача: находит посты готовые к публикации и запускает их."""
    from .models import Post
    now = timezone.now()
    ready_posts = Post.objects.filter(
        status=Post.STATUS_SCHEDULED,
        scheduled_at__lte=now,
    ).values_list('pk', flat=True)

    count = 0
    for post_id in ready_posts:
        publish_post_task.delay(post_id)
        count += 1

    if count:
        logger.info(f'Запущена публикация {count} запланированных постов')
    return count
