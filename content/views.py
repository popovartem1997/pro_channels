"""
Создание, редактирование и публикация постов.
"""
import re

from django.shortcuts import render, redirect, get_object_or_404
from django.db import transaction
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST, require_http_methods
from django.template.loader import render_to_string
from django.http import FileResponse, Http404, JsonResponse
from django.contrib import messages
from django.utils import timezone
from .models import Post, PostMedia, PublishResult, normalize_post_media_orders
from django.http import HttpResponse
from django.core.files.base import ContentFile
from django.db.models import Max, Q

POST_CREATE_SESSION_PREFILL = 'post_create_prefill_v1'
POST_LIST_PAGE_SIZE = 30


def _is_feed_delete_ajax(request) -> bool:
    """Удаление поста из ленты через fetch (без перезагрузки страницы)."""
    return request.method == 'POST' and (
        request.POST.get('ajax') == '1'
        or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    )


def _get_post_for_viewer(request, pk):
    """Пост, доступный текущему пользователю (как в post_detail / post_edit)."""
    if request.user.is_staff or request.user.is_superuser:
        return get_object_or_404(Post, pk=pk)
    if request.user.role in ('manager', 'assistant_admin'):
        allowed_channel_ids = _manager_content_channel_ids(request.user)
        return get_object_or_404(
            Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct(),
            pk=pk,
        )
    if getattr(request.user, 'role', '') == 'advertiser':
        ap = getattr(request.user, 'advertiser_profile', None)
        if ap:
            return get_object_or_404(
                Post.objects.filter(campaign_application__advertiser_id=ap.pk),
                pk=pk,
            )
        raise Http404()
    return get_object_or_404(Post, pk=pk, author=request.user)


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
    ctx = {
        'user_channels': user_channels,
        'preselected': preselected,
        'prefill_text': '',
        'prefill_text_html': '',
    }
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
    """JPEG/PNG и др., ошибочно сохранённые как «документ» (расширение + сигнатура файла)."""
    from .tasks import _fix_telegram_postmedia_mislabeled_as_document

    _fix_telegram_postmedia_mislabeled_as_document(post)


def _extract_first_http_url(text: str) -> str:
    if not text:
        return ''
    m = re.search(r'https?://[^\s<>"\]\)]+', text.strip())
    if not m:
        return ''
    return m.group(0).rstrip('.,;:!?)')


def _can_create_post_from_suggestion(request, bot, bot_target_ids) -> bool:
    if request.user.is_staff or request.user.is_superuser or bot.owner_id == request.user.id:
        return True
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and bot_target_ids:
        try:
            from managers.models import TeamMember

            return TeamMember.objects.filter(
                member=request.user,
                is_active=True,
                channels__pk__in=bot_target_ids,
            ).filter(Q(can_publish=True) | Q(can_moderate=True)).exists()
        except Exception:
            return False
    return False


def _run_suggestion_media_import(request, post_pk: int, bot) -> None:
    from .tasks import _import_suggestion_media_into_post, import_media_from_suggestion_task

    if bot.platform == bot.PLATFORM_MAX:
        try:
            _imported, warnings = _import_suggestion_media_into_post(post_pk)
            for w in warnings[:15]:
                messages.warning(request, w)
        except Exception:
            try:
                import_media_from_suggestion_task.delay(post_pk)
                messages.info(
                    request,
                    'Медиа из MAX подгрузятся в черновик в фоне (основной импорт не сработал).',
                )
            except Exception:
                messages.warning(
                    request,
                    'Не удалось импортировать медиа из MAX. Обновите страницу позже или добавьте файлы вручную.',
                )
        return
    try:
        _imported, warnings = _import_suggestion_media_into_post(post_pk)
        for w in warnings[:10]:
            messages.warning(request, w)
    except Exception:
        pass


