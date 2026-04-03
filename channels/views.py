"""
Управление каналами и пабликами.
"""
import json
from decimal import Decimal, InvalidOperation
from typing import Optional

import requests as http_requests
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.utils import timezone
from .fixed_ad_options import get_fixed_ad_options_state, sync_fixed_ad_options
from .models import Channel, ChannelGroup, ChannelInterestingFacts, ChannelMorningDigest, HistoryImportRun


def _team_channel_editor_user(user) -> bool:
    """Менеджер / помощник админа (не staff): доступ к каналам команды с ограниченным редактированием."""
    return getattr(user, 'role', '') in ('manager', 'assistant_admin') and not (
        user.is_staff or user.is_superuser
    )


def _ad_slot_selected_hours_from_json(schedule) -> list[set[int]]:
    """Из ad_slot_schedule_json — множества выбранных часов (0–23) по дням пн=0 … вс=6."""
    selected: list[set[int]] = [set() for _ in range(7)]
    if not isinstance(schedule, list):
        return selected
    for block in schedule:
        if not isinstance(block, dict):
            continue
        wd = block.get('weekday')
        try:
            wd = int(wd)
        except (TypeError, ValueError):
            continue
        if wd < 0 or wd > 6:
            continue
        for hm in block.get('times') or []:
            if not isinstance(hm, str):
                continue
            part = hm.strip().split(':')[0]
            if not part.isdigit():
                continue
            h = int(part)
            if 0 <= h <= 23:
                selected[wd].add(h)
    return selected


def _ad_slot_schedule_from_post(post) -> list:
    """Собирает JSON расписания из чекбоксов slot_w0 … slot_w6 (значения — час 0–23)."""
    blocks: list[dict] = []
    for wd in range(7):
        raw = post.getlist(f'slot_w{wd}')
        hours: list[int] = []
        for x in raw:
            try:
                h = int(x)
            except (TypeError, ValueError):
                continue
            if 0 <= h <= 23:
                hours.append(h)
        hours = sorted(set(hours))
        if hours:
            blocks.append({'weekday': wd, 'times': [f'{h:02d}:00' for h in hours]})
    return blocks


def _parse_ad_price(raw) -> Optional[Decimal]:
    """Парсит цену из POST (запятая/точка, пробелы). None — не менять поле."""
    if raw is None:
        return None
    s = str(raw).strip().replace(' ', '').replace(',', '.')
    if not s:
        return Decimal('0')
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _team_member_channel_ids(user) -> list[int]:
    """ID каналов, назначенных пользователю в команде (активное членство)."""
    from managers.models import TeamMember

    raw = TeamMember.objects.filter(member=user, is_active=True).values_list('channels__pk', flat=True)
    return sorted({int(x) for x in raw if str(x).isdigit()})


def _channel_create_store_form(request, *, platform: str, name: str, data: dict):
    """
    Store last entered values for channel create form.
    Using PRG pattern avoids browser "resubmit form" prompts.
    """
    request.session['channel_create_initial'] = {
        'platform': platform or '',
        'name': name or '',
        # platform-specific ids / tokens (tokens kept server-side in session)
        'tg_bot_token': (data.get('tg_bot_token') or '').strip(),
        'tg_chat_id': (data.get('tg_chat_id') or '').strip(),
        'vk_access_token': (data.get('vk_access_token') or '').strip(),
        'vk_group_id': (data.get('vk_group_id') or '').strip(),
        'max_bot_token': (data.get('max_bot_token') or '').strip(),
        'max_channel_id': (data.get('max_channel_id') or '').strip(),
        'ig_access_token': (data.get('ig_access_token') or '').strip(),
        'ig_account_id': (data.get('ig_account_id') or '').strip(),
    }
    request.session.modified = True


@login_required
def channel_list(request):
    if _team_channel_editor_user(request.user):
        ids = _team_member_channel_ids(request.user)
        channels = Channel.objects.filter(pk__in=ids, is_active=True).select_related('channel_group').order_by('-created_at')
        team_channel_list = True
        all_groups = []
    else:
        channels = Channel.objects.filter(owner=request.user).select_related('channel_group').order_by('-created_at')
        team_channel_list = False
        all_groups = list(
            ChannelGroup.objects.filter(owner=request.user).prefetch_related('channels').order_by('name', 'pk')
        )
    ch_list = list(channels)
    grouped_channels = []
    ungrouped_channels = []
    if not team_channel_list and all_groups:
        in_group_ids = set()
        for g in all_groups:
            members = [c for c in ch_list if c.channel_group_id == g.pk]
            grouped_channels.append({'group': g, 'channels': members})
            in_group_ids.update(c.pk for c in members)
        ungrouped_channels = [c for c in ch_list if c.pk not in in_group_ids]
    return render(request, 'channels/list.html', {
        'channels': ch_list,
        'grouped_channels': grouped_channels,
        'ungrouped_channels': ungrouped_channels,
        'all_groups': all_groups,
        'team_channel_list': team_channel_list,
    })


