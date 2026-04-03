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

_MODERATION_CHECKBOXES_NONE_SAVED = (
    'Отмечены получатели модерации, но ни один не сохранился. '
    'Проверьте в «Команда → доступы», что у менеджера включено «Может модерировать предложки», '
    'что менеджер привязан к тому же владельцу, что и бот, затем снова отметьте галочки и сохраните.'
)


def _suggestion_bot_channel_id_set(bot: SuggestionBot) -> set:
    return set(bot.target_channel_ids())

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


def _moderation_recipient_users_from_post(request, owner_user):
    """
    Пользователи из multiselect «Кому слать модерацию».
    Разрешены только владелец и активные менеджеры с правом модерировать предложки.
    """
    from django.contrib.auth import get_user_model
    from managers.models import TeamMember

    User = get_user_model()
    allowed = {owner_user.pk}
    allowed.update(
        TeamMember.objects.filter(
            owner=owner_user,
            is_active=True,
            can_moderate=True,
        ).values_list('member_id', flat=True)
    )
    ids: list[int] = []
    for x in request.POST.getlist('moderators'):
        s = str(x).strip()
        if s.isdigit():
            pk = int(s)
            if pk in allowed:
                ids.append(pk)
    return User.objects.filter(pk__in=ids)


def _moderation_qs_and_users_for_post(request, owner_user, *, existing_bot: SuggestionBot | None):
    """
    Чекбоксы «Кому слать модерацию» не попадают в POST, если ни одна не отмечена.
    Без этого при «Сохранить» вызывалось moderators.set([]) и список получателей обнулялся —
    модерация переставала уходить никому.

    Правило: есть отмеченные галочки → берём их; иначе если заполнен «чат модерации» → явно пустой
    список личных получателей (только группа); иначе при редактировании сохраняем текущий M2M из БД.
    """
    from django.contrib.auth import get_user_model

    User = get_user_model()
    admin_chat = (request.POST.get('admin_chat_id') or '').strip()
    if request.POST.getlist('moderators'):
        qs = _moderation_recipient_users_from_post(request, owner_user)
        return qs, list(qs)
    if admin_chat:
        return User.objects.none(), []
    if existing_bot is not None:
        qs = existing_bot.moderators.all()
        return qs, list(qs)
    return User.objects.none(), []


def _telegram_moderation_config_error(owner_user, mod_users, admin_chat_id: str) -> str | None:
    """Ошибка для messages.error или None, если личка или группа настроены."""
    from bots.models import telegram_user_id_for_moderation_recipient

    admin_chat_id = (admin_chat_id or '').strip()
    if not mod_users:
        return None
    if any(telegram_user_id_for_moderation_recipient(owner_user.pk, u) is not None for u in mod_users):
        return None
    if admin_chat_id:
        return None
    names = ', '.join(u.get_username() or u.email or str(u.pk) for u in mod_users[:8])
    if len(mod_users) > 8:
        names += '…'
    return (
        'Ни у кого из выбранных получателей нет сохранённого Telegram user ID, а «чат модерации» пуст. '
        'Укажите ID в «Профиль» или «Команда → доступы», либо добавьте ID группы (супергруппы), куда добавлен бот. '
        'Важно: пока модератор не нажал /start у этого бота, Telegram не доставит ему личные сообщения. '
        f'Получатели: {names}.'
    )


def _max_moderation_config_error(owner_user, mod_users, admin_chat_id: str) -> str | None:
    from bots.models import max_user_id_str_for_moderation_recipient

    admin_chat_id = (admin_chat_id or '').strip()
    if not mod_users:
        return None
    if any(max_user_id_str_for_moderation_recipient(owner_user.pk, u) for u in mod_users):
        return None
    if admin_chat_id:
        return None
    names = ', '.join(u.get_username() or u.email or str(u.pk) for u in mod_users[:8])
    if len(mod_users) > 8:
        names += '…'
    return (
        'Ни у кого из выбранных получателей нет MAX user ID и не задан чат модерации. '
        f'Заполните ID в профиле или карточке команды. Получатели: {names}.'
    )


