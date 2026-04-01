"""
Создание, редактирование и публикация постов.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.contrib import messages
import json
from django.utils import timezone
from .models import Post, PostMedia, PublishResult
from django.views.decorators.csrf import csrf_exempt
from django.http import HttpResponse
import secrets
from django.core.files.base import ContentFile
from django.db.models import Max, Q


def _manager_content_channel_ids(user):
    """
    Каналы, где менеджер может работать с постами из предложки / черновиками:
    публикация или модерация предложек.
    """
    from managers.models import TeamMember

    if getattr(user, 'role', '') not in ('manager', 'assistant_admin'):
        return []
    return list(
        TeamMember.objects.filter(
            member=user,
            is_active=True,
        )
        .filter(Q(can_publish=True) | Q(can_moderate=True))
        .values_list('channels__pk', flat=True)
        .distinct()
    )


def _fix_mislabeled_post_media(post):
    """JPEG/PNG, сохранённые как «документ», показываем как фото/видео."""
    IMAGE_EXT = ('.jpg', '.jpeg', '.png', '.webp', '.gif', '.bmp', '.heic', '.tiff')
    VIDEO_EXT = ('.mp4', '.mov', '.webm', '.mkv', '.m4v')
    to_update = []
    for m in PostMedia.objects.filter(post=post, media_type=PostMedia.TYPE_DOCUMENT):
        name = (m.file.name or '').lower()
        if any(name.endswith(e) for e in IMAGE_EXT):
            m.media_type = PostMedia.TYPE_PHOTO
            to_update.append(m)
        elif any(name.endswith(e) for e in VIDEO_EXT):
            m.media_type = PostMedia.TYPE_VIDEO
            to_update.append(m)
    if to_update:
        PostMedia.objects.bulk_update(to_update, ['media_type'])


@login_required
def post_list(request):
    from channels.models import Channel
    if request.user.role in ('manager', 'assistant_admin'):
        allowed_channel_ids = _manager_content_channel_ids(request.user)
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

        # Загрузка медиафайлов (порядок с 1: 1, 2, 3…)
        max_order = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
        base_order = int(max_order) if max_order is not None else 0
        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = PostMedia.TYPE_PHOTO
            if f.content_type.startswith('video'):
                media_type = PostMedia.TYPE_VIDEO
            elif not f.content_type.startswith('image'):
                media_type = PostMedia.TYPE_DOCUMENT
            PostMedia.objects.create(post=post, file=f, media_type=media_type, order=base_order + idx + 1)

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
                    channels__pk=bot.channel_id,
                ).filter(Q(can_publish=True) | Q(can_moderate=True)).exists()
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
    post_pk = post.pk

    def _enqueue_import():
        from .tasks import _import_suggestion_media_into_post, import_media_from_suggestion_task

        if bot.platform == bot.PLATFORM_MAX:
            try:
                import_media_from_suggestion_task.delay(post_pk)
            except Exception:
                try:
                    _imported, warnings = _import_suggestion_media_into_post(post_pk)
                    for w in warnings[:10]:
                        messages.warning(request, w)
                except Exception:
                    pass
            return
        try:
            _imported, warnings = _import_suggestion_media_into_post(post_pk)
            for w in warnings[:10]:
                messages.warning(request, w)
        except Exception:
            pass

    transaction.on_commit(_enqueue_import)
    if bot.platform == bot.PLATFORM_MAX:
        messages.info(request, 'Медиа из MAX подгрузятся в черновик в фоне (обычно до 1–2 минут).')

    messages.success(request, f'Создан черновик поста из предложки #{suggestion.short_tracking_id}.')
    return redirect('content:edit', pk=post.pk)


@login_required
def post_detail(request, pk):
    if request.user.role in ('manager', 'assistant_admin'):
        allowed_channel_ids = _manager_content_channel_ids(request.user)
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
        allowed_channel_ids = _manager_content_channel_ids(request.user)
        post = get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
        user_channels = Channel.objects.filter(
            pk__in=allowed_channel_ids,
            is_active=True,
        ).distinct()
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
        user_channels = Channel.objects.filter(owner=request.user, is_active=True)

    _fix_mislabeled_post_media(post)

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
                    m.order = max(1, int(raw))
                    m.save(update_fields=['order'])

        # Upload new media (порядок с 1)
        max_order = PostMedia.objects.filter(post=post).aggregate(m=Max('order'))['m']
        base_order = int(max_order) if max_order is not None else 0
        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = PostMedia.TYPE_PHOTO
            if f.content_type.startswith('video'):
                media_type = PostMedia.TYPE_VIDEO
            elif not f.content_type.startswith('image'):
                media_type = PostMedia.TYPE_DOCUMENT
            PostMedia.objects.create(post=post, file=f, media_type=media_type, order=base_order + idx + 1)

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