@login_required
def channel_create(request):
    # Гейт: проверяем лимит каналов
    if not request.user.can_add_channel:
        messages.error(
            request,
            'Вы достигли лимита каналов. Оформите подписку или продлите текущую.'
        )
        from django.urls import reverse
        return redirect(reverse('billing:subscribe'))

    if request.method == 'POST':
        platform = request.POST.get('platform')
        name = request.POST.get('name', '').strip()
        if not name or not platform:
            messages.error(request, 'Заполните название и платформу.')
            _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
            return redirect('channels:create')

        channel = Channel(owner=request.user, name=name, platform=platform)

        if platform == Channel.PLATFORM_TELEGRAM:
            token = request.POST.get('tg_bot_token', '').strip()
            channel.tg_chat_id = request.POST.get('tg_chat_id', '').strip()
            if token:
                try:
                    channel.set_tg_token(token)
                except ValueError as e:
                    messages.error(request, f'Не удалось сохранить токен Telegram: {e}')
                    _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
                    return redirect('channels:create')
        elif platform == Channel.PLATFORM_VK:
            token = request.POST.get('vk_access_token', '').strip()
            channel.vk_group_id = request.POST.get('vk_group_id', '').strip()
            if token:
                try:
                    channel.set_vk_token(token)
                except ValueError as e:
                    messages.error(request, f'Не удалось сохранить токен VK: {e}')
                    _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
                    return redirect('channels:create')
        elif platform == Channel.PLATFORM_MAX:
            token = request.POST.get('max_bot_token', '').strip()
            channel.max_channel_id = request.POST.get('max_channel_id', '').strip()
            if token:
                try:
                    channel.set_max_token(token)
                except ValueError as e:
                    messages.error(request, f'Не удалось сохранить токен MAX: {e}')
                    _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
                    return redirect('channels:create')
        elif platform == Channel.PLATFORM_INSTAGRAM:
            token = request.POST.get('ig_access_token', '').strip()
            channel.ig_account_id = request.POST.get('ig_account_id', '').strip()
            if token:
                try:
                    channel.set_ig_token(token)
                except ValueError as e:
                    messages.error(request, f'Не удалось сохранить токен Instagram: {e}')
                    _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
                    return redirect('channels:create')

        cg_raw = (request.POST.get('channel_group_id') or '').strip()
        if cg_raw.isdigit():
            cg = ChannelGroup.objects.filter(pk=int(cg_raw), owner=request.user).first()
            channel.channel_group = cg
        else:
            channel.channel_group = None

        try:
            channel.save()
        except IntegrityError as e:
            messages.error(request, f'Не удалось сохранить канал: {e}')
            _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
            return redirect('channels:create')
        except Exception as e:
            messages.error(request, f'Ошибка при сохранении канала: {e}')
            _channel_create_store_form(request, platform=platform, name=name, data=request.POST)
            return redirect('channels:create')

        messages.success(request, f'Канал "{name}" добавлен.')
        if request.POST.get('create_suggestion_bot') == 'on':
            from django.urls import reverse
            from urllib.parse import urlencode

            platform_map = {
                Channel.PLATFORM_TELEGRAM: 'telegram',
                Channel.PLATFORM_VK: 'vk',
                Channel.PLATFORM_MAX: 'max',
            }
            qd = {}
            if getattr(channel, 'channel_group_id', None):
                qd['chgroup_id'] = channel.channel_group_id
            p = platform_map.get(channel.platform)
            if p:
                qd['platform'] = p
            suffix = ('?' + urlencode(qd)) if qd else ''
            return redirect(reverse('bots:create') + suffix)
        return redirect('channels:list')

    groups = ChannelGroup.objects.filter(owner=request.user).order_by('name', 'pk')
    return render(request, 'channels/create.html', {
        'platforms': Channel.PLATFORM_CHOICES,
        'channel_groups': groups,
        'initial': request.session.pop('channel_create_initial', {}),
    })


@login_required
def channel_detail(request, pk):
    if _team_channel_editor_user(request.user):
        allowed_ids = _team_member_channel_ids(request.user)
        channel = get_object_or_404(Channel.objects.filter(pk__in=allowed_ids).select_related('channel_group'), pk=pk)
        channel_team_access = True
    else:
        channel = get_object_or_404(Channel.objects.select_related('channel_group'), pk=pk, owner=request.user)
        channel_team_access = False
    from content.models import Post
    from stats.models import ChannelStat
    recent_posts = Post.objects.filter(channels=channel).order_by('-created_at')[:10]
    stats = ChannelStat.objects.filter(channel=channel).order_by('-date')[:30]
    total_views = sum((s.views or 0) for s in stats)
    return render(request, 'channels/detail.html', {
        'channel': channel,
        'recent_posts': recent_posts,
        'stats': stats,
        'total_views': total_views,
        'channel_team_access': channel_team_access,
    })


