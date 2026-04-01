import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def notify_suggestion_approved_task(self, suggestion_id: int):
    """Уведомление об одобрении в фоне — HTTP-запросы к мессенджерам не блокируют веб-воркер."""
    from bots.models import Suggestion
    from bots.services import notify_suggestion_approved

    try:
        sug = Suggestion.objects.select_related('bot').get(pk=suggestion_id)
    except Suggestion.DoesNotExist:
        return
    try:
        notify_suggestion_approved(sug)
    except Exception as e:
        logger.warning('notify_suggestion_approved failed suggestion_id=%s: %s', suggestion_id, e)


@shared_task(bind=True, max_retries=2, default_retry_delay=30)
def notify_suggestion_rejected_task(self, suggestion_id: int, reason: str = ''):
    from bots.models import Suggestion
    from bots.services import notify_suggestion_rejected

    try:
        sug = Suggestion.objects.select_related('bot').get(pk=suggestion_id)
    except Suggestion.DoesNotExist:
        return
    try:
        notify_suggestion_rejected(sug, reason=reason or '')
    except Exception as e:
        logger.warning('notify_suggestion_rejected failed suggestion_id=%s: %s', suggestion_id, e)


@shared_task(bind=True, max_retries=3, default_retry_delay=10)
def process_telegram_update_task(self, bot_id: int, update_data: dict):
    """
    Обрабатывает Telegram update в фоне.

    Это делает webhook быстрым и стабильным: веб-сервер сразу отдаёт 200,
    а тяжёлая логика выполняется в celery worker.
    """
    try:
        logger.info("[TG Task] received update for bot_id=%s keys=%s", bot_id, list(update_data.keys())[:20])
        from bots.models import SuggestionBot
        from bots.telegram.handlers import build_application
        from telegram import Update

        bot_config = SuggestionBot.objects.get(id=bot_id, platform=SuggestionBot.PLATFORM_TELEGRAM, is_active=True)
        app = build_application(bot_config)

        async def process():
            async with app:
                upd = Update.de_json(update_data, app.bot)
                await app.process_update(upd)

        asyncio.run(process())
        logger.info("[TG Task] processed update ok for bot_id=%s", bot_id)
        return True
    except Exception as e:
        logger.exception("[TG Task] update failed for bot_id=%s: %s", bot_id, e)
        raise self.retry(exc=e)


@shared_task(bind=True, max_retries=2, default_retry_delay=5)
def flush_telegram_media_group_task(self, cache_key: str, bot_id: int):
    """
    Собрать части media_group из Django cache и оформить одну заявку.
    Отдельная задача, т.к. JobQueue PTB при process_update в Celery не выполняется.
    """
    from django.core.cache import cache
    from telegram import Bot

    from bots.models import SuggestionBot
    from bots.telegram.handlers import flush_collected_telegram_album

    flush_lock = cache_key + ':flush'
    if not cache.add(flush_lock, 1, timeout=120):
        return

    try:
        payload = cache.get(cache_key)
        if not isinstance(payload, dict):
            return
        msgs = payload.get('messages')
        if not isinstance(msgs, list) or not msgs:
            cache.delete(cache_key)
            return
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        cache.delete(cache_key)

        user_id = int(meta.get('user_id') or 0)
        chat_id = int(meta.get('chat_id') or 0)
        send_mode = bool(meta.get('send_mode'))

        bot_config = SuggestionBot.objects.get(
            id=bot_id,
            platform=SuggestionBot.PLATFORM_TELEGRAM,
            is_active=True,
        )
        token = bot_config.get_token()
        bot = Bot(token)

        async def run():
            await flush_collected_telegram_album(
                bot,
                bot_config,
                chat_id=chat_id,
                user_id=user_id,
                send_mode=send_mode,
                msgs=msgs,
                meta=meta,
            )

        asyncio.run(run())
    except Exception as e:
        logger.exception('[TG] flush_telegram_media_group_task failed: %s', e)
        raise self.retry(exc=e)
    finally:
        cache.delete(flush_lock)

