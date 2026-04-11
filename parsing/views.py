"""
Парсинг каналов по ключевикам + AI рерайт.
"""
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from urllib.parse import urlencode
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.utils import timezone
from django.db.models import Q, Count, F
from .models import ParseSource, ParseKeyword, ParsedItem, ParseTask, AIRewriteJob, KeywordHarvestJob
from django.http import JsonResponse
from django.http import HttpResponse
from django.views.decorators.http import require_POST, require_http_methods
from django.template.loader import render_to_string
import os


def _can_delete_parsed_items_as_owner_or_staff(user) -> bool:
    """Удаление материалов парсинга (по одному или массово) — владелец или Django-админ, не менеджер / не помощник."""
    return bool(
        user.is_staff
        or user.is_superuser
        or getattr(user, 'role', '') == 'owner'
    )


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


def _parsing_groups_qs(user):
    from channels.models import ChannelGroup

    if user.role in ('manager', 'assistant_admin'):
        ids = _manager_team_channel_ids(user)
        if not ids:
            return ChannelGroup.objects.none()
        return ChannelGroup.objects.filter(channels__pk__in=ids).distinct().order_by('name', 'pk')
    return ChannelGroup.objects.filter(owner=user).order_by('name', 'pk')


def _parsing_has_ungrouped_channels(user):
    from channels.models import Channel

    ids = list(_parsing_channels_qs(user).values_list('pk', flat=True))
    if not ids:
        return False
    return Channel.objects.filter(pk__in=ids, channel_group__isnull=True).exists()


def _get_parse_scope(request):
    """
    Фильтр парсинга по группе каналов (один паблик в разных соцсетях).
    chgroup=all | none | <id группы>
    """
    from channels.models import Channel

    acc_ids = set(_parsing_channels_qs(request.user).values_list('pk', flat=True))
    raw = (request.GET.get('chgroup') or '').strip()
    if raw == '':
        raw = (request.session.get('parsing_chgroup') or 'all').strip() or 'all'

    if raw in ('all',):
        request.session['parsing_chgroup'] = 'all'
        return {'mode': 'all', 'group': None, 'channel_ids': None, 'chgroup_param': 'all'}

    if raw in ('none', 'ungrouped'):
        request.session['parsing_chgroup'] = 'none'
        uids = list(Channel.objects.filter(pk__in=acc_ids, channel_group__isnull=True).values_list('pk', flat=True))
        return {'mode': 'ungrouped', 'group': None, 'channel_ids': uids, 'chgroup_param': 'none'}

    try:
        gid = int(raw)
    except ValueError:
        request.session['parsing_chgroup'] = 'all'
        return {'mode': 'all', 'group': None, 'channel_ids': None, 'chgroup_param': 'all'}

    try:
        g = _parsing_groups_qs(request.user).get(pk=gid)
    except Exception:
        request.session['parsing_chgroup'] = 'all'
        return {'mode': 'all', 'group': None, 'channel_ids': None, 'chgroup_param': 'all'}

    cids = list(g.channels.filter(pk__in=acc_ids).values_list('pk', flat=True))
    request.session['parsing_chgroup'] = str(gid)
    return {'mode': 'group', 'group': g, 'channel_ids': cids, 'chgroup_param': str(gid)}


def _channels_in_scope(user, scope):
    """Каналы, доступные в текущем фильтре (для форм «на какой канал вешать источник»)."""
    base = _parsing_channels_qs(user).order_by('platform', 'name')
    cids = scope.get('channel_ids')
    if cids is None:
        return base
    return base.filter(pk__in=cids)


def _parse_sources_qs(user, scope=None):
    if user.role in ('manager', 'assistant_admin'):
        ch_ids = list(_parsing_channels_qs(user).values_list('pk', flat=True))
        qs = ParseSource.objects.filter(channel_id__in=ch_ids)
    else:
        qs = ParseSource.objects.filter(owner=user)
    if scope and scope.get('channel_ids') is not None:
        qs = qs.filter(channel_id__in=scope['channel_ids'])
    return qs


def _parse_keywords_qs(user, scope=None):
    if user.role in ('manager', 'assistant_admin'):
        ch_ids = list(_parsing_channels_qs(user).values_list('pk', flat=True))
        qs = ParseKeyword.objects.filter(channel_id__in=ch_ids)
    else:
        qs = ParseKeyword.objects.filter(owner=user)
    if scope and scope.get('channel_ids') is not None:
        qs = qs.filter(channel_id__in=scope['channel_ids'])
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


def _ensure_auto_parse_task(owner, channel):
    """Обновить авто-задачу Celery (группа каналов или один канал)."""
    if not owner or not channel:
        return None
    from .schedule_sync import sync_auto_parse_tasks_for_channel

    sync_auto_parse_tasks_for_channel(channel)
    return None


def _parsing_template_extra(request, scope):
    """Общий контекст для шаблонов парсинга (фильтр по группе)."""
    p = scope.get('chgroup_param') or 'all'
    qprefix = '' if p == 'all' else f'chgroup={p}&'
    scope_q = '' if p == 'all' else f'?chgroup={p}'
    return {
        'channel_groups': _parsing_groups_qs(request.user),
        'parse_scope': scope,
        'channels_in_scope': _channels_in_scope(request.user, scope),
        'channel_groups_in_scope': _channels_in_scope(request.user, scope)
        .exclude(channel_group__isnull=True)
        .values('channel_group__pk', 'channel_group__name')
        .distinct()
        .order_by('channel_group__name'),
        'has_ungrouped_channels': _parsing_has_ungrouped_channels(request.user),
        'parse_chgroup_query_prefix': qprefix,
        'parsing_scope_query': scope_q,
        'has_any_parsing_channels': _parsing_channels_qs(request.user).exists(),
    }