def _can_manage_bot_by_channel(user, bot: SuggestionBot) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    if bot.owner_id == user.id:
        return True
    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        try:
            from managers.models import TeamMember
            bot_cids = _suggestion_bot_channel_id_set(bot)
            if not bot_cids:
                return False
            return TeamMember.objects.filter(
                member=user,
                is_active=True,
                can_manage_bots=True,
                channels__pk__in=bot_cids,
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
            bot_cids = _suggestion_bot_channel_id_set(bot)
            if not bot_cids:
                return False
            return TeamMember.objects.filter(
                member=user,
                is_active=True,
                can_manage_bots=True,
                channels__pk__in=bot_cids,
            ).exists()
        except Exception:
            return False
    return False


def _can_moderate_suggestion(user, suggestion) -> bool:
    if user.is_staff or user.is_superuser:
        return True
    if suggestion.bot.owner_id == user.id:
        return True
    if suggestion.bot.moderators.filter(id=user.id).exists():
        return True
    # Менеджер команды: доступ по каналам групп бота (как в ленте / suggestions_all)
    if getattr(user, 'role', '') in ('manager', 'assistant_admin'):
        try:
            from managers.models import TeamMember
            bot_cids = _suggestion_bot_channel_id_set(suggestion.bot)
            if not bot_cids:
                return False
            return TeamMember.objects.filter(
                member=user,
                is_active=True,
                can_moderate=True,
                channels__pk__in=bot_cids,
            ).exists()
        except Exception:
            return False
    return False


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

    # По умолчанию обрабатываем здесь — иначе без Celery-воркера бот «молчит» (200 уже отдан, update в очереди).
    from django.conf import settings

    from .tasks import process_telegram_update_core, process_telegram_update_task

    if getattr(settings, 'TELEGRAM_WEBHOOK_USE_CELERY', False):
        try:
            process_telegram_update_task.delay(int(bot_config.id), update_data)
        except Exception as e:
            logger.exception('[TG Webhook] Не удалось поставить задачу в очередь, обрабатываем синхронно: %s', e)
            try:
                process_telegram_update_core(int(bot_config.id), update_data)
            except Exception as e2:
                logger.exception('[TG Webhook] Синхронная обработка не удалась: %s', e2)
    else:
        try:
            process_telegram_update_core(int(bot_config.id), update_data)
        except Exception as e:
            logger.exception('[TG Webhook] Обработка update не удалась: %s', e)
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
        # Диагностика: логируем факт получения вебхука (best-effort)
        try:
            from .models import AuditLog
            ut = ''
            if isinstance(update_data, dict):
                ut = str(update_data.get('update_type') or update_data.get('type') or '')
            cb = update_data.get('callback') if isinstance(update_data, dict) else None
            cb_id = ''
            cb_payload = ''
            if isinstance(cb, dict):
                cb_id = str(cb.get('callback_id') or cb.get('id') or '')
                cb_payload = cb.get('payload')
                if isinstance(cb_payload, dict):
                    cb_payload = cb_payload.get('payload') or cb_payload.get('data') or ''
                cb_payload = str(cb_payload or '')
            msg = update_data.get('message') if isinstance(update_data, dict) else None
            att_preview = []
            if isinstance(msg, dict):
                body = msg.get('body') or {}
                if isinstance(body, dict):
                    atts = body.get('attachments') or []
                    if isinstance(atts, list):
                        for a in atts[:6]:
                            if isinstance(a, dict):
                                payload = a.get('payload') or {}
                                att_preview.append({
                                    'type': a.get('type'),
                                    'payload_keys': list(payload.keys())[:20] if isinstance(payload, dict) else [],
                                    'payload_url': (payload.get('url') if isinstance(payload, dict) else None),
                                })
            AuditLog.objects.create(
                actor=None,
                owner=bot_config.owner,
                action='max.webhook.received',
                object_type='SuggestionBot',
                object_id=str(bot_config.pk),
                data={
                    'update_type': ut,
                    'keys': list(update_data.keys())[:30] if isinstance(update_data, dict) else [],
                    'callback_id': cb_id,
                    'callback_payload': cb_payload[:200],
                    'attachments': att_preview,
                }
            )
        except Exception:
            pass
        from .max_bot.bot import process_max_webhook
        process_max_webhook(bot_config, update_data)
    except Exception as e:
        logger.exception('[MAX Webhook] Ошибка: %s', e)
        try:
            from .models import AuditLog
            AuditLog.objects.create(
                actor=None,
                owner=bot_config.owner,
                action='max.webhook.error',
                object_type='SuggestionBot',
                object_id=str(bot_config.pk),
                data={'error': str(e)[:500]},
            )
        except Exception:
            pass

    return HttpResponse(status=200)


# ─── Управление ботами (для владельца) ────────────────────────────────────────

from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme


@login_required
def bot_list(request):
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_manage_bots=True,
        ).values_list('channels__pk', flat=True)
        bots = (
            SuggestionBot.objects.filter(channel_groups__channels__pk__in=allowed_channel_ids)
            .distinct()
            .prefetch_related('channel_groups')
        )
    else:
        bots = SuggestionBot.objects.filter(owner=request.user).prefetch_related('channel_groups')
    return render(request, 'bots/list.html', {'bots': bots})


