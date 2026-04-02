"""
Celery задачи для публикации постов.
"""
import logging
from celery import shared_task
from django.db.models import Max
from django.utils import timezone

logger = logging.getLogger(__name__)


def _tg_postmedia_type_from_path(file_path: str, suggestion_content_type: str) -> str:
    """Тип вложения по расширению пути Telegram file_path (document как «фото»)."""
    from .models import PostMedia
    from bots.models import Suggestion

    fp = (file_path or '').lower()
    for ext in ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.tiff'):
        if fp.endswith(ext):
            return PostMedia.TYPE_PHOTO
    for ext in ('.mp4', '.mov', '.webm', '.mkv', '.m4v'):
        if fp.endswith(ext):
            return PostMedia.TYPE_VIDEO
    if suggestion_content_type == Suggestion.CONTENT_PHOTO:
        return PostMedia.TYPE_PHOTO
    if suggestion_content_type == Suggestion.CONTENT_VIDEO:
        return PostMedia.TYPE_VIDEO
    if suggestion_content_type == Suggestion.CONTENT_DOCUMENT:
        return PostMedia.TYPE_DOCUMENT
    return PostMedia.TYPE_DOCUMENT


def _import_suggestion_media_into_post(post_id: int) -> tuple[int, list[str]]:
    """
    Импортирует медиа из предложки в PostMedia.

    Возвращает (imported_count, warnings).
    """
    from .models import Post, PostMedia, normalize_post_media_orders
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

    max_o = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
    current_max = int(max_o) if max_o is not None else 0

    # Telegram media import
    if bot.platform == bot.PLATFORM_TELEGRAM and (suggestion.media_file_ids or []):
        # Не дублируем: один проход импорта на пост
        if PostMedia.objects.filter(post=post).exists():
            return imported, warnings
        token = bot.get_token()
        api_base = f'https://api.telegram.org/bot{token}'
        file_base = f'https://api.telegram.org/file/bot{token}'

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
                media_type = _tg_postmedia_type_from_path(file_path, suggestion.content_type)
                PostMedia.objects.create(
                    post=post,
                    file=ContentFile(dl.content, name=filename),
                    media_type=media_type,
                    order=current_max + idx + 1,
                )
                imported += 1
            except Exception as e:
                warnings.append(f'TG: failed file_id={file_id}: {e}')

    # MAX: копируем файлы с диска (сохранены при приёме предложки), без повторного скачивания с CDN
    if bot.platform == bot.PLATFORM_MAX:
        import os

        from bots.models import SuggestionStoredMedia

        stored_list = list(SuggestionStoredMedia.objects.filter(suggestion=suggestion).order_by('order', 'pk'))
        if stored_list:
            # Всегда пересобираем из локальных файлов (без MAX CDN), чтобы совпадало с числом вложений на диске.
            for m in PostMedia.objects.filter(post=post):
                try:
                    m.file.delete(save=False)
                except Exception:
                    pass
            PostMedia.objects.filter(post=post).delete()
            for idx, sm in enumerate(stored_list):
                sm.file.open('rb')
                raw_bytes = sm.file.read()
                sm.file.close()
                name = os.path.basename(sm.file.name) or f'media_{idx}'
                mt = sm.media_type
                if mt not in (PostMedia.TYPE_PHOTO, PostMedia.TYPE_VIDEO, PostMedia.TYPE_DOCUMENT):
                    mt = PostMedia.TYPE_DOCUMENT
                PostMedia.objects.create(
                    post=post,
                    file=ContentFile(raw_bytes, name=name),
                    media_type=mt,
                    order=idx + 1,
                )
                imported += 1
            normalize_post_media_orders(post)
            return imported, warnings

    # MAX fallback: старые предложки без локальных файлов — best-effort через API
    if bot.platform == bot.PLATFORM_MAX:
        try:
            from bots.max_media_preview import attachment_entries_from_raw

            entries = attachment_entries_from_raw(suggestion.raw_data)
            expected_attachments = max(
                len(entries),
                len(suggestion.media_file_ids or []),
            )
            existing_n = PostMedia.objects.filter(post=post).count()
            auto_prefix = f'max_{suggestion.short_tracking_id}_'
            if expected_attachments > 0 and existing_n >= expected_attachments:
                return imported, warnings

            if expected_attachments > 0 and 0 < existing_n < expected_attachments:
                for m in list(PostMedia.objects.filter(post=post)):
                    fname = (getattr(m.file, 'name', None) or '')
                    base = fname.split('/')[-1]
                    if base.startswith(auto_prefix):
                        try:
                            m.file.delete(save=False)
                        except Exception:
                            pass
                        m.delete()
                max_o = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
                current_max = int(max_o) if max_o is not None else 0

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

            # Собираем вложения и из ответа get_message, и из сырых сообщений вебхука.
            # Иначе API часто отдаёт неполный список (одно фото), а остальные только в raw messages.
            att_by_token: dict[str, dict] = {}
            att_order: list[str] = []
            _anon_counter = [0]

            def _register_attachment(att: dict) -> None:
                if not isinstance(att, dict):
                    return
                pl = att.get('payload') or {}
                tok = ''
                if isinstance(pl, dict):
                    tok = str(pl.get('token') or pl.get('id') or '')
                if tok:
                    if tok not in att_by_token:
                        att_by_token[tok] = att
                        att_order.append(tok)
                    else:
                        cur = att_by_token[tok]
                        if len(_iter_attachment_urls(att)) > len(_iter_attachment_urls(cur)):
                            att_by_token[tok] = att
                else:
                    k = f'__anon_{_anon_counter[0]}'
                    _anon_counter[0] += 1
                    att_by_token[k] = att
                    att_order.append(k)

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
                        for a in atts:
                            _register_attachment(a)
            except Exception:
                pass

            for m in messages_chain:
                body = (m.get('body') or {}) if isinstance(m, dict) else {}
                atts = body.get('attachments') or []
                if isinstance(atts, list):
                    for a in atts:
                        _register_attachment(a)

            attachments = [att_by_token[k] for k in att_order if k in att_by_token]

            order = 0
            seen_urls = set()
            seen_tokens: set[str] = set()
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

                payload = att.get('payload') or {}
                token_key = ''
                if isinstance(payload, dict):
                    token_key = str(payload.get('token') or payload.get('id') or '')

                urls = _iter_attachment_urls(att)
                # If no direct URL in payload, try resolve by token via API (video is documented)
                if not urls:
                    try:
                        token = token_key or None
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
                            else:
                                # Тип вложения не указан или неизвестен — пробуем как у превью MAX
                                iinfo = api.get_image(token)
                                urls = _deep_http_urls(iinfo)
                                if not urls:
                                    finfo = api.get_file(token)
                                    urls = _deep_http_urls(finfo)
                                if not urls:
                                    vinfo = api.get_video(token)
                                    urls = _deep_http_urls(vinfo)
                    except Exception:
                        urls = urls or []
                if not urls:
                    warnings.append(f'MAX: no URL for attachment (type={att_type})')
                    continue

                url = urls[0]
                # Разные вложения могут дать один и тот же URL в ответе API — не схлопываем по URL, если токены разные.
                if token_key:
                    if token_key in seen_tokens:
                        continue
                    seen_tokens.add(token_key)
                else:
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

    if imported:
        normalize_post_media_orders(post)

    return imported, warnings