def _parsing_sources_redirect(request, scope):
    """URL списка источников с сохранением фильтра chgroup."""
    base = reverse('parsing:sources')
    p = scope.get('chgroup_param') or 'all'
    if p and p != 'all':
        return f'{base}?chgroup={p}'
    return base


def _parse_url(viewname, scope, *, kwargs=None):
    """reverse + chgroup query для страниц парсинга."""
    url = reverse(viewname, kwargs=kwargs or {})
    p = scope.get('chgroup_param') or 'all'
    if p != 'all':
        sep = '&' if '?' in url else '?'
        return f'{url}{sep}chgroup={p}'
    return url


def _telethon_session_state_for_user(user_id: int) -> dict:
    """
    Состояние Telethon-сессий.
    Поддерживаем 2 формата:
    - user_<id>.session (UI flow)
    - user_default.session (management command fallback)
    """
    state = {
        'connected': False,
        'user_session': False,
        'default_session': False,
        'user_session_path': '',
        'default_session_path': '',
    }
    try:
        from django.conf import settings
        session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
        user_base = str(session_dir / f'user_{user_id}')
        default_base = str(session_dir / 'user_default')
        state['user_session_path'] = user_base + '.session'
        state['default_session_path'] = default_base + '.session'
        state['user_session'] = os.path.exists(state['user_session_path'])
        state['default_session'] = os.path.exists(state['default_session_path'])
        state['connected'] = bool(state['user_session'] or state['default_session'])
    except Exception:
        pass
    return state


def _telethon_session_exists_for_user(user_id: int) -> bool:
    return bool(_telethon_session_state_for_user(user_id).get('connected'))


def _sources_list_page_context(request):
    """Пагинация источников и ключевиков на странице парсинга."""
    scope = _get_parse_scope(request)
    from django.core.paginator import Paginator

    sources_qs = (
        _parse_sources_qs(request.user, scope)
        .select_related('channel', 'channel_group')
        .annotate(keyword_count=Count('keywords', distinct=True))
        .order_by('name', 'pk')
    )
    keywords_qs = (
        _parse_keywords_qs(request.user, scope)
        .select_related('channel', 'channel_group')
        .annotate(source_count=Count('sources', distinct=True))
        .order_by('keyword', 'pk')
    )

    try:
        per_page = int((request.GET.get('per_page') or '').strip() or 25)
    except Exception:
        per_page = 25
    per_page = max(10, min(per_page, 100))

    src_page = (request.GET.get('src_page') or '').strip()
    kw_page = (request.GET.get('kw_page') or '').strip()

    src_p = Paginator(sources_qs, per_page)
    kw_p = Paginator(keywords_qs, per_page)
    sources = src_p.get_page(src_page)
    keywords = kw_p.get_page(kw_page)
    return {
        'parse_scope': scope,
        'sources': sources,
        'keywords': keywords,
        'src_paginator': src_p,
        'kw_paginator': kw_p,
        'src_page_obj': sources,
        'kw_page_obj': keywords,
        'per_page': per_page,
    }


@login_required
def sources_list(request):
    ctx = _sources_list_page_context(request)
    scope = ctx['parse_scope']
    ctx.update(
        {
            'telethon_state': _telethon_session_state_for_user(request.user.id),
            'telethon_connected': _telethon_session_exists_for_user(request.user.id),
        }
    )
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/sources.html', ctx)


@login_required
@require_http_methods(['GET'])
def sources_list_fragments(request):
    """Тот же контент, что у списка источников, но только блок с двумя колонками (для AJAX)."""
    ctx = _sources_list_page_context(request)
    scope = ctx['parse_scope']
    ctx.update(_parsing_template_extra(request, scope))
    html = render_to_string('parsing/_parsing_panels_row.html', ctx, request=request)
    return JsonResponse({'html': html})


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

    telethon_state = _telethon_session_state_for_user(request.user.id)
    telethon_connected = bool(telethon_state.get('connected'))

    step = (request.GET.get('step') or '').strip()
    in_progress = bool((request.session.get('telethon_phone_code_hash') or '').strip())
    # ВАЖНО: после send_code Telethon создаёт файл .session, но это ещё не значит,
    # что сессия авторизована. Поэтому не блокируем шаг ввода кода, если авторизация в процессе.
    if telethon_connected and not in_progress and step != 'code':
        messages.info(request, 'Telegram уже подключён для парсинга. Повторная авторизация не требуется.')
        return redirect('parsing:sources')

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
                from parsing.tasks import _telethon_session_lock

                with _telethon_session_lock(request.user.id, wait=90.0, wait_chunk=5.0):
                    phone_code_hash = asyncio.run(_send())
            except RuntimeError as e:
                messages.error(
                    request,
                    f'Сессия Telegram занята (импорт истории или парсинг). Подождите минуту или завершите фоновую задачу: {e}',
                )
                return redirect('parsing:telethon_connect')
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
                from parsing.tasks import _telethon_session_lock

                with _telethon_session_lock(request.user.id, wait=90.0, wait_chunk=5.0):
                    me = asyncio.run(_confirm())
            except RuntimeError as e:
                messages.error(
                    request,
                    f'Сессия Telegram занята (импорт истории или парсинг). Повторите позже: {e}',
                )
                return redirect('/parsing/telethon/connect/?step=code')
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
        'telethon_state': telethon_state,
    })


