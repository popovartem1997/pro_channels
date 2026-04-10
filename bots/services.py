"""
Сервисные функции ботов (уведомления и интеграции).

Важно: эти функции должны быть best-effort — не ломать основной сценарий модерации.
"""

from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_TELEGRAM_SEND_TIMEOUT = 12


def _subscriber_text_ensure_tracking_id(text: str, tracking_short_id: str) -> str:
    """
    Если в шаблоне не было {tracking_id}, после replace номера в тексте нет —
    добавляем первой строкой #short_id (как в подтверждении приёма заявки).
    """
    tid = (tracking_short_id or '').strip()
    if not tid:
        return text
    body = (text or '').strip()
    if tid in body:
        return body
    return f'#{tid}\n\n{body}' if body else f'#{tid}'


def format_approved_subscriber_message(raw_template: str, tracking_short_id: str) -> str:
    raw = (raw_template or '').strip()
    if not raw:
        raw = 'Ваша заявка #{tracking_id} одобрена и будет опубликована. Спасибо!'
    text = raw.replace('{tracking_id}', tracking_short_id)
    return _subscriber_text_ensure_tracking_id(text, tracking_short_id)


def format_rejected_subscriber_message(raw_template: str, tracking_short_id: str, reason: str = '') -> str:
    raw = (raw_template or '').strip()
    if not raw:
        raw = 'Ваша заявка #{tracking_id} не прошла модерацию.\nПричина: {reason}'
    reason_clean = (reason or '').strip()
    had_reason_placeholder = '{reason}' in raw
    text = raw.replace('{tracking_id}', tracking_short_id).replace('{reason}', reason_clean)
    text = _subscriber_text_ensure_tracking_id(text, tracking_short_id)
    # Кастомный текст без {reason} — иначе комментарий с сайта/админки никогда не попадал в бот
    if reason_clean and not had_reason_placeholder and reason_clean not in text:
        text = text.rstrip() + f'\n\nПричина: {reason_clean}'
    return text


def _subscriber_menu_inline_keyboard_tg() -> dict:
    """Те же действия, что в боте предложки (callback обрабатывает handlers.handle_callback)."""
    return {
        'inline_keyboard': [
            [
                {'text': '📰 Прислать новость', 'callback_data': 'menu_send'},
                {'text': '💬 Связаться с админом', 'callback_data': 'menu_contact'},
            ],
            [
                {'text': '📬 Мои новости', 'callback_data': 'menu_my'},
                {'text': '📊 Статистика', 'callback_data': 'menu_stats'},
            ],
        ],
    }


def _subscriber_menu_buttons_max() -> list:
    """Кнопки MAX (callback как в max_bot._handle_message)."""
    return [
        [
            {'type': 'callback', 'text': '📰 Прислать новость', 'payload': 'menu_send'},
            {'type': 'callback', 'text': '📬 Мои новости', 'payload': 'menu_my'},
        ],
        [
            {'type': 'callback', 'text': '💬 Связаться с админом', 'payload': 'menu_contact'},
            {'type': 'callback', 'text': '📊 Статистика', 'payload': 'menu_stats'},
        ],
    ]


def notify_workspace_owner_telegram(owner_user, text: str, *, open_url: str | None = None) -> None:
    """
    Личные уведомления владельцу аккаунта в Telegram (как у модерации предложки).
    Нужны: активный бот предложки (Telegram) этого владельца и его telegram_user_id в профиле или в карточке команды.
    """
    from bots.models import SuggestionBot, telegram_chat_targets_for_moderation_recipient

    bot = (
        SuggestionBot.objects.filter(
            owner_id=owner_user.pk,
            platform=SuggestionBot.PLATFORM_TELEGRAM,
            is_active=True,
        )
        .order_by('-pk')
        .first()
    )
    if not bot:
        logger.debug('notify_workspace_owner_telegram: no telegram bot for owner_id=%s', owner_user.pk)
        return
    try:
        token = bot.get_token()
    except Exception as e:
        logger.warning('notify_workspace_owner_telegram: token owner_id=%s: %s', owner_user.pk, e)
        return
    if not (token or '').strip():
        return

    targets = telegram_chat_targets_for_moderation_recipient(int(owner_user.pk), owner_user)
    if not targets:
        logger.debug('notify_workspace_owner_telegram: no telegram_user_id owner_id=%s', owner_user.pk)
        return

    body = (text or '').strip()[:3900]
    markup = None
    ou = (open_url or '').strip()
    if ou.startswith(('http://', 'https://')):
        markup = {
            'inline_keyboard': [[{'text': 'Открыть на сайте', 'url': ou[:2000]}]]
        }

    for chat_id in targets:
        try:
            _telegram_send_message_raw(token, chat_id, body, reply_markup=markup)
        except Exception as e:
            logger.warning('notify_workspace_owner_telegram: send chat_id=%s: %s', chat_id, e)