@shared_task(bind=True, max_retries=2, default_retry_delay=60)
def import_media_from_suggestion_task(self, post_id: int):
    """
    Импорт медиа из предложки в фоне, чтобы модерация/редиректы не зависали.
    Best-effort: ошибки логируем, но задачу не валим бесконечными ретраями.
    """
    imported, warnings = _import_suggestion_media_into_post(int(post_id))
    for w in warnings[:20]:
        logger.warning('Suggestion media import warning post=%s: %s', post_id, w)
    if warnings and len(warnings) > 20:
        logger.warning('Suggestion media import: +%s more warnings (post=%s)', len(warnings) - 20, post_id)
    return imported


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
    """Автоматически создаёт ORD-регистрации для поста с меткой (API ОРД VK, erid)."""
    from core.models import get_global_api_keys
    from ord_marking.models import ORDRegistration
    from ord_marking.services import register_creative_for_registration

    keys = get_global_api_keys()
    if not (keys.get_vk_ord_access_token() or '').strip():
        return

    advertiser = None
    try:
        advertiser = post.advertising_order.advertiser
    except Exception:
        advertiser = None

    use_sandbox = bool(getattr(keys, 'vk_ord_use_sandbox', False))

    for channel in post.channels.filter(platform='vk'):
        if ORDRegistration.objects.filter(post=post, channel=channel).exists():
            continue

        reg = ORDRegistration.objects.create(
            post=post,
            channel=channel,
            advertiser=advertiser,
            label_text=post.ord_label or 'Реклама',
            status=ORDRegistration.STATUS_PENDING,
        )
        register_creative_for_registration(reg, use_sandbox=use_sandbox)


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
    # Для TG используем HTML-версию, для остальных — plain
    if channel.platform == Ch.PLATFORM_TELEGRAM and (post.text_html or '').strip():
        text = post.text_html
    else:
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