def _post_list_base_queryset(request):
    """Посты, видимые пользователю на странице списка (без среза)."""
    if request.user.role in ('manager', 'assistant_admin'):
        allowed_channel_ids = _manager_content_channel_ids(request.user)
        qs = Post.objects.filter(channels__pk__in=allowed_channel_ids).distinct()
    else:
        qs = Post.objects.filter(author=request.user)
    status_filter = request.GET.get('status', '')
    if status_filter:
        qs = qs.filter(status=status_filter)
    return (
        qs.select_related('author', 'published_by')
        .prefetch_related('channels')
        .order_by('-created_at', '-pk'),
        status_filter,
    )


@login_required
def post_list(request):
    posts_qs, status_filter = _post_list_base_queryset(request)
    batch = list(posts_qs[: POST_LIST_PAGE_SIZE + 1])
    has_more = len(batch) > POST_LIST_PAGE_SIZE
    posts = batch[:POST_LIST_PAGE_SIZE]
    return render(
        request,
        'content/list.html',
        {
            'posts': posts,
            'status_filter': status_filter,
            'statuses': Post.STATUS_CHOICES,
            'post_list_has_more': has_more,
            'post_list_next_offset': len(posts),
            'post_list_page_size': POST_LIST_PAGE_SIZE,
        },
    )


@login_required
@require_http_methods(['GET'])
def post_list_more(request):
    try:
        offset = max(0, int(request.GET.get('offset', 0)))
    except (TypeError, ValueError):
        offset = 0
    posts_qs, _status_filter = _post_list_base_queryset(request)
    batch = list(posts_qs[offset : offset + POST_LIST_PAGE_SIZE + 1])
    has_more = len(batch) > POST_LIST_PAGE_SIZE
    posts = batch[:POST_LIST_PAGE_SIZE]
    html = render_to_string(
        'content/_post_list_items.html',
        {'posts': posts},
        request=request,
    )
    return JsonResponse(
        {
            'html': html,
            'next_offset': offset + len(posts),
            'has_more': has_more,
        }
    )


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

    prefill_text = ''
    prefill_text_html = ''
    if request.method == 'GET':
        sess = request.session.pop(POST_CREATE_SESSION_PREFILL, None)
        if isinstance(sess, dict):
            prefill_text = (sess.get('text') or '')[:120_000]
            prefill_text_html = (sess.get('text_html') or '')[:120_000]
            ch_pre = sess.get('channel_ids') or []
            extra = [int(x) for x in ch_pre if str(x).isdigit()]
            allowed = set(user_channels.values_list('pk', flat=True))
            for pk in extra:
                if pk in allowed and pk not in preselected:
                    preselected.append(pk)

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

    return render(
        request,
        'content/create.html',
        _ctx_post_create(
            user_channels,
            preselected,
            prefill_text=prefill_text,
            prefill_text_html=prefill_text_html,
        ),
    )


@login_required
def post_create_from_suggestion(request, tracking_id):
    """
    Создать черновик поста из предложки и сразу открыть редактирование.
    Текст и медиа подставляются автоматически, медиа можно удалить на странице редактирования.
    """
    from bots.models import Suggestion

    suggestion = get_object_or_404(
        Suggestion.objects.select_related('bot', 'bot__owner').prefetch_related('bot__channel_groups'),
        tracking_id=tracking_id,
    )
    bot = suggestion.bot

    bot_target_ids = bot.target_channel_ids()

    if not _can_create_post_from_suggestion(request, bot, bot_target_ids):
        return HttpResponse(status=403)

    if not bot_target_ids:
        messages.error(
            request,
            'У бота предложки не выбраны группы каналов. Укажите группы в настройках бота и повторите.',
        )
        return redirect('bots:detail', bot_id=bot.id)

    # Reuse if already created
    existing = Post.objects.filter(suggestion=suggestion).order_by('-created_at').first()
    if existing:
        return redirect('content:edit', pk=existing.pk)

    text = (suggestion.text or '').strip() or '📩 Пост из предложки (проверьте медиа).'
    from html import escape as _html_esc

    text_html = ''
    if '\n' in text:
        text_html = _html_esc(text, quote=False).replace('\n', '<br>')

    post = Post.objects.create(
        author=bot.owner,
        text=text,
        text_html=text_html,
        status=Post.STATUS_DRAFT,
        suggestion=suggestion,
    )
    post.channels.set(bot_target_ids)
    post_pk = post.pk

    try:
        _run_suggestion_media_import(request, post_pk, bot)
    except Exception:
        try:
            from .tasks import import_media_from_suggestion_task

            import_media_from_suggestion_task.delay(post_pk)
        except Exception:
            pass

    messages.success(request, f'Создан черновик поста из предложки #{suggestion.short_tracking_id}.')
    return redirect('content:edit', pk=post.pk)