@login_required
def telethon_disconnect(request):
    """
    Завершить Telethon-сессию для парсинга и позволить переподключиться.
    Удаляем session-файлы на диске (best-effort).
    """
    if request.user.role in ('manager', 'assistant_admin'):
        messages.info(request, 'Завершение Telegram-сессии выполняет владелец аккаунта.')
        return redirect('parsing:sources')
    if request.method != 'POST':
        return HttpResponse(status=405)

    try:
        import os

        state = _telethon_session_state_for_user(request.user.id)
        # Удаляем и per-user, и fallback default, если он существует.
        bases = []
        try:
            from django.conf import settings
            session_dir = settings.BASE_DIR / 'media' / 'telethon_sessions'
            bases = [
                str(session_dir / f'user_{request.user.id}'),
                str(session_dir / 'user_default'),
            ]
        except Exception:
            bases = []

        paths = []
        for b in bases:
            paths.extend([b + '.session', b + '.session-journal'])

        removed = 0
        for p in paths:
            try:
                if os.path.exists(p):
                    os.remove(p)
                    removed += 1
            except Exception:
                pass
        # сбрасываем промежуточные значения шага авторизации
        request.session.pop('telethon_phone', None)
        request.session.pop('telethon_phone_code_hash', None)
        if removed:
            messages.success(request, 'Telegram-сессия завершена. Можно подключиться заново.')
        else:
            # Если UI показывал connected, но файлов не нашли — сообщим чуть точнее.
            if state.get('connected'):
                messages.info(request, 'Сессия отмечена как подключённая, но файлы не найдены. Можно подключаться заново.')
            else:
                messages.info(request, 'Сессия не найдена. Можно подключаться заново.')
    except Exception as e:
        messages.error(request, f'Не удалось завершить сессию: {e}')
    return redirect('parsing:sources')


@login_required
def source_create(request):
    scope = _get_parse_scope(request)
    channels_in_scope = _channels_in_scope(request.user, scope)
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        platform = request.POST.get('platform', '')
        source_id = request.POST.get('source_id', '').strip()
        target_gid = (request.POST.get('channel_group_id') or '').strip()
        if not all([name, platform, source_id]):
            messages.error(request, 'Заполните все поля.')
        else:
            allowed_channels = channels_in_scope.select_related('channel_group')
            allowed_ids = set(allowed_channels.values_list('pk', flat=True))
            if not allowed_ids:
                messages.error(request, 'Нет доступных каналов в текущем фильтре.')
                return redirect('parsing:sources')
            from channels.models import Channel
            from channels.models import ChannelGroup

            if not target_gid.isdigit():
                messages.error(request, 'Выберите группу каналов (проект) для источника.')
                return redirect(_parse_url('parsing:source_create', scope))

            group = ChannelGroup.objects.filter(pk=int(target_gid)).first()
            if not group:
                messages.error(request, 'Группа не найдена.')
                return redirect(_parse_url('parsing:source_create', scope))

            # группа должна пересекаться с текущей видимостью.
            # MAX/Instagram не парсим — якорный канал берём только из поддерживаемых для парсинга публикаций.
            anchor = (
                allowed_channels.filter(channel_group=group)
                .exclude(platform__in=(Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM))
                .order_by('pk')
                .first()
            )
            if not anchor:
                messages.error(request, 'Эта группа недоступна в текущем фильтре.')
                return redirect(_parse_url('parsing:source_create', scope))

            data_owner = _parsing_data_owner(request.user, anchor)
            ParseSource.objects.create(
                owner=data_owner,
                channel=anchor,
                channel_group=group,
                name=name,
                platform=platform,
                source_id=source_id,
            )
            try:
                _ensure_auto_parse_task(data_owner, anchor)
            except Exception:
                pass
            messages.success(request, f'Источник "{name}" добавлен.')
        return redirect(_parsing_sources_redirect(request, scope))
    # MAX парсинг отключён
    ctx = {'platforms': [p for p in ParseSource.PLATFORM_CHOICES if p[0] != 'max']}
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/source_create.html', ctx)


@login_required
def source_delete(request, pk):
    scope = _get_parse_scope(request)
    source = get_object_or_404(_parse_sources_qs(request.user, scope), pk=pk)
    if request.method == 'POST':
        ch = source.channel
        source.delete()
        messages.success(request, 'Источник удалён.')
        if ch:
            try:
                _ensure_auto_parse_task(ch.owner, ch)
            except Exception:
                pass
    return redirect(_parsing_sources_redirect(request, scope))