def _tg_utf16_len(s: str) -> int:
    """Длина строки в UTF-16 code units (как offset/length в Telegram MessageEntity)."""
    if not s:
        return 0
    return len(s.encode('utf-16-le')) // 2


def _tg_normalize_entities(entities) -> list:
    """Приводим типы полей к ожиданиям Bot API (int / str)."""
    out = []
    for e in entities or []:
        if not isinstance(e, dict):
            continue
        ne = {}
        for k, v in e.items():
            if k in ('offset', 'length'):
                try:
                    ne[k] = int(v)
                except (TypeError, ValueError):
                    ne[k] = 0
            elif k == 'custom_emoji_id' and v is not None:
                ne[k] = str(v)
            else:
                ne[k] = v
        out.append(ne)
    return out


def _tg_shift_entities(entities: list, delta: int) -> list:
    if not delta or not entities:
        return list(entities)
    out = []
    for e in entities:
        if not isinstance(e, dict):
            continue
        ne = dict(e)
        ne['offset'] = int(ne.get('offset', 0)) + int(delta)
        out.append(ne)
    return out


def _tg_footer_plain_from_html(tg_footer_html: str) -> str:
    """Подпись канала для режима entities — только plain, без HTML-тегов в чате."""
    from django.utils.html import strip_tags

    t = (tg_footer_html or '').strip()
    if not t:
        return ''
    plain = strip_tags(t).replace('\xa0', ' ')
    return plain.strip()


def _publish_telegram(post, channel):
    """Публикация в Telegram через Bot API."""
    import json as json_module
    import requests

    bot_token = channel.get_tg_token()
    chat_id = channel.tg_chat_id

    if not bot_token or not chat_id:
        raise ValueError('Не настроен токен бота или chat_id для Telegram')

    media_files = list(post.media_files.order_by('order', 'pk'))
    base_url = f'https://api.telegram.org/bot{bot_token}'

    raw_entities = getattr(post, 'tg_entities', None)
    tg_entities_list = raw_entities if isinstance(raw_entities, list) else []
    use_entities = bool(getattr(post, 'has_premium_emoji', False) and tg_entities_list)

    if use_entities:
        # ВАЖНО: смещения entities привязаны к post.text из импорта, не к text_html.
        # Если слать text_html + entities, API игнорирует custom_emoji / показывает «сырой» HTML.
        text = post.text if post.text is not None else ''
        entities = _tg_normalize_entities(tg_entities_list)
        if post.ord_label:
            prefix = f"{(post.ord_label or '').strip()}\n\n"
            text = prefix + text
            entities = _tg_shift_entities(entities, _tg_utf16_len(prefix))
        elif (channel.tg_footer or '').strip():
            footer = _tg_footer_plain_from_html(channel.tg_footer)
            if footer:
                text = f'{text}\n\n{footer}'
        kwargs = {
            'chat_id': chat_id,
            'disable_notification': post.disable_notification,
            'disable_web_page_preview': True,
            'entities': entities,
        }
    else:
        text = _build_text(post, channel)
        kwargs = {
            'chat_id': chat_id,
            'disable_notification': post.disable_notification,
            'disable_web_page_preview': True,
            'parse_mode': 'HTML',
        }

    if media_files:
        if len(media_files) == 1:
            mf = media_files[0]
            with open(mf.file.path, 'rb') as f:
                send_data = {**kwargs, 'caption': text}
                if use_entities:
                    send_data['caption_entities'] = json_module.dumps(kwargs['entities'])
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
                        item['caption_entities'] = kwargs['entities']
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
                for fp in files.values():
                    fp.close()
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
    for mf in post.media_files.order_by('order', 'pk')[:10]:
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