@login_required
def channel_edit(request, pk):
    footer_only = False

    # Менеджер / помощник: только подписи (tg/max/vk), по каналам из команды.
    if _team_channel_editor_user(request.user):
        allowed_ids = _team_member_channel_ids(request.user)
        channel = get_object_or_404(Channel.objects.filter(pk__in=allowed_ids).select_related('channel_group'), pk=pk)
        footer_only = True
    else:
        channel = get_object_or_404(Channel.objects.select_related('channel_group'), pk=pk, owner=request.user)

    if request.method == 'POST':
        if footer_only:
            # Менеджер может менять только подписи.
            before = {'tg_footer': channel.tg_footer, 'max_footer': channel.max_footer, 'vk_footer': channel.vk_footer}
            channel.tg_footer = request.POST.get('tg_footer', '').strip()
            channel.max_footer = request.POST.get('max_footer', '').strip()
            channel.vk_footer = request.POST.get('vk_footer', '').strip()
            channel.save(update_fields=['tg_footer', 'max_footer', 'vk_footer'])
            try:
                from bots.models import AuditLog
                changed = {}
                after = {'tg_footer': channel.tg_footer, 'max_footer': channel.max_footer, 'vk_footer': channel.vk_footer}
                for k in after:
                    if before.get(k) != after.get(k):
                        changed[k] = {'before': before.get(k), 'after': after.get(k)}
                if changed:
                    AuditLog.objects.create(
                        actor=request.user,
                        owner=channel.owner,
                        action='channel.footer_update',
                        object_type='Channel',
                        object_id=str(channel.pk),
                        data={'changed': changed},
                    )
            except Exception:
                pass
            messages.success(request, 'Подпись обновлена.')
            return redirect('channels:detail', pk=pk)

        # Полное редактирование для владельца
        channel.name = request.POST.get('name', channel.name).strip()
        channel.description = request.POST.get('description', '').strip()
        channel.ad_enabled = request.POST.get('ad_enabled') == 'on'
        parsed_price = _parse_ad_price(request.POST.get('ad_price'))
        if parsed_price is not None:
            channel.ad_price = parsed_price
        channel.ord_pad_external_id = (request.POST.get('ord_pad_external_id') or '').strip()

        channel.ad_slot_schedule_json = _ad_slot_schedule_from_post(request.POST)

        h_raw = (request.POST.get('ad_slot_horizon_days') or '').strip()
        if h_raw.isdigit():
            channel.ad_slot_horizon_days = max(1, min(366, int(h_raw)))
        lt_raw = (request.POST.get('ad_post_lifetime_days') or '').strip()
        if lt_raw.isdigit():
            channel.ad_post_lifetime_days = max(1, min(365, int(lt_raw)))

        if channel.platform == Channel.PLATFORM_TELEGRAM:
            new_token = request.POST.get('tg_bot_token', '').strip()
            channel.tg_chat_id = request.POST.get('tg_chat_id', channel.tg_chat_id).strip()
            if new_token:
                channel.set_tg_token(new_token)
        elif channel.platform == Channel.PLATFORM_VK:
            new_token = request.POST.get('vk_access_token', '').strip()
            channel.vk_group_id = request.POST.get('vk_group_id', channel.vk_group_id).strip()
            if new_token:
                channel.set_vk_token(new_token)
        elif channel.platform == Channel.PLATFORM_MAX:
            new_token = request.POST.get('max_bot_token', '').strip()
            channel.max_channel_id = request.POST.get('max_channel_id', channel.max_channel_id).strip()
            if new_token:
                channel.set_max_token(new_token)
        elif channel.platform == Channel.PLATFORM_INSTAGRAM:
            new_token = request.POST.get('ig_access_token', '').strip()
            channel.ig_account_id = request.POST.get('ig_account_id', channel.ig_account_id).strip()
            if new_token:
                channel.set_ig_token(new_token)

        channel.tg_footer = request.POST.get('tg_footer', '').strip()
        channel.max_footer = request.POST.get('max_footer', '').strip()
        channel.vk_footer = request.POST.get('vk_footer', '').strip()

        # Контакты админа (для предложки)
        channel.admin_contact_site = (request.POST.get('admin_contact_site') or '').strip()
        channel.admin_contact_tg = (request.POST.get('admin_contact_tg') or '').strip()
        channel.admin_contact_vk = (request.POST.get('admin_contact_vk') or '').strip()
        channel.admin_contact_max_phone = (request.POST.get('admin_contact_max_phone') or '').strip()

        cg_raw = (request.POST.get('channel_group_id') or '').strip()
        if cg_raw.isdigit():
            cg = ChannelGroup.objects.filter(pk=int(cg_raw), owner=request.user).first()
            channel.channel_group = cg
        else:
            channel.channel_group = None

        top_on = request.POST.get('ad_fixed_top_1h') == 'on'
        pin_on = request.POST.get('ad_fixed_pin_24h') == 'on'
        top_p = _parse_ad_price(request.POST.get('ad_fixed_top_1h_price'))
        pin_p = _parse_ad_price(request.POST.get('ad_fixed_pin_24h_price'))
        if top_p is None:
            top_p = Decimal('0')
        if pin_p is None:
            pin_p = Decimal('0')

        with transaction.atomic():
            channel.save()
            sync_fixed_ad_options(
                channel,
                top_enabled=top_on,
                top_price=top_p,
                pin_enabled=pin_on,
                pin_price=pin_p,
            )
        messages.success(request, 'Канал обновлён.')
        return redirect('channels:detail', pk=pk)

    groups = ChannelGroup.objects.filter(owner=request.user).order_by('name', 'pk')
    sel = _ad_slot_selected_hours_from_json(channel.ad_slot_schedule_json)
    day_labels = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']
    ad_slot_days = [
        {'weekday': i, 'label': day_labels[i], 'hours': sel[i]} for i in range(7)
    ]
    ad_slot_hours = [{'v': h, 'lb': f'{h:02d}'} for h in range(24)]
    ad_fixed_addons = get_fixed_ad_options_state(channel) if not footer_only else None
    return render(
        request,
        'channels/edit.html',
        {
            'channel': channel,
            'footer_only': footer_only,
            'channel_groups': groups,
            'ad_slot_days': ad_slot_days,
            'ad_slot_hours': ad_slot_hours,
            'server_timezone': getattr(settings, 'TIME_ZONE', 'UTC'),
            'ad_fixed_addons': ad_fixed_addons,
        },
    )