@login_required
def keyword_create(request):
    scope = _get_parse_scope(request)
    channels_in_scope = _channels_in_scope(request.user, scope)
    allowed_ch_ids = list(channels_in_scope.values_list('pk', flat=True))
    if request.method == 'POST':
        from collections import defaultdict

        from channels.models import Channel, ChannelGroup

        keyword = request.POST.get('keyword', '').strip()
        source_ids = request.POST.getlist('sources')
        target_mode = (request.POST.get('target_mode') or 'group_all').strip()
        target_gid = (request.POST.get('channel_group_id') or '').strip()
        target_cid = (request.POST.get('channel_id') or '').strip()

        if not keyword:
            messages.error(request, 'Введите ключевое слово.')
            return redirect(_parsing_sources_redirect(request, scope))

        if not allowed_ch_ids:
            messages.error(request, 'Нет доступных каналов в текущем фильтре.')
            return redirect(_parsing_sources_redirect(request, scope))

        if target_mode == 'sources_only':
            if not source_ids:
                messages.error(request, 'Выберите хотя бы один источник.')
                return redirect(_parse_url('parsing:keyword_create', scope))
            raw_ids = [int(x) for x in source_ids if str(x).isdigit()]
            sources_pick = (
                _parse_sources_qs(request.user, scope)
                .filter(pk__in=raw_ids, is_active=True)
                .select_related('channel', 'channel_group')
            )
            found_ids = set(sources_pick.values_list('pk', flat=True))
            if not raw_ids or set(raw_ids) != found_ids:
                messages.error(request, 'Недопустимый набор источников.')
                return redirect(_parse_url('parsing:keyword_create', scope))
            by_channel = defaultdict(list)
            for src in sources_pick:
                if not src.channel_id:
                    messages.error(
                        request,
                        'У одного из источников не задан канал публикации. Укажите канал в настройках источника.',
                    )
                    return redirect(_parse_url('parsing:keyword_create', scope))
                by_channel[src.channel_id].append(src.pk)
            created = 0
            for cid, pks in by_channel.items():
                ch = Channel.objects.filter(pk=cid, pk__in=allowed_ch_ids).first()
                if not ch or ch.platform in (Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM):
                    continue
                data_owner = _parsing_data_owner(request.user, ch)
                kw = ParseKeyword.objects.create(
                    owner=data_owner,
                    channel=ch,
                    channel_group=ch.channel_group,
                    keyword=keyword,
                )
                kw.sources.set(pks)
                created += 1
                try:
                    _ensure_auto_parse_task(data_owner, ch)
                except Exception:
                    pass
            if created == 0:
                messages.error(
                    request,
                    'Не удалось создать ключевик: нет подходящих каналов (платформы MAX/Instagram не участвуют в парсинге).',
                )
            else:
                messages.success(
                    request,
                    f'Ключевое слово «{keyword}» добавлено для {created} канал(ов) по выбранным источникам.',
                )
            return redirect(_parsing_sources_redirect(request, scope))

        if target_mode not in ('group_all', 'group_channel'):
            messages.error(request, 'Выберите способ привязки ключевого слова.')
            return redirect(_parse_url('parsing:keyword_create', scope))

        if not target_gid.isdigit():
            messages.error(request, 'Выберите группу каналов (проект).')
            return redirect(_parse_url('parsing:keyword_create', scope))
        group = ChannelGroup.objects.filter(pk=int(target_gid)).first()
        if not group:
            messages.error(request, 'Группа не найдена.')
            return redirect(_parse_url('parsing:keyword_create', scope))

        group_channels = list(
            channels_in_scope.select_related('channel_group')
            .filter(channel_group=group)
            .exclude(platform__in=(Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM))
            .values_list('pk', flat=True)
        )
        if not group_channels:
            messages.error(request, 'В выбранной группе нет доступных каналов.')
            return redirect(_parse_url('parsing:keyword_create', scope))

        sources_qs = _parse_sources_qs(request.user, scope).filter(is_active=True)
        sources_qs = sources_qs.filter(channel_group=group)
        if source_ids:
            allowed_src = set(sources_qs.values_list('pk', flat=True))
            safe = [int(x) for x in source_ids if str(x).isdigit() and int(x) in allowed_src]
        else:
            safe = list(sources_qs.values_list('pk', flat=True))

        if target_mode == 'group_all':
            created = 0
            for cid in group_channels:
                ch = Channel.objects.get(pk=cid)
                data_owner = _parsing_data_owner(request.user, ch)
                kw = ParseKeyword.objects.create(
                    owner=data_owner,
                    channel_id=cid,
                    channel_group=group,
                    keyword=keyword,
                )
                if safe:
                    kw.sources.set(safe)
                created += 1
                try:
                    _ensure_auto_parse_task(data_owner, kw.channel)
                except Exception:
                    pass
            messages.success(request, f'Ключевое слово «{keyword}» добавлено для {created} канал(ов) группы.')
        else:
            if not target_cid.isdigit() or int(target_cid) not in set(group_channels):
                messages.error(request, 'Выберите канал из выбранной группы.')
                return redirect(_parse_url('parsing:keyword_create', scope))
            ch = Channel.objects.get(pk=int(target_cid))
            data_owner = _parsing_data_owner(request.user, ch)
            kw = ParseKeyword.objects.create(
                owner=data_owner,
                channel=ch,
                channel_group=group,
                keyword=keyword,
            )
            if safe:
                kw.sources.set(safe)
            try:
                _ensure_auto_parse_task(data_owner, kw.channel)
            except Exception:
                pass
            messages.success(request, f'Ключевое слово «{keyword}» добавлено.')
        return redirect(_parsing_sources_redirect(request, scope))
    sources = _parse_sources_qs(request.user, scope).select_related('channel', 'channel_group')
    ctx = {'sources': sources}
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/keyword_create.html', ctx)


@login_required
def keyword_edit(request, pk):
    scope = _get_parse_scope(request)
    kw = get_object_or_404(
        _parse_keywords_qs(request.user, scope)
        .select_related('channel', 'channel_group')
        .prefetch_related('sources'),
        pk=pk,
    )
    selected_channel = kw.channel
    sources = _parse_sources_qs(request.user, scope).select_related('channel', 'channel_group')
    if selected_channel:
        if selected_channel.channel_group_id:
            sources = sources.filter(channel_group_id=selected_channel.channel_group_id)
        else:
            sources = sources.filter(channel_id=selected_channel.pk)
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
        return redirect(_parsing_sources_redirect(request, scope))

    ctx = {
        'sources': sources,
        'keyword_obj': kw,
        'selected_channel': selected_channel,
        'selected_source_ids': selected_source_ids,
        'kw_source_attachment_count': len(selected_source_ids),
    }
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/keyword_edit.html', ctx)


@login_required
def keyword_delete(request, pk):
    scope = _get_parse_scope(request)
    kw = get_object_or_404(_parse_keywords_qs(request.user, scope), pk=pk)
    if request.method == 'POST':
        ch = kw.channel
        kw.delete()
        messages.success(request, 'Ключевое слово удалено.')
        if ch:
            try:
                _ensure_auto_parse_task(ch.owner, ch)
            except Exception:
                pass
        nxt = (request.POST.get('next') or '').strip()
        if nxt.startswith('/') and not nxt.startswith('//'):
            return redirect(nxt)
    return redirect(_parsing_sources_redirect(request, scope))