@login_required
def bot_create(request):
    from managers.models import TeamMember
    team_members = TeamMember.objects.filter(owner=request.user, is_active=True, can_moderate=True).select_related('member')
    if request.method == 'POST':
        selected_moderators = set(request.POST.getlist('moderators'))
    else:
        selected_moderators = {str(request.user.pk)}
    from channels.models import ChannelGroup

    owner_groups = ChannelGroup.objects.filter(owner=request.user).order_by('name')

    # Preselect from querystring (e.g., after creating a group)
    chgroup_prefill = (request.GET.get('chgroup_id') or '').strip() if request.method == 'GET' else ''
    platform_prefill = (request.GET.get('platform') or '').strip() if request.method == 'GET' else ''
    if platform_prefill and platform_prefill not in dict(SuggestionBot.PLATFORM_CHOICES):
        platform_prefill = ''

    selected_channel_group_ids = set()
    if request.method == 'GET' and chgroup_prefill.isdigit():
        selected_channel_group_ids.add(int(chgroup_prefill))

    if request.method == 'POST':
        name = request.POST.get('name', '').strip()
        platform = request.POST.get('platform', '')
        token = request.POST.get('bot_token', '').strip()
        group_ids = request.POST.getlist('channel_group_ids')
        welcome_msg = request.POST.get('welcome_message', '').strip()
        success_msg = request.POST.get('success_message', '').strip()
        approved_msg = request.POST.get('approved_message', '').strip()
        rejected_msg = request.POST.get('rejected_message', '').strip()

        raw_gids = []
        for g in group_ids:
            s = (g or '').strip()
            if s.isdigit():
                raw_gids.append(int(s))
        selected_channel_group_ids = set(raw_gids)

        if not all([name, platform, token]) or not group_ids:
            messages.error(request, 'Заполните все обязательные поля и выберите хотя бы одну группу каналов.')
            return render(request, 'bots/create.html', {
                'platforms': SuggestionBot.PLATFORM_CHOICES,
                'team_members': team_members,
                'selected_moderators': selected_moderators,
                'owner_groups': owner_groups,
                'selected_channel_group_ids': selected_channel_group_ids,
            })

        groups = list(ChannelGroup.objects.filter(pk__in=raw_gids, owner=request.user))
        if len(groups) != len(set(raw_gids)):
            messages.error(request, 'Проверьте выбранные группы каналов.')
            return render(request, 'bots/create.html', {
                'platforms': SuggestionBot.PLATFORM_CHOICES,
                'team_members': team_members,
                'selected_moderators': selected_moderators,
                'owner_groups': owner_groups,
                'selected_channel_group_ids': selected_channel_group_ids,
            })

        admin_chat_post = (request.POST.get('admin_chat_id') or '').strip()
        moderation_qs, mod_users = _moderation_qs_and_users_for_post(request, request.user, existing_bot=None)
        if platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX) and request.POST.getlist(
            'moderators'
        ):
            if not mod_users:
                messages.error(request, _MODERATION_CHECKBOXES_NONE_SAVED)
                return render(request, 'bots/create.html', {
                    'platforms': SuggestionBot.PLATFORM_CHOICES,
                    'team_members': team_members,
                    'selected_moderators': selected_moderators,
                    'owner_groups': owner_groups,
                    'selected_channel_group_ids': selected_channel_group_ids,
                })
        if platform == SuggestionBot.PLATFORM_TELEGRAM:
            err = _telegram_moderation_config_error(request.user, mod_users, admin_chat_post)
            if err:
                messages.error(request, err)
                return render(request, 'bots/create.html', {
                    'platforms': SuggestionBot.PLATFORM_CHOICES,
                    'team_members': team_members,
                    'selected_moderators': selected_moderators,
                    'owner_groups': owner_groups,
                    'selected_channel_group_ids': selected_channel_group_ids,
                })
        elif platform == SuggestionBot.PLATFORM_MAX:
            err = _max_moderation_config_error(request.user, mod_users, admin_chat_post)
            if err:
                messages.error(request, err)
                return render(request, 'bots/create.html', {
                    'platforms': SuggestionBot.PLATFORM_CHOICES,
                    'team_members': team_members,
                    'selected_moderators': selected_moderators,
                    'owner_groups': owner_groups,
                    'selected_channel_group_ids': selected_channel_group_ids,
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

        if platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX):
            bot.admin_chat_id = admin_chat_post
            bot.custom_admin_chat_ids = []
            bot.notify_owner = False
        else:
            gid = request.POST.get('group_id', '').strip()
            # VK/MAX group ids are typically provided without '-'. Normalize to digits.
            gid = gid.replace('club', '').replace('public', '')
            gid = gid.lstrip('-').strip()
            bot.group_id = gid

        bot.save()
        bot.channel_groups.set(groups)
        # Автоподключение webhook для MAX (без кнопок)
        _ensure_max_webhook(bot)

        if platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX):
            bot.moderators.set(moderation_qs)

        # Audit
        try:
            from .models import AuditLog
            AuditLog.objects.create(
                actor=request.user,
                owner=request.user,
                action='suggestion_bot.create',
                object_type='SuggestionBot',
                object_id=str(bot.pk),
                data={
                    'name': bot.name,
                    'platform': bot.platform,
                    'channel_group_ids': list(bot.channel_groups.values_list('pk', flat=True)),
                },
            )
        except Exception:
            pass

        messages.success(request, f'Бот "{name}" создан.')
        return redirect('bots:list')

    return render(request, 'bots/create.html', {
        'platforms': SuggestionBot.PLATFORM_CHOICES,
        'team_members': team_members,
        'selected_moderators': selected_moderators,
        'owner_groups': owner_groups,
        'chgroup_prefill': chgroup_prefill,
        'platform_prefill': platform_prefill,
        'selected_channel_group_ids': selected_channel_group_ids,
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
        'can_manage_bot': _can_manage_bot_by_channel(request.user, bot),
    })