def _max_autolink_urls_in_escaped_text(s: str) -> str:
    """
    После html.escape для тела поста: голые http(s)-ссылки → <a href>, как ожидает MAX (format=html).
    """
    import html as html_module
    import re

    def _trim_trailing_punct(url: str):
        trail = ''
        u = url
        while u and u[-1] in '.,;:!?*)]':
            trail = u[-1] + trail
            u = u[:-1]
        return u, trail

    def _repl(m):
        raw = m.group(0)
        u, trail = _trim_trailing_punct(raw)
        if not u:
            return raw
        href_esc = html_module.escape(u, quote=True)
        return f'<a href="{href_esc}">{u}</a>{trail}'

    return re.sub(r'https?://[^\s<]+', _repl, s or '')


def _max_strip_inner_html(fragment: str) -> str:
    import re
    return re.sub(r'<[^>]+>', '', fragment or '').strip()


def _max_html_footer_to_markdown(html_fragment: str) -> str:
    """Подпись канала хранится в HTML — переводим в Markdown под формат MAX."""
    import html as html_module
    import re

    if not (html_fragment or '').strip():
        return ''
    text0 = html_module.unescape(html_fragment or '')
    text0 = text0.replace('\r\n', '\n')
    text0 = re.sub(r'<\s*br\s*/?\s*>', '\n', text0, flags=re.IGNORECASE)

    def _link_repl(m):
        url = (m.group(2) or '').strip()
        inner = m.group(3) or ''
        label = _max_strip_inner_html(inner) or url
        label = label.replace('\n', ' ').strip()
        for ch in ('[', ']', '`'):
            label = label.replace(ch, '')
        if not url:
            return m.group(0)
        return f'[{label}]({url})'

    text0 = re.sub(
        r'<a\s[^>]*\bhref\s*=\s*(["\'])([^"\']*)\1[^>]*>(.*?)</a\s*>',
        _link_repl,
        text0,
        flags=re.IGNORECASE | re.DOTALL,
    )

    text0 = re.sub(r'<\s*(b|strong)\s*>', '**', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'</\s*(b|strong)\s*>', '**', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'<\s*(i|em)\s*>', '_', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'</\s*(i|em)\s*>', '_', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'<\s*u\s*>', '++', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'</\s*u\s*>', '++', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'<\s*(s|del|strike)\s*>', '~~', text0, flags=re.IGNORECASE)
    text0 = re.sub(r'</\s*(s|del|strike)\s*>', '~~', text0, flags=re.IGNORECASE)
    text0 = re.sub(
        r'<\s*code\s*>(.*?)</\s*code\s*>',
        lambda m: '`' + re.sub(r'\s+', ' ', (m.group(1) or '').strip()) + '`',
        text0,
        flags=re.IGNORECASE | re.DOTALL,
    )

    def _bq(m):
        inner = (m.group(1) or '').strip()
        inner = re.sub(r'<[^>]+>', '', inner)
        lines = [ln.strip() for ln in inner.splitlines() if ln.strip()]
        return '\n'.join('> ' + ln for ln in lines) + '\n'

    text0 = re.sub(r'<\s*blockquote\s*>(.*?)</\s*blockquote\s*>', _bq, text0, flags=re.IGNORECASE | re.DOTALL)
    text0 = re.sub(r'<[^>]+>', '', text0)
    return text0.strip()