@login_required
@require_POST
def channel_test(request, pk):
    """
    AJAX: Проверяет подключение канала, делая тестовый вызов к API платформы.
    Возвращает JSON: {ok: true/false, message: '...'}
    """
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    action = (request.POST.get('action') or '').strip()

    try:
        if channel.platform == Channel.PLATFORM_TELEGRAM:
            token = channel.get_tg_token()
            if not token:
                return JsonResponse({'ok': False, 'message': 'Токен бота не задан'})
            resp = http_requests.get(
                f'https://api.telegram.org/bot{token}/getMe',
                timeout=10
            )
            data = resp.json()
            if data.get('ok'):
                bot_name = data['result'].get('username', '')
                return JsonResponse({'ok': True, 'message': f'Бот @{bot_name} подключён успешно'})
            else:
                return JsonResponse({'ok': False, 'message': data.get('description', 'Неверный токен')})

        elif channel.platform == Channel.PLATFORM_MAX:
            token = channel.get_max_token()
            if not token:
                return JsonResponse({'ok': False, 'message': 'Токен бота не задан'})

            # Optional: send a test message to validate recipient/chat_id.
            if action == 'send_test':
                if not channel.max_channel_id:
                    return JsonResponse({'ok': False, 'message': 'MAX Channel ID (chat_id) не задан'})
                chat_id_raw = str(channel.max_channel_id).strip()
                chat_id: object = chat_id_raw
                try:
                    # MAX API ожидает числовой chat_id; если в базе строка — приведём.
                    chat_id = int(chat_id_raw)
                except Exception:
                    chat_id = chat_id_raw
                resp = http_requests.post(
                    'https://platform-api.max.ru/messages',
                    params={'chat_id': chat_id},
                    headers={'Authorization': token},
                    json={'chat_id': chat_id, 'text': '✅ Тестовое сообщение от ProChannels'},
                    timeout=15,
                )
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
                if isinstance(data, dict):
                    # Typical error format: {"code": "...", "message": "..."}
                    # В успешном ответе MAX тоже есть поле "message" (объект сообщения),
                    # поэтому признаком ошибки считаем только http>=400 или наличие "code".
                    if resp.status_code >= 400 or data.get('code'):
                        return JsonResponse({
                            'ok': False,
                            'message': f'MAX sendMessage error (chat_id={chat_id_raw}, http={resp.status_code}): {data}',
                        })
                    # Success formats differ; treat "no code/message + 2xx" as success
                    if 200 <= resp.status_code < 300:
                        return JsonResponse({
                            'ok': True,
                            'message': f'Отправлено в MAX (chat_id={chat_id_raw}). Ответ: {data}',
                        })
                return JsonResponse({
                    'ok': False,
                    'message': f'MAX sendMessage error (chat_id={chat_id_raw}, http={resp.status_code}): {data}',
                })

            resp = http_requests.get(
                'https://platform-api.max.ru/me',
                headers={'Authorization': token},
                timeout=10
            )
            data = resp.json()
            # MAX API возвращает объект бота напрямую (не обёрнутый в ok/result)
            if 'user_id' in data or 'name' in data:
                bot_name = data.get('name', data.get('username', 'бот'))
                return JsonResponse({'ok': True, 'message': f'Бот «{bot_name}» подключён успешно'})
            else:
                return JsonResponse({'ok': False, 'message': f'Ошибка: {data}'})

        elif channel.platform == Channel.PLATFORM_VK:
            token = channel.get_vk_token()
            if not token:
                return JsonResponse({'ok': False, 'message': 'Токен не задан'})
            resp = http_requests.get(
                'https://api.vk.com/method/groups.getById',
                params={
                    'access_token': token,
                    'group_id': channel.vk_group_id,
                    'v': '5.131',
                },
                timeout=10
            )
            data = resp.json()
            if 'response' in data:
                group = data['response'][0]
                return JsonResponse({'ok': True, 'message': f'Группа «{group["name"]}» подключена'})
            else:
                err = data.get('error', {}).get('error_msg', 'Ошибка токена')
                return JsonResponse({'ok': False, 'message': err})

        elif channel.platform == Channel.PLATFORM_INSTAGRAM:
            token = channel.get_ig_token()
            account_id = channel.ig_account_id
            if not token or not account_id:
                return JsonResponse({'ok': False, 'message': 'Токен или Account ID не задан'})
            resp = http_requests.get(
                f'https://graph.facebook.com/v19.0/{account_id}',
                params={'access_token': token, 'fields': 'id,username,name'},
                timeout=10
            )
            data = resp.json()
            if 'id' in data:
                username = data.get('username', data.get('name', account_id))
                return JsonResponse({'ok': True, 'message': f'Instagram @{username} подключён успешно'})
            else:
                err = data.get('error', {}).get('message', 'Неверный токен')
                return JsonResponse({'ok': False, 'message': err})

        else:
            return JsonResponse({'ok': False, 'message': 'Проверка недоступна для этой платформы'})

    except Exception as e:
        return JsonResponse({'ok': False, 'message': f'Ошибка соединения: {e}'})


@login_required
def channel_group_create(request):
    """Создание группы каналов (один паблик в нескольких соцсетях)."""
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Создавать группы может только владелец аккаунта.')
        return redirect('channels:list')

    next_url = (request.GET.get('next') or '').strip()
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        next_url = (request.POST.get('next') or next_url).strip()
        if not name:
            messages.error(request, 'Укажите название группы.')
        else:
            ChannelGroup.objects.create(owner=request.user, name=name)
            messages.success(request, f'Группа «{name}» создана.')
            if next_url.startswith('/') and not next_url.startswith('//'):
                return redirect(next_url)
            return redirect('channels:list')

    return render(request, 'channels/group_create.html', {'next': next_url})


@login_required
def channel_group_edit(request, pk):
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Редактировать группы может только владелец аккаунта.')
        return redirect('channels:list')
    group = get_object_or_404(ChannelGroup, pk=pk, owner=request.user)
    if request.method == 'POST':
        name = (request.POST.get('name') or '').strip()
        if not name:
            messages.error(request, 'Укажите название группы.')
        else:
            group.name = name
            group.save(update_fields=['name'])
            messages.success(request, f'Группа «{name}» сохранена.')
            return redirect('channels:list')
    return render(request, 'channels/group_edit.html', {'group': group})


@login_required
@require_POST
def channel_group_delete(request, pk):
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Удалять группы может только владелец аккаунта.')
        return redirect('channels:list')
    group = get_object_or_404(ChannelGroup, pk=pk, owner=request.user)
    if group.channels.exists():
        messages.error(
            request,
            'Нельзя удалить группу, пока в неё входят каналы. Сначала переназначьте или снимите группу у каждого канала.',
        )
        return redirect('channels:list')
    name = group.name
    group.delete()
    messages.success(request, f'Группа «{name}» удалена.')
    return redirect('channels:list')


@login_required
@require_POST
def channel_set_group(request, pk):
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Назначать группу может только владелец аккаунта.')
        return redirect('channels:list')
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    cg_raw = (request.POST.get('channel_group_id') or '').strip()
    if cg_raw == '' or cg_raw == '__none__':
        channel.channel_group = None
    elif cg_raw.isdigit():
        cg = ChannelGroup.objects.filter(pk=int(cg_raw), owner=request.user).first()
        channel.channel_group = cg
    channel.save(update_fields=['channel_group'])
    messages.success(request, f'Группа для «{channel.name}» обновлена.')
    return redirect('channels:list')


