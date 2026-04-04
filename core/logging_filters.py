"""Фильтры для LOGGING (см. pro_channels.settings.LOGGING)."""

import logging


class SkipWebhookNotFoundFilter(logging.Filter):
    """
    Убирает WARNING django.request вида «Not Found: /bots/webhook/...».
    Чаще всего это старый URL в setWebhook у Telegram или сканирование; корректное решение —
    снять вебхук у бота или восстановить запись SuggestionBot в админке.
    """

    _PREFIXES = (
        'Not Found: /bots/webhook/',
        'Not Found: /posts/tg-import/webhook/',
    )

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(msg.startswith(p) for p in self._PREFIXES)