@login_required
def parsed_items(request):
    # Раздел "Найденные материалы" больше не используем — всё смотрим в ленте.
    scope = _get_parse_scope(request)
    q = {'kind': 'parsing'}
    if scope.get('chgroup_param') and scope.get('chgroup_param') != 'all':
        q['chgroup'] = scope.get('chgroup_param')
    return redirect(reverse('core:feed') + '?' + urlencode(q))


@login_required
def parsed_items_clear(request):
    """
    Удалить найденные материалы (ParsedItem) в текущем scope (группа/все).
    Нужно для повторного парсинга "с нуля".
    """
    scope = _get_parse_scope(request)
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _can_delete_parsed_items_as_owner_or_staff(request.user):
        messages.error(
            request,
            'Массовую очистку материалов парсинга могут выполнять только владелец аккаунта или администратор сайта.',
        )
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    items = _parsed_items_base_qs(request.user)
    if scope.get('channel_ids') is not None:
        items = items.filter(keyword__channel_id__in=scope['channel_ids'])

    deleted = 0
    try:
        deleted = items.count()
    except Exception:
        deleted = 0
    items.delete()

    # Сбросить видимые счётчики на источниках (best-effort)
    try:
        src_qs = _parse_sources_qs(request.user, scope)
        if scope.get('channel_ids') is not None:
            src_qs = src_qs.filter(channel_id__in=scope['channel_ids'])
        src_qs.update(last_parse_new_items=0, last_parse_keywords_matched=0)
    except Exception:
        pass

    messages.success(request, f'Очищено материалов: {deleted}. Следующий запуск парсинга создаст их заново.')
    return redirect(_parse_url('parsing:sources', scope))


@login_required
def item_skip(request, pk):
    """Отметить найденный материал как пропущенный/игнорируемый."""
    item = get_object_or_404(_parsed_items_base_qs(request.user), pk=pk)
    scope = _get_parse_scope(request)
    if request.method != 'POST':
        return redirect(_parse_url('parsing:items', scope))
    prev_status = item.status
    item.status = ParsedItem.STATUS_IGNORED
    item.save(update_fields=['status'])
    if prev_status != ParsedItem.STATUS_IGNORED:
        ParseKeyword.objects.filter(pk=item.keyword_id).update(stats_skipped=F('stats_skipped') + 1)
    wants_json = request.POST.get('ajax') == '1' or request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if wants_json:
        from core.views import compute_feed_quick_link_counts

        fc = (request.POST.get('feed_channel') or '').strip()
        fg = (request.POST.get('feed_chgroup') or '').strip()
        cid_scope = int(fc) if fc.isdigit() else None
        cg_scope = int(fg) if fg.isdigit() else None
        return JsonResponse(
            {
                'ok': True,
                'id': item.pk,
                'feed_counts': compute_feed_quick_link_counts(
                    request.user,
                    channel_id=cid_scope,
                    chgroup_id=cg_scope,
                ),
            }
        )
    messages.info(request, 'Материал помечен как пропущенный.')
    return redirect(_parse_url('parsing:items', scope))


@login_required
def item_delete(request, pk):
    """Удалить найденный материал (чтобы при следующем запуске мог появиться заново)."""
    item = get_object_or_404(_parsed_items_base_qs(request.user), pk=pk)
    scope = _get_parse_scope(request)
    if request.method != 'POST':
        return HttpResponse(status=405)
    if not _can_delete_parsed_items_as_owner_or_staff(request.user):
        messages.error(request, 'Удаление материалов парсинга доступно только владельцу или администратору сайта.')
        return redirect(_parse_url('parsing:items', scope))
    item.delete()
    messages.success(request, 'Материал удалён. При следующем парсинге может появиться заново.')
    return redirect(_parse_url('parsing:items', scope))


def _channel_ids_for_new_post_from_parsed_item(item, user):
    """Каналы черновика по логике «В пост» (группа ключевика или все доступные)."""
    kw_channel = item.keyword.channel
    if kw_channel:
        try:
            g = getattr(kw_channel, "channel_group", None)
            if g:
                from channels.models import Channel

                group_ids = list(
                    Channel.objects.filter(owner_id=kw_channel.owner_id, channel_group=g, is_active=True)
                    .values_list("pk", flat=True)
                )
                if group_ids:
                    return group_ids
            return [kw_channel.pk]
        except Exception:
            return [kw_channel.pk]
    return list(_parsing_channels_qs(user).values_list('pk', flat=True))


@login_required
def item_to_post(request, pk):
    """Создать черновик поста из найденного материала (или AI версии) и перейти в редактор поста."""
    item = get_object_or_404(
        _parsed_items_base_qs(request.user).select_related('keyword', 'keyword__channel'),
        pk=pk,
    )
    from content.models import Post
    from content.tasks import import_parsed_item_media_into_post

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
        text_html='',
        status=Post.STATUS_DRAFT,
        source_parsed_item=item,
        source_parse_keyword=item.keyword,
    )

    ch_ids = _channel_ids_for_new_post_from_parsed_item(item, request.user)
    if ch_ids:
        post.channels.set(ch_ids)

    _imp, media_warnings = import_parsed_item_media_into_post(post.pk, item)
    for w in media_warnings[:12]:
        messages.warning(request, w)

    item.status = ParsedItem.STATUS_USED
    item.save(update_fields=['status'])

    messages.success(request, 'Пост создан из материала. Отредактируйте и запланируйте публикацию.')
    return redirect('content:edit', pk=post.pk)