@login_required
def channel_delete(request, pk):
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    if request.method == 'POST':
        name = channel.name
        channel.delete()
        messages.success(request, f'Канал "{name}" удалён.')
        return redirect('channels:list')
    return render(request, 'channels/delete_confirm.html', {'channel': channel})


@login_required
def channel_import_history(request, pk: int):
    target = get_object_or_404(Channel, pk=pk, owner=request.user)
    if target.platform != Channel.PLATFORM_MAX:
        messages.error(request, 'Импорт истории доступен только для MAX-каналов.')
        return redirect('channels:detail', pk=pk)

    tg_channels = list(
        Channel.objects.filter(owner=request.user, platform=Channel.PLATFORM_TELEGRAM, is_active=True).order_by('-created_at')
    )
    recent_runs = list(
        HistoryImportRun.objects.filter(created_by=request.user, target_channel=target).select_related('source_channel')[:10]
    )
    active_run = (
        HistoryImportRun.objects.filter(
            created_by=request.user,
            target_channel=target,
            status__in=[HistoryImportRun.STATUS_PENDING, HistoryImportRun.STATUS_RUNNING],
        )
        .order_by('-created_at')
        .first()
    )
    return render(request, 'channels/import_history.html', {
        'target_channel': target,
        'tg_channels': tg_channels,
        'recent_runs': recent_runs,
        'active_import_run_id': active_run.pk if active_run else None,
    })


@login_required
@require_POST
def import_history_start(request):
    source_id = (request.POST.get('source_channel_id') or '').strip()
    target_id = (request.POST.get('target_channel_id') or '').strip()
    if not (source_id.isdigit() and target_id.isdigit()):
        return JsonResponse({'ok': False, 'message': 'Некорректные параметры.'}, status=400)

    source = get_object_or_404(Channel, pk=int(source_id), owner=request.user, platform=Channel.PLATFORM_TELEGRAM)
    target = get_object_or_404(Channel, pk=int(target_id), owner=request.user, platform=Channel.PLATFORM_MAX)

    with transaction.atomic():
        existing = HistoryImportRun.objects.select_for_update().filter(
            source_channel=source,
            target_channel=target,
            status__in=[HistoryImportRun.STATUS_PENDING, HistoryImportRun.STATUS_RUNNING],
        ).first()
        if existing:
            return JsonResponse({'ok': True, 'run_id': existing.pk, 'message': 'Импорт уже запущен.'})

        # Continue-from: если уже были попытки импорта для этой пары каналов — продолжим с максимального
        # last_tg_message_id (вдруг последний run завершился ошибкой/остановкой, но прогресс успел сохраниться).
        prev_last_id = None
        try:
            prev_runs = (
                HistoryImportRun.objects
                .filter(source_channel=source, target_channel=target)
                .order_by('-updated_at', '-created_at')[:25]
            )
            best = None
            for r in prev_runs:
                pj = getattr(r, 'progress_json', None)
                if not isinstance(pj, dict):
                    continue
                raw = pj.get('last_tg_message_id')
                if raw is None:
                    continue
                s = str(raw).strip()
                if not s.isdigit():
                    continue
                n = int(s)
                if best is None or n > best:
                    best = n
            prev_last_id = best
        except Exception:
            prev_last_id = None

        run = HistoryImportRun.objects.create(
            created_by=request.user,
            source_channel=source,
            target_channel=target,
            status=HistoryImportRun.STATUS_PENDING,
            progress_json={'sent': 0, 'errors': 0, 'last_tg_message_id': prev_last_id},
        )

    try:
        from .tasks import import_tg_history_to_max_task

        async_result = import_tg_history_to_max_task.delay(run.pk)
        j = dict(run.progress_json or {})
        log = list(j.get('journal') or [])
        log.append(
            {
                't': timezone.now().isoformat(timespec='seconds'),
                'step': 0,
                'step_total': 7,
                'msg': (
                    f'Задача записана в Redis-очередь «import_history» (id Celery: {async_result.id}). '
                    'Шаг 0 из 7: ждём, пока воркер заберёт задачу. Если так висит долго — откройте '
                    '«Настройки → Фоновые задачи» и проверьте длину очереди и список active.'
                ),
            }
        )
        j['journal'] = log[-50:]
        run.celery_task_id = async_result.id
        run.progress_json = j
        run.save(update_fields=['celery_task_id', 'progress_json'])
    except Exception as exc:
        run.status = HistoryImportRun.STATUS_ERROR
        run.error_message = f'Не удалось поставить задачу в очередь: {exc}'
        run.finished_at = timezone.now()
        run.save(update_fields=['status', 'error_message', 'finished_at'])
        return JsonResponse({'ok': False, 'message': run.error_message}, status=500)

    return JsonResponse({'ok': True, 'run_id': run.pk})


@login_required
def import_history_status(request, pk: int):
    run = get_object_or_404(
        HistoryImportRun.objects.select_related('source_channel', 'target_channel'),
        pk=pk,
        created_by=request.user,
    )
    return JsonResponse({
        'ok': True,
        'run': {
            'id': run.pk,
            'status': run.status,
            'started_at': run.started_at.isoformat() if run.started_at else None,
            'finished_at': run.finished_at.isoformat() if run.finished_at else None,
            'progress': run.progress_json or {},
            'error_message': run.error_message or '',
            'celery_task_id': run.celery_task_id or '',
            'source_channel': {'id': run.source_channel_id, 'name': run.source_channel.name},
            'target_channel': {'id': run.target_channel_id, 'name': run.target_channel.name},
            'cancel_requested': bool(run.cancel_requested),
        }
    })


def _redis_broker_queue_lengths(broker_url: str):
    """Длины списков Redis для очередей Celery (имена ключей = имена очередей)."""
    if not (broker_url or '').strip():
        return {}, 'broker_url пуст'
    try:
        import redis

        r = redis.from_url(broker_url, socket_connect_timeout=2, socket_timeout=2)
        out = {}
        for q in ('import_history', 'prio', 'celery'):
            try:
                out[q] = int(r.llen(q))
            except Exception:
                out[q] = None
        return out, None
    except Exception as exc:
        return {}, str(exc)


