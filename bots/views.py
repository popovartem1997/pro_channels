"""
Webhook-эндпоинты для получения обновлений от платформ.

URL-схема:
  /bots/webhook/telegram/<bot_id>/   — Telegram
  /bots/webhook/vk/<bot_id>/         — VK (Callback API)
  /bots/webhook/max/<bot_id>/        — MAX

Каждый эндпоинт:
  1. Проверяет секретный токен в заголовке (защита от посторонних запросов)
  2. Разбирает JSON
  3. Передаёт данные в соответствующий обработчик бота
  4. Возвращает HTTP 200 (платформа иначе будет повторять запрос)
"""
import json
import logging
import asyncio
from django.db import models
from django.http import HttpResponse

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404

from .models import SuggestionBot

logger = logging.getLogger(__name__)

def _ensure_max_webhook(bot: SuggestionBot):
    """
    Автоматически настраивает webhook для MAX при сохранении бота.
    Best-effort: не должен ломать сохранение.
    """
    if not bot or bot.platform != SuggestionBot.PLATFORM_MAX or not bot.is_active:
        return
    try:
        from django.conf import settings
        from django.urls import reverse
        from .max_bot.bot import MaxBotAPI

        site_url = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
        if not site_url:
            return
        url = site_url + reverse('bots:max_webhook', kwargs={'bot_id': bot.pk})
        api = MaxBotAPI(bot.get_token())
        api.set_webhook(url)
    except Exception:
        return


def _can_manage_bot_by_channel(user, bot: SuggestionBot) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    if bot.owner_id == user.id:
        return True
    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        try:
            from managers.models import TeamMember
            if bot.channel_id:
                return TeamMember.objects.filter(
                    member=user,
                    is_active=True,
                    can_manage_bots=True,
                    channels__pk=bot.channel_id,
                ).exists()
        except Exception:
            return False
    return False


def _can_view_bot(user, bot: SuggestionBot) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    if bot.owner_id == user.id:
        return True
    # Manager: allow if moderator or has bot-management for assigned channel
    if bot.moderators.filter(id=user.id).exists():
        return True
    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        try:
            from managers.models import TeamMember
            if bot.channel_id:
                return TeamMember.objects.filter(
                    member=user,
                    is_active=True,
                    can_manage_bots=True,
                    channels__pk=bot.channel_id,
                ).exists()
        except Exception:
            return False
    return False


def _can_moderate_suggestion(user, suggestion) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    if suggestion.bot.owner_id == user.id:
        return True
    return suggestion.bot.moderators.filter(id=user.id).exists()


def _get_bot_or_404(bot_id: int, platform: str) -> SuggestionBot:
    return get_object_or_404(SuggestionBot, id=bot_id, platform=platform, is_active=True)


# ─── Telegram ─────────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def telegram_webhook(request, bot_id: int):
    """
    Принимает обновления от Telegram.
    Telegram отправляет POST с JSON-телом.
    Для безопасности проверяем секрет в заголовке X-Telegram-Bot-Api-Secret-Token.
    """
    bot_config = _get_bot_or_404(bot_id, SuggestionBot.PLATFORM_TELEGRAM)

    # Проверка секретного токена (настраивается при setWebhook)
    secret = request.headers.get('X-Telegram-Bot-Api-Secret-Token', '')
    expected = getattr(bot_config, 'webhook_secret', '') or ''
    if expected and secret != expected:
        logger.warning('[TG Webhook] Неверный секрет для бота #%d', bot_id)
        return HttpResponse(status=403)

    try:
        update_data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    # Быстро отвечаем 200, обработка уходит в Celery
    try:
        from .tasks import process_telegram_update_task
        process_telegram_update_task.delay(int(bot_config.id), update_data)
    except Exception as e:
        logger.exception('[TG Webhook] Не удалось поставить задачу в очередь: %s', e)
    return HttpResponse(status=200)


