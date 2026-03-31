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

from django.http import HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404

from .models import SuggestionBot

logger = logging.getLogger(__name__)


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

    # Обработка через python-telegram-bot
    try:
        from telegram import Update
        from .telegram.handlers import build_application

        app = build_application(bot_config)

        async def process():
            async with app:
                update = Update.de_json(update_data, app.bot)
                await app.process_update(update)

        asyncio.run(process())
    except Exception as e:
        logger.exception('[TG Webhook] Ошибка обработки обновления: %s', e)

    # Всегда возвращаем 200 — иначе Telegram будет повторять запрос
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
    bots = SuggestionBot.objects.filter(owner=request.user)
    return render(request, 'bots/list.html', {'bots': bots})


@login_required
def bot_create(request):
    from managers.models import TeamMember
    team_members = TeamMember.objects.filter(owner=request.user, is_active=True, can_moderate=True).select_related('member')
    selected_moderators = set(request.POST.getlist('moderators')) if request.method == 'POST' else set()

    if request.method == 'POST':
        import re
        name = request.POST.get('name', '').strip()
        platform = request.POST.get('platform', '')
        token = request.POST.get('bot_token', '').strip()
        welcome_msg = request.POST.get('welcome_message', '').strip()
        success_msg = request.POST.get('success_message', '').strip()
        approved_msg = request.POST.get('approved_message', '').strip()
        rejected_msg = request.POST.get('rejected_message', '').strip()

        if not all([name, platform, token]):
            messages.error(request, 'Заполните все обязательные поля.')
            return render(request, 'bots/create.html', {
                'platforms': SuggestionBot.PLATFORM_CHOICES,
                'team_members': team_members,
                'selected_moderators': selected_moderators,
            })

        bot = SuggestionBot(
            owner=request.user,
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

        messages.success(request, f'Бот "{name}" создан.')
        return redirect('bots:list')

    return render(request, 'bots/create.html', {
        'platforms': SuggestionBot.PLATFORM_CHOICES,
        'team_members': team_members,
        'selected_moderators': selected_moderators,
    })


@login_required
def bot_detail(request, bot_id):
    bot = get_object_or_404(SuggestionBot, pk=bot_id, owner=request.user)
    from .models import Suggestion
    recent_suggestions = Suggestion.objects.filter(bot=bot).order_by('-submitted_at')[:20]
    return render(request, 'bots/detail.html', {
        'bot': bot,
        'recent_suggestions': recent_suggestions,
    })


@login_required
def suggestions_list(request):
    from .models import Suggestion
    status_filter = request.GET.get('status', 'pending')
    bot_id = request.GET.get('bot', '')
    suggestions = Suggestion.objects.filter(bot__owner=request.user).select_related('bot')
    if status_filter:
        suggestions = suggestions.filter(status=status_filter)
    if bot_id:
        suggestions = suggestions.filter(bot_id=bot_id)
    suggestions = suggestions.order_by('-submitted_at')[:100]
    bots = SuggestionBot.objects.filter(owner=request.user)
    return render(request, 'bots/suggestions.html', {
        'suggestions': suggestions,
        'status_filter': status_filter,
        'statuses': [('pending', 'Ожидают'), ('approved', 'Одобренные'), ('rejected', 'Отклонённые'), ('published', 'Опубликованные')],
        'bots': bots,
        'bot_id': bot_id,
    })


@login_required
def suggestion_moderate(request, pk):
    from .models import Suggestion
    suggestion = get_object_or_404(Suggestion, pk=pk, bot__owner=request.user)
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'approve':
            suggestion.approve(request.user)
            messages.success(request, f'Предложение #{suggestion.short_tracking_id} одобрено.')
        elif action == 'reject':
            reason = request.POST.get('reason', '')
            suggestion.reject(reason, request.user)
            messages.success(request, f'Предложение #{suggestion.short_tracking_id} отклонено.')
    return redirect(request.META.get('HTTP_REFERER', 'bots:suggestions'))


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
