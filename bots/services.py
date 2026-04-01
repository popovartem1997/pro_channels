"""
Сервисные функции ботов (уведомления и интеграции).

Важно: эти функции должны быть best-effort — не ломать основной сценарий модерации.
"""

from __future__ import annotations

import logging
import requests

logger = logging.getLogger(__name__)

_TELEGRAM_SEND_TIMEOUT = 12


def _telegram_send_message_raw(token: str, chat_id: str, text: str) -> None:
    """Отправка через Bot API с жёстким таймаутом (python-telegram-bot v20+ — async)."""
    if not token or not chat_id or not text:
        return
    r = requests.post(
        f'https://api.telegram.org/bot{token}/sendMessage',
        json={'chat_id': chat_id, 'text': text},
        timeout=_TELEGRAM_SEND_TIMEOUT,
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

    raw_approved = (bot.approved_message or '').strip()
    if not raw_approved:
        raw_approved = 'Ваша заявка #{tracking_id} одобрена и будет опубликована. Спасибо!'
    text = raw_approved.replace('{tracking_id}', suggestion.short_tracking_id)
    if not text.strip():
        return

    if bot.platform == bot.PLATFORM_TELEGRAM:
        try:
            _telegram_send_message_raw(bot.get_token(), str(suggestion.platform_user_id), text)
        except Exception as e:
            logger.warning('Telegram notify approve failed: %s', e)
            return

    elif bot.platform == bot.PLATFORM_MAX:
        try:
            from .max_bot.bot import MaxBotAPI
            api = MaxBotAPI(bot.get_token())
            # В личных сообщениях MAX обычно нужен user_id (а не chat_id)
            api.send_message_to_user(str(suggestion.platform_user_id), text)
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

    raw_rej = (bot.rejected_message or '').strip()
    if not raw_rej:
        raw_rej = 'Ваша заявка #{tracking_id} не прошла модерацию.\nПричина: {reason}'
    text = raw_rej.replace('{tracking_id}', suggestion.short_tracking_id).replace('{reason}', reason or '')
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

