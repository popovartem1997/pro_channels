"""
Создание, редактирование и публикация постов.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import json
from django.utils import timezone
from .models import Post, PostMedia, PublishResult
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
import secrets
from django.core.files.base import ContentFile


@login_required
def post_list(request):
    from channels.models import Channel
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_publish=True,
        ).values_list('channels__pk', flat=True)
        posts = Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct().order_by('-created_at')
    else:
        posts = Post.objects.filter(author=request.user).order_by('-created_at')
    status_filter = request.GET.get('status', '')
    if status_filter:
        posts = posts.filter(status=status_filter)
    return render(request, 'content/list.html', {
        'posts': posts,
        'status_filter': status_filter,
        'statuses': Post.STATUS_CHOICES,
    })


@login_required
def post_create(request):
    from channels.models import Channel
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        user_channels = Channel.objects.filter(
            pk__in=TeamMember.objects.filter(
                member=request.user, is_active=True, can_publish=True
            ).values_list('channels__pk', flat=True),
            is_active=True,
        ).distinct()
    else:
        user_channels = Channel.objects.filter(owner=request.user, is_active=True)

    # Pre-selection: ?channel=pk передаётся из страницы канала
    raw = request.GET.getlist('channel') if request.method == 'GET' else request.POST.getlist('channels')
    preselected = []
    for p in raw:
        try:
            preselected.append(int(p))
        except (ValueError, TypeError):
            pass

    if request.method == 'POST':
        text = request.POST.get('text', '').strip()
        channel_ids = request.POST.getlist('channels')
        scheduled_at_str = request.POST.get('scheduled_at', '').strip()
        repeat_enabled = request.POST.get('repeat_enabled') == 'on'
        repeat_type = request.POST.get('repeat_type', Post.REPEAT_NONE)
        repeat_interval_days = int(request.POST.get('repeat_interval_days', 3) or 3)
        pin_message = request.POST.get('pin_message') == 'on'
        disable_notification = request.POST.get('disable_notification') == 'on'
        ord_label = request.POST.get('ord_label', '').strip()

        if not text:
            messages.error(request, 'Введите текст поста.')
            return render(request, 'content/create.html', {'user_channels': user_channels, 'preselected': channel_ids})

        if not channel_ids:
            messages.error(request, 'Выберите хотя бы один канал.')
            return render(request, 'content/create.html', {'user_channels': user_channels, 'preselected': channel_ids})

        # TG Premium/Custom emoji: entities приходят только из "импорта из Telegram"
        has_premium_emoji = False
        tg_entities = []
        tg_entities_raw = request.POST.get('tg_entities', '').strip()
        if tg_entities_raw:
            try:
                tg_entities = json.loads(tg_entities_raw)
                has_premium_emoji = True if tg_entities else False
            except json.JSONDecodeError:
                messages.error(request, 'Импорт Telegram: неверный формат entities.')
                return render(request, 'content/create.html', {
                    'user_channels': user_channels, 'preselected': preselected,
                })

        post = Post(
            author=request.user,
            text=text,
            repeat_enabled=repeat_enabled,
            repeat_type=repeat_type,
            repeat_interval_days=repeat_interval_days,
            pin_message=pin_message,
            disable_notification=disable_notification,
            ord_label=ord_label,
            has_premium_emoji=has_premium_emoji,
            tg_entities=tg_entities,
        )

        if scheduled_at_str:
            from django.utils.dateparse import parse_datetime
            scheduled_at = parse_datetime(scheduled_at_str)
            if scheduled_at:
                post.scheduled_at = scheduled_at
                post.status = Post.STATUS_SCHEDULED
            else:
                messages.error(request, 'Неверный формат даты.')
                return render(request, 'content/create.html', {
        'user_channels': user_channels,
        'preselected': preselected,
    })
        else:
            post.status = Post.STATUS_DRAFT

        post.save()
        # restrict channel_ids to allowed channels
        allowed_ids = set(user_channels.values_list('pk', flat=True))
        selected_ids = [int(x) for x in channel_ids if str(x).isdigit() and int(x) in allowed_ids]
        if not selected_ids:
            messages.error(request, 'Нет доступа к выбранным каналам.')
            post.delete()
            return render(request, 'content/create.html', {'user_channels': user_channels, 'preselected': channel_ids})
        post.channels.set(selected_ids)

        # Загрузка медиафайлов (порядок = порядок загрузки)
        start_order = 0
        try:
            start_order = int(PostMedia.objects.filter(post=post).order_by('-order').values_list('order', flat=True).first() or 0)
        except Exception:
            start_order = 0
        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = PostMedia.TYPE_PHOTO
            if f.content_type.startswith('video'):
                media_type = PostMedia.TYPE_VIDEO
            elif not f.content_type.startswith('image'):
                media_type = PostMedia.TYPE_DOCUMENT
            PostMedia.objects.create(post=post, file=f, media_type=media_type, order=start_order + idx + 1)

        if request.POST.get('publish_now'):
            from .tasks import publish_post_task
            publish_post_task.delay(post.pk)
            messages.success(request, 'Пост отправлен на публикацию.')
        elif post.status == Post.STATUS_SCHEDULED:
            messages.success(request, f'Пост запланирован на {post.scheduled_at:%d.%m.%Y %H:%M}.')
        else:
            messages.success(request, 'Пост сохранён как черновик.')

        return redirect('content:list')

    return render(request, 'content/create.html', {
        'user_channels': user_channels,
        'preselected': preselected,
    })


@login_required
def post_create_from_suggestion(request, tracking_id):
    """
    Создать черновик поста из предложки и сразу открыть редактирование.
    Текст и медиа подставляются автоматически, медиа можно удалить на странице редактирования.
    """
    from bots.models import Suggestion
    import requests

    suggestion = get_object_or_404(
        Suggestion.objects.select_related('bot', 'bot__owner', 'bot__channel'),
        tracking_id=tracking_id,
    )
    bot = suggestion.bot

    # Access control
    if request.user.is_staff or request.user.is_superuser or bot.owner_id == request.user.id:
        allowed = True
    else:
        allowed = False
        if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and bot.channel_id:
            try:
                from managers.models import TeamMember
                allowed = TeamMember.objects.filter(
                    member=request.user,
                    is_active=True,
                    can_publish=True,
                    channels__pk=bot.channel_id,
                ).exists()
            except Exception:
                allowed = False
    if not allowed:
        return HttpResponse(status=403)

    if not bot.channel_id:
        messages.error(request, 'У бота предложки не выбран канал. Привяжите бота к каналу и повторите.')
        return redirect('bots:detail', bot_id=bot.id)

    # Reuse if already created
    existing = Post.objects.filter(suggestion=suggestion).order_by('-created_at').first()
    if existing:
        return redirect('content:edit', pk=existing.pk)

    text = (suggestion.text or '').strip() or '📩 Пост из предложки (проверьте медиа).'

    post = Post.objects.create(
        author=bot.owner,
        text=text,
        status=Post.STATUS_DRAFT,
        suggestion=suggestion,
    )
    post.channels.set([bot.channel_id])

    # Telegram media import
    if bot.platform == bot.PLATFORM_TELEGRAM and (suggestion.media_file_ids or []):
        token = bot.get_token()
        api_base = f'https://api.telegram.org/bot{token}'
        file_base = f'https://api.telegram.org/file/bot{token}'

        media_type = PostMedia.TYPE_DOCUMENT
        if suggestion.content_type == Suggestion.CONTENT_PHOTO:
            media_type = PostMedia.TYPE_PHOTO
        elif suggestion.content_type == Suggestion.CONTENT_VIDEO:
            media_type = PostMedia.TYPE_VIDEO
        elif suggestion.content_type == Suggestion.CONTENT_DOCUMENT:
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
                    order=idx,
                )
            except Exception as e:
                messages.warning(request, f'Не удалось загрузить медиа из Telegram (file_id={file_id}): {e}')

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
                if not urls:
                    continue
                url = urls[0]
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                try:
                    # Некоторые CDN-ссылки MAX не требуют Authorization; пробуем без него, а при ошибке — с ним.
                    dl = requests.get(url, timeout=30)
                    if dl.status_code >= 400:
                        dl = requests.get(url, headers={'Authorization': bot.get_token()}, timeout=30)
                    dl.raise_for_status()
                    ct = (dl.headers.get('Content-Type') or '').lower()
                    # If we got HTML/JSON instead of binary — skip
                    if ct.startswith('text/') or 'json' in ct:
                        raise ValueError(f'Unexpected content-type: {ct}')
                    if dl.content and dl.content[:20].lstrip().startswith(b'<!DOCTYPE'):
                        raise ValueError('Unexpected HTML response')
                    ext = 'bin'
                    if 'image/' in ct:
                        ext = ct.split('image/', 1)[1].split(';', 1)[0] or 'jpg'
                    elif 'video/' in ct:
                        ext = ct.split('video/', 1)[1].split(';', 1)[0] or 'mp4'
                    filename = f'max_{suggestion.short_tracking_id}_{order}.{ext}'
                    PostMedia.objects.create(
                        post=post,
                        file=ContentFile(dl.content, name=filename),
                        media_type=media_type,
                        order=order,
                    )
                    order += 1
                except Exception as e:
                    messages.warning(request, f'Не удалось загрузить медиа из MAX: {e}')
        except Exception:
            pass

    messages.success(request, f'Создан черновик поста из предложки #{suggestion.short_tracking_id}.')
    return redirect('content:edit', pk=post.pk)


@login_required
def post_detail(request, pk):
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_publish=True,
        ).values_list('channels__pk', flat=True)
        post = get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
    results = PublishResult.objects.filter(post=post).select_related('channel')
    return render(request, 'content/detail.html', {
        'post': post,
        'results': results,
    })


@login_required
def post_edit(request, pk):
    from channels.models import Channel
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user, is_active=True, can_publish=True
        ).values_list('channels__pk', flat=True)
        post = get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
        user_channels = Channel.objects.filter(
            pk__in=TeamMember.objects.filter(
                member=request.user, is_active=True, can_publish=True
            ).values_list('channels__pk', flat=True),
            is_active=True,
        ).distinct()
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
        user_channels = Channel.objects.filter(owner=request.user, is_active=True)

    if request.method == 'POST':
        # Delete selected existing media
        delete_media_ids = request.POST.getlist('delete_media')
        if delete_media_ids:
            to_delete = PostMedia.objects.filter(
                post=post,
                pk__in=[int(x) for x in delete_media_ids if str(x).isdigit()],
            )
            for m in to_delete:
                try:
                    m.file.delete(save=False)
                except Exception:
                    pass
            to_delete.delete()

        # Update media order (best-effort)
        for m in PostMedia.objects.filter(post=post):
            key = f'media_order_{m.pk}'
            if key in request.POST:
                raw = (request.POST.get(key) or '').strip()
                if raw.isdigit():
                    m.order = int(raw)
                    m.save(update_fields=['order'])

        # Upload new media
        try:
            current_max = int(PostMedia.objects.filter(post=post).order_by('-order').values_list('order', flat=True).first() or 0)
        except Exception:
            current_max = 0
        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = PostMedia.TYPE_PHOTO
            if f.content_type.startswith('video'):
                media_type = PostMedia.TYPE_VIDEO
            elif not f.content_type.startswith('image'):
                media_type = PostMedia.TYPE_DOCUMENT
            PostMedia.objects.create(post=post, file=f, media_type=media_type, order=current_max + idx + 1)

        channel_ids = request.POST.getlist('channels')
        post.text = request.POST.get('text', post.text).strip()
        post.pin_message = request.POST.get('pin_message') == 'on'
        post.disable_notification = request.POST.get('disable_notification') == 'on'
        post.ord_label = request.POST.get('ord_label', '').strip()
        post.repeat_enabled = request.POST.get('repeat_enabled') == 'on'
        post.repeat_type = request.POST.get('repeat_type', Post.REPEAT_NONE)
        post.repeat_interval_days = int(request.POST.get('repeat_interval_days', 3) or 3)

        # TG Premium/Custom emoji: entities приходят только из "импорта из Telegram"
        post.has_premium_emoji = False
        post.tg_entities = []
        tg_entities_raw = request.POST.get('tg_entities', '').strip()
        if tg_entities_raw:
            try:
                post.tg_entities = json.loads(tg_entities_raw)
                post.has_premium_emoji = True if post.tg_entities else False
            except json.JSONDecodeError:
                post.tg_entities = []
                post.has_premium_emoji = False

        scheduled_at_str = request.POST.get('scheduled_at', '').strip()
        if scheduled_at_str:
            from django.utils.dateparse import parse_datetime
            scheduled_at = parse_datetime(scheduled_at_str)
            if scheduled_at:
                post.scheduled_at = scheduled_at
                post.status = Post.STATUS_SCHEDULED
        if not post.text:
            messages.error(request, 'Введите текст поста.')
            return render(request, 'content/edit.html', {
                'post': post,
                'user_channels': user_channels,
                'selected_channels': [int(x) for x in channel_ids if str(x).isdigit()],
            })
        if not channel_ids:
            messages.error(request, 'Выберите хотя бы один канал.')
            return render(request, 'content/edit.html', {
                'post': post,
                'user_channels': user_channels,
                'selected_channels': [],
            })

        post.save()
        allowed_ids = set(user_channels.values_list('pk', flat=True))
        selected_ids = [int(x) for x in channel_ids if str(x).isdigit() and int(x) in allowed_ids]
        if not selected_ids:
            messages.error(request, 'Нет доступа к выбранным каналам.')
            return render(request, 'content/edit.html', {
                'post': post,
                'user_channels': user_channels,
                'selected_channels': [],
            })
        post.channels.set(selected_ids)
        messages.success(request, 'Пост обновлён.')
        return redirect('content:detail', pk=pk)

    return render(request, 'content/edit.html', {
        'post': post,
        'user_channels': user_channels,
        'selected_channels': list(post.channels.values_list('pk', flat=True)),
    })


@login_required
def tg_import_link(request):
    """Страница с кодом привязки для служебного Telegram-бота импорта."""
    from .models_imports import TelegramImportLink
    link, _ = TelegramImportLink.objects.get_or_create(
        user=request.user,
        defaults={'code': secrets.token_hex(8)},
    )
    if request.method == 'POST':
        link.code = secrets.token_hex(8)
        link.telegram_user_id = None
        link.linked_at = None
        link.save(update_fields=['code', 'telegram_user_id', 'linked_at'])
        messages.success(request, 'Код обновлён.')
        return redirect('content:tg_import_link')
    return render(request, 'content/tg_import_link.html', {'link': link})


@csrf_exempt
def tg_import_webhook(request):
    """Webhook Telegram для служебного импорта entities/custom_emoji."""
    from core.models import get_global_api_keys
    token = (get_global_api_keys().get_tg_import_bot_token() or '').strip()
    # Telegram будет ретраить вебхук при не-200.
    if not token:
        return HttpResponse('TG_IMPORT_BOT_TOKEN not set', status=500)
    if request.method != 'POST':
        return HttpResponse('ok', status=200)
    try:
        update = json.loads(request.body or b'{}')
    except Exception:
        return HttpResponse('ok', status=200)

    msg = update.get('message') or update.get('edited_message') or {}
    text = msg.get('text') or msg.get('caption') or ''
    entities = msg.get('entities') or msg.get('caption_entities') or []
    from_user = (msg.get('from') or {})
    tg_user_id = from_user.get('id')

    if not tg_user_id:
        return HttpResponse('ok', status=200)

    # Команда /start <code> для привязки
    if isinstance(text, str) and text.startswith('/start'):
        parts = text.split()
        if len(parts) >= 2:
            code = parts[1].strip()
            from .models_imports import TelegramImportLink
            try:
                link = TelegramImportLink.objects.get(code=code)
                link.telegram_user_id = int(tg_user_id)
                link.linked_at = timezone.now()
                link.save(update_fields=['telegram_user_id', 'linked_at'])
            except Exception:
                pass
        return HttpResponse('ok', status=200)

    # Сохраняем импорт только для привязанных
    from .models_imports import TelegramImportLink, TelegramImportedMessage
    try:
        link = TelegramImportLink.objects.get(telegram_user_id=int(tg_user_id))
    except TelegramImportLink.DoesNotExist:
        return HttpResponse('ok', status=200)

    TelegramImportedMessage.objects.create(
        user=link.user,
        text=text or '',
        entities=entities if isinstance(entities, list) else [],
    )
    return HttpResponse('ok', status=200)


@login_required
def post_delete(request, pk):
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_publish=True,
        ).values_list('channels__pk', flat=True)
        post = get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
    if request.method == 'POST':
        post.delete()
        messages.success(request, 'Пост удалён.')
        return redirect('content:list')
    return render(request, 'content/delete_confirm.html', {'post': post})


@login_required
def post_publish_now(request, pk):
    """Немедленная публикация поста."""
    if request.user.role in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_publish=True,
        ).values_list('channels__pk', flat=True)
        post = get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
    force = False
    if post.status == Post.STATUS_PUBLISHED:
        # Разрешаем "переопубликовать" отредактированный пост.
        force = True
    elif post.status not in (Post.STATUS_DRAFT, Post.STATUS_SCHEDULED, Post.STATUS_FAILED):
        messages.error(request, 'Нельзя опубликовать пост в текущем статусе.')
        return redirect('content:detail', pk=pk)
    from .tasks import publish_post_task
    try:
        post.published_by = request.user
        post.save(update_fields=['published_by'])
    except Exception:
        pass
    publish_post_task.delay(post.pk, force=force)
    messages.success(request, 'Пост отправлен на публикацию.' if not force else 'Пост отправлен на повторную публикацию.')
    return redirect('content:detail', pk=pk)