@login_required
@require_POST
def telegram_webhook_setup(request, bot_id: int):
    """
    Настраивает webhook для конкретного Telegram-бота клиента.
    Клиенту достаточно нажать кнопку в интерфейсе — сервер сам вызовет setWebhook.
    """
    from django.conf import settings
    from django.urls import reverse
    import secrets
    import requests

    bot = get_object_or_404(SuggestionBot, pk=bot_id)
    if not _can_manage_bot_by_channel(request.user, bot):
        return HttpResponse(status=403)
    if bot.platform != SuggestionBot.PLATFORM_TELEGRAM:
        return HttpResponse(status=400)

    site_url = (getattr(settings, 'SITE_URL', '') or '').rstrip('/')
    if not site_url:
        messages.error(request, 'SITE_URL не задан. Укажите публичный https URL сайта в настройках сервера.')
        return redirect('bots:detail', bot_id=bot.pk)
    if not site_url.startswith('https://'):
        messages.error(request, 'SITE_URL должен начинаться с https:// (Telegram требует HTTPS для webhook).')
        return redirect('bots:detail', bot_id=bot.pk)

    webhook_url = site_url + reverse('bots:telegram_webhook', kwargs={'bot_id': bot.pk})
    secret = secrets.token_urlsafe(32)

    try:
        token = bot.get_token()
        resp = requests.post(
            f'https://api.telegram.org/bot{token}/setWebhook',
            json={
                'url': webhook_url,
                'secret_token': secret,
                'drop_pending_updates': True,
            },
            timeout=15,
        )
        data = resp.json()
        if not data.get('ok'):
            raise ValueError(data.get('description', 'Telegram error'))
    except Exception as e:
        messages.error(request, f'Не удалось установить webhook: {e}')
        return redirect('bots:detail', bot_id=bot.pk)

    # Save secret only after successful setWebhook
    bot.webhook_secret = secret
    bot.save(update_fields=['webhook_secret'])

    messages.success(request, 'Webhook установлен. Теперь напишите боту любое сообщение для проверки.')
    return redirect('bots:detail', bot_id=bot.pk)