@login_required
@require_POST
def post_ai_from_suggestion(request, tracking_id):
    """
    Рерайт текста предложки через DeepSeek, создание/обновление черновика поста с suggestion_id
    и синхронный импорт медиа — чтобы в редакторе сразу были вложения.
    """
    from django.conf import settings
    from django.urls import reverse

    from bots.models import Suggestion
    from core.models import get_global_api_keys
    from parsing.deepseek_snippet import ai_tone_label, normalize_ai_tone, rewrite_for_feed_post

    suggestion = get_object_or_404(
        Suggestion.objects.select_related('bot', 'bot__owner').prefetch_related('bot__channel_groups'),
        tracking_id=tracking_id,
    )
    bot = suggestion.bot
    bot_target_ids = bot.target_channel_ids()

    if not _can_create_post_from_suggestion(request, bot, bot_target_ids):
        return HttpResponse(status=403)

    if not bot_target_ids:
        messages.error(
            request,
            'У бота предложки не выбраны группы каналов. Укажите группы в настройках бота и повторите.',
        )
        return redirect('bots:detail', bot_id=bot.id)

    keys = get_global_api_keys()
    api_key = keys.get_deepseek_api_key()
    if not api_key or not keys.ai_rewrite_enabled:
        messages.error(
            request,
            'Включите «AI рерайт» и сохраните ключ DeepSeek в разделе «Ключи API».',
        )
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    existing = Post.objects.filter(suggestion=suggestion).order_by('-created_at').first()
    if existing and existing.status != Post.STATUS_DRAFT:
        messages.info(request, 'Для этой заявки уже есть пост — откройте редактор.')
        return redirect('content:edit', pk=existing.pk)

    tone = normalize_ai_tone(request.POST.get('tone'))
    try:
        source_url = _extract_first_http_url(suggestion.text or '')
        plain, ht = rewrite_for_feed_post(
            original_text=suggestion.text or '',
            source_url=source_url,
            api_key=api_key,
            model_name=getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat'),
            tone=tone,
            embed_source_link=True,
        )
    except Exception as exc:
        messages.error(request, f'Не удалось сгенерировать текст: {exc}')
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    if not (plain or '').strip() and not (ht or '').strip():
        messages.error(
            request,
            'AI вернул пустой текст. Попробуйте ещё раз или создайте пост вручную.',
        )
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    text_plain = (plain or '').strip() or re.sub(r'<[^>]+>', ' ', ht or '').strip()
    text_html = (ht or '').strip()

    if existing:
        existing.text = text_plain
        existing.text_html = text_html
        existing.save(update_fields=['text', 'text_html'])
        post_pk = existing.pk
        msg = (
            f'Текст черновика обновлён через AI (тон: «{ai_tone_label(tone)}»). '
            'Медиа из заявки подтянуты при необходимости.'
        )
    else:
        post = Post.objects.create(
            author=bot.owner,
            text=text_plain,
            text_html=text_html,
            status=Post.STATUS_DRAFT,
            suggestion=suggestion,
        )
        post.channels.set(bot_target_ids)
        post_pk = post.pk
        msg = (
            f'Черновик из предложки #{suggestion.short_tracking_id} создан через AI '
            f'(тон: «{ai_tone_label(tone)}»).'
        )

    try:
        _run_suggestion_media_import(request, post_pk, bot)
    except Exception:
        try:
            from .tasks import import_media_from_suggestion_task

            import_media_from_suggestion_task.delay(post_pk)
        except Exception:
            pass

    messages.success(request, msg)
    return redirect('content:edit', pk=post_pk)