@login_required
def import_history_diagnostics(request):
    """
    Сводка для отладки «висит в очереди»: записи pending/running в БД и ответ Celery inspect.
    """
    from pro_channels.celery import app as celery_app

    qs = HistoryImportRun.objects.select_related('source_channel', 'target_channel')
    if not (request.user.is_staff or request.user.is_superuser):
        qs = qs.filter(created_by=request.user)

    pending = list(qs.filter(status=HistoryImportRun.STATUS_PENDING).order_by('-created_at')[:30])
    running = list(qs.filter(status=HistoryImportRun.STATUS_RUNNING).order_by('-created_at')[:30])

    def _run_short(r, *, orphan_no_task_id: bool = False):
        return {
            'id': r.pk,
            'status': r.status,
            'celery_task_id': r.celery_task_id or '',
            'orphan_no_task_id': orphan_no_task_id,
            'source': r.source_channel.name,
            'target': r.target_channel.name,
            'created_at': r.created_at.isoformat(timespec='seconds'),
            'started_at': r.started_at.isoformat(timespec='seconds') if r.started_at else None,
        }

    celery_workers = []
    celery_error = None
    active_history = []
    active_parse_tasks = []
    reserved_history = []
    registered_sample = []
    try:
        insp = celery_app.control.inspect(timeout=2.0)
        if insp is None:
            celery_error = 'inspect() вернул None — нет связи с брокером или воркерами.'
        else:
            ping = insp.ping()
            celery_workers = list((ping or {}).keys())
            reg = insp.registered() or {}
            for worker, names in reg.items():
                if not names:
                    continue
                want = [n for n in names if 'parsing.' in n or 'channels.tasks.import_tg' in n or 'content.tasks' in n]
                registered_sample.append(
                    {
                        'worker': worker,
                        'relevant_tasks': want[:40],
                        'total_registered': len(names),
                    }
                )
            for worker, tasks in (insp.active() or {}).items():
                for t in tasks or []:
                    name = t.get('name') or ''
                    if 'import_tg_history' in name:
                        active_history.append(
                            {
                                'worker': worker,
                                'task_id': t.get('id', ''),
                                'name': name,
                                'args': str(t.get('args', ''))[:180],
                            }
                        )
                    if 'execute_parse_task' in name:
                        active_parse_tasks.append(
                            {
                                'worker': worker,
                                'task_id': t.get('id', ''),
                                'name': name,
                                'args': str(t.get('args', ''))[:180],
                            }
                        )
            for worker, tasks in (insp.reserved() or {}).items():
                for t in tasks or []:
                    if not isinstance(t, dict):
                        continue
                    name = t.get('name') or ''
                    if 'import_tg_history' in name:
                        reserved_history.append(
                            {
                                'worker': worker,
                                'task_id': str(t.get('id', '')),
                                'name': name,
                                'args': str(t.get('args', ''))[:180],
                            }
                        )
    except Exception as exc:
        celery_error = str(exc)

    broker = (getattr(settings, 'CELERY_BROKER_URL', None) or '').strip()
    broker_tail = ''
    if broker:
        broker_tail = broker.split('@')[-1] if '@' in broker else broker[:120]

    redis_qlens, redis_len_err = _redis_broker_queue_lengths(broker)
    redis_prio_len = redis_qlens.get('prio') if isinstance(redis_qlens, dict) else None
    redis_celery_len = redis_qlens.get('celery') if isinstance(redis_qlens, dict) else None

    telethon_lock_by_owner = []
    try:
        from parsing.tasks import telethon_session_lock_redis_status

        seen_owners = set()
        for r in list(running) + list(pending):
            sc = getattr(r, 'source_channel', None)
            oid = getattr(sc, 'owner_id', None) if sc is not None else None
            if oid is None or oid in seen_owners:
                continue
            seen_owners.add(oid)
            telethon_lock_by_owner.append(telethon_session_lock_redis_status(int(oid)))
    except Exception as exc:
        telethon_lock_by_owner = [{'error': str(exc)}]

    pending_payload = []
    orphan_pending_ids = []
    for r in pending:
        orphan = not (r.celery_task_id or '').strip()
        if orphan:
            orphan_pending_ids.append(r.pk)
        pending_payload.append(_run_short(r, orphan_no_task_id=orphan))

    hints = [
        'Если pending не пустой, а workers пустой — контейнер celery не запущен или другой CELERY_BROKER_URL, чем у web.',
        'Команда: python manage.py requeue_pending_history_imports',
    ]
    if orphan_pending_ids:
        hints.insert(
            0,
            'Внимание: pending без celery_task_id — в брокер, скорее всего, ничего не отправлялось '
            '(старый код до сохранения id, сброс Redis и т.п.). На странице импорта нажмите «Снова в очередь Celery» '
            f'или выполните: python manage.py requeue_pending_history_imports --run-ids {" ".join(str(x) for x in orphan_pending_ids)}',
        )
    if getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False):
        hints.insert(
            0,
            'CELERY_TASK_ALWAYS_EAGER=True — задачи не попадают в Redis; воркер их никогда не увидит. Уберите из .env.',
        )
    if (
        redis_celery_len is not None
        and redis_celery_len > 0
        and not celery_error
        and celery_workers
    ):
        hints.append(
            f'В Redis в очереди «celery» сейчас ~{redis_celery_len} сообщ. Если воркер живой, но active пустой — '
            'перезапустите celery (command с -Q import_history,prio,celery,parse) или проверьте зависшие процессы.',
        )
    redis_import_len = redis_qlens.get('import_history') if isinstance(redis_qlens, dict) else None
    if redis_import_len is not None and redis_import_len > 0:
        hints.append(
            f'В очереди «import_history» ~{redis_import_len} задач(и) импорта. '
            'Если число не убывает — проверьте, что воркер запущен с -Q import_history,... и concurrency ≥ 1.',
        )
    if redis_prio_len is not None and redis_prio_len > 50:
        hints.append(
            f'В очереди «prio» ~{redis_prio_len} сообщ (публикация, тик планировщика). '
            'Если число растёт — воркер не слушает prio или все слоты заняты.',
        )
    hints.append(
        'Блокировка Telethon: по умолчанию файловый lock (flock) в media/telethon_sessions/.flocks/ '
        '(см. flock_path, flock_held_probe). Режим redis — ключ pch:telethon:sess:* (held_in_redis). '
        'Параллельный парсинг/импорт для того же владельца держит тот же lock.',
    )

    return JsonResponse(
        {
            'ok': True,
            'pending': pending_payload,
            'running': [_run_short(r) for r in running],
            'orphan_pending_ids': orphan_pending_ids,
            'telethon_lock_by_owner': telethon_lock_by_owner,
            'celery': {
                'workers': celery_workers,
                'active_history_import_tasks': active_history,
                'active_parse_tasks': active_parse_tasks,
                'reserved_history_import_tasks': reserved_history,
                'registered_task_groups': registered_sample,
                'error': celery_error,
            },
            'broker_host': broker_tail,
            'redis_queue_lengths': redis_qlens,
            'redis_queue_lengths_error': redis_len_err,
            # Совместимость со старым UI/скриптами
            'redis_celery_list_length': redis_celery_len,
            'redis_celery_list_error': redis_len_err,
            'settings': {
                'CELERY_TASK_ALWAYS_EAGER': getattr(settings, 'CELERY_TASK_ALWAYS_EAGER', False),
                'CELERY_TASK_DEFAULT_QUEUE': getattr(settings, 'CELERY_TASK_DEFAULT_QUEUE', 'celery'),
                'CELERY_TASK_ROUTES': getattr(settings, 'CELERY_TASK_ROUTES', None),
                'CELERY_WORKER_PREFETCH_MULTIPLIER': getattr(
                    settings, 'CELERY_WORKER_PREFETCH_MULTIPLIER', 1
                ),
            },
            'hints': hints
            + [
                'Парсинг по расписанию: должен работать контейнер celery-beat и в БД PeriodicTask (setup_periodic_tasks).',
                'После смены docker-compose перезапустите: docker compose up -d --build --force-recreate web celery celery-beat',
            ],
        }
    )


