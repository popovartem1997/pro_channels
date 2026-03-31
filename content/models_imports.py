from django.db import models
from django.contrib.auth import get_user_model

User = get_user_model()


class TelegramImportLink(models.Model):
    """Связка ProChannels пользователя с Telegram user_id (служебный бот импорта)."""
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="tg_import_link")
    telegram_user_id = models.BigIntegerField(null=True, blank=True, unique=True)
    code = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    linked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "Telegram link (import)"
        verbose_name_plural = "Telegram links (import)"


class TelegramImportedMessage(models.Model):
    """Последнее сообщение, присланное в служебного бота (текст+entities)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="tg_imported_messages")
    text = models.TextField(blank=True)
    entities = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "Telegram imported message"
        verbose_name_plural = "Telegram imported messages"
        ordering = ["-created_at"]

