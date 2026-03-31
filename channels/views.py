"""
Управление каналами и пабликами.
"""
import requests as http_requests
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from django.contrib import messages
from django.http import JsonResponse
from django.db import IntegrityError
from .models import Channel


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
    channels = Channel.objects.filter(owner=request.user).order_by('-created_at')
    return render(request, 'channels/list.html', {'channels': channels})


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
        return redirect('channels:list')

    return render(request, 'channels/create.html', {
        'platforms': Channel.PLATFORM_CHOICES,
        'initial': request.session.pop('channel_create_initial', {}),
    })


@login_required
def channel_detail(request, pk):
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
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
    })


@login_required
def channel_edit(request, pk):
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    if request.method == 'POST':
        channel.name = request.POST.get('name', channel.name).strip()
        channel.description = request.POST.get('description', '').strip()
        channel.ad_enabled = request.POST.get('ad_enabled') == 'on'
        try:
            channel.ad_price = request.POST.get('ad_price', channel.ad_price) or 0
        except Exception:
            pass

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
        channel.save()
        messages.success(request, 'Канал обновлён.')
        return redirect('channels:detail', pk=pk)

    return render(request, 'channels/edit.html', {'channel': channel})


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
                    'https://botapi.max.ru/messages',
                    params={'access_token': token},
                    json={'chat_id': chat_id, 'text': '✅ Тестовое сообщение от ProChannels'},
                    timeout=15,
                )
                try:
                    data = resp.json()
                except Exception:
                    data = resp.text
                if isinstance(data, dict):
                    # Typical error format: {"code": "...", "message": "..."}
                    if resp.status_code >= 400 or data.get('code') or data.get('message'):
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
                'https://botapi.max.ru/me',
                params={'access_token': token},
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
def channel_delete(request, pk):
    channel = get_object_or_404(Channel, pk=pk, owner=request.user)
    if request.method == 'POST':
        name = channel.name
        channel.delete()
        messages.success(request, f'Канал "{name}" удалён.')
        return redirect('channels:list')
    return render(request, 'channels/delete_confirm.html', {'channel': channel})