# ─── VK Callback API ──────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def vk_webhook(request, bot_id: int):
    """
    Принимает события от VK Callback API.

    VK требует:
      1. При первом подключении ответить строкой confirmation_code
      2. На все прочие события отвечать строкой 'ok'

    Настройка: в VK Admin → Управление → API → Callback API
      URL: https://ваш-домен/bots/webhook/vk/<bot_id>/
      Confirmation: строка из VK (сохранить в SuggestionBot.vk_confirmation_code)
    """
    bot_config = _get_bot_or_404(bot_id, SuggestionBot.PLATFORM_VK)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    event_type = data.get('type', '')

    # VK требует ответить кодом подтверждения при проверке адреса
    if event_type == 'confirmation':
        confirmation_code = getattr(bot_config, 'vk_confirmation_code', '') or ''
        if not confirmation_code:
            logger.error('[VK Webhook] vk_confirmation_code не задан для бота #%d', bot_id)
            return HttpResponse('error', status=500)
        return HttpResponse(confirmation_code)

    # Проверка secret_key (опционально)
    vk_secret = data.get('secret', '')
    expected_secret = getattr(bot_config, 'vk_secret_key', '') or ''
    if expected_secret and vk_secret != expected_secret:
        logger.warning('[VK Webhook] Неверный secret для бота #%d', bot_id)
        return HttpResponse(status=403)

    # Обработка событий сообщений
    if event_type == 'message_new':
        try:
            from .vk.bot import VKSuggestionBot
            # Создаём временный экземпляр только для обработки этого сообщения
            vk_bot = VKSuggestionBot(bot_config)
            message_obj = data.get('object', {}).get('message', data.get('object', {}))
            vk_bot._handle_webhook_message(message_obj)
        except Exception as e:
            logger.exception('[VK Webhook] Ошибка: %s', e)

    return HttpResponse('ok')


# ─── MAX Bot API ───────────────────────────────────────────────────────────────

@csrf_exempt
@require_POST
def max_webhook(request, bot_id: int):
    """
    Принимает обновления от MAX Bot API.
    MAX отправляет POST с JSON-телом, аналогично Telegram.
    """
    bot_config = _get_bot_or_404(bot_id, SuggestionBot.PLATFORM_MAX)

    try:
        update_data = json.loads(request.body)
    except json.JSONDecodeError:
        return HttpResponse(status=400)

    try:
        from .max_bot.bot import process_max_webhook
        process_max_webhook(bot_config, update_data)
    except Exception as e:
        logger.exception('[MAX Webhook] Ошибка: %s', e)

    return HttpResponse(status=200)


# ─── Управление ботами (для владельца) ────────────────────────────────────────

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect


@login_required
def bot_list(request):
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_manage_bots=True,
        ).values_list('channels__pk', flat=True)
        bots = SuggestionBot.objects.filter(channel_id__in=allowed_channel_ids).distinct().select_related('channel')
    else:
        bots = SuggestionBot.objects.filter(owner=request.user).select_related('channel')
    return render(request, 'bots/list.html', {'bots': bots})


