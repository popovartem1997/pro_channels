"""
Создание, редактирование и публикация постов.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from .models import Post, PostMedia, PublishResult, normalize_post_media_orders
from django.http import HttpResponse
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


def _ctx_post_create(user_channels, preselected, **extra):
    ctx = {'user_channels': user_channels, 'preselected': preselected}
    ctx.update(extra)
    return ctx


def _ctx_post_edit(post, user_channels, selected_channels, **extra):
    ctx = {
        'post': post,
        'user_channels': user_channels,
        'selected_channels': selected_channels,
    }
    ctx.update(extra)
    return ctx


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
        channel_ids = request.POST.getlist('channels')
        scheduled_at_str = request.POST.get('scheduled_at', '').strip()
        repeat_enabled = request.POST.get('repeat_enabled') == 'on'
        repeat_type = request.POST.get('repeat_type', Post.REPEAT_NONE)
        repeat_interval_days = int(request.POST.get('repeat_interval_days', 3) or 3)
        pin_message = request.POST.get('pin_message') == 'on'
        disable_notification = request.POST.get('disable_notification') == 'on'
        ord_label = request.POST.get('ord_label', '').strip()

        if not channel_ids:
            messages.error(request, 'Выберите хотя бы один канал.')
            return render(request, 'content/create.html', _ctx_post_create(user_channels, channel_ids))

        raw_text = request.POST.get('text', '')
        raw_html = request.POST.get('text_html') or ''
        # Не .strip(): иначе теряются ведущие/хвостовые пробелы (колонки, выравнивание в TG).
        text = raw_text
        text_html = raw_html

        if not (text or '').strip():
            messages.error(request, 'Введите текст поста.')
            return render(request, 'content/create.html', _ctx_post_create(user_channels, channel_ids))

        post = Post(
            author=request.user,
            text=text,
            text_html=text_html,
            repeat_enabled=repeat_enabled,
            repeat_type=repeat_type,
            repeat_interval_days=repeat_interval_days,
            pin_message=pin_message,
            disable_notification=disable_notification,
            ord_label=ord_label,
        )

        if scheduled_at_str:
            from django.utils.dateparse import parse_datetime
            scheduled_at = parse_datetime(scheduled_at_str)
            if scheduled_at:
                if timezone.is_naive(scheduled_at):
                    try:
                        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
                    except Exception:
                        pass
                if scheduled_at <= timezone.now():
                    messages.error(request, 'Время публикации уже прошло. Выберите время в будущем или нажмите «Опубликовать сейчас».')
                    return render(request, 'content/create.html', _ctx_post_create(user_channels, preselected))
                post.scheduled_at = scheduled_at
                post.status = Post.STATUS_SCHEDULED
            else:
                messages.error(request, 'Неверный формат даты.')
                return render(request, 'content/create.html', _ctx_post_create(user_channels, preselected))
        else:
            post.status = Post.STATUS_DRAFT

        post.save()
        # restrict channel_ids to allowed channels
        allowed_ids = set(user_channels.values_list('pk', flat=True))
        selected_ids = [int(x) for x in channel_ids if str(x).isdigit() and int(x) in allowed_ids]
        if not selected_ids:
            messages.error(request, 'Нет доступа к выбранным каналам.')
            post.delete()
            return render(request, 'content/create.html', _ctx_post_create(user_channels, channel_ids))
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

        normalize_post_media_orders(post)

        if request.POST.get('publish_now'):
            from .tasks import publish_post_task
            publish_post_task.delay(post.pk)
            messages.success(request, 'Пост отправлен на публикацию.')
        elif post.status == Post.STATUS_SCHEDULED:
            messages.success(request, f'Пост запланирован на {post.scheduled_at:%d.%m.%Y %H:%M}.')
        else:
            messages.success(request, 'Пост сохранён как черновик.')

        return redirect('content:list')

    return render(request, 'content/create.html', _ctx_post_create(user_channels, preselected))


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
    # Если канал бота входит в группу — по запросу выделяем все каналы этой группы
    # (один паблик может публиковаться в нескольких соцсетях).
    try:
        ch = bot.channel
        group = getattr(ch, 'channel_group', None)
        if group:
            post.channels.set(list(group.channels.filter(is_active=True).values_list('pk', flat=True)))
        else:
            post.channels.set([bot.channel_id])
    except Exception:
        post.channels.set([bot.channel_id])
    post_pk = post.pk

    def _enqueue_import():
        from .tasks import _import_suggestion_media_into_post, import_media_from_suggestion_task

        if bot.platform == bot.PLATFORM_MAX:
            # Сначала синхронно — на странице редактора сразу все фото; Celery оставляем как запас при сбое.
            try:
                _imported, warnings = _import_suggestion_media_into_post(post_pk)
                for w in warnings[:15]:
                    messages.warning(request, w)
            except Exception:
                try:
                    import_media_from_suggestion_task.delay(post_pk)
                    messages.info(request, 'Медиа из MAX подгрузятся в черновик в фоне (основной импорт не сработал).')
                except Exception:
                    messages.warning(request, 'Не удалось импортировать медиа из MAX. Обновите страницу позже или добавьте файлы вручную.')
            return
        try:
            _imported, warnings = _import_suggestion_media_into_post(post_pk)
            for w in warnings[:10]:
                messages.warning(request, w)
        except Exception:
            pass

    # Импортируем медиа сразу, чтобы при нажатии "Опубликовать сейчас" в редакторе
    # пост отправлялся с вложениями. Celery остаётся запасным вариантом.
    try:
        _enqueue_import()
    except Exception:
        try:
            from .tasks import import_media_from_suggestion_task
            import_media_from_suggestion_task.delay(post_pk)
        except Exception:
            pass

    messages.success(request, f'Создан черновик поста из предложки #{suggestion.short_tracking_id}.')
    return redirect('content:edit', pk=post.pk)


@login_required
def post_detail(request, pk):
    if request.user.is_staff or request.user.is_superuser:
        post = get_object_or_404(Post, pk=pk)
    elif request.user.role in ('manager', 'assistant_admin'):
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
    if request.user.is_staff or request.user.is_superuser:
        post = get_object_or_404(Post, pk=pk)
        user_channels = Channel.objects.filter(is_active=True).distinct()
    elif request.user.role in ('manager', 'assistant_admin'):
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
        original_status = post.status
        # Media selection: keep_media[] is checked by default.
        keep_media_ids = request.POST.getlist('keep_media')
        if keep_media_ids:
            keep_set = {int(x) for x in keep_media_ids if str(x).isdigit()}
            to_delete = PostMedia.objects.filter(post=post).exclude(pk__in=keep_set)
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

        normalize_post_media_orders(post)

        channel_ids = request.POST.getlist('channels')

        raw_text = request.POST.get('text', post.text)
        raw_html = request.POST.get('text_html') or ''
        post.text = raw_text
        post.text_html = raw_html

        post.pin_message = request.POST.get('pin_message') == 'on'
        post.disable_notification = request.POST.get('disable_notification') == 'on'
        post.ord_label = request.POST.get('ord_label', '').strip()
        post.repeat_enabled = request.POST.get('repeat_enabled') == 'on'
        post.repeat_type = request.POST.get('repeat_type', Post.REPEAT_NONE)
        post.repeat_interval_days = int(request.POST.get('repeat_interval_days', 3) or 3)

        scheduled_at_str = request.POST.get('scheduled_at', '').strip()
        if scheduled_at_str:
            from django.utils.dateparse import parse_datetime
            scheduled_at = parse_datetime(scheduled_at_str)
            if scheduled_at:
                if timezone.is_naive(scheduled_at):
                    try:
                        scheduled_at = timezone.make_aware(scheduled_at, timezone.get_current_timezone())
                    except Exception:
                        pass
                if scheduled_at <= timezone.now():
                    messages.error(request, 'Время публикации уже прошло. Выберите время в будущем или очистите поле планирования.')
                    return render(
                        request,
                        'content/edit.html',
                        _ctx_post_edit(post, user_channels, [int(x) for x in channel_ids if str(x).isdigit()]),
                    )
                post.scheduled_at = scheduled_at
                post.status = Post.STATUS_SCHEDULED
        else:
            # Если пользователь очистил планирование — снимаем scheduled (но не трогаем published).
            if post.status == Post.STATUS_SCHEDULED:
                post.scheduled_at = None
                post.status = Post.STATUS_DRAFT
        if not (post.text or '').strip():
            messages.error(request, 'Введите текст поста.')
            return render(
                request,
                'content/edit.html',
                _ctx_post_edit(post, user_channels, [int(x) for x in channel_ids if str(x).isdigit()]),
            )
        if not channel_ids:
            messages.error(request, 'Выберите хотя бы один канал.')
            return render(request, 'content/edit.html', _ctx_post_edit(post, user_channels, []))

        post.save()
        allowed_ids = set(user_channels.values_list('pk', flat=True))
        selected_ids = [int(x) for x in channel_ids if str(x).isdigit() and int(x) in allowed_ids]
        if not selected_ids:
            messages.error(request, 'Нет доступа к выбранным каналам.')
            return render(request, 'content/edit.html', _ctx_post_edit(post, user_channels, []))
        post.channels.set(selected_ids)
        # Publish now from edit page (same behavior as on detail page)
        if request.POST.get('publish_now'):
            # Если пост создан из предложки и медиа ещё не успели импортироваться — подгружаем перед публикацией.
            try:
                if getattr(post, 'suggestion_id', None) and not PostMedia.objects.filter(post=post).exists():
                    from .tasks import _import_suggestion_media_into_post
                    _imported, warnings = _import_suggestion_media_into_post(post.pk)
                    for w in (warnings or [])[:10]:
                        messages.warning(request, w)
            except Exception:
                pass
            from .tasks import publish_post_task
            force = bool(original_status == Post.STATUS_PUBLISHED)
            try:
                post.published_by = request.user
                post.scheduled_at = None
                post.status = Post.STATUS_PUBLISHING
                post.save(update_fields=['published_by', 'scheduled_at', 'status'])
            except Exception:
                pass
            publish_post_task.delay(post.pk, force=force)
            messages.success(
                request,
                'Пост отправлен на публикацию.' if not force else 'Пост отправлен на повторную публикацию.',
            )
            return redirect('content:detail', pk=pk)

        messages.success(request, 'Пост обновлён.')
        return redirect('content:detail', pk=pk)

    return render(
        request,
        'content/edit.html',
        _ctx_post_edit(post, user_channels, list(post.channels.values_list('pk', flat=True))),
    )


@login_required
def post_delete(request, pk):
    if request.user.is_staff or request.user.is_superuser:
        post = get_object_or_404(Post, pk=pk)
    elif request.user.role in ('manager', 'assistant_admin'):
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
        # Менеджер/помощник может удалять только свои посты (не посты владельца/админа).
        if getattr(post, 'author_id', None) != request.user.id:
            return HttpResponse(status=403)
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
    if request.method == 'POST':
        post.delete()
        messages.success(request, 'Пост удалён.')
        next_url = (request.POST.get('next') or request.GET.get('next') or '').strip()
        if next_url.startswith('/') and not next_url.startswith('//'):
            return redirect(next_url)
        return redirect('content:list')
    return render(request, 'content/delete_confirm.html', {'post': post})


@login_required
def post_publish_now(request, pk):
    """Немедленная публикация поста."""
    if request.user.is_staff or request.user.is_superuser:
        post = get_object_or_404(Post, pk=pk)
    elif request.user.role in ('manager', 'assistant_admin'):
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
    # Если пост создан из предложки и медиа ещё не успели импортироваться — подгружаем перед публикацией.
    try:
        if getattr(post, 'suggestion_id', None) and not PostMedia.objects.filter(post=post).exists():
            from .tasks import _import_suggestion_media_into_post
            _imported, warnings = _import_suggestion_media_into_post(post.pk)
            for w in (warnings or [])[:10]:
                messages.warning(request, w)
    except Exception:
        pass
    try:
        post.published_by = request.user
        # Если пост был запланирован (в т.ч. на прошедшее время) — снимаем планирование,
        # чтобы в UI сразу было видно, что это немедленная публикация.
        post.scheduled_at = None
        post.status = Post.STATUS_PUBLISHING
        post.save(update_fields=['published_by', 'scheduled_at', 'status'])
    except Exception:
        pass
    publish_post_task.delay(post.pk, force=force)
    messages.success(request, 'Пост отправлен на публикацию.' if not force else 'Пост отправлен на повторную публикацию.')
    return redirect('content:detail', pk=pk)
