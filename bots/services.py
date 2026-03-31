"""
Сервисные функции ботов (уведомления и интеграции).

Важно: эти функции должны быть best-effort — не ломать основной сценарий модерации.
"""

from __future__ import annotations


def notify_suggestion_approved(suggestion):
    """
    Уведомить автора предложения об одобрении (если возможно).
    Поддержка: Telegram / VK / MAX.
    """
    bot = suggestion.bot
    if not bot:
        return

    text = (bot.approved_message or '').replace('{tracking_id}', suggestion.short_tracking_id)
    if not text:
        return

    if bot.platform == bot.PLATFORM_TELEGRAM:
        try:
            from telegram import Bot  # python-telegram-bot
            tg_bot = Bot(token=bot.get_token())
            tg_bot.send_message(chat_id=suggestion.platform_user_id, text=text)
        except Exception:
            return

    elif bot.platform == bot.PLATFORM_MAX:
        try:
            from .max_bot.bot import MaxBotAPI
            api = MaxBotAPI(bot.get_token())
            # В личных сообщениях MAX обычно нужен user_id (а не chat_id)
            api.send_message_to_user(str(suggestion.platform_user_id), text)
        except Exception:
            return

    elif bot.platform == bot.PLATFORM_VK:
        try:
            from .vk.bot import VKSuggestionBot
            vk = VKSuggestionBot(bot)
            vk._send(str(suggestion.platform_user_id), text)
        except Exception:
            return


def notify_suggestion_rejected(suggestion, reason: str = ''):
    """
    Уведомить автора предложения об отклонении (если возможно).
    """
    bot = suggestion.bot
    if not bot:
        return

    text = (bot.rejected_message or '')
    text = text.replace('{tracking_id}', suggestion.short_tracking_id).replace('{reason}', reason or '')
    if not text:
        return

    if bot.platform == bot.PLATFORM_TELEGRAM:
        try:
            from telegram import Bot  # python-telegram-bot
            tg_bot = Bot(token=bot.get_token())
            tg_bot.send_message(chat_id=suggestion.platform_user_id, text=text)
        except Exception:
            return

    elif bot.platform == bot.PLATFORM_MAX:
        try:
            from .max_bot.bot import MaxBotAPI
            api = MaxBotAPI(bot.get_token())
            api.send_message_to_user(str(suggestion.platform_user_id), text)
        except Exception:
            return

    elif bot.platform == bot.PLATFORM_VK:
        try:
            from .vk.bot import VKSuggestionBot
            vk = VKSuggestionBot(bot)
            vk._send(str(suggestion.platform_user_id), text)
        except Exception:
            return