@login_required
@require_POST
def item_ai_to_post(request, pk):
    """DeepSeek: текст в выбранном тоне + черновик поста с медиа из парсинга → редактор."""
    import re

    from django.conf import settings

    from content.models import Post
    from content.tasks import import_parsed_item_media_into_post

    item = get_object_or_404(
        _parsed_items_base_qs(request.user).select_related('keyword', 'keyword__channel'),
        pk=pk,
    )
    from core.models import get_global_api_keys

    keys = get_global_api_keys()
    api_key = keys.get_deepseek_api_key()
    if not api_key or not keys.ai_rewrite_enabled:
        messages.error(
            request,
            'Включите «AI рерайт» и сохраните ключ DeepSeek в разделе «Ключи API».',
        )
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    from .deepseek_snippet import rewrite_for_feed_post
    from .feed_ai_moods import (
        ai_tone_label_for_owner,
        mood_instructions_map,
        normalize_ai_tone_for_owner,
        workspace_owner_for_parsed_item,
    )

    ws_owner = workspace_owner_for_parsed_item(item)
    tone = normalize_ai_tone_for_owner(request.POST.get('tone'), ws_owner)
    tone_rule = mood_instructions_map(ws_owner).get(tone)
    with_headline = request.POST.get('ai_with_headline') == 'on'
    embed_source = request.POST.get('ai_embed_source') == 'on'
    try:
        plain, ht = rewrite_for_feed_post(
            original_text=item.text or '',
            source_url=item.original_url or '',
            api_key=api_key,
            model_name=getattr(settings, 'DEEPSEEK_MODEL', 'deepseek-chat'),
            tone=tone,
            tone_rule=tone_rule,
            with_headline=with_headline,
            embed_source_link=embed_source,
        )
    except Exception as exc:
        messages.error(request, f'Не удалось сгенерировать текст: {exc}')
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    if not (plain or '').strip() and not (ht or '').strip():
        messages.error(request, 'AI вернул пустой текст.')
        return redirect(request.META.get('HTTP_REFERER') or reverse('core:feed'))

    text_plain = (plain or '').strip() or re.sub(r'<[^>]+>', ' ', ht or '').strip()
    text_html = (ht or '').strip()

    kw_channel = item.keyword.channel
    if request.user.role in ('manager', 'assistant_admin'):
        post_author = kw_channel.owner if kw_channel else request.user
    else:
        post_author = request.user

    post = Post.objects.create(
        author=post_author,
        text=text_plain,
        text_html=text_html,
        status=Post.STATUS_DRAFT,
        source_parsed_item=item,
        source_parse_keyword=item.keyword,
    )
    ch_ids = _channel_ids_for_new_post_from_parsed_item(item, request.user)
    if ch_ids:
        post.channels.set(ch_ids)

    _imp, media_warnings = import_parsed_item_media_into_post(post.pk, item)
    for w in media_warnings[:12]:
        messages.warning(request, w)

    try:
        item.ai_rewrite = (plain or '')[:5000]
        item.status = ParsedItem.STATUS_USED
        item.save(update_fields=['ai_rewrite', 'status'])
    except Exception:
        pass

    messages.success(
        request,
        f'Черновик создан (тон: «{ai_tone_label_for_owner(tone, ws_owner)}»), медиа из материала прикреплены при наличии файлов.',
    )
    return redirect('content:edit', pk=post.pk)


@login_required
@require_POST
def feed_ai_moods_save(request):
    """Сохранение списка интонаций AI для ленты (владелец workspace / менеджер с доступом)."""
    import json

    from django.contrib.auth import get_user_model

    from .feed_ai_moods import can_manage_feed_ai_moods, validate_moods_payload

    User = get_user_model()
    nxt = (request.POST.get('next') or '').strip() or reverse('core:feed')
    raw_owner = (request.POST.get('owner_id') or '').strip()
    if not raw_owner.isdigit():
        messages.error(request, 'Не указан владелец настроек.')
        return redirect(nxt)
    owner = get_object_or_404(User, pk=int(raw_owner))
    if not can_manage_feed_ai_moods(request.user, owner):
        messages.error(request, 'Нет прав на изменение этих настроек.')
        return redirect(nxt)
    raw_json = request.POST.get('moods_json', '')
    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError:
        messages.error(request, 'Некорректный формат данных.')
        return redirect(nxt)
    normalized, err = validate_moods_payload(data)
    if err:
        messages.error(request, err)
        return redirect(nxt)
    owner.feed_ai_moods = normalized
    owner.save(update_fields=['feed_ai_moods'])
    messages.success(request, 'Кнопки интонации для AI обновлены.')
    return redirect(nxt)

@login_required
def parse_tasks_list(request):
    """Список задач парсинга пользователя."""
    scope = _get_parse_scope(request)
    tasks = _parse_tasks_qs(request.user).prefetch_related('sources', 'keywords')
    if scope.get('channel_ids') is not None:
        tasks = tasks.filter(sources__channel_id__in=scope['channel_ids']).distinct()
    ctx = {'tasks': tasks}
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/tasks.html', ctx)