@login_required
def bot_edit(request, bot_id: int):
    from managers.models import TeamMember

    bot = get_object_or_404(
        SuggestionBot.objects.select_related('owner').prefetch_related('channel_groups'),
        pk=bot_id,
    )
    if not _can_view_bot(request.user, bot):
        return HttpResponse(status=403)

    selected_channel_group_ids = set(bot.channel_groups.values_list('pk', flat=True))

    footer_only = False
    can_edit_messages_only = False
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        # Managers may edit only bot messages (no tokens/webhook/admin chats).
        can_edit_messages_only = True

    team_members = TeamMember.objects.filter(owner=bot.owner, is_active=True, can_moderate=True).select_related('member')
    from channels.models import ChannelGroup

    owner_groups = ChannelGroup.objects.filter(owner=bot.owner).order_by('name')

    selected_moderators = set()
    if request.method == 'POST':
        selected_moderators = set(request.POST.getlist('moderators'))
    else:
        selected_moderators = set(str(x) for x in bot.moderators.values_list('id', flat=True))

    if request.method == 'POST':
        if not can_edit_messages_only:
            posted_gids = []
            for g in request.POST.getlist('channel_group_ids'):
                s = (g or '').strip()
                if s.isdigit():
                    posted_gids.append(int(s))
            selected_channel_group_ids = set(posted_gids)

        before = {
            'name': bot.name,
            'channel_group_ids': sorted(bot.channel_groups.values_list('pk', flat=True)),
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
                'owner_groups': owner_groups,
                'can_edit_messages_only': can_edit_messages_only,
                'selected_channel_group_ids': selected_channel_group_ids,
            })

        bot.name = name

        if not can_edit_messages_only:
            raw_gids = list(selected_channel_group_ids)
            if not raw_gids:
                messages.error(request, 'Выберите хотя бы одну группу каналов.')
                return render(request, 'bots/edit.html', {
                    'bot': bot,
                    'team_members': team_members,
                    'selected_moderators': selected_moderators,
                    'owner_groups': owner_groups,
                    'can_edit_messages_only': can_edit_messages_only,
                    'selected_channel_group_ids': selected_channel_group_ids,
                })
            groups = list(ChannelGroup.objects.filter(pk__in=raw_gids, owner=bot.owner))
            if len(groups) != len(set(raw_gids)):
                messages.error(request, 'Проверьте выбранные группы каналов.')
                return render(request, 'bots/edit.html', {
                    'bot': bot,
                    'team_members': team_members,
                    'selected_moderators': selected_moderators,
                    'owner_groups': owner_groups,
                    'can_edit_messages_only': can_edit_messages_only,
                    'selected_channel_group_ids': selected_channel_group_ids,
                })

        if not can_edit_messages_only:
            new_token = request.POST.get('bot_token', '').strip()
            if new_token:
                bot.set_token(new_token)

        bot.welcome_message = request.POST.get('welcome_message', bot.welcome_message).strip() or bot.welcome_message
        bot.success_message = request.POST.get('success_message', bot.success_message).strip() or bot.success_message
        bot.approved_message = request.POST.get('approved_message', bot.approved_message).strip() or bot.approved_message
        bot.rejected_message = request.POST.get('rejected_message', bot.rejected_message).strip() or bot.rejected_message

        if not can_edit_messages_only:
            admin_chat_post = (request.POST.get('admin_chat_id') or '').strip()
            moderation_qs, mod_users = _moderation_qs_and_users_for_post(request, bot.owner, existing_bot=bot)
            if bot.platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX) and request.POST.getlist(
                'moderators'
            ):
                if not mod_users:
                    messages.error(request, _MODERATION_CHECKBOXES_NONE_SAVED)
                    return render(request, 'bots/edit.html', {
                        'bot': bot,
                        'team_members': team_members,
                        'selected_moderators': selected_moderators,
                        'owner_groups': owner_groups,
                        'can_edit_messages_only': can_edit_messages_only,
                        'selected_channel_group_ids': selected_channel_group_ids,
                    })
            if bot.platform == SuggestionBot.PLATFORM_TELEGRAM:
                err = _telegram_moderation_config_error(bot.owner, mod_users, admin_chat_post)
                if err:
                    messages.error(request, err)
                    return render(request, 'bots/edit.html', {
                        'bot': bot,
                        'team_members': team_members,
                        'selected_moderators': selected_moderators,
                        'owner_groups': owner_groups,
                        'can_edit_messages_only': can_edit_messages_only,
                        'selected_channel_group_ids': selected_channel_group_ids,
                    })
            elif bot.platform == SuggestionBot.PLATFORM_MAX:
                err = _max_moderation_config_error(bot.owner, mod_users, admin_chat_post)
                if err:
                    messages.error(request, err)
                    return render(request, 'bots/edit.html', {
                        'bot': bot,
                        'team_members': team_members,
                        'selected_moderators': selected_moderators,
                        'owner_groups': owner_groups,
                        'can_edit_messages_only': can_edit_messages_only,
                        'selected_channel_group_ids': selected_channel_group_ids,
                    })

            if bot.platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX):
                bot.admin_chat_id = admin_chat_post
                bot.custom_admin_chat_ids = []
                bot.notify_owner = False
            else:
                gid = request.POST.get('group_id', '').strip()
                gid = gid.replace('club', '').replace('public', '')
                gid = gid.lstrip('-').strip()
                bot.group_id = gid

        bot.save()
        if not can_edit_messages_only:
            bot.channel_groups.set(groups)
        # Автоподключение webhook для MAX (без кнопок)
        if not can_edit_messages_only:
            _ensure_max_webhook(bot)

        if not can_edit_messages_only and bot.platform in (SuggestionBot.PLATFORM_TELEGRAM, SuggestionBot.PLATFORM_MAX):
            bot.moderators.set(moderation_qs)

        # Audit changes
        try:
            after = {
                'name': bot.name,
                'channel_group_ids': sorted(bot.channel_groups.values_list('pk', flat=True)),
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
        'owner_groups': owner_groups,
        'can_edit_messages_only': can_edit_messages_only,
        'selected_channel_group_ids': selected_channel_group_ids,
    })