@login_required
def bot_create(request):
    from managers.models import TeamMember
    team_members = TeamMember.objects.filter(owner=request.user, is_active=True, can_moderate=True).select_related('member')
    selected_moderators = set(request.POST.getlist('moderators')) if request.method == 'POST' else set()
    from channels.models import Channel
    owner_channels = Channel.objects.filter(owner=request.user).order_by('name')

    # Preselect from querystring (e.g., after creating a channel)
    channel_prefill = (request.GET.get('channel_id') or '').strip() if request.method == 'GET' else ''
    platform_prefill = (request.GET.get('platform') or '').strip() if request.method == 'GET' else ''
    if platform_prefill and platform_prefill not in dict(SuggestionBot.PLATFORM_CHOICES):
        platform_prefill = ''

    if request.method == 'POST':
        import re
        name = request.POST.get('name', '').strip()
        platform = request.POST.get('platform', '')
        token = request.POST.get('bot_token', '').strip()
        channel_id = (request.POST.get('channel_id') or '').strip()
        welcome_msg = request.POST.get('welcome_message', '').strip()
        success_msg = request.POST.get('success_message', '').strip()
        approved_msg = request.POST.get('approved_message', '').strip()
        rejected_msg = request.POST.get('rejected_message', '').strip()

        if not all([name, platform, token, channel_id]):
            messages.error(request, 'Заполните все обязательные поля.')
            return render(request, 'bots/create.html', {
                'platforms': SuggestionBot.PLATFORM_CHOICES,
                'team_members': team_members,
                'selected_moderators': selected_moderators,
                'owner_channels': owner_channels,
            })

        channel = get_object_or_404(Channel, pk=channel_id, owner=request.user)
        bot = SuggestionBot(
            owner=request.user,
            channel=channel,
            name=name,
            platform=platform,
            welcome_message=welcome_msg or SuggestionBot._meta.get_field('welcome_message').default,
            success_message=success_msg or SuggestionBot._meta.get_field('success_message').default,
            approved_message=approved_msg or SuggestionBot._meta.get_field('approved_message').default,
            rejected_message=rejected_msg or SuggestionBot._meta.get_field('rejected_message').default,
        )
        bot.set_token(token)

        if platform == SuggestionBot.PLATFORM_TELEGRAM:
            bot.admin_chat_id = request.POST.get('admin_chat_id', '').strip()
            bot.notify_owner = request.POST.get('notify_owner') == 'on'

            # Custom chat ids: numbers separated by comma/space/newline
            raw_custom = (request.POST.get('custom_admin_chat_ids') or '').strip()
            ids = []
            for part in re.split(r'[\s,]+', raw_custom):
                p = part.strip()
                if not p:
                    continue
                # Allow only numeric (chat_id/user_id). @username is not reliable for Bot API sends.
                if re.fullmatch(r'-?\d+', p):
                    ids.append(p)
            bot.custom_admin_chat_ids = ids
        else:
            gid = request.POST.get('group_id', '').strip()
            # VK/MAX group ids are typically provided without '-'. Normalize to digits.
            gid = gid.replace('club', '').replace('public', '')
            gid = gid.lstrip('-').strip()
            bot.group_id = gid

        bot.save()
        # Автоподключение webhook для MAX (без кнопок)
        _ensure_max_webhook(bot)

        # Moderators: only owner team members with can_moderate
        moderator_ids = request.POST.getlist('moderators')
        if moderator_ids:
            allowed_users = TeamMember.objects.filter(
                owner=request.user,
                is_active=True,
                can_moderate=True,
                member_id__in=moderator_ids,
            ).values_list('member_id', flat=True)
            bot.moderators.set(list(allowed_users))

        # Audit
        try:
            from .models import AuditLog
            AuditLog.objects.create(
                actor=request.user,
                owner=request.user,
                action='suggestion_bot.create',
                object_type='SuggestionBot',
                object_id=str(bot.pk),
                data={'name': bot.name, 'platform': bot.platform, 'channel_id': bot.channel_id},
            )
        except Exception:
            pass

        messages.success(request, f'Бот "{name}" создан.')
        return redirect('bots:list')

    return render(request, 'bots/create.html', {
        'platforms': SuggestionBot.PLATFORM_CHOICES,
        'team_members': team_members,
        'selected_moderators': selected_moderators,
        'owner_channels': owner_channels,
        'channel_prefill': channel_prefill,
        'platform_prefill': platform_prefill,
    })


@login_required
def bot_detail(request, bot_id):
    bot = get_object_or_404(SuggestionBot, pk=bot_id)
    if not _can_view_bot(request.user, bot):
        return HttpResponse(status=403)
    from .models import Suggestion
    recent_suggestions = Suggestion.objects.filter(bot=bot).order_by('-submitted_at')[:20]
    return render(request, 'bots/detail.html', {
        'bot': bot,
        'recent_suggestions': recent_suggestions,
    })