def _max_plain_urls_to_markdown_links(text: str) -> str:
    """Голые http(s) URL → [url](url); скобки в URL пропускаем (ломают markdown)."""
    import re

    def _repl(m):
        raw = m.group(0)
        if '(' in raw or ')' in raw:
            return raw
        trail = ''
        u = raw
        while u and u[-1] in '.,;:!?*)]':
            trail = u[-1] + trail
            u = u[:-1]
        if not u.startswith(('http://', 'https://')):
            return raw
        return f'[{u}]({u}){trail}'

    return re.sub(r'https?://[^\s\[\]<>]+', _repl, text or '')


def _max_footer_link_inline_keyboard(footer_html: str):
    """
    По документации MAX, кнопка type=link даёт гарантированно кликабельную ссылку
    (даже если разметка текста в канале отображается плоско).
    """
    import html as html_module
    import re

    if not (footer_html or '').strip():
        return None
    links = re.findall(
        r'<a\s[^>]*\bhref\s*=\s*(["\'])([^"\']+)\1[^>]*>(.*?)</a\s*>',
        footer_html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not links:
        return None

    buttons_row = []
    for _, url_raw, inner in links[:7]:
        url = (url_raw or '').strip()
        if not url.startswith(('http://', 'https://')):
            continue
        label = _max_strip_inner_html(inner or '')
        label = html_module.unescape(label).strip() or 'Ссылка'
        label = label[:200]
        url = url[:2048]
        buttons_row.append({'type': 'link', 'text': label, 'url': url})

    if not buttons_row:
        return None

    return {
        'type': 'inline_keyboard',
        'payload': {'buttons': [buttons_row]},
    }


def _publish_max(post, channel):
    """Публикация в MAX (https://dev.max.ru/).

    Важно: MAX больше не поддерживает access_token в query-параметрах.
    Нужно использовать:
    - Base URL: https://platform-api.max.ru
    - Authorization: <access_token> (header)
    - Отправка сообщения: POST /messages?chat_id=...
    """
    import requests
    import time
    bot_token = channel.get_max_token()
    channel_id = channel.max_channel_id

    if not bot_token or not channel_id:
        raise ValueError('Не настроен токен или channel_id для MAX')

    media_files = list(post.media_files.order_by('order', 'pk'))

    def _upload_attachment(mf):
        """
        Загружает файл в MAX и возвращает attachment dict для /messages.
        Документация: https://dev.max.ru/docs-api/methods/POST/uploads
        """
        mt = (getattr(mf, 'media_type', '') or '').strip().lower()
        if mt == 'photo':
            upload_type = 'image'
            attachment_type = 'image'
        elif mt == 'video':
            upload_type = 'video'
            attachment_type = 'video'
        else:
            upload_type = 'file'
            attachment_type = 'file'

        # 1) URL для загрузки
        u = requests.post(
            'https://platform-api.max.ru/uploads',
            params={'type': upload_type},
            headers={'Authorization': bot_token},
            timeout=30,
        )
        try:
            udata = u.json()
        except Exception:
            udata = {}
        upload_url = udata.get('url') if isinstance(udata, dict) else None
        upload_token = udata.get('token') if isinstance(udata, dict) else None
        if not upload_url:
            raise ValueError(f'MAX upload: no url (type={upload_type}, http={u.status_code}): {udata or u.text}')

        # 2) Multipart upload файла
        try:
            file_path = mf.file.path
        except Exception:
            file_path = ''
        if not file_path:
            raise ValueError('MAX upload: file path is empty')

        filename = ''
        try:
            filename = (getattr(mf.file, 'name', '') or '').split('/')[-1]
        except Exception:
            filename = ''
        if not filename:
            filename = 'upload.bin'

        def _guess_mime(name: str) -> str:
            try:
                import mimetypes
                mt_guess, _ = mimetypes.guess_type(name)
                return mt_guess or 'application/octet-stream'
            except Exception:
                return 'application/octet-stream'

        mime = _guess_mime(filename)

        def _try_upload(*, use_auth: bool):
            with open(file_path, 'rb') as f:
                files = {'data': (filename, f, mime)}
                headers = {'Authorization': bot_token} if use_auth else {}
                return requests.post(
                    upload_url,
                    headers=headers,
                    files=files,
                    timeout=180,
                )

        # MAX upload URL в разных случаях может требовать или не требовать Authorization.
        # Делаем best-effort: сначала без Authorization, затем с ним.
        r = _try_upload(use_auth=False)
        if r.status_code >= 400:
            r = _try_upload(use_auth=True)

        # MAX upload endpoints могут возвращать не-JSON (HTML) при ошибке — сохраняем как текст.
        rtext = ''
        try:
            rtext = r.text or ''
        except Exception:
            rtext = ''
        try:
            rdata = r.json()
        except Exception:
            rdata = None
            # Иногда сервер возвращает JSON как text/plain.
            try:
                import json as json_module
                rdata = json_module.loads(rtext) if rtext else None
            except Exception:
                rdata = None
        if r.status_code >= 400:
            body = rdata if rdata is not None else (rtext[:500] if rtext else '')
            raise ValueError(f'MAX upload failed (type={upload_type}, http={r.status_code}): {body}')

        # video/audio: payload = {token: "..."}
        if upload_type in ('video', 'audio'):
            tok = rdata.get('token') if isinstance(rdata, dict) else None
            if not tok:
                # Попробуем достать token из текста (на случай странного формата ответа)
                import re
                m = re.search(r'"token"\s*:\s*"([^"]+)"', rtext or '')
                if m:
                    tok = m.group(1)
                else:
                    # Иногда upload endpoint отвечает XML вида <retval>1</retval> без token.
                    # В таком случае используем token из первого шага (/uploads), если он был возвращён.
                    if upload_token:
                        tok = str(upload_token).strip()
                    else:
                        raise ValueError(
                            f'MAX upload: no token (type={upload_type}): '
                            f'upload_step={udata or None}, upload_resp={(rdata or (rtext[:500] if rtext else None))}'
                        )
            payload = {'token': tok}
        else:
            # image/file: payload = full response JSON
            if not isinstance(rdata, dict) or not rdata:
                raise ValueError(f'MAX upload: empty payload (type={upload_type}): {rdata}')
            payload = rdata

        return {'type': attachment_type, 'payload': payload}

    chat_id_raw = str(channel_id).strip()
    try:
        chat_id = int(chat_id_raw)
    except Exception:
        chat_id = chat_id_raw

    from channels.models import Channel as Ch

    # MAX всегда получает plain text (HTML-теги из редактора вырезаны на сохранении).
    if post.ord_label:
        main_raw = f'{post.ord_label}\n\n{post.text}'
    else:
        main_raw = post.text or ''

    footer_text = ''
    if (not post.ord_label) and channel.platform == Ch.PLATFORM_MAX:
        # По запросу: MAX — только обычный текст без разметки и без кнопок.
        footer_text = (channel.max_footer or '').strip()

    max_text = '\n\n'.join(p for p in ((main_raw or '').replace('\r\n', '\n'), footer_text) if p)
    if len(max_text) > 4000:
        max_text = max_text[:3997] + '…'

    payload = {'text': max_text}

    # Медиа: загружаем в MAX и добавляем attachments (до 10)
    attachments = []
    for mf in media_files[:10]:
        attachments.append(_upload_attachment(mf))
    if attachments:
        payload['attachments'] = attachments

    # Отправка. В MAX вложения могут быть "не готовы" сразу после upload -> retry.
    resp = None
    data = None
    for attempt in range(6):
        resp = requests.post(
            'https://platform-api.max.ru/messages',
            params={'chat_id': chat_id},
            headers={'Authorization': bot_token, 'Content-Type': 'application/json; charset=utf-8'},
            json=payload,
            timeout=30,
        )
        try:
            data = resp.json()
        except Exception:
            data = resp.text

        if isinstance(data, dict) and data.get('code') == 'attachment.not.ready':
            time.sleep(min(10, 1 + attempt * 2))
            continue
        break

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

    media_files = list(post.media_files.order_by('order', 'pk'))
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
