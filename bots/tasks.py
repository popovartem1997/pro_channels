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
        # Не полагаться на application.running (он может быть True после initialize) —
        # альбомы обрабатываем ожиданием+flush в самом хендлере, а не отложенной задачей.
        app.bot_data['album_flush_mode'] = 'inline'

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


@shared_task(bind=True, max_retries=15, default_retry_delay=2)
def flush_telegram_media_group_task(self, cache_key: str, bot_id: int):
    """
    Собрать части media_group из Django cache и оформить одну заявку.
    Отдельная задача, т.к. JobQueue PTB при process_update в Celery не выполняется.
    """
    import time

    from celery.exceptions import Retry
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
            cache.delete(cache_key + ':sched')
            return

        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        parts = payload.get('parts')
        if parts is None:
            parts = payload.get('messages')
        if not isinstance(parts, list):
            parts = []

        last_ts = float(meta.get('_last_append_ts') or 0)
        # Подождать, пока придут остальные сообщения альбома (разнесены по времени в Celery).
        if last_ts > 0 and (time.time() - last_ts) < 1.6 and self.request.retries < 14:
            logger.info(
                '[TG] album flush debounce retry=%s parts=%s age=%.2fs',
                self.request.retries,
                len(parts),
                time.time() - last_ts,
            )
            raise self.retry(countdown=2)

        payload = cache.get(cache_key)
        if not isinstance(payload, dict):
            cache.delete(cache_key + ':sched')
            return
        parts = payload.get('parts')
        if parts is None:
            parts = payload.get('messages')
        meta = payload.get('meta') if isinstance(payload.get('meta'), dict) else {}
        if not isinstance(parts, list) or not parts:
            logger.warning('[TG] album flush: пустой буфер key=%s', cache_key)
            cache.delete(cache_key)
            cache.delete(cache_key + ':sched')
            return

        logger.info('[TG] album flush run parts=%s key=%s', len(parts), cache_key)
        msgs = list(parts)

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
        cache.delete(cache_key)
        cache.delete(cache_key + ':sched')
    except Retry:
        raise
    except Exception as e:
        logger.exception('[TG] flush_telegram_media_group_task failed: %s', e)
        cache.delete(cache_key + ':sched')
        raise self.retry(exc=e)
    finally:
        cache.delete(flush_lock)