@login_required
def bot_edit(request, bot_id: int):
    from managers.models import TeamMember
    import re

    bot = get_object_or_404(SuggestionBot, pk=bot_id)
    if not _can_view_bot(request.user, bot):
        return HttpResponse(status=403)

    footer_only = False
    can_edit_messages_only = False
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        # Managers may edit only bot messages (no tokens/webhook/admin chats).
        can_edit_messages_only = True

    team_members = TeamMember.objects.filter(owner=bot.owner, is_active=True, can_moderate=True).select_related('member')
    from channels.models import Channel
    owner_channels = Channel.objects.filter(owner=bot.owner).order_by('name')

    selected_moderators = set()
    if request.method == 'POST':
        selected_moderators = set(request.POST.getlist('moderators'))
    else:
        selected_moderators = set(str(x) for x in bot.moderators.values_list('id', flat=True))

    # textarea initial
    custom_admin_chat_ids = '\n'.join(str(x) for x in (bot.custom_admin_chat_ids or []))

    if request.method == 'POST':
        before = {
            'name': bot.name,
            'channel_id': bot.channel_id,
            'welcome_message': bot.welcome_message,
            'success_message': bot.success_message,
            'approved_message': bot.approved_message,
            'rejected_message': bot.rejected_message,
            'admin_chat_id': bot.admin_chat_id,
            'notify_owner': bot.notify_owner,
            'custom_admin_chat_ids': list(bot.custom_admin_chat_ids or []),
            'group_id': bot.group_id,
            'moderator_ids': list(bot.moderators.values_list('id', flat=True)),
        }
        name = request.POST.get('name', '').strip()
        if not name:
            messages.error(request, 'Название обязательно.')
            return render(request, 'bots/edit.html', {
                'bot': bot,
                'team_members': team_members,
                'selected_moderators': selected_moderators,
                'custom_admin_chat_ids': request.POST.get('custom_admin_chat_ids', custom_admin_chat_ids),
                'owner_channels': owner_channels,
                'can_edit_messages_only': can_edit_messages_only,
            })

        bot.name = name

        if not can_edit_messages_only:
            channel_id = (request.POST.get('channel_id') or '').strip()
            if channel_id:
                bot.channel = get_object_or_404(Channel, pk=channel_id, owner=bot.owner)

        if not can_edit_messages_only:
            new_token = request.POST.get('bot_token', '').strip()
            if new_token:
                bot.set_token(new_token)

        bot.welcome_message = request.POST.get('welcome_message', bot.welcome_message).strip() or bot.welcome_message
        bot.success_message = request.POST.get('success_message', bot.success_message).strip() or bot.success_message
        bot.approved_message = request.POST.get('approved_message', bot.approved_message).strip() or bot.approved_message
        bot.rejected_message = request.POST.get('rejected_message', bot.rejected_message).strip() or bot.rejected_message

        if not can_edit_messages_only:
            if bot.platform == SuggestionBot.PLATFORM_TELEGRAM:
                bot.admin_chat_id = request.POST.get('admin_chat_id', '').strip()
                bot.notify_owner = request.POST.get('notify_owner') == 'on'

                raw_custom = (request.POST.get('custom_admin_chat_ids') or '').strip()
                ids = []
                for part in re.split(r'[\s,]+', raw_custom):
                    p = part.strip()
                    if not p:
                        continue
                    if re.fullmatch(r'-?\d+', p):
                        ids.append(p)
                bot.custom_admin_chat_ids = ids
            else:
                gid = request.POST.get('group_id', '').strip()
                gid = gid.replace('club', '').replace('public', '')
                gid = gid.lstrip('-').strip()
                bot.group_id = gid

        bot.save()
        # Автоподключение webhook для MAX (без кнопок)
        if not can_edit_messages_only:
            _ensure_max_webhook(bot)

        if not can_edit_messages_only:
            # apply moderators set (owner only)
            moderator_ids = request.POST.getlist('moderators')
            allowed_users = TeamMember.objects.filter(
                owner=bot.owner,
                is_active=True,
                can_moderate=True,
                member_id__in=moderator_ids,
            ).values_list('member_id', flat=True)
            bot.moderators.set(list(allowed_users))

        # Audit changes
        try:
            after = {
                'name': bot.name,
                'channel_id': bot.channel_id,
                'welcome_message': bot.welcome_message,
                'success_message': bot.success_message,
                'approved_message': bot.approved_message,
                'rejected_message': bot.rejected_message,
                'admin_chat_id': bot.admin_chat_id,
                'notify_owner': bot.notify_owner,
                'custom_admin_chat_ids': list(bot.custom_admin_chat_ids or []),
                'group_id': bot.group_id,
                'moderator_ids': list(bot.moderators.values_list('id', flat=True)),
            }
            changed = {}
            for k in after.keys():
                if before.get(k) != after.get(k):
                    changed[k] = {'before': before.get(k), 'after': after.get(k)}
            if changed:
                from .models import AuditLog
                AuditLog.objects.create(
                    actor=request.user,
                    owner=bot.owner,
                    action='suggestion_bot.update',
                    object_type='SuggestionBot',
                    object_id=str(bot.pk),
                    data={'changed': changed, 'limited_mode': bool(can_edit_messages_only)},
                )
        except Exception:
            pass

        messages.success(request, 'Бот обновлён.')
        return redirect('bots:detail', bot_id=bot.pk)

    return render(request, 'bots/edit.html', {
        'bot': bot,
        'team_members': team_members,
        'selected_moderators': selected_moderators,
        'custom_admin_chat_ids': custom_admin_chat_ids,
        'owner_channels': owner_channels,
        'can_edit_messages_only': can_edit_messages_only,
    })


