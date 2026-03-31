"""
Парсинг каналов по ключевикам + AI рерайт.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from .models import ParseSource, ParseKeyword, ParsedItem, ParseTask, AIRewriteJob
from django.http import JsonResponse


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
    from channels.models import Channel
    # 1) querystring has priority
    cid = request.GET.get('channel') or request.POST.get('channel_id')
    if cid:
        try:
            return Channel.objects.get(pk=int(cid), owner=request.user)
        except Exception:
            return None
    # 2) session
    sid = request.session.get('parsing_channel_id')
    if sid:
        try:
            return Channel.objects.get(pk=int(sid), owner=request.user)
        except Exception:
            pass
    # 3) first active channel
    return Channel.objects.filter(owner=request.user, is_active=True).order_by('created_at').first()


@login_required
def sources_list(request):
    selected_channel = _get_selected_channel(request)
    if selected_channel:
        request.session['parsing_channel_id'] = selected_channel.pk
        sources = ParseSource.objects.filter(owner=request.user, channel=selected_channel)
        keywords = ParseKeyword.objects.filter(owner=request.user, channel=selected_channel)
    else:
        sources = ParseSource.objects.filter(owner=request.user)
        keywords = ParseKeyword.objects.filter(owner=request.user)
    from channels.models import Channel
    channels = Channel.objects.filter(owner=request.user, is_active=True).order_by('-created_at')
    return render(request, 'parsing/sources.html', {
        'channels': channels,
        'selected_channel': selected_channel,
        'sources': sources,
        'keywords': keywords,
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
            ParseSource.objects.create(
                owner=request.user,
                channel=selected_channel,
                name=name,
                platform=platform,
                source_id=source_id
            )
            try:
                _ensure_auto_parse_task(request.user, selected_channel)
            except Exception:
                pass
            messages.success(request, f'Источник "{name}" добавлен.')
        return redirect('parsing:sources')
    from channels.models import Channel
    channels = Channel.objects.filter(owner=request.user, is_active=True).order_by('-created_at')
    return render(request, 'parsing/source_create.html', {
        'platforms': ParseSource.PLATFORM_CHOICES,
        'channels': channels,
        'selected_channel': selected_channel,
    })


@login_required
def source_delete(request, pk):
    source = get_object_or_404(ParseSource, pk=pk, owner=request.user)
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
                channel_ids = list(Channel.objects.filter(owner=request.user, is_active=True).values_list('pk', flat=True))
                if not channel_ids:
                    messages.error(request, 'Нет активных каналов. Сначала добавьте канал.')
                    return redirect('parsing:sources')
                created = 0
                for cid in channel_ids:
                    kw = ParseKeyword.objects.create(owner=request.user, channel_id=cid, keyword=keyword)
                    created += 1
                    try:
                        _ensure_auto_parse_task(request.user, kw.channel)
                    except Exception:
                        pass
                messages.success(request, f'Ключевое слово "{keyword}" добавлено для {created} канал(ов).')
            else:
                if not selected_channel:
                    messages.error(request, 'Сначала выберите канал для парсинга.')
                    return redirect('parsing:sources')
                kw = ParseKeyword.objects.create(owner=request.user, channel=selected_channel, keyword=keyword)
                if source_ids:
                    kw.sources.set(source_ids)
                try:
                    _ensure_auto_parse_task(request.user, selected_channel)
                except Exception:
                    pass
                messages.success(request, f'Ключевое слово "{keyword}" добавлено.')
        return redirect('parsing:sources')
    sources = ParseSource.objects.filter(owner=request.user, channel=selected_channel) if selected_channel else ParseSource.objects.filter(owner=request.user)
    return render(request, 'parsing/keyword_create.html', {
        'sources': sources,
        'selected_channel': selected_channel,
    })


@login_required
def keyword_delete(request, pk):
    kw = get_object_or_404(ParseKeyword, pk=pk, owner=request.user)
    if request.method == 'POST':
        kw.delete()
        messages.success(request, 'Ключевое слово удалено.')
    return redirect('parsing:sources')


@login_required
def parsed_items(request):
    selected_channel = _get_selected_channel(request)
    items = ParsedItem.objects.filter(keyword__owner=request.user).select_related('source', 'keyword').order_by('-found_at')
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
    item = get_object_or_404(ParsedItem, pk=pk, keyword__owner=request.user)
    if request.method == 'POST':
        item.status = ParsedItem.STATUS_IGNORED
        item.save(update_fields=['status'])
        messages.info(request, 'Материал помечен как пропущенный.')
    return redirect('parsing:items')


@login_required
def item_to_post(request, pk):
    """Создать черновик поста из найденного материала (или AI версии) и перейти в редактор поста."""
    item = get_object_or_404(ParsedItem, pk=pk, keyword__owner=request.user)
    from content.models import Post
    from channels.models import Channel

    # Текст: AI версия приоритетнее
    text = (item.ai_rewrite or '').strip() or item.text

    post = Post.objects.create(
        author=request.user,
        text=text,
        status=Post.STATUS_DRAFT,
    )

    # Каналы по умолчанию — все активные каналы пользователя (можно потом снять галочки)
    channel_ids = list(Channel.objects.filter(owner=request.user, is_active=True).values_list('pk', flat=True))
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
    tasks = ParseTask.objects.filter(owner=request.user).prefetch_related('sources', 'keywords')
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

        task = ParseTask.objects.create(
            owner=request.user,
            name=name,
            schedule_cron=schedule,
        )
        if source_ids:
            task.sources.set(source_ids)
        if keyword_ids:
            task.keywords.set(keyword_ids)
        messages.success(request, f'Задача "{name}" создана.')
        return redirect('parsing:parse_tasks')

    sources = ParseSource.objects.filter(owner=request.user, is_active=True)
    keywords_qs = ParseKeyword.objects.filter(owner=request.user, is_active=True)
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
    task = get_object_or_404(ParseTask, pk=pk, owner=request.user)
    if request.method == 'POST':
        from .tasks import execute_parse_task
        execute_parse_task.delay(task.pk)
        messages.success(request, f'Задача "{task.name}" запущена.')
    return redirect('parsing:parse_tasks')


@login_required
def parse_task_delete(request, pk):
    """Удаление задачи парсинга."""
    task = get_object_or_404(ParseTask, pk=pk, owner=request.user)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Задача удалена.')
    return redirect('parsing:parse_tasks')


@login_required
def ai_rewrite_list(request):
    jobs = AIRewriteJob.objects.filter(owner=request.user).order_by('-created_at')
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
            item = ParsedItem.objects.get(pk=int(item_id), keyword__owner=request.user)
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
