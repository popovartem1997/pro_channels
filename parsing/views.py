"""
Парсинг каналов по ключевикам + AI рерайт.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.db.models import Q, Count
from .models import ParseSource, ParseKeyword, ParsedItem, ParseTask, AIRewriteJob
from django.http import JsonResponse
from django.http import HttpResponse
import os


def _manager_team_channel_ids(user):
    """Каналы команды с правами как у ленты постов / предложек."""
    from managers.models import TeamMember

    if getattr(user, 'role', '') not in ('manager', 'assistant_admin'):
        return []
    return list(
        TeamMember.objects.filter(member=user, is_active=True)
        .filter(Q(can_publish=True) | Q(can_moderate=True))
        .values_list('channels__pk', flat=True)
        .distinct()
    )


def _parsing_channels_qs(user):
    from channels.models import Channel

    if user.role in ('manager', 'assistant_admin'):
        ids = _manager_team_channel_ids(user)
        return Channel.objects.filter(pk__in=ids, is_active=True)
    return Channel.objects.filter(owner=user, is_active=True)


def _parsing_channel_owner_ids(user):
    return list(_parsing_channels_qs(user).values_list('owner_id', flat=True).distinct())


def _parsing_data_owner(request_user, channel):
    """FK owner в ParseSource / ParseKeyword / ParseTask — у владельца канала."""
    if channel and request_user.role in ('manager', 'assistant_admin'):
        return channel.owner
    return request_user


def _parse_sources_qs(user, selected_channel=None):
    if user.role in ('manager', 'assistant_admin'):
        ch_ids = list(_parsing_channels_qs(user).values_list('pk', flat=True))
        qs = ParseSource.objects.filter(channel_id__in=ch_ids)
    else:
        qs = ParseSource.objects.filter(owner=user)
    if selected_channel is not None:
        qs = qs.filter(channel=selected_channel)
    return qs


def _parse_keywords_qs(user, selected_channel=None):
    if user.role in ('manager', 'assistant_admin'):
        ch_ids = list(_parsing_channels_qs(user).values_list('pk', flat=True))
        qs = ParseKeyword.objects.filter(channel_id__in=ch_ids)
    else:
        qs = ParseKeyword.objects.filter(owner=user)
    if selected_channel is not None:
        qs = qs.filter(channel=selected_channel)
    return qs


def _parse_tasks_qs(user):
    if user.role in ('manager', 'assistant_admin'):
        return ParseTask.objects.filter(owner_id__in=_parsing_channel_owner_ids(user))
    return ParseTask.objects.filter(owner=user)


def _parsed_items_base_qs(user):
    if user.role in ('manager', 'assistant_admin'):
        ch_ids = _manager_team_channel_ids(user)
        return ParsedItem.objects.filter(keyword__channel_id__in=ch_ids)
    return ParsedItem.objects.filter(keyword__owner=user)


def _ai_rewrite_jobs_qs(user):
    if user.role in ('manager', 'assistant_admin'):
        oids = _parsing_channel_owner_ids(user)
        return AIRewriteJob.objects.filter(Q(owner_id__in=oids) | Q(owner=user)).order_by('-created_at')
    return AIRewriteJob.objects.filter(owner=user).order_by('-created_at')


def _ensure_parse_scheduler_every_20m():
    """
    Убедиться, что Celery Beat запускает parsing.tasks.check_parse_tasks каждые 20 минут.
    Best-effort: если django_celery_beat не установлен/не настроен — тихо пропускаем.
    """
    try:
        from django_celery_beat.models import IntervalSchedule, PeriodicTask
        every_20m, _ = IntervalSchedule.objects.get_or_create(
            every=20, period=IntervalSchedule.MINUTES
        )
        PeriodicTask.objects.update_or_create(
            name='parsing: check parse tasks (every 20m)',
            defaults={
                'interval': every_20m,
                'task': 'parsing.tasks.check_parse_tasks',
                'enabled': True,
            }
        )
    except Exception:
        return


def _ensure_auto_parse_task(owner, channel):
    """
    Автоматически создать/обновить задачу парсинга для канала владельца.
    Задача каждые 20 минут по всем активным источникам и ключевикам выбранного канала.
    """
    if not owner or not channel:
        return None
    _ensure_parse_scheduler_every_20m()
    task, _ = ParseTask.objects.get_or_create(
        owner=owner,
        name=f'Auto parsing (channel {channel.pk})',
        defaults={'schedule_cron': '*/20 * * * *', 'is_active': True},
    )
    # Keep schedule fixed
    if task.schedule_cron != '*/20 * * * *':
        task.schedule_cron = '*/20 * * * *'
        task.is_active = True
        task.save(update_fields=['schedule_cron', 'is_active'])

    sources = list(ParseSource.objects.filter(owner=owner, channel=channel, is_active=True).values_list('pk', flat=True))
    keywords = list(ParseKeyword.objects.filter(owner=owner, channel=channel, is_active=True).values_list('pk', flat=True))
    if sources:
        task.sources.set(sources)
    if keywords:
        task.keywords.set(keywords)
    return task


def _get_selected_channel(request):
    channels_qs = _parsing_channels_qs(request.user)
    cid = request.GET.get('channel') or request.POST.get('channel_id')
    if cid:
        try:
            return channels_qs.get(pk=int(cid))
        except Exception:
            return None
    sid = request.session.get('parsing_channel_id')
    if sid:
        try:
            return channels_qs.get(pk=int(sid))
        except Exception:
            pass
    return channels_qs.order_by('created_at').first()

def _telethon_session_exists_for_user(user_id: int) -> bool:
    """
    Быстрый признак "Telegram подключён": наличие session-файла Telethon.
    Полная проверка через is_user_authorized() возможна, но дороже для обычных страниц.
    """
    try:
        from django.conf import settings
        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        session_path = str(session_dir / f'user_{user_id}')
        return os.path.exists(session_path + '.session')
    except Exception:
        return False


@login_required
def sources_list(request):
    selected_channel = _get_selected_channel(request)
    if selected_channel:
        request.session['parsing_channel_id'] = selected_channel.pk
        sources = (
            _parse_sources_qs(request.user, selected_channel)
            .annotate(keyword_count=Count('keywords', distinct=True))
        )
        keywords = (
            _parse_keywords_qs(request.user, selected_channel)
            .annotate(source_count=Count('sources', distinct=True))
        )
    else:
        sources = _parse_sources_qs(request.user).annotate(keyword_count=Count('keywords', distinct=True))
        keywords = _parse_keywords_qs(request.user).annotate(source_count=Count('sources', distinct=True))
    channels = _parsing_channels_qs(request.user).order_by('-created_at')
    return render(request, 'parsing/sources.html', {
        'channels': channels,
        'selected_channel': selected_channel,
        'sources': sources,
        'keywords': keywords,
        'telethon_connected': _telethon_session_exists_for_user(request.user.id),
    })


@login_required
def telethon_connect(request):
    """
    Интерактивное подключение Telegram user API (Telethon) для парсинга.
    Шаг 1: телефон -> отправить код
    Шаг 2: код (и опционально пароль 2FA) -> сохранить session
    """
    if request.user.role in ('manager', 'assistant_admin'):
        messages.info(request, 'Подключение Telegram для парсинга настраивает владелец аккаунта.')
        return redirect('parsing:sources')
    from django.conf import settings
    from core.models import get_global_api_keys
    import asyncio

    keys = get_global_api_keys()
    api_id = (keys.telegram_api_id or '').strip()
    api_hash = (keys.get_telegram_api_hash() or '').strip()
    if not api_id or not api_hash:
        messages.error(request, 'Не заданы TELEGRAM_API_ID / TELEGRAM_API_HASH (Ключи API → Парсинг Telegram).')
        return redirect('parsing:sources')

    session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
    session_dir.mkdir(parents=True, exist_ok=True)
    session_path = str(session_dir / f'user_{request.user.id}')

    telethon_connected = _telethon_session_exists_for_user(request.user.id)
    if telethon_connected:
        messages.info(request, 'Telegram уже подключён для парсинга. Повторная авторизация не требуется.')
        return redirect('parsing:sources')

    step = (request.GET.get('step') or '').strip()
    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'send_code':
            phone = (request.POST.get('phone') or '').strip()
            if not phone:
                messages.error(request, 'Введите номер телефона.')
                return redirect('parsing:telethon_connect')

            async def _send():
                from telethon import TelegramClient
                client = TelegramClient(session_path, int(api_id), api_hash)
                await client.connect()
                res = await client.send_code_request(phone)
                await client.disconnect()
                return res.phone_code_hash

            try:
                phone_code_hash = asyncio.run(_send())
            except Exception as e:
                messages.error(request, f'Не удалось отправить код: {e}')
                return redirect('parsing:telethon_connect')

            request.session['telethon_phone'] = phone
            request.session['telethon_phone_code_hash'] = phone_code_hash
            messages.success(request, 'Код отправлен. Введите код из Telegram.')
            return redirect('/parsing/telethon/connect/?step=code')

        if action == 'confirm_code':
            phone = (request.session.get('telethon_phone') or '').strip()
            phone_code_hash = (request.session.get('telethon_phone_code_hash') or '').strip()
            code = (request.POST.get('code') or '').strip()
            password = (request.POST.get('password') or '').strip()
            if not phone or not phone_code_hash:
                messages.error(request, 'Сначала отправьте код.')
                return redirect('parsing:telethon_connect')
            if not code:
                messages.error(request, 'Введите код.')
                return redirect('/parsing/telethon/connect/?step=code')

            async def _confirm():
                from telethon import TelegramClient
                from telethon.errors import SessionPasswordNeededError
                client = TelegramClient(session_path, int(api_id), api_hash)
                await client.connect()
                try:
                    await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
                except SessionPasswordNeededError:
                    if not password:
                        raise SessionPasswordNeededError(request='2fa')
                    await client.sign_in(password=password)
                me = await client.get_me()
                await client.disconnect()
                return me

            try:
                me = asyncio.run(_confirm())
            except Exception as e:
                # If 2FA required and password empty
                if 'SessionPasswordNeededError' in str(type(e)) or '2fa' in str(e).lower():
                    messages.error(request, 'Нужен пароль 2FA. Введите пароль Telegram и повторите.')
                    return redirect('/parsing/telethon/connect/?step=code')
                messages.error(request, f'Не удалось подтвердить код: {e}')
                return redirect('/parsing/telethon/connect/?step=code')

            # cleanup
            request.session.pop('telethon_phone', None)
            request.session.pop('telethon_phone_code_hash', None)
            messages.success(request, f'Telegram подключён для парсинга: {getattr(me, "username", None) or getattr(me, "id", "")}')
            return redirect('parsing:sources')

    return render(request, 'parsing/telethon_connect.html', {
        'step': step or 'phone',
        'telethon_connected': telethon_connected,
    })


@login_required
def source_create(request):
    selected_channel = _get_selected_channel(request)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        platform = request.POST.get('platform', '')
        source_id = request.POST.get('source_id', '').strip()
        if not all([name, platform, source_id]):
            messages.error(request, 'Заполните все поля.')
        else:
            if not selected_channel:
                messages.error(request, 'Сначала выберите канал для парсинга.')
                return redirect('parsing:sources')
            data_owner = _parsing_data_owner(request.user, selected_channel)
            ParseSource.objects.create(
                owner=data_owner,
                channel=selected_channel,
                name=name,
                platform=platform,
                source_id=source_id
            )
            try:
                _ensure_auto_parse_task(data_owner, selected_channel)
            except Exception:
                pass
            messages.success(request, f'Источник "{name}" добавлен.')
        return redirect('parsing:sources')
    channels = _parsing_channels_qs(request.user).order_by('-created_at')
    return render(request, 'parsing/source_create.html', {
        'platforms': ParseSource.PLATFORM_CHOICES,
        'channels': channels,
        'selected_channel': selected_channel,
    })


@login_required
def source_delete(request, pk):
    source = get_object_or_404(_parse_sources_qs(request.user), pk=pk)
    if request.method == 'POST':
        source.delete()
        messages.success(request, 'Источник удалён.')
    return redirect('parsing:sources')


@login_required
def keyword_create(request):
    selected_channel = _get_selected_channel(request)
    if request.method == 'POST':
        keyword = request.POST.get('keyword', '').strip()
        source_ids = request.POST.getlist('sources')
        all_channels = request.POST.get('all_channels') == 'on'
        if not keyword:
            messages.error(request, 'Введите ключевое слово.')
        else:
            from channels.models import Channel
            if all_channels:
                channel_ids = list(_parsing_channels_qs(request.user).values_list('pk', flat=True))
                if not channel_ids:
                    messages.error(request, 'Нет доступных каналов для парсинга.')
                    return redirect('parsing:sources')
                created = 0
                for cid in channel_ids:
                    ch = Channel.objects.get(pk=cid)
                    data_owner = _parsing_data_owner(request.user, ch)
                    kw = ParseKeyword.objects.create(owner=data_owner, channel_id=cid, keyword=keyword)
                    created += 1
                    try:
                        _ensure_auto_parse_task(data_owner, kw.channel)
                    except Exception:
                        pass
                messages.success(request, f'Ключевое слово "{keyword}" добавлено для {created} канал(ов).')
            else:
                if not selected_channel:
                    messages.error(request, 'Сначала выберите канал для парсинга.')
                    return redirect('parsing:sources')
                data_owner = _parsing_data_owner(request.user, selected_channel)
                kw = ParseKeyword.objects.create(owner=data_owner, channel=selected_channel, keyword=keyword)
                if source_ids:
                    allowed_src = set(_parse_sources_qs(request.user, selected_channel).values_list('pk', flat=True))
                    safe = [int(x) for x in source_ids if str(x).isdigit() and int(x) in allowed_src]
                    if safe:
                        kw.sources.set(safe)
                try:
                    _ensure_auto_parse_task(data_owner, selected_channel)
                except Exception:
                    pass
                messages.success(request, f'Ключевое слово "{keyword}" добавлено.')
        return redirect('parsing:sources')
    sources = _parse_sources_qs(request.user, selected_channel) if selected_channel else _parse_sources_qs(request.user)
    return render(request, 'parsing/keyword_create.html', {
        'sources': sources,
        'selected_channel': selected_channel,
    })


@login_required
def keyword_edit(request, pk):
    kw = get_object_or_404(
        _parse_keywords_qs(request.user).select_related('channel').prefetch_related('sources'),
        pk=pk,
    )
    selected_channel = kw.channel
    sources = _parse_sources_qs(request.user, selected_channel) if selected_channel else _parse_sources_qs(request.user)
    selected_source_ids = set(kw.sources.values_list('pk', flat=True))

    if request.method == 'POST':
        keyword = (request.POST.get('keyword') or '').strip()
        source_ids = request.POST.getlist('sources')
        if not keyword:
            messages.error(request, 'Введите ключевое слово.')
        else:
            kw.keyword = keyword
            kw.save(update_fields=['keyword'])
            allowed_src = set(sources.values_list('pk', flat=True))
            safe = [int(x) for x in source_ids if str(x).isdigit() and int(x) in allowed_src]
            kw.sources.set(safe)
            if selected_channel:
                try:
                    data_owner = _parsing_data_owner(request.user, selected_channel)
                    _ensure_auto_parse_task(data_owner, selected_channel)
                except Exception:
                    pass
            messages.success(request, 'Ключевое слово обновлено.')
        if selected_channel:
            return redirect(f"{reverse('parsing:sources')}?channel={selected_channel.pk}")
        return redirect('parsing:sources')

    return render(request, 'parsing/keyword_edit.html', {
        'sources': sources,
        'keyword_obj': kw,
        'selected_channel': selected_channel,
        'selected_source_ids': selected_source_ids,
    })


@login_required
def keyword_delete(request, pk):
    kw = get_object_or_404(_parse_keywords_qs(request.user), pk=pk)
    if request.method == 'POST':
        kw.delete()
        messages.success(request, 'Ключевое слово удалено.')
    return redirect('parsing:sources')


@login_required
def parsed_items(request):
    selected_channel = _get_selected_channel(request)
    items = _parsed_items_base_qs(request.user).select_related('source', 'keyword').order_by('-found_at')
    if selected_channel:
        items = items.filter(keyword__channel=selected_channel)
    status_filter = request.GET.get('status', '')
    if status_filter:
        items = items.filter(status=status_filter)
    return render(request, 'parsing/items.html', {
        'items': items[:100],
        'status_filter': status_filter,
        'statuses': ParsedItem.STATUS_CHOICES,
        'selected_channel': selected_channel,
    })


@login_required
def item_skip(request, pk):
    """Отметить найденный материал как пропущенный/игнорируемый."""
    item = get_object_or_404(_parsed_items_base_qs(request.user), pk=pk)
    if request.method == 'POST':
        item.status = ParsedItem.STATUS_IGNORED
        item.save(update_fields=['status'])
        messages.info(request, 'Материал помечен как пропущенный.')
    return redirect('parsing:items')


@login_required
def item_to_post(request, pk):
    """Создать черновик поста из найденного материала (или AI версии) и перейти в редактор поста."""
    item = get_object_or_404(
        _parsed_items_base_qs(request.user).select_related('keyword', 'keyword__channel'),
        pk=pk,
    )
    from content.models import Post

    # Текст: AI версия приоритетнее
    text = (item.ai_rewrite or '').strip() or item.text

    kw_channel = item.keyword.channel
    if request.user.role in ('manager', 'assistant_admin'):
        post_author = kw_channel.owner if kw_channel else request.user
    else:
        post_author = request.user

    post = Post.objects.create(
        author=post_author,
        text=text,
        status=Post.STATUS_DRAFT,
    )

    if kw_channel:
        post.channels.set([kw_channel.pk])
    else:
        channel_ids = list(_parsing_channels_qs(request.user).values_list('pk', flat=True))
        if channel_ids:
            post.channels.set(channel_ids)

    item.status = ParsedItem.STATUS_USED
    item.save(update_fields=['status'])

    messages.success(request, 'Пост создан из материала. Отредактируйте и запланируйте публикацию.')
    return redirect('content:edit', pk=post.pk)

@login_required
def parse_tasks_list(request):
    """Список задач парсинга пользователя."""
    selected_channel = _get_selected_channel(request)
    tasks = _parse_tasks_qs(request.user).prefetch_related('sources', 'keywords')
    if selected_channel:
        tasks = tasks.filter(sources__channel=selected_channel).distinct()
    return render(request, 'parsing/tasks.html', {'tasks': tasks, 'selected_channel': selected_channel})


@login_required
def parse_task_create(request):
    """Создание новой задачи парсинга."""
    selected_channel = _get_selected_channel(request)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        source_ids = request.POST.getlist('sources')
        keyword_ids = request.POST.getlist('keywords')
        schedule = request.POST.get('schedule_cron', '0 */6 * * *').strip()

        if not name:
            messages.error(request, 'Введите название задачи.')
            return redirect('parsing:parse_tasks')
        if not selected_channel:
            messages.error(request, 'Сначала выберите канал для парсинга.')
            return redirect('parsing:parse_tasks')

        data_owner = _parsing_data_owner(request.user, selected_channel)
        task = ParseTask.objects.create(
            owner=data_owner,
            name=name,
            schedule_cron=schedule,
        )
        allowed_s = set(_parse_sources_qs(request.user, selected_channel).values_list('pk', flat=True))
        allowed_k = set(_parse_keywords_qs(request.user, selected_channel).values_list('pk', flat=True))
        if source_ids:
            safe_s = [int(x) for x in source_ids if str(x).isdigit() and int(x) in allowed_s]
            if safe_s:
                task.sources.set(safe_s)
        if keyword_ids:
            safe_k = [int(x) for x in keyword_ids if str(x).isdigit() and int(x) in allowed_k]
            if safe_k:
                task.keywords.set(safe_k)
        messages.success(request, f'Задача "{name}" создана.')
        return redirect('parsing:parse_tasks')

    sources = _parse_sources_qs(request.user).filter(is_active=True)
    keywords_qs = _parse_keywords_qs(request.user).filter(is_active=True)
    if selected_channel:
        sources = sources.filter(channel=selected_channel)
        keywords_qs = keywords_qs.filter(channel=selected_channel)
    return render(request, 'parsing/parse_task_create.html', {
        'sources': sources,
        'keywords': keywords_qs,
        'selected_channel': selected_channel,
    })


@login_required
def parse_task_run(request, pk):
    """Ручной запуск задачи парсинга."""
    task = get_object_or_404(_parse_tasks_qs(request.user), pk=pk)
    if request.method == 'POST':
        from .tasks import execute_parse_task
        execute_parse_task.delay(task.pk)
        messages.success(request, f'Задача "{task.name}" запущена.')
    return redirect('parsing:parse_tasks')


@login_required
def parse_task_delete(request, pk):
    """Удаление задачи парсинга."""
    task = get_object_or_404(_parse_tasks_qs(request.user), pk=pk)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Задача удалена.')
    return redirect('parsing:parse_tasks')


@login_required
def ai_rewrite_list(request):
    jobs = _ai_rewrite_jobs_qs(request.user)
    from django.conf import settings
    return render(request, 'parsing/ai_rewrite.html', {
        'jobs': jobs[:50],
        'ai_enabled': getattr(settings, 'AI_REWRITE_ENABLED', False),
    })


@login_required
def ai_rewrite_create(request):
    from django.conf import settings
    if not getattr(settings, 'AI_REWRITE_ENABLED', False):
        messages.info(request, 'AI рерайт временно отключён. Вернёмся к этому позже.')
        return redirect('parsing:items')
    # Prefill: ?item=pk
    item = None
    item_id = request.GET.get('item')
    if item_id:
        try:
            item = _parsed_items_base_qs(request.user).get(pk=int(item_id))
        except Exception:
            item = None

    if request.method == 'POST':
        original_text = request.POST.get('text', '').strip()
        prompt = request.POST.get('prompt', '').strip()
        if not original_text:
            messages.error(request, 'Введите исходный текст.')
            return redirect('parsing:ai_rewrite')
        job = AIRewriteJob.objects.create(
            owner=request.user,
            original_text=original_text,
            prompt=prompt,
            parsed_item=item,
        )
        from .tasks import ai_rewrite_task
        ai_rewrite_task.delay(job.pk)
        messages.success(request, 'Задача AI рерайта запущена.')
        return redirect('parsing:ai_rewrite')
    return render(request, 'parsing/ai_rewrite_create.html', {
        'prefill_text': item.text if item else '',
    })
