"""
Управление каналами и пабликами.
"""
import json
from decimal import Decimal, InvalidOperation
from typing import Optional, Tuple

import requests as http_requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.http import JsonResponse
from django.db import IntegrityError, transaction
from django.utils import timezone
from .models import Channel, ChannelGroup, HistoryImportRun


def _team_channel_editor_user(user) -> bool:
    """Менеджер / помощник админа (не staff): доступ к каналам команды с ограниченным редактированием."""
    return getattr(user, 'role', '') in ('manager', 'assistant_admin') and not (
        user.is_staff or user.is_superuser
    )


def _parse_ad_slot_schedule_json(raw) -> Tuple[Optional[list], Optional[str]]:
    """
    Парсит JSON расписания слотов рекламы.
    Формат: [{"weekday": 0, "times": ["10:00", "14:00"]}, ...] — weekday пн=0 … вс=6.
    """
    if raw is None:
        return [], None
    s = str(raw).strip()
    if not s:
        return [], None
    try:
        data = json.loads(s)
    except json.JSONDecodeError as e:
        return None, f'Некорректный JSON: {e}'
    if not isinstance(data, list):
        return None, 'Ожидается JSON-массив [...]'
    for i, block in enumerate(data):
        if not isinstance(block, dict):
            return None, f'Элемент {i}: нужен объект {{ "weekday", "times" }}'
        wd = block.get('weekday')
        if wd is not None:
            try:
                wdi = int(wd)
            except (TypeError, ValueError):
                return None, f'Элемент {i}: weekday должен быть числом 0–6'
            if wdi < 0 or wdi > 6:
                return None, f'Элемент {i}: weekday в диапазоне 0 (пн) … 6 (вс)'
        times = block.get('times')
        if times is not None and not isinstance(times, list):
            return None, f'Элемент {i}: times — массив строк времени (например "10:30")'
    return data, None


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

        sched_list, sched_err = _parse_ad_slot_schedule_json(request.POST.get('ad_slot_schedule_json'))
        if sched_err:
            messages.warning(request, f'Расписание слотов не обновлено: {sched_err}')
        else:
            channel.ad_slot_schedule_json = sched_list

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

        channel.save()
        messages.success(request, 'Канал обновлён.')
        return redirect('channels:detail', pk=pk)

    groups = ChannelGroup.objects.filter(owner=request.user).order_by('name', 'pk')
    ad_slot_json_text = json.dumps(channel.ad_slot_schedule_json or [], ensure_ascii=False, indent=2)
    return render(
        request,
        'channels/edit.html',
        {
            'channel': channel,
            'footer_only': footer_only,
            'channel_groups': groups,
            'ad_slot_json_text': ad_slot_json_text,
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
        import_tg_history_to_max_task.delay(run.pk)
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
            'source_channel': {'id': run.source_channel_id, 'name': run.source_channel.name},
            'target_channel': {'id': run.target_channel_id, 'name': run.target_channel.name},
            'cancel_requested': bool(run.cancel_requested),
        }
    })


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