@login_required
def conversations_list(request):
    """Список диалогов 'подписчик ↔ менеджер' по ботам предложки."""
    from .models import BotConversation
    qs = BotConversation.objects.select_related('bot', 'bot__channel')
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_manage_bots=True,
        ).values_list('channels__pk', flat=True)
        qs = qs.filter(bot__channel_id__in=allowed_channel_ids)
    else:
        qs = qs.filter(bot__owner=request.user)
    qs = qs.order_by('-last_message_at', '-created_at')[:200]
    return render(request, 'bots/conversations_list.html', {'conversations': qs})


@login_required
def conversation_detail(request, pk: int):
    """Просмотр диалога и ответ менеджера пользователю (через Telegram Bot API)."""
    from .models import BotConversation, BotConversationMessage, AuditLog
    conv = get_object_or_404(BotConversation.objects.select_related('bot', 'bot__channel', 'bot__owner'), pk=pk)
    if not _can_manage_bot_by_channel(request.user, conv.bot) and not _can_view_bot(request.user, conv.bot):
        return HttpResponse(status=403)

    if request.method == 'POST':
        text = (request.POST.get('text') or '').strip()
        if not text:
            messages.error(request, 'Введите текст ответа.')
            return redirect('bots:conversation_detail', pk=pk)

        if conv.bot.platform != SuggestionBot.PLATFORM_TELEGRAM:
            messages.error(request, 'Ответы через сайт пока поддерживаются только для Telegram.')
            return redirect('bots:conversation_detail', pk=pk)

        # Send to user via Telegram Bot API
        try:
            import requests
            token = conv.bot.get_token()
            resp = requests.post(
                f'https://api.telegram.org/bot{token}/sendMessage',
                json={'chat_id': conv.platform_user_id, 'text': text},
                timeout=10,
            )
            data = resp.json()
            if not data.get('ok'):
                raise ValueError(data.get('description', 'Telegram error'))
        except Exception as e:
            messages.error(request, f'Не удалось отправить сообщение: {e}')
            return redirect('bots:conversation_detail', pk=pk)

        BotConversationMessage.objects.create(
            conversation=conv,
            direction='out',
            sender_user=request.user,
            text=text,
            raw_data={},
        )
        conv.last_message_at = timezone.now()
        conv.save(update_fields=['last_message_at'])

        AuditLog.objects.create(
            actor=request.user,
            owner=conv.bot.owner,
            action='bot_conversation.reply',
            object_type='BotConversation',
            object_id=str(conv.pk),
            data={'bot_id': conv.bot_id},
        )
        messages.success(request, 'Ответ отправлен.')
        return redirect('bots:conversation_detail', pk=pk)

    msgs = BotConversationMessage.objects.filter(conversation=conv).select_related('sender_user')
    return render(request, 'bots/conversation_detail.html', {'conv': conv, 'messages_list': msgs})


