import asyncio
import logging

from celery import shared_task

logger = logging.getLogger(__name__)


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