@login_required
@require_POST
def import_history_requeue(request, pk: int):
    """
    Повторно отправить в Celery pending-импорт, у которого нет celery_task_id (или force для staff).
    """
    run = get_object_or_404(HistoryImportRun, pk=pk, created_by=request.user)
    if run.status != HistoryImportRun.STATUS_PENDING:
        return JsonResponse(
            {'ok': False, 'message': 'Повторная постановка возможна только для статуса «В очереди».'},
            status=400,
        )
    force = request.POST.get('force') == '1' and (request.user.is_staff or request.user.is_superuser)
    if (run.celery_task_id or '').strip() and not force:
        return JsonResponse(
            {
                'ok': False,
                'message': 'У записи уже есть id задачи Celery. Если задача пропала из брокера — staff может отправить снова с force=1.',
            },
            status=400,
        )
    from .tasks import import_tg_history_to_max_task

    try:
        ar = import_tg_history_to_max_task.delay(run.pk)
        j = dict(run.progress_json or {})
        log = list(j.get('journal') or [])
        log.append(
            {
                't': timezone.now().isoformat(timespec='seconds'),
                'msg': f'Повторная отправка в Celery (id: {ar.id}).',
            }
        )
        j['journal'] = log[-50:]
        run.celery_task_id = ar.id
        run.progress_json = j
        run.save(update_fields=['celery_task_id', 'progress_json'])
    except Exception as exc:
        return JsonResponse({'ok': False, 'message': str(exc)}, status=500)
    return JsonResponse({'ok': True, 'run_id': run.pk, 'celery_task_id': ar.id})


@login_required
@require_POST
def import_history_stop(request, pk: int):
    """
    Сразу переводим run в cancelled в БД — иначе после рестарта Celery запись
    вечно «running», новый импорт заблокирован, а флаг cancel не увидит мёртвый воркер.
    """
    now = timezone.now()
    updated = HistoryImportRun.objects.filter(
        pk=pk,
        created_by=request.user,
        status__in=(HistoryImportRun.STATUS_PENDING, HistoryImportRun.STATUS_RUNNING),
    ).update(
        cancel_requested=True,
        status=HistoryImportRun.STATUS_CANCELLED,
        finished_at=now,
        updated_at=now,
    )
    if not updated:
        run = get_object_or_404(HistoryImportRun, pk=pk, created_by=request.user)
        return JsonResponse({'ok': True, 'message': 'Импорт уже завершён.', 'run': {
            'id': run.pk,
            'status': run.status,
            'cancel_requested': bool(run.cancel_requested),
            'progress': run.progress_json or {},
            'error_message': run.error_message or '',
        }})
    run = HistoryImportRun.objects.get(pk=pk)
    return JsonResponse({'ok': True, 'message': 'Остановка зафиксирована.', 'run': {
        'id': run.pk,
        'status': run.status,
        'cancel_requested': bool(run.cancel_requested),
        'finished_at': run.finished_at.isoformat() if run.finished_at else None,
        'progress': run.progress_json or {},
        'error_message': run.error_message or '',
    }})