@login_required
def conversations_list(request):
    """Список диалогов 'подписчик ↔ менеджер' по ботам предложки."""
    from .models import BotConversation
    qs = BotConversation.objects.select_related('bot', 'bot__owner').prefetch_related('bot__channel_groups')
    if getattr(request.user, 'role', '') in ('manager', 'assistant_admin') and not (request.user.is_staff or request.user.is_superuser):
        from managers.models import TeamMember
        allowed_channel_ids = TeamMember.objects.filter(
            member=request.user,
            is_active=True,
            can_manage_bots=True,
        ).values_list('channels__pk', flat=True)
        qs = qs.filter(bot__channel_groups__channels__pk__in=allowed_channel_ids).distinct()
    else:
        qs = qs.filter(bot__owner=request.user)
    qs = qs.order_by('-last_message_at', '-created_at')[:200]
    return render(request, 'bots/conversations_list.html', {'conversations': qs})


@login_required
def conversation_detail(request, pk: int):
    """Просмотр диалога и ответ менеджера пользователю (через Telegram Bot API)."""
    from .models import BotConversation, BotConversationMessage, AuditLog
    conv = get_object_or_404(BotConversation.objects.select_related('bot', 'bot__owner'), pk=pk)
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
            models.Q(bot__channel_groups__channels__pk__in=allowed_channel_ids)
            | models.Q(bot__moderators=request.user)
        )
    else:
        sug_qs = Suggestion.objects.filter(bot__owner=request.user)

    sug_qs = sug_qs.select_related('bot', 'bot__owner').prefetch_related('bot__channel_groups').distinct()
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
                'channel_name': ', '.join(g.name for g in s.bot.channel_groups.all()[:5]) or '—',
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
def suggestion_detail(request, pk: int):
    """Карточка предложения из ленты: полный текст, действия, контакты админа канала."""
    from .models import Suggestion
    from django.urls import reverse

    suggestion = get_object_or_404(
        Suggestion.objects.select_related('bot', 'bot__owner', 'moderated_by').prefetch_related(
            'bot__channel_groups'
        ),
        pk=pk,
    )
    if not _can_moderate_suggestion(request.user, suggestion):
        return HttpResponse(status=403)

    channel = suggestion.bot.representative_channel()
    can_edit_contacts = bool(
        channel
        and (
            request.user.is_staff
            or request.user.is_superuser
            or channel.owner_id == request.user.id
        )
    )

    detail_url = reverse('bots:suggestion_detail', args=[suggestion.pk])
    media_ids = list(suggestion.media_file_ids or [])
    media_indices = list(range(len(media_ids)))
    return render(
        request,
        'bots/suggestion_detail.html',
        {
            'suggestion': suggestion,
            'channel': channel,
            'can_edit_contacts': can_edit_contacts,
            'detail_url': detail_url,
            'media_indices': media_indices,
        },
    )


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
            # Синхронно: Telegram ~12 с, MAX ~30 с — зато автор гарантированно получает ответ в боте
            try:
                from .services import notify_suggestion_approved
                notify_suggestion_approved(suggestion)
            except Exception:
                pass
            messages.success(request, f'Предложение #{suggestion.short_tracking_id} одобрено.')
            # Если пришли из ленты — сразу ведём в создание поста из предложки
            next_url = (request.POST.get('next') or '').strip()
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
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
            next_url = (request.POST.get('next') or '').strip()
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
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
    if not bot:
        return HttpResponse(status=404)

    media_ids = suggestion.media_file_ids or []
    if idx < 0 or idx >= len(media_ids):
        return HttpResponse(status=404)

    # ─── MAX: media_file_ids — token из Bot API, качаем через platform-api / CDN
    if bot.platform == SuggestionBot.PLATFORM_MAX:
        from .max_bot.bot import MaxBotAPI
        from .max_media_preview import attachment_entries_from_raw, fetch_max_preview_bytes

        token = media_ids[idx]
        if not token:
            return HttpResponse(status=404)
        entries = attachment_entries_from_raw(suggestion.raw_data)
        att_type = ''
        att_dict: dict = {}
        if idx < len(entries) and str(entries[idx].get('token')) == str(token):
            att_type = entries[idx].get('type') or ''
            att_dict = entries[idx].get('att') if isinstance(entries[idx].get('att'), dict) else {}
        elif idx < len(entries):
            att_type = entries[idx].get('type') or ''
            att_dict = entries[idx].get('att') if isinstance(entries[idx].get('att'), dict) else {}
            token = str(entries[idx].get('token') or token)

        api = MaxBotAPI(bot.get_token())
        out = fetch_max_preview_bytes(api, bot.get_token(), str(token), att_type, att_dict)
        if not out:
            return HttpResponse(status=404)
        data, ct = out
        return HttpResponse(data, content_type=ct)

    if bot.platform != SuggestionBot.PLATFORM_TELEGRAM:
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