@login_required
def post_detail(request, pk):
    post = _get_post_for_viewer(request, pk)
    results = PublishResult.objects.filter(post=post).select_related('channel')
    return render(request, 'content/detail.html', {
        'post': post,
        'results': results,
    })


@login_required
def post_media_download(request, pk, media_pk):
    """
    Скачивание вложения поста с нормальным именем и Content-Disposition.
    Нужно для Safari/iOS (атрибут download на чужом URL часто не срабатывает).
    """
    post = _get_post_for_viewer(request, pk)
    media = get_object_or_404(PostMedia, pk=media_pk, post=post)
    if not media.file_is_available:
        raise Http404
    f = media.file.open('rb')
    return FileResponse(
        f,
        as_attachment=True,
        filename=media.suggested_download_filename,
    )


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
        from .tasks import _media_type_for_uploaded_file

        for idx, f in enumerate(request.FILES.getlist('media_files')):
            media_type = _media_type_for_uploaded_file(f)
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
                post.updated_at = timezone.now()
                post.save(
                    update_fields=['published_by', 'scheduled_at', 'status', 'updated_at']
                )
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
        if post.status in (Post.STATUS_PUBLISHED, Post.STATUS_PUBLISHING):
            msg = 'Опубликованные посты удалять нельзя.'
            if _is_feed_delete_ajax(request):
                return JsonResponse({'ok': False, 'error': msg}, status=400)
            messages.error(request, msg)
            return redirect('content:detail', pk=pk)
        # Менеджер/помощник может удалять только свои посты (не посты владельца/админа).
        if getattr(post, 'author_id', None) != request.user.id:
            if _is_feed_delete_ajax(request):
                return JsonResponse({'ok': False, 'error': 'Нет доступа'}, status=403)
            return HttpResponse(status=403)
    else:
        post = get_object_or_404(Post, pk=pk, author=request.user)
    if request.method == 'POST':
        post.delete()
        if _is_feed_delete_ajax(request):
            from core.views import compute_feed_quick_link_counts

            fc = (request.POST.get('feed_channel') or '').strip()
            fg = (request.POST.get('feed_chgroup') or '').strip()
            cid_scope = int(fc) if fc.isdigit() else None
            cg_scope = int(fg) if fg.isdigit() else None
            feed_counts = compute_feed_quick_link_counts(
                request.user,
                channel_id=cid_scope,
                chgroup_id=cg_scope,
            )
            return JsonResponse({'ok': True, 'feed_counts': feed_counts})
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
    elif post.status == Post.STATUS_PUBLISHING:
        # Зависание после рестарта Celery: повторяем задачу; уже удавшиеся каналы задача пропустит.
        messages.info(
            request,
            'Отправлена повторная попытка публикации. Уже опубликованные каналы будут пропущены.',
        )
    elif post.status not in (Post.STATUS_DRAFT, Post.STATUS_SCHEDULED, Post.STATUS_FAILED):
        messages.error(request, 'Нельзя опубликовать пост в текущем статусе.')
        return redirect('content:detail', pk=pk)

    notify_publish_success = post.status != Post.STATUS_PUBLISHING

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
        post.updated_at = timezone.now()
        post.save(
            update_fields=['published_by', 'scheduled_at', 'status', 'updated_at']
        )
    except Exception:
        pass
    publish_post_task.delay(post.pk, force=force)
    if notify_publish_success:
        messages.success(
            request,
            'Пост отправлен на публикацию.' if not force else 'Пост отправлен на повторную публикацию.',
        )
    return redirect('content:detail', pk=pk)