def _apply_morning_digest_from_post(request, digest) -> Optional[str]:
    """Заполняет экземпляр ChannelMorningDigest из POST. Ошибка валидации — строка, иначе None."""
    import datetime as pydt

    digest.is_enabled = request.POST.get('is_enabled') == 'on'
    tz_raw = (request.POST.get('timezone_name') or '').strip()
    if tz_raw:
        digest.timezone_name = tz_raw[:64]
    st = (request.POST.get('send_time') or '05:00').strip().replace('.', ':')[:5]
    try:
        digest.send_time = pydt.datetime.strptime(st, '%H:%M').time()
    except ValueError:
        pass
    digest.weekdays = sorted(
        {int(x) for x in request.POST.getlist('weekdays') if str(x).isdigit() and 0 <= int(x) <= 6}
    )

    try:
        digest.latitude = Decimal((request.POST.get('latitude') or str(digest.latitude)).replace(',', '.'))
        digest.longitude = Decimal((request.POST.get('longitude') or str(digest.longitude)).replace(',', '.'))
    except InvalidOperation:
        return 'Некорректные широта или долгота.'

    digest.location_label = (request.POST.get('location_label') or '').strip()[:120]
    digest.country_for_holidays = (request.POST.get('country_for_holidays') or 'RU').strip().upper()[:2]
    hs = (request.POST.get('horoscope_sign') or 'general').strip()[:20]
    digest.horoscope_sign = hs if hs in dict(ChannelMorningDigest.ZODIAC_CHOICES) else ChannelMorningDigest.ZODIAC_GENERAL

    digest.block_date = request.POST.get('block_date') == 'on'
    digest.block_weather = request.POST.get('block_weather') == 'on'
    digest.block_sun = request.POST.get('block_sun') == 'on'
    digest.block_quote = request.POST.get('block_quote') == 'on'
    digest.block_english = request.POST.get('block_english') == 'on'
    digest.block_holidays = request.POST.get('block_holidays') == 'on'
    digest.block_horoscope = request.POST.get('block_horoscope') == 'on'
    digest.block_image = request.POST.get('block_image') == 'on'

    digest.use_ai_quote = request.POST.get('use_ai_quote') == 'on'
    digest.use_ai_english = request.POST.get('use_ai_english') == 'on'
    digest.use_ai_horoscope = request.POST.get('use_ai_horoscope') == 'on'
    digest.image_seed_extra = (request.POST.get('image_seed_extra') or '')[:80]
    return None


@login_required
def channel_digest_edit(request, pk):
    """Настройка автоматического утреннего дайджеста (только владелец канала)."""
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Раздел доступен только владельцу канала.')
        return redirect('channels:list')
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    digest, _ = ChannelMorningDigest.objects.get_or_create(channel=channel)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()
        if action == 'geocode':
            from .digest_services import geocode_place_label

            label = (request.POST.get('location_label') or digest.location_label or '').strip()
            lat, lon = geocode_place_label(label)
            if lat is not None and lon is not None:
                digest.location_label = label or digest.location_label
                digest.latitude = Decimal(str(lat))
                digest.longitude = Decimal(str(lon))
                digest.save(update_fields=['location_label', 'latitude', 'longitude', 'updated_at'])
                messages.success(
                    request,
                    f'Координаты обновлены: {digest.latitude}, {digest.longitude}.',
                )
            else:
                messages.warning(
                    request,
                    'Не удалось найти место. Уточните запрос или введите широту и долготу вручную.',
                )
            return redirect('channels:digest_edit', pk=pk)

        err = _apply_morning_digest_from_post(request, digest)
        if err:
            messages.error(request, err)
            return redirect('channels:digest_edit', pk=pk)

        if action == 'generate_now':
            digest.save()
            from .digest_services import create_morning_digest_draft_now

            ok, msg = create_morning_digest_draft_now(digest.pk)
            if ok:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect('channels:digest_edit', pk=pk)

        digest.save()
        messages.success(request, 'Настройки утреннего дайджеста сохранены.')
        return redirect('channels:digest_edit', pk=pk)

    selected_wd = {int(x) for x in (digest.weekdays or []) if str(x).isdigit()}
    weekday_meta = [
        (0, 'Пн'),
        (1, 'Вт'),
        (2, 'Ср'),
        (3, 'Чт'),
        (4, 'Пт'),
        (5, 'Сб'),
        (6, 'Вс'),
    ]
    return render(
        request,
        'channels/digest_edit.html',
        {
            'channel': channel,
            'digest': digest,
            'selected_weekdays': selected_wd,
            'weekday_meta': weekday_meta,
            'zodiac_choices': ChannelMorningDigest.ZODIAC_CHOICES,
        },
    )


@login_required
def channel_interesting_facts_edit(request, pk):
    """Интересные факты по теме → черновики (DeepSeek). Только владелец канала."""
    if _team_channel_editor_user(request.user):
        messages.error(request, 'Раздел доступен только владельцу канала.')
        return redirect('channels:list')
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    facts, _ = ChannelInterestingFacts.objects.get_or_create(channel=channel)

    if request.method == 'POST':
        action = (request.POST.get('action') or '').strip()

        facts.is_enabled = request.POST.get('is_enabled') == 'on'
        facts.topic = (request.POST.get('topic') or '').strip()
        ih = (request.POST.get('interval_hours') or '').strip()
        if ih.isdigit():
            v = int(ih)
            allowed = {c[0] for c in ChannelInterestingFacts.INTERVAL_CHOICES}
            if v in allowed:
                facts.interval_hours = v

        if action == 'generate_now':
            if len(facts.topic) < 5:
                messages.error(
                    request,
                    'Укажите тему запроса в поле выше (минимум 5 символов) — она сохранится вместе с генерацией.',
                )
                return redirect('channels:interesting_facts_edit', pk=pk)
            facts.save()
            from .facts_services import create_draft_post_for_facts

            ok, msg = create_draft_post_for_facts(facts.pk, force=True)
            if ok:
                messages.success(request, msg)
            else:
                messages.error(request, msg)
            return redirect('channels:interesting_facts_edit', pk=pk)

        if facts.is_enabled and len(facts.topic) < 5:
            messages.error(request, 'Для включения укажите тему запроса (не меньше нескольких слов).')
            return redirect('channels:interesting_facts_edit', pk=pk)

        facts.save()
        messages.success(request, 'Настройки сохранены.')
        return redirect('channels:interesting_facts_edit', pk=pk)

    return render(
        request,
        'channels/interesting_facts_edit.html',
        {
            'channel': channel,
            'facts': facts,
            'interval_choices': ChannelInterestingFacts.INTERVAL_CHOICES,
        },
    )