def _telegram_send_message_raw(
    token: str,
    chat_id: str,
    text: str,
    reply_markup: dict | None = None,
) -> None:
    """Отправка через Bot API с жёстким таймаутом (python-telegram-bot v20+ — async)."""
    if not token or not chat_id or not text:
        return
    from core.telegram_bot_request import telegram_bot_requests_proxies

    payload: dict = {'chat_id': chat_id, 'text': text}
    if reply_markup:
        payload['reply_markup'] = reply_markup
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json=payload,
        timeout=_TELEGRAM_SEND_TIMEOUT,
        proxies=telegram_bot_requests_proxies(),
    )
    data = r.json() if r.content else {}
    if not data.get('ok'):
        raise RuntimeError(data.get('description') or r.text or f'HTTP {r.status_code}')


def notify_suggestion_approved(suggestion):
    """
    Уведомить автора предложения об одобрении (если возможно).
    Поддержка: Telegram / VK / MAX.
    """
    bot = suggestion.bot
    if not bot:
        return

    text = format_approved_subscriber_message(bot.approved_message or '', suggestion.short_tracking_id)
    if not text.strip():
        return

    if bot.platform == bot.PLATFORM_TELEGRAM:
        try:
            _telegram_send_message_raw(
                bot.get_token(),
                str(suggestion.platform_user_id),
                text,
                reply_markup=_subscriber_menu_inline_keyboard_tg(),
            )
        except Exception as e:
            logger.warning('Telegram notify approve failed: %s', e)
            return

    elif bot.platform == bot.PLATFORM_MAX:
        try:
            from .max_bot.bot import MaxBotAPI
            api = MaxBotAPI(bot.get_token())
            api.send_message_to_user(
                str(suggestion.platform_user_id),
                text,
                buttons=_subscriber_menu_buttons_max(),
            )
        except Exception as e:
            logger.warning('MAX notify approve failed: %s', e)
            return

    elif bot.platform == bot.PLATFORM_VK:
        try:
            from .vk.bot import VKSuggestionBot
            vk = VKSuggestionBot(bot)
            vk._send(str(suggestion.platform_user_id), text)
        except Exception as e:
            logger.warning('VK notify approve failed: %s', e)
            return


def notify_suggestion_rejected(suggestion, reason: str = ''):
    """
    Уведомить автора предложения об отклонении (если возможно).
    """
    bot = suggestion.bot
    if not bot:
        return

    reason_final = (reason or '').strip() or (getattr(suggestion, 'rejection_reason', None) or '').strip()
    text = format_rejected_subscriber_message(
        bot.rejected_message or '', suggestion.short_tracking_id, reason_final
    )
    if not text.strip():
        return

    if bot.platform == bot.PLATFORM_TELEGRAM:
        try:
            _telegram_send_message_raw(bot.get_token(), str(suggestion.platform_user_id), text)
        except Exception as e:
            logger.warning('Telegram notify reject failed: %s', e)
            return

    elif bot.platform == bot.PLATFORM_MAX:
        try:
            from .max_bot.bot import MaxBotAPI
            api = MaxBotAPI(bot.get_token())
            api.send_message_to_user(str(suggestion.platform_user_id), text)
        except Exception as e:
            logger.warning('MAX notify reject failed: %s', e)
            return

    elif bot.platform == bot.PLATFORM_VK:
        try:
            from .vk.bot import VKSuggestionBot
            vk = VKSuggestionBot(bot)
            vk._send(str(suggestion.platform_user_id), text)
        except Exception as e:
            logger.warning('VK notify reject failed: %s', e)
            return