@login_required
def parse_task_create(request):
    """Создание новой задачи парсинга."""
    scope = _get_parse_scope(request)
    channels_in_scope = _channels_in_scope(request.user, scope)
    allowed_ch_ids = set(channels_in_scope.values_list('pk', flat=True))
    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        source_ids = request.POST.getlist('sources')
        keyword_ids = request.POST.getlist('keywords')
        schedule = request.POST.get('schedule_cron', '0 */6 * * *').strip()
        target_cid = (request.POST.get('channel_id') or '').strip()

        if not name:
            messages.error(request, 'Введите название задачи.')
            return redirect(_parse_url('parsing:parse_tasks', scope))
        if not allowed_ch_ids:
            messages.error(request, 'Нет каналов в текущем фильтре.')
            return redirect(_parse_url('parsing:parse_tasks', scope))
        if len(allowed_ch_ids) == 1:
            target_cid = str(next(iter(allowed_ch_ids)))
        if not target_cid.isdigit() or int(target_cid) not in allowed_ch_ids:
            messages.error(request, 'Выберите канал-владелец задачи (для учётной записи владельца).')
            return redirect(_parse_url('parsing:parse_task_create', scope))

        from channels.models import Channel

        anchor = Channel.objects.get(pk=int(target_cid))
        data_owner = _parsing_data_owner(request.user, anchor)
        task = ParseTask.objects.create(
            owner=data_owner,
            name=name,
            schedule_cron=schedule,
        )
        allowed_s = set(_parse_sources_qs(request.user, scope).values_list('pk', flat=True))
        allowed_k = set(_parse_keywords_qs(request.user, scope).values_list('pk', flat=True))
        if source_ids:
            safe_s = [int(x) for x in source_ids if str(x).isdigit() and int(x) in allowed_s]
            if safe_s:
                task.sources.set(safe_s)
        if keyword_ids:
            safe_k = [int(x) for x in keyword_ids if str(x).isdigit() and int(x) in allowed_k]
            if safe_k:
                task.keywords.set(safe_k)
        messages.success(request, f'Задача «{name}» создана.')
        return redirect(_parse_url('parsing:parse_tasks', scope))

    sources = _parse_sources_qs(request.user, scope).filter(is_active=True)
    keywords_qs = _parse_keywords_qs(request.user, scope).filter(is_active=True)
    ctx = {'sources': sources, 'keywords': keywords_qs}
    ctx.update(_parsing_template_extra(request, scope))
    return render(request, 'parsing/parse_task_create.html', ctx)


@login_required
def parse_task_run(request, pk):
    """Ручной запуск задачи парсинга."""
    scope = _get_parse_scope(request)
    task = get_object_or_404(_parse_tasks_qs(request.user), pk=pk)
    if request.method == 'POST':
        from .tasks import execute_parse_task

        execute_parse_task.delay(task.pk)
        messages.success(request, f'Задача «{task.name}» запущена.')
    return redirect(_parse_url('parsing:parse_tasks', scope))


@login_required
def parse_task_delete(request, pk):
    """Удаление задачи парсинга."""
    scope = _get_parse_scope(request)
    task = get_object_or_404(_parse_tasks_qs(request.user), pk=pk)
    if request.method == 'POST':
        task.delete()
        messages.success(request, 'Задача удалена.')
    return redirect(_parse_url('parsing:parse_tasks', scope))


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


def _keyword_harvest_jobs_qs(user):
    from django.db.models import Q
    from managers.models import TeamMember

    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        gids = set(
            TeamMember.objects.filter(member=user, is_active=True)
            .filter(Q(can_publish=True) | Q(can_moderate=True))
            .values_list('channels__channel_group_id', flat=True)
        )
        gids.discard(None)
        return KeywordHarvestJob.objects.filter(channel_group_id__in=gids)
    return KeywordHarvestJob.objects.filter(channel_group__owner=user)


def _harvest_target_channels_select_qs(user):
    from channels.models import Channel
    from django.db.models import Q
    from managers.models import TeamMember

    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        cids = list(
            TeamMember.objects.filter(member=user, is_active=True)
            .filter(Q(can_publish=True) | Q(can_moderate=True))
            .values_list('channels__pk', flat=True)
        )
        qs = Channel.objects.filter(pk__in=cids, is_active=True, channel_group_id__isnull=False)
    else:
        qs = Channel.objects.filter(owner=user, is_active=True, channel_group_id__isnull=False)
    return qs.exclude(platform__in=(Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM)).select_related(
        'channel_group'
    ).order_by('channel_group__name', 'platform', 'name')


def _harvest_allowed_channel_groups(user):
    from channels.models import ChannelGroup

    gids = _harvest_target_channels_select_qs(user).values_list('channel_group_id', flat=True).distinct()
    return ChannelGroup.objects.filter(pk__in=gids).order_by('name')


def _keyword_harvest_target_channel_ids(job: KeywordHarvestJob) -> list[int]:
    """Каналы, куда попадут ключевики при применении задачи (как в apply_harvest_keywords)."""
    from channels.models import Channel

    group = job.channel_group
    if job.target_mode == KeywordHarvestJob.TARGET_GROUP_ONE and job.target_channel_id:
        return [int(job.target_channel_id)]
    return list(
        Channel.objects.filter(channel_group=group, is_active=True)
        .exclude(platform__in=(Channel.PLATFORM_MAX, Channel.PLATFORM_INSTAGRAM))
        .values_list('pk', flat=True)
    )


def _keyword_harvest_visible_suggestions(job: KeywordHarvestJob) -> list[dict]:
    """
    Кандидаты от AI (phrase, repeat_score, comment) без фраз, уже заведённых на целевых каналах.
    """
    from .harvest_services import normalize_suggestion_list_for_ui
    from .models import ParseKeyword

    raw = job.suggested_keywords or []
    rows = normalize_suggestion_list_for_ui(raw) if raw else []
    if not rows:
        return []
    ch_ids = _keyword_harvest_target_channel_ids(job)
    existing_lower: set[str] = set()
    if ch_ids:
        existing_lower = {
            (k or '').strip().lower()
            for k in ParseKeyword.objects.filter(channel_id__in=ch_ids).values_list('keyword', flat=True)
            if (k or '').strip()
        }
    out: list[dict] = []
    for row in rows:
        phrase = (row.get('phrase') or '').strip()
        if not phrase or phrase.lower() in existing_lower:
            continue
        out.append(row)
    out.sort(key=lambda r: (-(r.get('repeat_score') or 0), (r.get('phrase') or '').lower()))
    return out


@login_required
def keyword_harvest_list(request):
    jobs = _keyword_harvest_jobs_qs(request.user).select_related('channel_group', 'created_by')[:80]
    return render(request, 'parsing/keyword_harvest_list.html', {'jobs': jobs})