@login_required
def bot_delete(request, bot_id: int):
    bot = get_object_or_404(SuggestionBot, pk=bot_id, owner=request.user)
    if request.method == 'POST':
        name = bot.name
        bot.delete()
        messages.success(request, f'Бот "{name}" удалён.')
        return redirect('bots:list')
    return render(request, 'bots/delete_confirm.html', {'bot': bot})


@login_required
def suggestions_list(request):
    from .models import Suggestion
    status_filter = request.GET.get('status', 'pending')
    bot_id = request.GET.get('bot', '')
    suggestions = Suggestion.objects.filter(
        models.Q(bot__owner=request.user) | models.Q(bot__moderators=request.user)
    ).select_related('bot').distinct()
    if status_filter:
        suggestions = suggestions.filter(status=status_filter)
    if bot_id:
        suggestions = suggestions.filter(bot_id=bot_id)
    suggestions = suggestions.order_by('-submitted_at')[:100]
    bots = SuggestionBot.objects.filter(models.Q(owner=request.user) | models.Q(moderators=request.user)).distinct()
    return render(request, 'bots/suggestions.html', {
        'suggestions': suggestions,
        'status_filter': status_filter,
        'statuses': [('pending', 'Ожидают'), ('approved', 'Одобренные'), ('rejected', 'Отклонённые'), ('published', 'Опубликованные')],
        'bots': bots,
        'bot_id': bot_id,
    })


@login_required
def suggestions_all(request):
    """
    Отдельная страница: все предложения со всех ботов.
    - Владелец/админ: все предложения по своим ботам
    - Менеджер: предложения по ботам, привязанным к каналам, к которым есть доступ (can_moderate)
    """
    from .models import Suggestion
    from parsing.models import ParsedItem

    status_filter = request.GET.get('status', 'pending')
    if status_filter in ('all', ''):
        status_filter = ''
    source_filter = request.GET.get('source', 'all')  # all | subscriber | parsing

    # ---- Subscriber suggestions (from bots) ----
    if request.user.is_staff or request.user.is_superuser:
        sug_qs = Suggestion.objects.all()
    elif getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_moderate=True,
        ).values_list('channels__pk', flat=True)
        sug_qs = Suggestion.objects.filter(
            models.Q(bot__channel_id__in=allowed_channel_ids)
            | models.Q(bot__moderators=request.user)
        )
    else:
        sug_qs = Suggestion.objects.filter(bot__owner=request.user)

    sug_qs = sug_qs.select_related('bot', 'bot__channel').distinct()
    if status_filter:
        sug_qs = sug_qs.filter(status=status_filter)

    # ---- Parsed items (from parsing) ----
    if request.user.is_staff or request.user.is_superuser:
        parsed_qs = ParsedItem.objects.all()
    elif getattr(request.user, 'role', '') in ('manager', 'assistant_admin'):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_moderate=True,
        ).values_list('channels__pk', flat=True)
        parsed_qs = ParsedItem.objects.filter(source__channel_id__in=allowed_channel_ids)
    else:
        parsed_qs = ParsedItem.objects.filter(source__owner=request.user)

    parsed_qs = parsed_qs.select_related('source', 'source__channel', 'keyword')

    items = []
    if source_filter in ('all', 'subscriber'):
        for s in sug_qs.order_by('-submitted_at')[:200]:
            items.append({
                'kind': 'subscriber',
                'created_at': s.submitted_at,
                'status': s.status,
                'bot_name': getattr(s.bot, 'name', ''),
                'channel_name': getattr(getattr(s.bot, 'channel', None), 'name', ''),
                'sender': getattr(s, 'sender_display', '') or (s.platform_username or s.platform_user_id),
                'text': s.text or '',
                'obj': s,
            })

    if source_filter in ('all', 'parsing'):
        for p in parsed_qs.order_by('-found_at')[:200]:
            # map parsed status to suggestion-like
            st = 'pending' if p.status == ParsedItem.STATUS_NEW else ('published' if p.status == ParsedItem.STATUS_USED else 'rejected')
            items.append({
                'kind': 'parsing',
                'created_at': p.found_at,
                'status': st,
                'bot_name': 'Парсинг',
                'channel_name': getattr(getattr(p.source, 'channel', None), 'name', '') or '',
                'sender': f'{p.source.get_platform_display()} · {p.source.name}',
                'text': p.text or '',
                'url': p.original_url or '',
                'obj': p,
            })

    # Sort unified feed
    items.sort(key=lambda x: x.get('created_at') or timezone.now(), reverse=True)
    items = items[:300]

    return render(request, 'bots/suggestions_all.html', {
        'items': items,
        'status_filter': status_filter,
        'source_filter': source_filter,
        'statuses': [
            ('', 'Все'),
            ('pending', 'Ожидают'),
            ('approved', 'Одобренные'),
            ('rejected', 'Отклонённые'),
            ('published', 'Опубликованные'),
        ],
        'sources': [('all', 'Все'), ('subscriber', 'От подписчиков'), ('parsing', 'Парсинг')],
    })