@login_required
def keyword_harvest_create(request):
    from channels.models import Channel, ChannelGroup

    groups = list(_harvest_allowed_channel_groups(request.user))
    channels = list(_harvest_target_channels_select_qs(request.user))
    if not groups:
        messages.error(
            request,
            'Нет групп каналов с подходящими каналами (нужна хотя бы одна группа с TG/VK, не MAX/Instagram).',
        )
        return redirect('parsing:sources')

    if request.method == 'POST':
        from .harvest_services import parse_example_channels_from_post

        gid = (request.POST.get('channel_group') or '').strip()
        example_raw = (request.POST.get('example_channels') or request.POST.get('example_channel') or '').strip()
        example_list = parse_example_channels_from_post(example_raw)
        region = (request.POST.get('region_prompt') or '').strip()
        try:
            max_posts = int((request.POST.get('max_posts') or '20').strip() or 20)
        except ValueError:
            max_posts = 20
        max_posts = max(5, min(max_posts, 65535))
        target_ch_pk = (request.POST.get('target_channel') or '').strip()

        if not gid.isdigit():
            messages.error(request, 'Выберите группу каналов.')
            return redirect('parsing:keyword_harvest_create')
        group = ChannelGroup.objects.filter(pk=int(gid)).first()
        allowed_gids = {g.pk for g in groups}
        if not group or group.pk not in allowed_gids:
            messages.error(request, 'Группа недоступна.')
            return redirect('parsing:keyword_harvest_create')
        if not example_list:
            messages.error(
                request,
                'Укажите хотя бы один канал-пример в Telegram (@username или ссылка t.me/…), по одному в строке или через запятую.',
            )
            return redirect('parsing:keyword_harvest_create')
        if not region:
            messages.error(request, 'Опишите ваш район и контекст для AI.')
            return redirect('parsing:keyword_harvest_create')

        owner_id = group.owner_id
        if not _telethon_session_exists_for_user(owner_id):
            messages.error(
                request,
                'Для чтения постов из Telegram нужна авторизованная сессия Telethon у владельца аккаунта '
                f'(user_{owner_id}.session или user_default). Подключите Telegram в разделе парсинга.',
            )
            return redirect('parsing:keyword_harvest_create')

        target_mode = KeywordHarvestJob.TARGET_GROUP_ALL
        target_channel = None
        if target_ch_pk.isdigit():
            ch = Channel.objects.filter(pk=int(target_ch_pk)).select_related('channel_group').first()
            if (
                ch
                and ch.channel_group_id == group.id
                and ch.pk in {c.pk for c in channels}
            ):
                target_mode = KeywordHarvestJob.TARGET_GROUP_ONE
                target_channel = ch

        first_example = (example_list[0] or '')[:255]
        job = KeywordHarvestJob.objects.create(
            created_by=request.user,
            channel_group=group,
            target_mode=target_mode,
            target_channel=target_channel,
            example_channel=first_example,
            example_channels=example_list,
            region_prompt=region[:8000],
            max_posts=max_posts,
            status=KeywordHarvestJob.STATUS_PENDING,
        )
        from .tasks import run_keyword_harvest_job

        run_keyword_harvest_job.delay(job.pk)
        messages.success(
            request,
            f'Задача №{job.pk} поставлена в очередь. Через минуту обновите страницу списка или откройте задачу.',
        )
        return redirect('parsing:keyword_harvest_detail', pk=job.pk)

    return render(
        request,
        'parsing/keyword_harvest_create.html',
        {
            'groups': groups,
            'channels': channels,
        },
    )


@login_required
def keyword_harvest_detail(request, pk):
    job = get_object_or_404(
        _keyword_harvest_jobs_qs(request.user).select_related('channel_group', 'target_channel'),
        pk=pk,
    )

    if request.method == 'POST' and request.POST.get('action') == 'apply':
        if job.status != KeywordHarvestJob.STATUS_READY:
            messages.error(request, 'Задача ещё не готова или уже обработана.')
            return redirect('parsing:keyword_harvest_detail', pk=job.pk)
        visible = _keyword_harvest_visible_suggestions(job)
        include = set(request.POST.getlist('include'))
        to_add = [kw for i, kw in enumerate(visible) if str(i) in include]
        if not to_add:
            messages.warning(request, 'Отметьте галочками хотя бы один ключевик, который нужно добавить.')
            return redirect('parsing:keyword_harvest_detail', pk=job.pk)

        from .harvest_services import apply_harvest_keywords

        try:
            created = apply_harvest_keywords(job, to_add)
        except Exception as exc:
            messages.error(request, f'Не удалось создать ключевики: {exc}')
            return redirect('parsing:keyword_harvest_detail', pk=job.pk)

        job.status = KeywordHarvestJob.STATUS_APPLIED
        job.applied_at = timezone.now()
        job.save(update_fields=['status', 'applied_at', 'updated_at'])
        messages.success(
            request,
            f'Добавлено записей ключевиков: {created}. Дубликаты и уже существующие фразы пропущены.',
        )
        return redirect('parsing:keyword_harvest_detail', pk=job.pk)

    telethon_ok = _telethon_session_exists_for_user(job.channel_group.owner_id)
    from .harvest_services import normalize_suggestion_list_for_ui

    visible_keywords: list[dict] = []
    harvest_skipped_existing_count = 0
    suggested_rows_for_display = normalize_suggestion_list_for_ui(job.suggested_keywords or [])
    if job.status == KeywordHarvestJob.STATUS_READY:
        suggested_nonempty = len(suggested_rows_for_display)
        visible_keywords = _keyword_harvest_visible_suggestions(job)
        harvest_skipped_existing_count = max(0, suggested_nonempty - len(visible_keywords))

    return render(
        request,
        'parsing/keyword_harvest_detail.html',
        {
            'job': job,
            'telethon_ok': telethon_ok,
            'visible_keywords': visible_keywords,
            'harvest_skipped_existing_count': harvest_skipped_existing_count,
            'suggested_rows_for_display': suggested_rows_for_display,
        },
    )