@login_required
def suggestion_moderate(request, pk):
    from .models import Suggestion
    suggestion = get_object_or_404(Suggestion, pk=pk)
    if not _can_moderate_suggestion(request.user, suggestion):
        return HttpResponse(status=403)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            suggestion.approve(request.user)
            # Уведомление автора новости (если возможно)
            try:
                from .services import notify_suggestion_approved
                notify_suggestion_approved(suggestion)
            except Exception:
                pass
            messages.success(request, f'Предложение #{suggestion.short_tracking_id} одобрено.')
            # Если пришли из ленты — сразу ведём в создание поста из предложки
            next_url = (request.POST.get('next') or '').strip()
            if next_url:
                return redirect(next_url)
        elif action == 'reject':
            reason = request.POST.get('reason', '')
            suggestion.reject(reason, request.user)
            try:
                from .services import notify_suggestion_rejected
                notify_suggestion_rejected(suggestion, reason=reason)
            except Exception:
                pass
            messages.success(request, f'Предложение #{suggestion.short_tracking_id} отклонено.')
    return redirect(request.META.get('HTTP_REFERER', 'bots:suggestions'))


@login_required
def suggestion_media(request, pk: int, idx: int):
    """
    Прокси для медиа предложки (нужно для миниатюр в ленте).
    Не отдаём прямые ссылки вида https://api.telegram.org/file/bot<TOKEN>/... в браузер.
    """
    from .models import Suggestion
    import requests

    suggestion = get_object_or_404(Suggestion.objects.select_related('bot'), pk=pk)
    if not _can_moderate_suggestion(request.user, suggestion):
        return HttpResponse(status=403)

    bot = suggestion.bot
    if not bot or bot.platform != SuggestionBot.PLATFORM_TELEGRAM:
        return HttpResponse(status=404)

    media_ids = suggestion.media_file_ids or []
    if idx < 0 or idx >= len(media_ids):
        return HttpResponse(status=404)
    file_id = media_ids[idx]
    if not file_id:
        return HttpResponse(status=404)

    token = bot.get_token()
    api_base = f'https://api.telegram.org/bot{token}'
    file_base = f'https://api.telegram.org/file/bot{token}'
    try:
        r = requests.get(f'{api_base}/getFile', params={'file_id': file_id}, timeout=15)
        data = r.json()
        if not data.get('ok'):
            return HttpResponse(status=404)
        file_path = data['result']['file_path']
        dl = requests.get(f'{file_base}/{file_path}', timeout=30)
        dl.raise_for_status()
        ct = dl.headers.get('Content-Type') or 'application/octet-stream'
        return HttpResponse(dl.content, content_type=ct)
    except Exception:
        return HttpResponse(status=404)


# ─── Страница лидерборда (публичная) ──────────────────────────────────────────

def leaderboard(request, bot_id: int):
    """
    Публичный лидерборд — топ отправителей предложений для конкретного бота.
    Доступен по URL: /bots/<bot_id>/leaderboard/
    """
    from django.shortcuts import render
    bot_config = get_object_or_404(SuggestionBot, id=bot_id, is_active=True)
    top_users = bot_config.user_stats.order_by('-approved', '-total')[:50]
    return render(request, 'bots/leaderboard.html', {
        'bot': bot_config,
        'top_users': top_users,
    })
